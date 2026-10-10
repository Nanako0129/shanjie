import AppKit
import CShanjie

/// macOS key events to `ShanjieKey` (docs/contracts/s3b.md section 6). Only the physical key
/// (`keyCode`) and `modifierFlags` are read, never `event.characters`, so the active keyboard
/// layout and dead keys cannot change what the core receives.
enum KeyMap {
    // ShanjieKey.kind (s3a section 6).
    static let char: UInt32 = 1, space: UInt32 = 2, enter: UInt32 = 3, backspace: UInt32 = 4,
        delete: UInt32 = 5, esc: UInt32 = 6, left: UInt32 = 7, right: UInt32 = 8, up: UInt32 = 9,
        down: UInt32 = 10, home: UInt32 = 11, end: UInt32 = 12, tab: UInt32 = 13, pageUp: UInt32 = 14, pageDown: UInt32 = 15

    /// kVK_PageUp and kVK_PageDown. Deliberately not in `specials` or `translate`: the shell turns them into the core's
    /// kinds 14 / 15 only while a vertical candidate window is open and no modifier is held (candidate-vertical contract
    /// section 2.2, `Session.handle`); every other time they are keys outside the tables.
    static let pageKeys: [UInt16: UInt32] = [116: pageUp, 121: pageDown]

    /// Virtual key codes of the special keys (Carbon kVK_*; the tests cross-check against Carbon).
    static let specials: [UInt16: UInt32] = [
        36: enter,   // Return
        76: enter,   // keypad Enter
        49: space,
        51: backspace,  // Delete
        117: delete,    // Forward Delete
        53: esc,
        123: left, 124: right, 126: up, 125: down,
        115: home, 119: end,
        48: tab,
    ]

    /// The ANSI physical layout: virtual key code to the unshifted ASCII keycap.
    static let ansi: [UInt16: Character] = [
        0: "a", 11: "b", 8: "c", 2: "d", 14: "e", 3: "f", 5: "g", 4: "h", 34: "i", 38: "j",
        40: "k", 37: "l", 46: "m", 45: "n", 31: "o", 35: "p", 12: "q", 15: "r", 1: "s", 17: "t",
        32: "u", 9: "v", 13: "w", 7: "x", 16: "y", 6: "z",
        29: "0", 18: "1", 19: "2", 20: "3", 21: "4", 23: "5", 22: "6", 26: "7", 28: "8", 25: "9",
        50: "`", 27: "-", 24: "=", 33: "[", 30: "]", 42: "\\", 41: ";", 39: "'", 43: ",", 47: ".",
        44: "/",
    ]

    /// bit0 SHIFT, bit1 CONTROL, bit2 OPTION, bit3 COMMAND, bit4 CAPSLOCK.
    static func modifiers(_ flags: NSEvent.ModifierFlags) -> UInt32 {
        var m: UInt32 = 0
        if flags.contains(.shift) { m |= 1 << 0 }
        if flags.contains(.control) { m |= 1 << 1 }
        if flags.contains(.option) { m |= 1 << 2 }
        if flags.contains(.command) { m |= 1 << 3 }
        if flags.contains(.capsLock) { m |= 1 << 4 }
        return m
    }

    /// nil for keys outside both tables (keypad digits, function keys, ...): those never reach the core.
    static func translate(keyCode: UInt16, flags: NSEvent.ModifierFlags) -> ShanjieKey? {
        let mods = modifiers(flags)
        if let kind = specials[keyCode] { return ShanjieKey(kind: kind, ch: 0, modifiers: mods) }
        if let c = ansi[keyCode], let scalar = c.unicodeScalars.first {
            return ShanjieKey(kind: char, ch: scalar.value, modifiers: mods)
        }
        return nil
    }
}
