// swift-tools-version:5.9
import PackageDescription

// Everything that is not a screen: the data shapes the desktop syncs, the session clock, the cloud client.
// No UIKit or SwiftUI in here, so `swift test` runs it on a Mac without a simulator.
let package = Package(
    name: "CarmenCore",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "CarmenCore", targets: ["CarmenCore"])],
    targets: [
        .target(name: "CarmenCore"),
        .testTarget(name: "CarmenCoreTests", dependencies: ["CarmenCore"]),
    ]
)
