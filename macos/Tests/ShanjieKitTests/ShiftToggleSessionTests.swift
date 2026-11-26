import AppKit
import XCTest
@testable import ShanjieKit

/// docs/spec/keys-and-composition.md §9.2: the shell side of the Shift toggle, through `Session.handle` with real `NSEvent`s.
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
    /// The clock the shell reads when it handles a Shift event (the system uptime in the app).
    private var uptimeNow: TimeInterval = 0

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    // The toggle defaults to off since docs/spec/english-mixed.md §10.1; these tests are about it on, so the default store says on.
    private func makeShell(shift: ShiftToggleStore = MemoryShiftToggleStore(true), english: EnglishStore = MemoryEnglishStore(overwrite: true),
                           layout: LayoutStore = MemoryLayoutStore(), hint: FakeShiftHint) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: layout, learningDirectory: nil,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: english, acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(),
                          rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(),
                          appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(),
                          shiftToggleStore: shift, shiftHint: hint)
        shell.mouseAgo = { 1000 }
        shell.uptime = { [unowned self] in self.uptimeNow }
        XCTAssertNotNil(shell.engine)
        return shell
    }

    /// A Shift event as the system sends it: the generic bit and the left device bit while down (0x2), no bits when up.
    @discardableResult
    private func shift(_ c: Controller, down: Bool, at: TimeInterval) -> Bool {
        uptimeNow = at
        let flags: NSEvent.ModifierFlags = down ? NSEvent.ModifierFlags(rawValue: NSEvent.ModifierFlags.shift.rawValue | 0x2) : []
        return c.session.handle(Keys.event(leftShift, flags: flags, type: .flagsChanged, time: at))
    }

    /// A lone Shift tap: press, then release 0.1 s later.
    @discardableResult
    private func tap(_ c: Controller) -> [Bool] {
        time += 5
        return [shift(c, down: true, at: time), shift(c, down: false, at: time + 0.1)]
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

    /// docs/spec/english-mixed.md §11: a key event that types `ch` (with its characters, so it counts as a typing key).
    @discardableResult
    private func typeKey(_ c: Controller, _ ch: Character, flags: NSEvent.ModifierFlags = []) -> Bool {
        c.session.handle(Keys.event(Keys.code(for: ch), flags: flags, characters: String(ch)))
    }

    /// docs/spec/keys-and-composition.md §9.4: after activate, the first typing key shows the mode once; later keys do not.
    func testFirstTypingKeyAfterActivateShowsTheModeOnce() {
        let hint = FakeShiftHint()
        let c = Controller(makeShell(hint: hint))
        c.session.activate()
        typeKey(c, "h")
        XCTAssertEqual(hint.shown.map(\.text), ["中"])
        typeKey(c, "i")
        XCTAssertEqual(hint.shown.map(\.text), ["中"], "only the first key")
    }

    /// docs/spec/keys-and-composition.md §9.4: in an app already in English, the first key shows 英 and still passes through.
    func testFirstTypingKeyInEnglishShowsEnglishAndPassesThrough() {
        let hint = FakeShiftHint()
        let c = Controller(makeShell(hint: hint))
        c.session.activate()
        tap(c)  // 英 (and clears the pending hint)
        XCTAssertEqual(hint.shown.map(\.text), ["英"])
        typeKey(c, "h")
        XCTAssertEqual(hint.shown.map(\.text), ["英"], "the tap already showed the mode")
        c.session.deactivate()
        c.session.activate()
        XCTAssertFalse(typeKey(c, "h"), "English with nothing composed: passes through")
        XCTAssertEqual(hint.shown.map(\.text), ["英", "英"])
    }

    /// docs/spec/keys-and-composition.md §9.4: arrows and shortcuts are not typing keys and keep the hint pending.
    func testNonTypingKeysDoNotShowOrClearTheHint() {
        let hint = FakeShiftHint()
        let c = Controller(makeShell(hint: hint))
        c.session.activate()
        c.session.handle(Keys.event(123, flags: [.function, .numericPad], characters: "\u{F702}"))  // ← as the system sends it
        typeKey(c, "c", flags: .command)
        XCTAssertTrue(hint.shown.isEmpty)
        typeKey(c, "h")
        XCTAssertEqual(hint.shown.map(\.text), ["中"])
    }

    /// docs/spec/keys-and-composition.md §9.4: a key that reaches a new controller without activate (an owner change) arms the hint too, and the toggle turned off
    /// in between shows nothing.
    func testOwnerChangeArmsTheHintAndTheToggleOffSilencesIt() {
        let hint = FakeShiftHint()
        let shell = makeShell(hint: hint)
        let a = Controller(shell), b = Controller(shell)
        a.session.activate()
        typeKey(a, "h")
        typeKey(b, "h")  // b claims the composition owner without activate
        XCTAssertEqual(hint.shown.map(\.text), ["中", "中"])
        a.session.activate()
        shell.applyShiftToggle(false)
        typeKey(a, "h")
        XCTAssertEqual(hint.shown.map(\.text), ["中", "中"], "toggle off: no hint")
    }

    /// docs/spec/keys-and-composition.md §9.4: after a pause of `modeHintIdleSeconds` the next typing key shows the mode again; shorter pauses and steady typing do not.
    func testPauseShowsTheHintAgain() {
        let hint = FakeShiftHint()
        let shell = makeShell(hint: hint)
        var clock: TimeInterval = 1000
        shell.idleClock = { clock }
        let c = Controller(shell)
        c.session.activate()
        // Esc after each key keeps the composition empty, so every key is the start of typing.
        typeKey(c, "h"); c.press(Keys.esc)
        clock += 10; typeKey(c, "i"); c.press(Keys.esc)
        XCTAssertEqual(hint.shown.map(\.text), ["中"], "a 10 s pause is not enough")
        clock += 31; typeKey(c, "j"); c.press(Keys.esc)
        XCTAssertEqual(hint.shown.map(\.text), ["中", "中"], "a 31 s pause shows it")
        for _ in 0..<8 { clock += 5; typeKey(c, "k"); c.press(Keys.esc) }  // 40 s of steady typing
        XCTAssertEqual(hint.shown.map(\.text), ["中", "中"], "no pause, no hint")
    }

    /// docs/spec/keys-and-composition.md §9.4: the bound is "at least": exactly `modeHintIdleSeconds` shows, a little less does not.
    func testPauseBoundaryIsInclusive() {
        let hint = FakeShiftHint()
        let shell = makeShell(hint: hint)
        var clock: TimeInterval = 1000
        shell.idleClock = { clock }
        let c = Controller(shell)
        c.session.activate()
        typeKey(c, "h"); c.press(Keys.esc)
        clock += Shell.modeHintIdleSeconds - 0.1; typeKey(c, "i"); c.press(Keys.esc)
        XCTAssertEqual(hint.shown.count, 1)
        clock += Shell.modeHintIdleSeconds; typeKey(c, "j")
        XCTAssertEqual(hint.shown.count, 2)
    }

    /// docs/spec/keys-and-composition.md §9.4: a key in the middle of a composition (a candidate pick after a long look) is not the start of typing.
    func testNoPauseHintInsideAComposition() {
        let hint = FakeShiftHint()
        let shell = makeShell(hint: hint)
        var clock: TimeInterval = 1000
        shell.idleClock = { clock }
        let c = Controller(shell)
        c.session.activate()
        typeKey(c, "s")  // ㄋ, shows 中 at the start
        clock += 35
        typeKey(c, " ")  // Space opens the candidates
        XCTAssertEqual(hint.shown.count, 1)
    }

    /// docs/spec/keys-and-composition.md §9.4: a Shift tap after a pause shows the hint and counts as the pause's end, so the next key does not show it again.
    func testShiftTapAfterAPauseIsNotFollowedByASecondHint() {
        let hint = FakeShiftHint()
        let shell = makeShell(hint: hint)
        var clock: TimeInterval = 1000
        shell.idleClock = { clock }
        let c = Controller(shell)
        c.session.activate()
        typeKey(c, "h")
        clock += 40
        tap(c)
        typeKey(c, "i")
        XCTAssertEqual(hint.shown.map(\.text), ["中", "英"])
    }

    /// docs/spec/keys-and-composition.md §9.4: with the toggle off a pause shows nothing.
    func testPauseShowsNothingWithTheToggleOff() {
        let hint = FakeShiftHint()
        let shell = makeShell(shift: MemoryShiftToggleStore(false), hint: hint)
        var clock: TimeInterval = 1000
        shell.idleClock = { clock }
        let c = Controller(shell)
        c.session.activate()
        typeKey(c, "h")
        clock += 40; typeKey(c, "i")
        XCTAssertTrue(hint.shown.isEmpty)
    }

    /// docs/spec/keys-and-composition.md §9.4: with the Shift toggle off nothing is pending.
    func testNoModeHintWithTheToggleOff() {
        let hint = FakeShiftHint()
        let c = Controller(makeShell(shift: MemoryShiftToggleStore(false), hint: hint))
        c.session.activate()
        typeKey(c, "h")
        XCTAssertTrue(hint.shown.isEmpty)
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
        // The Command / Control / Caps Lock keys never take the ASCII road, with Option held too.
        for flags: NSEvent.ModifierFlags in [.command, .control, .capsLock, [.command, .option], [.control, .option], [.capsLock, .option]] {
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

    /// docs/spec/keys-and-composition.md §9.2: a controller whose client reads nil toggles the app it knew, not the shared `""` entry, so the next key
    /// of the original controller does not pull the state back to English.
    func testANilBundleReadToggleUsesTheKnownApp() {
        let shell = makeShell(hint: FakeShiftHint())
        let a = Controller(shell, bundle: "com.example.A"), b = Controller(shell, bundle: "com.example.A")
        a.session.activate()
        tap(a)
        XCTAssertTrue(shell.engineAscii)
        b.session.activate()
        XCTAssertTrue(shell.engineAscii, "B read A's bundle ID: English")
        b.client.bundle = nil
        tap(b)
        XCTAssertFalse(shell.asciiApps.contains(""), "the nil read did not use the shared entry")
        XCTAssertTrue(a.press(Keys.code(for: "h")), "A types: Chinese, the composition is consumed")
        XCTAssertFalse(shell.engineAscii)
        XCTAssertTrue(shell.asciiApps.isEmpty)
    }

    /// docs/spec/keys-and-composition.md §9.2: sessions activated while the toggle was off still learn their bundle ID, so turning the
    /// setting on later does not bring the nil-read path back.
    func testSessionsActivatedWithTheToggleOffStillKnowTheirApp() {
        let shell = makeShell(shift: MemoryShiftToggleStore(false), hint: FakeShiftHint())
        let a = Controller(shell, bundle: "com.example.A"), b = Controller(shell, bundle: "com.example.A")
        b.session.activate()
        a.session.activate()
        shell.applyShiftToggle(true)
        tap(a)
        XCTAssertTrue(shell.engineAscii)
        b.client.bundle = nil
        tap(b)
        XCTAssertFalse(shell.asciiApps.contains(""))
        XCTAssertTrue(a.press(Keys.code(for: "h")), "A types: Chinese")
        XCTAssertFalse(shell.engineAscii)
    }

    /// docs/spec/keys-and-composition.md §9.2: an empty bundle read is treated as no read, like a nil one.
    func testAnEmptyBundleReadToggleUsesTheKnownApp() {
        let shell = makeShell(hint: FakeShiftHint())
        let a = Controller(shell, bundle: "com.example.A"), b = Controller(shell, bundle: "com.example.A")
        a.session.activate()
        tap(a)
        b.session.activate()
        b.client.bundle = ""
        tap(b)
        XCTAssertFalse(shell.asciiApps.contains(""))
        XCTAssertTrue(a.press(Keys.code(for: "h")), "A types: Chinese")
    }

    /// docs/spec/keys-and-composition.md §9.2: a client that never gave a bundle ID shares the `""` entry, as before.
    func testAClientThatNeverReadABundleIDSharesTheEmptyEntry() {
        let shell = makeShell(hint: FakeShiftHint())
        let c = Controller(shell, bundle: nil)
        c.session.activate()
        tap(c)
        XCTAssertEqual(shell.asciiApps, [""])
        tap(c)
        XCTAssertTrue(shell.asciiApps.isEmpty)
    }

    /// Where the core's ASCII state is not available (the Eten layout) the composition is sent first.
    func testWithoutAShadowTheCompositionIsSentFirst() {
        let c = Controller(makeShell(layout: MemoryLayoutStore("eten"), hint: FakeShiftHint()))
        c.session.activate()
        c.type(Layouts.keys("ㄋㄧˇ ㄏㄠˇ", eten: true))
        XCTAssertEqual(c.client.marked, "你好")
        tap(c)
        XCTAssertEqual(c.client.text, "你好")
        XCTAssertEqual(c.client.marked, "")
        XCTAssertFalse(c.press(Keys.code(for: "h")))
    }

    /// English mixing off, standard layout: the composition is kept (nothing is sent) and the next letter goes on it.
    func testWithMixingOffTheCompositionIsKept() {
        let c = Controller(makeShell(english: MemoryEnglishStore(false), hint: FakeShiftHint()))
        c.session.activate()
        c.type("su3cl3")
        XCTAssertEqual(c.client.marked, "你好")
        tap(c)
        XCTAssertEqual(c.client.text, "", "nothing sent")
        XCTAssertEqual(c.client.marked, "你好", "kept")
        XCTAssertTrue(c.press(Keys.code(for: "h")))
        XCTAssertEqual(c.client.marked, "你好h")
        XCTAssertEqual(c.client.text, "")
    }

    /// English mixing off: the unfinished syllable is dropped by the switch itself.
    func testWithMixingOffTheUnfinishedSyllableIsDropped() {
        let c = Controller(makeShell(english: MemoryEnglishStore(false), hint: FakeShiftHint()))
        c.session.activate()
        c.type("283" + "28")  // 打, then ㄉㄚ without a tone
        XCTAssertEqual(c.client.marked, "打ㄉㄚ")
        tap(c)
        XCTAssertEqual(c.client.marked, "打")
        XCTAssertEqual(c.client.text, "")
    }

    /// Eten, or the abbreviation composer: the composition is sent first, without its unfinished syllable (reset mode 2).
    func testTheCompositionSentFirstLeavesOutTheUnfinishedSyllable() {
        let eten = Controller(makeShell(layout: MemoryLayoutStore("eten"), hint: FakeShiftHint()))
        eten.session.activate()
        eten.type(Layouts.keys("ㄉㄚˇ", eten: true) + "da")
        XCTAssertEqual(eten.client.marked, "打ㄉㄚ")
        tap(eten)
        XCTAssertEqual(eten.client.text, "打", "no Zhuyin symbol is sent")
        XCTAssertEqual(eten.client.marked, "")
        let shell = makeShell(english: MemoryEnglishStore(false), hint: FakeShiftHint())
        shell.applyAbbreviation(true)
        let c = Controller(shell)
        c.session.activate()
        c.type("283" + "28")
        XCTAssertEqual(c.client.marked, "打ㄉㄚ")
        tap(c)
        XCTAssertEqual(c.client.text, "打", "no Zhuyin symbol is sent")
        XCTAssertEqual(c.client.marked, "")
    }

    /// English mixing off: an Option key in the English state with a composition reaches the core as its character, as with mixing on.
    func testOptionInEnglishWithMixingOffSendsTheCharacterToTheCore() {
        let c = Controller(makeShell(english: MemoryEnglishStore(false), hint: FakeShiftHint()))
        c.session.activate()
        c.type("su3cl3")
        tap(c)
        XCTAssertTrue(c.session.handle(option(";", shift: true, characters: "Ú")))
        XCTAssertEqual(c.client.marked, "你好:")
        XCTAssertEqual(c.client.text, "")
    }

    // MARK: 用哪一側的 Shift (docs/spec/keys-and-composition.md §9.1)

    /// A lone tap of the left (56) or right (60) Shift, with its device bit.
    private func tap(_ c: Controller, key: UInt16) {
        time += 5
        uptimeNow = time
        let bit: UInt = key == 56 ? 0x2 : 0x4
        _ = c.session.handle(Keys.event(key, flags: NSEvent.ModifierFlags(rawValue: NSEvent.ModifierFlags.shift.rawValue | bit), type: .flagsChanged, time: time))
        uptimeNow = time + 0.1
        _ = c.session.handle(Keys.event(key, flags: [], type: .flagsChanged, time: time + 0.1))
    }

    /// The stored side is read at start, kept when the toggle is switched off and on, and a change from the settings window reaches the
    /// running shell at the next release.
    func testShiftSideSurvivesRestartAndToggle() {
        let store = MemoryShiftToggleStore(true, side: "right")
        let shell = makeShell(shift: store, hint: FakeShiftHint())
        let c = Controller(shell)
        c.session.activate()
        /// Whether a tap of `key` switched; the state is put back when it did.
        func switches(_ key: UInt16) -> Bool {
            let before = shell.engineAscii
            tap(c, key: key)
            guard shell.engineAscii != before else { return false }
            tap(c, key: key)
            XCTAssertEqual(shell.engineAscii, before, "the second tap switches back")
            return true
        }
        XCTAssertEqual(shell.shiftSide, .right, "read from the store")
        XCTAssertFalse(switches(56), "left")
        XCTAssertTrue(switches(60), "right")
        shell.applyShiftToggle(false)
        shell.applyShiftToggle(true)
        XCTAssertFalse(switches(56), "left, after the toggle went off and on")
        XCTAssertTrue(switches(60), "right, after the toggle went off and on")
        SettingsModel(shell: shell).setShiftSide(.left)
        XCTAssertEqual(store.shiftSide, "left")
        XCTAssertFalse(switches(60), "right, after the change to left")
        XCTAssertTrue(switches(56), "left, after the change to left")
    }

    func testSettingOffStopsTheTapAndClearsEveryApp() {
        let store = MemoryShiftToggleStore(true)
        let hint = FakeShiftHint()
        let shell = makeShell(shift: store, hint: hint)
        XCTAssertTrue(shell.shiftToggleOn, "saved on")
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
        shift(c, down: true, at: 10)
        c.press(Keys.code(for: "a"), flags: .shift)
        shift(c, down: false, at: 10.1)
        XCTAssertTrue(hint.shown.isEmpty, "Shift+letter")
        shell.mouseAgo = { 0.05 }
        shift(c, down: true, at: 20)
        shift(c, down: false, at: 20.1)
        XCTAssertTrue(hint.shown.isEmpty, "Shift+click")
    }

    /// A release with no press seen (a secure field, an app switch) is nothing, and the next tap still switches.
    func testAnUnpairedReleaseDoesNotInvertLaterTaps() {
        let hint = FakeShiftHint()
        let c = Controller(makeShell(hint: hint))
        c.session.activate()
        shift(c, down: false, at: 50)
        XCTAssertTrue(hint.shown.isEmpty)
        tap(c)
        XCTAssertEqual(hint.shown.map(\.text), ["英"])
    }

    /// The hint is anchored at the composition cursor (the rectangle the candidate window asks for), not at the end of the text.
    func testTheHintAnchorsAtTheCompositionCursor() {
        let c = Controller(makeShell(hint: FakeShiftHint()))
        c.session.activate()
        c.type("su3cl3")
        c.press(123)  // Left: the cursor is after the first character
        tap(c)
        XCTAssertEqual(c.client.lineCursor, 1)
    }

    /// The bundle ID is a synchronous IPC: activate reads it once for the profile and the Shift state.
    func testActivateReadsTheBundleIDOnce() {
        let c = Controller(makeShell(hint: FakeShiftHint()))
        c.session.activate()
        XCTAssertEqual(c.client.bundleReads, 1)
    }

    /// An owner change by a key (no `activate`) hands the new app's state to the core: B is Chinese although A was English, and back.
    func testAKeyThatClaimsOwnershipSyncsTheState() {
        let shell = makeShell(hint: FakeShiftHint())
        let a = Controller(shell, bundle: "com.example.A"), b = Controller(shell, bundle: "com.example.B")
        a.session.activate()
        tap(a)
        XCTAssertTrue(b.press(Keys.code(for: "h")), "B's key claims the shell: B is Chinese")
        b.session.commitComposition()
        XCTAssertFalse(a.press(Keys.code(for: "h")), "A's key claims it back: A is English")
    }

    /// A rebuilt engine (layout, word pack) starts with set_ascii off in the core: `build()` sends the state again.
    func testRebuildSendsTheStateToTheNewEngine() {
        let shell = makeShell(hint: FakeShiftHint())
        let c = Controller(shell)
        c.session.activate()
        tap(c)
        shell.setAcgPack(false)
        XCTAssertEqual(shell.lastBuiltAscii, true)
    }

    /// The abbreviation composer takes the English run away (the core's `shadow_on()`); the shell then stops sending ASCII keys too, so
    /// Shift+1 is the keycap path's full-width mark as it was before the toggle.
    func testAsciiKeysAreNotSentWithoutAShadow() {
        let shell = makeShell(hint: FakeShiftHint())
        let c = Controller(shell)
        c.session.activate()
        c.type("su3cl3")
        tap(c)
        shell.applyAbbreviation(true)
        XCTAssertTrue(c.session.handle(Keys.event(Keys.code(for: "1"), flags: .shift, characters: "!")))
        XCTAssertEqual(c.client.marked, "你好！")
    }

    // MARK: Option in the English state (docs/spec/keys-and-composition.md §9.2, rule 1o)

    /// Option (and Shift) with a key of the main block, as the system sends it on the US layout (measured in a VM with HID-level events): `characters` is
    /// the Option character, `charactersIgnoringModifiers` the key's character with neither Option nor Shift (`;` for Option+Shift+;). The shell reads
    /// neither: it asks the event for the character with Shift alone (`characters(byApplyingModifiers:)`, which goes by the key code and the keyboard
    /// layout of the machine the test runs on; the expectations are the US layout's).
    private func option(_ key: Character, shift: Bool = false, extra: NSEvent.ModifierFlags = [], characters: String) -> NSEvent {
        Keys.event(Keys.code(for: key), flags: NSEvent.ModifierFlags.option.union(shift ? .shift : []).union(extra), characters: characters, ignoring: String(key))
    }

    func testOptionInEnglishWithNothingComposedTypesTheKeyWithoutOption() {
        let c = Controller(makeShell(hint: FakeShiftHint()))
        c.session.activate()
        tap(c)
        XCTAssertTrue(c.session.handle(option(";", shift: true, characters: "Ú")), "eaten")
        XCTAssertEqual(c.client.text, ":")
        XCTAssertTrue(c.session.handle(option(";", characters: "…")))
        XCTAssertTrue(c.session.handle(option("a", characters: "å")))
        XCTAssertEqual(c.client.text, ":;a")
        XCTAssertEqual(c.client.marked, "", "sent, not composed")
        XCTAssertEqual(c.client.calls, [.insert(":"), .insert(";"), .insert("a")])
        // Outside the rule: with Command, Control or Caps Lock, a key outside the main block, a result that is not one ASCII character.
        for extra: NSEvent.ModifierFlags in [.command, .control, .capsLock] {
            XCTAssertFalse(c.session.handle(option(";", extra: extra, characters: "…")))
        }
        XCTAssertFalse(c.session.handle(Keys.event(123, flags: .option, characters: "\u{F702}")), "Option+Left")
        XCTAssertFalse(c.session.handle(Keys.event(Keys.space, flags: .option, characters: "\u{A0}", ignoring: " ")), "Option+Space")
        XCTAssertTrue(c.session.handle(option(",", shift: true, characters: "¯")))
        XCTAssertTrue(c.session.handle(option("a", shift: true, characters: "Å")))
        XCTAssertEqual(c.client.text, ":;a<A")
        XCTAssertFalse(c.session.handle(Keys.event(Keys.keypad0, flags: .option, characters: "0")), "a keypad digit")
        XCTAssertFalse(c.session.handle(Keys.event(Keys.code(for: ";"), characters: ";")), "the same key without Option is passed on as before")
        XCTAssertEqual(c.client.text, ":;a<A")
    }

    func testOptionInEnglishWithACompositionSendsTheCharacterToTheCore() {
        let c = Controller(makeShell(hint: FakeShiftHint()))
        c.session.activate()
        c.type("su3cl3")
        tap(c)
        let colon = option(";", shift: true, characters: "Ú")
        XCTAssertEqual(Session.asciiKey(colon)?.kind, KeyMap.char)
        XCTAssertEqual(Session.asciiKey(colon)?.ch, UInt32(UnicodeScalar(":").value))
        XCTAssertEqual(Session.asciiKey(colon)?.modifiers, 0)
        XCTAssertEqual(Session.asciiKey(option(";", characters: "…"))?.ch, UInt32(UnicodeScalar(";").value))
        XCTAssertNil(Session.asciiKey(Keys.event(123, flags: .option, characters: "\u{F702}")), "Option+Left is not a key of the rule")
        XCTAssertTrue(c.session.handle(colon))
        XCTAssertEqual(c.client.marked, "你好:")
        XCTAssertTrue(c.session.handle(option("a", characters: "å")))
        XCTAssertEqual(c.client.marked, "你好:a")
        XCTAssertEqual(c.client.text, "")
    }

    func testOptionInEnglishWithoutAShadowIsEatenAndChangesNothing() {
        let shell = makeShell(hint: FakeShiftHint())
        let c = Controller(shell)
        c.session.activate()
        c.type("su3")
        tap(c)
        shell.applyAbbreviation(true)
        XCTAssertTrue(shell.engineAscii && !shell.asciiOn && shell.composing, "the state the rule is about")
        let before = c.client.calls
        XCTAssertTrue(c.session.handle(option(";", characters: "…")))
        XCTAssertTrue(c.session.handle(option(";", shift: true, characters: "Ú")))
        XCTAssertEqual(c.client.calls, before, "nothing sent, nothing marked")
        XCTAssertEqual(c.client.marked, "你")
        XCTAssertEqual(c.client.text, "")
        XCTAssertTrue(c.press(Keys.enter))
        XCTAssertEqual(c.client.text, "你")
    }

    /// Chinese state: the key goes to the core as its keycap with the Option bit (rule 1o), and the full-width character is composed.
    func testOptionInChineseComposesTheFullWidthCharacter() {
        let c = Controller(makeShell(hint: FakeShiftHint()))
        c.session.activate()
        XCTAssertTrue(c.session.handle(option(";", shift: true, characters: "Ú")))
        XCTAssertEqual(c.client.marked, "：")
        XCTAssertTrue(c.session.handle(option(";", characters: "…")))
        XCTAssertEqual(c.client.marked, "：；")
        XCTAssertEqual(c.client.text, "")
        XCTAssertTrue(c.press(Keys.enter))
        XCTAssertEqual(c.client.text, "：；")
    }

    func testARightShiftTapSwitchesToo() {
        let hint = FakeShiftHint()
        let c = Controller(makeShell(hint: hint))
        c.session.activate()
        time += 5
        uptimeNow = time
        c.session.handle(Keys.event(60, flags: NSEvent.ModifierFlags(rawValue: NSEvent.ModifierFlags.shift.rawValue | 0x4), type: .flagsChanged, time: time))
        uptimeNow = time + 0.1
        c.session.handle(Keys.event(60, flags: [], type: .flagsChanged, time: time + 0.1))
        XCTAssertEqual(hint.shown.map(\.text), ["英"])
    }

    /// Turning the setting off pushes English off without asking the client for its bundle ID.
    func testSettingOffDoesNotReadTheBundleID() {
        let shell = makeShell(hint: FakeShiftHint())
        let c = Controller(shell)
        c.session.activate()
        tap(c)
        let reads = c.client.bundleReads
        shell.applyShiftToggle(false)
        XCTAssertEqual(c.client.bundleReads, reads)
        XCTAssertFalse(shell.engineAscii)
    }
}
