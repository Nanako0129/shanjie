import CShanjie
import Foundation

/// `shanjie --selftest` (docs/contracts/s3b.md section 9): build the engine from Resources, load
/// the LM, type dev302 row 284 with the standard layout and require the chat and formal top-1
/// sentences, then build with the bundled ACG pack (Resources/packs), require the same sentences and one pack word, then load the
/// small model (Resources/tinylm.sjtl) and require the rerank to change a chat sentence, and the switch off not to (s7c2b section
/// 2.5), then load the word list (Resources/english-words.txt) and require `rust` Space to open the English window with `rust`
/// first (zhen-mixed section 3.2), and the colloquial table (Resources/colloquial.sjlm): with the rerank on and the context
/// 桌子一直在搖 the chat sentence for dev302 row 202 must be 桌腳的螺絲鬆掉了 (colloquial-term.md section 2.3). Returns the exit status. Writes nothing but a fixed message and a return code to stderr; touches no file, UserDefaults, TIS, NSApplication or IMK server.
@MainActor
public enum Selftest {
    /// Row 284 `ㄔㄨˊ ㄈㄤˊ ㄒㄧㄣ ㄓㄨㄤ ㄌㄜ˙ ㄧ ㄍㄜ˙ ㄌㄩˋ ㄕㄨㄟˇ ㄏㄨˊ` on the standard layout (s3a section 1);
    /// it was row 10 before S2k, whose chat and formal answers the word-class term made equal, and row 226
    /// before dec-recombine, whose chat and formal answers the one-path-per-(surface, last word) decoder made equal;
    /// a space is the first tone. The tests derive the same keys from the zhuyin independently.
    static let row284Standard = "tj6z;6vup 5j; xk7u ek7xm4gjo3cj6"
    /// Formal is 律水壺 with trigram.sjt3 beside the model and 濾水壺 without it, so this also catches a missing table.
    static let expected: [(profile: UInt32, text: String)] = [(0, "廚房新裝了一個率水壺"), (1, "廚房新裝了一個律水壺")]

    /// The bundled word pack (acg-pack contract A.2): 碇源堂 `ㄉㄧㄥˋ ㄩㄢˊ ㄊㄤˊ` on the standard layout. The chat
    /// profile spells it 定元堂 without the pack, so the sentence is the pack word only if the pack loaded.
    static let packWordStandard = "2u/4m06w;6"
    static let packWord = "碇源堂"
    /// The bundled IT+AI pack (it-pack contract 3.2): 圖像生成 `ㄊㄨˊ ㄒㄧㄤˋ ㄕㄥ ㄔㄥˊ` on the standard layout, chat profile. It is in the pack's hand-written list, and
    /// both profiles spell it 圖象生成 without the pack and 圖像生成 with it (measured with the CLI), so the sentence is the word only if the pack loaded. The pack is on in the
    /// engine below, as the shell ships it.
    static let itWordStandard = "wj6vu;4g/ t/6"
    static let itWord = "圖像生成"
    /// The bundled meme pack (meme-pack contract 3.2): the word is not fixed here. The selftest reads the first data row of the bundled
    /// `packs/meme-add.tsv` (comment and blank lines skipped) and requires the standard-layout keys of its reading to commit exactly that word
    /// (chat profile, a fresh composition), so a rebuilt pack does not break it; a missing, empty or unreadable file fails too. The data
    /// PR's first row therefore has to be a word the pack really makes the sentence decode type (the meme-pack check set's rule).
    /// The Dachen keys of the 41 symbols (37 zhuyin and 4 tones), the standard layout of s3a section 1; a space is the first tone.
    static let standardKeys: [Character: Character] = {
        let pairs = zip("ㄅㄉˇˋㄓˊ˙ㄚㄞㄢㄦㄆㄊㄍㄐㄔㄗㄧㄛㄟㄣㄇㄋㄎㄑㄕㄘㄨㄜㄠㄤㄈㄌㄏㄒㄖㄙㄩㄝㄡㄥ", "1234567890-qwertyuiopasdfghjkl;zxcvbnm,./")
        return Dictionary(uniqueKeysWithValues: pairs.map { ($0, $1) })
    }()

    /// The first data row of the bundled meme pack as (word, standard-layout keys); nil when the file is missing or has no usable row.
    static func memeSample(_ resources: URL) -> (word: String, keys: String)? {
        guard let text = try? String(contentsOf: resources.appendingPathComponent("packs/meme-add.tsv"), encoding: .utf8) else { return nil }
        for line in text.split(separator: "\n") where !line.hasPrefix("#") && !line.allSatisfy(\.isWhitespace) {
            let f = line.split(separator: "\t", omittingEmptySubsequences: false)
            guard f.count >= 2, !f[1].isEmpty else { return nil }
            var keys = ""
            for syllable in f[0].split(separator: "-") {
                for ch in syllable {
                    guard let k = standardKeys[ch] else { return nil }
                    keys.append(k)
                }
                if let last = syllable.last, !"ˊˇˋ˙".contains(last) { keys.append(" ") }
            }
            return (String(f[1]), keys)
        }
        return nil
    }

