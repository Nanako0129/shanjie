import CShanjie
import Foundation

/// `shanjie --selftest` (docs/contracts/s3b.md section 9): build the engine from Resources, load
/// the LM, type dev302 row 226 with the standard layout and require the chat and formal top-1
/// sentences. Returns the exit status. Writes nothing but a fixed message and a return code to
/// stderr; touches no file, UserDefaults, TIS, NSApplication or IMK server.
@MainActor
public enum Selftest {
    /// Row 226 `ㄒㄧㄥˋ ㄏㄠˇ ㄐㄧㄡˋ ㄏㄨˋ ㄔㄜ ㄐㄧˊ ㄕˊ ㄍㄢˇ ㄉㄠˋ` on the standard layout (s3a section 1);
    /// it was row 10 before S2k, whose chat and formal answers the word-class term made equal;
    /// a space is the first tone. The tests derive the same keys from the zhuyin independently.
    static let row226Standard = "vu/4cl3ru.4cj4tk ru6g6e032l4"
    static let expected: [(profile: UInt32, text: String)] = [(0, "幸好救護車及時趕到"), (1, "幸好救護車即時趕到")]

    public static func run(resources: URL) -> Int32 {
        let (made, code) = CoreEngine.make(dataDir: resources.path, layout: 0)
        guard let engine = made else { return fail("selftest: shanjie_engine_new failed, code", code) }
        let lm = engine.loadLM(path: resources.appendingPathComponent("bigram.sjlm").path)
        guard lm == 0 else { return fail("selftest: shanjie_engine_load_lm failed, code", lm) }
        for (profile, text) in expected {
            if case .failed(let c) = engine.setProfile(profile) {
                return fail("selftest: shanjie_engine_set_profile failed, code", c)
            }
            var keys = row226Standard.unicodeScalars.map {
                $0 == " " ? ShanjieKey(kind: KeyMap.space, ch: 0, modifiers: 0)
                          : ShanjieKey(kind: KeyMap.char, ch: $0.value, modifiers: 0)
            }
            keys.append(ShanjieKey(kind: KeyMap.enter, ch: 0, modifiers: 0))
            var committed = ""
            for k in keys {
                switch engine.key(k) {
                case .ok(let o): committed += o.commit
                case .failed(let c): return fail("selftest: shanjie_engine_key failed, code", c)
                }
            }
            guard committed == text else { return fail("selftest: unexpected sentence for profile", Int32(profile)) }
        }
        return 0
    }

    /// Only the fixed message and a number; never input or output text.
    private static func fail(_ message: StaticString, _ code: Int32) -> Int32 {
        FileHandle.standardError.write(Data("\(message) \(code)\n".utf8))
        return 1
    }
}
