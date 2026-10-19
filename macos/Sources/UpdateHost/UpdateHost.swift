import Foundation

/// The decisions of 善解更新.app (docs/contracts/standalone-updater.md section 3.2): which input method bundle it may
/// update, and what its `ShanjieUpdateBuild` allows. Foundation only, so the helper links nothing it does not need and the
/// tests reach the decisions without Sparkle.
public enum UpdateHost {
    /// The folder names scripts/build-app.sh gives the shipping and the development ID.
    public static let appNames = ["善解輸入法.app", "善解（開發版）.app"]

    /// The two places an input method bundle may be installed: the system's and the user's Input Methods folder.
    public static func roots(home: String) -> [String] {
        ["/Library/Input Methods", home + "/Library/Input Methods"]
    }

    /// The host: the bundle three levels above the helper (`<host>/Contents/Helpers/善解更新.app`), only when its real path
    /// is exactly `<root>/<one of appNames>`. Any other place (a copy in a container or on the Desktop, a symlink that leads
    /// elsewhere) gives `nil`, and the helper exits without a window. The caller supplies nothing: not an argument, not a path.
    public static func host(helper: URL, roots: [String]) -> URL? {
        let up = helper.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        guard let real = realpath(up.path, nil) else { return nil }
        defer { free(real) }
        let path = String(cString: real)
        guard roots.contains(where: { r in appNames.contains { path == r + "/" + $0 } }) else { return nil }
        return URL(fileURLWithPath: path)
    }

    public enum Trust: Equatable {
        /// `release`: the host's signature must satisfy the Developer ID requirement.
        case verifyDeveloperID
        /// `test`: an ad-hoc update test build has no Team to check.
        case skip
        /// `local` or anything else: never reads a feed, so the helper exits.
        case exit
    }

    public static func trust(build: String?) -> Trust {
        switch build {
        case "release": .verifyDeveloperID
        case "test": .skip
        default: .exit
        }
    }
}
