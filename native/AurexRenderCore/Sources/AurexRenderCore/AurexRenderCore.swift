import Foundation
import Metal

public enum AurexRenderCore {
    public static let manifestSchemaVersion = 1
    public static let version = "0.1.0-mvp"

    public static var metalDeviceName: String? {
        MTLCreateSystemDefaultDevice()?.name
    }
}
