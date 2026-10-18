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
        for (before, want) in [("claude", " 你好"), ("2026", " 你好"), ("今天", "你好"), ("今天 ", "你好"), ("今天，", "你好"), ("", "你好")] {
            let c = Controller(makeShell(spacing: true))
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

    /// Two commits in one composition: 39 syllables, an English run (Shift+C...), and the next key close it as the 40th unit, so
    /// the whole string is committed while the key itself starts a new syllable in the same composition. The second commit's
    /// leading space follows the last character of the first (the "e" of the English token), not the client's text.
    func testSecondCommitInTheSameCompositionUsesTheLastCommittedCharacter() {
        let c = Controller(makeShell(spacing: true))
        c.session.activate()
        for _ in 0..<39 { c.type("su3") }
        c.type("c", flags: .shift)
        c.type("laude")
        c.type("2")  // a Zhuyin key closes the run: the token is unit 40, 39 x 你 + Claude is committed, and ㄉ starts a new syllable
        let first = inserts(c)
        XCTAssertEqual(first.count, 1)
        XCTAssertEqual(first.first, String(repeating: "你", count: 39) + " Claude")
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
}
