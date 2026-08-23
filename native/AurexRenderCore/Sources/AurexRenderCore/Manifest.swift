import CryptoKit
import Foundation

public struct RenderManifest: Decodable, Sendable {
    public let schemaVersion: Int
    public let canvas: Canvas
    public let output: OutputOptions
    public let layers: [SceneLayer]

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, canvas, output, layers
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        canvas = try container.decode(Canvas.self, forKey: .canvas)
        output = try container.decodeIfPresent(OutputOptions.self, forKey: .output) ?? OutputOptions()
        layers = try container.decodeIfPresent([SceneLayer].self, forKey: .layers) ?? []
    }
}

public struct Canvas: Decodable, Sendable {
    public let width: Int
    public let height: Int
    public let frameRate: FrameRate
    public let frameCount: Int
    public let backgroundColor: RGBAColor
}

public struct FrameRate: Decodable, Equatable, Sendable {
    public let numerator: Int
    public let denominator: Int

    public init(numerator: Int, denominator: Int) {
        self.numerator = numerator
        self.denominator = denominator
    }

    public var framesPerSecond: Double {
        Double(numerator) / Double(denominator)
    }
}

public enum HardwareAccelerationPolicy: String, Decodable, Sendable {
    case automatic
    case prefer
    case require
    case software
}

public struct OutputOptions: Decodable, Sendable {
    public let bitRate: Int
    public let hardwareAcceleration: HardwareAccelerationPolicy
    public let keyFrameIntervalSeconds: Int

    public init(
        bitRate: Int = 8_000_000,
        hardwareAcceleration: HardwareAccelerationPolicy = .prefer,
        keyFrameIntervalSeconds: Int = 2
    ) {
        self.bitRate = bitRate
        self.hardwareAcceleration = hardwareAcceleration
        self.keyFrameIntervalSeconds = keyFrameIntervalSeconds
    }

    private enum CodingKeys: String, CodingKey {
        case bitRate, hardwareAcceleration, keyFrameIntervalSeconds
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        bitRate = try container.decodeIfPresent(Int.self, forKey: .bitRate) ?? 8_000_000
        hardwareAcceleration = try container.decodeIfPresent(
            HardwareAccelerationPolicy.self,
            forKey: .hardwareAcceleration
        ) ?? .prefer
        keyFrameIntervalSeconds = try container.decodeIfPresent(
            Int.self,
            forKey: .keyFrameIntervalSeconds
        ) ?? 2
    }
}

public enum SceneLayerType: String, Decodable, Sendable {
    case solid
    case image
}

public enum ImageContentMode: String, Decodable, Sendable {
    case stretch
    case fit
    case fill
}

public struct SceneLayer: Decodable, Sendable {
    public let id: String
    public let type: SceneLayerType
    public let zIndex: Int
    public let startFrame: Int
    public let endFrame: Int?
    public let rect: NormalizedRect
    public let endRect: NormalizedRect?
    public let opacity: Double
    public let endOpacity: Double?
    public let color: RGBAColor?
    public let source: String?
    public let contentMode: ImageContentMode

    private enum CodingKeys: String, CodingKey {
        case id, type, zIndex, startFrame, endFrame, rect, endRect
        case opacity, endOpacity, color, source, contentMode
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        type = try container.decode(SceneLayerType.self, forKey: .type)
        zIndex = try container.decodeIfPresent(Int.self, forKey: .zIndex) ?? 0
        startFrame = try container.decodeIfPresent(Int.self, forKey: .startFrame) ?? 0
        endFrame = try container.decodeIfPresent(Int.self, forKey: .endFrame)
        rect = try container.decode(NormalizedRect.self, forKey: .rect)
        endRect = try container.decodeIfPresent(NormalizedRect.self, forKey: .endRect)
        opacity = try container.decodeIfPresent(Double.self, forKey: .opacity) ?? 1
        endOpacity = try container.decodeIfPresent(Double.self, forKey: .endOpacity)
        color = try container.decodeIfPresent(RGBAColor.self, forKey: .color)
        source = try container.decodeIfPresent(String.self, forKey: .source)
        contentMode = try container.decodeIfPresent(ImageContentMode.self, forKey: .contentMode) ?? .stretch
    }

    public func resolvedEndFrame(frameCount: Int) -> Int {
        endFrame ?? frameCount
    }

    public func isActive(at frameIndex: Int, frameCount: Int) -> Bool {
        frameIndex >= startFrame && frameIndex < resolvedEndFrame(frameCount: frameCount)
    }

    public func rect(at frameIndex: Int, frameCount: Int) -> NormalizedRect {
        guard let endRect else { return rect }
        return rect.interpolated(to: endRect, progress: animationProgress(at: frameIndex, frameCount: frameCount))
    }

    public func evaluatedOpacity(at frameIndex: Int, frameCount: Int) -> Double {
        guard let endOpacity else { return opacity }
        let progress = animationProgress(at: frameIndex, frameCount: frameCount)
        return opacity + (endOpacity - opacity) * progress
    }

    private func animationProgress(at frameIndex: Int, frameCount: Int) -> Double {
        let visibleFrames = resolvedEndFrame(frameCount: frameCount) - startFrame
        guard visibleFrames > 1 else { return 0 }
        let value = Double(frameIndex - startFrame) / Double(visibleFrames - 1)
        return min(1, max(0, value))
    }
}

public struct NormalizedRect: Decodable, Equatable, Sendable {
    public let x: Double
    public let y: Double
    public let width: Double
    public let height: Double

    public func interpolated(to other: NormalizedRect, progress: Double) -> NormalizedRect {
        NormalizedRect(
            x: x + (other.x - x) * progress,
            y: y + (other.y - y) * progress,
            width: width + (other.width - width) * progress,
            height: height + (other.height - height) * progress
        )
    }

