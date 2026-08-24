import AVFoundation
import CoreMedia
import CoreVideo
import Foundation

final class VideoFrameDecoder {
    let url: URL
    let frameRate: Double
    let duration: Double

    private let asset: AVAsset
    private let track: AVAssetTrack
    private var reader: AVAssetReader
    private var output: AVAssetReaderTrackOutput
    private var nextFrameIndex = 0
    private var currentFrame: CVPixelBuffer?

    init(url: URL) throws {
        self.url = url
        let asset = AVAsset(url: url)
        guard let track = asset.tracks(withMediaType: .video).first else {
            throw AurexRenderError.invalidManifest("video source '\(url.lastPathComponent)' has no video track")
        }
        self.asset = asset
        self.track = track
        frameRate = track.nominalFrameRate > 0 ? Double(track.nominalFrameRate) : 30
        duration = max(0.001, CMTimeGetSeconds(asset.duration))
        let (reader, output) = try Self.makeReader(asset: asset, track: track)
        self.reader = reader
        self.output = output
        guard reader.startReading() else {
            throw AurexRenderError.renderFailed("cannot start video reader for '\(url.lastPathComponent)'")
        }
    }

    func frame(at index: Int) throws -> CVPixelBuffer {
        let target = max(0, index)
        if target < nextFrameIndex {
            try reset()
        }
        while nextFrameIndex <= target {
            guard let sample = output.copyNextSampleBuffer(),
                  let imageBuffer = CMSampleBufferGetImageBuffer(sample) else {
                throw AurexRenderError.renderFailed(
                    "video source '\(url.lastPathComponent)' ended before frame \(target)"
                )
            }
            currentFrame = imageBuffer
            nextFrameIndex += 1
        }
        guard let currentFrame else {
            throw AurexRenderError.renderFailed("video source '\(url.lastPathComponent)' returned no frame")
        }
        return currentFrame
    }

    private func reset() throws {
        reader.cancelReading()
        let (nextReader, nextOutput) = try Self.makeReader(asset: asset, track: track)
        reader = nextReader
        output = nextOutput
        nextFrameIndex = 0
        currentFrame = nil
        guard reader.startReading() else {
            throw AurexRenderError.renderFailed("cannot restart video reader for '\(url.lastPathComponent)'")
        }
    }

    private static func makeReader(
        asset: AVAsset,
        track: AVAssetTrack
    ) throws -> (AVAssetReader, AVAssetReaderTrackOutput) {
        let reader: AVAssetReader
        do {
            reader = try AVAssetReader(asset: asset)
        } catch {
            throw AurexRenderError.invalidManifest(
                "cannot open video source: \(error.localizedDescription)"
            )
        }
        let output = AVAssetReaderTrackOutput(
            track: track,
            outputSettings: [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
                kCVPixelBufferMetalCompatibilityKey as String: true,
            ]
        )
        output.alwaysCopiesSampleData = false
        guard reader.canAdd(output) else {
            throw AurexRenderError.renderFailed("cannot attach video reader output")
        }
        reader.add(output)
        return (reader, output)
    }
}
