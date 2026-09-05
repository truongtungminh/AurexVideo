import Foundation
import Metal

public enum AurexRenderCore {
    public static let manifestSchemaVersion = 2
    public static let version = "0.2.0-v2"

    public static var metalDeviceName: String? {
        MTLCreateSystemDefaultDevice()?.name
    }
}
