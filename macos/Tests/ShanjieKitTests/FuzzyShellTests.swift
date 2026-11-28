import XCTest
@testable import ShanjieKit

/// docs/spec/decoder.md §6.1, §6.4 (Swift): the 模糊輸入 flags are stored under `fuzzyFlags` (absent is 0), sent to
/// every engine the shell builds, reach the core (typing shows the tolerant word), and the adjacent-key checkbox is greyed in the Eten
/// layout with its value kept.
@MainActor
final class FuzzyShellTests: XCTestCase {
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

    /// Never chosen is 0 and the core is told so; every setting goes to the store and to the engine; an engine the shell builds
    /// again (a layout change) is sent the stored flags, not the core's default.
    func testFlagsAreStoredAndSentToEveryNewEngine() {
        let store = MemoryFuzzyStore()
        let shell = makeShell(store)
        let model = SettingsModel(shell: shell)
        XCTAssertEqual(model.fuzzyFlags, 0)
        XCTAssertEqual(shell.fuzzySent, [0], "the first engine is told the default too")
        XCTAssertNil(store.fuzzyFlags, "nothing is written until the user chooses")
        model.setFuzzy(bit: 1, true)
        XCTAssertEqual(store.fuzzyFlags, 2)
        model.setFuzzy(bit: 4, true)
        model.setFuzzy(bit: 0, true)
        model.setFuzzy(bit: 1, false)
        XCTAssertEqual(store.fuzzyFlags, 17)
        XCTAssertEqual(model.fuzzyFlags, 17, "the window follows the shell's notification")
        shell.selectLayout(.eten)  // builds a new engine
        XCTAssertEqual(shell.fuzzySent.count, 2)
        XCTAssertEqual(shell.fuzzySent.last, 17)
    }

    /// A stored value from outside the window (a hand-edited default) keeps only the bits the core knows (0-5); the core would refuse the rest.
    func testStoredFlagsKeepOnlyKnownBits() {
        let shell = makeShell(MemoryFuzzyStore(0xffff))
        XCTAssertEqual(shell.fuzzyFlags, 63)
        XCTAssertEqual(shell.fuzzySent, [63])
    }

    /// TYPO-ONE-SYM (docs/spec/decoder.md §6.2): the 單一注音打錯 checkbox is bit 5 of the same stored flags (value 32), off until chosen,
    /// independent of the other bits, and the settings window's sentence about it is pinned.
    func testOneSymbolBitIsStoredAsBit5() {
        let store = MemoryFuzzyStore()
        let model = SettingsModel(shell: makeShell(store))
        XCTAssertEqual(CoreEngine.fuzzyOneSymBit, 5)
        XCTAssertEqual(CoreEngine.fuzzyAllMask, 63)
        XCTAssertEqual(model.fuzzyFlags & 32, 0, "off by default")
        model.setFuzzy(bit: 5, true)
        XCTAssertEqual(store.fuzzyFlags, 32)
        model.setFuzzy(bit: 4, true)
        XCTAssertEqual(store.fuzzyFlags, 48)
        model.setFuzzy(bit: 5, false)
        XCTAssertEqual(store.fuzzyFlags, 16)
        XCTAssertTrue(SettingsModel.oneSymNote.hasPrefix("單一注音打錯："))
        XCTAssertTrue(SettingsModel.oneSymNote.contains("line、steam）維持英文"))
    }

