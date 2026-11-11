import Foundation
import XCTest
@testable import ShanjieKit

/// docs/spec/install-and-update.md §10.1: the 關於 page's version string, the background check's decisions, the menu item
/// only while an update is known, the install press (a silent check first), and the updater starting only from a release or
/// update test bundle. No Sparkle and no network: `probe`, `closeSettings` and `launchHelper` are recording closures.
@MainActor
final class UpdatesTests: XCTestCase {
    private let day = Updates.interval
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    /// `opens` counts launches of 善解更新; `events` records the order of closing the settings window and launching it.
    final class Counter {
        var probes = 0, events: [String] = []
        var opens: Int { events.filter { $0 == "launch" }.count }
    }

    private func updates(_ store: MemoryUpdateStore = MemoryUpdateStore(), version: String = "0.4.7",
                         answer: ProbeStart = .started) -> (Updates, Counter) {
        let u = Updates(store: store, version: version)
        let c = Counter()
        u.probe = { c.probes += 1; return answer }
        u.closeSettings = { c.events.append("close") }
        u.launchHelper = { c.events.append("launch") }
        return (u, c)
    }

    /// The 關於 page's note follows the folder the bundle sits in: the system's one names the password prompt, any other says no
    /// password, and (with U2) points to the installer's 「這台 Mac」.
    func testLocationNote() {
        XCTAssertEqual(UpdateText.location(bundleURL: URL(fileURLWithPath: "/Library/Input Methods/善解輸入法.app")), UpdateText.locationSystem)
        XCTAssertTrue(UpdateText.locationSystem.contains("善解更新 wants permission to update 善解輸入法."))
        // The dialog itself is the system's (Chinese on a Chinese system); only that line is Sparkle's fixed English.
        XCTAssertTrue(UpdateText.locationSystem.contains("請你輸入密碼來允許更新；框裡有一行說明固定是英文"))
        for path in ["/Users/a/Library/Input Methods/善解輸入法.app", "/Users/a/Desktop/善解輸入法.app",
                     "/Library/Input Methods/sub/善解輸入法.app"] {
            XCTAssertEqual(UpdateText.location(bundleURL: URL(fileURLWithPath: path)), UpdateText.locationUser, path)
        }
        XCTAssertTrue(UpdateText.locationUser.contains("系統會暫時停用善解"))
        XCTAssertTrue(UpdateText.locationUser.hasSuffix("請重新下載安裝程式並選「這台 Mac」。"), "U2 ships the installer's 這台 Mac")
    }

    func testVersionText() {
        XCTAssertEqual(Updates.versionText("0.4.7"), "0.4.7")
        XCTAssertEqual(Updates.versionText("0.0.0"), "開發版")
        XCTAssertEqual(Updates.versionText(nil), "開發版")
        XCTAssertEqual(Updates(store: MemoryUpdateStore(), version: "0.0.0").versionText, "開發版")
    }

    func testShouldCheckDecision() {
        XCTAssertFalse(Updates.shouldCheck(enabled: false, lastCheck: nil, now: now), "toggle off must never check")
        XCTAssertFalse(Updates.shouldCheck(enabled: false, lastCheck: now.addingTimeInterval(-10 * day), now: now))
        XCTAssertTrue(Updates.shouldCheck(enabled: true, lastCheck: nil, now: now))
        XCTAssertFalse(Updates.shouldCheck(enabled: true, lastCheck: now.addingTimeInterval(-day + 60), now: now), "less than a day")
        XCTAssertTrue(Updates.shouldCheck(enabled: true, lastCheck: now.addingTimeInterval(-day - 60), now: now), "more than a day")
        XCTAssertTrue(Updates.shouldCheck(enabled: true, lastCheck: now.addingTimeInterval(day), now: now), "clock set back")
    }

