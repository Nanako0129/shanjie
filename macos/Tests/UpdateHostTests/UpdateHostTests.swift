import XCTest
@testable import UpdateHost

final class UpdateHostTests: XCTestCase {
    private var tmp: URL!
    private var root: String!

    override func setUpWithError() throws {
        // realpath of the temp dir: /var is a symlink to /private/var on macOS.
        let base = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        tmp = URL(fileURLWithPath: String(cString: realpath(base.path, nil)!))
        root = tmp.path + "/Input Methods"
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: tmp) }

    /// Makes `<dir>/<name>/Contents/Helpers/善解更新.app` and returns the helper's URL.
    @discardableResult
    private func helper(in dir: String, _ name: String) throws -> URL {
        let h = URL(fileURLWithPath: dir).appendingPathComponent(name + "/Contents/Helpers/善解更新.app")
        try FileManager.default.createDirectory(at: h, withIntermediateDirectories: true)
        return h
    }

    func testAllowedHostsAreAccepted() throws {
        for name in UpdateHost.appNames {
            let h = try helper(in: root, name)
            XCTAssertEqual(UpdateHost.host(helper: h, roots: [root])?.path, root + "/" + name)
        }
    }

    func testRootsAreTheSystemAndTheUserFolder() {
        XCTAssertEqual(UpdateHost.roots(home: "/Users/a"), ["/Library/Input Methods", "/Users/a/Library/Input Methods"])
    }

    func testACopyElsewhereIsRejected() throws {
        XCTAssertNil(UpdateHost.host(helper: try helper(in: tmp.path + "/Desktop", "善解輸入法.app"), roots: [root]))
        XCTAssertNil(UpdateHost.host(helper: try helper(in: root + "/sub", "善解輸入法.app"), roots: [root]), "not directly in the folder")
    }

    func testAnotherAppNameInTheRightFolderIsRejected() throws {
        XCTAssertNil(UpdateHost.host(helper: try helper(in: root, "別的.app"), roots: [root]))
    }

    func testAHelperThatIsNotNestedThreeDeepIsRejected() throws {
        let h = URL(fileURLWithPath: root + "/善解輸入法.app")  // the host itself as "helper": three levels up is not the folder
        try FileManager.default.createDirectory(at: h, withIntermediateDirectories: true)
        XCTAssertNil(UpdateHost.host(helper: h, roots: [root]))
    }

    func testASymlinkIsResolvedByRealpath() throws {
        // A link in the allowed folder that leads out of it: the real path is elsewhere.
        let real = try helper(in: tmp.path + "/elsewhere", "善解輸入法.app").deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        try FileManager.default.createSymbolicLink(atPath: root + "/善解輸入法.app", withDestinationPath: real.path)
        let viaLink = URL(fileURLWithPath: root + "/善解輸入法.app/Contents/Helpers/善解更新.app")
        XCTAssertNil(UpdateHost.host(helper: viaLink, roots: [root]))
        // A link from elsewhere that leads into the allowed folder resolves to the allowed path.
        let h = try helper(in: root, "善解（開發版）.app")
        try FileManager.default.createSymbolicLink(atPath: tmp.path + "/link.app", withDestinationPath: root + "/善解（開發版）.app")
        let via = URL(fileURLWithPath: tmp.path + "/link.app/Contents/Helpers/善解更新.app")
        XCTAssertEqual(UpdateHost.host(helper: via, roots: [root])?.path, root + "/善解（開發版）.app")
        _ = h
    }

    /// Both places (the system's and the user's folder, here two temporary roots standing in for them) with both names.
    func testBothRootsWithBothNames() throws {
        let system = tmp.path + "/system/Library/Input Methods", user = tmp.path + "/home/Library/Input Methods"
        for r in [system, user] {
            for name in UpdateHost.appNames {
                let h = try helper(in: r, name)
                XCTAssertEqual(UpdateHost.host(helper: h, roots: [system, user])?.path, r + "/" + name)
            }
        }
    }

    /// Copies a sandboxed caller could make (its container) or a user could (the Desktop) are not installed copies.
    func testContainerAndDesktopCopiesAreRejected() throws {
        let home = tmp.path + "/home"
        let roots = UpdateHost.roots(home: home)
        for dir in [home + "/Library/Containers/com.nyanako.inputmethod.shanjie/Data/Library/Input Methods",
                    home + "/Library/Containers/com.nyanako.inputmethod.shanjie/Data", home + "/Desktop"] {
            XCTAssertNil(UpdateHost.host(helper: try helper(in: dir, "善解輸入法.app"), roots: roots), dir)
        }
        XCTAssertEqual(UpdateHost.host(helper: try helper(in: home + "/Library/Input Methods", "善解輸入法.app"), roots: roots)?.path,
                       home + "/Library/Input Methods/善解輸入法.app", "the control: the same home's real folder passes")
    }

    /// A home that is a prefix of another (`/Users/a` and `/Users/ab`) gives the other no way in.
    func testHomePrefixBoundary() throws {
        let a = tmp.path + "/a", ab = tmp.path + "/ab"
        let h = try helper(in: ab + "/Library/Input Methods", "善解輸入法.app")
        XCTAssertNil(UpdateHost.host(helper: h, roots: UpdateHost.roots(home: a)))
        XCTAssertNotNil(UpdateHost.host(helper: h, roots: UpdateHost.roots(home: ab)))
    }

    /// The feed comes from Info.plist only, and a missing or empty one stops the helper.
    func testFeedURLFromInfoPlist() {
        XCTAssertEqual(UpdateHost.feedURL(info: ["SUFeedURL": "https://example.com/appcast.xml"]), "https://example.com/appcast.xml")
        XCTAssertNil(UpdateHost.feedURL(info: [:]))
        XCTAssertNil(UpdateHost.feedURL(info: nil))
        XCTAssertNil(UpdateHost.feedURL(info: ["SUFeedURL": ""]))
        XCTAssertNil(UpdateHost.feedURL(info: ["SUFeedURL": 1]))
    }

    /// The three keys Sparkle stores a skipped version under are removed, nothing else in the domain is touched.
    func testSkippedVersionsAreCleared() throws {
        let suite = "UpdateHostTests." + UUID().uuidString
        let d = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { d.removePersistentDomain(forName: suite) }
        XCTAssertEqual(UpdateHost.skippedVersionKeys, ["SUSkippedVersion", "SUSkippedMajorVersion", "SUSkippedMajorSubreleaseVersion"],
                       "the stored names (SUConstants.m), not the constant names")
        for k in UpdateHost.skippedVersionKeys { d.set("999", forKey: k) }
        d.set(true, forKey: "updateAutoCheck")
        UpdateHost.clearSkippedVersions(in: d)
        for k in UpdateHost.skippedVersionKeys { XCTAssertNil(d.object(forKey: k), k) }
        XCTAssertEqual(d.object(forKey: "updateAutoCheck") as? Bool, true)
    }

    func testOnlyApplicationUpdatesProceed() {
        XCTAssertTrue(UpdateHost.allowsInstall(type: "application"))
        for t in ["package", "interactive-package", "Application", ""] { XCTAssertFalse(UpdateHost.allowsInstall(type: t), t) }
    }

    func testTheBuildDecidesTheTrustCheck() {
        XCTAssertEqual(UpdateHost.trust(build: "release"), .verifyDeveloperID)
        XCTAssertEqual(UpdateHost.trust(build: "test"), .skip)
        XCTAssertEqual(UpdateHost.trust(build: "local"), .exit)
        XCTAssertEqual(UpdateHost.trust(build: nil), .exit)
        XCTAssertEqual(UpdateHost.trust(build: "Release"), .exit)
    }
}
