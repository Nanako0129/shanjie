import AppKit
import XCTest
@testable import ShanjieKit

/// docs/spec/macos-shell.md §13: the in-memory diagnostic log.
@MainActor
final class DiagLogTests: XCTestCase {
    private var resources: URL!
    private var time: TimeInterval = 100
    private var uptimeNow: TimeInterval = 0

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    private func makeShell(shiftToggle: Bool = true, hint: FakeShiftHint = FakeShiftHint()) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(),
                          quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(),
                          profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(),
                          shiftToggleStore: MemoryShiftToggleStore(shiftToggle), shiftHint: hint)
        shell.mouseAgo = { 1000 }
        shell.uptime = { [unowned self] in self.uptimeNow }
        XCTAssertNotNil(shell.engine)
        return shell
    }

    /// A left Shift event as the system sends it: the generic bit and the left device bit while down, no bits when up.
    private func shift(_ c: Controller, down: Bool) {
        uptimeNow = time
        let flags: NSEvent.ModifierFlags = down ? NSEvent.ModifierFlags(rawValue: NSEvent.ModifierFlags.shift.rawValue | 0x2) : []
        c.session.handle(Keys.event(56, flags: flags, type: .flagsChanged, time: time))
    }

    private func tap(_ c: Controller) {
        time += 5
        shift(c, down: true)
        time += 0.1
        shift(c, down: false)
    }

    /// A key event that types `ch` (with its characters, so it counts as a typing key).
    private func typeKey(_ c: Controller, _ ch: Character) {
        c.session.handle(Keys.event(Keys.code(for: ch), characters: String(ch)))
    }

    // MARK: k. client reads

    /// The reads each step makes of both clients together: bundle ID, the three text reads (`reads`), caret rectangle, line rectangle.
    private func readSteps(shiftToggle: Bool) -> [String: [Int]] {
        let shell = makeShell(shiftToggle: shiftToggle)
        let a = Controller(shell, bundle: "com.example.A"), b = Controller(shell, bundle: "com.example.B")
        var out: [String: [Int]] = [:]
        func counts() -> [Int] {
            [a.client.bundleReads + b.client.bundleReads, a.client.reads + b.client.reads,
             a.client.caretAsks + b.client.caretAsks, a.client.lineAsks + b.client.lineAsks]
        }
        func step(_ name: String, _ body: () -> Void) {
            let before = counts()
            body()
            out[name] = zip(counts(), before).map { $0 - $1 }
        }
        step("activate") { a.session.activate() }
        step("key s") { typeKey(a, "s") }
        step("key u") { typeKey(a, "u") }
        step("key 3") { typeKey(a, "3") }
        step("commitComposition") { a.session.commitComposition() }
        step("tap 1") { tap(a) }
        step("key h (English)") { typeKey(a, "h") }
        step("tap 2") { tap(a) }
        step("key s again") { typeKey(a, "s") }
        step("tap 3 (composing)") { tap(a) }
        step("tap 4 (composing)") { tap(a) }
        step("toggleAppChat") { a.session.toggleAppChat() }
        step("key b claims") { typeKey(b, "s") }
        step("deactivate a (not owner)") { a.session.deactivate() }
        step("deactivate b (owner)") { b.session.deactivate() }
        step("activate a") { a.session.activate() }
        return out
    }

    /// The client reads of activate, deactivate, commitComposition, the Shift toggle, toggleAppChat and the keys, with the Shift
    /// toggle on and off. The numbers were measured before the diagnostic log existed and must not change with it: recording
    /// adds no client read (docs/spec/macos-shell.md §13).
    func testRecordingAddsNoClientRead() {
        // [bundleReads, reads, caretAsks, lineAsks]
        // Measured on the shell before the diagnostic log (origin/main 12a85fb4), 2026-11-10. Never edit these to make a test pass.
        let on: [String: [Int]] = [
            "activate": [1, 0, 0, 0], "key s": [1, 2, 0, 2], "key u": [1, 0, 0, 1], "key 3": [1, 0, 0, 1], "commitComposition": [1, 0, 0, 0],
            "tap 1": [1, 0, 1, 0], "key h (English)": [0, 0, 0, 0], "tap 2": [1, 0, 1, 0], "key s again": [1, 3, 0, 1],
            "tap 3 (composing)": [1, 0, 0, 2], "tap 4 (composing)": [1, 0, 0, 1], "toggleAppChat": [1, 0, 0, 0], "key b claims": [3, 2, 0, 2],
            "deactivate a (not owner)": [0, 0, 0, 0], "deactivate b (owner)": [1, 0, 0, 0], "activate a": [1, 0, 0, 0],
        ]
        let off: [String: [Int]] = [
            "activate": [1, 0, 0, 0], "key s": [1, 2, 0, 1], "key u": [1, 0, 0, 1], "key 3": [1, 0, 0, 1], "commitComposition": [1, 0, 0, 0],
            "tap 1": [0, 0, 0, 0], "key h (English)": [1, 3, 0, 1], "tap 2": [0, 0, 0, 0], "key s again": [1, 0, 0, 1],
            "tap 3 (composing)": [0, 0, 0, 0], "tap 4 (composing)": [0, 0, 0, 0], "toggleAppChat": [1, 0, 0, 0], "key b claims": [3, 2, 0, 1],
            "deactivate a (not owner)": [0, 0, 0, 0], "deactivate b (owner)": [1, 0, 0, 0], "activate a": [1, 0, 0, 0],
        ]
        XCTAssertEqual(readSteps(shiftToggle: true), on, "toggle on")
        XCTAssertEqual(readSteps(shiftToggle: false), off, "toggle off")
    }

    // MARK: helpers for the log

    private func lines(_ shell: Shell) -> [String] {
        shell.diag.export(version: "0.0.0").split(separator: "\n").map(String.init)
    }

    /// The event part of each line after the header (the time dropped).
    private func events(_ shell: Shell) -> [String] {
        lines(shell).dropFirst().map { String($0.drop(while: { $0 != " " }).dropFirst()) }
    }

    private static let bundlePattern = #"(-|\?|[A-Za-z0-9._-]{1,255})"#
    private static let outcomePattern = "(" + ShiftTap.Outcome.allCases.map(\.rawValue).joined(separator: "|") + ")"
    /// docs/spec/macos-shell.md §13.2: the closed grammar of an exported line.
    private static let headerLine = try! NSRegularExpression(
        pattern: #"^shanjie [A-Za-z0-9._-]+ macOS [0-9]+\.[0-9]+\.[0-9]+ exported [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"#)
    private static let eventLine = try! NSRegularExpression(pattern: "^-[0-9]+\\.[0-9]{3} (shift (left|right) \(outcomePattern)"
        + "|toggle \(bundlePattern) (en|zh) (hint|nohint)|(activate|deactivate) \(bundlePattern) (owner|notowner) (en|zh)"
        + "|owner \(bundlePattern)|ascii \(bundlePattern) (en|zh)|flags \(bundlePattern) (all|some|none))$")

    private static func matches(_ re: NSRegularExpression, _ s: String) -> Bool {
        re.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) != nil
    }

    /// A Shift event with explicit device bits (0x2 left, 0x4 right) and other flags.
    private func flags(_ c: Controller, _ code: UInt16, _ raw: UInt, extra: NSEvent.ModifierFlags = []) {
        uptimeNow = time
        var f = NSEvent.ModifierFlags(rawValue: raw).union(extra)
        if raw & 0x6 != 0 { f.insert(.shift) }
        c.session.handle(Keys.event(code, flags: f, type: .flagsChanged, time: time))
    }

    // MARK: a. buffer limits

    func testEachBufferKeepsItsNewestEntries() {
        let shell = makeShell()
        var t = 0.0
        shell.diag.clock = { t }
        for i in 0...DiagLog.shiftLimit { t = Double(i); shell.diag.record(.shift(right: i == 0, outcome: .toggled)) }
        for i in 0...DiagLog.otherLimit { t = Double(i); shell.diag.record(.owner(bundle: i == 0 ? "com.first" : "com.next")) }
        let ev = events(shell)
        XCTAssertEqual(ev.filter { $0.hasPrefix("shift ") }.count, DiagLog.shiftLimit)
        XCTAssertEqual(ev.filter { $0.hasPrefix("owner ") }.count, DiagLog.otherLimit)
        XCTAssertFalse(ev.contains("shift right toggled"), "the oldest Shift entry is dropped")
        XCTAssertFalse(ev.contains("owner com.first"), "the oldest other entry is dropped")
    }

    // MARK: b. format

    func testEveryEventHasItsFixedFormat() {
        let shell = makeShell()
        var t = 10.0
        shell.diag.clock = { t }
        shell.diag.record(.shift(right: false, outcome: .toggled))
        t = 11
        shell.diag.record(.toggle(bundle: "com.a", english: true, hintShown: false))
        t = 12.5
        shell.diag.record(.activate(bundle: DiagLog.clean(nil), owner: true, english: false))
        shell.diag.record(.deactivate(bundle: "com.a", owner: false, english: true))
        shell.diag.record(.owner(bundle: "com.b"))
        shell.diag.record(.ascii(bundle: "-", english: false))
        t = 20
        let l = lines(shell)
        XCTAssertTrue(Self.matches(Self.headerLine, l[0]), l[0])
        XCTAssertEqual(Array(l.dropFirst()), [
            "-10.000 shift left toggled", "-9.000 toggle com.a en nohint", "-7.500 activate - owner zh", "-7.500 deactivate com.a notowner en",
            "-7.500 owner com.b", "-7.500 ascii - zh",
        ])
    }

    // MARK: c. nothing typed is recorded

    func testTypedTextNeverReachesTheLog() {
        let shell = makeShell()
        let c = Controller(shell)
        c.session.activate()
        let marker = "quixoticzebra"
        var before = lines(shell).count
        // 1. Chinese composition sent by Enter.
        c.type(marker)
        c.press(Keys.enter)
        XCTAssertEqual(lines(shell).count, before, "typing in Chinese adds no event")
        // 2. English with a composition open: the asciiKey path.
        c.type("su3")
        tap(c)
        before = lines(shell).count
        for ch in marker { typeKey(c, ch) }
        XCTAssertTrue(c.client.marked.contains(marker), "the asciiKey path did not run")
        // 3. commitComposition sends it.
        c.session.commitComposition()
        XCTAssertEqual(lines(shell).count, before, "the English keys and commitComposition add no event")
        let text = shell.diag.export(version: "0.0.0")
        let scalars = Array(marker)
        for n in 4...scalars.count {
            for i in 0...(scalars.count - n) {
                XCTAssertFalse(text.contains(String(scalars[i..<i + n])), "a piece of the typed text is in the log")
            }
        }
        XCTAssertFalse(text.unicodeScalars.contains { (0x3100...0x312F).contains($0.value) || "ˊˇˋ˙".unicodeScalars.contains($0) },
                       "a Zhuyin symbol is in the log")
        let l = lines(shell)
        XCTAssertTrue(Self.matches(Self.headerLine, l[0]), l[0])
        for line in l.dropFirst() { XCTAssertTrue(Self.matches(Self.eventLine, line), line) }
    }

    // MARK: d. the switch

    func testTheSwitchIsOnUntilTurnedOffAndOffEmptiesTheLog() {
        let store = MemoryDiagLogStore()
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(),
                          quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(),
                          profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(),
                          diagLogStore: store)
        XCTAssertTrue(shell.diag.enabled, "never chosen is on")
        let c = Controller(shell)
        c.session.activate()
        XCTAssertFalse(shell.diag.isEmpty)
        shell.diag.enabled = false
        XCTAssertTrue(shell.diag.isEmpty, "off empties the log")
        XCTAssertEqual(store.diagLog, false)
        c.session.deactivate()
        c.session.activate()
        shell.diag.record(.shift(right: false, outcome: .toggled))
        XCTAssertTrue(shell.diag.isEmpty, "nothing is recorded while off")
        let rebuilt = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil,
                            dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                            abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(),
                            quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(),
                            candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(),
                            profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(),
                            diagLogStore: store)
        XCTAssertFalse(rebuilt.diag.enabled, "the stored choice survives a new Shell")
        shell.diag.enabled = true
        XCTAssertTrue(shell.diag.isEmpty, "on starts from empty")
    }

    // MARK: e. a tap

    func testATapRecordsTheShiftAndTheToggle() {
        let hint = FakeShiftHint()
        let shell = makeShell(hint: hint)
        let c = Controller(shell, bundle: "com.example.A")
        c.session.activate()
        tap(c)
        XCTAssertEqual(Array(events(shell).suffix(3)), ["shift left toggled", "ascii com.example.A en", "toggle com.example.A en hint"])
        c.client.caret = nil  // no rectangle: no hint
        tap(c)
        XCTAssertEqual(events(shell).last, "toggle com.example.A zh nohint")
    }

    // MARK: e2. every reason

    func testEveryReasonIsRecorded() {
        // keyCodes: 56 left Shift, 60 right Shift, 59 Control. Flags: 0x2 left down, 0x4 right down.
        let sequences: [ShiftTap.Outcome: (Controller, Shell) -> Void] = [
            .modifierAtPress: { c, _ in self.flags(c, 56, 0x2, extra: .control); self.time += 0.1; self.flags(c, 56, 0) },
            .capsLockAtPress: { c, _ in self.flags(c, 56, 0x2, extra: .capsLock); self.time += 0.1; self.flags(c, 56, 0) },
            .modifierBetween: { c, _ in
                self.flags(c, 56, 0x2); self.flags(c, 59, 0x2, extra: .control); self.flags(c, 59, 0x2); self.time += 0.1; self.flags(c, 56, 0)
            },
            .otherSideChanged: { c, _ in self.flags(c, 56, 0x6); self.time += 0.1; self.flags(c, 56, 0) },
            .tooLong: { c, _ in self.flags(c, 56, 0x2); self.time += ShiftTap.SHIFT_TAP_MAX + 0.1; self.flags(c, 56, 0) },
            .mouse: { c, s in s.mouseAgo = { 0.05 }; self.flags(c, 56, 0x2); self.time += 0.1; self.flags(c, 56, 0); s.mouseAgo = { 1000 } },
            .noPress: { c, _ in self.flags(c, 56, 0) },
            .otherKeyReleased: { c, _ in self.flags(c, 56, 0x2); self.flags(c, 60, 0x6); self.time += 0.1; self.flags(c, 56, 0x4) },
            .modifierAtRelease: { c, _ in self.flags(c, 56, 0x2); self.time += 0.1; self.flags(c, 56, 0, extra: .control) },
            .capsLockAtRelease: { c, _ in self.flags(c, 56, 0x2); self.time += 0.1; self.flags(c, 56, 0, extra: .capsLock) },
        ]
        XCTAssertEqual(Set(sequences.keys), Set(ShiftTap.Outcome.allCases).subtracting([.toggled]), "a reason without a test")
        for (outcome, run) in sequences {
            let shell = makeShell()
            let c = Controller(shell)
            c.session.activate()
            time += 5
            run(c, shell)
            XCTAssertEqual(events(shell).filter { $0.hasPrefix("shift ") }.last, "shift left \(outcome.rawValue)", outcome.rawValue)
            XCTAssertFalse(events(shell).contains { $0.hasPrefix("toggle ") }, "\(outcome.rawValue) switched")
        }
    }

    /// docs/spec/macos-shell.md §13.1: when several reasons hold, the first one is recorded.
    func testTheFirstReasonWins() {
        let cases: [(ShiftTap.Outcome, String, (Controller) -> Void)] = [
            (.modifierAtPress, "Control at the press, then too long", { c in
                self.flags(c, 56, 0x2, extra: .control); self.time += ShiftTap.SHIFT_TAP_MAX + 0.1; self.flags(c, 56, 0)
            }),
            (.modifierAtPress, "Control and Caps Lock at the press", { c in
                self.flags(c, 56, 0x2, extra: [.control, .capsLock]); self.time += 0.1; self.flags(c, 56, 0)
            }),
            (.capsLockAtPress, "Caps Lock at the press, then another modifier", { c in
                self.flags(c, 56, 0x2, extra: .capsLock); self.flags(c, 59, 0x2, extra: [.control, .capsLock]); self.flags(c, 59, 0x2, extra: .capsLock)
                self.time += 0.1; self.flags(c, 56, 0, extra: .capsLock)
            }),
            (.modifierBetween, "a modifier between, then too long with Control at the release", { c in
                self.flags(c, 56, 0x2); self.flags(c, 59, 0x2, extra: .control); self.time += ShiftTap.SHIFT_TAP_MAX + 0.1
                self.flags(c, 56, 0, extra: .control)
            }),
            (.otherSideChanged, "the other side changed, and Control at the release", { c in
                self.flags(c, 56, 0x6); self.time += 0.1; self.flags(c, 56, 0, extra: .control)
            }),
            (.modifierAtRelease, "Control and Caps Lock at the release, too long", { c in
                self.flags(c, 56, 0x2); self.time += ShiftTap.SHIFT_TAP_MAX + 0.1; self.flags(c, 56, 0, extra: [.control, .capsLock])
            }),
            (.capsLockAtRelease, "Caps Lock at the release, too long", { c in
                self.flags(c, 56, 0x2); self.time += ShiftTap.SHIFT_TAP_MAX + 0.1; self.flags(c, 56, 0, extra: .capsLock)
            }),
            (.tooLong, "too long and a click", { c in
                c.session.shell.mouseAgo = { 0.05 }; self.flags(c, 56, 0x2); self.time += ShiftTap.SHIFT_TAP_MAX + 0.1; self.flags(c, 56, 0)
            }),
        ]
        for (outcome, name, run) in cases {
            let shell = makeShell()
            let c = Controller(shell)
            c.session.activate()
            time += 5
            run(c)
            XCTAssertEqual(events(shell).filter { $0.hasPrefix("shift ") }.last, "shift left \(outcome.rawValue)", name)
        }
    }

    // MARK: f. secure input

    func testNoShiftDuringSecureInputButTheToggleIsKept() {
        var secure = true
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { secure }, layoutStore: MemoryLayoutStore(), learningDirectory: nil,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(),
                          quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(),
                          profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(),
                          shiftToggleStore: MemoryShiftToggleStore(true))
        shell.mouseAgo = { 1000 }
        shell.uptime = { [unowned self] in self.uptimeNow }
        let c = Controller(shell)
        c.session.activate()
        tap(c)
        time += 5
        shift(c, down: true)
        c.press(Keys.code(for: "a"), flags: .shift)
        time += 0.1
        shift(c, down: false)
        XCTAssertFalse(events(shell).contains { $0.hasPrefix("shift ") }, "a Shift was recorded during secure input")
        XCTAssertTrue(events(shell).contains { $0.hasPrefix("toggle ") }, "the switch itself is recorded")
        secure = false
        tap(c)
        XCTAssertTrue(events(shell).contains { $0.hasPrefix("shift ") }, "positive control: recorded once secure input ends")
    }

    // MARK: g. a capital

    func testAShiftInterruptedByAKeyRecordsNothing() {
        let shell = makeShell()
        let c = Controller(shell)
        c.session.activate()
        let before = lines(shell)
        time += 5
        shift(c, down: true)
        c.press(Keys.code(for: "a"), flags: .shift)
        time += 0.1
        shift(c, down: false)
        XCTAssertEqual(lines(shell).count, before.count)
    }

    /// docs/spec/macos-shell.md §13.1: with the two Shift keys overlapping, a key between press and release still records nothing (the press
    /// record of the interrupted side is replaced by the other side's), nor does a Shift held since before the tap saw its press.
    func testOverlappingShiftKeysWithAKeyRecordNothing() {
        let sequences: [(String, (Controller) -> Void)] = [
            ("left, a, right, up left, b, up right", { c in
                self.flags(c, 56, 0x2); c.press(Keys.code(for: "a"), flags: .shift); self.flags(c, 60, 0x6); self.time += 0.1
                self.flags(c, 56, 0x4); c.press(Keys.code(for: "b"), flags: .shift); self.flags(c, 60, 0)
            }),
            ("left, right, a, up left, up right", { c in
                self.flags(c, 56, 0x2); self.flags(c, 60, 0x6); c.press(Keys.code(for: "a"), flags: .shift); self.time += 0.1
                self.flags(c, 56, 0x4); self.flags(c, 60, 0)
            }),
            ("held before, a, up", { c in c.press(Keys.code(for: "a"), flags: .shift); self.flags(c, 56, 0) }),
        ]
        for (name, run) in sequences {
            let shell = makeShell()
            let c = Controller(shell)
            c.session.activate()
            let before = events(shell).filter { $0.hasPrefix("shift ") }
            time += 5
            run(c)
            XCTAssertEqual(events(shell).filter { $0.hasPrefix("shift ") }, before, name)
            time += 5
            tap(c)
            XCTAssertEqual(events(shell).filter { $0.hasPrefix("shift ") }.last, "shift left toggled", "\(name): the next tap is recorded")
        }
    }

    /// The switch turned off outside the settings window (the store alone, as `defaults write` does): the log reads as empty at once and the
    /// next event empties it.
    func testOffInTheStoreAloneEmptiesTheLog() {
        let store = MemoryDiagLogStore()
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(),
                          quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(),
                          profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(),
                          diagLogStore: store)
        Controller(shell).session.activate()
        XCTAssertFalse(shell.diag.isEmpty)
        store.diagLog = false
        XCTAssertTrue(shell.diag.isEmpty)
        XCTAssertEqual(lines(shell).count, 1, "the export has the header only")
        shell.diag.record(.owner(bundle: "com.x"))
        store.diagLog = true
        XCTAssertTrue(shell.diag.isEmpty, "the old entries are gone once it is on again")
    }

    private func makeShell(store: DiagLogStore) -> Shell {
        Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil,
              dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
              abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(),
              quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(),
              candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(),
              profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(),
              diagLogStore: store)
    }

    /// docs/spec/macos-shell.md §13.3 (a, a2, b): once turned off, the entries never come back: not when the change notification
    /// (`storeChanged()`) is handled after it went on again, not when it is handled with the store still on (off and on both landed first),
    /// not when the settings window turns it on with no event in between.
    func testOldEntriesNeverComeBackAfterAnOff() {
        let viaNotificationAfterOn: (MemoryDiagLogStore, Shell) -> Void = { store, shell in
            store.diagLog = false; shell.diag.storeChanged(); store.diagLog = true
        }
        let offAndOnBeforeTheNotification: (MemoryDiagLogStore, Shell) -> Void = { store, shell in
            store.diagLog = false; store.diagLog = true; shell.diag.storeChanged()
        }
        let onFromSettings: (MemoryDiagLogStore, Shell) -> Void = { store, shell in store.diagLog = false; shell.diag.enabled = true }
        for (name, change) in [("a", viaNotificationAfterOn), ("a2", offAndOnBeforeTheNotification), ("b", onFromSettings)] {
            let store = MemoryDiagLogStore()
            let shell = makeShell(store: store)
            Controller(shell).session.activate()
            XCTAssertFalse(shell.diag.isEmpty, name)
            change(store, shell)
            XCTAssertTrue(shell.diag.enabled, name)
            XCTAssertTrue(shell.diag.isEmpty, "\(name): the old entries are gone")
            XCTAssertEqual(lines(shell).count, 1, "\(name): the export has the header only")
        }
    }

    /// (g) The preview sheet was open when the log was emptied (turned off outside the window): 複製 copies nothing.
    func testAPreviewTakenBeforeAnEmptyingIsNotCopied() {
        let store = MemoryDiagLogStore()
        let shell = makeShell(store: store)
        Controller(shell).session.activate()
        let model = SettingsModel(shell: shell)
        model.openDiagPreview()
        XCTAssertNotNil(model.diagPreview)
        store.diagLog = false
        shell.diag.storeChanged()
        let pb = FakePasteboard()
        model.copyDiagPreview(to: pb)
        XCTAssertEqual(pb.calls, [], "nothing reaches the pasteboard")
        XCTAssertNil(model.diagPreview, "the sheet closes")
    }

    /// docs/spec/macos-shell.md §13.1 (c): with the right Shift's bit stuck on, a capital typed with the left Shift records nothing, and a
    /// clean tap afterwards lets the next release that is not a tap be recorded again.
    func testAStaleOtherSideBitDoesNotStopTheRecordsForGood() {
        let shell = makeShell()
        let c = Controller(shell)
        c.session.activate()
        func shifts() -> [String] { events(shell).filter { $0.hasPrefix("shift ") } }
        time += 5
        flags(c, 56, 0x6); c.press(Keys.code(for: "a"), flags: .shift); time += 0.1; flags(c, 56, 0x4)
        XCTAssertEqual(shifts(), [], "the capital records nothing")
        time += 5
        flags(c, 56, 0x6); time += 0.1; flags(c, 56, 0x4)
        XCTAssertEqual(shifts(), ["shift left toggled"])
        time += 5
        flags(c, 56, 0x6); time += ShiftTap.SHIFT_TAP_MAX + 0.1; flags(c, 56, 0x4)
        XCTAssertEqual(shifts(), ["shift left toggled", "shift left tooLong"], "recorded again after the clean tap")
    }

    /// (e) The clean release that clears the flag is itself judged with the flag as it was: left down, a key, right down, right up too
    /// late — the right release is not a tap and is not recorded.
    func testTheCleanReleaseItselfIsJudgedWithTheFlagAsItWas() {
        let shell = makeShell()
        let c = Controller(shell)
        c.session.activate()
        time += 5
        flags(c, 56, 0x2); c.press(Keys.code(for: "a"), flags: .shift); flags(c, 60, 0x6); time += ShiftTap.SHIFT_TAP_MAX + 0.1; flags(c, 60, 0x2)
        XCTAssertEqual(events(shell).filter { $0.hasPrefix("shift ") }, [])
    }

    // MARK: h. bundle IDs

    func testABundleIDOutsideTheAllowedCharactersIsAQuestionMark() {
        XCTAssertEqual(DiagLog.clean("com.apple.TextEdit"), "com.apple.TextEdit")
        XCTAssertEqual(DiagLog.clean(nil), "-")
        XCTAssertEqual(DiagLog.clean(String(repeating: "a", count: 255)), String(repeating: "a", count: 255))
        for bad in ["com.a\nb", "com.[x](y)", "com.a b", "com.例子", String(repeating: "a", count: 300)] {
            let shell = makeShell()
            let c = Controller(shell, bundle: bad)
            c.session.activate()
            XCTAssertEqual(events(shell).last, "activate ? owner zh")
        }
    }

    // MARK: i, j. copying

    func testTheCopyIsForThisMacOnlyAndTransient() {
        let pb = FakePasteboard()
        DiagLog.copy("x\n", to: pb)
        XCTAssertEqual(pb.calls, ["prepare currentHostOnly", "item org.nspasteboard.TransientType public.utf8-plain-text"],
                       "cleared for this Mac only, then one item with both types")
        XCTAssertEqual(pb.strings[.string], "x\n")
    }

    func testThePreviewIsWhatIsCopiedAndAnEmptyLogGreysTheButton() throws {
        let shell = makeShell()
        let model = SettingsModel(shell: shell)
        XCTAssertTrue(model.diagLogEmpty)
        let c = Controller(shell)
        c.session.activate()
        model.refreshExternal()
        XCTAssertFalse(model.diagLogEmpty)
        model.openDiagPreview()
        let preview = try XCTUnwrap(model.diagPreview)
        tap(c)  // events after the sheet opened are not in what it copies
        let pb = FakePasteboard()
        model.copyDiagPreview(to: pb)
        XCTAssertEqual(pb.strings[.string].map { Array($0.utf8) }, Array(preview.utf8), "the copy is the text the sheet showed")
        XCTAssertNil(model.diagPreview, "複製 closes the sheet")
        model.setDiagLog(false)
        XCTAssertTrue(model.diagLogEmpty)
        XCTAssertFalse(model.diagLog)
    }

    // MARK: l. flags (docs/spec/macos-shell.md §13.1): were the modifier events delivered

    /// What the injected system counter returns.
    private var system: UInt32 = 0
    private var secure = false

    private func flagsShell(shiftToggle: Bool = true, store: DiagLogStore = MemoryDiagLogStore(), resources: URL? = nil) -> Shell {
        let shell = Shell(resources: resources ?? self.resources, panel: FakePanel(), isSecureInput: { [unowned self] in self.secure },
                          layoutStore: MemoryLayoutStore(), learningDirectory: nil,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(),
                          quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(),
                          profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(),
                          shiftToggleStore: MemoryShiftToggleStore(shiftToggle), diagLogStore: store,
                          flagsCounter: { [unowned self] in self.system })
        shell.mouseAgo = { 1000 }
        shell.uptime = { [unowned self] in self.uptimeNow }
        return shell
    }

    /// `n` modifier events that are no Shift tap (Control down), given to `c`'s session.
    private func mods(_ c: Controller, _ n: Int) {
        for _ in 0..<n { flags(c, 59, 0, extra: .control) }
    }

    /// The `flags` lines, after checking every line against the closed grammar.
    private func flagLines(_ shell: Shell, file: StaticString = #filePath, line: UInt = #line) -> [String] {
        for l in lines(shell).dropFirst() { XCTAssertTrue(Self.matches(Self.eventLine, l), l, file: file, line: line) }
        return events(shell).filter { $0.hasPrefix("flags ") }
    }

    /// One interval of a fresh shell (its first activate to its deactivate): the system counts `counted`, the input method gets `seen`.
    private func oneInterval(counted: UInt32, seen: Int) -> [String] {
        let shell = flagsShell()
        let c = Controller(shell, bundle: "com.example.A")
        c.session.activate()
        system &+= counted
        mods(c, seen)
        c.session.deactivate()
        return flagLines(shell)
    }

    func testFlagsComparesTheSystemCountWithTheEventsReceived() {
        system = 1000  // the counter is already running when the process starts: the first interval is recorded like any other
        XCTAssertEqual(oneInterval(counted: 6, seen: 6), ["flags com.example.A all"])
        XCTAssertEqual(oneInterval(counted: 6, seen: 0), ["flags com.example.A none"])
        XCTAssertEqual(oneInterval(counted: 6, seen: 2), ["flags com.example.A some"])
        XCTAssertEqual(oneInterval(counted: 6, seen: 9), ["flags com.example.A all"], "more received than counted (a repeated delivery)")
        XCTAssertEqual(oneInterval(counted: 0, seen: 0), [], "the system saw none")
        XCTAssertEqual(oneInterval(counted: 0, seen: 3), [], "the system saw none")
        XCTAssertEqual(DiagLog.Event.flags(bundle: "-", delivery: .nothing).line, "flags - none")
    }

    func testFlagsCountsAcrossAWrapOfTheCounter() {
        for (seen, expected) in [(6, "all"), (5, "some")] {
            system = UInt32.max - 1
            let shell = flagsShell()
            let c = Controller(shell, bundle: "com.example.A")
            c.session.activate()
            system = 4  // six later
            mods(c, seen)
            c.session.deactivate()
            XCTAssertEqual(flagLines(shell), ["flags com.example.A \(expected)"])
        }
        XCTAssertEqual(DiagLog.delivery(system: 4 &- (UInt32.max - 1), seen: 2 &- (UInt32.max - 3)), .all, "both wrapped: 6 and 6")
    }

    /// The `flags` line comes right after its `deactivate` line, with the same time and bundle.
    func testFlagsIsRecordedAtTheMomentOfTheDeactivate() {
        let shell = flagsShell()
        var t = 0.0
        shell.diag.clock = { t += 1; return t }  // every reading differs
        let c = Controller(shell, bundle: "com.example.A")
        c.session.activate()
        system += 2
        mods(c, 2)
        c.session.deactivate()
        let last = Array(lines(shell).suffix(2))
        XCTAssertEqual(last.map { String($0.drop(while: { $0 != " " }).dropFirst()) }, ["deactivate com.example.A owner zh", "flags com.example.A all"])
        XCTAssertEqual(last[0].prefix(while: { $0 != " " }), last[1].prefix(while: { $0 != " " }), "one moment, not two")
    }

    /// IMK can activate B before it deactivates A: each closes its own interval, and the differences are the whole process's.
    func testOverlappingIntervalsEachCloseWithProcessWideCounts() {
        let shell = flagsShell()
        let a = Controller(shell, bundle: "com.example.A"), b = Controller(shell, bundle: "com.example.B")
        a.session.activate()
        system += 4; mods(a, 4)
        b.session.activate()
        system += 2; mods(b, 2)  // in both intervals
        a.session.deactivate()   // not the owner any more: still closes its interval
        XCTAssertEqual(flagLines(shell), ["flags com.example.A all"], "6 counted, 6 received by the process (4 of them by A)")
        system += 2
        b.session.deactivate()
        XCTAssertEqual(flagLines(shell), ["flags com.example.A all", "flags com.example.B some"], "4 counted, 2 received")
    }

    func testASecondActivateKeepsTheFirstStart() {
        let shell = flagsShell()
        let c = Controller(shell, bundle: "com.example.A")
        c.session.activate()
        system += 4; mods(c, 4)
        c.session.activate()
        system += 2
        c.session.deactivate()
        XCTAssertEqual(flagLines(shell), ["flags com.example.A some"], "6 counted and 4 received since the first activate (2 and 0 since the second)")
    }

    func testADeactivateWithoutAnIntervalRecordsNoFlags() {
        let shell = flagsShell()
        let c = Controller(shell, bundle: "com.example.A")
        system += 6
        c.session.deactivate()
        XCTAssertEqual(flagLines(shell), [], "no activate before it")
        c.session.activate()
        system += 6; mods(c, 6)
        c.session.deactivate()
        system += 6
        c.session.deactivate()
        XCTAssertEqual(flagLines(shell), ["flags com.example.A all"], "the repeated deactivate adds none")
    }

    /// Secure input seen at any observation point of the interval voids it; the next interval is recorded again.
    func testNoFlagsForAnIntervalThatSawSecureInput() {
        let points: [(String, (Controller, Controller) -> Void, (Controller) -> Void)] = [
            ("at the activate", { a, _ in self.secure = true; a.session.activate(); self.secure = false }, { _ in }),
            ("at the deactivate", { a, _ in a.session.activate() }, { _ in self.secure = true }),
            ("at a key in between", { a, _ in a.session.activate(); self.secure = true; a.press(Keys.code(for: "a")); self.secure = false }, { _ in }),
            ("at a modifier event in between", { a, _ in a.session.activate(); self.secure = true; self.mods(a, 1); self.secure = false }, { _ in }),
            ("at another session's activate", { a, b in a.session.activate(); self.secure = true; b.session.activate(); self.secure = false }, { _ in }),
            ("at another session's deactivate", { a, b in a.session.activate(); self.secure = true; b.session.deactivate(); self.secure = false }, { _ in }),
        ]
        for (name, begin, beforeEnd) in points {
            secure = false
            let shell = flagsShell()
            let a = Controller(shell, bundle: "com.example.A"), b = Controller(shell, bundle: "com.example.B")
            begin(a, b)
            system += 6; mods(a, 6)
            beforeEnd(a)
            a.session.deactivate()
            b.session.deactivate()
            XCTAssertEqual(flagLines(shell), [], name)
            secure = false
            a.session.activate()
            system += 6; mods(a, 6)
            a.session.deactivate()
            XCTAssertEqual(flagLines(shell), ["flags com.example.A all"], "\(name): positive control, the next interval is recorded")
        }
    }

    func testNoFlagsWhileTheLogIsOffAndNoStartIsKept() {
        let store = MemoryDiagLogStore(false)
        let shell = flagsShell(store: store)
        let c = Controller(shell, bundle: "com.example.A")
        c.session.activate()
        system += 3; mods(c, 3)
        shell.diag.enabled = true  // on from here: the activate above left no start
        system += 3; mods(c, 3)
        c.session.deactivate()
        XCTAssertEqual(flagLines(shell), [])
        XCTAssertTrue(events(shell).contains("deactivate com.example.A owner zh"), "positive control: the deactivate itself is recorded")
    }

    /// Off and on again in the middle of an interval, by each way the switch can change: that interval is not recorded, the next is.
    func testNoFlagsForAnIntervalTheSwitchChangedIn() {
        let changes: [(String, (MemoryDiagLogStore, Shell, Controller) -> Void)] = [
            ("the settings window", { _, shell, _ in shell.diag.enabled = false; shell.diag.enabled = true }),
            ("the change notification", { store, shell, _ in store.diagLog = false; shell.diag.storeChanged(); store.diagLog = true; shell.diag.storeChanged() }),
            ("off and on before the notification", { store, shell, _ in store.diagLog = false; store.diagLog = true; shell.diag.storeChanged() }),
            ("the store alone, an event while off", { store, _, c in store.diagLog = false; self.mods(c, 1); store.diagLog = true }),
        ]
        for (name, change) in changes {
            let store = MemoryDiagLogStore()
            let shell = flagsShell(store: store)
            let c = Controller(shell, bundle: "com.example.A")
            c.session.activate()
            system += 3; mods(c, 3)
            change(store, shell, c)
            system += 3; mods(c, 3)
            c.session.deactivate()
            XCTAssertEqual(flagLines(shell), [], name)
            c.session.activate()
            system += 6; mods(c, 6)
            c.session.deactivate()
            XCTAssertEqual(flagLines(shell), ["flags com.example.A all"], "\(name): positive control, the next interval is recorded")
        }
    }

    func testNoFlagsWhileTheShiftToggleIsOff() {
        let off = flagsShell(shiftToggle: false)
        let c = Controller(off, bundle: "com.example.A")
        c.session.activate()
        system += 6; mods(c, 6)
        c.session.deactivate()
        XCTAssertEqual(flagLines(off), [], "off throughout")
        XCTAssertEqual(off.flagsSeen, 6, "counted all the same, before the toggle is looked at")

        let offAtTheEnd = flagsShell()
        let d = Controller(offAtTheEnd, bundle: "com.example.A")
        d.session.activate()
        system += 6; mods(d, 6)
        offAtTheEnd.applyShiftToggle(false)
        d.session.deactivate()
        XCTAssertEqual(flagLines(offAtTheEnd), [], "off at the deactivate")

        let offAtTheStart = flagsShell(shiftToggle: false)
        let e = Controller(offAtTheStart, bundle: "com.example.A")
        e.session.activate()
        offAtTheStart.applyShiftToggle(true)
        system += 6; mods(e, 6)
        e.session.deactivate()
        XCTAssertEqual(flagLines(offAtTheStart), [], "off at the activate")
    }

    /// A `flagsChanged` event is counted in the first line of `handle`: with no engine too.
    func testFlagsChangedIsCountedBeforeTheEngineExists() {
        let shell = flagsShell(resources: resources.appendingPathComponent("missing"))
        XCTAssertNil(shell.engine)
        let c = Controller(shell, bundle: "com.example.A")
        c.session.activate()
        system += 2; mods(c, 2)
        XCTAssertEqual(shell.flagsSeen, 2)
        c.press(Keys.code(for: "a"))
        XCTAssertEqual(shell.flagsSeen, 2, "a key is not a modifier event")
        c.session.deactivate()
        XCTAssertEqual(flagLines(shell), ["flags com.example.A all"])
    }
}

/// docs/spec/macos-shell.md §13.3: records the pasteboard calls a copy makes; never the real pasteboard.
@MainActor
final class FakePasteboard: DiagPasteboard {
    private(set) var calls: [String] = []
    private(set) var strings: [NSPasteboard.PasteboardType: String] = [:]

    func prepareForNewContents(with options: NSPasteboard.ContentsOptions) -> Int {
        calls.append(options.contains(.currentHostOnly) ? "prepare currentHostOnly" : "prepare")
        strings = [:]
        return 0
    }

    func writeObjects(_ objects: [NSPasteboardWriting]) -> Bool {
        for case let item as NSPasteboardItem in objects {
            calls.append("item " + item.types.map(\.rawValue).sorted().joined(separator: " "))
            for t in item.types { strings[t] = item.string(forType: t) }
        }
        return true
    }
}