    /// The timer path: `tick` probes only when the decision says so, and a started check counts for the day.
    func testTickFollowsToggleAndDay() {
        let store = MemoryUpdateStore()
        let (u, c) = updates(store)
        XCTAssertTrue(u.autoCheck, "on by default")
        u.setAutoCheck(false)
        XCTAssertEqual(store.autoCheck, false)
        u.tick(now: now)
        XCTAssertEqual(c.probes, 0, "toggle off: no background check")
        u.setAutoCheck(true)
        u.tick(now: now)
        XCTAssertEqual(c.probes, 1)
        XCTAssertEqual(store.lastCheck, now)
        u.finished(.upToDate)
        u.tick(now: now.addingTimeInterval(day - 60))
        XCTAssertEqual(c.probes, 1, "within a day: no second check")
        u.tick(now: now.addingTimeInterval(day + 60))
        XCTAssertEqual(c.probes, 2, "after a day: checks again")
    }

    /// 立即檢查 connects whatever the toggle says, and is ignored while a check runs.
    func testCheckNowIgnoresToggleAndARunningCheck() {
        let store = MemoryUpdateStore()
        store.autoCheck = false
        let (u, c) = updates(store)
        u.checkNow(now: now)
        XCTAssertEqual(c.probes, 1)
        XCTAssertTrue(u.checking)
        u.checkNow(now: now.addingTimeInterval(5))
        XCTAssertEqual(c.probes, 1, "a second press while checking starts nothing")
        XCTAssertNil(u.result, "and is not a failure")
        XCTAssertEqual(u.lastCheck, now)
        u.finished(.available, version: "0.4.8")
        XCTAssertFalse(u.checking)
        XCTAssertEqual(u.available, "0.4.8")
        XCTAssertEqual(UpdateText.result(u.result, available: u.available), "有新版本 0.4.8")
        XCTAssertEqual(c.opens, 0, "立即檢查 never opens Sparkle's window")
    }

    /// An updater that is not running: 立即檢查 reads 檢查失敗 with the date; a busy Sparkle session is not a failure.
    func testCheckNowWhenTheUpdaterCannotCheck() {
        let (down, _) = updates(answer: .unavailable)
        down.checkNow(now: now)
        XCTAssertEqual(down.result, .failed)
        XCTAssertEqual(down.lastCheck, now, "the failed press is dated")
        XCTAssertFalse(down.checking)

        let (busy, _) = updates(answer: .busy)
        busy.checkNow(now: now)
        XCTAssertNil(busy.result)
        XCTAssertNil(busy.lastCheck)
        XCTAssertFalse(busy.checking)
    }

    /// 更新到 X: a silent check first; the interactive flow only when it confirms a newer version.
    func testInstallChecksFirst() {
        let (u, c) = updates()
        u.finished(.available, version: "0.4.8")
        u.install(now: now)
        XCTAssertEqual(c.probes, 1, "install starts a silent check")
        XCTAssertEqual(c.opens, 0, "not the interactive flow yet")
        XCTAssertTrue(u.checking)
        u.install(now: now)
        XCTAssertEqual(c.probes, 1, "ignored while checking")
        XCTAssertEqual(c.opens, 0, "our own silent check has no Sparkle window to bring up")
        u.finished(.available, version: "0.4.9")
        XCTAssertEqual(c.events, ["close", "launch"], "confirmed: the settings window closes, then 善解更新 opens")
        XCTAssertEqual(u.available, "0.4.9")

        u.install(now: now)
        u.finished(.upToDate)
        XCTAssertEqual(c.opens, 1, "gone since: no Sparkle window")
        XCTAssertNil(u.available)
        XCTAssertEqual(UpdateText.result(u.result, available: u.available), "已是最新")

        let (f, fc) = updates()
        f.finished(.available, version: "0.4.8")
        f.install(now: now)
        f.finished(.failed)
        XCTAssertEqual(fc.opens, 0, "failed check: no Sparkle window")
        XCTAssertEqual(f.available, "0.4.8", "and the known version stays")

        let (down, dc) = updates(answer: .unavailable)
        down.install(now: now)
        XCTAssertEqual(down.result, .failed)
        XCTAssertEqual(down.lastCheck, now)
        XCTAssertEqual(dc.opens, 0)
    }

