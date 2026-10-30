import AppKit
import XCTest
@testable import ShanjieKit

/// docs/contracts/browser-first-syllable.md acceptance 1: the shell through the real core.
@MainActor
final class BrowserFirstSyllableTests: XCTestCase {
    private var resources: URL!
    private let chrome = "com.google.Chrome"

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    private func makeShell(_ store: MemoryPredictionStore = MemoryPredictionStore(), abbreviation: Bool = false) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: store, abbreviationStore: MemoryAbbreviationStore(abbreviation), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        return shell
    }

    /// Whether the panel is visible after each key of ㄋ ㄧ ˇ (你), then ㄏ (the second syllable's first key).
    private func visibility(bundle: String, shell: Shell? = nil) -> [Bool] {
        let c = Controller(shell ?? makeShell(), bundle: bundle)
        c.session.activate()
        return "su3c".map { k in
            c.type(String(k))
            return c.panel.visible
        }
    }

    /// (a) In a browser the first syllable shows no row, the second syllable's first key does.
    func testABrowserHidesTheRowForTheFirstSyllableOnly() {
        XCTAssertEqual(visibility(bundle: chrome), [false, false, false, true])
    }

    /// (b) Outside the list nothing changes; (c) the switch off is the same.
    func testOtherAppsAndTheSwitchOffKeepTheRow() {
        let textEdit = visibility(bundle: "com.apple.TextEdit")
        XCTAssertEqual(textEdit, [true, true, true, true], "every key has a row in a text view")
        XCTAssertEqual(visibility(bundle: chrome, shell: makeShell(MemoryPredictionStore(browserFirstSyllable: false))), textEdit)
    }

    /// (d) Tab enters the hidden row, which then shows; (e) ↓ opens the candidate window, which shows.
    func testTabAndDownStillShow() {
        let c = Controller(makeShell(), bundle: chrome)
        c.session.activate()
        c.type("s")
        XCTAssertFalse(c.panel.visible)
        XCTAssertTrue(c.press(48))  // Tab
        XCTAssertTrue(c.panel.visible)
        XCTAssertEqual(c.panel.selected, 0)
        c.press(Keys.esc)
        c.press(Keys.esc)
        let d = Controller(makeShell(), bundle: chrome)
        d.session.activate()
        d.type("su3")
        XCTAssertFalse(d.panel.visible)
        XCTAssertTrue(d.press(125))  // ↓
        XCTAssertTrue(d.panel.visible)
        XCTAssertGreaterThanOrEqual(d.panel.selected, 0)
    }

    /// (f) The one-syllable rule on the preedit.
    func testSingleSyllable() {
        for s in ["", "ㄘ", "ㄘㄜ", "ㄘㄜˋ", "測", "1"] { XCTAssertTrue(Shell.singleSyllable(s), s) }
        for s in ["測ㄕ", "測試", "ㄘ測"] { XCTAssertFalse(Shell.singleSyllable(s), s) }
    }

    /// (g) The rule reads no bundle ID of its own: one read per key, in the list or not.
    func testOneBundleReadPerKey() {
        for bundle in [chrome, "com.apple.TextEdit"] {
            let c = Controller(makeShell(), bundle: bundle)
            c.session.activate()
            for k in "su3c" {
                let before = c.client.bundleReads
                c.type(String(k))
                XCTAssertEqual(c.client.bundleReads - before, 1, "\(bundle) \(k)")
            }
        }
    }

    /// (h) Never chosen is on; the settings model turns it off, the store keeps it, a new shell reads it.
    func testTheSwitchIsStored() {
        let store = MemoryPredictionStore()
        let model = SettingsModel(shell: makeShell(store))
        XCTAssertTrue(model.browserFirstSyllable)
        model.setBrowserFirstSyllable(false)
        XCTAssertEqual(store.browserFirstSyllable, false)
        XCTAssertFalse(makeShell(store).browserFirstSyllableOn)
    }

    /// (i) An output that is not a key (the prediction switch's snapshot) follows the same rule.
    func testANonKeyOutputFollowsTheRule() {
        let c = Controller(makeShell(), bundle: chrome)
        c.session.activate()
        c.type("s")
        c.session.perform(.togglePrediction)
        c.session.perform(.togglePrediction)  // on again: the snapshot recomputes the row
        XCTAssertFalse(c.panel.visible)
        let t = Controller(makeShell(), bundle: "com.apple.TextEdit")
        t.session.activate()
        t.type("s")
        t.session.perform(.togglePrediction)
        t.session.perform(.togglePrediction)
        XCTAssertTrue(t.panel.visible, "outside the list the same snapshot shows the row")
    }

    /// (j) With 可省略韻母 on, ㄋ ㄔ are two units the row alone resolves, so it shows.
    func testAbbreviationOnKeepsTheRow() {
        let c = Controller(makeShell(abbreviation: true), bundle: chrome)
        c.session.activate()
        c.type("st")
        XCTAssertEqual(c.client.marked, "ㄋㄔ")
        XCTAssertTrue(c.panel.visible)
    }
}
