//! Virtual key to `ShanjieKey` and the `OnTestKeyDown` predicate (docs/spec/windows-tip.md §3.6).
//! Pure functions: no TSF, so the unit tests need no host. `Key` holds a typed character, so no `Debug` (R2).

pub const KIND_CHAR: u32 = 1;
pub const KIND_SPACE: u32 = 2;
pub const KIND_ESC: u32 = 6;

pub const MOD_SHIFT: u32 = 1;
pub const MOD_CONTROL: u32 = 2;
pub const MOD_CAPS: u32 = 16;

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Key {
    pub kind: u32,
    pub ch: u32,
    pub modifiers: u32,
}

/// Modifier state read by the sink with `GetKeyState`.
#[derive(Clone, Copy, Default)]
pub struct Mods {
    pub shift: bool,
    pub ctrl: bool,
    pub alt: bool,
    pub win: bool,
    pub caps: bool,
}

/// `VK_PRIOR` and `VK_NEXT` are absent on purpose: the core takes them only with a vertical candidate window open (docs/spec/windows-tip.md §3.6).
const SPECIAL: [(u32, u32); 12] = [
    (0x20, 2),  // VK_SPACE
    (0x0D, 3),  // VK_RETURN
    (0x08, 4),  // VK_BACK
    (0x2E, 5),  // VK_DELETE
    (0x1B, 6),  // VK_ESCAPE
    (0x25, 7),  // VK_LEFT
    (0x27, 8),  // VK_RIGHT
    (0x26, 9),  // VK_UP
    (0x28, 10), // VK_DOWN
    (0x24, 11), // VK_HOME
    (0x23, 12), // VK_END
    (0x09, 13), // VK_TAB
];

/// The keycap on a US keyboard without Shift: the standard zhuyin layout is physical, so a fixed table, never `ToUnicode`.
const OEM: [(u32, char); 11] = [
    (0xBA, ';'),  // VK_OEM_1
    (0xBF, '/'),  // VK_OEM_2
    (0xC0, '`'),  // VK_OEM_3
    (0xDB, '['),  // VK_OEM_4
    (0xDC, '\\'), // VK_OEM_5
    (0xDD, ']'),  // VK_OEM_6
    (0xDE, '\''), // VK_OEM_7
    (0xBB, '='),  // VK_OEM_PLUS
    (0xBD, '-'),  // VK_OEM_MINUS
    (0xBC, ','),  // VK_OEM_COMMA
    (0xBE, '.'),  // VK_OEM_PERIOD
];

/// `None` for a key the core never sees (Alt or Win held, or a key outside the table): the key goes to the app.
pub fn map_key(vk: u32, m: Mods) -> Option<Key> {
    if m.alt || m.win {
        return None;
    }
    let modifiers = (m.shift as u32 * MOD_SHIFT) | (m.ctrl as u32 * MOD_CONTROL) | (m.caps as u32 * MOD_CAPS);
    let (kind, ch) = if let Some(&(_, kind)) = SPECIAL.iter().find(|(v, _)| *v == vk) {
        (kind, 0)
    } else if let Some(&(_, c)) = OEM.iter().find(|(v, _)| *v == vk) {
        (KIND_CHAR, c as u32)
    } else if (0x30..=0x39).contains(&vk) {
        (KIND_CHAR, vk) // '0'..'9' have the VK of their character
    } else if (0x41..=0x5A).contains(&vk) {
        (KIND_CHAR, vk + 0x20) // 'A'..'Z' to 'a'..'z'
    } else {
        return None;
    };
    Some(Key { kind, ch, modifiers })
}

