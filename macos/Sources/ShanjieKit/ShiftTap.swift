import Foundation

/// Shift-toggle contract section 1: whether a Shift press and release was a lone tap. No AppKit: the tests feed it events.
///
/// A tap is a Shift (left or right) that went down with no other modifier and Caps Lock off, and came up with no key pressed, no other
/// modifier used and no mouse button pressed in between, within `ShiftTap.SHIFT_TAP_MAX`.
public struct ShiftTap {
    /// SHIFT_TAP_MAX: the longest press that still counts as a tap. There is no reference measurement of it (neither Windows' Microsoft
    /// Zhuyin nor vChewing documents a value): it is an inferred value, chosen so that holding Shift to type a capital and then
    /// giving up does not switch. Tune it from the maintainer's device feedback.
    public static let SHIFT_TAP_MAX: TimeInterval = 0.5

    /// kVK_Shift, kVK_RightShift.
    static let shiftKeys: Set<UInt16> = [56, 60]

    /// The Shift key now down and the time it went down; `nil` when no tap is possible (nothing down, or the press is spoiled).
    private var candidate: (key: UInt16, at: TimeInterval)?
    /// Shift keys that are down now. Two at once spoil the tap: the flags cannot say which one came up.
    private var down: Set<UInt16> = []

    public init() {}

    /// A `keyDown` anywhere: Shift+letter, Shift+arrow and the like are not a tap.
    public mutating func keyDown() { candidate = nil }

    /// One `flagsChanged` event. `others`: Control, Option, Command or Fn is in the flags; `capsLock`: the light is on; `now`: the event's
    /// time; `mouseAgo`: seconds since the latest mouse-button press, asked only when it can decide (a release). True: switch.
    public mutating func flagsChanged(keyCode: UInt16, others: Bool, capsLock: Bool, now: TimeInterval, mouseAgo: () -> TimeInterval) -> Bool {
        guard Self.shiftKeys.contains(keyCode) else {
            candidate = nil  // another modifier moved while Shift may be down
            return false
        }
        if down.remove(keyCode) == nil {  // pressed
            down.insert(keyCode)
            candidate = down.count == 1 && !others && !capsLock ? (keyCode, now) : nil
            return false
        }
        defer { candidate = nil }
        guard let c = candidate, c.key == keyCode, !others, !capsLock else { return false }
        let held = now - c.at
        return held <= Self.SHIFT_TAP_MAX && mouseAgo() > held
    }
}