    /// classes-v5 (2026-10-16, docs/contracts/classes-v5.md): dev302 row 27 `ㄨㄛˇ ㄧˇ ㄐㄧㄥ ㄏㄨㄟˊ ㄈㄨˋ ㄋㄧˇ ㄉㄜ˙ ㄧㄡˊ ㄐㄧㄢˋ ㄌㄜ˙` on the
    /// standard layout, chat profile, no left context, the bundled pack loaded: the n-gram's first is 我已經回復你的郵件了 and
    /// tinylm-v5 picks 回覆. Measured with the CLI (chat dev302 rowstats, with and without `--packs acg`: wrong without
    /// `--tinylm`, right with it) and by SelftestTests/RerankTests. Row 65 below stopped working because μ 0.2 makes the n-gram
    /// itself give 再過去. History: s7c2b section 2.5 (revised 2026-10-12): dev302 row 65 `ㄨㄛˇ ㄉㄥˇ ㄧ ㄒㄧㄚˋ ㄗㄞˋ ㄍㄨㄛˋ ㄑㄩˋ` on the standard layout, chat
    /// profile, no left context, the bundled pack loaded; a space is the first tone. The n-gram's first is 我等一下在過去; the small
    /// model picks 我等一下再過去, tinylm-v2 (2026-10-12), tinylm-v3 (2026-10-13) and tinylm-v4 (2026-10-14; its standard level equals v3's) alike: measured by this selftest under these
    /// conditions (SelftestTests) and by RerankTests without the pack. Under model-v6 + kn-v1 + tinylm-v5 (chat standard 0.3/2/0.25)
    /// `SelftestTests.testPassesWithTheFullResources` and the RerankTests passed on 2026-10-15 (`make test-swift`). Row 25 (執行這個城市 -> 程式), the sentence under tinylm-v1,
    /// stayed 城市 under v2 in the same XCTest run (14 failures before the swap); v3 changes it again, and it is the device sentence
    /// (s7h section 1, `DeviceRow` in the tests). A different condition, for context only, under v2: the CLI with `--profile chat
    /// --context --tinylm` and no pack gave top1 0 -> 4 on rows 25, 27, 46, 49 and 65, row 25 being the one that did not change.
    /// The tests derive the same keys from the zhuyin independently.
    static let rerankRowStandard = "ji3u3ru/ cjo6zj4su32k7u.6ru04xk7"
    static let rerankedText = "我已經回覆你的郵件了"
    static let ngramText = "我已經回復你的郵件了"

    /// colloquial-term.md section 2.3: dev302 row 202 `ㄓㄨㄛ ㄐㄧㄠˇ ㄉㄜ˙ ㄌㄨㄛˊ ㄙ ㄙㄨㄥ ㄉㄧㄠˋ ㄌㄜ˙` on the standard layout, chat profile, standard
    /// strength, the bundled pack, the left context of that row. The n-gram's first and the shipping rerank give 桌腳的羅斯松掉了; the
    /// colloquial term makes the rerank pick 螺絲鬆 (measured with the CLI, `--context`, one row: c 0 without `--colloquial-core`, c 4 with it).
    static let colloquialRowStandard = "5ji rul32k7xji6n nj/ 2ul4xk7"
    static let colloquialContext = "桌子一直在搖"
    static let colloquialText = "桌腳的螺絲鬆掉了"

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
                                                  packs: UInt32(SHANJIE_PACK_ACG) | UInt32(SHANJIE_PACK_IT) | UInt32(SHANJIE_PACK_MEME))
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
        guard let meme = memeSample(resources) else { return fail("selftest: the bundled meme pack has no usable first row, code", 0) }
        let (memeShown, memeCode) = type(meme.keys, withPack)
        guard memeCode == 0 else { return fail("selftest: shanjie_engine_key (meme pack) failed, code", memeCode) }
        guard memeShown == meme.word else { return fail("selftest: the bundled meme word pack is not in effect, code", 0) }
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

    /// Row 284 in both profiles; nil when both match, else the exit status.
    private static func checkSentences(_ engine: CoreEngine) -> Int32? {
        for (profile, text) in expected {
            if case .failed(let c) = engine.setProfile(profile) {
                return fail("selftest: shanjie_engine_set_profile failed, code", c)
            }
            let (committed, c) = type(row284Standard, engine)
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