    /// A press while the input method's Sparkle session runs starts no check and opens 善解更新 again (it checks by itself;
    /// already running, LaunchServices only activates it).
    func testInstallWhileBusyFocuses() {
        let (u, c) = updates(answer: .busy)
        u.install(now: now)
        XCTAssertEqual(c.events, ["close", "launch"], "the same two steps as a confirmed check")
        XCTAssertFalse(u.checking)
        XCTAssertNil(u.lastCheck, "no check started")
        u.checkNow(now: now)
        XCTAssertEqual(c.opens, 1, "立即檢查 never brings Sparkle's windows up")
    }

    /// Only the check `install` started opens the interactive flow; a background check finding a version does not, and a
    /// cycle ending without a version keeps the one found.
    func testBackgroundFindDoesNotOpenAWindowOrDropAVersion() {
        let (u, c) = updates()
        u.tick(now: now)
        u.finished(.available, version: "0.4.8")
        XCTAssertEqual(c.opens, 0)
        u.checkNow(now: now)
        u.finished(nil)
        XCTAssertEqual(u.available, "0.4.8", "a cycle ending without a result keeps the found version")
        u.checkNow(now: now)
        u.finished(.available)
        XCTAssertEqual(u.available, "0.4.8", "a newer version reported without its number keeps the known one")
    }

    /// A failed check keeps a version already found; up to date clears it; a result recorded by another version is dropped.
    func testResultsAndRestore() {
        let store = MemoryUpdateStore()
        let (u, _) = updates(store)
        u.finished(.available, version: "0.4.8")
        u.finished(.failed)
        XCTAssertEqual(u.available, "0.4.8")
        XCTAssertEqual(UpdateText.result(u.result, available: u.available), "檢查失敗")
        XCTAssertEqual(Updates(store: store, version: "0.4.7").available, "0.4.8", "same version: restored")
        XCTAssertNil(Updates(store: store, version: "0.4.8").available, "after updating: dropped")
        XCTAssertNil(store.status)
        let (v, _) = updates(store)
        v.finished(.available, version: "0.4.9")
        v.finished(.upToDate)
        XCTAssertNil(v.available)
        XCTAssertEqual(UpdateText.result(v.result, available: v.available), "已是最新")
    }

    /// Only an app bundle marked as a release or an update test build starts the updater; a local build never does.
    func testUpdaterRunsOnlyFromAReleaseOrTestBundle() {
        let app = URL(fileURLWithPath: "/Users/x/Library/Input Methods/善解輸入法.app")
        XCTAssertTrue(Updates.runsUpdater(bundleURL: app, build: "release"))
        XCTAssertTrue(Updates.runsUpdater(bundleURL: app, build: "test"))
        XCTAssertFalse(Updates.runsUpdater(bundleURL: app, build: "local"), "a local build never reads the production feed")
        XCTAssertFalse(Updates.runsUpdater(bundleURL: app, build: nil), "no marker: no updater")
        XCTAssertFalse(Updates.runsUpdater(bundleURL: app, build: "Release"))
        XCTAssertFalse(Updates.runsUpdater(bundleURL: URL(fileURLWithPath: "/repo/macos/.build/debug"), build: "release"))
        let runner = Bundle.main
        XCTAssertFalse(Updates.runsUpdater(bundleURL: runner.bundleURL, build: runner.object(forInfoDictionaryKey: Updates.buildKey) as? String),
                       "the test runner is not the app")
    }

