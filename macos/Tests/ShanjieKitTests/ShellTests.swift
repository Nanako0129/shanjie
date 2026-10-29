import AppKit
import Carbon.HIToolbox
import CShanjie
import XCTest
@testable import ShanjieKit

/// Acceptance 4 (docs/contracts/s3b.md section 10): fake clients driving the shell through the
/// real C core and the real LM. No engine is ever faked.
@MainActor
final class ShellTests: XCTestCase {
    private var resources: URL!

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    private func makeShell(secure: Bool = false, store: LayoutStore = MemoryLayoutStore(), english: EnglishStore = MemoryEnglishStore()) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { secure }, layoutStore: store, learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: english, acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        return shell
    }

    // MARK: fake client

    func testTypeNihaoThenEnterCommits() {
        let c = Controller(makeShell())
        c.session.activate()
        XCTAssertEqual(c.type("su3cl3"), Array(repeating: true, count: 6))
        XCTAssertEqual(c.client.marked, "你好")
        XCTAssertEqual(c.client.calls.last, .mark("你好", cursor: 2, underlined: true))
        XCTAssertTrue(c.press(Keys.enter))
        XCTAssertEqual(c.client.calls.filter { if case .insert = $0 { true } else { false } }, [.insert("你好")])
        XCTAssertEqual(c.client.text, "你好")
        XCTAssertEqual(c.client.marked, "")
        XCTAssertFalse(c.panel.visible)
    }

    /// enter-pending contract §2: Shift+Enter commits what is shown and passes the key on, so the app gets its line
    /// break (rules 12b, 19a); Enter with an unfinished syllable commits it as shown (12a).
    func testShiftEnterCommitsThenPassesTheKeyOn() {
        let c = Controller(makeShell(english: MemoryEnglishStore(false)))  // saved off: with it on the 1 may show as a digit
        c.session.activate()
        c.type("su3cl3")
        XCTAssertFalse(c.press(Keys.enter, flags: .shift))
        XCTAssertEqual(c.client.text, "你好")
        XCTAssertEqual(c.client.marked, "")
        c.type("1")
        XCTAssertEqual(c.client.marked, "ㄅ")
        XCTAssertFalse(c.press(Keys.enter, flags: .shift))
        XCTAssertEqual(c.client.text, "你好ㄅ")
        c.type("su3a")
        XCTAssertTrue(c.press(Keys.enter))
        XCTAssertEqual(c.client.text, "你好ㄅ你ㄇ")
    }

    func testCandidatesShowThenNumberSelects() throws {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3 ")  // ㄋㄧˇ, then space opens the candidates (rule 15)
        XCTAssertTrue(c.panel.visible)
        XCTAssertTrue((2...9).contains(c.panel.items.count), "the test presses 2, so it needs 2 to 9 candidates")
        XCTAssertEqual(c.panel.selected, 0)
        let second = try XCTUnwrap(c.panel.items.dropFirst().first, "fewer than two candidates")
        XCTAssertTrue(c.type("2")[0])
        XCTAssertEqual(c.client.marked, second, "the composition shows the chosen candidate")
        XCTAssertFalse(c.panel.visible)
        XCTAssertEqual(c.panel.items, [], "the panel's candidates are cleared")
        XCTAssertEqual(c.client.text, "")
    }

    /// V3 (docs/contracts/v3-engine.md section 5): the first key shows the prediction row with no selection and no
    /// numbers; Tab enters it, selects the first item and shows the numbers.
    func testPredictionRowShowsNoNumbersUntilTabEnters() throws {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("s")  // ㄋ
        XCTAssertTrue(c.panel.visible)
        XCTAssertEqual(c.panel.selected, -1)
        XCTAssertFalse(c.panel.items.isEmpty)
        let passive = CandidateCells()
        let before = passive.update(candidates: c.panel.items, notes: c.panel.items.map { _ in nil },
                                    selected: c.panel.selected, first: c.panel.first, columns: c.panel.columns)
        // Predict-down contract B.3.2: the first cell shows the Tab glyph where its number goes; nothing else shows a number.
        XCTAssertEqual(before.cells[0].numberText, CellMetrics.tabGlyph)
        XCTAssertTrue(before.cells[0].showsNumber, "the Tab glyph shows on the not-entered row")
        XCTAssertTrue(before.cells.dropFirst().allSatisfy { !$0.showsNumber }, "no number on the not-entered row")
        XCTAssertTrue(c.press(48))  // Tab
        XCTAssertEqual(c.panel.selected, 0)
        let entered = CandidateCells()
        let after = entered.update(candidates: c.panel.items, notes: c.panel.items.map { _ in nil },
                                   selected: c.panel.selected, first: c.panel.first, columns: c.panel.columns)
        XCTAssertTrue(after.cells.allSatisfy(\.showsNumber), "numbers show once entered")
        XCTAssertEqual(after.cells[0].numberText, "1", "the glyph is the number 1 once entered")
    }

    func testLineRectReachesThePanel() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3 ")
        XCTAssertEqual(c.panel.lineRect, c.client.line)
    }

    /// s3b2 section 10: the panel takes the client's light or dark appearance.
    func testAppearanceReachesThePanel() {
        let c = Controller(makeShell())
        c.client.appearance = NSAppearance(named: .aqua)
        c.session.activate()
        c.type("su3 ")
        XCTAssertEqual(c.panel.appearance?.name, .aqua)
    }

    /// s3b2 section 2.2: the rectangle is asked for at the composition cursor, not at the end.
    func testLineRectIsAskedAtTheCursor() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3cl3")
        _ = c.session.handle(Keys.event(123))  // ←: the cursor moves between the two syllables
        c.type(" ")
        XCTAssertEqual(c.client.lineCursor, 1)
    }

    /// The client's lineRect is a synchronous IPC: a selection move in the grid reuses the last answer,
    /// a new syllable asks again.
    func testLineRectIsNotAskedAgainForSelectionMoves() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3 ")
        // V3: every key of the composition shows or updates the prediction row, so each of them asked once
        // (s, su, su3); the space only opens the candidate window over the same preedit and cursor.
        let asked = c.client.lineAsks
        XCTAssertEqual(asked, 3)
        XCTAssertTrue(c.press(125))  // ↓ expands
        XCTAssertTrue(c.press(125))  // ↓ one row
        XCTAssertTrue(c.press(124))  // → one cell
        XCTAssertEqual(c.client.lineAsks, asked, "selection moves must not ask the client again")
        XCTAssertEqual(c.panel.lineRect, c.client.line)
        c.type("cl3 ")
        XCTAssertGreaterThan(c.client.lineAsks, asked, "a new composition asks again")
    }

    /// s3b2 section 8: down expands (the panel is told the columns, first and total); a click on a
    /// lower row picks that cell, and after a scroll the position counts from `first`.
    func testExpandedGridReachesThePanelAndAClickPicksTheCell() throws {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("g4 ")  // ㄕˋ has more than four pages of candidates
        XCTAssertEqual(c.panel.columns, 0)
        XCTAssertEqual(c.panel.items.count, 9)
        let down: UInt16 = 125
        XCTAssertTrue(c.press(down))
        XCTAssertEqual(c.panel.columns, 9)
        XCTAssertEqual(c.panel.first, 0)
        XCTAssertGreaterThan(c.panel.total, 45)
        XCTAssertEqual(c.panel.items.count, 45)
        XCTAssertEqual(c.panel.selected, 0)
        let lower = c.panel.items[11]   // second row, third column
        c.panel.click(11)
        XCTAssertFalse(c.panel.visible)
        XCTAssertEqual(c.client.marked, lower)
        XCTAssertEqual(c.client.text, "")
    }

    func testClickAfterScrollingCountsFromFirst() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("g4 ")
        for _ in 0..<6 { c.press(125) }   // expand, then five rows down: the sixth row scrolls the grid
        XCTAssertEqual(c.panel.first, 9)
        let target = c.panel.items[3]
        c.panel.click(3)
        XCTAssertEqual(c.client.marked, target)
        XCTAssertFalse(c.panel.visible)
    }

    func testClickByNonOwnerOrOutOfPageDoesNothing() {
        let shell = makeShell()
        let a = Controller(shell), b = Controller(shell)
        a.session.activate()
        a.type("su3 ")
        let n = a.panel.items.count
        a.panel.click(n)                 // past this page
        a.panel.click(-1)
        XCTAssertTrue(a.panel.visible)
        let before = a.client.calls
        b.session.candidateSelected(at: 1)   // b does not own the composition
        XCTAssertEqual(a.client.calls, before)
        XCTAssertTrue(a.panel.visible)
    }

    func testMouseClickPicksThroughTheCoreAndNeverInsertsDirectly() throws {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3 ")
        let second = try XCTUnwrap(c.panel.items.dropFirst().first, "fewer than two candidates")
        c.panel.click(1)
        XCTAssertEqual(c.client.marked, second)
        XCTAssertFalse(c.panel.visible)
        XCTAssertEqual(c.client.text, "", "never inserted directly")
    }

    /// s3a rule 22a: on an empty composition a tone key types its mark (3 gives "ˇ", not "3") into
    /// the composition; Enter sends it.
    func testToneKeyOnEmptyCompositionTypesItsMark() {
        let c = Controller(makeShell(english: MemoryEnglishStore(false)))  // the switch saved off: rule 22a as before
        c.session.activate()
        XCTAssertEqual(c.type("3"), [true])
        XCTAssertEqual(c.client.marked, "ˇ")
        XCTAssertEqual(c.client.text, "")
        _ = c.session.handle(Keys.event(36))  // Return
        XCTAssertEqual(c.client.text, "ˇ")
    }

    /// Digits.md A.3.4 / A.3.7: with 中英混打 on (the default now: nothing saved) and digits.tsv in Resources, the same key on an empty
    /// composition is the digit "3"; a saved off keeps the mark (the test above).
    func testToneKeyOnEmptyCompositionTypesTheDigitWhenEnglishMixingIsOn() {
        let c = Controller(makeShell())
        c.session.activate()
        XCTAssertEqual(c.type("3"), [true])
        XCTAssertEqual(c.client.marked, "3")
        _ = c.session.handle(Keys.event(36))  // Return
        XCTAssertEqual(c.client.text, "3")
    }

    func testCapsLockKeyPassesWithoutChangingAnything() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3")
        let before = c.client.calls
        XCTAssertEqual(c.type("cl3", flags: .capsLock), [false, false, false])
        XCTAssertFalse(c.press(Keys.enter, flags: .capsLock))
        XCTAssertEqual(c.client.calls, before)
        c.type("cl3")
        c.press(Keys.enter)
        XCTAssertEqual(c.client.text, "你好", "the composition was untouched")
    }

    func testKeyOutsideTheTableCommitsThenPasses() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3")
        XCTAssertFalse(c.press(Keys.keypad0))
        XCTAssertEqual(c.client.text, "你")
        XCTAssertEqual(c.client.marked, "")
        let calls = c.client.calls
        XCTAssertFalse(c.press(Keys.keypad0), "empty composition: pass through")
        XCTAssertEqual(c.client.calls, calls)
    }

    func testNilEventAndFlagsChanged() {
        let c = Controller(makeShell())
        c.session.activate()
        XCTAssertFalse(c.session.handle(nil))
        XCTAssertEqual(c.client.calls, [])
        c.type("su3")
        let calls = c.client.calls
        XCTAssertFalse(c.session.handle(Keys.event(UInt16(kVK_Shift), flags: .shift, type: .flagsChanged)))
        XCTAssertEqual(c.client.calls, calls, "flagsChanged never reaches the core")
        XCTAssertFalse(c.session.handle(nil))
        XCTAssertEqual(c.client.text, "你", "nil with a composition commits first")
        XCTAssertEqual(c.client.marked, "")
    }

    func testCommitComposition() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3cl3 ")
        XCTAssertTrue(c.panel.visible)
        c.session.commitComposition()
        XCTAssertEqual(c.client.text, "你好")
        XCTAssertEqual(c.client.marked, "")
        XCTAssertFalse(c.panel.visible)
        c.press(Keys.enter)
        XCTAssertEqual(c.client.text, "你好", "nothing left to commit")
    }

    /// Section 7: a non-zero code (here 2, an out-of-range kind, from the real core) clears the
    /// composition and the panel and lets the key pass.
    func testNonZeroReturnCode() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3 ")
        XCTAssertTrue(c.panel.visible)
        XCTAssertFalse(c.session.send(ShanjieKey(kind: 99, ch: 0, modifiers: 0)))
        XCTAssertEqual(c.client.marked, "")
        XCTAssertFalse(c.panel.visible)
        XCTAssertEqual(c.panel.items, [])
        XCTAssertFalse(c.press(Keys.enter), "the core holds no composition either")
        XCTAssertEqual(c.client.text, "")
        // The next keys start afresh: the old ㄋㄧˇ does not come back.
        c.type("cl3")
        XCTAssertEqual(c.client.marked, "好")
        c.press(Keys.enter)
        XCTAssertEqual(c.client.text, "好")
    }

    /// The same with a composition but no candidates open, and code 2 from the key path.
    func testNonZeroReturnCodeWithoutCandidates() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3cl")
        XCTAssertFalse(c.session.send(ShanjieKey(kind: 1, ch: 0xD800, modifiers: 0)))  // not a scalar
        XCTAssertEqual(c.client.marked, "")
        c.type("3")  // rule 22a: puts ˇ into the composition; the Enter below sends it
        c.press(Keys.enter)
        XCTAssertFalse(c.client.text.contains("你"))
        XCTAssertEqual(c.client.marked, "")
    }

    func testEngineThatCannotBeBuiltPassesEveryKey() {
        let shell = Shell(resources: resources.appendingPathComponent("missing"), panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        XCTAssertNil(shell.engine)
        let c = Controller(shell)
        c.session.activate()
        XCTAssertEqual(c.type("su3"), [false, false, false])
        XCTAssertEqual(c.client.calls, [])
    }

    // MARK: profile (observable)

    func testProfileFollowsTheApp() {
        let shell = makeShell()
        let discord = Controller(shell, bundle: "com.hnc.Discord")
        discord.session.activate()
        discord.type(Row131.standardKeys)
        discord.press(Keys.enter)
        let textEdit = Controller(shell, bundle: "com.apple.TextEdit")
        textEdit.session.activate()
        textEdit.type(Row131.standardKeys)
        textEdit.press(Keys.enter)
        XCTAssertEqual(discord.client.text, Row131.chat)
        XCTAssertEqual(textEdit.client.text, Row131.formal)
        XCTAssertNotEqual(discord.client.text, textEdit.client.text)
        XCTAssertEqual(Row131.standardKeys, Selftest.row131Standard)
    }

    func testEveryChatAppGetsTheChatProfile() {
        let shell = makeShell()
        for app in Shell.chatApps.sorted() + ["com.apple.TextEdit", "com.apple.Notes"] {
            let c = Controller(shell, bundle: app)
            c.session.activate()
            c.type(Row131.standardKeys)
            c.press(Keys.enter)
            XCTAssertEqual(c.client.text, Shell.chatApps.contains(app) ? Row131.chat : Row131.formal)
        }
    }

    // MARK: composition owner

    /// (a) A is composing when B gets a key: A's composition goes back to A, never into B.
    func testOwnerA_keyOnAnotherControllerCommitsToTheOwner() {
        let shell = makeShell()
        let a = Controller(shell), b = Controller(shell)
        a.session.activate()
        a.type("su3 ")
        XCTAssertTrue(a.panel.visible)
        let aCandidates = a.panel.items
        b.type("c")  // ㄏ
        XCTAssertEqual(a.client.text, "你")
        XCTAssertEqual(a.client.marked, "")
        // The panel is the shell's one panel: A's candidate window is gone (selection -1) and it now shows B's
        // prediction row for ㄏ (V3), not A's candidates.
        XCTAssertEqual(a.panel.selected, -1)
        XCTAssertTrue(b.panel.visible)
        XCTAssertFalse(b.panel.items.isEmpty)
        XCTAssertNotEqual(b.panel.items, aCandidates, "A's candidates must not leak into the shared panel")
        // B's row is what a fresh shell shows for ㄏ alone.
        let fresh = Controller(makeShell())
        fresh.session.activate()
        fresh.type("c")
        XCTAssertEqual(b.panel.items, fresh.panel.items)
        XCTAssertEqual(b.client.marked, "ㄏ")
        XCTAssertFalse(b.client.calls.contains { if case .insert = $0 { true } else { false } })
        b.type("l3")
        b.press(Keys.enter)
        XCTAssertEqual(b.client.text, "好")
        XCTAssertEqual(a.client.text, "你")
    }

    /// (b) A is released while composing: its composition is dropped, never shown in B.
    func testOwnerB_releasedOwnerIsDiscarded() {
        let shell = makeShell()
        var a: Controller? = Controller(shell)
        let aClient = a!.client
        a!.session.activate()
        a!.type("su3")
        a = nil
        let b = Controller(shell)
        b.session.activate()
        b.type("c")
        XCTAssertEqual(b.client.marked, "ㄏ")
        b.type("l3")
        b.press(Keys.enter)
        XCTAssertEqual(b.client.text, "好")
        XCTAssertEqual(aClient.text, "", "discarded, not committed")
    }

    /// (b) observed at the release itself: the owner's deinit discards and clears the shared
    /// panel and array, before any other controller does anything.
    func testOwnerB_deinitDiscardsAtOnce() {
        let shell = makeShell()
        var a: Controller? = Controller(shell)
        let panel = a!.panel
        a!.session.activate()
        a!.type("su3 ")
        XCTAssertTrue(panel.visible)
        a = nil
        XCTAssertFalse(panel.visible)
        XCTAssertEqual(panel.items, [])
        XCTAssertFalse(shell.composing)
    }

    /// (c) B is composing when A's deactivateServer arrives late: nothing reaches A.
    func testOwnerC_lateDeactivateOfANonOwner() {
        let shell = makeShell()
        let a = Controller(shell), b = Controller(shell)
        a.session.activate()
        b.session.activate()
        b.type("su3 ")
        let aCalls = a.client.calls
        a.session.deactivate()
        a.session.commitComposition()
        XCTAssertEqual(a.client.calls, aCalls)
        XCTAssertTrue(b.panel.visible, "B's candidates stay open")
        XCTAssertFalse(b.panel.items.isEmpty, "and its candidates are intact")
        // Predict-down contract B.4 (expectation change): the window's first candidate is the prediction row's first item now
        // (it was the lexicon's first, 你), so the test names it from the panel instead of by its text.
        let first = b.panel.items[0]
        b.press(Keys.enter)
        b.press(Keys.enter)
        XCTAssertEqual(b.client.text, first)
    }

    func testOwnerDeactivateCommitsUnlessSecure() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3 ")
        c.session.deactivate()
        XCTAssertEqual(c.client.text, "你")
        XCTAssertEqual(c.client.marked, "")
        XCTAssertFalse(c.panel.visible)

        let s = Controller(makeShell(secure: true))
        s.session.activate()
        s.type("su3")
        s.session.deactivate()
        XCTAssertEqual(s.client.text, "", "secure input: discarded")
        XCTAssertEqual(s.client.marked, "")
        s.session.activate()
        s.press(Keys.enter)
        XCTAssertEqual(s.client.text, "", "the core discarded it too (reset mode 1)")
    }

    /// A new owner gets its own app's profile, also when it never got activateServer.
    func testOwnerChangeReappliesTheProfile() {
        let shell = makeShell()
        let a = Controller(shell, bundle: "com.hnc.Discord"), b = Controller(shell, bundle: "com.apple.TextEdit")
        a.session.activate()
        a.type("su3")
        b.type(Row131.standardKeys)
        b.press(Keys.enter)
        XCTAssertEqual(b.client.text, Row131.formal)
        XCTAssertEqual(a.client.text, "你")
        // S2h §11: without a left context, as before S2h; 你 as history would make chat pick 期中 like formal.
        a.client.selectedOverride = NSRange(location: NSNotFound, length: 0)
        a.type(Row131.standardKeys)  // and back: Discord's chat profile again
        a.press(Keys.enter)
        XCTAssertEqual(a.client.text, "你" + Row131.chat)
    }

    // MARK: keyboard layout (section 13.2)

    /// `s` is ㄋ on the standard layout and ㄙ on ETen (s3a section 1).
    private static let probe = "s", standardProbe = "ㄋ", etenProbe = "ㄙ"

    func testStoredEtenLayoutAppliesFromTheStart() {
        let store = MemoryLayoutStore("eten")
        let c = Controller(makeShell(store: store))
        c.session.activate()
        XCTAssertEqual(c.session.layout, .eten)
        c.type(Self.probe)
        XCTAssertEqual(c.client.marked, Self.etenProbe, "the stored layout was not used to build the engine")
        XCTAssertEqual(store.layout, "eten")
    }

    func testMissingOrInvalidLayoutIsStandard() {
        for value in [nil, "", "bogus", "zhuyin", "ETEN", "com.nyanako.inputmethod.shanjie.eten"] as [String?] {
            let c = Controller(makeShell(store: MemoryLayoutStore(value)))
            c.session.activate()
            XCTAssertEqual(c.session.layout, .standard, "\(value ?? "nil")")
            c.type(Self.probe)
            XCTAssertEqual(c.client.marked, Self.standardProbe, "\(value ?? "nil")")
        }
    }

    func testMenuSelectionCommitsSwitchesAndStores() {
        let store = MemoryLayoutStore()
        let c = Controller(makeShell(store: store))
        c.session.activate()
        c.type("su3")
        c.session.selectLayout(.eten)
        XCTAssertEqual(c.client.text, "你", "the composition is committed before the switch")
        XCTAssertEqual(c.client.marked, "")
        XCTAssertEqual(store.layout, "eten")
        XCTAssertEqual(c.session.layout, .eten)
        c.type(Self.probe)
        XCTAssertEqual(c.client.marked, Self.etenProbe)
        c.press(Keys.esc)
        c.session.selectLayout(.standard)
        XCTAssertEqual(store.layout, "standard")
        c.type(Self.probe)
        XCTAssertEqual(c.client.marked, Self.standardProbe)
    }

    /// sw (docs/contracts/sw-sensitive-demote.md section 3): the menu item is checked by default, flips the
    /// core's setting (the frozen probe's row ㄍㄠˇ ㄨㄢˊ ㄌㄜ˙; the user report ㄍㄠˇ ㄨㄢˊ ㄓㄜˋ ㄅㄛ is right on the 通語 SJ-LM-7-9.7M files with or without the table), is stored, and a new shell reads the stored
    /// choice (a stored off survives a restart and a layout switch rebuild).
    func testDemoteMenuItemTogglesStoresAndSurvivesRestart() {
        let report = Layouts.keys("ㄍㄠˇ ㄨㄢˊ ㄌㄜ˙", eten: false)
        let store = MemoryDemoteStore()
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: store, predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        let c = Controller(shell)
        c.session.activate()
        let item = { c.session.menu.first { $0.action == .toggleDemote } }
        XCTAssertEqual(item()?.title, "避免把敏感字詞排在前面")
        XCTAssertEqual(item()?.checked, true, "default on")
        c.type(report)
        XCTAssertEqual(c.client.marked, "搞完了")
        c.session.perform(.toggleDemote)          // mid-composition: the snapshot is shown at once
        XCTAssertEqual(store.demote, false)
        XCTAssertEqual(item()?.checked, false)
        XCTAssertEqual(c.client.marked, "睪丸了")
        c.session.perform(.toggleDemote)
        XCTAssertEqual(c.client.marked, "搞完了")
        c.session.perform(.toggleDemote)
        XCTAssertEqual(c.client.marked, "睪丸了")
        c.press(Keys.esc)
        c.type(report)
        XCTAssertEqual(c.client.marked, "睪丸了")
        c.press(Keys.esc)
        c.session.selectLayout(.eten)      // the rebuilt engine gets the stored setting too
        c.session.selectLayout(.standard)
        c.type(report)
        XCTAssertEqual(c.client.marked, "睪丸了")
        c.press(Keys.esc)
        let again = Controller(Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                                     learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: store, predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore()))
        again.session.activate()
        again.type(report)
        XCTAssertEqual(again.client.marked, "睪丸了", "a new shell reads the stored off")
        again.press(Keys.esc)
        again.session.perform(.toggleDemote)
        XCTAssertEqual(store.demote, true)
        again.type(report)
        XCTAssertEqual(again.client.marked, "搞完了")
    }

    /// V3 (docs/contracts/v3-engine.md section 10.5): the "即時預測" item is checked by default, switches the row off
    /// and on (shown at once mid-composition), is stored, and a new shell reads the stored off.
    func testPredictionMenuItemTogglesStoresAndSurvivesRestart() {
        let store = MemoryPredictionStore()
        let make = {
            Shell(resources: self.resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                  learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: store, abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        }
        let c = Controller(make())
        c.session.activate()
        let item = { c.session.menu.first { $0.action == .togglePrediction } }
        XCTAssertEqual(item()?.title, "即時預測")
        XCTAssertEqual(item()?.checked, true, "default on")
        c.type("s")  // ㄋ
        XCTAssertTrue(c.panel.visible)
        c.session.perform(.togglePrediction)  // mid-composition: the snapshot hides the row at once
        XCTAssertEqual(store.prediction, false)
        XCTAssertEqual(item()?.checked, false)
        XCTAssertFalse(c.panel.visible)
        c.press(Keys.esc)
        c.type("s")
        XCTAssertFalse(c.panel.visible, "off: no row on the first key")
        c.session.perform(.togglePrediction)  // back on: the row for the pending ㄋ shows at once
        XCTAssertEqual(store.prediction, true)
        XCTAssertTrue(c.panel.visible)
        c.session.perform(.togglePrediction)
        c.press(Keys.esc)
        let again = Controller(make())
        again.session.activate()
        again.type("s")
        XCTAssertFalse(again.panel.visible, "a new shell reads the stored off")
        XCTAssertEqual(again.session.menu.first { $0.action == .togglePrediction }?.checked, false)
    }

    /// V3 section 12.1: "可省略韻母" sits right after "即時預測", is unchecked by default, is stored, makes ㄋ then ㄔ two units
    /// (preedit "ㄋㄔ"; off, ㄔ replaces ㄋ), is sent again to the engine a rebuild makes (the word pack switch and a layout switch rebuild it),
    /// is greyed while the prediction row is off, and a new shell reads the stored on.
    func testAbbreviationMenuItemTogglesStoresRebuildsAndSurvivesRestart() {
        let store = MemoryAbbreviationStore()
        let make = {
            Shell(resources: self.resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                  learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: store, englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        }
        let c = Controller(make())
        c.session.activate()
        let item = { c.session.menu.first { $0.action == .toggleAbbreviation } }
        let titles = c.session.menu.map(\.title)
        XCTAssertEqual(titles.firstIndex(of: "可省略韻母"), titles.firstIndex(of: "即時預測").map { $0 + 1 })
        XCTAssertEqual(item()?.checked, false, "default off")
        c.type("st")  // ㄋ, ㄔ
        XCTAssertEqual(c.client.marked, "ㄔ", "off: the second key replaces the first")
        c.press(Keys.esc)
        c.session.perform(.toggleAbbreviation)
        XCTAssertEqual(store.abbreviation, true)
        XCTAssertEqual(item()?.checked, true)
        c.type("st")
        XCTAssertEqual(c.client.marked, "ㄋㄔ", "on: two units")
        c.press(Keys.esc)
        c.session.perform(.toggleAcgPack)  // rebuilds the engine, whose default is off
        c.session.perform(.toggleAcgPack)
        c.type("st")
        XCTAssertEqual(c.client.marked, "ㄋㄔ", "the rebuilt engine got the setting")
        c.press(Keys.esc)
        c.session.perform(.layout(.eten))  // a layout switch rebuilds it too; Eten: n = ㄋ, b = ㄅ
        c.type("nb")
        XCTAssertEqual(c.client.marked, "ㄋㄅ", "the engine rebuilt by a layout switch got the setting")
        c.press(Keys.esc)
        c.session.perform(.layout(.standard))
        c.session.perform(.togglePrediction)
        XCTAssertEqual(item()?.enabled, false, "greyed while the row is off")
        XCTAssertEqual(item()?.checked, true)
        c.session.perform(.togglePrediction)
        XCTAssertEqual(item()?.enabled, true)
        let again = Controller(make())
        again.session.activate()
        again.type("st")
        XCTAssertEqual(again.client.marked, "ㄋㄔ", "a new shell reads the stored on")
    }

    private func englishController(vertical: Bool = false) -> Controller {
        Controller(Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                  learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(true), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(vertical), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore()))
    }

    /// zhen-inline section 1.3f: while an English run is open the core's run range gets the thick underline (the rest of the marked
    /// text stays single) and the hint is on the panel; once the run ends both are gone.
    func testEnglishRunHasThickUnderlineAndTheHint() {
        let c = englishController()
        c.session.activate()
        let single = NSUnderlineStyle.single.rawValue, thick = NSUnderlineStyle.thick.rawValue
        c.type("su3")  // ㄋㄧˇ, one Chinese character before the run
        XCTAssertEqual(c.client.underlineRuns, [.init(range: NSRange(location: 0, length: 1), style: single)])
        XCTAssertNil(c.panel.hint, "no run, no hint")
        c.type("w", flags: .shift)
        c.type("indows")
        XCTAssertEqual(c.client.marked, "你Windows")
        XCTAssertEqual(c.client.underlineRuns, [.init(range: NSRange(location: 0, length: 1), style: single),
                                                .init(range: NSRange(location: 1, length: 7), style: thick)],
                       "thick over the run, single before it")
        XCTAssertEqual(c.panel.hint, "空白：英文斷開")
        XCTAssertTrue(c.panel.visible)
        XCTAssertEqual(c.panel.vertical, 0, "horizontal orientation: the bar")
        c.type(" ")  // the run ends
        XCTAssertEqual(c.client.marked, "你Windows")
        XCTAssertEqual(c.client.underlineRuns, [.init(range: NSRange(location: 0, length: 8), style: single)], "all single again")
        XCTAssertNil(c.panel.hint, "the hint goes with the run")
        XCTAssertFalse(c.panel.visible)
    }

    /// The hint is not a candidate: a click on it never reaches the core (`Session.candidateSelected` returns false and the
    /// composition is as it was), where a click on a real candidate does (true control).
    func testClickOnTheHintPicksNothing() {
        let c = englishController()
        c.session.activate()
        c.type("su3 ")  // a candidate window for ㄋㄧˇ
        XCTAssertTrue(c.panel.visible && c.panel.hint == nil)
        XCTAssertTrue(c.session.candidateSelected(at: 0), "control: a click on a candidate is a pick")
        c.type("w", flags: .shift)
        c.type("in")
        XCTAssertNotNil(c.panel.hint)
        let marked = c.client.marked, calls = c.client.calls
        c.panel.click(0)
        XCTAssertFalse(c.session.candidateSelected(at: 0), "the hint is on the panel: nothing is sent to the core")
        XCTAssertEqual(c.client.marked, marked)
        XCTAssertEqual(c.client.calls, calls, "the client was not touched")
        XCTAssertNotNil(c.panel.hint)
        c.type(" ")  // the run ends, the hint goes: a click is a pick again
        XCTAssertNil(c.panel.hint)
        c.type("su3 ")
        XCTAssertNil(c.panel.hint)
        XCTAssertTrue(c.session.candidateSelected(at: 0), "after the hint, candidates are clickable again")
    }

    /// The hint is anchored at the run's start and the client is asked for the line only once per run (a synchronous IPC on the
    /// key path), not at every letter; the next run asks again.
    func testHintAsksTheClientForTheLineOnlyOncePerRun() {
        let c = englishController()
        c.session.activate()
        c.type("su3")
        c.type("w", flags: .shift)
        let asked = c.client.lineAsks
        XCTAssertEqual(c.client.lineCursor, 1, "anchored at the run's start (after the one Chinese character)")
        c.type("indows")
        XCTAssertEqual(c.client.lineAsks, asked, "six more letters, no new question")
        XCTAssertEqual(c.client.lineCursor, 1)
        XCTAssertEqual(c.panel.lineRect, c.client.line)
        c.type(" ")
        c.type("w", flags: .shift)
        XCTAssertEqual(c.client.lineAsks, asked + 1, "another run asks again")
        XCTAssertEqual(c.client.lineCursor, 8, "at the new run's start")
    }

    /// With the vertical orientation chosen the hint is drawn like the vertical prediction row (kind 2), not the horizontal bar,
    /// and it is not a vertical candidate window (Page Up / Down stay out of the core).
    func testHintFollowsTheVerticalOrientation() {
        let c = englishController(vertical: true)
        c.session.activate()
        c.type("w", flags: .shift)
        XCTAssertEqual(c.panel.hint, "空白：英文斷開")
        XCTAssertEqual(c.panel.vertical, VerticalLayout.predictions)
        XCTAssertFalse(c.session.shell.verticalOpen)
    }

    /// zhen-mixed section 3.2 / 5.3: "中英混打（實驗）" sits after "即時預測" and before "語意校正", is unchecked by default, is stored,
    /// makes `rust` and Space open the English window (the list is eval/golden/english-tiny.txt), is sent again to every engine a
    /// rebuild makes (the word pack switch and a layout switch rebuild it), and a new shell reads the stored on.
    func testEnglishMenuItemTogglesStoresRebuildsAndSurvivesRestart() {
        let store = MemoryEnglishStore(false)  // saved off; the default is on since digits.md A.3.7
        let make = {
            Shell(resources: self.resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                  learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: store, acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        }
        let c = Controller(make())
        c.session.activate()
        let item = { c.session.menu.first { $0.action == .toggleEnglish } }
        let titles = c.session.menu.map(\.title)
        // After 即時預測 and before 語意校正 (here with 可省略韻母 shown, it is between; the app hides it, HiddenAbbreviationTests).
        let at = try! XCTUnwrap(titles.firstIndex(of: "中英混打（實驗）"))
        XCTAssertGreaterThan(at, try! XCTUnwrap(titles.firstIndex(of: "即時預測")))
        XCTAssertEqual(titles.firstIndex(of: "語意校正"), at + 1)
        XCTAssertEqual(item()?.checked, false, "saved off")
        XCTAssertEqual(item()?.enabled, true, "the list loaded")
        let window = { (c: Controller) -> [String] in c.type("rust "); return c.panel.items }
        XCTAssertEqual(window(c), [], "off: Space is rule 10, and ㄔㄧ is not a syllable")
        XCTAssertEqual(c.client.marked, "ㄔㄧ")
        c.press(Keys.esc)
        c.session.perform(.toggleEnglish)
        XCTAssertEqual(store.english, true)
        XCTAssertEqual(item()?.checked, true)
        XCTAssertEqual(window(c), ["rust", "Rust", "RUST"], "on: the English window")
        c.type("1")
        XCTAssertEqual(c.client.text, "", "choosing the English item sends nothing (zhen-inline section 1.0)")
        XCTAssertEqual(c.client.marked, "rust", "the word stays in the composition")
        c.press(Keys.enter)
        XCTAssertEqual(c.client.text, "rust", "Enter sends it, no space")
        XCTAssertEqual(c.client.marked, "")
        c.session.perform(.toggleAcgPack)  // rebuilds the engine, whose default is off
        c.session.perform(.toggleAcgPack)
        XCTAssertEqual(window(c), ["rust", "Rust", "RUST"], "the rebuilt engine got the setting")
        c.press(Keys.esc)
        c.press(Keys.esc)
        c.session.perform(.layout(.eten))  // a layout switch rebuilds it too
        c.session.perform(.layout(.standard))
        XCTAssertEqual(window(c), ["rust", "Rust", "RUST"], "the engine rebuilt by a layout switch got the setting")
        c.press(Keys.esc)
        c.press(Keys.esc)
        c.session.perform(.toggleEnglish)  // off while a window could be open: nothing opens
        XCTAssertEqual(window(c), [])
        c.press(Keys.esc)
        let again = Controller(make())
        again.session.activate()
        XCTAssertEqual(store.english, false)
        store.english = true
        XCTAssertEqual(window(Controller(make())), ["rust", "Rust", "RUST"], "a new shell reads the stored on")
        _ = again
    }

    /// zhen-inline section 2 / 4.3: the display choice defaults to "as typed" (a store with no value is true), shows the letters while
    /// the English is typed and the Zhuyin when it is "on Space", is stored, and is sent again to every engine a rebuild makes (the
    /// core's own default is true, so the rebuilt engine would show letters if the shell did not send false).
    func testEnglishInlineDefaultStoredAndSentToEveryEngine() {
        let store = MemoryEnglishStore(true)
        let make = {
            Shell(resources: self.resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                  learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: store, acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        }
        XCTAssertNil(store.englishInline, "never chosen")
        let shell = make()
        XCTAssertTrue(shell.englishInlineOn, "no value is true")
        let c = Controller(shell)
        c.session.activate()
        c.type("rus")
        XCTAssertEqual(c.client.marked, "rus", "as typed")
        c.press(Keys.esc)
        shell.applyEnglishInline(false)
        XCTAssertEqual(store.englishInline, false)
        c.type("rus")
        XCTAssertEqual(c.client.marked, "ㄋㄧ", "on Space: the Zhuyin")
        c.press(Keys.esc)
        c.session.perform(.toggleAcgPack)  // rebuilds the engine
        c.session.perform(.toggleAcgPack)
        c.type("rus")
        XCTAssertEqual(c.client.marked, "ㄋㄧ", "the rebuilt engine got the choice")
        c.press(Keys.esc)
        c.session.perform(.layout(.eten))
        c.session.perform(.layout(.standard))
        c.type("rus")
        XCTAssertEqual(c.client.marked, "ㄋㄧ", "and the one rebuilt by a layout switch")
        c.press(Keys.esc)
        // A new shell reads the stored choice.
        let again = Controller(make())
        again.session.activate()
        again.type("rus")
        XCTAssertEqual(again.client.marked, "ㄋㄧ", "a new shell reads the stored false")
        again.press(Keys.esc)
        store.englishInline = true
        let third = Controller(make())
        third.session.activate()
        third.type("rus")
        XCTAssertEqual(third.client.marked, "rus", "and the stored true")
    }

    /// zhen-keyorder section 3 / 5.3: the key-order choice defaults to false (a store with no value), is stored, takes effect (`c u p` and
    /// Space opens the English window only with it, `ㄏㄧㄣ` is not a syllable), is sent again to every engine a rebuild makes (the core's
    /// default is false, so a rebuilt engine would lose it if the shell did not send true), and closes an English window it opened when
    /// it is turned off.
    func testEnglishKeyOrderDefaultStoredAndSentToEveryEngine() {
        let store = MemoryEnglishStore(true)
        let make = {
            Shell(resources: self.resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                  learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: store, acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        }
        let window = { (c: Controller) -> [String] in c.type("cup "); return c.panel.items }
        XCTAssertNil(store.englishKeyOrder, "never chosen")
        let shell = make()
        XCTAssertFalse(shell.englishKeyOrderOn, "no value is false")
        let c = Controller(shell)
        c.session.activate()
        XCTAssertEqual(window(c), [], "off: Space does nothing, as before")
        c.press(Keys.esc)
        shell.applyEnglishKeyOrder(true)
        XCTAssertEqual(store.englishKeyOrder, true)
        XCTAssertEqual(window(c), ["cup", "Cup", "CUP"], "on: the window opens without an overwrite")
        shell.applyEnglishKeyOrder(false)
        XCTAssertEqual(c.panel.items, [], "turning it off closes the window")
        XCTAssertEqual(c.client.marked, "ㄏㄧㄣ", "the unfinished syllable stays")
        c.press(Keys.esc)
        shell.applyEnglishKeyOrder(true)
        c.session.perform(.toggleAcgPack)  // rebuilds the engine
        c.session.perform(.toggleAcgPack)
        XCTAssertEqual(window(c), ["cup", "Cup", "CUP"], "the rebuilt engine got the choice")
        c.press(Keys.esc)
        c.session.perform(.layout(.eten))
        c.session.perform(.layout(.standard))
        XCTAssertEqual(window(c), ["cup", "Cup", "CUP"], "and the one rebuilt by a layout switch")
        c.press(Keys.esc)
        // A new shell reads the stored choice; with the stored false it does not open.
        let again = Controller(make())
        again.session.activate()
        XCTAssertEqual(window(again), ["cup", "Cup", "CUP"], "a new shell reads the stored true")
        again.press(Keys.esc)
        store.englishKeyOrder = false
        let third = Controller(make())
        third.session.activate()
        XCTAssertEqual(window(third), [], "and the stored false")
    }

    /// zhen-inline section 2: the 英文的顯示 choice goes through the session that owns the composition, like the other apply*, and the
    /// snapshot redraws what is on screen: letters typed, the choice turned to "on Space", the marked text is the Zhuyin at once, and
    /// back.
    func testEnglishInlineChoiceRedrawsAComposition() {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(true), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        let c = Controller(shell)
        c.session.activate()
        c.type("rus")
        XCTAssertEqual(c.client.marked, "rus")
        XCTAssertTrue(shell.owner === c.session, "premise: the session owns the composition")
        shell.applyEnglishInline(false)
        XCTAssertEqual(c.client.marked, "ㄋㄧ", "the composition on screen is drawn again from the snapshot")
        shell.applyEnglishInline(true)
        XCTAssertEqual(c.client.marked, "rus")
    }

    /// zhen-mixed section 3.2 / 5.3: a word list that is missing or refused (non-zero from `load_english`) makes `englishAvailable`
    /// false and greys the item; the stored choice still shows, and the switch does nothing.
    func testEnglishListThatDoesNotLoadGreysTheItem() throws {
        let others = Set(TestData.files.map(\.name)).subtracting(["english-words.txt"])
        let make = { (dir: URL) -> Shell in
            Shell(resources: dir, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                  learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(true), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        }
        let missing = try XCTUnwrap(TestData.resources(only: others))
        let refused = try XCTUnwrap(TestData.resources(only: others))
        try "AB\n".write(to: refused.appendingPathComponent("english-words.txt"), atomically: true, encoding: .utf8)
        for dir in [missing, refused] {
            let shell = make(dir)
            XCTAssertNotNil(shell.engine, "the engine itself is fine")
            XCTAssertFalse(shell.englishAvailable)
            let c = Controller(shell)
            c.session.activate()
            let entry = c.session.menu.first { $0.action == .toggleEnglish }
            XCTAssertEqual(entry?.enabled, false)
            XCTAssertEqual(entry?.checked, true, "greyed, still showing the setting")
            c.type("rust ")
            XCTAssertFalse(c.panel.visible, "the switch is on but there is no list")
            XCTAssertEqual(c.client.marked, "ㄔㄧ")
        }
        XCTAssertTrue(make(try XCTUnwrap(TestData.resources())).englishAvailable)
    }

    /// acg-pack (docs/contracts/acg-pack.md A.2): the "動漫與遊戲詞" switch is on by default (user decision 2026-10-09), a toggle rebuilds the engine
    /// without the pack (the fixture pack spells 榊遊矢, which the base lexicon cannot: it gives 神遊使), the choice is stored, and a new shell
    /// reads it. On again: back to the engine with the pack.
    func testAcgPackMenuItemTogglesStoresAndSurvivesRestart() throws {
        let packs = resources.appendingPathComponent("packs")
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        try "ㄕㄣˊ-ㄧㄡˊ-ㄕˇ\t榊遊矢\t-7.04116568\tacg\n".write(to: packs.appendingPathComponent("acg-add.tsv"), atomically: true, encoding: .utf8)
        let store = MemoryAcgPackStore()
        let make = {
            Shell(resources: self.resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                  learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: store, quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        }
        let name = "gp6u.6g3"  // ㄕㄣˊ ㄧㄡˊ ㄕˇ; both profiles spell it 神遊使 without the pack (measured with the CLI, 2026-10-27)
        let c = Controller(make(), bundle: "com.hnc.Discord")
        c.session.activate()
        let line = { c.session.menu.first { $0.title.hasPrefix("已開啟的詞包") }?.title }
        XCTAssertNil(c.session.menu.first { $0.action == .toggleAcgPack }, "it-pack 3.2: no switch in the menu")
        XCTAssertEqual(line(), "已開啟的詞包：動漫與遊戲詞", "default on; the IT switch is on too but this fixture has no it-add.tsv, so it is not loaded")
        c.type(name)
        XCTAssertEqual(c.client.marked, "榊遊矢")
        c.press(Keys.esc)
        c.session.perform(.toggleAcgPack)
        XCTAssertEqual(store.acgPack, false)
        XCTAssertNil(line(), "no pack loaded, no line")
        c.type(name)
        XCTAssertNotEqual(c.client.marked, "榊遊矢")
        c.press(Keys.esc)
        let again = Controller(make(), bundle: "com.hnc.Discord")
        again.session.activate()
        again.type(name)
        XCTAssertNotEqual(again.client.marked, "榊遊矢", "a new shell reads the stored off")
        again.press(Keys.esc)
        again.session.perform(.toggleAcgPack)
        XCTAssertEqual(store.acgPack, true)
        again.type(name)
        XCTAssertEqual(again.client.marked, "榊遊矢")
    }

    /// it-pack contract 3.2: the "資訊與 AI 詞" switch is on when `itPack` was never set (the maskless default is on), a toggle rebuilds the engine without
    /// the pack, the choice is stored under `itPack`, and a new shell reads it. The ACG switch is independent. The fixture pack spells 螢幕保護器, which the
    /// base lexicon cannot (both profiles give 螢幕保護氣 without the pack, measured with the CLI 2026-10-27; `TestData.itPackRow`).
    func testItPackMenuItemTogglesStoresAndSurvivesRestart() throws {
        let packs = resources.appendingPathComponent("packs")
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        try TestData.itPackRow.write(to: packs.appendingPathComponent("it-add.tsv"), atomically: true, encoding: .utf8)
        let store = MemoryItPackStore()
        let make = {
            Shell(resources: self.resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                  learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(), itPackStore: store)
        }
        let name = "u/6aj41l3cj4fu4"  // ㄧㄥˊ ㄇㄨˋ ㄅㄠˇ ㄏㄨˋ ㄑㄧˋ (a space is the first tone)
        let c = Controller(make(), bundle: "com.hnc.Discord")
        c.session.activate()
        let line = { c.session.menu.first { $0.title.hasPrefix("已開啟的詞包") }?.title }
        XCTAssertNil(c.session.menu.first { $0.action == .toggleItPack }, "it-pack 3.2: no switch in the menu")
        XCTAssertEqual(line(), "已開啟的詞包：資訊與 AI 詞", "default on; this fixture has no acg-add.tsv")
        c.type(name)
        XCTAssertEqual(c.client.marked, "螢幕保護器")
        c.press(Keys.esc)
        c.session.perform(.toggleItPack)
        XCTAssertEqual(store.itPack, false)
        XCTAssertNil(line(), "no pack loaded, no line")
        c.type(name)
        XCTAssertNotEqual(c.client.marked, "螢幕保護器")
        c.press(Keys.esc)
        let again = Controller(make(), bundle: "com.hnc.Discord")
        again.session.activate()
        again.type(name)
        XCTAssertNotEqual(again.client.marked, "螢幕保護器", "a new shell reads the stored off")
        again.press(Keys.esc)
        again.session.perform(.toggleItPack)
        XCTAssertEqual(store.itPack, true)
        again.type(name)
        XCTAssertEqual(again.client.marked, "螢幕保護器")
    }

    /// acg-pack A2.4: the grey line under "動漫與遊戲詞" says how old the data is. The date is the manifest's `latest_source_revision` as a UTC date
    /// (23:59:59Z must not become the next day in the local zone), the entry has no action, it stays with the pack off, and a missing file or
    /// field means no line.
    func testAcgDataDateLineComesFromTheManifest() throws {
        let packs = resources.appendingPathComponent("packs")
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        let manifest = packs.appendingPathComponent("acg.json")
        let make = { (store: MemoryAcgPackStore) in
            Controller(Shell(resources: self.resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                             learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: store, quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore()))
        }
        let titles = { (c: Controller) in c.session.menu.map(\.title) }
        XCTAssertNil(make(MemoryAcgPackStore()).session.shell.acgDataDate, "no manifest, no line")
        try #"{"latest_source_revision": "2026-10-06T23:59:59Z", "version": "x"}"#.write(to: manifest, atomically: true, encoding: .utf8)
        let on = make(MemoryAcgPackStore())
        let line = "資料更新至 2026-10-06（維基百科）"
        // it-pack 3.2: the date line moved to Settings (SettingsTests); the menu has one non-clickable pack line instead.
        // No pack file in this fixture, so no 已開啟的詞包 line either (testPackLineNamesTheLoadedPacks covers it).
        XCTAssertEqual(titles(on), ["標準鍵盤", "倚天鍵盤", "避免把敏感字詞排在前面", "即時預測", "可省略韻母", "中英混打（實驗）", "語意校正", "這個 App 用聊天設定", "善解設定…", "清除選字記憶…", "不要備份選字記憶"])
        XCTAssertFalse(titles(on).contains(line), "the date is not in the menu")
        XCTAssertEqual(on.session.shell.acgDataDate.map(MenuEntry.Text.acgDataDate), line, "the date Settings shows")
        try #"{"version": "x"}"#.write(to: manifest, atomically: true, encoding: .utf8)
        XCTAssertNil(make(MemoryAcgPackStore()).session.shell.acgDataDate, "no field, no line")
        try "not json".write(to: manifest, atomically: true, encoding: .utf8)
        XCTAssertNil(make(MemoryAcgPackStore()).session.shell.acgDataDate, "unreadable, no line")
    }

    /// A pack that fails to load (default on) must not leave the user with no engine: the shell falls back to
    /// building without the pack, keeps the stored choice, and typing works.
    func testCorruptPackFallsBackToAnEngineWithoutIt() throws {
        let packs = resources.appendingPathComponent("packs")
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        try "not a row\n".write(to: packs.appendingPathComponent("acg-add.tsv"), atomically: true, encoding: .utf8)
        XCTAssertNotEqual(CoreEngine.make(dataDir: resources.path, layout: 0, packsDir: packs.path).1, 0, "the fixture is a pack that cannot load")
        let store = MemoryAcgPackStore()
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: store, quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        XCTAssertNil(store.acgPack, "the preference is left as it was")
        let c = Controller(shell)
        c.session.activate()
        XCTAssertNil(c.session.menu.first { $0.title.hasPrefix("已開啟的詞包") }, "it-pack 3.2: the fall-back engine has no pack, so the menu names none")
        XCTAssertEqual(c.type("su3cl3"), Array(repeating: true, count: 6))
        XCTAssertEqual(c.client.marked, "你好")
    }

    /// quote-pack.md section 2: with both packs on, a broken file costs only its own pack. Each case breaks one file and gives the
    /// other a row (score 0) that wins 「ㄋㄧˇ ㄏㄠˇ」, so the pick shows which pack the fallback engine kept.
    func testWithBothPacksOnABrokenFileKeepsTheOtherPack() throws {
        let packs = resources.appendingPathComponent("packs")
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        for (broken, kept, word) in [("acg-add.tsv", "quotes-add.tsv", "擬好"), ("quotes-add.tsv", "acg-add.tsv", "泥好")] {
            try "not a row\n".write(to: packs.appendingPathComponent(broken), atomically: true, encoding: .utf8)
            try "ㄋㄧˇ-ㄏㄠˇ\t\(word)\t0.0\tx\n".write(to: packs.appendingPathComponent(kept), atomically: true, encoding: .utf8)
            let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                              learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(true), quotePackStore: MemoryQuotePackStore(true), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
            XCTAssertNotNil(shell.engine, broken)
            let c = Controller(shell)
            c.session.activate()
            c.type("su3cl3")
            XCTAssertEqual(c.client.marked, word, "\(broken) broken: \(kept) is still loaded")
        }
    }

    private func packShell(acg: MemoryAcgPackStore, it: MemoryItPackStore) -> Shell {
        Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
              learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: acg, quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(), itPackStore: it)
    }

    /// it-pack contract 3.2: the menu has one non-clickable line naming the packs the engine loaded, in the settings order; none loaded, no line.
    func testPackLineNamesTheLoadedPacks() throws {
        let packs = resources.appendingPathComponent("packs")
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        try "ㄉㄧㄥˋ-ㄩㄢˊ-ㄊㄤˊ\t碇源堂\t-7.04116568\tacg\n".write(to: packs.appendingPathComponent("acg-add.tsv"), atomically: true, encoding: .utf8)
        try TestData.itPackRow.write(to: packs.appendingPathComponent("it-add.tsv"), atomically: true, encoding: .utf8)
        let line = { (acg: Bool, it: Bool) -> MenuEntry? in
            let c = Controller(self.packShell(acg: MemoryAcgPackStore(acg), it: MemoryItPackStore(it)))
            return c.session.menu.first { $0.title.hasPrefix("已開啟的詞包") }
        }
        XCTAssertEqual(line(true, true)?.title, "已開啟的詞包：動漫與遊戲詞、資訊與 AI 詞")
        XCTAssertEqual(line(true, false)?.title, "已開啟的詞包：動漫與遊戲詞")
        XCTAssertEqual(line(false, true)?.title, "已開啟的詞包：資訊與 AI 詞")
        XCTAssertNil(line(false, false), "no pack, no line")
        XCTAssertNil(line(true, true)?.action, "not clickable")
    }

    /// it-pack contract 3.2: a broken it-add.tsv fails the pack build; the retry drops that pack, and this fixture has no acg-add.tsv either, so
    /// neither pack is loaded and the menu names none, while both stored choices (what Settings shows) stay on and are not rewritten. Fails if the line reads acgPackOn / itPackOn.
    func testBrokenItPackDropsBothPacksFromTheMenuLine() throws {
        let packs = resources.appendingPathComponent("packs")
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        try "not a row\n".write(to: packs.appendingPathComponent("it-add.tsv"), atomically: true, encoding: .utf8)
        let acg = MemoryAcgPackStore(true), it = MemoryItPackStore(true)
        let shell = packShell(acg: acg, it: it)
        XCTAssertNotNil(shell.engine, "the pack-less retry built an engine")
        XCTAssertEqual(shell.loadedPackMask, 0)
        let c = Controller(shell)
        XCTAssertNil(c.session.menu.first { $0.title.hasPrefix("已開啟的詞包") })
        XCTAssertTrue(shell.acgPackOn && shell.itPackOn, "Settings keeps showing the stored choices")
        XCTAssertEqual(acg.acgPack, true)
        XCTAssertEqual(it.itPack, true)
    }

    private func packShell3(quote: Bool) -> Shell {
        Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
              learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(true), quotePackStore: MemoryQuotePackStore(quote), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(), itPackStore: MemoryItPackStore(true))
    }

    /// Fall-back order when the combined build fails: with all three packs on (mask 7) and only quotes-add.tsv broken, the no-pack build works
    /// (so a pack is at fault), then dropping one pack at a time keeps the healthy ones: ACG and IT stay loaded (mask 5), the menu names both.
    func testBrokenQuotesFileKeepsAcgAndIt() throws {
        let packs = resources.appendingPathComponent("packs")
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        try "ㄉㄧㄥˋ-ㄩㄢˊ-ㄊㄤˊ\t碇源堂\t-7.04116568\tacg\n".write(to: packs.appendingPathComponent("acg-add.tsv"), atomically: true, encoding: .utf8)
        try TestData.itPackRow.write(to: packs.appendingPathComponent("it-add.tsv"), atomically: true, encoding: .utf8)
        try "not a row\n".write(to: packs.appendingPathComponent("quotes-add.tsv"), atomically: true, encoding: .utf8)
        let shell = packShell3(quote: true)
        XCTAssertNotNil(shell.engine)
        XCTAssertEqual(shell.packMask, 7)
        XCTAssertEqual(shell.loadedPackMask, 5, "only the broken pack is dropped")
        XCTAssertEqual(shell.engineBuilds, 4, "combined, no packs, drop ACG (still broken), drop quotes (works)")
        let c = Controller(shell)
        XCTAssertEqual(c.session.menu.first { $0.title.hasPrefix("已開啟的詞包") }?.title, "已開啟的詞包：動漫與遊戲詞、資訊與 AI 詞")
        XCTAssertTrue(shell.quotePackOn, "the stored choice is untouched")
    }

    /// A build failure that is not a pack's (a broken overlay-add.tsv in the data directory): the combined build and the no-pack build both
    /// fail, and the shell gives up after exactly those two builds, with no engine.
    func testBrokenNonPackFileStopsAfterTwoBuilds() throws {
        let overlay = resources.appendingPathComponent("overlay-add.tsv")
        try FileManager.default.removeItem(at: overlay)
        try "not a row\n".write(to: overlay, atomically: true, encoding: .utf8)
        let shell = packShell3(quote: true)
        XCTAssertNil(shell.engine)
        XCTAssertEqual(shell.engineBuilds, 2)
    }

    func testSwitchingLayoutCommitsThenUsesTheNewLayout() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3")
        c.session.selectLayout(.eten)
        XCTAssertEqual(c.client.text, "你")
        XCTAssertEqual(c.client.marked, "")
        c.type("ne3hz3")
        c.press(Keys.enter)
        XCTAssertEqual(c.client.text, "你你好")
        c.session.selectLayout(.standard)
        c.type("su3cl3")
        c.press(Keys.enter)
        XCTAssertEqual(c.client.text, "你你好你好")
        // The rebuilt engine keeps the profile (TextEdit: formal).
        c.type(Row131.standardKeys)
        c.press(Keys.enter)
        XCTAssertTrue(c.client.text.hasSuffix(Row131.formal))
    }
}
