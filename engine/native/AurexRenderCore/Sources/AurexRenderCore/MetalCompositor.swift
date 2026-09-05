import CoreVideo
import Foundation
import Metal
import MetalKit
import simd

private let compositorShaderSource = #"""
#include <metal_stdlib>
using namespace metal;

struct QuadUniforms {
    float4 clipRect;
    float4 uvRect;
    float4 color;
    // x = radius in canvas pixels, y = canvas width, z = canvas height.
    float4 shape;
};

struct VertexOutput {
    float4 position [[position]];
    float2 uv;
};

vertex VertexOutput quadVertex(
    uint vertexID [[vertex_id]],
    constant QuadUniforms &uniforms [[buffer(0)]])
{
    const float2 vertices[6] = {
        float2(0.0, 0.0), float2(0.0, 1.0), float2(1.0, 0.0),
        float2(1.0, 0.0), float2(0.0, 1.0), float2(1.0, 1.0)
    };
    float2 local = vertices[vertexID];
    VertexOutput output;
    output.position = float4(
        mix(uniforms.clipRect.x, uniforms.clipRect.z, local.x),
        mix(uniforms.clipRect.y, uniforms.clipRect.w, local.y),
        0.0,
        1.0
    );
    output.uv = local;
    return output;
}

float roundedMask(
    VertexOutput input,
    constant QuadUniforms &uniforms)
{
    // Work in physical canvas pixels so a CSS-style circular radius remains
    // circular even when the Scene IR canvas is a 9:16 vertical video.
    float2 canvasSize = max(uniforms.shape.yz, float2(1.0));
    float2 halfSize = abs(uniforms.clipRect.zw - uniforms.clipRect.xy) * canvasSize * 0.25;
    float radius = min(uniforms.shape.x, min(halfSize.x, halfSize.y));
    if (radius <= 0.0) {
        return 1.0;
    }

    float2 point = (input.uv - 0.5) * (halfSize * 2.0);
    float2 q = abs(point) - (halfSize - radius);
    float distance = length(max(q, float2(0.0))) + min(max(q.x, q.y), 0.0) - radius;
    // `distance` is already measured in canvas pixels.  A fixed one-pixel
    // transition keeps the edge antialiased without relying on derivative
    // functions that are unstable for this IOSurface render target.
    return 1.0 - smoothstep(-1.0, 1.0, distance);
}

fragment float4 solidFragment(
    VertexOutput input [[stage_in]],
    constant QuadUniforms &uniforms [[buffer(0)]])
{
    float mask = roundedMask(input, uniforms);
    return float4(uniforms.color.rgb * uniforms.color.a * mask, uniforms.color.a * mask);
}

