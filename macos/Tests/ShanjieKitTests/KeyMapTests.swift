import AppKit
import Carbon.HIToolbox
import CShanjie
import XCTest
@testable import ShanjieKit

/// Acceptance 4, key translation (docs/contracts/s3b.md section 6). The expected tables are built
/// from Carbon's kVK_* constants, independently of KeyMap's literal numbers.
final class KeyMapTests: XCTestCase {
    static let ansi: [Int: Character] = [
        kVK_ANSI_A: "a", kVK_ANSI_B: "b", kVK_ANSI_C: "c", kVK_ANSI_D: "d", kVK_ANSI_E: "e",
        kVK_ANSI_F: "f", kVK_ANSI_G: "g", kVK_ANSI_H: "h", kVK_ANSI_I: "i", kVK_ANSI_J: "j",
        kVK_ANSI_K: "k", kVK_ANSI_L: "l", kVK_ANSI_M: "m", kVK_ANSI_N: "n", kVK_ANSI_O: "o",
        kVK_ANSI_P: "p", kVK_ANSI_Q: "q", kVK_ANSI_R: "r", kVK_ANSI_S: "s", kVK_ANSI_T: "t",
        kVK_ANSI_U: "u", kVK_ANSI_V: "v", kVK_ANSI_W: "w", kVK_ANSI_X: "x", kVK_ANSI_Y: "y",
        kVK_ANSI_Z: "z",
        kVK_ANSI_0: "0", kVK_ANSI_1: "1", kVK_ANSI_2: "2", kVK_ANSI_3: "3", kVK_ANSI_4: "4",
        kVK_ANSI_5: "5", kVK_ANSI_6: "6", kVK_ANSI_7: "7", kVK_ANSI_8: "8", kVK_ANSI_9: "9",
        kVK_ANSI_Grave: "`", kVK_ANSI_Minus: "-", kVK_ANSI_Equal: "=", kVK_ANSI_LeftBracket: "[",
        kVK_ANSI_RightBracket: "]", kVK_ANSI_Backslash: "\\", kVK_ANSI_Semicolon: ";",
        kVK_ANSI_Quote: "'", kVK_ANSI_Comma: ",", kVK_ANSI_Period: ".", kVK_ANSI_Slash: "/",
    ]

    static let specials: [Int: UInt32] = [
        kVK_Return: 3, kVK_ANSI_KeypadEnter: 3, kVK_Space: 2, kVK_Delete: 4, kVK_ForwardDelete: 5,
        kVK_Escape: 6, kVK_LeftArrow: 7, kVK_RightArrow: 8, kVK_UpArrow: 9, kVK_DownArrow: 10,
        kVK_Home: 11, kVK_End: 12, kVK_Tab: 13,
    ]

    func testEveryAnsiKeyIsItsUnshiftedCharacter() {
        XCTAssertEqual(Self.ansi.count, 47)
        for (code, c) in Self.ansi {
            let k = KeyMap.translate(keyCode: UInt16(code), flags: [])
            XCTAssertEqual(k?.kind, 1)
            XCTAssertEqual(k?.ch, c.unicodeScalars.first!.value)
            // Shift never changes the character: it is a modifier bit only.
            XCTAssertEqual(KeyMap.translate(keyCode: UInt16(code), flags: .shift)?.ch, c.unicodeScalars.first!.value)
        }
        XCTAssertEqual(KeyMap.ansi.count, Self.ansi.count)
    }

    func testSpecialKeys() {
        for (code, kind) in Self.specials {
            let k = KeyMap.translate(keyCode: UInt16(code), flags: [])
            XCTAssertEqual(k?.kind, kind)
            XCTAssertEqual(k?.ch, 0)
        }
        XCTAssertEqual(KeyMap.specials.count, Self.specials.count)
    }

    func testModifierBits() {
        let cases: [(NSEvent.ModifierFlags, UInt32)] = [
            (.shift, 1), (.control, 2), (.option, 4), (.command, 8), (.capsLock, 16),
            ([.shift, .control, .option, .command, .capsLock], 31), ([], 0), (.function, 0),
        ]
        for (flags, bits) in cases {
            XCTAssertEqual(KeyMap.translate(keyCode: UInt16(kVK_ANSI_A), flags: flags)?.modifiers, bits)
            XCTAssertEqual(KeyMap.translate(keyCode: UInt16(kVK_Space), flags: flags)?.modifiers, bits)
        }
    }

    func testKeysOutsideTheTablesAreNotTranslated() {
        for code in [kVK_ANSI_Keypad0, kVK_ANSI_Keypad5, kVK_ANSI_KeypadPlus, kVK_ANSI_KeypadDecimal,
                     kVK_F1, kVK_F12, kVK_PageUp, kVK_PageDown, kVK_Help, kVK_ISO_Section, kVK_JIS_Yen] {
            XCTAssertNil(KeyMap.translate(keyCode: UInt16(code), flags: []))
        }
    }

    /// Both layouts: every one of the 37 symbol keys and 5 tone keys, typed through the shell into
    /// the real core, shows that symbol (or completes the syllable).
    @MainActor
    func testBothLayoutsThroughTheCore() throws {
        let res = try XCTUnwrap(TestData.resources())
        let shell = Shell(resources: res, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore())
        let c = Controller(shell)
        c.session.activate()
        for eten in [false, true] {
            if eten { c.session.selectLayout(.eten) }
            XCTAssertEqual(Layouts.symbols.count, 37)
            for (symbol, std, et) in Layouts.symbols {
                XCTAssertTrue(c.type(String(eten ? et : std)).allSatisfy { $0 })
                XCTAssertEqual(c.client.marked, symbol, "layout \(eten ? "eten" : "standard")")
                c.press(Keys.esc)  // rule 12: drop the pending syllable
                XCTAssertEqual(c.client.marked, "")
            }
            // ㄇㄚ with each of the five tones completes a syllable: no Zhuyin left in the preedit.
            let ma = eten ? "ma" : "a8"
            for tone in [" "] + Layouts.tones.map({ String(eten ? $0.2 : $0.1) }) {
                c.type(ma + tone)
                XCTAssertFalse(c.client.marked.isEmpty)
                XCTAssertFalse(c.client.marked.contains { ("ㄅ"..."ㄩ").contains(String($0)) }, "tone key")
                c.press(Keys.esc)  // rule 20: clear the composition
                XCTAssertEqual(c.client.marked, "")
            }
            XCTAssertEqual(c.client.text, "", "nothing is committed")
        }
    }
}
