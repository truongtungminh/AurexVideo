import AVFoundation
import CoreMedia
import CoreVideo
import Darwin
import Foundation

public struct RawFrameEncodingOptions: Sendable {
    public let width: Int
    public let height: Int
    public let frameRate: FrameRate
    public let frameCount: Int
    public let bitRate: Int
    public let hardwareAcceleration: HardwareAccelerationPolicy
    public let keyFrameIntervalSeconds: Int

    public init(
        width: Int,
        height: Int,
        frameRate: FrameRate,
        frameCount: Int,
        bitRate: Int = 8_000_000,
        hardwareAcceleration: HardwareAccelerationPolicy = .prefer,
        keyFrameIntervalSeconds: Int = 2
    ) {
        self.width = width
        self.height = height
        self.frameRate = frameRate
        self.frameCount = frameCount
        self.bitRate = bitRate
        self.hardwareAcceleration = hardwareAcceleration
        self.keyFrameIntervalSeconds = keyFrameIntervalSeconds
    }
}

private final class H264MovieWriter {
    private let writer: AVAssetWriter
    private let input: AVAssetWriterInput
    private let adaptor: AVAssetWriterInputPixelBufferAdaptor

    init(
        outputURL: URL,
        width: Int,
        height: Int,
        frameRate: FrameRate,
        bitRate: Int,
        keyFrameIntervalSeconds: Int,
        candidate: EncoderCandidate
    ) throws {
        let frameRateValue = frameRate.framesPerSecond
        let keyFrameInterval = max(
            1,
            Int((frameRateValue * Double(keyFrameIntervalSeconds)).rounded())
        )
        var compressionProperties: [String: Any] = [
            AVVideoAverageBitRateKey: bitRate,
            AVVideoExpectedSourceFrameRateKey: frameRateValue,
            AVVideoMaxKeyFrameIntervalKey: keyFrameInterval,
            AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
            AVVideoAllowFrameReorderingKey: false,
        ]
        compressionProperties[AVVideoAverageNonDroppableFrameRateKey] = frameRateValue

        var outputSettings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
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
            kCVPixelBufferWidthKey as String: width,
            kCVPixelBufferHeightKey as String: height,
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
                    width: manifest.canvas.width,
                    height: manifest.canvas.height,
                    frameRate: manifest.canvas.frameRate,
                    bitRate: manifest.output.bitRate,
                    keyFrameIntervalSeconds: manifest.output.keyFrameIntervalSeconds,
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

    /// Encode tightly packed BGRA frames supplied on a pipe. This is the
    /// universal compatibility entry point: Browser/other scene adapters may
    /// rasterize a frame, while Aurex Render Core still owns the delivery
    /// timeline and H.264/VideoToolbox encoding for every AurexVideo project.
    public func encodeRawBGRA(
        from input: FileHandle,
        options: RawFrameEncodingOptions,
        to outputURL: URL,
        overwrite: Bool = false,
        progress: ((RenderProgress) -> Void)? = nil
    ) throws -> RenderReport {
        guard options.width >= 16,
              options.height >= 16,
              options.width.isMultiple(of: 2),
              options.height.isMultiple(of: 2) else {
            throw AurexRenderError.invalidManifest("raw BGRA dimensions must be even and at least 16px")
        }
        guard options.frameRate.numerator > 0,
              options.frameRate.denominator > 0,
              (1...240).contains(options.frameRate.framesPerSecond) else {
            throw AurexRenderError.invalidManifest("raw BGRA frameRate must resolve to 1...240 fps")
        }
        guard (1...2_000_000).contains(options.frameCount) else {
            throw AurexRenderError.invalidManifest("raw BGRA frameCount must be between 1 and 2,000,000")
        }
        guard (100_000...200_000_000).contains(options.bitRate) else {
            throw AurexRenderError.invalidManifest("raw BGRA bitRate must be between 100,000 and 200,000,000")
        }
        guard outputURL.pathExtension.lowercased() == "mp4" else {
            throw AurexRenderError.invalidManifest("output path must use the .mp4 extension")
        }

        let fileManager = FileManager.default
        let outputURL = outputURL.standardizedFileURL
        let outputExists = fileManager.fileExists(atPath: outputURL.path)
        guard overwrite || !outputExists else {
            throw AurexRenderError.renderFailed("output already exists; pass overwrite explicitly")
        }
        try fileManager.createDirectory(
            at: outputURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )

        let encoders = try H264EncoderDiscovery.availableEncoders()
        let candidates = try H264EncoderDiscovery.candidates(
            for: options.hardwareAcceleration,
            available: encoders
        )
        guard let candidate = candidates.first else {
            throw AurexRenderError.unavailable("no H.264 encoder candidate is available")
        }

        let temporaryURL = outputURL.deletingLastPathComponent().appendingPathComponent(
            ".\(outputURL.deletingPathExtension().lastPathComponent)-aurex-\(UUID().uuidString).mp4"
        )
        let timeline = FrameTimeline(frameRate: options.frameRate, frameCount: options.frameCount)
        let frameBytes = options.width * options.height * 4
        let renderStarted = ProcessInfo.processInfo.systemUptime
        var movieWriter: H264MovieWriter?

        do {
            movieWriter = try H264MovieWriter(
                outputURL: temporaryURL,
                width: options.width,
                height: options.height,
                frameRate: options.frameRate,
                bitRate: options.bitRate,
                keyFrameIntervalSeconds: options.keyFrameIntervalSeconds,
                candidate: candidate
            )
            for frameIndex in 0..<options.frameCount {
                guard let movieWriter else {
                    throw AurexRenderError.writerFailed("writer unexpectedly unavailable")
                }
                let data = try Self.readExactly(frameBytes, from: input)
                let pixelBuffer = try movieWriter.makePixelBuffer()
                try Self.copyBGRA(data, to: pixelBuffer, width: options.width, height: options.height)
                try movieWriter.append(pixelBuffer, at: timeline.presentationTime(forFrame: frameIndex))
                progress?(RenderProgress(completedFrames: frameIndex + 1, totalFrames: options.frameCount))
            }
            guard let movieWriter else {
                throw AurexRenderError.writerFailed("writer unexpectedly unavailable")
            }
            try movieWriter.finish(at: timeline.duration)

            if outputExists {
                _ = try fileManager.replaceItemAt(outputURL, withItemAt: temporaryURL)
            } else {
                try fileManager.moveItem(at: temporaryURL, to: outputURL)
            }

            let attributes = try fileManager.attributesOfItem(atPath: outputURL.path)
            let size = (attributes[.size] as? NSNumber)?.uint64Value ?? 0
            let lastPTS = timeline.presentationTime(forFrame: max(0, options.frameCount - 1))
            return RenderReport(
                schemaVersion: 1,
                coreVersion: AurexRenderCore.version,
                manifestSHA256: "raw-bgra-stream",
                outputPath: outputURL.path,
                outputSizeBytes: size,
                width: options.width,
                height: options.height,
                pixelFormat: "32BGRA->420v",
                codec: "H.264",
                colorSpace: "BT.709 video-range",
                audio: "not-supported-in-core-video-pass",
                zeroCopyVideoPath: true,
                pipeline: "Raw BGRA->IOSurface CVPixelBuffer->AVAssetWriter/VideoToolbox",
                metalDevice: AurexRenderCore.metalDeviceName ?? "unavailable",
                timeline: TimelineReport(
                    frameRateNumerator: options.frameRate.numerator,
                    frameRateDenominator: options.frameRate.denominator,
                    frameCount: options.frameCount,
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
                    fallbackUsed: candidate.fallbackUsed,
                    failedAttempts: []
                ),
                renderSeconds: ProcessInfo.processInfo.systemUptime - renderStarted
            )
        } catch {
            movieWriter?.cancel()
            try? fileManager.removeItem(at: temporaryURL)
            throw error
        }
    }

    private static func readExactly(_ byteCount: Int, from input: FileHandle) throws -> Data {
        var result = Data()
        result.reserveCapacity(byteCount)
        while result.count < byteCount {
            guard let chunk = try input.read(upToCount: byteCount - result.count), !chunk.isEmpty else {
                throw AurexRenderError.renderFailed(
                    "raw BGRA input ended at \(result.count) bytes of \(byteCount)"
                )
            }
            result.append(chunk)
        }
        return result
    }

    private static func copyBGRA(
        _ data: Data,
        to pixelBuffer: CVPixelBuffer,
        width: Int,
        height: Int
    ) throws {
        guard data.count == width * height * 4 else {
            throw AurexRenderError.renderFailed("raw BGRA frame has an unexpected byte count")
        }
        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, []) }
        guard let baseAddress = CVPixelBufferGetBaseAddress(pixelBuffer) else {
            throw AurexRenderError.writerFailed("CVPixelBuffer base address is unavailable")
        }
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let sourceBytesPerRow = width * 4
        data.withUnsafeBytes { rawBuffer in
            guard let source = rawBuffer.baseAddress else { return }
            for row in 0..<height {
                memcpy(
                    baseAddress.advanced(by: row * bytesPerRow),
                    source.advanced(by: row * sourceBytesPerRow),
                    sourceBytesPerRow
                )
            }
        }
    }
}
