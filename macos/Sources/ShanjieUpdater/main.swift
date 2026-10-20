import AppKit
import os
import Security
import Sparkle
import UpdateHost

/// 善解更新.app (docs/contracts/standalone-updater.md section 2.1): not sandboxed, at `<input method>.app/Contents/Helpers`.
/// It updates the one bundle that contains it, and only when that bundle sits in an Input Methods folder under its own name;
/// it takes no argument and no input from whoever launched it. Feed and key come from the host's Info.plist (the key Sparkle
/// reads from the host bundle; the feed this delegate pins, since Sparkle would read the host domain's defaults first); this
/// bundle's own Info.plist has none, so it can update nothing else. Logging follows R2:
/// static strings only.
final class Helper: NSObject, NSApplicationDelegate, SPUUpdaterDelegate {
    static let log = Logger(subsystem: "com.nyanako.inputmethod.shanjie", category: "updater")
    /// Only release builds ship, so the requirement names the shipping ID and Team 2LJ882GPY8.
    /// The two certificate fields (code review 2026-10-20) make it a Developer ID signature: the Developer ID intermediate
    /// (1.2.840.113635.100.6.2.6) and the Developer ID Application leaf (1.2.840.113635.100.6.1.13), as Apple's own
    /// Developer ID requirement names them, not only any Apple-anchored certificate with that Team.
    static let requirementText =
        #"anchor apple generic and identifier "com.nyanako.inputmethod.shanjie" and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists and certificate leaf[subject.OU] = "2LJ882GPY8""#

    private let host: Bundle
    /// The host's Info.plist `SUFeedURL`, pinned through `feedURLString(for:)` (section 2.1).
    private let feed: String
    private var updater: SPUUpdater?
    private var found = false

    init(host: Bundle, feed: String) {
        self.host = host
        self.feed = feed
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        let driver = SPUStandardUserDriver(hostBundle: host, delegate: nil)
        let u = SPUUpdater(hostBundle: host, applicationBundle: host, userDriver: driver, delegate: self)
        updater = u
        do { try u.start() } catch {
            Self.log.error("the updater did not start")
            NSApp.terminate(nil)
            return
        }
        // Silent first: with no newer version nothing is shown, so a caller that only launches this app learns nothing and
        // cannot make Sparkle's own alerts appear.
        u.checkForUpdateInformation()
    }

    /// An open-document event (`open -a 善解更新.app <file>`) shows nothing: without this AppKit would put up its own "cannot
    /// open" alert with the caller's file name (section 2.1, security review P3-4). The URL variant is the one AppKit calls
    /// when it is implemented; the file-name variants are then not called.
    func application(_ application: NSApplication, open urls: [URL]) {}

    /// The host's Info.plist value, never the host domain's user defaults, which Sparkle would read first (S2).
    func feedURLString(for updater: SPUUpdater) -> String? { feed }

    /// Only this helper's own silent check and the interactive one it starts (`UpdateHost.helperMayPerform`); a scheduled
    /// background check, should Sparkle's scheduler ever be on, is refused.
    func updater(_ updater: SPUUpdater, mayPerform updateCheck: SPUUpdateCheck) throws {
        guard UpdateHost.helperMayPerform(Self.check(updateCheck)) else {
            throw NSError(domain: "ShanjieUpdater", code: 2, userInfo: [NSLocalizedDescriptionKey: "background checks are off"])
        }
    }

    static func check(_ c: SPUUpdateCheck) -> UpdateHost.Check {
        switch c {
        case .updates: .userInitiated
        case .updateInformation: .information
        default: .background  // updatesInBackground, and anything a later Sparkle adds: refused
        }
    }

    /// Only plain application updates (`UpdateHost.allowsInstall`): a guided package would run Apple's installer as root.
    func updater(_ updater: SPUUpdater, shouldProceedWithUpdate item: SUAppcastItem, updateCheck: SPUUpdateCheck) throws {
        guard UpdateHost.allowsInstall(type: item.installationType) else {
            throw NSError(domain: "ShanjieUpdater", code: 1, userInfo: [NSLocalizedDescriptionKey: "unsupported update type"])
        }
    }

