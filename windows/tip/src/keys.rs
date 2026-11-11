//! Virtual key to `ShanjieKey` and the `OnTestKeyDown` predicate (docs/spec/windows-tip.md §3.6).
//! Pure functions: no TSF, so the unit tests need no host (and the module is built on every platform). `Key` holds a typed character, so no `Debug` (R2).
#![cfg_attr(not(windows), allow(dead_code))]

pub const KIND_CHAR: u32 = 1;

pub const MOD_SHIFT: u32 = 1;
pub const MOD_CONTROL: u32 = 2;
pub const MOD_CAPS: u32 = 16;

/// The `ShanjieKey` the host gets (docs/spec/windows-tip.md §5.3).
pub use shanjie_win_proto::Key;

/// Modifier state read by the sink with `GetKeyState`.
#[derive(Clone, Copy, Default)]
pub struct Mods {
    pub shift: bool,
    pub ctrl: bool,
    pub alt: bool,
    pub win: bool,
    pub caps: bool,
}

/// `VK_PRIOR` and `VK_NEXT` are absent on purpose: `map_key` adds them only while the candidate window is open (docs/spec/windows-tip.md §3.6).
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

const VK_PRIOR: u32 = 0x21;
const VK_NEXT: u32 = 0x22;
/// `PAGE_UP` and `PAGE_DOWN` of shanjie.h.
const KIND_PAGE_UP: u32 = 14;
const KIND_PAGE_DOWN: u32 = 15;