    /// The input menu has 有新版本 X，按此更新… only while an update is known, greyed while a check runs, and it starts the
    /// silent check of `install`.
    func testMenuItemOnlyWhenAnUpdateIsKnown() throws {
        let resources = try XCTUnwrap(TestData.resources())
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(),
                          rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(),
                          appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        let session = Controller(shell).session
        func updateItems() -> [MenuEntry] { session.menu.filter { $0.action == .installUpdate } }
        XCTAssertTrue(updateItems().isEmpty, "no update state")
        let (u, c) = updates()
        shell.updates = u
        XCTAssertTrue(updateItems().isEmpty, "nothing known yet")
        u.finished(.upToDate)
        XCTAssertTrue(updateItems().isEmpty, "up to date")
        u.finished(.available, version: "0.4.8")
        XCTAssertEqual(updateItems().map(\.title), ["有新版本 0.4.8，按此更新…"])
        XCTAssertEqual(updateItems().map(\.enabled), [true])
        session.perform(.installUpdate)
        XCTAssertEqual(c.probes, 1, "the menu item starts the silent check")
        XCTAssertEqual(updateItems().map(\.enabled), [false], "greyed while it runs")
        u.finished(.available, version: "0.4.8")
        XCTAssertEqual(c.opens, 1)
        u.finished(.upToDate)
        XCTAssertTrue(updateItems().isEmpty, "gone once up to date")
    }

    // MARK: notification (docs/spec/install-and-update.md §3.8; contract update-notify §6.1)

    /// a, b: a background check that finds X notifies once; the next day's background check notifies again; a tick the same
    /// day does not check, so does not notify.
    func testBackgroundFindNotifiesOncePerDay() {
        let store = MemoryUpdateStore()
        let (u, c) = updates(store)
        var posted: [String] = []
        u.notify = { posted.append($0) }
        u.tick(now: now)
        u.finished(.available, version: "0.8.0")
        XCTAssertEqual(posted, ["0.8.0"])
        u.tick(now: now.addingTimeInterval(day / 2))
        XCTAssertEqual(c.probes, 1)
        XCTAssertEqual(posted, ["0.8.0"])
        u.tick(now: now.addingTimeInterval(day))
        XCTAssertEqual(c.probes, 2)
        u.finished(.available, version: "0.8.0")
        XCTAssertEqual(posted, ["0.8.0", "0.8.0"])
    }

    /// c: 立即檢查 and 更新到 X are pressed by someone looking at the page: no notification, also when a background check ran
    /// before them.
    func testUserChecksDoNotNotify() {
        let (u, _) = updates()
        var posted: [String] = []
        u.notify = { posted.append($0) }
        u.tick(now: now)
        u.finished(.upToDate)
        u.checkNow(now: now.addingTimeInterval(60))
        u.finished(.available, version: "0.8.0")
        u.install(now: now.addingTimeInterval(120))
        u.finished(.available, version: "0.8.0")
        XCTAssertEqual(posted, [])
    }

    /// d: either switch off → no notification (the background check still runs with notifications off).
    func testSwitchesOffDoNotNotify() {
        let (u, c) = updates()
        var posted: [String] = []
        u.notify = { posted.append($0) }
        u.setNotify(false)
        u.tick(now: now)
        XCTAssertEqual(c.probes, 1)
        u.finished(.available, version: "0.8.0")
        XCTAssertEqual(posted, [])
        u.setNotify(true)
        u.setAutoCheck(false)
        XCTAssertFalse(Updates.shouldNotify(enabled: true, autoCheck: false, background: true, result: .available))
        u.tick(now: now.addingTimeInterval(day))
        XCTAssertEqual(c.probes, 1)
        XCTAssertEqual(posted, [])
    }

    /// e: up to date or a failed check → no notification, also when a failed check keeps an already known newer version.
    func testOtherResultsDoNotNotify() {
        let (u, _) = updates()
        var posted: [String] = []
        u.notify = { posted.append($0) }
        u.tick(now: now)
        u.finished(.upToDate)
        XCTAssertEqual(posted, [])
        u.tick(now: now.addingTimeInterval(day))
        u.finished(.available, version: "0.8.0")
        XCTAssertEqual(posted, ["0.8.0"])
        u.tick(now: now.addingTimeInterval(2 * day))
        u.finished(.failed)
        XCTAssertEqual(u.available, "0.8.0")  // a failed check keeps the known version (docs/spec/install-and-update.md §3.2)
        XCTAssertEqual(posted, ["0.8.0"])
    }

