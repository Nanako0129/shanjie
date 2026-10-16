import Foundation
import XCTest
@testable import ShanjieKit

/// docs/contracts/about-update.md section 3.1: the 關於 page's version string, the background check's decisions, the menu item
/// only while an update is known, and the updater not starting outside an app bundle. No Sparkle and no network: `probe` and
/// `install` are counting closures.
@MainActor
final class UpdatesTests: XCTestCase {
    private let day = Updates.interval
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    private func updates(_ store: MemoryUpdateStore = MemoryUpdateStore(), version: String = "0.4.7") -> (Updates, Counter) {
        let u = Updates(store: store, version: version)
        let c = Counter()
        u.probe = { c.probes += 1; return true }
        u.install = { c.installs += 1 }
        return (u, c)
    }

    final class Counter { var probes = 0, installs = 0 }

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

    /// 立即檢查 connects whatever the toggle says; a probe that cannot start reads 檢查失敗 and does not count as a check.
    func testCheckNowIgnoresToggle() {
        let store = MemoryUpdateStore()
        store.autoCheck = false
        let (u, c) = updates(store)
        XCTAssertFalse(u.autoCheck)
        u.checkNow(now: now)
        XCTAssertEqual(c.probes, 1)
        XCTAssertTrue(u.checking)
        u.finished(.available, version: "0.4.8")
        XCTAssertFalse(u.checking)
        XCTAssertEqual(u.available, "0.4.8")
        XCTAssertEqual(UpdateText.result(u.result, available: u.available), "有新版本 0.4.8")

        let (down, _) = updates()
        down.probe = { false }
        down.checkNow(now: now)
        XCTAssertEqual(down.result, .failed)
        XCTAssertNil(down.lastCheck)
        XCTAssertFalse(down.checking)
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

    func testUpdaterRunsOnlyFromAnAppBundle() {
        XCTAssertTrue(Updates.runsUpdater(bundleURL: URL(fileURLWithPath: "/Users/x/Library/Input Methods/善解輸入法.app")))
        XCTAssertFalse(Updates.runsUpdater(bundleURL: URL(fileURLWithPath: "/repo/macos/.build/debug")))
        XCTAssertFalse(Updates.runsUpdater(bundleURL: Bundle.main.bundleURL), "the test runner is not the app")
    }

    /// The input menu has 有新版本 X，按此更新… only while an update is known, and it runs the interactive flow.
    func testMenuItemOnlyWhenAnUpdateIsKnown() throws {
        let resources = try XCTUnwrap(TestData.resources())
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(),
                          rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(),
                          appProfileStore: MemoryAppProfileStore())
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
        session.perform(.installUpdate)
        XCTAssertEqual(c.installs, 1)
        XCTAssertEqual(c.probes, 0, "the menu item runs the interactive flow, not a probe")
        u.finished(.upToDate)
        XCTAssertTrue(updateItems().isEmpty, "gone once up to date")
    }
}
