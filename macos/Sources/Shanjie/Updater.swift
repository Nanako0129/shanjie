import AppKit
import os
import ShanjieKit
import Sparkle
import UpdateHost

/// Sparkle's version check in the input method (docs/contracts/about-update.md sections 1 and 2), modeled on Syrtis's UpdaterService. The
/// decisions live in `Updates` (ShanjieKit, tested); this class only connects them to Sparkle.
///
/// - Starts only from an app bundle built by the release workflow or as an update test build (`Updates.runsUpdater`, the
///   Info.plist key `ShanjieUpdateBuild`): a local build never reads the production feed.
/// - Sparkle's own scheduler is off (`SUEnableAutomaticChecks` false in Info.plist). A timer here calls `Updates.tick`, which
///   runs `checkForUpdateInformation` (no window) at most once a day and only with 自動檢查更新 on: a window popping up while
///   the user types would take the focus.
/// - Only the information-only check runs here, and the framework has no XPC services (`SUEnableInstallerLauncherService`
///   false), no Autoupdate and no Updater.app. The feed is pinned to Info.plist and the skipped versions are cleared before
///   every check (standalone-updater.md section 2.1). The install flow is 善解更新.app's (`launchHelper`, docs/contracts/standalone-updater.md): it runs outside the
///   sandbox and opens only after the user pressed 更新到 X or the menu item and a silent check just confirmed a newer version
///   (`Updates.install`).
/// - Started with `startingUpdater: false` and `start()` here, unlike Syrtis: when the standard controller starts the updater
///   itself and that fails, it shows a modal NSAlert (`runModal`, SPUStandardUpdaterController.m in Sparkle 2.9.3 and 2.10.0), which
///   would block the input method. A failure here is logged and every check then reads 檢查失敗.
///
/// Logging follows R2: static strings only, never a version, URL or error. Sparkle's own log (SULog) is the documented
/// exception (docs/PLAN.md R10).
@MainActor
final class Updater: NSObject, SPUUpdaterDelegate {
    private static let log = Logger(subsystem: "com.nyanako.inputmethod.shanjie", category: "update")
    /// The first background tick comes a minute after launch, so the check never competes with the engine's start; then hourly.
    /// The hour only bounds how late a due check runs (the daily rule is `Updates.shouldCheck`); neither value was measured.
    private static let firstTick: TimeInterval = 60
    private static let tickEvery: TimeInterval = 60 * 60

    private let updates: Updates
    private var controller: SPUStandardUpdaterController?
    private var started = false
    /// The version `didFindValidUpdate` reported in the current cycle.
    private var found: String?
    private var timer: Timer?
    /// This bundle's Info.plist `SUFeedURL`, pinned through `feedURLString(for:)` like the helper's (standalone-updater.md
    /// section 2.1); without it the updater does not start.
    private nonisolated let feed = UpdateHost.feedURL(info: Bundle.main.infoDictionary)

    init(updates: Updates) {
        self.updates = updates
        super.init()
        let build = Bundle.main.object(forInfoDictionaryKey: Updates.buildKey) as? String
        guard Updates.runsUpdater(bundleURL: Bundle.main.bundleURL, build: build), feed != nil else { return }
        let c = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil)
        controller = c
        do {
            try c.updater.start()
            started = true
        } catch {
            Self.log.error("the updater did not start")
        }
        updates.probe = { [weak self] in self?.probe() ?? .unavailable }
        updates.launchHelper = { [weak self] in self?.launchHelper() }
        let t = Timer(timeInterval: Self.tickEvery, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.updates.tick() }
        }
        t.fireDate = Date(timeIntervalSinceNow: Self.firstTick)
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    private func probe() -> ProbeStart {
        guard started, let updater = controller?.updater else { return .unavailable }
        guard !updater.sessionInProgress else { return .busy }
        // Sparkle filters found versions by the skipped ones in this domain (standalone-updater.md section 2.1).
        UpdateHost.clearSkippedVersions(in: .standard)
        updater.checkForUpdateInformation()
        return .started
    }

    /// 更新到 X, after a silent check confirmed a newer version (`Updates` closes the settings window first): opens
    /// 善解更新.app, the sandbox-free updater inside this bundle (docs/contracts/standalone-updater.md section 2.2). No argument
    /// (a sandboxed caller's are ignored anyway); the helper works out its host itself and shows Sparkle's dialogs. A press
    /// while it already runs just activates it (LaunchServices).
    private func launchHelper() {
        let url = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/善解更新.app")
        let c = NSWorkspace.OpenConfiguration()
        c.activates = true
        let log = Self.log
        NSWorkspace.shared.openApplication(at: url, configuration: c) { _, error in
            if error != nil { log.error("the updater did not open") }
        }
    }

    // MARK: SPUUpdaterDelegate (Sparkle calls it on the main thread)

    /// The Info.plist value, never this domain's user defaults, which Sparkle would read first (standalone-updater.md S2).
    nonisolated func feedURLString(for updater: SPUUpdater) -> String? { feed }

    nonisolated func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        let version = item.displayVersionString
        MainActor.assumeIsolated { found = version }
    }

    /// One place for every outcome: no error after `didFindValidUpdate` is a newer version, `SUNoUpdateError` is up to date,
    /// any other error (network, feed signature, appcast) is a failed check. `found` is taken before `finished`, which may
    /// open 善解更新.
    nonisolated func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck, error: (any Error)?) {
        let e = error as NSError?
        let noUpdate = e.map { $0.domain == SUSparkleErrorDomain && $0.code == Int(SUError.noUpdateError.rawValue) } ?? false
        let failed = e != nil && !noUpdate
        MainActor.assumeIsolated {
            let version = found
            found = nil
            if failed {
                Self.log.error("update check failed")
                updates.finished(.failed)
            } else if noUpdate {
                updates.finished(.upToDate)
            } else if let version {
                updates.finished(.available, version: version)
            } else {
                updates.finished(nil)
            }
        }
    }
}