    func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) { found = true }

    func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck, error: (any Error)?) {
        // A refused background cycle says nothing about ours, which is still running or about to.
        if Self.check(updateCheck) == .background { return }
        if updateCheck == .updateInformation, found, error == nil {
            found = false
            // One main-queue turn later: the information session ends before the interactive one starts.
            DispatchQueue.main.async { updater.checkForUpdates() }
            return
        }
        found = false
        // Checked a turn later, when Sparkle's own session bookkeeping has ended; an install in progress keeps the app alive.
        DispatchQueue.main.async { if !updater.sessionInProgress { NSApp.terminate(nil) } }
    }
}

/// `why` is always a literal (R2: static strings only), so it is logged public.
private func exitQuietly(_ why: StaticString) -> Never {
    Helper.log.error("\(String(describing: why), privacy: .public)")
    exit(1)
}

/// Security's SecTranslocateCreateOriginalPathForURL, looked up at run time: it is exported by Security.framework but not
/// declared in the SDK headers. Missing symbol or a failed call gives `nil`, and the helper exits (no host).
private func translocationOriginal(_ url: URL) -> URL? {
    typealias Original = @convention(c) (CFURL, UnsafeMutablePointer<Unmanaged<CFError>?>?) -> Unmanaged<CFURL>?
    guard let lib = dlopen("/System/Library/Frameworks/Security.framework/Security", RTLD_NOW),
          let sym = dlsym(lib, "SecTranslocateCreateOriginalPathForURL")
    else { return nil }
    let original = unsafeBitCast(sym, to: Original.self)
    return original(url as CFURL, nil)?.takeRetainedValue() as URL?
}

/// `release` builds: the host must satisfy the Developer ID requirement.
private func hostIsDeveloperID(_ host: URL) -> Bool {
    var code: SecStaticCode?
    var req: SecRequirement?
    guard SecStaticCodeCreateWithPath(host as CFURL, [], &code) == errSecSuccess, let code,
          SecRequirementCreateWithString(Helper.requirementText as CFString, [], &req) == errSecSuccess, let req
    else { return false }
    return SecStaticCodeCheckValidity(code, SecCSFlags(rawValue: kSecCSCheckAllArchitectures), req) == errSecSuccess
}

let home = getpwuid(getuid()).map { String(cString: $0.pointee.pw_dir) } ?? ""
guard !home.isEmpty, let me = UpdateHost.untranslocated(Bundle.main.bundleURL, original: translocationOriginal),
      let hostURL = UpdateHost.host(helper: me, roots: UpdateHost.roots(home: home)),
      let host = Bundle(url: hostURL)
else { exitQuietly("not inside an installed input method") }
let build = host.infoDictionary?["ShanjieUpdateBuild"] as? String
switch UpdateHost.trust(build: build) {
case .exit: exitQuietly("this build does not update")
case .skip: break
case .verifyDeveloperID: if !hostIsDeveloperID(hostURL) { exitQuietly("the input method is not signed by the developer") }
}
guard let feed = UpdateHost.feedURL(info: host.infoDictionary) else { exitQuietly("the input method has no feed URL") }
// Sparkle reads the skipped versions and its scheduler settings from the host's domain (SUHost: a suite named by the host's
// bundle ID when the host is not the main bundle) before Info.plist; cleared before Sparkle starts, so neither a press of
// 跳過這個版本 nor a value written by anyone else hides the next update or starts a background session (section 2.1).
guard let hostID = host.bundleIdentifier, let hostDefaults = UserDefaults(suiteName: hostID) else {
    exitQuietly("the input method has no bundle identifier")
}
UpdateHost.clearHostDefaults(in: hostDefaults)

let app = NSApplication.shared
let helper = Helper(host: host, feed: feed)
app.delegate = helper
app.setActivationPolicy(.regular)
app.run()