/// `OnTestKeyDown` does not advance the engine, so it can only say "may eat". With a composition every mapped key may be eaten;
/// without one only a plain character key, Space, and Ctrl+\ (core rule 1's exception) may.
pub fn may_eat(composing: bool, k: &Key) -> bool {
    if composing {
        return true;
    }
    let plain = k.modifiers & MOD_CONTROL == 0;
    (plain && (k.kind == KIND_CHAR || k.kind == KIND_SPACE)) || (k.kind == KIND_CHAR && k.ch == '\\' as u32 && k.modifiers == MOD_CONTROL)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plain() -> Mods {
        Mods::default()
    }
    fn key(vk: u32, m: Mods) -> Key {
        map_key(vk, m).expect("mapped")
    }

    #[test]
    fn special_keys_map_to_their_kind() {
        let want = [(0x20, 2), (0x0D, 3), (0x08, 4), (0x2E, 5), (0x1B, 6), (0x25, 7), (0x27, 8), (0x26, 9), (0x28, 10), (0x24, 11), (0x23, 12), (0x09, 13)];
        for (vk, kind) in want {
            let k = key(vk, plain());
            assert!(k.kind == kind && k.ch == 0 && k.modifiers == 0, "vk {vk:#x}");
        }
    }

    #[test]
    fn page_keys_and_unlisted_keys_are_not_sent() {
        for vk in [0x21, 0x22, 0x10, 0x11, 0x12, 0x14, 0x70, 0x60, 0x6B, 0xE5, 0x00] {
            assert!(map_key(vk, plain()).is_none(), "vk {vk:#x}");
        }
    }

    #[test]
    fn character_keys_carry_the_unshifted_keycap() {
        for (i, c) in ('0'..='9').enumerate() {
            let k = key(0x30 + i as u32, plain());
            assert!(k.kind == KIND_CHAR && k.ch == c as u32, "digit {c}");
        }
        for (i, c) in ('a'..='z').enumerate() {
            let k = key(0x41 + i as u32, plain());
            assert!(k.kind == KIND_CHAR && k.ch == c as u32, "letter {c}");
        }
        let oem = [(0xBA, ';'), (0xBB, '='), (0xBC, ','), (0xBD, '-'), (0xBE, '.'), (0xBF, '/'), (0xC0, '`'), (0xDB, '['), (0xDC, '\\'), (0xDD, ']'), (0xDE, '\'')];
        for (vk, c) in oem {
            let k = key(vk, plain());
            assert!(k.kind == KIND_CHAR && k.ch == c as u32, "oem {c}");
        }
        // Shift does not change the character, only bit0.
        let k = key(0xBA, Mods { shift: true, ..plain() });
        assert!(k.ch == ';' as u32 && k.modifiers == MOD_SHIFT);
    }

    #[test]
    fn modifier_bits() {
        let k = key(0x41, Mods { shift: true, ctrl: true, caps: true, ..plain() });
        assert!(k.modifiers == MOD_SHIFT | MOD_CONTROL | MOD_CAPS);
        assert!(key(0x41, Mods { ctrl: true, ..plain() }).modifiers == 2);
        assert!(key(0x41, Mods { caps: true, ..plain() }).modifiers == 16);
    }

    #[test]
    fn alt_and_win_keys_are_not_converted() {
        for vk in [0x41, 0x20, 0x0D, 0x30, 0xBA] {
            assert!(map_key(vk, Mods { alt: true, ..plain() }).is_none());
            assert!(map_key(vk, Mods { win: true, ..plain() }).is_none());
            assert!(map_key(vk, Mods { alt: true, ctrl: true, ..plain() }).is_none(), "AltGr");
        }
    }

    #[test]
    fn on_test_key_down_with_an_empty_composition() {
        // Plain character keys and Space may be eaten.
        assert!(may_eat(false, &key(0x41, plain())));
        assert!(may_eat(false, &key(0x31, plain())));
        assert!(may_eat(false, &key(0xBC, plain())));
        assert!(may_eat(false, &key(0x41, Mods { shift: true, ..plain() })));
        assert!(may_eat(false, &key(0x20, plain())));
        // Everything else goes to the app.
        for vk in [0x0D, 0x08, 0x2E, 0x1B, 0x25, 0x27, 0x26, 0x28, 0x24, 0x23, 0x09] {
            assert!(!may_eat(false, &key(vk, plain())), "vk {vk:#x}");
        }
        // Ctrl+letter goes to the app; Ctrl+\ may be eaten; Ctrl+Shift+\ may not.
        assert!(!may_eat(false, &key(0x41, Mods { ctrl: true, ..plain() })));
        assert!(!may_eat(false, &key(0x20, Mods { ctrl: true, ..plain() })));
        assert!(may_eat(false, &key(0xDC, Mods { ctrl: true, ..plain() })));
        assert!(!may_eat(false, &key(0xDC, Mods { ctrl: true, shift: true, ..plain() })));
    }

    #[test]
    fn on_test_key_down_with_a_composition() {
        for vk in [0x41, 0x31, 0x20, 0x0D, 0x08, 0x2E, 0x1B, 0x25, 0x27, 0x26, 0x28, 0x24, 0x23, 0x09, 0xBC] {
            assert!(may_eat(true, &key(vk, plain())), "vk {vk:#x}");
        }
        assert!(may_eat(true, &key(0x41, Mods { ctrl: true, ..plain() })));
    }
}
