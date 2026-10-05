import AppKit
import CShanjie
import XCTest
@testable import ShanjieKit

/// S4 in the shell (docs/contracts/s4-learning.md section 6.11-6.13, shell parts): left context,
/// privacyGate, menu and the learning directory, through the real C core. Learning directories
/// are temporary.
@MainActor
final class LearningTests: XCTestCase {
    private var resources: URL!

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    /// A gate whose secure-input answer a test can flip mid-composition.
    final class Gate { var secure = false }

    private func makeShell(gate: Gate = Gate(), learning: URL? = nil, dialogs: FakeDialogs = FakeDialogs()) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { gate.secure },
                          layoutStore: MemoryLayoutStore(), learningDirectory: learning, dialogs: dialogs)
        XCTAssertNotNil(shell.engine)
        return shell
    }

    // MARK: 6.11 left context (string processing, range, timing)

    func testOnlyTheTrailingHanCharactersAreKept() {
        let c = Controller(makeShell())
        let cases: [(String, String?)] = [
            ("ab\t中", "中"), ("x\r國", "國"), ("\"中\"", nil), ("好😀", nil), ("中👨‍👩‍👧", nil),
            ("我們今天", "今天"), ("第一行\n他", "他"), ("", nil), ("abc", nil),
        ]
        for (before, expected) in cases {
            c.client.before = before
            XCTAssertEqual(c.client.markedRange().location, NSNotFound, "no marked text: {NSNotFound, 0}")
            XCTAssertEqual(c.session.leftContext(), expected, "case \(cases.firstIndex { $0.0 == before }!)")
        }
    }

    func testRangeIsAtMost32UnitsBeforeTheInsertion() {
        let c = Controller(makeShell())
        c.client.before = String(repeating: "中", count: 10_000)
        XCTAssertEqual(c.session.leftContext(), "中中")
        XCTAssertEqual(c.client.requested, [NSRange(location: 10_000 - 32, length: 32)])
        c.client.before = "ab中"
        XCTAssertEqual(c.session.leftContext(), "中")
        XCTAssertEqual(c.client.requested.last, NSRange(location: 0, length: 3))
        XCTAssertTrue(c.client.requested.allSatisfy { $0.length <= LeftContext.maxUTF16 })
    }

    func testNoInsertionPointOrLeftoverMarkedTextMeansNoContext() {
        let c = Controller(makeShell())
        c.client.before = "ab\t中"
        c.client.selectedOverride = NSRange(location: NSNotFound, length: 0)
        XCTAssertNil(c.session.leftContext())
        c.client.selectedOverride = nil
        c.client.markedOverride = NSRange(location: 3, length: 1)
        XCTAssertNil(c.session.leftContext())
        XCTAssertEqual(c.client.requested, [], "nothing read in either case")
    }

    func testReadOncePerCompositionAndNeverDuringIt() {
        let c = Controller(makeShell())
        c.client.before = "今天"
        c.session.activate()
        c.press(Keys.enter)          // pass-through outside a composition: no read
        XCTAssertEqual(c.client.reads, 0)
        c.type("su3cl3 ")            // composition, then the candidate window
        XCTAssertEqual(c.client.requested.count, 1)
        let reads = c.client.reads
        c.type("2")
        c.press(Keys.enter)
        XCTAssertEqual(c.client.reads, reads, "no read during the composition or at its commit")
        c.type("su3")
        XCTAssertEqual(c.client.requested.count, 2, "the next composition reads once")
        c.session.commitComposition()
        XCTAssertEqual(c.client.requested.count, 2)
    }

    func testNoReadWhenGated() {
        let gate = Gate()
        gate.secure = true
        for (shell, bundle) in [(makeShell(gate: gate), "com.apple.TextEdit"),
                                (makeShell(), "com.bitwarden.desktop"),
                                (makeShell(), nil)] as [(Shell, String?)] {
            let c = Controller(shell, bundle: bundle)
            c.client.before = "今天"
            c.session.activate()
            c.type("su3cl3")
            c.press(Keys.enter)
            XCTAssertEqual(c.client.text, "你好", "typing still works")
            XCTAssertEqual(c.client.reads, 0, "a paused gate reads nothing")
        }
    }

    // MARK: 6.12 privacyGate

    func testGatePausesOnSecureInputDenylistAndUnknownApps() {
        let gate = Gate()
        let shell = makeShell(gate: gate)
        XCTAssertNil(shell.learningPause(bundle: "com.apple.TextEdit"))
        XCTAssertNil(shell.learningPause(bundle: "com.apple.Terminal"), "terminals learn (user decision, section 3)")
        for id in Shell.learningDenylist {
            XCTAssertEqual(shell.learningPause(bundle: id), .app)
        }
        XCTAssertEqual(shell.learningPause(bundle: "com.1password.1password"), .app)
        XCTAssertEqual(shell.learningPause(bundle: nil), .app)
        XCTAssertEqual(shell.learningPause(bundle: ""), .app)
        gate.secure = true
        XCTAssertEqual(shell.learningPause(bundle: "com.apple.TextEdit"), .secureInput)
        XCTAssertEqual(Shell.learningDenylist, [
            "com.1password.1password", "com.agilebits.onepassword7", "com.bitwarden.desktop",
            "org.keepassxc.keepassxc", "com.apple.Passwords", "com.apple.keychainaccess",
        ])
    }

    func testMenuShowsThePauseFirstWithFixedStrings() {
        let gate = Gate()
        let shell = makeShell(gate: gate)
        let text = Controller(shell), denied = Controller(shell, bundle: "org.keepassxc.keepassxc")
        let unknown = Controller(shell, bundle: nil)
        XCTAssertEqual(text.session.menu.first?.title, "標準鍵盤", "nothing paused: no status line")
        XCTAssertFalse(text.session.menu.contains { $0.title.hasPrefix("學習已暫停") })
        XCTAssertEqual(denied.session.menu.first, MenuEntry(title: "學習已暫停（此 App）"))
        XCTAssertEqual(unknown.session.menu.first, MenuEntry(title: "學習已暫停（此 App）"))
        gate.secure = true
        XCTAssertEqual(text.session.menu.first, MenuEntry(title: "學習已暫停（安全輸入）"))
        XCTAssertEqual(denied.session.menu.first?.action, nil, "a status line, not an action")
    }

    // MARK: menu: clear and backup (section 4)

    /// The clear asks in a window (user request 2026-10-05); 取消 keeps everything, 清除 clears.
    func testClearAsksInAWindowFirst() throws {
        let dir = TestLearning.directory()
        let dialogs = FakeDialogs()
        let c = Controller(makeShell(learning: dir, dialogs: dialogs))
        XCTAssertEqual(c.session.menu.map(\.title), ["標準鍵盤", "倚天鍵盤", "清除選字記憶…", "不要備份選字記憶"])
        XCTAssertEqual(c.session.menu.first { $0.title == "清除選字記憶…" }?.action, .clear)
        c.session.activate()
        try repick(c)
        c.press(Keys.enter)
        XCTAssertFalse(TestLearning.records(in: dir).isEmpty)
        dialogs.answer = false
        c.session.perform(.clear)
        XCTAssertEqual(dialogs.asked, 1)
        XCTAssertFalse(TestLearning.records(in: dir).isEmpty, "取消 cleared the records")
        dialogs.answer = true
        c.session.perform(.clear)
        XCTAssertEqual(dialogs.asked, 2)
        XCTAssertTrue(TestLearning.records(in: dir).isEmpty, "清除 did not clear")
        XCTAssertEqual(dialogs.failures, 0)
        XCTAssertEqual(c.session.menu.map(\.title), ["標準鍵盤", "倚天鍵盤", "清除選字記憶…", "不要備份選字記憶"])
    }

    /// A clear that does not succeed is shown, never passed off as done (here: no engine at all).
    func testFailedClearIsShown() {
        let dialogs = FakeDialogs()
        let shell = Shell(resources: resources.appendingPathComponent("missing"), panel: FakePanel(),
                          isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: TestLearning.directory(), dialogs: dialogs)
        XCTAssertNil(shell.engine)
        let c = Controller(shell)
        c.session.perform(.clear)
        XCTAssertEqual(dialogs.failures, 1, "the failure window did not open")
    }

    func testBackupIsOnByDefaultAndTheExclusionSurvivesAClear() throws {
        let dir = TestLearning.directory()
        let c = Controller(makeShell(learning: dir))
        let toggle = { c.session.menu.first { $0.action == .toggleBackup } }
        let excluded = { try dir.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup }
        XCTAssertEqual(toggle()?.checked, false, "backed up by default (user decision, section 9.3)")
        XCTAssertEqual(try excluded(), false)
        c.session.perform(.toggleBackup)
        XCTAssertEqual(try excluded(), true, "set on the directory")
        XCTAssertEqual(toggle()?.checked, true)
        c.session.perform(.clear)
        XCTAssertTrue(FileManager.default.fileExists(atPath: dir.path), "the directory stays")
        XCTAssertEqual(try excluded(), true, "a clear keeps the flag")
        c.session.perform(.toggleBackup)
        XCTAssertEqual(try excluded(), false)
        XCTAssertEqual(toggle()?.checked, false)
    }

    func testAppLearningDirectoryIsApplicationSupportShanjie() throws {
        // Path arithmetic only: nothing is created or read there.
        let url = try XCTUnwrap(Shell.learningURL())
        XCTAssertTrue(url.path.hasSuffix("/Library/Application Support/shanjie"))
    }

    // MARK: through the learning core

    /// Types ㄋㄧˇ, opens the candidates, picks the second one (a re-pick) and leaves the composition
    /// open; returns the picked word.
    @discardableResult
    private func repick(_ c: Controller) throws -> String {
        c.client.before = "他"           // a single character learns only after a Han character (section 12)
        c.type("su3 ")
        let second = try XCTUnwrap(c.panel.items.dropFirst().first)
        c.type("2")
        return second
    }

    /// Like `repick`, for a word of two characters (ㄋㄧˇ ㄏㄠˇ): it may learn under "^" too, so when a
    /// gate test stores nothing the gate is the reason, not the single-character rule (section 12).
    @discardableResult
    private func repickTwo(_ c: Controller) throws -> String {
        c.type("su3cl3 ")
        let items = c.panel.items
        let i = try XCTUnwrap(items.indices.dropFirst().first { items[$0].count == 2 })
        // one digit selects within the first page of 9; an off-page candidate must fail loudly
        XCTAssertLessThan(i, 9, "the two-character candidate is not on the first page")
        guard i < 9 else { return items[i] }
        c.type("\(i + 1)")
        return items[i]
    }

    func testRepickThenEnterStoresARecord() throws {
        let dir = TestLearning.directory()
        let c = Controller(makeShell(learning: dir))
        c.session.activate()
        let word = try repick(c)
        c.press(Keys.enter)
        let lines = TestLearning.records(in: dir)
        XCTAssertTrue(lines.contains { $0.split(separator: "\t", omittingEmptySubsequences: false).dropFirst(2).first.map(String.init) == word })
        XCTAssertTrue(lines.allSatisfy { $0.split(separator: "\t", omittingEmptySubsequences: false).count == 5 })
        let attrs = try FileManager.default.attributesOfItem(atPath: dir.appendingPathComponent("learning.tsv").path)
        XCTAssertEqual((attrs[.posixPermissions] as? NSNumber)?.intValue, 0o600)
    }

    /// A mouse pick samples the learning gate before the core call, as a key does: the gate turns on
    /// after the last key and before the click, and the click must still learn nothing.
    func testClickWhilePausedLearnsNothing() throws {
        let dir = TestLearning.directory(), gate = Gate()
        let c = Controller(makeShell(gate: gate, learning: dir))
        c.session.activate()
        c.type("su3 ")
        gate.secure = true
        c.panel.click(1)
        gate.secure = false
        c.press(Keys.enter)
        XCTAssertEqual(TestLearning.records(in: dir), [])
    }

    func testClickWhileEnabledLearns() throws {
        let dir = TestLearning.directory()
        let c = Controller(makeShell(learning: dir))
        c.session.activate()
        c.type("su3 ")
        let second = try XCTUnwrap(c.panel.items.dropFirst().first)
        c.panel.click(1)
        c.press(Keys.enter)
        XCTAssertTrue(TestLearning.records(in: dir).contains { $0.contains("\t\(second)\t") })
    }

    func testGateTurningOnMidCompositionStoresNothing() throws {
        let dir = TestLearning.directory(), gate = Gate()
        let c = Controller(makeShell(gate: gate, learning: dir))
        c.session.activate()
        try repickTwo(c)
        gate.secure = true
        c.press(Keys.enter)
        XCTAssertEqual(TestLearning.records(in: dir), [])
    }

    func testPickWhileGatedThenOpenedStoresNothing() throws {
        let dir = TestLearning.directory(), gate = Gate()
        let c = Controller(makeShell(gate: gate, learning: dir))
        c.session.activate()
        gate.secure = true
        try repickTwo(c)
        gate.secure = false
        c.press(Keys.enter)
        XCTAssertEqual(TestLearning.records(in: dir), [])
    }

    /// Control for the two tests above: the same two-character pick, ungated, is stored.
    func testTwoCharacterRepickUngatedStoresARecord() throws {
        let dir = TestLearning.directory()
        let c = Controller(makeShell(learning: dir))
        c.session.activate()
        let word = try repickTwo(c)
        c.press(Keys.enter)
        XCTAssertTrue(TestLearning.records(in: dir).contains { $0.contains("\t\(word)\t") })
    }

    /// Section 6.8 (XCTest part): secure input on mid-composition, then each reset path.
    func testResetPathsWithSecureInputStoreNothing() throws {
        for path in ["commitComposition", "deactivate", "claim"] {
            let dir = TestLearning.directory(), gate = Gate()
            let shell = makeShell(gate: gate, learning: dir)
            let c = Controller(shell)
            c.session.activate()
            try repick(c)
            gate.secure = true
            switch path {
            case "commitComposition": c.session.commitComposition()
            case "deactivate": c.session.deactivate()
            default: Controller(shell).type("su3")
            }
            XCTAssertEqual(TestLearning.records(in: dir), [], path)
        }
    }

    func testLayoutSwitchReenablesLearning() throws {
        let dir = TestLearning.directory()
        let c = Controller(makeShell(learning: dir))
        c.session.activate()
        c.session.selectLayout(.eten)    // a rebuilt engine starts with learning off
        c.client.before = "他"           // a single character learns only after a Han character (section 12)
        c.type("ne3 ")                   // ㄋㄧˇ on ETen, then the candidates
        let second = try XCTUnwrap(c.panel.items.dropFirst().first)
        c.type("2")
        c.press(Keys.enter)
        XCTAssertTrue(TestLearning.records(in: dir).contains { $0.contains("\t\(second)\t") })
    }

    func testClearRemovesFilesKeepsDirectoryAndFlag() throws {
        let dir = TestLearning.directory()
        let c = Controller(makeShell(learning: dir))
        c.session.activate()
        try repick(c)
        c.press(Keys.enter)
        XCTAssertFalse(TestLearning.records(in: dir).isEmpty)
        c.session.perform(.toggleBackup)
        c.session.perform(.clear)
        let left = try FileManager.default.contentsOfDirectory(atPath: dir.path)
        XCTAssertEqual(left, [])
        XCTAssertEqual(try dir.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup, true)
    }

    /// Code 3 from the core (a file that cannot be deleted) opens the failure window.
    func testCoreClearFailureIsShown() throws {
        let dir = TestLearning.directory()
        let dialogs = FakeDialogs()
        let c = Controller(makeShell(learning: dir, dialogs: dialogs))
        let blocker = dir.appendingPathComponent("learning.tsv", isDirectory: true)
        try FileManager.default.createDirectory(at: blocker, withIntermediateDirectories: false)
        try Data("x".utf8).write(to: blocker.appendingPathComponent("x"))
        c.session.perform(.clear)
        XCTAssertEqual(dialogs.failures, 1, "the failure window did not open")
    }

    /// A failed write (read-only directory) shows the fixed status line.
    func testFailedWriteIsShown() throws {
        let dir = TestLearning.directory()
        let c = Controller(makeShell(learning: dir))
        c.session.activate()
        try FileManager.default.setAttributes([.posixPermissions: 0o500], ofItemAtPath: dir.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dir.path) }
        try repick(c)
        c.press(Keys.enter)
        XCTAssertTrue(c.session.menu.contains(MenuEntry(title: "選字記憶無法存檔")))
    }
}
