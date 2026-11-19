import XCTest
@testable import ShanjieKit

/// docs/spec/decoder.md §6.6 (Swift): the 三聲變調 checkbox is stored under `tone3Sandhi` (absent is on), sent to every engine the shell builds, and the
/// checkbox reaches the core (typing ㄨㄛˊ ㄒㄧㄤˇ shows 我想 only while it is on).
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
}
