import CShanjie
import Foundation

/// `shanjie --selftest` (docs/contracts/s3b.md section 9): build the engine from Resources, load
/// the LM, type dev302 row 131 with the standard layout and require the chat and formal top-1
/// sentences and dev302 row 10 for the chat one, then build with the bundled ACG pack (Resources/packs), require the same sentences and one pack word, then load the
/// small model (Resources/tinylm.sjtl) and require the rerank to change a chat sentence, and the switch off not to (s7c2b section
/// 2.5), then load the word list (Resources/english-words.txt) and require `rust` Space to open the English window with `rust`
/// first (zhen-mixed section 3.2), and the colloquial table (Resources/colloquial.sjlm): with the rerank on and the context
/// 這間店的蛋糕很好吃 the chat sentence for dev302 row 108 must be 你要不要嚐嚐看 (colloquial-term.md section 2.3). Returns the exit status. Writes nothing but a fixed message and a return code to stderr; touches no file, UserDefaults, TIS, NSApplication or IMK server.
@MainActor
public enum Selftest {
    /// Row 131 `ㄅㄣˇ ㄕˋ ㄒㄧㄚˋ ㄓㄡ ㄙㄢ ㄏㄨㄟˋ ㄧㄡˇ ㄉㄚˋ ㄩˇ` on the standard layout (s3a section 1);
    /// it was row 10 before S2k, whose chat and formal answers the word-class term made equal, row 226
    /// before dec-recombine, whose chat and formal answers the one-path-per-(surface, last word) decoder made equal, and row 284
    /// until SJ-LM-7 (docs/contracts/sj-lm-7-pr2.md), whose chat and formal answers the new model made equal; a space is the first tone.
    /// The tests derive the same keys from the zhuyin independently. Measured 2026-10-27 with the CLI on the final files
    /// (`--context`, with and without `--packs acg,it`, n-gram first and the shipping rerank both give these): chat is 本市下週三會有大雨, formal 本是下週三會有大雨
    /// with trigram.sjt3 beside the model and 本市下週三會有大雨 without it, so this also catches a missing formal table.
    static let row131Standard = "1p3g4vu845. n0 cjo4u.3284m3"
    /// Row 10 `ㄑㄧˊ ㄓㄨㄥ ㄅㄠˋ ㄍㄠˋ ㄇㄧㄥˊ ㄊㄧㄢ ㄧㄠˋ ㄐㄧㄠ`: the chat profile gives 期中報告明天要交 with trigram-chat.sjt3 beside the model and
    /// 期中報告明天要教 without it (the formal profile gives 交 either way), so this catches a missing chat table.
    static let row10Standard = "fu65j/ 1l4el4au/6wu0 ul4rul "
    static let expected: [(profile: UInt32, keys: String, text: String)] = [
        (0, row131Standard, "本市下週三會有大雨"), (1, row131Standard, "本是下週三會有大雨"), (0, row10Standard, "期中報告明天要交"),
    ]

    /// The bundled word pack (acg-pack contract A.2): 榊遊矢 `ㄕㄣˊ ㄧㄡˊ ㄕˇ` on the standard layout. The chat
    /// profile spells it 神遊使 without the pack, so the sentence is the pack word only if the pack loaded. (碇源堂, the word until SJ-LM-7, is in the new
    /// model's vocabulary and comes out first without the pack; measured with the CLI 2026-10-27.)
    static let packWordStandard = "gp6u.6g3"
    static let packWord = "榊遊矢"
    /// The bundled IT+AI pack (it-pack contract 3.2): 螢幕保護器 `ㄧㄥˊ ㄇㄨˋ ㄅㄠˇ ㄏㄨˋ ㄑㄧˋ` on the standard layout, chat profile. It is a row of the pack, and
    /// both profiles spell it 螢幕保護氣 without the pack and 螢幕保護器 with it (measured with the CLI, 2026-10-27; 圖像生成, the word until SJ-LM-7, comes out first
    /// without the pack on the new model), so the sentence is the word only if the pack loaded. The pack is on in the
    /// engine below, as the shell ships it.
    static let itWordStandard = "u/6aj41l3cj4fu4"
    static let itWord = "螢幕保護器"

    /// SJ-LM-7 (2026-10-27, docs/contracts/sj-lm-7-pr2.md): dev302 row 65 `ㄨㄛˇ ㄉㄥˇ ㄧ ㄒㄧㄚˋ ㄗㄞˋ ㄍㄨㄛˋ ㄑㄩˋ` on the standard layout, chat profile, no left context,
    /// the bundled packs loaded, the small model loaded and the colloquial table not yet: the n-gram's first is 我等一下在過去 and the rerank picks 我等一下再過去
    /// (measured with the CLI on the final files, chat standard, `--packs acg,it --tinylm`, without `--colloquial-core`; the CLI's plain dev302 row 65 is the
    /// same with `--context`). A space is the first tone. History: s7c2b section 2.5: this row since 2026-10-12 (tinylm-v2 to v4); classes-v5 (μ 0.2) made the n-gram
    /// itself give 再過去 and the sentence moved to row 27 (我已經回復你的郵件了 -> 回覆), which on the SJ-LM-7 files the rerank alone no longer changes (only
    /// the colloquial term does). Row 25 (執行這個城市 -> 程式) is the device sentence (s7h section 1, `DeviceRow` in the tests).
    /// The tests derive the same keys from the zhuyin independently.
    static let rerankRowStandard = "ji32/3u vu84y94eji4fm4"
    static let rerankedText = "我等一下再過去"
    static let ngramText = "我等一下在過去"

