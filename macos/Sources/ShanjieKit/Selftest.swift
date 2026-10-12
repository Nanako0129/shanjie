import CShanjie
import Foundation

/// `shanjie --selftest` (docs/contracts/s3b.md section 9): build the engine from Resources, load
/// the LM, type dev302 row 226 with the standard layout and require the chat and formal top-1
/// sentences, then build with the bundled ACG pack (Resources/packs), require the same sentences and one pack word, then load the
/// small model (Resources/tinylm.sjtl) and require the rerank to change a chat sentence, and the switch off not to (s7c2b section
/// 2.5). Returns the exit status. Writes nothing but a fixed message and a return code to stderr; touches no file, UserDefaults, TIS, NSApplication or IMK server.
@MainActor
public enum Selftest {
    /// Row 226 `ㄒㄧㄥˋ ㄏㄠˇ ㄐㄧㄡˋ ㄏㄨˋ ㄔㄜ ㄐㄧˊ ㄕˊ ㄍㄢˇ ㄉㄠˋ` on the standard layout (s3a section 1);
    /// it was row 10 before S2k, whose chat and formal answers the word-class term made equal;
    /// a space is the first tone. The tests derive the same keys from the zhuyin independently.
    static let row226Standard = "vu/4cl3ru.4cj4tk ru6g6e032l4"
    static let expected: [(profile: UInt32, text: String)] = [(0, "幸好救護車及時趕到"), (1, "幸好救護車即時趕到")]

    /// The bundled word pack (acg-pack contract A.2): 碇源堂 `ㄉㄧㄥˋ ㄩㄢˊ ㄊㄤˊ` on the standard layout. The chat
    /// profile spells it 定元堂 without the pack, so the sentence is the pack word only if the pack loaded.
    static let packWordStandard = "2u/4m06w;6"
    static let packWord = "碇源堂"

    /// s7c2b section 2.5 (revised 2026-10-12): dev302 row 65 `ㄨㄛˇ ㄉㄥˇ ㄧ ㄒㄧㄚˋ ㄗㄞˋ ㄍㄨㄛˋ ㄑㄩˋ` on the standard layout, chat
    /// profile, no left context, the bundled pack loaded; a space is the first tone. The n-gram's first is 我等一下在過去; the small
    /// model (tinylm-v2) picks 我等一下再過去. Measured under these conditions by this selftest and by RerankTests (2026-10-12, both
    /// pass). Row 25 (執行這個城市 -> 程式), the sentence under tinylm-v1, stayed 城市 under v2 in the same XCTest run (14 failures
    /// before the swap). A different condition, for context only: the CLI with `--profile chat --context --tinylm` and no pack
    /// gave top1 0 -> 4 on rows 25, 27, 46, 49 and 65, row 25 being the one that did not change. The tests derive the same keys
    /// from the zhuyin independently.
    static let rerankRowStandard = "ji32/3u vu84y94eji4fm4"
    static let rerankedText = "我等一下再過去"
    static let ngramText = "我等一下在過去"

