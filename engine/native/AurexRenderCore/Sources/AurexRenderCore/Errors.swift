import Foundation

public enum AurexRenderError: LocalizedError, Equatable {
    case invalidManifest(String)
    case unsupported(String)
    case unavailable(String)
    case renderFailed(String)
    case writerFailed(String)

    public var errorDescription: String? {
        switch self {
        case .invalidManifest(let message):
            "Invalid manifest: \(message)"
        case .unsupported(let message):
            "Unsupported: \(message)"
        case .unavailable(let message):
            "Unavailable: \(message)"
        case .renderFailed(let message):
            "Render failed: \(message)"
        case .writerFailed(let message):
            "Writer failed: \(message)"
        }
    }
}
