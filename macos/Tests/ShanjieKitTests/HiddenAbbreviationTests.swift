import XCTest
@testable import ShanjieKit

/// 可省略韻母 does not ship in v0.4.0 (user, 2026-10-10): with `showsAbbreviation: false`, as the app passes, the switch is in
/// neither the menu nor the settings model, the composer stays off even when the store says on, and nothing writes the store.
@MainActor
final class HiddenAbbreviationTests: XCTestCase {
    func testHiddenAbbreviationIsOffAndOffersNoSwitch() throws {
        let resources = try XCTUnwrap(TestData.resources())
        let store = MemoryAbbreviationStore(true)
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(),
                          predictionStore: MemoryPredictionStore(), abbreviationStore: store, englishStore: MemoryEnglishStore(false), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(),
                          showsAbbreviation: false, spacingStore: MemorySpacingStore())
        let c = Controller(shell)
        c.session.activate()
        XCTAssertFalse(shell.abbreviationOn, "the stored on is not read")
        XCTAssertFalse(c.session.menu.contains { $0.action == .toggleAbbreviation })
        XCTAssertFalse(c.session.menu.map(\.title).contains("可省略韻母"))
        let titles = c.session.menu.map(\.title)  // as in the app: 中英混打 (zhen-mixed) and then 語意校正 follow 即時預測 directly
        XCTAssertEqual(titles.firstIndex(of: "中英混打（實驗）"), titles.firstIndex(of: "即時預測").map { $0 + 1 })
        XCTAssertEqual(titles.firstIndex(of: "語意校正"), titles.firstIndex(of: "即時預測").map { $0 + 2 })
        XCTAssertFalse(SettingsModel(shell: shell).showsAbbreviation)
        c.type("st")  // ㄋ, ㄔ (English mixing is off here: with it on, a key over a symbol is English, zhen-revert)
        XCTAssertEqual(c.client.marked, "ㄔ", "off: the second key replaces the first instead of opening a unit")
        c.press(Keys.esc)
        c.session.perform(.toggleAbbreviation)
        XCTAssertFalse(shell.abbreviationOn)
        XCTAssertEqual(store.abbreviation, true, "the store is not written")
        shell.applyAbbreviation(false)  // the settings window's path; a write would store false
        XCTAssertEqual(store.abbreviation, true, "the store is not written")
        c.type("st")
        XCTAssertEqual(c.client.marked, "ㄔ")
    }
}
