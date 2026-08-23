// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "AurexRenderCore",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .library(name: "AurexRenderCore", targets: ["AurexRenderCore"]),
        .executable(name: "aurex-render", targets: ["aurex-render"]),
    ],
    targets: [
        .target(
            name: "AurexRenderCore",
            linkerSettings: [
                .linkedFramework("AVFoundation"),
                .linkedFramework("CoreMedia"),
                .linkedFramework("CoreVideo"),
                .linkedFramework("Metal"),
                .linkedFramework("MetalKit"),
                .linkedFramework("VideoToolbox"),
            ]
        ),
        .executableTarget(
            name: "aurex-render",
            dependencies: ["AurexRenderCore"]
        ),
        .testTarget(
            name: "AurexRenderCoreTests",
            dependencies: ["AurexRenderCore"]
        ),
    ],
    swiftLanguageModes: [.v6]
)