    /// The real core: `ㄨㄛˇ ㄒㄧㄟˇ ㄑㄩˋ` (keys `我 v u o 3 …`, ㄒㄧㄟˇ is not a syllable of the lexicon) shows 我想去 with bit 5 and not without,
    /// the window turning it on with the engine running takes effect on the next keys, and it works in the Eten layout too (bit 4 does not).
    func testOneSymbolBitReachesTheCore() {
        let off = Controller(makeShell(MemoryFuzzyStore()))
        off.session.activate()
        off.type(Layouts.keys("ㄨㄛˇ ㄒㄧㄟˇ ㄑㄩˋ", eten: false))
        XCTAssertNotEqual(off.client.marked, "我想去", "bit 5 off: the slip is not guessed")

        let shell = makeShell(MemoryFuzzyStore())
        let model = SettingsModel(shell: shell)
        let c = Controller(shell)
        c.session.activate()
        model.setFuzzy(bit: 5, true)  // turned on from the window with the engine already running: no restart
        c.type(Layouts.keys("ㄨㄛˇ ㄒㄧㄟˇ ㄑㄩˋ", eten: false))
        XCTAssertEqual(c.client.marked, "我想去", "effective at once")

        let on = Controller(makeShell(MemoryFuzzyStore(32)))
        on.session.activate()
        on.type(Layouts.keys("ㄨㄛˇ ㄒㄧㄟˇ ㄑㄩˋ", eten: false))
        XCTAssertEqual(on.client.marked, "我想去")
    }

    /// Eten: the adjacent-key checkbox is greyed but the one-symbol one is not (it has no such state in the model), and the core reads it there.
    func testOneSymbolBitWorksInTheEtenLayout() {
        let store = MemoryFuzzyStore(32)
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore("eten"),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(),
                          rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(),
                          appProfileStore: MemoryAppProfileStore(), fuzzyStore: store, spacingStore: MemorySpacingStore())
        let model = SettingsModel(shell: shell)
        XCTAssertFalse(model.fuzzyAdjacentEnabled, "layout is Eten")
        XCTAssertEqual(model.fuzzyFlags, 32, "the value is kept")
        let c = Controller(shell)
        c.session.activate()
        c.type(Layouts.keys("ㄨㄛˇ ㄒㄧㄟˇ ㄑㄩˋ", eten: true))
        XCTAssertEqual(c.client.marked, "我想去")
    }

    /// The flags reach the core: with ㄓ/ㄗ on, `ㄗ ㄉㄠˋ` shows 知道; with the stored default (all off) it shows what it showed before.
    func testFlagsChangeWhatTheCoreShows() throws {
        let off = Controller(makeShell(MemoryFuzzyStore()))
        off.session.activate()
        off.type(Layouts.keys("ㄗ ㄉㄠˋ", eten: false))
        XCTAssertNotEqual(off.client.marked, "知道", "all off: the typed reading is exact")
        let on = Controller(makeShell(MemoryFuzzyStore(0b10)))
        on.session.activate()
        on.type(Layouts.keys("ㄗ ㄉㄠˋ", eten: false))
        XCTAssertEqual(on.client.marked, "知道")
    }

    /// The window changing the flags mid-session reaches the running engine: the same keys then show the tolerant word.
    func testFlagsFromTheWindowReachTheRunningEngine() {
        let shell = makeShell(MemoryFuzzyStore())
        let model = SettingsModel(shell: shell)
        let c = Controller(shell)
        c.session.activate()
        c.type(Layouts.keys("ㄗ ㄉㄠˋ", eten: false))
        XCTAssertNotEqual(c.client.marked, "知道")
        model.setFuzzy(bit: 1, true)
        XCTAssertEqual(c.client.marked, "知道", "the composition is recomputed with the new flags")
    }

    /// docs/spec/decoder.md §6.1: the adjacent-key checkbox follows the layout (greyed in Eten, where the core ignores bit 4); the value is kept
    /// and the pair checkboxes are not affected.
    func testAdjacentCheckboxIsGreyedInTheEtenLayout() {
        let shell = makeShell(MemoryFuzzyStore())
        let model = SettingsModel(shell: shell)
        model.setFuzzy(bit: 4, true)
        model.setFuzzy(bit: 2, true)
        XCTAssertTrue(model.fuzzyAdjacentEnabled)
        model.selectLayout(.eten)
        XCTAssertFalse(model.fuzzyAdjacentEnabled, "greyed in Eten")
        XCTAssertEqual(model.fuzzyFlags, 0b10100, "the value is kept")
        model.selectLayout(.standard)
        XCTAssertTrue(model.fuzzyAdjacentEnabled)
        XCTAssertEqual(model.fuzzyFlags, 0b10100)
    }
}
