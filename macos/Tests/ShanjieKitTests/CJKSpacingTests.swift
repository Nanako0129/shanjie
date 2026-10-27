import AppKit
import XCTest
@testable import ShanjieKit

/// docs/contracts/cjk-spacing.md section 3: the pure function, then the shell through the real core.
final class CJKSpacingFunctionTests: XCTestCase {
    func testTable() {
        let rows: [(Character?, String, String)] = [
            // section 1 examples
            (nil, "我用claude寫", "我用 claude 寫"),
            (nil, "2026下午3點", "2026 下午 3 點"),
            (nil, "我用 claude 寫", "我用 claude 寫"),
            (nil, "（2026）", "（2026）"),
            (nil, "，claude", "，claude"),
            // previous character: letter, digit, Han, space, full-width punctuation, none
            ("e", "寫", " 寫"), ("3", "點", " 點"), ("寫", "claude", " claude"), ("寫", "3", " 3"),
            ("e", "claude", "claude"), ("寫", "寫", "寫"),
            (" ", "寫", "寫"), (" ", "claude", "claude"),
            ("，", "寫", "寫"), ("，", "claude", "claude"),
            (nil, "寫", "寫"), (nil, "claude", "claude"),
            // supplementary-plane Han (U+20000) on both sides
            (nil, "\u{20000}a", "\u{20000} a"), (nil, "a\u{20000}", "a \u{20000}"),
            ("a", "\u{20000}", " \u{20000}"), ("\u{20000}", "a", " a"),
            // Han with a variation selector (U+845B U+E0100) is one grapheme whose first scalar is Han, on both sides
            (nil, "\u{845B}\u{E0100}a", "\u{845B}\u{E0100} a"), (nil, "a\u{845B}\u{E0100}", "a \u{845B}\u{E0100}"),
            ("\u{845B}\u{E0100}", "a", " a"), ("a", "\u{845B}\u{E0100}", " \u{845B}\u{E0100}"),
            // only punctuation, empty
            (nil, "，。！", "，。！"), ("e", "，", "，"), ("寫", "，", "，"),
            (nil, "", ""), ("e", "", ""),
            // other characters never trigger: full-width alphanumerics, bopomofo, ASCII symbols
            (nil, "我Ａ", "我Ａ"), (nil, "ㄅa", "ㄅa"), (nil, "我%", "我%"), (nil, "我@a", "我@a"),
        ]
        for (p, text, want) in rows {
            XCTAssertEqual(CJKSpacing.apply(previous: p, text: text), want, "\(p.map(String.init) ?? "nil") + \(text)")
        }
    }
}

@MainActor
final class CJKSpacingShellTests: XCTestCase {
    private var resources: URL!

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    final class Gate { var secure = false }

