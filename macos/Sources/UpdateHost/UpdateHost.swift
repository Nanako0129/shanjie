import Foundation

/// The decisions of 善解更新.app (docs/contracts/standalone-updater.md section 2.1): which input method bundle it may
/// update, and what its `ShanjieUpdateBuild` allows. Foundation only, so the helper links nothing it does not need and the
/// tests reach the decisions without Sparkle.
public enum UpdateHost {
    /// The folder names scripts/build-app.sh gives the shipping and the development ID.
    public static let appNames = ["善解輸入法.app", "善解（開發版）.app"]

    /// The two places an input method bundle may be installed: the system's and the user's Input Methods folder. The home
    /// (`pw_dir`) is resolved with realpath first, so a home given through a symlink, with other letter case or a trailing
    /// slash still matches the real path `host` compares with; a home that does not exist is used as given, minus trailing
    /// slashes.
    public static func roots(home: String) -> [String] {
        var h = home
        if let real = realpath(home, nil) {
            h = String(cString: real)
            free(real)
        }
        while h.count > 1, h.hasSuffix("/") { h.removeLast() }
        return ["/Library/Input Methods", h + "/Library/Input Methods"]
    }

    /// App Translocation: a quarantined bundle may run from a read-only mount under `/AppTranslocation/` (the same test as
    /// Sparkle's SUHost.isRunningTranslocated), where three levels up is the mount, never an allowed place. Such a helper URL is mapped back to the
    /// original path by `original` (the helper passes Security's SecTranslocateCreateOriginalPathForURL); `nil` from it
    /// means no host. The original then goes through the same realpath check as any other path.
    public static func untranslocated(_ helper: URL, original: (URL) -> URL?) -> URL? {
        helper.path.contains("/AppTranslocation/") ? original(helper) : helper
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

    /// The feed URL from a bundle's own Info.plist (`infoDictionary`, which is neither localized nor read through user
    /// defaults), `nil` when it is missing or empty. Sparkle reads `SUFeedURL` from the host's user defaults before its
    /// Info.plist when the host is not the main bundle (standalone-updater.md S2), so both the helper and the input method
    /// return this from the delegate's `feedURLString(for:)`, and neither starts without it.
    public static func feedURL(info: [String: Any]?) -> String? {
        guard let s = info?["SUFeedURL"] as? String, !s.isEmpty else { return nil }
        return s
    }

    /// The keys Sparkle stores "skip this version" under in the host's defaults domain (SUConstants.m in 2.10.0: the
    /// constant SUSkippedMinorVersionKey stores `SUSkippedVersion`). Both apps remove them before every check
    /// (standalone-updater.md section 2.1): the helper writes another domain than the sandboxed input method reads, and a
    /// skipped version written by anyone would hide every update.
    public static let skippedVersionKeys = ["SUSkippedVersion", "SUSkippedMajorVersion", "SUSkippedMajorSubreleaseVersion"]
    /// Sparkle's scheduler settings, which it also reads from the host's defaults before Info.plist (SPUUpdaterSettings.m in
    /// 2.10.0): a value there would start background checks the Info.plist turns off (SUEnableAutomaticChecks false), and in
    /// the helper a running background session made the user's check return without a window.
    public static let schedulerKeys = ["SUEnableAutomaticChecks", "SUScheduledCheckInterval", "SUAutomaticallyUpdate"]

    /// Removes the skipped versions and the scheduler settings before Sparkle reads them.
    public static func clearHostDefaults(in defaults: some KeyRemoving) {
        for k in skippedVersionKeys + schedulerKeys { defaults.removeObject(forKey: k) }
    }

    /// Which of Sparkle's checks may run (the delegate's `updater(_:mayPerform:)`): the helper runs its own silent check and
    /// then the user's interactive one; the input method only ever probes. Background (scheduled) checks never run in either.
    public enum Check: Sendable { case userInitiated, background, information }
    public static func helperMayPerform(_ c: Check) -> Bool { c != .background }
    public static func inputMethodMayPerform(_ c: Check) -> Bool { c == .information }

    /// Only a plain application update proceeds; a package (`package`, `interactive-package`) would run Apple's installer
    /// as root in the system domain.
    public static func allowsInstall(type: String) -> Bool { type == "application" }
}

/// What `clearHostDefaults` needs from a defaults store; the tests use an in-memory one, so they write no real preferences.
public protocol KeyRemoving {
    func removeObject(forKey defaultName: String)
}

extension UserDefaults: KeyRemoving {}
