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
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { secure }, layoutStore: store, learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore())
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

    func testLineRectReachesThePanel() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3 ")
        XCTAssertEqual(c.panel.lineRect, c.client.line)
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
        XCTAssertEqual(c.client.lineAsks, 1)
        XCTAssertTrue(c.press(125))  // ↓ expands
        XCTAssertTrue(c.press(125))  // ↓ one row
        XCTAssertTrue(c.press(124))  // → one cell
        XCTAssertEqual(c.client.lineAsks, 1, "selection moves must not ask the client again")
        XCTAssertEqual(c.panel.lineRect, c.client.line)
        c.type("cl3 ")
        XCTAssertGreaterThan(c.client.lineAsks, 1, "a new composition asks again")
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
        let shell = Shell(resources: resources.appendingPathComponent("missing"), panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore())
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
        discord.type(Row10.standardKeys)
        discord.press(Keys.enter)
        let textEdit = Controller(shell, bundle: "com.apple.TextEdit")
        textEdit.session.activate()
        textEdit.type(Row10.standardKeys)
        textEdit.press(Keys.enter)
        XCTAssertEqual(discord.client.text, Row10.chat)
        XCTAssertEqual(textEdit.client.text, Row10.formal)
        XCTAssertNotEqual(discord.client.text, textEdit.client.text)
        XCTAssertEqual(Row10.standardKeys, Selftest.row10Standard)
    }

    func testEveryChatAppGetsTheChatProfile() {
        let shell = makeShell()
        for app in Shell.chatApps.sorted() + ["com.apple.TextEdit", "com.apple.Notes"] {
            let c = Controller(shell, bundle: app)
            c.session.activate()
            c.type(Row10.standardKeys)
            c.press(Keys.enter)
            XCTAssertEqual(c.client.text, Shell.chatApps.contains(app) ? Row10.chat : Row10.formal)
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
        b.type("c")  // ㄏ
        XCTAssertEqual(a.client.text, "你")
        XCTAssertEqual(a.client.marked, "")
        XCTAssertFalse(a.panel.visible)
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
        b.type(Row10.standardKeys)
        b.press(Keys.enter)
        XCTAssertEqual(b.client.text, Row10.formal)
        XCTAssertEqual(a.client.text, "你")
        // S2h §11: without a left context, as before S2h; 你 as history would make chat pick 期中 like formal.
        a.client.selectedOverride = NSRange(location: NSNotFound, length: 0)
        a.type(Row10.standardKeys)  // and back: Discord's chat profile again
        a.press(Keys.enter)
        XCTAssertEqual(a.client.text, "你" + Row10.chat)
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
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: store)
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
                                     learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: store))
        again.session.activate()
        again.type(report)
        XCTAssertEqual(again.client.marked, "睪丸這波", "a new shell reads the stored off")
        again.press(Keys.esc)
        again.session.perform(.toggleDemote)
        XCTAssertEqual(store.demote, true)
        again.type(report)
        XCTAssertEqual(again.client.marked, "搞完這波")
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
        c.type(Row10.standardKeys)
        c.press(Keys.enter)
        XCTAssertTrue(c.client.text.hasSuffix(Row10.formal))
    }
}
