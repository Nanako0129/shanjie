import AppKit
import XCTest
@testable import ShanjieKit

/// docs/spec/prediction.md §6, §7: the macOS shell offers 可省略韻母 (default off), shows its note and fixed examples, leaves the
/// candidate length cap off, and turns the English shadow off while the switch is on. Through the real core.
@MainActor
final class AbbreviationShellTests: XCTestCase {
    private var resources: URL!

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    private func makeShell(abbreviation: Bool) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(abbreviation), englishStore: MemoryEnglishStore(overwrite: true),
                          acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(),
                          profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(),
                          showsAbbreviation: Shell.shipsAbbreviation, spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        return shell
    }

    /// The value the app passes, the line under the toggle and the fixed example lines are the spec's.
    func testTheAppOffersTheSwitchWithTheSpecsNoteAndExamples() {
        XCTAssertTrue(Shell.shipsAbbreviation)
        XCTAssertTrue(SettingsModel(shell: makeShell(abbreviation: false)).showsAbbreviation)
        XCTAssertFalse(makeShell(abbreviation: false).abbreviationOn, "nothing saved is off")
        XCTAssertEqual(MenuEntry.Text.abbreviationNote, "只打聲母、不打聲調也能從預測列選詞，例如下面幾行。開著時中英混打不作用。")
        XCTAssertEqual(MenuEntry.Text.abbreviationExamples, ["ㄇ ㄊ → 明天", "ㄇ ㄅ ㄈ → 沒辦法", "ㄗㄨㄟ ㄐㄧㄣ → 最近"])
    }

    /// English mixing does not act while the switch is on: o k (ㄟ then ㄜ, the same column) is English `ok` with the switch off
    /// (重打同一個位置算英文 is on in this rig) and two unfinished units with it on. The shell's ASCII state follows the switch too.
    func testEnglishMixingDoesNotActWhileTheSwitchIsOn() {
        for (abbreviation, marked) in [(false, "ok"), (true, "ㄟㄜ")] {
            let shell = makeShell(abbreviation: abbreviation)
            XCTAssertTrue(shell.englishOn, "English mixing is on in this rig")
            XCTAssertEqual(shell.asciiOn, !abbreviation)
            let c = Controller(shell, bundle: "com.apple.TextEdit")
            c.session.activate()
            c.type("ok")
            XCTAssertEqual(c.client.marked, marked, "abbreviation \(abbreviation)")
        }
    }

    /// Each example's keys give its word first, with an empty context; and toneless ㄒ ㄧ ㄢ shows 現在, two characters for
    /// one unfinished unit, which the length cap would drop: the macOS shell leaves the cap off.
    func testTheExamplesRankFirstAndTheLengthCapIsOff() {
        // Standard layout: ㄇ a, ㄊ w, ㄅ 1, ㄈ z, ㄗ y, ㄨ j, ㄟ o, ㄐ r, ㄧ u, ㄣ p, ㄒ v, ㄢ 0.
        for (keys, word) in [("aw", "明天"), ("a1z", "沒辦法"), ("yjorup", "最近")] {
            let c = Controller(makeShell(abbreviation: true), bundle: "com.apple.TextEdit")
            c.session.activate()
            c.type(keys)
            XCTAssertEqual(c.panel.items.first, word, "\(keys): \(c.panel.items)")
        }
        let c = Controller(makeShell(abbreviation: true), bundle: "com.apple.TextEdit")
        c.session.activate()
        c.type("vu0")
        XCTAssertTrue(c.panel.items.contains("現在"), "\(c.panel.items)")
        XCTAssertFalse(c.panel.items.contains("西安"), "R3: ㄒㄧㄢ is one unit or three, never ㄒㄧ/ㄢ")
    }
}
