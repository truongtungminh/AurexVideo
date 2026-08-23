import Testing
@testable import AurexRenderCore
import CoreMedia
import Foundation

@Test func exposesMVPContractVersion() {
    #expect(AurexRenderCore.manifestSchemaVersion == 1)
    #expect(!AurexRenderCore.version.isEmpty)
}

@Test func decodesAndValidatesFrameIndexedManifest() throws {
    let data = Data(
        """
        {
          "schemaVersion": 1,
          "canvas": {
            "width": 1920,
            "height": 1080,
            "frameRate": {"numerator": 30000, "denominator": 1001},
            "frameCount": 60,
            "backgroundColor": "#101820"
          },
          "layers": [{
            "id": "card",
            "type": "solid",
            "rect": {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.3},
            "endRect": {"x": 0.5, "y": 0.2, "width": 0.4, "height": 0.3},
            "color": "#E8B44FFF"
          }]
        }
        """.utf8
    )
    let manifest = try JSONDecoder().decode(RenderManifest.self, from: data)
    try ManifestValidator.validate(manifest)
    #expect(manifest.output.hardwareAcceleration == .prefer)
    #expect(manifest.layers[0].rect(at: 59, frameCount: 60).x == 0.5)
}

@Test func rationalTimelineHasExactPresentationTimes() {
    let rate = FrameRate(numerator: 30_000, denominator: 1_001)
    let timeline = FrameTimeline(frameRate: rate, frameCount: 60)
    #expect(timeline.presentationTime(forFrame: 1) == CMTime(value: 1_001, timescale: 30_000))
    #expect(timeline.duration == CMTime(value: 60_060, timescale: 30_000))
}

@Test func rejectsOddH264Dimensions() throws {
    let data = Data(
        """
        {
          "schemaVersion": 1,
          "canvas": {
            "width": 641,
            "height": 360,
            "frameRate": {"numerator": 30, "denominator": 1},
            "frameCount": 1,
            "backgroundColor": "#000000"
          }
        }
        """.utf8
    )
    let manifest = try JSONDecoder().decode(RenderManifest.self, from: data)
    do {
        try ManifestValidator.validate(manifest)
        Issue.record("Expected odd dimensions to fail validation")
    } catch let error as AurexRenderError {
        #expect(error.localizedDescription.contains("even"))
    }
}

private extension FrameRate {
    init(numerator: Int, denominator: Int) {
        self.initForTests(numerator: numerator, denominator: denominator)
    }

    initForTests(numerator: Int, denominator: Int) {
        let data = Data("{\"numerator\":\(numerator),\"denominator\":\(denominator)}".utf8)
        self = try! JSONDecoder().decode(FrameRate.self, from: data)
    }
}
