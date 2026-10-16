import AppKit
import os
import ShanjieKit
import Sparkle

/// Sparkle in the input method (docs/contracts/about-update.md sections 1 and 2), modeled on Syrtis's UpdaterService. The
/// decisions live in `Updates` (ShanjieKit, tested); this class only connects them to Sparkle.
///
/// - Starts only when running from an app bundle (`Updates.runsUpdater`).
/// - Sparkle's own scheduler is off (`SUEnableAutomaticChecks` false in Info.plist). A timer here calls `Updates.tick`, which
///   runs `checkForUpdateInformation` (no window) at most once a day and only with 自動檢查更新 on: a window popping up while
///   the user types would take the focus.
/// - The interactive flow (`checkForUpdates`) runs only on the user's press: 更新到 X on the 關於 page, or the menu item.
/// - Started with `startingUpdater: false` and `start()` here, unlike Syrtis: when the standard controller starts the updater
///   itself and that fails, it shows a modal NSAlert (`runModal`, SPUStandardUpdaterController.m in Sparkle 2.9.3), which
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

    init(updates: Updates) {
        self.updates = updates
        super.init()
        guard Updates.runsUpdater(bundleURL: Bundle.main.bundleURL) else { return }
        let c = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil)
        controller = c
        do {
            try c.updater.start()
            started = true
        } catch {
            Self.log.error("the updater did not start")
        }
        updates.probe = { [weak self] in self?.probe() ?? false }
        updates.install = { [weak self] in self?.install() }
        let t = Timer(timeInterval: Self.tickEvery, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.updates.tick() }
        }
        t.fireDate = Date(timeIntervalSinceNow: Self.firstTick)
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    private func probe() -> Bool {
        guard started, let updater = controller?.updater, !updater.sessionInProgress else { return false }
        found = nil
        updater.checkForUpdateInformation()
        return true
    }

    private func install() {
        guard started else {
            updates.finished(.failed)
            return
        }
        found = nil
        controller?.checkForUpdates(nil)
    }

    // MARK: SPUUpdaterDelegate (Sparkle calls it on the main thread)

    nonisolated func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        let version = item.displayVersionString
        MainActor.assumeIsolated { found = version }
    }

    /// One place for every outcome: no error after `didFindValidUpdate` is a newer version, `SUNoUpdateError` is up to date,
    /// any other error (network, feed signature, appcast) is a failed check.
    nonisolated func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck, error: (any Error)?) {
        let e = error as NSError?
        let noUpdate = e.map { $0.domain == SUSparkleErrorDomain && $0.code == Int(SUError.noUpdateError.rawValue) } ?? false
        let failed = e != nil && !noUpdate
        MainActor.assumeIsolated {
            if failed {
                Self.log.error("update check failed")
                updates.finished(.failed)
            } else if noUpdate {
                updates.finished(.upToDate)
            } else if let found {
                updates.finished(.available, version: found)
            } else {
                updates.finished(nil)
            }
            found = nil
        }
    }
}
