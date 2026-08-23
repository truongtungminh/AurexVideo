import CoreMedia
import Foundation
import VideoToolbox

public struct EncoderDescriptor: Codable, Equatable, Sendable {
    public let identifier: String
    public let name: String
    public let isHardwareAccelerated: Bool
    public let performanceRating: Double?
    public let qualityRating: Double?

    public init(
        identifier: String,
        name: String,
        isHardwareAccelerated: Bool,
        performanceRating: Double? = nil,
        qualityRating: Double? = nil
    ) {
        self.identifier = identifier
        self.name = name
        self.isHardwareAccelerated = isHardwareAccelerated
        self.performanceRating = performanceRating
        self.qualityRating = qualityRating
    }
}

public enum EncoderPath: String, Codable, Sendable {
    case hardware
    case software
    case automatic
}

struct EncoderCandidate {
    let path: EncoderPath
    let descriptor: EncoderDescriptor?
    let fallbackUsed: Bool

    var specification: [String: Any]? {
        switch path {
        case .hardware:
            var value: [String: Any] = [
                kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder as String: true,
            ]
            if let descriptor {
                value[kVTVideoEncoderSpecification_EncoderID as String] = descriptor.identifier
            }
            return value
        case .software:
            var value: [String: Any] = [
                kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder as String: false,
            ]
            if let descriptor {
                value[kVTVideoEncoderSpecification_EncoderID as String] = descriptor.identifier
            }
            return value
        case .automatic:
            return nil
        }
    }
}

public enum H264EncoderDiscovery {
    public static func availableEncoders() throws -> [EncoderDescriptor] {
        var rawList: CFArray?
        let status = VTCopyVideoEncoderList(nil, &rawList)
        guard status == noErr, let rawList else {
            throw AurexRenderError.unavailable("VideoToolbox encoder discovery failed with OSStatus \(status)")
        }

        let entries = rawList as NSArray
        var encoders: [EncoderDescriptor] = []
        for case let entry as NSDictionary in entries {
            guard let codec = entry.object(forKey: kVTVideoEncoderList_CodecType) as? NSNumber,
                  codec.uint32Value == kCMVideoCodecType_H264,
                  let identifier = entry.object(forKey: kVTVideoEncoderList_EncoderID) as? String else {
                continue
            }
            let displayName = (
                entry.object(forKey: kVTVideoEncoderList_DisplayName) as? String
                ?? entry.object(forKey: kVTVideoEncoderList_EncoderName) as? String
                ?? identifier
            )
            let isHardware = (
                entry.object(forKey: kVTVideoEncoderList_IsHardwareAccelerated) as? NSNumber
            )?.boolValue ?? false
            encoders.append(
                EncoderDescriptor(
                    identifier: identifier,
                    name: displayName,
                    isHardwareAccelerated: isHardware,
                    performanceRating: (
                        entry.object(forKey: kVTVideoEncoderList_PerformanceRating) as? NSNumber
                    )?.doubleValue,
                    qualityRating: (
                        entry.object(forKey: kVTVideoEncoderList_QualityRating) as? NSNumber
                    )?.doubleValue
                )
            )
        }
        return encoders.sorted {
            if $0.isHardwareAccelerated != $1.isHardwareAccelerated {
                return $0.isHardwareAccelerated && !$1.isHardwareAccelerated
            }
            let leftScore = ($0.performanceRating ?? 0) + ($0.qualityRating ?? 0)
            let rightScore = ($1.performanceRating ?? 0) + ($1.qualityRating ?? 0)
            if leftScore != rightScore { return leftScore > rightScore }
            return $0.identifier < $1.identifier
        }
    }

    static func candidates(
        for policy: HardwareAccelerationPolicy,
        available: [EncoderDescriptor]
    ) throws -> [EncoderCandidate] {
        let hardware = available.filter(\.isHardwareAccelerated)
        let software = available.filter { !$0.isHardwareAccelerated }

        switch policy {
        case .automatic:
            return [EncoderCandidate(path: .automatic, descriptor: nil, fallbackUsed: false)]
        case .require:
            guard !hardware.isEmpty else {
                throw AurexRenderError.unavailable("no hardware H.264 encoder is available")
            }
            return hardware.map { EncoderCandidate(path: .hardware, descriptor: $0, fallbackUsed: false) }
        case .software:
            if software.isEmpty {
                return [EncoderCandidate(path: .software, descriptor: nil, fallbackUsed: false)]
            }
            return software.map { EncoderCandidate(path: .software, descriptor: $0, fallbackUsed: false) }
        case .prefer:
            var result = hardware.map {
                EncoderCandidate(path: .hardware, descriptor: $0, fallbackUsed: false)
            }
            if software.isEmpty {
                result.append(EncoderCandidate(path: .software, descriptor: nil, fallbackUsed: true))
            } else {
                result.append(contentsOf: software.map {
                    EncoderCandidate(path: .software, descriptor: $0, fallbackUsed: true)
                })
            }
            return result
        }
    }
}