fragment float4 imageFragment(
    VertexOutput input [[stage_in]],
    constant QuadUniforms &uniforms [[buffer(0)]],
    texture2d<float> image [[texture(0)]])
{
    constexpr sampler textureSampler(coord::normalized, address::clamp_to_edge, filter::linear);
    float2 uv = mix(uniforms.uvRect.xy, uniforms.uvRect.zw, input.uv);
    float4 value = image.sample(textureSampler, uv);
    float mask = roundedMask(input, uniforms);
    value.rgb *= uniforms.color.a * mask;
    value.a *= uniforms.color.a * mask;
    return value;
}
"""#

private struct QuadUniforms {
    var clipRect: SIMD4<Float>
    var uvRect: SIMD4<Float>
    var color: SIMD4<Float>
    var shape: SIMD4<Float>
}

struct ImageGeometry: Equatable {
    let rect: NormalizedRect
    let uvRect: SIMD4<Float>
}

private struct LoadedTexture {
    let texture: any MTLTexture
    let width: Int
    let height: Int
}

public final class MetalCompositor {
    public let deviceName: String

    private let device: any MTLDevice
    private let commandQueue: any MTLCommandQueue
    private let solidPipeline: any MTLRenderPipelineState
    private let imagePipeline: any MTLRenderPipelineState
    private let textureCache: CVMetalTextureCache
    private let textures: [String: LoadedTexture]
    private var videoDecoders: [String: VideoFrameDecoder]
    private let textRasterizer: TextRasterizer
    private let manifest: RenderManifest
    private let orderedLayers: [SceneLayer]

    public init(document: ManifestDocument) throws {
        guard let device = MTLCreateSystemDefaultDevice() else {
            throw AurexRenderError.unavailable("Metal device")
        }
        guard let commandQueue = device.makeCommandQueue() else {
            throw AurexRenderError.unavailable("Metal command queue")
        }
        let library: any MTLLibrary
        do {
            library = try device.makeLibrary(source: compositorShaderSource, options: nil)
        } catch {
            throw AurexRenderError.unavailable("Metal shader compilation: \(error.localizedDescription)")
        }
        guard let vertexFunction = library.makeFunction(name: "quadVertex"),
              let solidFunction = library.makeFunction(name: "solidFragment"),
              let imageFunction = library.makeFunction(name: "imageFragment") else {
            throw AurexRenderError.unavailable("Metal compositor shader functions")
        }

        func makePipeline(fragment: any MTLFunction) throws -> any MTLRenderPipelineState {
            let descriptor = MTLRenderPipelineDescriptor()
            descriptor.vertexFunction = vertexFunction
            descriptor.fragmentFunction = fragment
            descriptor.colorAttachments[0].pixelFormat = .bgra8Unorm
            descriptor.colorAttachments[0].isBlendingEnabled = true
            descriptor.colorAttachments[0].rgbBlendOperation = .add
            descriptor.colorAttachments[0].alphaBlendOperation = .add
            descriptor.colorAttachments[0].sourceRGBBlendFactor = .one
            descriptor.colorAttachments[0].destinationRGBBlendFactor = .oneMinusSourceAlpha
            descriptor.colorAttachments[0].sourceAlphaBlendFactor = .one
            descriptor.colorAttachments[0].destinationAlphaBlendFactor = .oneMinusSourceAlpha
            return try device.makeRenderPipelineState(descriptor: descriptor)
        }

        do {
            solidPipeline = try makePipeline(fragment: solidFunction)
            imagePipeline = try makePipeline(fragment: imageFunction)
        } catch {
            throw AurexRenderError.unavailable("Metal render pipeline: \(error.localizedDescription)")
        }

        var cache: CVMetalTextureCache?
        let cacheStatus = CVMetalTextureCacheCreate(kCFAllocatorDefault, nil, device, nil, &cache)
        guard cacheStatus == kCVReturnSuccess, let cache else {
            throw AurexRenderError.unavailable("CVMetalTextureCache (CVReturn \(cacheStatus))")
        }

        let loader = MTKTextureLoader(device: device)
        let textRasterizer = try TextRasterizer(document: document)
        var loadedTextures: [String: LoadedTexture] = [:]
        var videoDecoders: [String: VideoFrameDecoder] = [:]
        for layer in document.manifest.layers {
            switch layer.type {
            case .image:
                guard let source = layer.source else { continue }
                let assetURL = try Self.resolveAsset(source, relativeTo: document.url)
                let texture: any MTLTexture
                do {
                    texture = try loader.newTexture(
                        URL: assetURL,
                        options: [
                            .SRGB: false,
                            .origin: MTKTextureLoader.Origin.topLeft,
                            .textureUsage: NSNumber(value: MTLTextureUsage.shaderRead.rawValue),
                        ]
                    )
                } catch {
                    throw AurexRenderError.invalidManifest(
                        "image layer '\(layer.id)' cannot load '\(source)': \(error.localizedDescription)"
                    )
                }
                loadedTextures[layer.id] = LoadedTexture(
                    texture: texture,
                    width: texture.width,
                    height: texture.height
                )
            case .text:
                let image = try textRasterizer.image(
                    for: layer,
                    canvasWidth: document.manifest.canvas.width,
                    canvasHeight: document.manifest.canvas.height
                )
                let texture: any MTLTexture
                do {
                    texture = try loader.newTexture(
                        cgImage: image,
                        options: [
                            .SRGB: false,
                            .origin: MTKTextureLoader.Origin.topLeft,
                            .textureUsage: NSNumber(value: MTLTextureUsage.shaderRead.rawValue),
                        ]
                    )
                } catch {
                    throw AurexRenderError.invalidManifest(
                        "text layer '\(layer.id)' cannot rasterize: \(error.localizedDescription)"
                    )
                }
                loadedTextures[layer.id] = LoadedTexture(
                    texture: texture,
                    width: texture.width,
                    height: texture.height
                )
            case .video:
                guard let source = layer.source else { continue }
                let assetURL = try Self.resolveAsset(source, relativeTo: document.url)
                videoDecoders[layer.id] = try VideoFrameDecoder(url: assetURL)
            case .solid:
                break
            }
        }

        self.device = device
        self.deviceName = device.name
        self.commandQueue = commandQueue
        self.textureCache = cache
        self.textures = loadedTextures
        self.videoDecoders = videoDecoders
        self.textRasterizer = textRasterizer
        self.manifest = document.manifest
        self.orderedLayers = document.manifest.layers.enumerated().sorted {
            if $0.element.zIndex != $1.element.zIndex {
                return $0.element.zIndex < $1.element.zIndex
            }
            return $0.offset < $1.offset
        }.map(\.element)
    }

    public func render(frameIndex: Int, into pixelBuffer: CVPixelBuffer) throws {
        let canvas = manifest.canvas
        guard frameIndex >= 0, frameIndex < canvas.frameCount else {
            throw AurexRenderError.renderFailed("frame index \(frameIndex) is outside the timeline")
        }
        guard CVPixelBufferGetWidth(pixelBuffer) == canvas.width,
              CVPixelBufferGetHeight(pixelBuffer) == canvas.height,
              CVPixelBufferGetPixelFormatType(pixelBuffer) == kCVPixelFormatType_32BGRA else {
            throw AurexRenderError.renderFailed("pixel buffer must match canvas BGRA dimensions")
        }

        CVBufferSetAttachment(
            pixelBuffer,
            kCVImageBufferColorPrimariesKey,
            kCVImageBufferColorPrimaries_ITU_R_709_2,
            .shouldPropagate
        )
        CVBufferSetAttachment(
            pixelBuffer,
            kCVImageBufferTransferFunctionKey,
            kCVImageBufferTransferFunction_ITU_R_709_2,
            .shouldPropagate
        )
        CVBufferSetAttachment(
            pixelBuffer,
            kCVImageBufferYCbCrMatrixKey,
            kCVImageBufferYCbCrMatrix_ITU_R_709_2,
            .shouldPropagate
        )

        var cvTexture: CVMetalTexture?
        let textureStatus = CVMetalTextureCacheCreateTextureFromImage(
            kCFAllocatorDefault,
            textureCache,
            pixelBuffer,
            nil,
            .bgra8Unorm,
            canvas.width,
            canvas.height,
            0,
            &cvTexture
        )
        guard textureStatus == kCVReturnSuccess,
              let cvTexture,
              let targetTexture = CVMetalTextureGetTexture(cvTexture) else {
            throw AurexRenderError.renderFailed(
                "cannot map CVPixelBuffer to Metal texture (CVReturn \(textureStatus))"
            )
        }
        guard let commandBuffer = commandQueue.makeCommandBuffer() else {
            throw AurexRenderError.renderFailed("cannot create Metal command buffer")
        }

        let renderPass = MTLRenderPassDescriptor()
        renderPass.colorAttachments[0].texture = targetTexture
        renderPass.colorAttachments[0].loadAction = .clear
        renderPass.colorAttachments[0].storeAction = .store
        let background = canvas.backgroundColor
        renderPass.colorAttachments[0].clearColor = MTLClearColor(
            red: Double(background.red),
            green: Double(background.green),
            blue: Double(background.blue),
            alpha: 1
        )
        guard let encoder = commandBuffer.makeRenderCommandEncoder(descriptor: renderPass) else {
            throw AurexRenderError.renderFailed("cannot create Metal render encoder")
        }
        encoder.setViewport(
            MTLViewport(
                originX: 0,
                originY: 0,
                width: Double(canvas.width),
                height: Double(canvas.height),
                znear: 0,
                zfar: 1
            )
        )

        for layer in orderedLayers where layer.isActive(at: frameIndex, frameCount: canvas.frameCount) {
            let baseRect = layer.rect(at: frameIndex, frameCount: canvas.frameCount)
            let opacity = Float(layer.evaluatedOpacity(at: frameIndex, frameCount: canvas.frameCount))
            var drawRect = baseRect
            var uvRect = SIMD4<Float>(0, 0, 1, 1)
            var color = SIMD4<Float>(1, 1, 1, opacity)
            var videoTexture: CVMetalTexture?

            switch layer.type {
            case .solid:
                guard let layerColor = layer.color else { continue }
                let alpha = layerColor.alpha * opacity
                color = SIMD4<Float>(layerColor.red, layerColor.green, layerColor.blue, alpha)
                encoder.setRenderPipelineState(solidPipeline)
            case .image, .text:
                guard let loadedTexture = textures[layer.id] else {
                    encoder.endEncoding()
                    throw AurexRenderError.renderFailed("missing loaded texture for layer '\(layer.id)'")
                }
                let geometry = Self.imageGeometry(
                    contentMode: layer.contentMode,
                    target: baseRect,
                    canvasWidth: canvas.width,
                    canvasHeight: canvas.height,
                    imageWidth: loadedTexture.width,
                    imageHeight: loadedTexture.height,
                    zoom: layer.zoom,
                    panX: layer.panX,
                    panY: layer.panY
                )
                drawRect = geometry.rect
                uvRect = geometry.uvRect
                encoder.setRenderPipelineState(imagePipeline)
                encoder.setFragmentTexture(loadedTexture.texture, index: 0)
            case .video:
                guard let decoder = videoDecoders[layer.id] else {
                    encoder.endEncoding()
                    throw AurexRenderError.renderFailed("missing video decoder for layer '\(layer.id)'")
                }
                let sourceFrame = Self.videoFrameIndex(
                    layer: layer,
                    frameIndex: frameIndex,
                    canvasFrameRate: canvas.frameRate.framesPerSecond,
                    decoder: decoder
                )
                let pixelBuffer = try decoder.frame(at: sourceFrame)
                let textureStatus = CVMetalTextureCacheCreateTextureFromImage(
                    kCFAllocatorDefault,
                    textureCache,
                    pixelBuffer,
                    nil,
                    .bgra8Unorm,
                    CVPixelBufferGetWidth(pixelBuffer),
                    CVPixelBufferGetHeight(pixelBuffer),
                    0,
                    &videoTexture
                )
                guard textureStatus == kCVReturnSuccess,
                      let videoTexture,
                      let texture = CVMetalTextureGetTexture(videoTexture) else {
                    encoder.endEncoding()
                    throw AurexRenderError.renderFailed(
                        "cannot map video frame to Metal texture for layer '\(layer.id)' (CVReturn \(textureStatus))"
                    )
                }
                let geometry = Self.imageGeometry(
                    contentMode: layer.contentMode,
                    target: baseRect,
                    canvasWidth: canvas.width,
                    canvasHeight: canvas.height,
                    imageWidth: CVPixelBufferGetWidth(pixelBuffer),
                    imageHeight: CVPixelBufferGetHeight(pixelBuffer),
                    zoom: layer.zoom,
                    panX: layer.panX,
                    panY: layer.panY
                )
                drawRect = geometry.rect
                uvRect = geometry.uvRect
                encoder.setRenderPipelineState(imagePipeline)
                encoder.setFragmentTexture(texture, index: 0)
            }

            var uniforms = QuadUniforms(
                clipRect: Self.clipRect(drawRect),
                uvRect: uvRect,
                color: color,
                shape: SIMD4<Float>(
                    Float(layer.cornerRadius * Double(canvas.height)),
                    Float(canvas.width),
                    Float(canvas.height),
                    0
                )
            )
            encoder.setVertexBytes(&uniforms, length: MemoryLayout<QuadUniforms>.stride, index: 0)
            encoder.setFragmentBytes(&uniforms, length: MemoryLayout<QuadUniforms>.stride, index: 0)
            encoder.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 6)
        }
        encoder.endEncoding()
        commandBuffer.commit()
        commandBuffer.waitUntilCompleted()
        if commandBuffer.status == .error {
            throw AurexRenderError.renderFailed(
                "Metal command failed: \(commandBuffer.error?.localizedDescription ?? "unknown error")"
            )
        }
    }

    private static func clipRect(_ rect: NormalizedRect) -> SIMD4<Float> {
        SIMD4<Float>(
            Float(rect.x * 2 - 1),
            Float(1 - rect.y * 2),
            Float((rect.x + rect.width) * 2 - 1),
            Float(1 - (rect.y + rect.height) * 2)
        )
    }

    static func imageGeometry(
        contentMode: ImageContentMode,
        target: NormalizedRect,
        canvasWidth: Int,
        canvasHeight: Int,
        imageWidth: Int,
        imageHeight: Int,
        zoom: Double = 1,
        panX: Double = 0,
        panY: Double = 0
    ) -> ImageGeometry {
        let targetWidth = target.width * Double(canvasWidth)
        let targetHeight = target.height * Double(canvasHeight)
        let imageWidth = Double(imageWidth)
        let imageHeight = Double(imageHeight)
        let safeZoom = min(3, max(1, zoom.isFinite ? zoom : 1))
        let safePanX = min(50, max(-50, panX.isFinite ? panX : 0))
        let safePanY = min(50, max(-50, panY.isFinite ? panY : 0))
        switch contentMode {
        case .stretch:
            return ImageGeometry(rect: target, uvRect: SIMD4<Float>(0, 0, 1, 1))
        case .fit:
            let scale = min(targetWidth / imageWidth, targetHeight / imageHeight) * safeZoom
            let drawWidth = imageWidth * scale / Double(canvasWidth)
            let drawHeight = imageHeight * scale / Double(canvasHeight)
            return ImageGeometry(
                rect: NormalizedRect(
                    x: target.x + (target.width - drawWidth) / 2,
                    y: target.y + (target.height - drawHeight) / 2,
                    width: drawWidth,
                    height: drawHeight
                ),
                uvRect: SIMD4<Float>(0, 0, 1, 1)
            )
        case .fill:
            let scale = max(targetWidth / imageWidth, targetHeight / imageHeight) * safeZoom
            let visibleWidth = targetWidth / scale / imageWidth
            let visibleHeight = targetHeight / scale / imageHeight
            let uInset = (1 - visibleWidth) / 2
            let vInset = (1 - visibleHeight) / 2
            let shiftedUInset = uInset - safePanX / 50 * uInset
            let shiftedVInset = vInset - safePanY / 50 * vInset
            return ImageGeometry(
                rect: target,
                uvRect: SIMD4<Float>(
                    Float(shiftedUInset),
                    Float(shiftedVInset),
                    Float(shiftedUInset + visibleWidth),
                    Float(shiftedVInset + visibleHeight)
                )
            )
        }
    }

    private static func videoFrameIndex(
        layer: SceneLayer,
        frameIndex: Int,
        canvasFrameRate: Double,
        decoder: VideoFrameDecoder
    ) -> Int {
        let timelineTime = Double(frameIndex) / max(0.001, canvasFrameRate)
        let sceneStart = Double(layer.startFrame) / max(0.001, canvasFrameRate)
        let localElapsed = layer.videoSyncMode == .timeline
            ? max(0, timelineTime)
            : max(0, timelineTime - sceneStart)
        let loopStart = min(max(0, decoder.duration - 0.001), layer.videoLoopStart)
        let configuredEnd = layer.videoLoopEnd > loopStart ? layer.videoLoopEnd : decoder.duration
        let loopEnd = min(decoder.duration, max(loopStart + 0.001, configuredEnd))
        let span = max(0.001, loopEnd - loopStart)
        let targetTime: Double
        switch layer.videoSyncMode {
        case .freeze:
            targetTime = loopStart
        case .scene, .timeline:
            targetTime = layer.videoLoop
                ? loopStart + localElapsed.truncatingRemainder(dividingBy: span)
                : min(loopEnd - 0.001, loopStart + localElapsed)
        }
        return max(0, Int((targetTime * decoder.frameRate).rounded(.down)))
    }

    private static func resolveAsset(_ source: String, relativeTo manifestURL: URL) throws -> URL {
        let baseURL = manifestURL.deletingLastPathComponent().resolvingSymlinksInPath().standardizedFileURL
        let assetURL = baseURL.appendingPathComponent(source).resolvingSymlinksInPath().standardizedFileURL
        let basePath = baseURL.path.hasSuffix("/") ? baseURL.path : baseURL.path + "/"
        guard assetURL.path.hasPrefix(basePath),
              FileManager.default.fileExists(atPath: assetURL.path) else {
            throw AurexRenderError.invalidManifest("image source '\(source)' is missing or escapes the manifest directory")
        }
        return assetURL
    }
}
