import XCTest
@testable import ShanjieKit

/// shift-toggle section 5: what is a lone Shift tap.
final class ShiftTapTests: XCTestCase {
    private let left: UInt16 = 56, right: UInt16 = 60, command: UInt16 = 55
    private let noMouse: () -> TimeInterval = { 1000 }

    private func tap(_ t: inout ShiftTap, _ key: UInt16, at: TimeInterval, others: Bool = false, caps: Bool = false,
                     mouse: @escaping () -> TimeInterval = { 1000 }) -> Bool {
        t.flagsChanged(keyCode: key, others: others, capsLock: caps, now: at, mouseAgo: mouse)
    }

    func testLoneTapSwitchesLeftAndRight() {
        for key in [left, right] {
            var t = ShiftTap()
            XCTAssertFalse(tap(&t, key, at: 1.0))
            XCTAssertTrue(tap(&t, key, at: 1.1), "press and release is a tap")
            XCTAssertFalse(tap(&t, key, at: 2.0), "the next press starts over")
            XCTAssertTrue(tap(&t, key, at: 2.2))
        }
    }

    func testAKeyBetweenIsNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, at: 1.0)
        t.keyDown()  // Shift+letter, Shift+arrow
        XCTAssertFalse(tap(&t, left, at: 1.1))
    }

    func testAnotherModifierIsNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, at: 1.0)
        XCTAssertFalse(tap(&t, command, at: 1.05, others: true))
        XCTAssertFalse(tap(&t, left, at: 1.1))
        // Shift pressed while Command is held never starts a tap.
        XCTAssertFalse(tap(&t, left, at: 2.0, others: true))
        XCTAssertFalse(tap(&t, left, at: 2.1, others: true))
        // Nor does one released while Command came down in between and went up again.
        XCTAssertFalse(tap(&t, left, at: 3.0))
        XCTAssertFalse(tap(&t, command, at: 3.02, others: true))
        XCTAssertFalse(tap(&t, command, at: 3.04))
        XCTAssertFalse(tap(&t, left, at: 3.1))
    }

    func testMouseDownBetweenIsNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, at: 1.0)
        XCTAssertFalse(tap(&t, left, at: 1.4, mouse: { 0.2 }), "a click 0.2 s ago, held 0.4 s: Shift+click")
        _ = tap(&t, left, at: 2.0)
        XCTAssertTrue(tap(&t, left, at: 2.4, mouse: { 0.9 }), "a click before the press does not count")
    }

    func testHeldLongerThanTheLimitIsNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, at: 1.0)
        XCTAssertFalse(tap(&t, left, at: 1.0 + ShiftTap.SHIFT_TAP_MAX + 0.01))
        _ = tap(&t, left, at: 2.0)
        XCTAssertTrue(tap(&t, left, at: 2.0 + ShiftTap.SHIFT_TAP_MAX))
    }

    func testCapsLockOnIsNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, at: 1.0, caps: true)
        XCTAssertFalse(tap(&t, left, at: 1.1, caps: true))
    }

    func testBothShiftKeysAreNotATap() {
        var t = ShiftTap()
        _ = tap(&t, left, at: 1.0)
        _ = tap(&t, right, at: 1.05)
        XCTAssertFalse(tap(&t, right, at: 1.1))
        XCTAssertFalse(tap(&t, left, at: 1.15))
    }
}
