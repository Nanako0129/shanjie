import Foundation

/// docs/spec/keys-and-composition.md §9.1: whether a Shift press and release was a lone tap. No AppKit: the tests feed it events.
///
/// A tap is a Shift (left or right) that went down with no other modifier and Caps Lock off, and came up with no key pressed, no other
/// modifier used, no change of the other Shift's bit and no mouse button pressed in between, within `ShiftTap.SHIFT_TAP_MAX`.
public struct ShiftTap {
    /// SHIFT_TAP_MAX: the longest press that still counts as a tap. There is no reference measurement of it (neither Windows' Microsoft
    /// Zhuyin nor vChewing documents a value): it is an inferred value, chosen so that holding Shift to type a capital and then
    /// giving up does not switch. Tune it on a device.
    public static let SHIFT_TAP_MAX: TimeInterval = 0.5

    /// kVK_Shift, kVK_RightShift.
    static let shiftKeys: Set<UInt16> = [56, 60]

    /// What a Shift release was (docs/spec/macos-shell.md §13.1): `toggled`, or the one reason it was not a tap, one per exit path.
    public enum Outcome: String, CaseIterable, Sendable {
        case toggled
        /// At the press: another modifier was down, or Caps Lock was on.
        case modifierAtPress, capsLockAtPress
        /// Between press and release: another modifier moved; the other Shift's bit differs from the press.
        case modifierBetween, otherSideChanged
        case tooLong, mouse
        /// No press seen (the tap was reset, or Shift was down when the input method became active); the release is of the other side.
        case noPress, otherKeyReleased
        /// At the release, with no flagsChanged of it in between: Control, Option, Command or Fn down; Caps Lock on.
        case modifierAtRelease, capsLockAtRelease
    }

    /// The Shift key now down, the time it went down, whether the other Shift's bit was set then, the first reason the press can no
    /// longer be a tap (`nil` while it still can), and whether a key went down since (a capital: not recorded at all).
    private var press: (key: UInt16, at: TimeInterval, otherDown: Bool, spoiled: Outcome?, interrupted: Bool)?

    /// docs/spec/macos-shell.md §13.1: a key went down while a Shift was (or may have been) held, since both Shift keys were last up or
    /// the last clean press and release. While set, a release that is not a tap is not recorded (`nil`): with the two Shift keys
    /// overlapping, the press record of the key that was interrupted can be replaced by the other side's, and its release would otherwise
    /// tell a capital apart. A clean press and release (its own key, no key since its press) clears it too: that press was made after the
    /// last key, every release clears the press record, so what follows depends on later events only; without it a stale bit of the other
    /// side would keep it set for good. The toggle decision never reads it.
    private var typedSinceAllUp = false

    public init() {}

    /// A `keyDown` anywhere: Shift+letter, Shift+arrow and the like are not a tap. `shiftHeld`: the key's flags have Shift (a Shift held
    /// since before this tap saw its press).
    public mutating func keyDown(shiftHeld: Bool = false) {
        press?.interrupted = true
        if press != nil || shiftHeld { typedSinceAllUp = true }
    }

    /// `event(…) == .toggled`.
    public mutating func flagsChanged(keyCode: UInt16, leftDown: Bool, rightDown: Bool, others: Bool, capsLock: Bool, now: TimeInterval,
                                      processedAt: TimeInterval, mouseAgo: () -> TimeInterval) -> Bool {
        event(keyCode: keyCode, leftDown: leftDown, rightDown: rightDown, others: others, capsLock: capsLock, now: now,
              processedAt: processedAt, mouseAgo: mouseAgo) == .toggled
    }

    /// One `flagsChanged` event. `leftDown`, `rightDown`: whether the flags say each Shift key is down now. The set of down keys is rebuilt
    /// from them on every event, never kept, so a missed event cannot leave a stale key behind; `others`: Control, Option, Command or Fn
    /// is in the flags; `capsLock`: the light is on; `now`: the event's time; `processedAt`: the time this event is handled, on the same
    /// clock as `now` (the system uptime); `mouseAgo`: seconds since the latest mouse-button press, asked only when it can decide (a
    /// release). The outcome of a Shift release; `nil` for anything else, for a release after a key (a capital), and for a release that is
    /// not a tap after a key while both Shift keys were not yet all up and no clean press and release came since.
    public mutating func event(keyCode: UInt16, leftDown: Bool, rightDown: Bool, others: Bool, capsLock: Bool, now: TimeInterval,
                               processedAt: TimeInterval, mouseAgo: () -> TimeInterval) -> Outcome? {
        guard Self.shiftKeys.contains(keyCode) else {
            if press?.spoiled == nil { press?.spoiled = .modifierBetween }  // another modifier moved while Shift may be down
            return nil
        }
        let down = Set([leftDown ? UInt16(56) : nil, rightDown ? UInt16(60) : nil].compactMap { $0 })
        let otherDown = !down.subtracting([keyCode]).isEmpty
        if down.contains(keyCode) {
            press = (keyCode, now, otherDown, others ? .modifierAtPress : capsLock ? .capsLockAtPress : nil, false)
            return nil
        }
        // Read before the defer clears the press; the reset takes effect from the next release, this one is judged with the flag as it was.
        let clean = press.map { $0.key == keyCode && !$0.interrupted } ?? false
        defer {
            press = nil
            if down.isEmpty || clean { typedSinceAllUp = false }
        }
        let outcome = release(keyCode: keyCode, otherDown: otherDown, others: others, capsLock: capsLock, now: now, processedAt: processedAt,
                              mouseAgo: mouseAgo)
        return outcome != .toggled && typedSinceAllUp ? nil : outcome
    }

    private func release(keyCode: UInt16, otherDown: Bool, others: Bool, capsLock: Bool, now: TimeInterval, processedAt: TimeInterval,
                         mouseAgo: () -> TimeInterval) -> Outcome? {
        guard let p = press else { return .noPress }
        if p.interrupted { return nil }
        guard p.key == keyCode else { return .otherKeyReleased }
        if let spoiled = p.spoiled { return spoiled }
        // docs/spec/keys-and-composition.md §9.1: the other side's bit that was already set at the press and is unchanged now (a stale bit) does not spoil
        // the tap; a change of it in between (two Shift keys really pressed together) does.
        if p.otherDown != otherDown { return .otherSideChanged }
        if others { return .modifierAtRelease }
        if capsLock { return .capsLockAtRelease }
        if now - p.at > Self.SHIFT_TAP_MAX { return .tooLong }
        // A click since the press is `mouseAgo` seconds old at most `processedAt - p.at`; the event's own timestamps would miss a click
        // between a late-handled release and the release itself.
        return mouseAgo() > processedAt - p.at ? .toggled : .mouse
    }
}