    private func makeShell(spacing: Bool?, gate: Gate = Gate(), store: MemorySpacingStore? = nil) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { gate.secure }, layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: store ?? MemorySpacingStore(spacing))
        XCTAssertNotNil(shell.engine)
        return shell
    }

    private func inserts(_ c: Controller) -> [String] {
        c.client.calls.compactMap { if case .insert(let s) = $0 { s } else { nil } }
    }

    func testStoreDefaultsToOff() {
        XCTAssertFalse(makeShell(spacing: nil).spacingOn)
        XCTAssertTrue(makeShell(spacing: true).spacingOn)
        XCTAssertFalse(makeShell(spacing: false).spacingOn)
    }

    func testSetterStoresAndNotifies() {
        let store = MemorySpacingStore()
        let shell = makeShell(spacing: nil, store: store)
        var posts = 0
        let token = NotificationCenter.default.addObserver(forName: Shell.didChangeSettings, object: shell, queue: nil) { _ in posts += 1 }
        defer { NotificationCenter.default.removeObserver(token) }
        shell.setSpacing(true)
        XCTAssertTrue(shell.spacingOn)
        XCTAssertEqual(store.spacing, true)
        XCTAssertEqual(posts, 1)
    }

    /// Off: insertText gets o.commit byte for byte, even with a Latin letter before the cursor.
    func testOffIsByteIdentical() {
        let c = Controller(makeShell(spacing: nil))
        c.client.before = "claude"
        c.session.activate()
        c.type("su3cl3")
        c.press(Keys.enter)
        XCTAssertEqual(inserts(c), ["你好"])
    }

    func testOnAddsLeadingSpaceFromTheCharacterBeforeTheCursor() {
        let shell = makeShell(spacing: true)
        for (before, want) in [("claude", " 你好"), ("2026", " 你好"), ("今天", "你好"), ("今天 ", "你好"), ("今天，", "你好"), ("", "你好")] {
            let c = Controller(shell)
            c.client.before = before
            c.session.activate()
            c.type("su3cl3")
            c.press(Keys.enter)
            XCTAssertEqual(inserts(c), [want], "before: \(before)")
        }
    }

    /// p is the raw last character of the read, not only the Han tail.
    func testLeadingSpaceUsesTheRawLastCharacter() {
        let c = Controller(makeShell(spacing: true))
        c.client.before = String(repeating: "中", count: 40) + "x"
        c.session.activate()
        c.type("su3cl3")
        c.press(Keys.enter)
        XCTAssertEqual(inserts(c), [" 你好"])
    }

    /// Two commits in one composition: 127 syllables, an English run (Shift+C...), and the next key close it as the 128th unit, so
    /// the whole string is committed while the key itself starts a new syllable in the same composition. The second commit's
    /// leading space follows the last character of the first (the "e" of the English token), not the client's text.
    func testSecondCommitInTheSameCompositionUsesTheLastCommittedCharacter() {
        let c = Controller(makeShell(spacing: true))
        c.session.activate()
        for _ in 0..<127 { c.type("su3") }
        c.type("c", flags: .shift)
        c.type("laude")
        c.type(" ")  // Space closes the run (a Zhuyin key now goes on it, zhen-revert section 2.4): the token is unit 128, 127 x 你 + Claude is committed
        c.type("2")  // and ㄉ starts a new syllable in the emptied composition
        let first = inserts(c)
        XCTAssertEqual(first.count, 1)
        XCTAssertEqual(first.first, String(repeating: "你", count: 127) + " Claude")
        c.type("u3")
        c.press(Keys.enter)  // commits the new syllable as the second commit of this composition
        XCTAssertEqual(inserts(c).count, 2)
        XCTAssertEqual(inserts(c).last, " 底", "p is the e of Claude")
    }

    /// A paused gate reads nothing, so there is no leading space; spaces inside the text still go in.
    func testPausedLearningSkipsTheLeadingSpaceOnly() {
        let gate = Gate()
        gate.secure = true
        let c = Controller(makeShell(spacing: true, gate: gate))
        c.client.before = "claude"
        c.session.activate()
        c.type("su3cl3")
        c.press(Keys.enter)
        XCTAssertEqual(c.client.reads, 0)
        XCTAssertEqual(inserts(c), ["你好"], "no leading space")
        c.type("su3")
        c.type("c", flags: .shift)
        c.type("laude")
        c.type("2u3")
        c.press(Keys.enter)
        XCTAssertEqual(inserts(c).last, "你 Claude 底")
    }

    /// Revision one: p is the Han character with a variation selector (U+845B U+E0100), a single grapheme whose first scalar is Han.
    func testVariationSelectorHanCountsAsHanForTheLeadingSpace() {
        let c = Controller(makeShell(spacing: true))
        c.client.before = "\u{845B}\u{E0100}"
        c.session.activate()
        c.type("c", flags: .shift)
        c.type("laude")
        c.press(Keys.enter)
        XCTAssertEqual(inserts(c), [" Claude"])
    }

    /// The leftover of a finished composition: 127 x 你 + Claude is committed by the 128th unit and the pending ㄉ stays in the
    /// composition (p is the e of Claude). Returns the controller with that first commit made.
    private func afterTheFirstCommitOfOneComposition() -> Controller {
        let c = Controller(makeShell(spacing: true))
        c.session.activate()
        for _ in 0..<127 { c.type("su3") }
        c.type("c", flags: .shift)
        c.type("laude")
        c.type(" ")  // Space ends the run; a Zhuyin key would go on it (zhen-revert section 2.4)
        c.type("2")
        XCTAssertEqual(inserts(c), [String(repeating: "你", count: 127) + " Claude"])
        return c
    }

    /// A commit straight from the candidates, with no key in between, follows the last committed character (the e of Claude).
    func testPickAfterACommitUsesTheLastCommittedCharacter() {
        let c = afterTheFirstCommitOfOneComposition()
        XCTAssertTrue(c.session.candidateSelected(at: 0))
        c.press(Keys.enter)
        XCTAssertEqual(inserts(c).count, 2)
        XCTAssertTrue(inserts(c).last?.hasPrefix(" ") == true, "\(inserts(c).last ?? "")")
    }

    /// A pass-through key (Cmd+C: the app acts on it, the cursor may move) forgets p, so the same commit gets no leading space.
    func testPassThroughKeyClearsPBeforeAPick() {
        let c = afterTheFirstCommitOfOneComposition()
        XCTAssertFalse(c.type("c", flags: .command)[0], "Cmd+C is not handled")
        XCTAssertTrue(c.session.candidateSelected(at: 0))
        c.press(Keys.enter)
        XCTAssertEqual(inserts(c).count, 2)
        XCTAssertFalse(inserts(c).last?.hasPrefix(" ") == true, "\(inserts(c).last ?? "")")
    }

    /// Ctrl+\ starts a composition without reading the client (a modifier), and the composition's first key still clears p: the
    /// Zhuyin typed to the left of 、 commits as 你好、 with no space after the previous commit that ended in a letter.
    func testControlKeyStartingACompositionClearsAnOldP() {
        let c = Controller(makeShell(spacing: true))
        c.session.activate()
        c.type("su3")
        c.type("c", flags: .shift)
        c.type("laude")
        c.press(Keys.enter)
        XCTAssertEqual(inserts(c), ["你 Claude"])
        c.type("\\", flags: .control)  // 、 starts a composition
        c.press(123)  // left arrow: the cursor goes before 、
        c.type("su3cl3")
        c.press(Keys.enter)
        XCTAssertEqual(inserts(c).last, "你好、")
    }

    /// Revision one: the settings model reads the shell's switch, the setter changes it, and the line under it is the contract's.
    func testSettingsModelFollowsTheShellAndTheNoteIsTheContractsLine() {
        let shell = makeShell(spacing: true)
        let model = SettingsModel(shell: shell)
        XCTAssertTrue(model.spacing)
        model.setSpacing(false)
        XCTAssertFalse(shell.spacingOn)
        XCTAssertFalse(model.spacing)
        shell.setSpacing(true)
        XCTAssertTrue(model.spacing)
        XCTAssertEqual(MenuEntry.Text.spacing, "中英文之間自動加空格")
        XCTAssertEqual(MenuEntry.Text.spacingNote, "在漢字和半形英文、數字之間自動加一個空格（俗稱「盤古之白」）。只改善解送出的文字。")
    }
}
