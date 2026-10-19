import AppKit
import os
import Security
import Sparkle
import UpdateHost

/// 善解更新.app (docs/contracts/standalone-updater.md section 3.2): not sandboxed, at `<input method>.app/Contents/Helpers`.
/// It updates the one bundle that contains it, and only when that bundle sits in an Input Methods folder under its own name;
/// it takes no argument and no input from whoever launched it. Feed and key come from the host's Info.plist (Sparkle reads
/// them from the host bundle); this bundle's own Info.plist has none, so it can update nothing else. Logging follows R2:
/// static strings only.
final class Helper: NSObject, NSApplicationDelegate, SPUUpdaterDelegate {
    private static let log = Logger(subsystem: "com.nyanako.inputmethod.shanjie", category: "updater")
    /// Only release builds ship, so the requirement names the shipping ID and Team 2LJ882GPY8.
    static let requirementText =
        #"anchor apple generic and identifier "com.nyanako.inputmethod.shanjie" and certificate leaf[subject.OU] = "2LJ882GPY8""#

    private let host: Bundle
    private var updater: SPUUpdater?
    private var found = false

    init(host: Bundle) { self.host = host }

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

    /// Only plain application updates: a guided package would run Sparkle's installer as root.
    func updater(_ updater: SPUUpdater, shouldProceedWithUpdate item: SUAppcastItem, updateCheck: SPUUpdateCheck) throws {
        guard item.installationType == "application" else {
            throw NSError(domain: "ShanjieUpdater", code: 1, userInfo: [NSLocalizedDescriptionKey: "unsupported update type"])
        }
    }

    func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) { found = true }

    func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck, error: (any Error)?) {
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

private func exitQuietly(_ why: StaticString) -> Never {
    Logger(subsystem: "com.nyanako.inputmethod.shanjie", category: "updater").error("\(why)")
    exit(1)
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
guard !home.isEmpty, let hostURL = UpdateHost.host(helper: Bundle.main.bundleURL, roots: UpdateHost.roots(home: home)),
      let host = Bundle(url: hostURL)
else { exitQuietly("not inside an installed input method") }
let build = host.object(forInfoDictionaryKey: "ShanjieUpdateBuild") as? String
switch UpdateHost.trust(build: build) {
case .exit: exitQuietly("this build does not update")
case .skip: break
case .verifyDeveloperID: if !hostIsDeveloperID(hostURL) { exitQuietly("the input method is not signed by the developer") }
}

let app = NSApplication.shared
let helper = Helper(host: host)
app.delegate = helper
app.setActivationPolicy(.regular)
app.run()
