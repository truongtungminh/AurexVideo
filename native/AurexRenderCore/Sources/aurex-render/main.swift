import AurexRenderCore
import Foundation

let payload: [String: Any] = [
    "name": "AurexRenderCore",
    "version": AurexRenderCore.version,
    "manifestSchemaVersion": AurexRenderCore.manifestSchemaVersion,
    "metalDevice": AurexRenderCore.metalDeviceName as Any,
]

let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))
