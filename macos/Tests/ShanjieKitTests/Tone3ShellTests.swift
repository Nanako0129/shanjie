import XCTest
@testable import ShanjieKit

/// docs/spec/decoder.md §6.6 (Swift): the 變調與口語讀音 checkbox is stored under `tone3Sandhi` (absent is on), sent to every engine the shell builds, and the
/// checkbox reaches the core (typing ㄨㄛˊ ㄒㄧㄤˇ shows 我想, and ㄌㄧˋ ˙ㄏㄞ shows 厲害, only while it is on).
@MainActor
final class Tone3ShellTests: XCTestCase {
    private var resources: URL!

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    private func makeShell(_ store: MemoryFuzzyStore) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(),
                          rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(),
                          appProfileStore: MemoryAppProfileStore(), fuzzyStore: store, spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        return shell
    }

    /// No value is on, and the first engine is told so; the checkbox writes the store and goes to the engine; a rebuilt engine (a layout change) is sent the
    /// stored value, not the core's default.
    func testAbsentIsOnAndTheCheckboxIsStoredAndSent() {
        let store = MemoryFuzzyStore()
        let shell = makeShell(store)
        let model = SettingsModel(shell: shell)
        XCTAssertTrue(model.tone3)
        XCTAssertEqual(shell.tone3Sent, [true], "the first engine is told the default too")
        XCTAssertNil(store.tone3Sandhi, "nothing is written until the user chooses")
        model.setTone3(false)
        XCTAssertEqual(store.tone3Sandhi, false)
        XCTAssertFalse(model.tone3, "the window follows the shell's notification")
        shell.selectLayout(.eten)  // builds a new engine
        XCTAssertEqual(shell.tone3Sent, [true, false])
        XCTAssertEqual(makeShell(MemoryFuzzyStore(tone3Sandhi: false)).tone3Sent, [false], "a stored off is sent to the first engine")
    }

    /// The switch reaches the core: ㄨㄛˊ ㄒㄧㄤˇ shows 我想 while it is on (ㄨㄛˊ has no word of its own: read as ㄨㄛˇ), and with a stored off the same keys
    /// do not (ㄨㄛˊ is not accepted). Turned off from the window in the middle of a composition, the accepted syllable is kept as ㄨㄛˇ, so 我想 stays.
    func testTheCheckboxChangesWhatTheCoreShows() {
        let shell = makeShell(MemoryFuzzyStore())
        let model = SettingsModel(shell: shell)
        let c = Controller(shell)
        c.session.activate()
        c.type(Layouts.keys("ㄨㄛˊ ㄒㄧㄤˇ", eten: false))
        XCTAssertEqual(c.client.marked, "我想")
        model.setTone3(false)
        XCTAssertEqual(c.client.marked, "我想", "the composition keeps a path: the syllable is stored as its third tone")
        let off = Controller(makeShell(MemoryFuzzyStore(tone3Sandhi: false)))
        off.session.activate()
        off.type(Layouts.keys("ㄨㄛˊ ㄒㄧㄤˇ", eten: false))
        XCTAssertNotEqual(off.client.marked, "我想", "off: ㄨㄛˊ is not accepted")
    }

    /// The one checkbox is both: its title, and colloquial sandhi through the same switch (docs/spec/decoder.md §6.6, §8.7): ㄌㄧˋ ˙ㄏㄞ shows 厲害 (˙ㄏㄞ has no
    /// word of its own: accepted after a syllable, the word read under ㄌㄧˋ ㄏㄞˋ) and ㄖㄜˋ ㄊㄥ ㄊㄥ shows 熱騰騰 (a row of the lexicon read only while it is on);
    /// with a stored off neither does.
    func testTheTitleAndColloquialSandhiOnTheSameSwitch() {
        XCTAssertEqual(SettingsModel.tone3Title, "變調與口語讀音")
        XCTAssertTrue(SettingsModel.tone3Note.hasPrefix("變調與口語讀音："))
        for (reading, word) in [("ㄌㄧˋ ㄏㄞ˙", "厲害"), ("ㄖㄜˋ ㄊㄥ ㄊㄥ", "熱騰騰")] {
            let on = Controller(makeShell(MemoryFuzzyStore()))
            on.session.activate()
            on.type(Layouts.keys(reading, eten: false))
            XCTAssertEqual(on.client.marked, word)
            let off = Controller(makeShell(MemoryFuzzyStore(tone3Sandhi: false)))
            off.session.activate()
            off.type(Layouts.keys(reading, eten: false))
            XCTAssertNotEqual(off.client.marked, word, "off: \(reading)")
        }
    }
}