/// `None` for a key the core never sees (Alt or Win held, or a key outside the table): the key goes to the app. `window`: the last `Out` of this
/// instance had a candidate window open (`candidate_selected >= 0`). Page Up and Page Down map only then and only with no modifier held, the one
/// state in which the core takes them (`shanjie.h`: "only while the last output had candidate_vertical 1 and no modifier is held").
pub fn map_key(vk: u32, m: Mods, window: bool) -> Option<Key> {
    if m.alt || m.win {
        return None;
    }
    let modifiers = (m.shift as u32 * MOD_SHIFT) | (m.ctrl as u32 * MOD_CONTROL) | (m.caps as u32 * MOD_CAPS);
    let (kind, ch) = if let Some(&(_, kind)) = SPECIAL.iter().find(|(v, _)| *v == vk) {
        (kind, 0)
    } else if (vk == VK_PRIOR || vk == VK_NEXT) && window && modifiers == 0 {
        (if vk == VK_PRIOR { KIND_PAGE_UP } else { KIND_PAGE_DOWN }, 0)
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

/// The keycaps whose Shift form is a punctuation key of docs/spec/keys-and-composition.md §8.1 (Shift+, gives ，, Shift+1 gives ！, …).
const SHIFT_PUNCT: [char; 13] = [',', '.', '/', '1', ';', '\\', '=', '[', ']', '9', '0', '`', '\''];

/// `OnTestKeyDown` does not advance the engine, so it can only say "may eat". With a composition or a candidate window open every mapped key may be
/// eaten, whatever the scheme; that covers what the core takes with a window open (rules 3v-8, and rules 3m-8m of scheme 1: the digits 1-9, Up, Down, Left, Right,
/// Space, Enter, Esc, Backspace, Page Up, Page Down, and in scheme 1 every other key too), checked against the real core by the host crate's `window_keys_match_the_core`.
/// Without one, exactly the keys the core handles with an empty composition (English input and prediction off, standard layout) under `scheme` (the host's
/// `shanjie_engine_set_key_scheme` value, from this connection's `Welcome`; this module never reads the registry, and a value other than 1 is read as 0): a plain character
/// key (a Zhuyin, tone or punctuation key, rules 2, 14, 22a), a Shift+key of the §8.1 punctuation list (scheme 1: not Shift+, and Shift+., which
/// the core passes on, rule 2m), and Ctrl+\ (scheme 1: also Ctrl+, and Ctrl+., rule 1m).
/// Everything else the core passes through (rules 1 and 22): Space, Shift+letter, Caps Lock on, other modifiers. Those must be
/// refused here: in an IMM32 host a key `OnTestKeyDown` claims is lost when `OnKeyDown` then lets it go (docs/spec/windows-tip.md §5.4).
pub fn may_eat(scheme: u8, composing: bool, window: bool, k: &Key) -> bool {
    if composing || window {
        return true;
    }
    if k.kind != KIND_CHAR {
        return false;
    }
    let ms = scheme == 1;
    let comma_or_period = k.ch == ',' as u32 || k.ch == '.' as u32;
    match k.modifiers {
        0 => true,
        MOD_SHIFT => char::from_u32(k.ch).is_some_and(|c| SHIFT_PUNCT.contains(&c)) && !(ms && comma_or_period),
        MOD_CONTROL => k.ch == '\\' as u32 || (ms && comma_or_period),
        _ => false,
    }
}

/// `may_eat` with an empty composition for a thread that has no connection yet, so no `Welcome` and no scheme: only a key that both schemes refuse is
/// refused without connecting (the caller then connects for any other key and decides again with the scheme it learns).
pub fn may_eat_either(k: &Key) -> bool {
    may_eat(0, false, false, k) || may_eat(1, false, false, k)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plain() -> Mods {
        Mods::default()
    }
    fn key(vk: u32, m: Mods) -> Key {
        map_key(vk, m, false).expect("mapped")
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
    fn unlisted_keys_are_not_sent() {
        for window in [false, true] {
            for vk in [0x10, 0x11, 0x12, 0x14, 0x70, 0x60, 0x6B, 0xE5, 0x00] {
                assert!(map_key(vk, plain(), window).is_none(), "vk {vk:#x}");
            }
        }
    }

    #[test]
    fn page_keys_map_only_with_a_window_open_and_no_modifier() {
        // Window closed: not mapped, so OnTestKeyDown answers FALSE for them even with a composition open.
        for vk in [0x21, 0x22] {
            assert!(map_key(vk, plain(), false).is_none(), "vk {vk:#x} without a window");
        }
        // Window open: kinds 14 and 15, no character.
        let up = map_key(0x21, plain(), true).expect("Page Up");
        let down = map_key(0x22, plain(), true).expect("Page Down");
        assert!(up.kind == 14 && up.ch == 0 && up.modifiers == 0);
        assert!(down.kind == 15 && down.ch == 0 && down.modifiers == 0);
        // Any modifier: not sent (the core would not handle it, shanjie.h), including Caps Lock.
        for vk in [0x21, 0x22] {
            for m in [Mods { shift: true, ..plain() }, Mods { ctrl: true, ..plain() }, Mods { caps: true, ..plain() }, Mods { alt: true, ..plain() }, Mods { win: true, ..plain() }] {
                assert!(map_key(vk, m, true).is_none(), "vk {vk:#x} with a modifier");
            }
        }
        // The window changes nothing else.
        for vk in [0x20, 0x0D, 0x28, 0x31, 0x41] {
            assert!(map_key(vk, plain(), true) == map_key(vk, plain(), false), "vk {vk:#x}");
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
            assert!(map_key(vk, Mods { alt: true, ..plain() }, false).is_none());
            assert!(map_key(vk, Mods { win: true, ..plain() }, false).is_none());
            assert!(map_key(vk, Mods { alt: true, ctrl: true, ..plain() }, false).is_none(), "AltGr");
        }
    }

    #[test]
    fn on_test_key_down_with_an_empty_composition() {
        // Plain character keys (Zhuyin, tone, punctuation) and the §8.1 Shift punctuation may be eaten.
        assert!(may_eat(0, false, false, &key(0x41, plain())));
        assert!(may_eat(0, false, false, &key(0x31, plain())));
        assert!(may_eat(0, false, false, &key(0xBC, plain())));
        for vk in [0xBC, 0xBE, 0xBF, 0x31, 0xBA, 0xDC, 0xBB, 0xDB, 0xDD, 0x39, 0x30, 0xC0, 0xDE] {
            assert!(may_eat(0, false, false, &key(vk, Mods { shift: true, ..plain() })), "Shift+vk {vk:#x}");
        }
        // What the core passes with an empty composition goes to the app (rules 1 and 22): Space, Shift+letter, Shift+2..8,
        // Shift+-, and any key with Caps Lock on.
        assert!(!may_eat(0, false, false, &key(0x20, plain())));
        assert!(!may_eat(0, false, false, &key(0x41, Mods { shift: true, ..plain() })));
        assert!(!may_eat(0, false, false, &key(0x32, Mods { shift: true, ..plain() })));
        assert!(!may_eat(0, false, false, &key(0xBD, Mods { shift: true, ..plain() })));
        assert!(!may_eat(0, false, false, &key(0x41, Mods { caps: true, ..plain() })));
        assert!(!may_eat(0, false, false, &key(0x31, Mods { caps: true, ..plain() })));
        // Everything else goes to the app.
        for vk in [0x0D, 0x08, 0x2E, 0x1B, 0x25, 0x27, 0x26, 0x28, 0x24, 0x23, 0x09] {
            assert!(!may_eat(0, false, false, &key(vk, plain())), "vk {vk:#x}");
        }
        // Ctrl+letter goes to the app; Ctrl+\ may be eaten; Ctrl+Shift+\ may not.
        assert!(!may_eat(0, false, false, &key(0x41, Mods { ctrl: true, ..plain() })));
        assert!(!may_eat(0, false, false, &key(0x20, Mods { ctrl: true, ..plain() })));
        assert!(may_eat(0, false, false, &key(0xDC, Mods { ctrl: true, ..plain() })));
        assert!(!may_eat(0, false, false, &key(0xDC, Mods { ctrl: true, shift: true, ..plain() })));
    }

    #[test]
    fn on_test_key_down_with_a_composition() {
        for vk in [0x41, 0x31, 0x20, 0x0D, 0x08, 0x2E, 0x1B, 0x25, 0x27, 0x26, 0x28, 0x24, 0x23, 0x09, 0xBC] {
            assert!(may_eat(0, true, false, &key(vk, plain())), "vk {vk:#x}");
        }
        assert!(may_eat(0, true, false, &key(0x41, Mods { ctrl: true, ..plain() })));
    }

    #[test]
    fn on_test_key_down_with_a_window_open() {
        // The keys of rules 3v-8, with a window open.
        for vk in [0x31, 0x39, 0x28, 0x26, 0x25, 0x27, 0x20, 0x0D, 0x1B, 0x08] {
            assert!(may_eat(0, false, true, &map_key(vk, plain(), true).unwrap()), "vk {vk:#x}");
        }
        for vk in [0x21, 0x22] {
            assert!(may_eat(0, false, true, &map_key(vk, plain(), true).unwrap()), "page key {vk:#x}");
        }
    }

    #[test]
    fn on_test_key_down_in_scheme_1_with_an_empty_composition() {
        let shift = Mods { shift: true, ..plain() };
        let ctrl = Mods { ctrl: true, ..plain() };
        // Shift+, and Shift+. are passed on by the core (rule 2m, K8): refused in scheme 1, still eaten in scheme 0.
        for vk in [0xBC, 0xBE] {
            assert!(may_eat(0, false, false, &key(vk, shift)), "scheme 0, Shift+vk {vk:#x}");
            assert!(!may_eat(1, false, false, &key(vk, shift)), "scheme 1, Shift+vk {vk:#x}");
        }
        // The other Shift punctuation is eaten in both.
        for vk in [0xBF, 0x31, 0xBA, 0xDC, 0xBB, 0xDB, 0xDD, 0x39, 0x30, 0xC0, 0xDE] {
            assert!(may_eat(0, false, false, &key(vk, shift)) && may_eat(1, false, false, &key(vk, shift)), "Shift+vk {vk:#x}");
        }
        // Ctrl+, and Ctrl+. are the full-width marks in scheme 1 (rule 1m), passed on in scheme 0; Ctrl+\ is eaten in both; other Ctrl keys in neither.
        for vk in [0xBC, 0xBE] {
            assert!(!may_eat(0, false, false, &key(vk, ctrl)) && may_eat(1, false, false, &key(vk, ctrl)), "Ctrl+vk {vk:#x}");
            assert!(!may_eat(1, false, false, &key(vk, Mods { ctrl: true, shift: true, ..plain() })), "Ctrl+Shift+vk {vk:#x}");
            assert!(!may_eat(1, false, false, &key(vk, Mods { ctrl: true, caps: true, ..plain() })), "Ctrl+Caps+vk {vk:#x}");
        }
        assert!(may_eat(0, false, false, &key(0xDC, ctrl)) && may_eat(1, false, false, &key(0xDC, ctrl)));
        assert!(!may_eat(1, false, false, &key(0x41, ctrl)) && !may_eat(1, false, false, &key(0x20, ctrl)));
        // Unchanged in scheme 1: plain keys eaten; Space, Shift+letter, Enter and the like passed on.
        assert!(may_eat(1, false, false, &key(0x41, plain())) && may_eat(1, false, false, &key(0xBC, plain())));
        assert!(!may_eat(1, false, false, &key(0x20, plain())) && !may_eat(1, false, false, &key(0x41, shift)) && !may_eat(1, false, false, &key(0x0D, plain())));
        // A scheme the host never sends is read as 0.
        assert!(may_eat(2, false, false, &key(0xBC, shift)) && !may_eat(2, false, false, &key(0xBC, ctrl)));
    }

    #[test]
    fn a_composition_or_a_window_eats_every_mapped_key_in_both_schemes() {
        for scheme in [0u8, 1] {
            for vk in [0x41, 0x31, 0x20, 0x0D, 0x08, 0x2E, 0x1B, 0x25, 0x27, 0x26, 0x28, 0x24, 0x23, 0x09, 0xBC] {
                assert!(may_eat(scheme, true, false, &key(vk, plain())) && may_eat(scheme, false, true, &key(vk, plain())), "scheme {scheme} vk {vk:#x}");
            }
            assert!(may_eat(scheme, true, false, &key(0x41, Mods { shift: true, ..plain() })));
            assert!(may_eat(scheme, false, true, &key(0xBC, Mods { ctrl: true, ..plain() })));
        }
    }

    #[test]
    fn without_a_connection_only_a_key_both_schemes_refuse_is_refused() {
        let shift = Mods { shift: true, ..plain() };
        let ctrl = Mods { ctrl: true, ..plain() };
        assert!(may_eat_either(&key(0x41, plain())));
        assert!(may_eat_either(&key(0xBC, shift)), "eaten in scheme 0");
        assert!(may_eat_either(&key(0xBC, ctrl)), "eaten in scheme 1");
        for k in [key(0x20, plain()), key(0x41, shift), key(0x0D, plain()), key(0x41, ctrl), key(0xBD, shift), key(0x41, Mods { caps: true, ..plain() })] {
            assert!(!may_eat_either(&k));
        }
    }
}
