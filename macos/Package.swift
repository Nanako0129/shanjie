// swift-tools-version: 6.0
import Foundation
import PackageDescription

// The Rust core is linked as the static library `cargo build --release -p core` produces
// (docs/contracts/s3b.md section 2). Linking the archive by its full path, rather than `-lcore`
// with a search path, means no other libcore.dylib or .a anywhere on the search path can be
// picked up instead. scripts/build-app.sh builds it first; `swift test` needs it built too.
let repoRoot = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().path
let libcore = repoRoot + "/target/release/libcore.a"

let package = Package(
    name: "Shanjie",
    platforms: [.macOS("26.0")],
    targets: [
        // Header-only module over core/include/shanjie.h; see Sources/CShanjie/module.modulemap.
        .systemLibrary(name: "CShanjie", path: "Sources/CShanjie"),
        .target(
            name: "ShanjieKit",
            dependencies: ["CShanjie"],
            linkerSettings: [.unsafeFlags([libcore])]
        ),
        // TIS registration shared by `shanjie install` and the installer (docs/contracts/s3c-installer.md
        // section 2.3). ShanjieKit stays free of TIS.
        .target(
            name: "ShanjieInstall",
            linkerSettings: [.linkedFramework("Carbon")]
        ),
        .executableTarget(
            name: "Shanjie",
            dependencies: ["ShanjieKit", "ShanjieInstall"],
            // The IMK glue only: IMKInputController's overrides cannot be main-actor isolated in
            // the Swift 6 mode, so the main-thread requirement is checked at run time instead
            // (MainActor.assumeIsolated). ShanjieKit and the tests stay in the Swift 6 mode.
            swiftSettings: [.swiftLanguageMode(.v5)],
            linkerSettings: [.linkedFramework("Carbon"), .linkedFramework("InputMethodKit")]
        ),
        // 安裝善解輸入法.app; AppKit only, the window is built in code.
        .executableTarget(
            name: "ShanjieInstaller",
            dependencies: ["ShanjieInstall"],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        .testTarget(name: "ShanjieKitTests", dependencies: ["ShanjieKit"]),
        .testTarget(name: "ShanjieInstallTests", dependencies: ["ShanjieInstall"]),
    ]
)