    /// f: an absent `updateNotify` reads on, and the switch is stored.
    func testNotifySwitchDefaultsOnAndIsStored() {
        let store = MemoryUpdateStore()
        XCTAssertTrue(Updates(store: store, version: "0.4.7").notifyEnabled)
        let u = Updates(store: store, version: "0.4.7")
        u.setNotify(false)
        XCTAssertEqual(store.notify, false)
        XCTAssertFalse(Updates(store: store, version: "0.4.7").notifyEnabled)
    }

    /// g: the 關於 page's line shows only when a notification would be posted but the system has them off.
    func testPermissionLine() {
        XCTAssertTrue(Updates.showsPermissionLine(enabled: true, autoCheck: true, permission: .denied))
        XCTAssertFalse(Updates.showsPermissionLine(enabled: true, autoCheck: true, permission: .allowed))
        XCTAssertFalse(Updates.showsPermissionLine(enabled: true, autoCheck: true, permission: .notDetermined))
        XCTAssertFalse(Updates.showsPermissionLine(enabled: false, autoCheck: true, permission: .denied))
        XCTAssertFalse(Updates.showsPermissionLine(enabled: true, autoCheck: false, permission: .denied))
        let (u, _) = updates()
        u.notifyPermission = .denied
        XCTAssertTrue(u.showsPermissionLine)
        u.setNotify(false)
        XCTAssertFalse(u.showsPermissionLine)
    }

    /// A click on the notification while the background check runs is kept: when that check finds a newer version, 善解更新 opens.
    func testInstallDuringARunningCheckIsKept() {
        let (u, c) = updates()
        var posted: [String] = []
        u.notify = { posted.append($0) }
        u.tick(now: now)
        XCTAssertTrue(u.checking)
        u.install(now: now.addingTimeInterval(5))
        XCTAssertEqual(c.probes, 1)  // no second check
        XCTAssertEqual(c.opens, 0)
        u.finished(.available, version: "0.8.0")
        XCTAssertEqual(c.events, ["close", "launch"])
        XCTAssertEqual(posted, [])  // 善解更新 opened: no second notification replacing the clicked one
    }

    /// Up to date clears the update notification left in Notification Center; other results leave it.
    func testUpToDateClearsTheNotification() {
        let (u, _) = updates()
        var cleared = 0
        u.clearNotification = { cleared += 1 }
        u.tick(now: now)
        u.finished(.available, version: "0.8.0")
        u.tick(now: now.addingTimeInterval(day))
        u.finished(.failed)
        XCTAssertEqual(cleared, 0)
        u.tick(now: now.addingTimeInterval(2 * day))
        u.finished(.upToDate)
        XCTAssertEqual(cleared, 1)
    }

    /// The settings window re-reads the permission whenever it shows or becomes key (`SettingsModel.refreshExternal`), so coming
    /// back from System Settings to the reused window updates the 關於 page's line.
    func testSettingsRefreshReadsThePermission() throws {
        let resources = try XCTUnwrap(TestData.resources())
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(),
                          rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(),
                          appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        let (u, _) = updates()
        var reads = 0
        u.refreshPermission = { reads += 1 }
        shell.updates = u
        let model = SettingsModel(shell: shell)
        let before = reads
        model.refreshExternal()
        XCTAssertEqual(reads, before + 1)
    }

    /// The fixed strings of the notification and the 關於 page.
    func testNotificationText() {
        XCTAssertEqual(UpdateText.notificationTitle("0.8.0"), "善解有新版本 0.8.0")
        XCTAssertEqual(UpdateText.notificationBody, "按這裡更新；也可以從輸入選單更新。")
        XCTAssertEqual(UpdateText.permissionOff, "系統設定裡關閉了善解的通知")
        XCTAssertEqual(UpdateText.openNotificationSettings, "打開通知設定")
    }
}