    public init(x: Double, y: Double, width: Double, height: Double) {
        self.x = x
        self.y = y
        self.width = width
        self.height = height
    }
}

public struct RGBAColor: Decodable, Equatable, Sendable {
    public let red: Float
    public let green: Float
    public let blue: Float
    public let alpha: Float

    public init(hex: String) throws {
        let value = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        guard value.count == 6 || value.count == 8, let packed = UInt64(value, radix: 16) else {
            throw AurexRenderError.invalidManifest("color '\(hex)' must be #RRGGBB or #RRGGBBAA")
        }
        let rgba = value.count == 6 ? (packed << 8) | 0xFF : packed
        red = Float((rgba >> 24) & 0xFF) / 255
        green = Float((rgba >> 16) & 0xFF) / 255
        blue = Float((rgba >> 8) & 0xFF) / 255
        alpha = Float(rgba & 0xFF) / 255
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        try self.init(hex: container.decode(String.self))
    }
}

public struct ManifestDocument: Sendable {
    public let url: URL
    public let manifest: RenderManifest
    public let sha256: String

    public static func load(from url: URL) throws -> ManifestDocument {
        let resolvedURL = url.standardizedFileURL
        let data = try Data(contentsOf: resolvedURL)
        let manifest: RenderManifest
        do {
            manifest = try JSONDecoder().decode(RenderManifest.self, from: data)
        } catch let error as AurexRenderError {
            throw error
        } catch {
            throw AurexRenderError.invalidManifest(error.localizedDescription)
        }
        try ManifestValidator.validate(manifest)
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        return ManifestDocument(url: resolvedURL, manifest: manifest, sha256: digest)
    }
}

public enum ManifestValidator {
    public static func validate(_ manifest: RenderManifest) throws {
        guard manifest.schemaVersion == AurexRenderCore.manifestSchemaVersion else {
            throw AurexRenderError.invalidManifest(
                "schemaVersion \(manifest.schemaVersion) is unsupported; expected \(AurexRenderCore.manifestSchemaVersion)"
            )
        }
        let canvas = manifest.canvas
        guard (16...8192).contains(canvas.width), (16...8192).contains(canvas.height) else {
            throw AurexRenderError.invalidManifest("canvas dimensions must be between 16 and 8192 pixels")
        }
        guard canvas.width.isMultiple(of: 2), canvas.height.isMultiple(of: 2) else {
            throw AurexRenderError.invalidManifest("H.264 canvas dimensions must be even")
        }
        guard canvas.frameRate.numerator > 0,
              canvas.frameRate.numerator <= Int(Int32.max),
              canvas.frameRate.denominator > 0,
              canvas.frameRate.framesPerSecond >= 1,
              canvas.frameRate.framesPerSecond <= 240 else {
            throw AurexRenderError.invalidManifest("frameRate must resolve to 1...240 fps")
        }
        guard (1...2_000_000).contains(canvas.frameCount) else {
            throw AurexRenderError.invalidManifest("frameCount must be between 1 and 2,000,000")
        }
        guard canvas.backgroundColor.alpha == 1 else {
            throw AurexRenderError.invalidManifest("backgroundColor must be opaque for H.264 output")
        }
        guard (100_000...200_000_000).contains(manifest.output.bitRate) else {
            throw AurexRenderError.invalidManifest("output.bitRate must be between 100,000 and 200,000,000")
        }
        guard (1...30).contains(manifest.output.keyFrameIntervalSeconds) else {
            throw AurexRenderError.invalidManifest("output.keyFrameIntervalSeconds must be between 1 and 30")
        }

        var identifiers = Set<String>()
        for layer in manifest.layers {
            guard !layer.id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw AurexRenderError.invalidManifest("layer id cannot be empty")
            }
            guard identifiers.insert(layer.id).inserted else {
                throw AurexRenderError.invalidManifest("duplicate layer id '\(layer.id)'")
            }
            let endFrame = layer.resolvedEndFrame(frameCount: canvas.frameCount)
            guard layer.startFrame >= 0, endFrame > layer.startFrame, endFrame <= canvas.frameCount else {
                throw AurexRenderError.invalidManifest("layer '\(layer.id)' has an invalid frame range")
            }
            try validate(rect: layer.rect, layerID: layer.id)
            if let endRect = layer.endRect {
                try validate(rect: endRect, layerID: layer.id)
            }
            guard (0...1).contains(layer.opacity),
                  layer.endOpacity.map({ (0...1).contains($0) }) ?? true else {
                throw AurexRenderError.invalidManifest("layer '\(layer.id)' opacity must be between 0 and 1")
            }
            switch layer.type {
            case .solid:
                guard layer.color != nil else {
                    throw AurexRenderError.invalidManifest("solid layer '\(layer.id)' requires color")
                }
            case .image:
                guard let source = layer.source, isSafeRelativePath(source) else {
                    throw AurexRenderError.invalidManifest(
                        "image layer '\(layer.id)' requires a relative source path without '..'"
                    )
                }
            }
        }
    }

    private static func validate(rect: NormalizedRect, layerID: String) throws {
        let values = [rect.x, rect.y, rect.width, rect.height]
        guard values.allSatisfy(\.isFinite), rect.width > 0, rect.height > 0 else {
            throw AurexRenderError.invalidManifest("layer '\(layerID)' rect must be finite with positive size")
        }
    }

    private static func isSafeRelativePath(_ path: String) -> Bool {
        guard !path.isEmpty, !(path as NSString).isAbsolutePath else { return false }
        return !path.split(separator: "/", omittingEmptySubsequences: false).contains("..")
    }
}
