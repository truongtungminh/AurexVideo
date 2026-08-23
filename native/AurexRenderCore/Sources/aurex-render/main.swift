import AurexRenderCore
import Darwin
import Foundation

private struct ValidationResult: Codable {
    let valid: Bool
    let manifestPath: String
    let manifestSHA256: String
    let schemaVersion: Int
    let width: Int
    let height: Int
    let frameRateNumerator: Int
    let frameRateDenominator: Int
    let frameCount: Int
    let layerCount: Int
}

private struct ErrorResult: Codable {
    let error: String
    let message: String
}

private enum CLI {
    static let help = """
    AurexRenderCore MVP

    Usage:
      aurex-render capabilities
      aurex-render validate --manifest <manifest.json>
      aurex-render render --manifest <manifest.json> --output <video.mp4> [options]

    Render options:
      --report <report.json>  Sidecar path (default: <video>.render-report.json)
      --overwrite             Atomically replace an existing output after success
      --quiet                 Suppress human-readable progress on stderr
    """

    static func run(_ arguments: [String]) throws {
        guard let command = arguments.first, command != "help", command != "--help", command != "-h" else {
            print(help)
            return
        }
        switch command {
        case "capabilities":
            guard arguments.count == 1 else {
                throw AurexRenderError.invalidManifest("capabilities does not accept options")
            }
            try writeJSON(RenderEngine.capabilities(), to: .standardOutput)
        case "validate":
            let options = try parse(arguments: Array(arguments.dropFirst()), allowedFlags: [])
            guard let manifestPath = options.values["--manifest"] else {
                throw AurexRenderError.invalidManifest("validate requires --manifest")
            }
            guard options.values.count == 1 else {
                throw AurexRenderError.invalidManifest("validate only accepts --manifest")
            }
            let document = try ManifestDocument.load(
                from: URL(fileURLWithPath: manifestPath).standardizedFileURL
            )
            let canvas = document.manifest.canvas
            try writeJSON(
                ValidationResult(
                    valid: true,
                    manifestPath: document.url.path,
                    manifestSHA256: document.sha256,
                    schemaVersion: document.manifest.schemaVersion,
                    width: canvas.width,
                    height: canvas.height,
                    frameRateNumerator: canvas.frameRate.numerator,
                    frameRateDenominator: canvas.frameRate.denominator,
                    frameCount: canvas.frameCount,
                    layerCount: document.manifest.layers.count
                ),
                to: .standardOutput
            )
        case "render":
            let options = try parse(
                arguments: Array(arguments.dropFirst()),
                allowedFlags: ["--overwrite", "--quiet"]
            )
            let allowedValues = Set(["--manifest", "--output", "--report"])
            guard Set(options.values.keys).isSubset(of: allowedValues) else {
                let unknown = Set(options.values.keys).subtracting(allowedValues).sorted().joined(separator: ", ")
                throw AurexRenderError.invalidManifest("unknown render option(s): \(unknown)")
            }
            guard let manifestPath = options.values["--manifest"],
                  let outputPath = options.values["--output"] else {
                throw AurexRenderError.invalidManifest("render requires --manifest and --output")
            }
            let document = try ManifestDocument.load(
                from: URL(fileURLWithPath: manifestPath).standardizedFileURL
            )
            let outputURL = URL(fileURLWithPath: outputPath).standardizedFileURL
            let reportURL = options.values["--report"].map {
                URL(fileURLWithPath: $0).standardizedFileURL
            } ?? outputURL.deletingPathExtension().appendingPathExtension("render-report.json")
            var lastPrintedPercent = -1
            let quiet = options.flags.contains("--quiet")
            let report = try RenderEngine().render(
                document: document,
                to: outputURL,
                overwrite: options.flags.contains("--overwrite")
            ) { progress in
                guard !quiet else { return }
                let percent = Int((progress.fractionCompleted * 100).rounded(.down))
                if percent == 100 || percent >= lastPrintedPercent + 10 {
                    FileHandle.standardError.write(
                        Data("Rendering frames: \(progress.completedFrames)/\(progress.totalFrames) (\(percent)%)\n".utf8)
                    )
                    lastPrintedPercent = percent
                }
            }
            let reportData = try encodedJSON(report)
            try FileManager.default.createDirectory(
                at: reportURL.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            try reportData.write(to: reportURL, options: .atomic)
            FileHandle.standardOutput.write(reportData)
        default:
            throw AurexRenderError.invalidManifest("unknown command '\(command)'\n\n\(help)")
        }
    }

    private struct ParsedOptions {
        var values: [String: String] = [:]
        var flags: Set<String> = []
    }

    private static func parse(arguments: [String], allowedFlags: Set<String>) throws -> ParsedOptions {
        var result = ParsedOptions()
        var index = 0
        while index < arguments.count {
            let option = arguments[index]
            guard option.hasPrefix("--") else {
                throw AurexRenderError.invalidManifest("unexpected positional argument '\(option)'")
            }
            if allowedFlags.contains(option) {
                guard result.flags.insert(option).inserted else {
                    throw AurexRenderError.invalidManifest("duplicate option '\(option)'")
                }
                index += 1
                continue
            }
            guard index + 1 < arguments.count, !arguments[index + 1].hasPrefix("--") else {
                throw AurexRenderError.invalidManifest("option '\(option)' requires a value")
            }
            guard result.values[option] == nil else {
                throw AurexRenderError.invalidManifest("duplicate option '\(option)'")
            }
            result.values[option] = arguments[index + 1]
            index += 2
        }
        return result
    }

    private static func encodedJSON<T: Encodable>(_ value: T) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        var data = try encoder.encode(value)
        data.append(0x0A)
        return data
    }

    private static func writeJSON<T: Encodable>(_ value: T, to handle: FileHandle) throws {
        handle.write(try encodedJSON(value))
    }
}

do {
    try CLI.run(Array(CommandLine.arguments.dropFirst()))
} catch {
    let payload = ErrorResult(
        error: String(describing: type(of: error)),
        message: error.localizedDescription
    )
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    if var data = try? encoder.encode(payload) {
        data.append(0x0A)
        FileHandle.standardError.write(data)
    } else {
        FileHandle.standardError.write(Data("aurex-render failed: \(error.localizedDescription)\n".utf8))
    }
    exit(EXIT_FAILURE)
}
