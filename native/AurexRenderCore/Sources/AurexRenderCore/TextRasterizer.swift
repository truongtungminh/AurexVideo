import CoreGraphics
import CoreText
import Foundation

final class TextRasterizer {
    private let baseURL: URL
    private var registeredFontNames: [String: String] = [:]

    init(document: ManifestDocument) throws {
        baseURL = document.url.deletingLastPathComponent().standardizedFileURL
        let sources = Set(document.manifest.layers.compactMap(\.fontSource))
        for source in sources {
            let url = try Self.resolveAsset(source, relativeTo: baseURL)
            var registrationError: Unmanaged<CFError>?
            _ = CTFontManagerRegisterFontsForURL(url as CFURL, .process, &registrationError)
            if let descriptors = CTFontManagerCreateFontDescriptorsFromURL(url as CFURL) as? [CTFontDescriptor] {
                for descriptor in descriptors {
                    if let family = CTFontDescriptorCopyAttribute(descriptor, kCTFontFamilyNameAttribute) as? String,
                       let name = CTFontDescriptorCopyAttribute(descriptor, kCTFontNameAttribute) as? String {
                        registeredFontNames[family] = name
                        // The bundled Google Fonts Saira WOFF2 subset exposes
                        // its Core Text family as "Saira Thin", while the
                        // editor/Scene IR contract uses the CSS family name
                        // "Saira". Keep an alias so native text does not
                        // silently fall back to Inter when resolving it.
                        let normalizedFamily = family
                            .trimmingCharacters(in: .whitespacesAndNewlines)
                            .lowercased()
                        if normalizedFamily == "saira thin" {
                            registeredFontNames["Saira"] = name
                        }
                    }
                }
            }
            registrationError = nil
        }
    }

    func image(for layer: SceneLayer, canvasWidth: Int, canvasHeight: Int) throws -> CGImage {
        let width = max(1, Int(ceil(layer.rect.width * Double(canvasWidth))))
        let height = max(1, Int(ceil(layer.rect.height * Double(canvasHeight))))
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width * 4,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
                | CGBitmapInfo.byteOrder32Big.rawValue
        ) else {
            throw AurexRenderError.unavailable("CoreGraphics text bitmap context")
        }
        context.clear(CGRect(x: 0, y: 0, width: width, height: height))

        let attributed = try attributedString(for: layer)
        let lines = splitLines(from: attributed)
        let font = makeFont(for: layer)
        let lineHeight = max(CGFloat(layer.fontSize) * CGFloat(layer.lineHeight), CTFontGetAscent(font) + CTFontGetDescent(font))
        let totalHeight = lineHeight * CGFloat(max(1, lines.count))
        var baseline = (CGFloat(height) + totalHeight) / 2 - lineHeight + CTFontGetDescent(font)

        for lineText in lines {
            let line = CTLineCreateWithAttributedString(lineText as CFAttributedString)
            var ascent: CGFloat = 0
            var descent: CGFloat = 0
            var leading: CGFloat = 0
            let lineWidth = CGFloat(CTLineGetTypographicBounds(line, &ascent, &descent, &leading))
            let x: CGFloat
            switch layer.textAlignment {
            case .left:
                x = 0
            case .right:
                x = max(0, CGFloat(width) - lineWidth)
            case .center:
                x = max(0, (CGFloat(width) - lineWidth) / 2)
            }
            context.saveGState()
            context.textPosition = CGPoint(x: x, y: baseline)
            CTLineDraw(line, context)
            context.restoreGState()
            baseline -= lineHeight
        }
        guard let image = context.makeImage() else {
            throw AurexRenderError.renderFailed("CoreGraphics failed to rasterize text layer '\(layer.id)'")
        }
        return image
    }

    private func attributedString(for layer: SceneLayer) throws -> NSAttributedString {
        let result = NSMutableAttributedString()
        let font = makeFont(for: layer)
        let baseColor = cgColor(layer.textColor ?? RGBAColor(red: 1, green: 1, blue: 1, alpha: 1))
        let attributes: [NSAttributedString.Key: Any] = [
            NSAttributedString.Key(kCTFontAttributeName as String): font,
            NSAttributedString.Key(kCTForegroundColorAttributeName as String): baseColor,
        ]
        if !layer.spans.isEmpty {
            for span in layer.spans {
                result.append(NSAttributedString(
                    string: span.text,
                    attributes: [
                        NSAttributedString.Key(kCTFontAttributeName as String): font,
                        NSAttributedString.Key(kCTForegroundColorAttributeName as String): cgColor(span.color ?? layer.textColor ?? RGBAColor(red: 1, green: 1, blue: 1, alpha: 1)),
                    ]
                ))
                result.append(NSAttributedString(string: " ", attributes: attributes))
            }
            if result.length > 0 {
                result.deleteCharacters(in: NSRange(location: result.length - 1, length: 1))
            }
        } else if let text = layer.text {
            result.append(NSAttributedString(string: text, attributes: attributes))
        }
        return result.copy() as! NSAttributedString
    }

    private func splitLines(from source: NSAttributedString) -> [NSAttributedString] {
        guard source.length > 0 else { return [source] }
        var lines: [NSAttributedString] = []
        var start = 0
        let string = source.string as NSString
        for index in 0..<string.length where string.character(at: index) == 10 {
            lines.append(source.attributedSubstring(from: NSRange(location: start, length: index - start)))
            start = index + 1
        }
        lines.append(source.attributedSubstring(from: NSRange(location: start, length: string.length - start)))
        return lines.isEmpty ? [source] : lines
    }

    private func makeFont(for layer: SceneLayer) -> CTFont {
        let name = registeredFontNames[layer.fontFamily]
            ?? (layer.fontWeight >= 700 ? "Inter-Bold" : layer.fontFamily)
        return CTFontCreateWithName(name as CFString, CGFloat(layer.fontSize), nil)
    }

    private func cgColor(_ color: RGBAColor) -> CGColor {
        CGColor(
            red: CGFloat(color.red),
            green: CGFloat(color.green),
            blue: CGFloat(color.blue),
            alpha: CGFloat(color.alpha)
        )
    }

    private static func resolveAsset(_ source: String, relativeTo baseURL: URL) throws -> URL {
        guard !source.isEmpty, !source.hasPrefix("/"), !source.split(separator: "/").contains("..") else {
            throw AurexRenderError.invalidManifest("font source '\(source)' is unsafe")
        }
        let assetURL = baseURL.appendingPathComponent(source).resolvingSymlinksInPath().standardizedFileURL
        let basePath = baseURL.path.hasSuffix("/") ? baseURL.path : baseURL.path + "/"
        guard assetURL.path.hasPrefix(basePath), FileManager.default.fileExists(atPath: assetURL.path) else {
            throw AurexRenderError.invalidManifest("font source '\(source)' is missing")
        }
        return assetURL
    }
}
