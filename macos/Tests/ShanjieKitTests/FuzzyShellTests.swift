import XCTest
@testable import ShanjieKit

/// docs/contracts/fuzzy-input.md section 2.2 and 3.4 (Swift): the 模糊輸入 flags are stored under `fuzzyFlags` (absent is 0), sent to
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
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(),
                          rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(),
                          appProfileStore: MemoryAppProfileStore(), fuzzyStore: store)
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

    /// A stored value from outside the window (a hand-edited default) keeps only the bits the core knows; the core would refuse the rest.
    func testStoredFlagsKeepOnlyKnownBits() {
        let shell = makeShell(MemoryFuzzyStore(0xffff))
        XCTAssertEqual(shell.fuzzyFlags, 31)
        XCTAssertEqual(shell.fuzzySent, [31])
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

    /// Section 2.2: the adjacent-key checkbox follows the layout (greyed in Eten, where the core ignores bit 4); the value is kept
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
