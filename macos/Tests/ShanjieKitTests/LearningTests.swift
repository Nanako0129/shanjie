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
                          layoutStore: MemoryLayoutStore(), learningDirectory: learning, dialogs: dialogs, demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
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
            XCTAssertEqual(LeftContext.han(before, truncated: false), expected, "case \(cases.firstIndex { $0.0 == before }!)")
        }
    }

    func testRangeIsAtMost256UnitsBeforeTheInsertion() {
        let c = Controller(makeShell())
        c.client.before = String(repeating: "中", count: 10_000)
        XCTAssertEqual(c.readLeft().text, String(repeating: "中", count: 64))
        XCTAssertEqual(LeftContext.maxUTF16, 256)
        XCTAssertEqual(c.client.requested, [NSRange(location: 10_000 - 256, length: 256)])
        c.client.before = "ab中"
        XCTAssertEqual(c.readLeft().text, "ab中")
        XCTAssertEqual(c.client.requested.last, NSRange(location: 0, length: 3))
        XCTAssertTrue(c.client.requested.allSatisfy { $0.length <= LeftContext.maxUTF16 })
    }

    // MARK: context-return section 1: the raw text of the same message, at most 64 graphemes

    /// What the session passes to the core for `before` (TextEdit, not a terminal).
    private func passer() -> (String) -> String? {
        let c = Controller(makeShell())
        return { before in
            c.client.before = before
            return c.readLeft().text
        }
    }

    func testTheLastLineBreakEndsTheContext() {
        let passed = passer()
        for br in ["\n", "\r\n", "\r", "\u{2028}", "\u{2029}", "\u{0B}", "\u{0C}", "\u{85}"] {
            XCTAssertEqual(passed("第一行，有 English\(br)你好 Joe"), "你好 Joe", "\(br.unicodeScalars.map { $0.value })")
        }
        XCTAssertEqual(passed("中\0文"), "文", "NUL is a break, not deleted")
        XCTAssertEqual(passed("我說：「Joe 😀」，"), "我說：「Joe 😀」，", "punctuation, spaces, English and emoji stay")
        XCTAssertNil(passed("第一行\n"), "nothing after the break")
    }

    func testSixtyFourGraphemesAreKept() {
        let passed = passer()
        XCTAssertEqual(passed(String(repeating: "中", count: 65)), String(repeating: "中", count: 64))
        // A ZWJ sequence, a skin-tone modifier and a combining mark count one grapheme each.
        let wide = "👨‍👩‍👧" + "👍🏽" + "e\u{301}"
        XCTAssertEqual(["👨‍👩‍👧", "👍🏽", "e\u{301}"].map(\.utf16.count), [8, 4, 2], "the units of each (LeftContext.maxUTF16's comment)")
        XCTAssertEqual(passed("x" + wide + String(repeating: "中", count: 61)), wide + String(repeating: "中", count: 61))
        // A 4-byte character right at the 64th grapheme from the end is kept whole; one more and it goes.
        XCTAssertEqual(passed("a𠮷" + String(repeating: "中", count: 63)), "𠮷" + String(repeating: "中", count: 63))
        XCTAssertEqual(passed("𠮷" + String(repeating: "中", count: 64)), String(repeating: "中", count: 64))
    }

    /// A read that starts inside the text drops its first grapheme (it may be cut): 100 four-unit emoji, the last 256 units read.
    func testATruncatedReadDropsItsFirstGrapheme() {
        let c = Controller(makeShell())
        c.client.before = String(repeating: "👍🏽", count: 100)
        let read = c.readLeft()
        XCTAssertEqual(c.client.requested.last, NSRange(location: 400 - 256, length: 256))
        XCTAssertEqual(read.text, String(repeating: "👍🏽", count: 63))
        // Cut inside a surrogate pair: nothing of the half character reaches the core.
        c.client.before = String(repeating: "𠮷", count: 200) + "中"
        XCTAssertEqual(c.readLeft().text, String(repeating: "𠮷", count: 63) + "中")
    }

    /// P3-2: an app that answers with more than was asked for is cut to the last `maxUTF16` units and treated as truncated.
    func testAnOverlongReplyIsClampedFirst() {
        let c = Controller(makeShell())
        c.client.before = "你好"
        for br in ["\n", "\0"] {
            c.client.replyOverride = String(repeating: "中", count: 1_000) + br + "你好"
            XCTAssertEqual(c.readLeft().text, "你好", "\(br.unicodeScalars.map { $0.value })")
            XCTAssertEqual(LeftContext.han(LeftContext.clamp(c.client.replyOverride!).text, truncated: true), "你好", "the same read's Han tail")
        }
        // The clamped reply counts as truncated: of 100 four-unit emoji the last 256 units hold 64, and the first of them goes.
        c.client.replyOverride = "\n" + String(repeating: "👍🏽", count: 100)
        XCTAssertEqual(c.readLeft().text, String(repeating: "👍🏽", count: 63))
        // 1 MB without a line break: only the last maxUTF16 units are looked at.
        let big = String(repeating: "a", count: 1 << 20)
        XCTAssertLessThanOrEqual(LeftContext.clamp(big).text.utf16.count, LeftContext.maxUTF16)
        XCTAssertTrue(LeftContext.clamp(big).truncated)
        c.client.replyOverride = big
        let start = ContinuousClock.now
        let read = c.readLeft()
        let took = ContinuousClock.now - start
        XCTAssertEqual(read.text, String(repeating: "a", count: 64))
        print("context-return: leftContext() on a 1 MB reply took \(took)")
    }

    /// Review of PR #294: the first key of a composition reads the bundle ID once, for the gate and the terminal check together.
    func testTheBundleIDIsReadOncePerKey() {
        let c = Controller(makeShell(), bundle: "com.apple.Terminal")
        c.client.before = "$ ls 我們今天"
        let before = c.client.bundleReads
        XCTAssertTrue(c.session.send(ShanjieKey(kind: KeyMap.char, ch: UInt32(Character("g").asciiValue!), modifiers: 0)))
        XCTAssertEqual(c.client.bundleReads - before, 1)
        XCTAssertEqual(c.client.requested.count, 1, "the left context was read")
    }

    func testTerminalAppsGetOnlyTheHanTail() {
        XCTAssertEqual(LeftContext.terminals, [
            "com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable", "net.kovidgoyal.kitty", "com.github.wez.wezterm",
            "org.alacritty", "co.zeit.hyper", "com.mitchellh.ghostty",
        ])
        let shell = makeShell()
        for bundle in LeftContext.terminals {
            let c = Controller(shell, bundle: bundle)
            c.client.before = "$ ls secret.txt 我們今天"
            XCTAssertEqual(c.readLeft().text, "今天", bundle)
            c.client.before = "$ ls secret.txt"
            XCTAssertNil(c.readLeft().text, bundle)
        }
        let c = Controller(shell, bundle: "com.apple.TextEdit")
        c.client.before = "$ ls secret.txt 我們今天"
        XCTAssertEqual(c.readLeft().text, "$ ls secret.txt 我們今天")
    }

    func testNoInsertionPointOrLeftoverMarkedTextMeansNoContext() {
        let c = Controller(makeShell())
        c.client.before = "ab\t中"
        c.client.selectedOverride = NSRange(location: NSNotFound, length: 0)
        XCTAssertNil(c.readLeft().text)
        c.client.selectedOverride = nil
        c.client.markedOverride = NSRange(location: 3, length: 1)
        XCTAssertNil(c.readLeft().text)
        XCTAssertEqual(c.client.requested, [], "nothing read in either case")
    }

    /// Clients measured on device (2026-10-06) report no marked text as an empty range at the
    /// insertion point, or as {NSNotFound, NSNotFound}; both still mean no marked text.
    func testEmptyMarkedRangeAnywhereStillReadsTheContext() {
        let c = Controller(makeShell())
        c.client.before = "ab\t中"
        for marked in [NSRange(location: 4, length: 0), NSRange(location: 0, length: 0),
                       NSRange(location: NSNotFound, length: NSNotFound)] {
            c.client.markedOverride = marked
            XCTAssertEqual(c.readLeft().text, "ab\t中", "\(marked)")
        }
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
        XCTAssertEqual(c.session.menu.map(\.title), ["標準鍵盤", "倚天鍵盤", "避免把敏感字詞排在前面", "即時預測", "可省略韻母", "中英混打（實驗）", "語意校正", "這個 App 用聊天設定", "善解設定…", "清除選字記憶…", "不要備份選字記憶"])
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
        XCTAssertEqual(c.session.menu.map(\.title), ["標準鍵盤", "倚天鍵盤", "避免把敏感字詞排在前面", "即時預測", "可省略韻母", "中英混打（實驗）", "語意校正", "這個 App 用聊天設定", "善解設定…", "清除選字記憶…", "不要備份選字記憶"])
    }

    /// The clear window's disclosure (new-word B section 2.7): the words learned from character re-picks are named
    /// along with re-picks and prediction picks, and the Time Machine caveat stays.
    func testClearMessageNamesNewWords() {
        XCTAssertEqual(DialogText.clearMessage, "會刪除這台電腦上記住的改選、預測列選取，以及從逐字改選學到的新詞，之後要重新學。清除只刪本機檔案；已經進 Time Machine 備份或本機快照的副本不受影響。")
    }

    /// A clear that does not succeed is shown, never passed off as done (here: no engine at all).
    func testFailedClearIsShown() {
        let dialogs = FakeDialogs()
        let shell = Shell(resources: resources.appendingPathComponent("missing"), panel: FakePanel(),
                          isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: TestLearning.directory(), dialogs: dialogs, demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
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
        // Clearing the flag soon after setting it can reach the file system a few ms after setResourceValues returns
        // (measured for PR #198: a fresh read right after said true in 12 of 400 runs with no gap, none with 50 ms or more;
        // the slowest clear seen, under load, landed after about 740 ms), so wait for it to land before the two reads.
        let deadline = Date().addingTimeInterval(5)
        while try excluded() != false && Date() < deadline {
            var d = dir
            d.removeAllCachedResourceValues()
            usleep(2_000)
        }
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
        c.client.before = "他們"         // a single character learns only after two Han characters (sections 12, 14)
        c.type("su3 ")
        let second = try XCTUnwrap(c.panel.items.dropFirst().first)
        c.type("2")
        return second
    }

    /// Like `repick`, for a word of two characters (ㄋㄧˇ ㄏㄠˇ): it may learn at sentence start too (under
    /// a "^>..." key), so when a gate test stores nothing the gate is the reason, not the single-character rule (section 12).
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
        // A two-character word: a single character at sentence start learns nothing (section 12 rule 6).
        c.type("su3cl3 ")
        let items = c.panel.items
        let i = try XCTUnwrap(items.indices.dropFirst().first { items[$0].count == 2 })
        c.panel.click(i)
        c.press(Keys.enter)
        XCTAssertTrue(TestLearning.records(in: dir).contains { $0.contains("\t\(items[i])\t") })
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
        c.client.before = "他們"         // a single character learns only after two Han characters (sections 12, 14)
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