    /// colloquial-term.md section 2.3: dev302 row 108 `ㄋㄧˇ ㄧㄠˋ ㄅㄨˊ ㄧㄠˋ ㄔㄤˊ ㄔㄤˊ ㄎㄢˋ` on the standard layout, chat profile, standard
    /// strength, the bundled pack, the left context of that row. The n-gram's first and the rerank without the table give 你要不要常常看; the
    /// colloquial term makes the rerank pick 嚐嚐看 (measured with the CLI, `--context`, one row: pick 0 without `--colloquial-core`, 1 with it).
    static let colloquialRowStandard = "su3ul41j6ul4t;6t;6d04"
    static let colloquialContext = "這間店的蛋糕很好吃"
    static let colloquialText = "你要不要嚐嚐看"

    /// zhen-mixed: `rust` then Space on the standard layout opens the English window (the shipped list and the tests' synthetic list
    /// both have the word; its leftover ㄔㄧ is not in the lexicon, so these are the window's only items).
    static let englishKeys = "rust "
    static let englishWindow = ["rust", "Rust", "RUST"]

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
                                                  packsDir: resources.appendingPathComponent("packs").path,
                                                  packs: UInt32(SHANJIE_PACK_ACG) | UInt32(SHANJIE_PACK_IT))
        guard let withPack = packed else { return fail("selftest: shanjie_engine_new_packs failed, code", packCode) }
        let packLM = withPack.loadLM(path: lmPath)
        guard packLM == 0 else { return fail("selftest: shanjie_engine_load_lm (pack) failed, code", packLM) }
        if let bad = checkSentences(withPack) { return bad }
        if case .failed(let c) = withPack.setProfile(0) { return fail("selftest: shanjie_engine_set_profile (pack) failed, code", c) }
        let (word, c) = type(packWordStandard, withPack)
        guard c == 0 else { return fail("selftest: shanjie_engine_key (pack) failed, code", c) }
        guard word == packWord else { return fail("selftest: the bundled word pack is not in effect, code", 0) }
        let (itShown, itCode) = type(itWordStandard, withPack)
        guard itCode == 0 else { return fail("selftest: shanjie_engine_key (it pack) failed, code", itCode) }
        guard itShown == itWord else { return fail("selftest: the bundled IT+AI word pack is not in effect, code", 0) }
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
        // The colloquial table, as the shell loads it after the small model: a bundle without it (or with one the core refuses), or where
        // the term does nothing, fails here, so 常談 cannot ship silently doing nothing. The rerank and the context go back afterwards.
        let colloquial = withPack.loadColloquial(path: resources.appendingPathComponent(Shell.colloquialFile).path)
        guard colloquial == 0 else { return fail("selftest: shanjie_engine_load_colloquial failed, code", colloquial) }
        if case .failed(let c) = withPack.setRerank(true) { return fail("selftest: shanjie_engine_set_rerank failed, code", c) }
        let ctx = withPack.setLeftContext(colloquialContext)
        guard ctx == 0 else { return fail("selftest: shanjie_engine_set_left_context failed, code", ctx) }
        let (colloquialShown, cc) = typeReranked(colloquialRowStandard, withPack)
        guard cc == 0 else { return fail("selftest: colloquial typing failed, code", cc) }
        guard colloquialShown == colloquialText else { return fail("selftest: the colloquial term is not in effect, code", 0) }
        _ = withPack.setLeftContext(nil)
        if case .failed(let c) = withPack.setRerank(false) { return fail("selftest: shanjie_engine_set_rerank (off) failed, code", c) }
        // The word list, as the shell loads it: a bundle without it, or with a list the core refuses, fails here, so 中英混打 cannot
        // ship greyed out or silently doing nothing. The switch goes back off afterwards, as it starts.
        let english = withPack.loadEnglish(path: resources.appendingPathComponent(Shell.englishFile).path)
        guard english == 0 else { return fail("selftest: shanjie_engine_load_english failed, code", english) }
        if case .failed(let c) = withPack.setEnglish(true) { return fail("selftest: shanjie_engine_set_english failed, code", c) }
        var window: [String] = []
        for k in keyEvents(englishKeys) {
            switch withPack.key(k) {
            case .ok(let o): window = o.candidates
            case .failed(let c): return fail("selftest: English typing failed, code", c)
            }
        }
        if case .failed(let c) = withPack.reset(mode: 1) { return fail("selftest: shanjie_engine_reset failed, code", c) }
        if case .failed(let c) = withPack.setEnglish(false) { return fail("selftest: shanjie_engine_set_english (off) failed, code", c) }
        guard window == englishWindow else { return fail("selftest: the English window did not open, code", 0) }
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

    /// The `expected` rows in their profiles; nil when all match, else the exit status.
    private static func checkSentences(_ engine: CoreEngine) -> Int32? {
        for (profile, keys, text) in expected {
            if case .failed(let c) = engine.setProfile(profile) {
                return fail("selftest: shanjie_engine_set_profile failed, code", c)
            }
            let (committed, c) = type(keys, engine)
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
