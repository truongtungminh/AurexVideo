import CoreMedia
import Foundation

public struct FrameTimeline: Sendable {
    public let frameRate: FrameRate
    public let frameCount: Int

    public init(frameRate: FrameRate, frameCount: Int) {
        self.frameRate = frameRate
        self.frameCount = frameCount
    }

    public func presentationTime(forFrame frameIndex: Int) -> CMTime {
        CMTime(
            value: Int64(frameIndex) * Int64(frameRate.denominator),
            timescale: CMTimeScale(frameRate.numerator)
        )
    }

    public var frameDuration: CMTime {
        CMTime(value: Int64(frameRate.denominator), timescale: CMTimeScale(frameRate.numerator))
    }

    public var duration: CMTime {
        presentationTime(forFrame: frameCount)
    }
}
