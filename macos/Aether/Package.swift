// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Aether",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(name: "Aether", targets: ["Aether"]),
    ],
    targets: [
        .executableTarget(
            name: "Aether",
            path: "Sources/Aether",
            resources: [
                .process("Resources"),
            ]
        ),
        .testTarget(
            name: "AetherTests",
            dependencies: ["Aether"],
            path: "Tests/AetherTests"
        ),
    ]
)
