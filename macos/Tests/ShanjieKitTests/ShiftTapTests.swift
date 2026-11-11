import XCTest
@testable import ShanjieKit

/// docs/spec/keys-and-composition.md §9.2: what is a lone Shift tap.
final class ShiftTapTests: XCTestCase {
    private let left: UInt16 = 56, right: UInt16 = 60, command: UInt16 = 55
    private let noMouse: () -> TimeInterval = { 1000 }

    /// One event. `down`: whether this key's Shift bit is set; `alsoHeld`: the other Shift key's bit; `processed`: when it is handled
    /// (defaults to the event's time).
    private func tap(_ t: inout ShiftTap, _ key: UInt16, down: Bool, at: TimeInterval, alsoHeld: Bool = false, processed: TimeInterval? = nil,
                     others: Bool = false, caps: Bool = false, mouse: @escaping () -> TimeInterval = { 1000 }) -> Bool {
        let leftDown = key == left ? down : alsoHeld, rightDown = key == right ? down : alsoHeld
        return t.flagsChanged(keyCode: key, leftDown: leftDown, rightDown: rightDown, others: others, capsLock: caps, now: at,
                              processedAt: processed ?? at, mouseAgo: mouse)
    }

    func testLoneTapSwitchesLeftAndRight() {
        for key in [left, right] {
            var t = ShiftTap()
            XCTAssertFalse(tap(&t, key, down: true, at: 1.0))
            XCTAssertTrue(tap(&t, key, down: false, at: 1.1), "press and release is a tap")
            XCTAssertFalse(tap(&t, key, down: true, at: 2.0), "the next press starts over")
            XCTAssertTrue(tap(&t, key, down: false, at: 2.2))
        }
    }

    /// A release whose press was never seen (secure field, app switch) must not turn every later tap upside down.
    func testAnUnpairedReleaseDoesNotInvertLaterTaps() {
        var t = ShiftTap()
        XCTAssertFalse(tap(&t, left, down: false, at: 0.5), "a release alone is nothing")
        XCTAssertFalse(tap(&t, left, down: true, at: 1.0))
        XCTAssertTrue(tap(&t, left, down: false, at: 1.1), "the next normal tap still switches")
        // And a press whose release was missed: the next press and release is a tap again.
        XCTAssertFalse(tap(&t, left, down: true, at: 2.0))
        XCTAssertFalse(tap(&t, left, down: true, at: 3.0), "a second press without a release")
        XCTAssertTrue(tap(&t, left, down: false, at: 3.1))
    }

