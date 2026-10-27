import AppKit
import XCTest
@testable import ShanjieKit

/// shift-toggle section 5: the shell side of the Shift toggle, through `Session.handle` with real `NSEvent`s.
@MainActor
final class FakeShiftHint: ShiftHintPanel {
    private(set) var shown: [(text: String, rect: NSRect)] = []
    func show(_ text: String, lineRect: NSRect, appearance: NSAppearance?, glassTint: Double) { shown.append((text, lineRect)) }
}

@MainActor
final class ShiftToggleSessionTests: XCTestCase {
    private var resources: URL!
    private let leftShift: UInt16 = 56
    private var time: TimeInterval = 100

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    private func makeShell(shift: ShiftToggleStore = MemoryShiftToggleStore(), english: EnglishStore = MemoryEnglishStore(),
                           layout: LayoutStore = MemoryLayoutStore(), hint: FakeShiftHint) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: layout, learningDirectory: nil,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: english, acgPackStore: MemoryAcgPackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(),
                          rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(),
                          appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(),
                          shiftToggleStore: shift, shiftHint: hint)
        shell.mouseAgo = { 1000 }
        XCTAssertNotNil(shell.engine)
        return shell
    }

    /// A lone Shift tap: press, then release 0.1 s later.
    @discardableResult
    private func tap(_ c: Controller) -> [Bool] {
        time += 5
        let down = c.session.handle(Keys.event(leftShift, flags: .shift, type: .flagsChanged, time: time))
        let up = c.session.handle(Keys.event(leftShift, flags: [], type: .flagsChanged, time: time + 0.1))
        return [down, up]
    }

    func testTapSwitchesAndShowsTheHintAtTheInsertionPoint() {
        let hint = FakeShiftHint()
        let shell = makeShell(hint: hint)
        let c = Controller(shell)
        c.session.activate()
        XCTAssertEqual(tap(c), [false, false], "a flagsChanged event is never consumed")
        XCTAssertEqual(hint.shown.map(\.text), ["英"])
        XCTAssertEqual(hint.shown.last?.rect, c.client.caret, "nothing composed: the insertion point's rectangle")
        tap(c)
        XCTAssertEqual(hint.shown.map(\.text), ["英", "中"])
    }

    func testNoRectangleNoHintButTheStateSwitches() {
        let hint = FakeShiftHint()
        let c = Controller(makeShell(hint: hint))
        c.session.activate()
        c.client.caret = nil
        tap(c)
        XCTAssertTrue(hint.shown.isEmpty)
        XCTAssertFalse(c.press(Keys.code(for: "h")), "English all the same: the key passes through")
    }

    func testEnglishWithNothingComposedPassesEveryKeyThrough() {
        let c = Controller(makeShell(hint: FakeShiftHint()))
        c.session.activate()
        XCTAssertTrue(c.press(Keys.code(for: "h")), "control: Chinese, the key is Zhuyin")
        c.session.commitComposition()
        tap(c)
        XCTAssertEqual(c.type("hello,. "), Array(repeating: false, count: 8))
        XCTAssertEqual(c.client.marked, "")
        tap(c)
        XCTAssertTrue(c.press(Keys.code(for: "h")), "back to Chinese")
    }

    func testComposedTextIsKeptAndTheNextCharactersAreEnglish() {
        let hint = FakeShiftHint()
        let c = Controller(makeShell(hint: hint))
        c.session.activate()
        c.type("su3cl3")  // 你好
        XCTAssertEqual(c.client.marked, "你好")
        tap(c)
        XCTAssertEqual(hint.shown.last?.rect, c.client.line, "composing: the marked text's rectangle")
        XCTAssertEqual(c.client.marked, "你好", "kept")
        XCTAssertTrue(c.press(Keys.code(for: "o")))
        XCTAssertTrue(c.press(Keys.code(for: "k")))
        XCTAssertEqual(c.client.marked, "你好ok")
        XCTAssertTrue(c.press(Keys.enter))
        XCTAssertEqual(c.client.text, "你好ok")
        XCTAssertFalse(c.press(Keys.code(for: "h")), "the composition is gone: pass through again")
    }

    func testShiftOneIsTheCharacterTheKeyboardMadeNotTheKeycap() {
        let c = Controller(makeShell(hint: FakeShiftHint()))
        c.session.activate()
        c.type("su3cl3")
        tap(c)
        let bang = Keys.event(Keys.code(for: "1"), flags: .shift, characters: "!")
        XCTAssertEqual(Session.asciiKey(bang)?.ch, UInt32(UnicodeScalar("!").value))
        XCTAssertEqual(Session.asciiKey(bang)?.modifiers, 0)
        XCTAssertTrue(c.session.handle(bang))
        // The Command / Control / Option / Caps Lock keys never take the ASCII road.
        for flags: NSEvent.ModifierFlags in [.command, .control, .option, .capsLock] {
            XCTAssertNil(Session.asciiKey(Keys.event(0, flags: flags, characters: "a")))
        }
        XCTAssertNil(Session.asciiKey(Keys.event(49, characters: " ")), "the space bar keeps its rule")
    }

    func testShiftOnePutsABangOnTheRun() {
        let c = Controller(makeShell(hint: FakeShiftHint()))
        c.session.activate()
        c.type("su3cl3")
        tap(c)
        XCTAssertTrue(c.session.handle(Keys.event(Keys.code(for: "1"), flags: .shift, characters: "!")))
        XCTAssertEqual(c.client.marked, "你好!")
        XCTAssertTrue(c.session.handle(Keys.event(Keys.code(for: "3"), characters: "3")))
        XCTAssertEqual(c.client.marked, "你好!3", "3 is a character, not a tone key")
    }

    func testEachAppRemembersItsOwnState() {
        let hint = FakeShiftHint()
        let shell = makeShell(hint: hint)
        let a = Controller(shell, bundle: "com.example.A"), b = Controller(shell, bundle: "com.example.B")
        a.session.activate()
        tap(a)
        b.session.activate()
        XCTAssertFalse(shell.engineAscii, "B is Chinese")
        XCTAssertTrue(b.press(Keys.code(for: "h")))
        b.session.commitComposition()
        a.session.activate()
        XCTAssertTrue(shell.engineAscii, "back in A: English")
        XCTAssertFalse(a.press(Keys.code(for: "h")))
    }

    func testWithoutAShadowTheCompositionIsSentFirst() {
        for (name, shell) in [("English mixing off", makeShell(english: MemoryEnglishStore(false), hint: FakeShiftHint())),
                              ("Eten layout", makeShell(layout: MemoryLayoutStore("eten"), hint: FakeShiftHint()))] {
            let c = Controller(shell)
            c.session.activate()
            c.type(name == "Eten layout" ? Layouts.keys("ㄋㄧˇ ㄏㄠˇ", eten: true) : "su3cl3")
            XCTAssertEqual(c.client.marked, "你好", name)
            tap(c)
            XCTAssertEqual(c.client.text, "你好", name)
            XCTAssertEqual(c.client.marked, "", name)
            XCTAssertFalse(c.press(Keys.code(for: "h")), name)
        }
    }

    func testSettingOffStopsTheTapAndClearsEveryApp() {
        let store = MemoryShiftToggleStore()
        let hint = FakeShiftHint()
        let shell = makeShell(shift: store, hint: hint)
        XCTAssertTrue(shell.shiftToggleOn, "default on")
        let c = Controller(shell)
        c.session.activate()
        tap(c)
        XCTAssertFalse(c.press(Keys.code(for: "h")))
        shell.applyShiftToggle(false)
        XCTAssertEqual(store.shiftToggle, false)
        XCTAssertTrue(shell.asciiApps.isEmpty)
        XCTAssertTrue(c.press(Keys.code(for: "h")), "A's letter key is Zhuyin again")
        c.session.commitComposition()
        let shown = hint.shown.count
        tap(c)
        XCTAssertEqual(hint.shown.count, shown, "the tap is ignored")
        XCTAssertTrue(c.press(Keys.code(for: "h")))
    }

    func testKeyOrMouseBetweenIsNotATapThroughTheSession() {
        let hint = FakeShiftHint()
        let shell = makeShell(hint: hint)
        let c = Controller(shell)
        c.session.activate()
        c.session.handle(Keys.event(leftShift, flags: .shift, type: .flagsChanged, time: 10))
        c.press(Keys.code(for: "a"), flags: .shift)
        c.session.handle(Keys.event(leftShift, flags: [], type: .flagsChanged, time: 10.1))
        XCTAssertTrue(hint.shown.isEmpty, "Shift+letter")
        shell.mouseAgo = { 0.05 }
        c.session.handle(Keys.event(leftShift, flags: .shift, type: .flagsChanged, time: 20))
        c.session.handle(Keys.event(leftShift, flags: [], type: .flagsChanged, time: 20.1))
        XCTAssertTrue(hint.shown.isEmpty, "Shift+click")
    }
}
