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
    // The first external dependency (docs/contracts/about-update.md section 2): Sparkle, pinned exactly. 2.9.0 added signed
    // feeds, 2.9.2 fixed two security issues and 2.9.5 and 2.9.6 three more (2.10.0: docs/contracts/app-sandbox.md section
    // 2.8, where a sandboxed input method drives Sparkle's installer outside the sandbox). The official prebuilt framework, whose binary is pinned by the checksum in
    // Sparkle's own Package.swift; Package.resolved is committed and every build resolves with --force-resolved-versions.
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.0"),
    ],
    targets: [
        // Header-only module over core/include/shanjie.h; see Sources/CShanjie/module.modulemap.
        .systemLibrary(name: "CShanjie", path: "Sources/CShanjie"),
        .target(
            name: "ShanjieKit",
            dependencies: ["CShanjie"],
            // Accelerate: core/src/tinylm.rs declares its symbols, and Rust's #[link(kind = "framework")] does not travel in the staticlib.
            linkerSettings: [.unsafeFlags([libcore]), .linkedFramework("Accelerate")]
        ),
        // TIS registration shared by `shanjie install` and the installer (docs/contracts/s3c-installer.md
        // section 2.3). ShanjieKit stays free of TIS.
        .target(
            name: "ShanjieInstall",
            linkerSettings: [.linkedFramework("Carbon")]
        ),
        .executableTarget(
            name: "Shanjie",
            // Sparkle only here: ShanjieKit and the tests stay free of it (the update decisions they test live in ShanjieKit).
            dependencies: ["ShanjieKit", "ShanjieInstall", "UpdateHost", .product(name: "Sparkle", package: "Sparkle")],
            // The IMK glue only: IMKInputController's overrides cannot be main-actor isolated in
            // the Swift 6 mode, so the main-thread requirement is checked at run time instead
            // (MainActor.assumeIsolated). ShanjieKit and the tests stay in the Swift 6 mode.
            swiftSettings: [.swiftLanguageMode(.v5)],
            // Sparkle.framework sits in Contents/Frameworks/Updater of the bundle (scripts/build-app.sh, app-sandbox.md
            // section 2.8). Contents/Frameworks itself is no longer searched, so a framework left there fails at launch;
            // SwiftPM adds @loader_path (Contents/MacOS) on its own, where the build puts nothing but the executable.
            linkerSettings: [.linkedFramework("Carbon"), .linkedFramework("InputMethodKit"),
                             .unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks/Updater"])]
        ),
        // 安裝善解輸入法.app; AppKit only, the window is built in code.
        .executableTarget(
            name: "ShanjieInstaller",
            dependencies: ["ShanjieInstall"],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        // The decisions of the standalone updater (docs/contracts/standalone-updater.md section 2.1); Foundation only.
        .target(name: "UpdateHost"),
        // 善解更新.app, embedded at Contents/Helpers of the input method (scripts/build-app.sh). Sparkle.framework sits in its own
        // Contents/Frameworks, which SwiftPM's @loader_path (Contents/MacOS) does not reach, hence the rpath.
        .executableTarget(
            name: "ShanjieUpdater",
            dependencies: ["UpdateHost", .product(name: "Sparkle", package: "Sparkle")],
            swiftSettings: [.swiftLanguageMode(.v5)],
            linkerSettings: [.unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"])]
        ),
        .testTarget(name: "UpdateHostTests", dependencies: ["UpdateHost"]),
        .testTarget(name: "ShanjieKitTests", dependencies: ["ShanjieKit"]),
        .testTarget(name: "ShanjieInstallTests", dependencies: ["ShanjieInstall"]),
    ]
)