    func testAKeyBetweenIsNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, down: true, at: 1.0)
        t.keyDown()  // Shift+letter, Shift+arrow
        XCTAssertFalse(tap(&t, left, down: false, at: 1.1))
    }

    func testAnotherModifierIsNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, down: true, at: 1.0)
        XCTAssertFalse(tap(&t, command, down: false, at: 1.05, others: true))
        XCTAssertFalse(tap(&t, left, down: false, at: 1.1))
        // Shift pressed while Command is held never starts a tap.
        XCTAssertFalse(tap(&t, left, down: true, at: 2.0, others: true))
        XCTAssertFalse(tap(&t, left, down: false, at: 2.1, others: true))
        // Nor does one released while Command came down in between and went up again.
        XCTAssertFalse(tap(&t, left, down: true, at: 3.0))
        XCTAssertFalse(tap(&t, command, down: false, at: 3.02, others: true))
        XCTAssertFalse(tap(&t, command, down: false, at: 3.04))
        XCTAssertFalse(tap(&t, left, down: false, at: 3.1))
    }

    func testMouseDownBetweenIsNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, down: true, at: 1.0)
        XCTAssertFalse(tap(&t, left, down: false, at: 1.4, mouse: { 0.2 }), "a click 0.2 s ago, held 0.4 s: Shift+click")
        _ = tap(&t, left, down: true, at: 2.0)
        XCTAssertTrue(tap(&t, left, down: false, at: 2.4, mouse: { 0.9 }), "a click before the press does not count")
    }

    /// The release is handled 0.05 s after its timestamp; a click 0.02 s after the press is 0.13 s old then, which is newer than the
    /// 0.1 s the event says Shift was held, so comparing with the event's hold time misses it.
    func testAClickBeforeALateHandledReleaseIsNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, down: true, at: 10.0)
        XCTAssertFalse(tap(&t, left, down: false, at: 10.1, processed: 10.15, mouse: { 0.13 }))
        _ = tap(&t, left, down: true, at: 20.0)
        XCTAssertTrue(tap(&t, left, down: false, at: 20.1, processed: 20.15, mouse: { 0.16 }), "control: a click before the press")
    }

    func testHeldLongerThanTheLimitIsNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, down: true, at: 1.0)
        XCTAssertFalse(tap(&t, left, down: false, at: 1.0 + ShiftTap.SHIFT_TAP_MAX + 0.01))
        _ = tap(&t, left, down: true, at: 2.0)
        XCTAssertTrue(tap(&t, left, down: false, at: 2.0 + ShiftTap.SHIFT_TAP_MAX))
    }

    func testCapsLockOnIsNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, down: true, at: 1.0, caps: true)
        XCTAssertFalse(tap(&t, left, down: false, at: 1.1, caps: true))
    }

    /// docs/spec/keys-and-composition.md §9.1: the other side was already down at the press and still is at the release, so the press is a lone tap (the
    /// accepted trade-off); the first Shift's own release is not.
    func testHoldingOneShiftAndTappingTheOtherSwitches() {
        var t = ShiftTap()
        _ = tap(&t, left, down: true, at: 1.0)
        _ = tap(&t, right, down: true, at: 1.05, alsoHeld: true)
        XCTAssertTrue(tap(&t, right, down: false, at: 1.1, alsoHeld: true))
        XCTAssertFalse(tap(&t, left, down: false, at: 1.15))
    }

    /// docs/spec/keys-and-composition.md §9.1: the right Shift's bit stays in the flags all along (a release that never arrived): the left tap still switches.
    func testAStaleOtherShiftBitDoesNotSpoilATap() {
        var t = ShiftTap()
        XCTAssertFalse(tap(&t, left, down: true, at: 1.0, alsoHeld: true))
        XCTAssertTrue(tap(&t, left, down: false, at: 1.1, alsoHeld: true))
    }

    /// docs/spec/keys-and-composition.md §9.1: both Shift keys really pressed together (the other side changes during the press) never switch.
    func testTwoShiftKeysPressedTogetherAreNotATap() {
        var t = ShiftTap()
        XCTAssertFalse(tap(&t, left, down: true, at: 1.0))
        XCTAssertFalse(tap(&t, right, down: true, at: 1.05, alsoHeld: true))
        XCTAssertFalse(tap(&t, left, down: false, at: 1.1, alsoHeld: true))
        XCTAssertFalse(tap(&t, right, down: false, at: 1.15))
    }

    /// The left release was missed; the flags of the next events say only the right key is down, so a right tap still switches.
    func testAMissedLeftReleaseDoesNotSpoilARightTap() {
        var t = ShiftTap()
        _ = tap(&t, left, down: true, at: 1.0)  // its release never arrives
        XCTAssertFalse(tap(&t, right, down: true, at: 5.0))
        XCTAssertTrue(tap(&t, right, down: false, at: 5.1))
    }

    /// docs/spec/macos-shell.md §13.1, docs/spec/privacy.md §11 (f): every sequence of up to 7 of {left down, left up, right down, right up,
    /// a key} (the key's Shift flag from the bits then). (1) Each Shift release's outcome is `nil` or the outcome of the same release with
    /// every key removed: a key can only hide a record, never change one. (2) After a clean release (the event before it is its own key's
    /// press, no key in between), every outcome equals the run with all the keys before that release removed: what was typed earlier no
    /// longer decides whether anything is recorded.
    func testARecordNeverDependsOnAKeyTypedWhileShiftWasHeld() {
        enum Step: CaseIterable { case leftDown, leftUp, rightDown, rightUp, key }
        func run(_ steps: [Step]) -> [ShiftTap.Outcome?] {
            var t = ShiftTap(), l = false, r = false, at: TimeInterval = 1, out: [ShiftTap.Outcome?] = []
            for step in steps {
                at += 0.05
                let code: UInt16
                switch step {
                case .key: t.keyDown(shiftHeld: l || r); continue
                case .leftDown: l = true; code = left
                case .leftUp: l = false; code = left
                case .rightDown: r = true; code = right
                case .rightUp: r = false; code = right
                }
                out.append(t.event(keyCode: code, leftDown: l, rightDown: r, others: false, capsLock: false, now: at, processedAt: at,
                                   mouseAgo: { 1000 }))
            }
            return out
        }
        var sequences: [[Step]] = [[]], checked = 0
        for _ in 1...7 {
            sequences = sequences.flatMap { s in Step.allCases.map { s + [$0] } }
            for s in sequences {
                let out = run(s), noKeys = run(s.filter { $0 != .key })
                for (i, o) in out.enumerated() where o != nil {
                    XCTAssertEqual(o, noKeys[i], "\(s) release \(i)")
                }
                // The clean releases: the Shift event right before is the same key's press and no key came between.
                var shiftIndex = -1
                for (j, step) in s.enumerated() where step != .key {
                    shiftIndex += 1
                    guard step == .leftUp || step == .rightUp, j > 0, s[j - 1] == (step == .leftUp ? .leftDown : .rightDown) else { continue }
                    let laterKeysOnly = Array(s[..<j].filter { $0 != .key }) + Array(s[j...])
                    let expected = run(laterKeysOnly)
                    XCTAssertEqual(Array(out[(shiftIndex + 1)...]), Array(expected[(shiftIndex + 1)...]), "\(s) after clean release \(shiftIndex)")
                    checked += 1
                }
            }
        }
        XCTAssertGreaterThan(checked, 10_000)
    }
}
