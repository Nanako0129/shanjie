import XCTest
@testable import ShanjieKit

/// shift-toggle section 5: what is a lone Shift tap.
final class ShiftTapTests: XCTestCase {
    private let left: UInt16 = 56, right: UInt16 = 60, command: UInt16 = 55
    private let noMouse: () -> TimeInterval = { 1000 }

    /// One event. `down`: the Shift bit of the flags for this key; `processed`: when it is handled (defaults to the event's time).
    private func tap(_ t: inout ShiftTap, _ key: UInt16, down: Bool, at: TimeInterval, processed: TimeInterval? = nil, others: Bool = false,
                     caps: Bool = false, mouse: @escaping () -> TimeInterval = { 1000 }) -> Bool {
        t.flagsChanged(keyCode: key, isDown: down, others: others, capsLock: caps, now: at, processedAt: processed ?? at, mouseAgo: mouse)
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

    func testBothShiftKeysAreNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, down: true, at: 1.0)
        _ = tap(&t, right, down: true, at: 1.05)
        XCTAssertFalse(tap(&t, right, down: false, at: 1.1))
        XCTAssertFalse(tap(&t, left, down: false, at: 1.15))
    }
}
