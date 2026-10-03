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

    private func makeShell(secure: Bool = false) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { secure })
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

    func testCandidatesShowThenNumberSelects() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3 ")  // ㄋㄧˇ, then space opens the candidates (rule 15)
        XCTAssertTrue(c.panel.visible)
        XCTAssertTrue((1...9).contains(c.panel.items.count))
        XCTAssertEqual(c.panel.selected, 0)
        XCTAssertEqual(c.session.candidates, c.panel.items)
        let second = c.panel.items[1]
        XCTAssertTrue(c.type("2")[0])
        XCTAssertEqual(c.client.marked, second, "the composition shows the chosen candidate")
        XCTAssertFalse(c.panel.visible)
        XCTAssertEqual(c.session.candidates, [], "the shell's candidate array is cleared")
        XCTAssertEqual(c.client.text, "")
    }

    func testMouseSelectionGoesThroughTheCoreAsANumberKey() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3 ")
        let second = c.panel.items[1]
        c.session.candidateSelected(second)
        XCTAssertEqual(c.client.marked, second)
        XCTAssertFalse(c.panel.visible)
        XCTAssertEqual(c.client.text, "", "never inserted directly")
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
        XCTAssertEqual(c.session.candidates, [])
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
        c.type("3")  // a tone key with nothing pending and an empty composition
        c.press(Keys.enter)
        XCTAssertFalse(c.client.text.contains("你"))
        XCTAssertEqual(c.client.marked, "")
    }

    func testEngineThatCannotBeBuiltPassesEveryKey() {
        let shell = Shell(resources: resources.appendingPathComponent("missing"), panel: FakePanel(), isSecureInput: { false })
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
        XCTAssertEqual(shell.candidates, [])
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
        XCTAssertFalse(b.session.candidates.isEmpty, "and the shared array is intact")
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
        a.type(Row10.standardKeys)  // and back: Discord's chat profile again
        a.press(Keys.enter)
        XCTAssertEqual(a.client.text, "你" + Row10.chat)
    }

    // MARK: input mode

    func testSwitchingModeCommitsThenUsesTheNewLayout() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3")
        c.session.setInputMode("com.nyanako.inputmethod.shanjie.eten")
        XCTAssertEqual(c.client.text, "你")
        XCTAssertEqual(c.client.marked, "")
        c.type("ne3hz3")
        c.press(Keys.enter)
        XCTAssertEqual(c.client.text, "你你好")
        c.session.setInputMode("com.nyanako.inputmethod.shanjie.standard")
        c.type("su3cl3")
        c.press(Keys.enter)
        XCTAssertEqual(c.client.text, "你你好你好")
        // The rebuilt engine keeps the profile (TextEdit: formal).
        c.type(Row10.standardKeys)
        c.press(Keys.enter)
        XCTAssertTrue(c.client.text.hasSuffix(Row10.formal))
    }
}
