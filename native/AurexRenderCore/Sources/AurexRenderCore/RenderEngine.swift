import AVFoundation
import CoreMedia
import CoreVideo
import Foundation

private final class H264MovieWriter {
    private let writer: AVAssetWriter
    private let input: AVAssetWriterInput
    private let adaptor: AVAssetWriterInputPixelBufferAdaptor

    init(
        outputURL: URL,
        manifest: RenderManifest,
        candidate: EncoderCandidate
    ) throws {
        let canvas = manifest.canvas
        let frameRate = canvas.frameRate.framesPerSecond
        let keyFrameInterval = max(
            1,
            Int((frameRate * Double(manifest.output.keyFrameIntervalSeconds)).rounded())
        )
        var compressionProperties: [String: Any] = [
            AVVideoAverageBitRateKey: manifest.output.bitRate,
            AVVideoExpectedSourceFrameRateKey: frameRate,
            AVVideoMaxKeyFrameIntervalKey: keyFrameInterval,
            AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
            AVVideoAllowFrameReorderingKey: false,
        ]
        compressionProperties[AVVideoAverageNonDroppableFrameRateKey] = frameRate

        var outputSettings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: canvas.width,
            AVVideoHeightKey: canvas.height,
            AVVideoCompressionPropertiesKey: compressionProperties,
            AVVideoColorPropertiesKey: [
                AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2,
                AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2,
                AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2,
            ],
        ]
        if let specification = candidate.specification {
            outputSettings[AVVideoEncoderSpecificationKey] = specification
        }

        do {
            writer = try AVAssetWriter(outputURL: outputURL, fileType: .mp4)
        } catch {
            throw AurexRenderError.writerFailed("cannot create AVAssetWriter: \(error.localizedDescription)")
        }
        writer.shouldOptimizeForNetworkUse = true
        guard writer.canApply(outputSettings: outputSettings, forMediaType: .video) else {
            throw AurexRenderError.writerFailed("AVFoundation rejected H.264 output settings")
        }
        input = AVAssetWriterInput(mediaType: .video, outputSettings: outputSettings)
        input.expectsMediaDataInRealTime = false

        let pixelBufferAttributes: [String: Any] = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: canvas.width,
            kCVPixelBufferHeightKey as String: canvas.height,
            kCVPixelBufferMetalCompatibilityKey as String: true,
            kCVPixelBufferIOSurfacePropertiesKey as String: [String: Any](),
        ]
        adaptor = AVAssetWriterInputPixelBufferAdaptor(
            assetWriterInput: input,
            sourcePixelBufferAttributes: pixelBufferAttributes
        )
        guard writer.canAdd(input) else {
            throw AurexRenderError.writerFailed("cannot add H.264 input to AVAssetWriter")
        }
        writer.add(input)
        guard writer.startWriting() else {
            throw Self.writerError(writer, context: "startWriting")
        }
        writer.startSession(atSourceTime: .zero)
        guard adaptor.pixelBufferPool != nil else {
            writer.cancelWriting()
            throw AurexRenderError.writerFailed("AVAssetWriter did not create a CVPixelBufferPool")
        }
    }

    func makePixelBuffer() throws -> CVPixelBuffer {
        guard let pool = adaptor.pixelBufferPool else {
            throw AurexRenderError.writerFailed("CVPixelBufferPool is unavailable")
        }
        var pixelBuffer: CVPixelBuffer?
        let status = CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &pixelBuffer)
        guard status == kCVReturnSuccess, let pixelBuffer else {
            throw AurexRenderError.writerFailed("CVPixelBufferPool allocation failed with CVReturn \(status)")
        }
        return pixelBuffer
    }

    func append(_ pixelBuffer: CVPixelBuffer, at presentationTime: CMTime) throws {
        let deadline = Date().addingTimeInterval(30)
        while !input.isReadyForMoreMediaData {
            if writer.status == .failed || writer.status == .cancelled {
                throw Self.writerError(writer, context: "encoder backpressure")
            }
            guard Date() < deadline else {
                throw AurexRenderError.writerFailed("encoder remained backpressured for 30 seconds")
            }
            Thread.sleep(forTimeInterval: 0.001)
        }
        guard adaptor.append(pixelBuffer, withPresentationTime: presentationTime) else {
            throw Self.writerError(writer, context: "append frame at \(presentationTime.seconds)s")
        }
    }

    func finish(at endTime: CMTime) throws {
        writer.endSession(atSourceTime: endTime)
        input.markAsFinished()
        let semaphore = DispatchSemaphore(value: 0)
        writer.finishWriting {
            semaphore.signal()
        }
        semaphore.wait()
        guard writer.status == .completed else {
            throw Self.writerError(writer, context: "finishWriting")
        }
    }

    func cancel() {
        writer.cancelWriting()
    }

    private static func writerError(_ writer: AVAssetWriter, context: String) -> AurexRenderError {
        let detail = writer.error?.localizedDescription ?? "status \(writer.status.rawValue)"
        return .writerFailed("\(context): \(detail)")
    }
}

public final class RenderEngine {
    public init() {}

    public static func capabilities() throws -> RenderCapabilities {
        RenderCapabilities(
            coreVersion: AurexRenderCore.version,
            manifestSchemaVersion: AurexRenderCore.manifestSchemaVersion,
            metalDevice: AurexRenderCore.metalDeviceName,
            h264Encoders: try H264EncoderDiscovery.availableEncoders(),
            supportedLayerTypes: [SceneLayerType.solid.rawValue, SceneLayerType.image.rawValue],
            supportedHardwarePolicies: [
                HardwareAccelerationPolicy.automatic.rawValue,
                HardwareAccelerationPolicy.prefer.rawValue,
                HardwareAccelerationPolicy.require.rawValue,
                HardwareAccelerationPolicy.software.rawValue,
            ]
        )
    }

