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

    func testTheBuildDecidesTheTrustCheck() {
        XCTAssertEqual(UpdateHost.trust(build: "release"), .verifyDeveloperID)
        XCTAssertEqual(UpdateHost.trust(build: "test"), .skip)
        XCTAssertEqual(UpdateHost.trust(build: "local"), .exit)
        XCTAssertEqual(UpdateHost.trust(build: nil), .exit)
        XCTAssertEqual(UpdateHost.trust(build: "Release"), .exit)
    }
}
