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
                          predictionStore: MemoryPredictionStore(), abbreviationStore: store, acgPackStore: MemoryAcgPackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(),
                          showsAbbreviation: false)
        let c = Controller(shell)
        c.session.activate()
        XCTAssertFalse(shell.abbreviationOn, "the stored on is not read")
        XCTAssertFalse(c.session.menu.contains { $0.action == .toggleAbbreviation })
        XCTAssertFalse(c.session.menu.map(\.title).contains("可省略韻母"))
        XCTAssertFalse(SettingsModel(shell: shell).showsAbbreviation)
        c.type("st")  // ㄋ, ㄔ
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
