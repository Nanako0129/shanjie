import Foundation
import XCTest
@testable import ShanjieKit

/// docs/contracts/about-update.md section 3.1: the 關於 page's version string, the background check's decisions, the menu item
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
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(),
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
}
