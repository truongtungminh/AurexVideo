import Foundation

public struct RenderProgress: Codable, Sendable {
    public let completedFrames: Int
    public let totalFrames: Int

    public var fractionCompleted: Double {
        Double(completedFrames) / Double(totalFrames)
    }
}

public struct EncoderReport: Codable, Sendable {
    public let path: EncoderPath
    public let identifier: String?
    public let name: String?
    public let hardwareAccelerated: Bool?
    public let fallbackUsed: Bool
    public let failedAttempts: [String]
}

public struct TimelineReport: Codable, Sendable {
    public let frameRateNumerator: Int
    public let frameRateDenominator: Int
    public let frameCount: Int
    public let firstPresentationTimeValue: Int64
    public let lastPresentationTimeValue: Int64
    public let durationValue: Int64
    public let timescale: Int32
    public let durationSeconds: Double
}

public struct RenderReport: Codable, Sendable {
    public let schemaVersion: Int
    public let coreVersion: String
    public let manifestSHA256: String
    public let outputPath: String
    public let outputSizeBytes: UInt64
    public let width: Int
    public let height: Int
    public let pixelFormat: String
    public let codec: String
    public let colorSpace: String
    public let audio: String
    public let zeroCopyVideoPath: Bool
    public let pipeline: String
    public let metalDevice: String
    public let timeline: TimelineReport
    public let encoder: EncoderReport
    public let renderSeconds: Double
}

public struct RenderCapabilities: Codable, Sendable {
    public let coreVersion: String
    public let manifestSchemaVersion: Int
    public let metalDevice: String?
    public let h264Encoders: [EncoderDescriptor]
    public let supportedLayerTypes: [String]
    public let supportedHardwarePolicies: [String]
}
