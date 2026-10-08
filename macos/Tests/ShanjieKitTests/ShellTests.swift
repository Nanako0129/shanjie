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

    private func makeShell(secure: Bool = false, store: LayoutStore = MemoryLayoutStore()) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { secure }, layoutStore: store, learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), acgPackStore: MemoryAcgPackStore())
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
        XCTAssertTrue(before.cells.allSatisfy { !$0.showsNumber }, "no number on the not-entered row")
        XCTAssertTrue(c.press(48))  // Tab
        XCTAssertEqual(c.panel.selected, 0)
        let entered = CandidateCells()
        let after = entered.update(candidates: c.panel.items, notes: c.panel.items.map { _ in nil },
                                   selected: c.panel.selected, first: c.panel.first, columns: c.panel.columns)
        XCTAssertTrue(after.cells.allSatisfy(\.showsNumber), "numbers show once entered")
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
        let c = Controller(makeShell())
        c.session.activate()
        XCTAssertEqual(c.type("3"), [true])
        XCTAssertEqual(c.client.marked, "ˇ")
        XCTAssertEqual(c.client.text, "")
        _ = c.session.handle(Keys.event(36))  // Return
        XCTAssertEqual(c.client.text, "ˇ")
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
        let shell = Shell(resources: resources.appendingPathComponent("missing"), panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), acgPackStore: MemoryAcgPackStore())
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
        discord.type(Row226.standardKeys)
        discord.press(Keys.enter)
        let textEdit = Controller(shell, bundle: "com.apple.TextEdit")
        textEdit.session.activate()
        textEdit.type(Row226.standardKeys)
        textEdit.press(Keys.enter)
        XCTAssertEqual(discord.client.text, Row226.chat)
        XCTAssertEqual(textEdit.client.text, Row226.formal)
        XCTAssertNotEqual(discord.client.text, textEdit.client.text)
        XCTAssertEqual(Row226.standardKeys, Selftest.row226Standard)
    }

    func testEveryChatAppGetsTheChatProfile() {
        let shell = makeShell()
        for app in Shell.chatApps.sorted() + ["com.apple.TextEdit", "com.apple.Notes"] {
            let c = Controller(shell, bundle: app)
            c.session.activate()
            c.type(Row226.standardKeys)
            c.press(Keys.enter)
            XCTAssertEqual(c.client.text, Shell.chatApps.contains(app) ? Row226.chat : Row226.formal)
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
        b.press(Keys.enter)
        b.press(Keys.enter)
        XCTAssertEqual(b.client.text, "你")
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
        b.type(Row226.standardKeys)
        b.press(Keys.enter)
        XCTAssertEqual(b.client.text, Row226.formal)
        XCTAssertEqual(a.client.text, "你")
        // S2h §11: without a left context, as before S2h; 你 as history would make chat pick 期中 like formal.
        a.client.selectedOverride = NSRange(location: NSNotFound, length: 0)
        a.type(Row226.standardKeys)  // and back: Discord's chat profile again
        a.press(Keys.enter)
        XCTAssertEqual(a.client.text, "你" + Row226.chat)
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
    /// core's setting (the user report ㄍㄠˇ ㄨㄢˊ ㄓㄜˋ ㄅㄛ), is stored, and a new shell reads the stored
    /// choice (a stored off survives a restart and a layout switch rebuild).
    func testDemoteMenuItemTogglesStoresAndSurvivesRestart() {
        let report = Layouts.keys("ㄍㄠˇ ㄨㄢˊ ㄓㄜˋ ㄅㄛ", eten: false)
        let store = MemoryDemoteStore()
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: store, predictionStore: MemoryPredictionStore(), acgPackStore: MemoryAcgPackStore())
        let c = Controller(shell)
        c.session.activate()
        let item = { c.session.menu.first { $0.action == .toggleDemote } }
        XCTAssertEqual(item()?.title, "避免把敏感字詞排在前面")
        XCTAssertEqual(item()?.checked, true, "default on")
        c.type(report)
        XCTAssertEqual(c.client.marked, "搞完這波")
        c.session.perform(.toggleDemote)          // mid-composition: the snapshot is shown at once
        XCTAssertEqual(store.demote, false)
        XCTAssertEqual(item()?.checked, false)
        XCTAssertEqual(c.client.marked, "睪丸這波")
        c.session.perform(.toggleDemote)
        XCTAssertEqual(c.client.marked, "搞完這波")
        c.session.perform(.toggleDemote)
        XCTAssertEqual(c.client.marked, "睪丸這波")
        c.press(Keys.esc)
        c.type(report)
        XCTAssertEqual(c.client.marked, "睪丸這波")
        c.press(Keys.esc)
        c.session.selectLayout(.eten)      // the rebuilt engine gets the stored setting too
        c.session.selectLayout(.standard)
        c.type(report)
        XCTAssertEqual(c.client.marked, "睪丸這波")
        c.press(Keys.esc)
        let again = Controller(Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                                     learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: store, predictionStore: MemoryPredictionStore(), acgPackStore: MemoryAcgPackStore()))
        again.session.activate()
        again.type(report)
        XCTAssertEqual(again.client.marked, "睪丸這波", "a new shell reads the stored off")
        again.press(Keys.esc)
        again.session.perform(.toggleDemote)
        XCTAssertEqual(store.demote, true)
        again.type(report)
        XCTAssertEqual(again.client.marked, "搞完這波")
    }

    /// V3 (docs/contracts/v3-engine.md section 10.5): the "即時預測" item is checked by default, switches the row off
    /// and on (shown at once mid-composition), is stored, and a new shell reads the stored off.
    func testPredictionMenuItemTogglesStoresAndSurvivesRestart() {
        let store = MemoryPredictionStore()
        let make = {
            Shell(resources: self.resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                  learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: store, acgPackStore: MemoryAcgPackStore())
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

    /// acg-pack (docs/contracts/acg-pack.md A.2): the "動漫與遊戲詞" item is checked by default (user decision 2026-10-09), a toggle rebuilds the engine
    /// without the pack (the fixture pack spells 碇源堂, which the base lexicon cannot), the choice is stored, and a new shell
    /// reads it. On again: back to the engine with the pack.
    func testAcgPackMenuItemTogglesStoresAndSurvivesRestart() throws {
        let packs = resources.appendingPathComponent("packs")
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        try "ㄉㄧㄥˋ-ㄩㄢˊ-ㄊㄤˊ\t碇源堂\t-7.04116568\tacg\n".write(to: packs.appendingPathComponent("acg-add.tsv"), atomically: true, encoding: .utf8)
        let store = MemoryAcgPackStore()
        let make = {
            Shell(resources: self.resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                  learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), acgPackStore: store)
        }
        let name = "2u/4m06w;6"  // ㄉㄧㄥˋ ㄩㄢˊ ㄊㄤˊ; the chat profile spells it 定元堂 without the pack (formal already gets it right)
        let c = Controller(make(), bundle: "com.hnc.Discord")
        c.session.activate()
        let item = { c.session.menu.first { $0.action == .toggleAcgPack } }
        XCTAssertEqual(item()?.title, "動漫與遊戲詞")
        XCTAssertEqual(item()?.checked, true, "default on")
        c.type(name)
        XCTAssertEqual(c.client.marked, "碇源堂")
        c.press(Keys.esc)
        c.session.perform(.toggleAcgPack)
        XCTAssertEqual(store.acgPack, false)
        XCTAssertEqual(item()?.checked, false)
        c.type(name)
        XCTAssertNotEqual(c.client.marked, "碇源堂")
        c.press(Keys.esc)
        let again = Controller(make(), bundle: "com.hnc.Discord")
        again.session.activate()
        again.type(name)
        XCTAssertNotEqual(again.client.marked, "碇源堂", "a new shell reads the stored off")
        again.press(Keys.esc)
        again.session.perform(.toggleAcgPack)
        XCTAssertEqual(store.acgPack, true)
        again.type(name)
        XCTAssertEqual(again.client.marked, "碇源堂")
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
        c.type(Row226.standardKeys)
        c.press(Keys.enter)
        XCTAssertTrue(c.client.text.hasSuffix(Row226.formal))
    }
}