    public static func run(resources: URL) -> Int32 {
        let lmPath = resources.appendingPathComponent("bigram.sjlm").path
        // The pack off: the engine the app had before the pack existed. Scoped so it is freed before the
        // second engine exists (only one at a time, like the shell).
        do {
            let (made, code) = CoreEngine.make(dataDir: resources.path, layout: 0)
            guard let engine = made else { return fail("selftest: shanjie_engine_new failed, code", code) }
            let lm = engine.loadLM(path: lmPath)
            guard lm == 0 else { return fail("selftest: shanjie_engine_load_lm failed, code", lm) }
            if let bad = checkSentences(engine) { return bad }
        }
        // The pack on, as the shell ships it (default on): a missing or corrupt bundled pack must fail here,
        // and the ordinary sentence must still come out the same.
        let (packed, packCode) = CoreEngine.make(dataDir: resources.path, layout: 0,
                                                  packsDir: resources.appendingPathComponent("packs").path)
        guard let withPack = packed else { return fail("selftest: shanjie_engine_new_packs failed, code", packCode) }
        let packLM = withPack.loadLM(path: lmPath)
        guard packLM == 0 else { return fail("selftest: shanjie_engine_load_lm (pack) failed, code", packLM) }
        if let bad = checkSentences(withPack) { return bad }
        if case .failed(let c) = withPack.setProfile(0) { return fail("selftest: shanjie_engine_set_profile (pack) failed, code", c) }
        let (word, c) = type(packWordStandard, withPack)
        guard c == 0 else { return fail("selftest: shanjie_engine_key (pack) failed, code", c) }
        guard word == packWord else { return fail("selftest: the bundled word pack is not in effect, code", 0) }
        // The small model, as the shell loads it: a bundle without it (or with one for another n-gram model) fails here, so
        // 語意校正 cannot ship silently doing nothing.
        let tiny = withPack.loadTinyLM(path: resources.appendingPathComponent(Shell.tinyLMFile).path)
        guard tiny == 0 else { return fail("selftest: shanjie_engine_load_tinylm failed, code", tiny) }
        for (on, text) in [(true, rerankedText), (false, ngramText)] {
            if case .failed(let c) = withPack.setRerank(on) { return fail("selftest: shanjie_engine_set_rerank failed, code", c) }
            let (shown, c) = typeReranked(rerankRowStandard, withPack)
            guard c == 0 else { return fail("selftest: rerank typing failed, code", c) }
            guard shown == text else { return fail("selftest: unexpected rerank composition with the switch", on ? 1 : 0) }
        }
        return 0
    }

    /// The keys of a layout string: a space is the first tone, everything else a character key. The one mapping `type` and
    /// `typeReranked` share.
    private static func keyEvents(_ keys: String) -> [ShanjieKey] {
        keys.unicodeScalars.map {
            $0 == " " ? ShanjieKey(kind: KeyMap.space, ch: 0, modifiers: 0)
                      : ShanjieKey(kind: KeyMap.char, ch: $0.value, modifiers: 0)
        }
    }

    /// Types `keys` and after every key takes the job, runs it and applies it, all on this thread (the shell runs it on a queue);
    /// returns the composition shown at the end and discards it, or a nonzero code.
    private static func typeReranked(_ keys: String, _ engine: CoreEngine) -> (String, Int32) {
        var shown = ""
        for k in keyEvents(keys) {
            switch engine.key(k) {
            case .ok(let o): shown = o.preedit
            case .failed(let c): return ("", c)
            }
            let (job, c) = engine.rerankTake(free: { shanjie_rerank_free($0) })
            guard c == 0 else { return ("", c) }
            guard let job else { continue }
            let r = job.run()
            guard r == 0 else { return ("", r) }
            switch job.apply(on: engine) {
            case .ok(let o): shown = o.preedit
            case .failed(let c) where c == SHANJIE_NO_CHANGE: break
            case .failed(let c): return ("", c)
            }
        }
        if case .failed(let c) = engine.reset(mode: 1) { return ("", c) }
        return (shown, 0)
    }

    /// Row 226 in both profiles; nil when both match, else the exit status.
    private static func checkSentences(_ engine: CoreEngine) -> Int32? {
        for (profile, text) in expected {
            if case .failed(let c) = engine.setProfile(profile) {
                return fail("selftest: shanjie_engine_set_profile failed, code", c)
            }
            let (committed, c) = type(row226Standard, engine)
            if c != 0 { return fail("selftest: shanjie_engine_key failed, code", c) }
            if committed != text { return fail("selftest: unexpected sentence for profile", Int32(profile)) }
        }
        return nil
    }

    /// Types `keys` (a space is the first tone) and Enter; returns the committed text, or a nonzero code.
    private static func type(_ keys: String, _ engine: CoreEngine) -> (String, Int32) {
        var all = keyEvents(keys)
        all.append(ShanjieKey(kind: KeyMap.enter, ch: 0, modifiers: 0))
        var committed = ""
        for k in all {
            switch engine.key(k) {
            case .ok(let o): committed += o.commit
            case .failed(let c): return ("", c)
            }
        }
        return (committed, 0)
    }

    /// Only the fixed message and a number; never input or output text.
    private static func fail(_ message: StaticString, _ code: Int32) -> Int32 {
        FileHandle.standardError.write(Data("\(message) \(code)\n".utf8))
        return 1
    }
}
