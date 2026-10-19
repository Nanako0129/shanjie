import AppKit
import os
import ShanjieKit
import Sparkle

/// Sparkle in the input method (docs/contracts/about-update.md sections 1 and 2), modeled on Syrtis's UpdaterService. The
/// decisions live in `Updates` (ShanjieKit, tested); this class only connects them to Sparkle.
///
/// - Starts only from an app bundle built by the release workflow or as an update test build (`Updates.runsUpdater`, the
///   Info.plist key `ShanjieUpdateBuild`): a local build never reads the production feed.
/// - Sparkle's own scheduler is off (`SUEnableAutomaticChecks` false in Info.plist). A timer here calls `Updates.tick`, which
///   runs `checkForUpdateInformation` (no window) at most once a day and only with 自動檢查更新 on: a window popping up while
///   the user types would take the focus.
/// - The interactive flow (`checkForUpdates`) runs only after the user pressed 更新到 X or the menu item and a silent check
///   just confirmed a newer version (`Updates.install`), so Sparkle's own "you're up to date" or error alert never opens for
///   a version that is gone. Once the user is in Sparkle's flow, Sparkle's UI can still show modal alerts (a failed download
///   or install); whether the input method keeps typing around them is a device check (contract section 3.4).
/// - Sparkle's windows are lifted to the settings window's level while its session runs (`raise`).
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
    /// How often `watch` looks for a newly opened Sparkle window, so at most this long behind the settings window. Not measured.
    private static let raiseEvery: TimeInterval = 0.25

    private let updates: Updates
    private var controller: SPUStandardUpdaterController?
    private var started = false
    /// The version `didFindValidUpdate` reported in the current cycle.
    private var found: String?
    private var timer: Timer?
    /// Runs while a Sparkle session with windows may be open (`watch`).
    private var raiser: Timer?

    init(updates: Updates) {
        self.updates = updates
        super.init()
        let build = Bundle.main.object(forInfoDictionaryKey: Updates.buildKey) as? String
        guard Updates.runsUpdater(bundleURL: Bundle.main.bundleURL, build: build) else { return }
        let c = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: self, userDriverDelegate: nil)
        controller = c
        do {
            try c.updater.start()
            started = true
        } catch {
            Self.log.error("the updater did not start")
        }
        updates.probe = { [weak self] in self?.probe() ?? .unavailable }
        updates.interactive = { [weak self] in self?.interactive() }
        updates.focus = { [weak self] in self?.focus() }
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
        updater.checkForUpdateInformation()
        return .started
    }

    /// Called from inside the probe's finishing delegate call: deferred one main-queue turn, so the probe session has ended
    /// before the interactive one starts.
    private func interactive() {
        DispatchQueue.main.async { [weak self] in
            MainActor.assumeIsolated {
                self?.controller?.checkForUpdates(nil)
                self?.watch()
            }
        }
    }

    /// 更新到 X or the menu item pressed while Sparkle's session runs: Sparkle's own path for a running update
    /// (`checkForUpdates` → `showUpdateInFocus`, which shows the current alert or progress window; while no update is shown it
    /// logs and does nothing, never starting a second check), then `raise`, because the activation it asks for may be refused.
    private func focus() {
        controller?.checkForUpdates(nil)
        raise(all: true)
    }

    /// The input method is an agent app (LSUIElement) whose `NSApp.activate()` macOS may refuse, so Sparkle's normal-level
    /// windows opened behind the floating settings window, and once the user went to another app nothing brought them back:
    /// no Dock icon, and a second press of 更新到 X found the session busy (device check 2026-10-16). Sparkle opens its windows
    /// when it gets there (the alert after its own feed check, the progress window after 安裝更新); the standard user driver's
    /// delegate has no call for each window, so a timer looks for them while the session runs.
    // ponytail: polls every raiseEvery for the whole session; a custom SPUUserDriver wrapping the standard one could raise
    // each window as it opens, at the cost of replacing SPUStandardUpdaterController.
    private func watch() {
        guard raiser == nil else { return }
        let t = Timer(timeInterval: Self.raiseEvery, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.raise(all: false)
                if self.controller?.updater.sessionInProgress != true {
                    self.raiser?.invalidate()
                    self.raiser = nil
                }
            }
        }
        RunLoop.main.add(t, forMode: .common)  // .common: also during Sparkle's modal alerts
        raiser = t
    }

    /// Sparkle's windows go to the settings window's level (`SettingsWindowController.level`) and to the front, even when the
    /// input method is not active. `all` (a press of 更新到 X): also the windows already lifted, which the settings window may
    /// cover again after a click on it, and minimized ones (Sparkle's alert and progress windows can be minimized).
    private func raise(all: Bool) {
        let sparkle = Bundle(for: SPUUpdater.self)
        for w in NSApp.windows where w.isVisible || (all && w.isMiniaturized) {
            guard let c = w.windowController, Bundle(for: type(of: c)) == sparkle else { continue }
            guard all || w.level != SettingsWindowController.level else { continue }
            if w.isMiniaturized { w.deminiaturize(nil) }
            w.level = SettingsWindowController.level
            w.orderFrontRegardless()
        }
    }

    // MARK: SPUUpdaterDelegate (Sparkle calls it on the main thread)

    nonisolated func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        let version = item.displayVersionString
        MainActor.assumeIsolated { found = version }
    }

    /// One place for every outcome: no error after `didFindValidUpdate` is a newer version, `SUNoUpdateError` is up to date,
    /// any other error (network, feed signature, appcast) is a failed check. `found` is taken before `finished`, which may
    /// start the interactive cycle; that cycle sets its own.
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