    public func render(
        document: ManifestDocument,
        to outputURL: URL,
        overwrite: Bool = false,
        progress: ((RenderProgress) -> Void)? = nil
    ) throws -> RenderReport {
        let outputURL = outputURL.standardizedFileURL
        guard outputURL.pathExtension.lowercased() == "mp4" else {
            throw AurexRenderError.invalidManifest("output path must use the .mp4 extension")
        }
        let fileManager = FileManager.default
        let outputExists = fileManager.fileExists(atPath: outputURL.path)
        guard overwrite || !outputExists else {
            throw AurexRenderError.renderFailed("output already exists; pass overwrite explicitly")
        }
        try fileManager.createDirectory(
            at: outputURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )

        let manifest = document.manifest
        let timeline = FrameTimeline(
            frameRate: manifest.canvas.frameRate,
            frameCount: manifest.canvas.frameCount
        )
        let compositor = try MetalCompositor(document: document)
        let encoders = try H264EncoderDiscovery.availableEncoders()
        let candidates = try H264EncoderDiscovery.candidates(
            for: manifest.output.hardwareAcceleration,
            available: encoders
        )
        var failedAttempts: [String] = []
        let renderStarted = ProcessInfo.processInfo.systemUptime

        for (attemptIndex, candidate) in candidates.enumerated() {
            let temporaryURL = outputURL.deletingLastPathComponent().appendingPathComponent(
                ".\(outputURL.deletingPathExtension().lastPathComponent)-aurex-\(UUID().uuidString).mp4"
            )
            var movieWriter: H264MovieWriter?
            do {
                movieWriter = try H264MovieWriter(
                    outputURL: temporaryURL,
                    manifest: manifest,
                    candidate: candidate
                )
                for frameIndex in 0..<manifest.canvas.frameCount {
                    try autoreleasepool {
                        guard let movieWriter else {
                            throw AurexRenderError.writerFailed("writer unexpectedly unavailable")
                        }
                        let pixelBuffer = try movieWriter.makePixelBuffer()
                        try compositor.render(frameIndex: frameIndex, into: pixelBuffer)
                        try movieWriter.append(
                            pixelBuffer,
                            at: timeline.presentationTime(forFrame: frameIndex)
                        )
                    }
                    progress?(
                        RenderProgress(
                            completedFrames: frameIndex + 1,
                            totalFrames: manifest.canvas.frameCount
                        )
                    )
                }
                try movieWriter?.finish(at: timeline.duration)

                if outputExists {
                    _ = try fileManager.replaceItemAt(outputURL, withItemAt: temporaryURL)
                } else {
                    try fileManager.moveItem(at: temporaryURL, to: outputURL)
                }

                let attributes = try fileManager.attributesOfItem(atPath: outputURL.path)
                let size = (attributes[.size] as? NSNumber)?.uint64Value ?? 0
                let lastPTS = timeline.presentationTime(
                    forFrame: max(0, manifest.canvas.frameCount - 1)
                )
                let elapsed = ProcessInfo.processInfo.systemUptime - renderStarted
                return RenderReport(
                    schemaVersion: 1,
                    coreVersion: AurexRenderCore.version,
                    manifestSHA256: document.sha256,
                    outputPath: outputURL.path,
                    outputSizeBytes: size,
                    width: manifest.canvas.width,
                    height: manifest.canvas.height,
                    pixelFormat: "32BGRA->420v",
                    codec: "H.264",
                    colorSpace: "BT.709 video-range",
                    audio: "not-supported-in-mvp",
                    zeroCopyVideoPath: true,
                    pipeline: "Metal->IOSurface CVPixelBuffer->AVAssetWriter/VideoToolbox",
                    metalDevice: compositor.deviceName,
                    timeline: TimelineReport(
                        frameRateNumerator: manifest.canvas.frameRate.numerator,
                        frameRateDenominator: manifest.canvas.frameRate.denominator,
                        frameCount: manifest.canvas.frameCount,
                        firstPresentationTimeValue: 0,
                        lastPresentationTimeValue: lastPTS.value,
                        durationValue: timeline.duration.value,
                        timescale: timeline.duration.timescale,
                        durationSeconds: timeline.duration.seconds
                    ),
                    encoder: EncoderReport(
                        path: candidate.path,
                        identifier: candidate.descriptor?.identifier,
                        name: candidate.descriptor?.name,
                        hardwareAccelerated: candidate.descriptor?.isHardwareAccelerated,
                        fallbackUsed: candidate.fallbackUsed || attemptIndex > 0,
                        failedAttempts: failedAttempts
                    ),
                    renderSeconds: elapsed
                )
            } catch let error as AurexRenderError {
                movieWriter?.cancel()
                try? fileManager.removeItem(at: temporaryURL)
                guard case .writerFailed = error else { throw error }
                let name = candidate.descriptor?.identifier ?? candidate.path.rawValue
                failedAttempts.append("\(name): \(error.localizedDescription)")
                continue
            } catch {
                movieWriter?.cancel()
                try? fileManager.removeItem(at: temporaryURL)
                throw error
            }
        }

        throw AurexRenderError.writerFailed(
            "all H.264 encoder attempts failed: \(failedAttempts.joined(separator: " | "))"
        )
    }
}
