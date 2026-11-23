//! Colloquial sandhi in the engine (docs/spec/decoder.md §6.6 and §8.7, docs/spec/keys-and-composition.md §3.2): the engine as the App builds it. Needs data/lm
//! like engine_lm.rs and fails loudly without it. The position table is tested in core/src/tone3.rs, the evaluation CLI in cli/tests/colloq.rs.
use core::engine::*;
use std::sync::OnceLock;

#[path = "common/toy.rs"]
mod toy;
use toy::{keys_of, real_lm_path, root};

const APP_PACKS: u32 = PACK_ACG | PACK_IT | PACK_MEME | PACK_BEAUTY;

/// One engine loaded from the files; every test engine is made from its shared parts (`Engine::new_shared`), with the App's switches.
/// `toy::shared`: the lexicon, the capped lexicon, the model.
fn parts() -> &'static toy::Shared {
    toy::shared()
}

fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| {
        let mut e = Engine::new_with_packs(&root().join("data/lexicon"), Layout::Standard, Some((&root().join("data/packs"), APP_PACKS))).unwrap();
        e.load_lm(&real_lm_path()).unwrap();
        e.share().unwrap()
    })
}

fn app(tone3: bool) -> Engine {
    let mut e = Engine::new_shared(shared(), Layout::Standard, Some((&root().join("data/packs"), APP_PACKS))).unwrap();
    e.set_english(true).unwrap();
    e.set_english_overwrite(false).unwrap();
    e.set_tone3(tone3).unwrap();
    e
}

/// One key's output, every field.
fn show(o: &Output) -> String {
    format!(
        "{}|{}|{}|{}|{:?}|{:?}|{}|{}|{}|{}|{}|{}",
        o.handled, o.commit, o.preedit, o.cursor_utf16, o.candidates, o.selected, o.columns, o.first, o.total, o.vertical, o.english_run_utf16_start, o.english_run_utf16_len
    )
}

fn trace(e: &mut Engine, typed: &str) -> Vec<String> {
    typed.chars().map(|c| show(&e.key(Key::ch(c, 0)).expect("no internal error"))).collect()
}

/// The outputs the branch point gave for `typed` (core/tests/fixtures/colloq-branch-traces.tsv).
fn branch(typed: &str) -> Vec<String> {
    let rows: Vec<String> = include_str!("fixtures/colloq-branch-traces.tsv")
        .lines()
        .filter(|l| !l.starts_with('#'))
        .filter_map(|l| {
            let mut f = l.splitn(3, '\t');
            (f.next() == Some(typed)).then(|| f.nth(1).unwrap().to_string())
        })
        .collect();
    assert_eq!(rows.len(), typed.chars().count(), "the fixture has {typed}");
    rows
}

fn caps(e: &mut Engine, typed: &str) -> Output {
    typed.chars().map(|c| e.key(Key::ch(c, 0)).expect("no internal error")).last().unwrap()
}

fn keys(e: &mut Engine, syls: &[&str]) -> Output {
    let mut last = None;
    for s in syls {
        for k in keys_of(Layout::Standard, s) {
            last = Some(e.key(k).expect("no internal error"));
        }
    }
    last.unwrap()
}

fn press(e: &mut Engine, kind: KeyKind) -> Output {
    e.key(Key::new(kind)).expect("no internal error")
}

/// With the window open: pages (Space) until `word` is on the page shown, and picks it; `None` when no page has it.
fn find_and_pick(e: &mut Engine, mut o: Output, word: &str) -> Option<Output> {
    for _ in 0..=(o.total as usize / 9 + 1) {
        if let Some(i) = o.candidates.iter().position(|c| c == word) {
            return e.pick(i).unwrap();
        }
        o = press(e, KeyKind::Space);
    }
    None
}

fn syls(e: &Engine) -> Vec<&str> {
    e.syllables().iter().map(String::as_str).collect()
}

/// docs/spec/keys-and-composition.md §3.3: ㄌㄧˋ ㄏㄞ˙ (x u 4 c 9 7), App defaults: ˙ㄏㄞ has no one-syllable entry and stands after a fourth tone, so the branch point reverted it to English
/// (step 0, the fixture). Now both syllables stay, no English run, and 厲害 shows.
#[test]
fn li_hai_is_accepted_and_shows_the_word() {
    let lex = &*parts().lex;
    assert!(!lex.has_single("ㄏㄞ˙") && lex.has_single("ㄏㄞˋ"), "premise");
    assert!(branch("xu4c97")[5].contains("|力c97|"), "premise: the branch point reverted the last syllable to English");
    let mut e = app(true);
    let o = caps(&mut e, "xu4c97");
    assert_eq!(syls(&e), ["ㄌㄧˋ", "ㄏㄞ˙"]);
    assert_eq!((o.preedit.as_str(), o.english_run_utf16_len), ("厲害", 0));
}

/// docs/spec/keys-and-composition.md §3.3: once accepted, the neutral syllable keeps a path whatever happens before it: (a) the syllable before it picked as another character, (b) a syllable
/// typed between the two, (c) the syllable before it deleted, so it is the first. No error, no reset, the syllable stays and shows a character.
#[test]
fn the_accepted_syllable_keeps_a_path_when_what_is_before_it_changes() {
    // (a)
    let mut e = app(true);
    caps(&mut e, "xu4c97");
    press(&mut e, KeyKind::Left);
    let o = press(&mut e, KeyKind::Down);
    let o = find_and_pick(&mut e, o, "立").expect("立 is listed for ㄌㄧˋ");
    assert_eq!(syls(&e), ["ㄌㄧˋ", "ㄏㄞ˙"], "(a)");
    assert!(o.preedit.starts_with('立') && o.preedit.chars().count() == 2 && !o.preedit.contains('ㄏ'), "(a) {}", o.preedit);
    // (b)
    let mut e = app(true);
    caps(&mut e, "xu4c97");
    press(&mut e, KeyKind::Left);
    let o = keys(&mut e, &["ㄉㄚˋ"]);
    assert_eq!(syls(&e), ["ㄌㄧˋ", "ㄉㄚˋ", "ㄏㄞ˙"], "(b)");
    assert!(o.preedit.chars().count() == 3 && !o.preedit.contains('ㄏ'), "(b) {}", o.preedit);
    // (c)
    let mut e = app(true);
    caps(&mut e, "xu4c97");
    press(&mut e, KeyKind::Left);
    let o = press(&mut e, KeyKind::Backspace);
    assert_eq!(syls(&e), ["ㄏㄞ˙"], "(c)");
    assert!(o.preedit.chars().count() == 1 && o.preedit != "ㄏ", "(c) {}", o.preedit);
}

/// docs/spec/keys-and-composition.md §3.3: every neutral syllable of the probe word list (eval/probe/colloq-probe-words.tsv: the words the two dictionaries read with a neutral tone; the
/// syllable is the word's lexicon syllable at that position with the neutral mark) that has no one-syllable entry, typed alone after one syllable, is
/// accepted and decodes: two syllables, two characters.
#[test]
fn every_neutral_syllable_of_the_word_list_is_accepted_after_a_syllable() {
    let lex = &*parts().lex;
    let list = std::fs::read_to_string(root().join("eval/probe/colloq-probe-words.tsv")).unwrap();
    let at: std::collections::HashMap<&str, Vec<usize>> = list.lines().filter(|l| !l.starts_with('#')).map(|l| l.split('\t').collect::<Vec<_>>()).filter(|c| c[2] == "neutral").fold(
        std::collections::HashMap::new(),
        |mut m, c| {
            m.entry(c[0]).or_default().push(c[1].parse::<usize>().unwrap() - 1);
            m
        },
    );
    let mut seen = std::collections::BTreeSet::new();
    for f in ["mcbpmf-data.txt", "overlay-add.tsv"] {
        for l in std::fs::read_to_string(root().join("data/lexicon").join(f)).unwrap().lines() {
            let c: Vec<&str> = l.split_whitespace().collect();
            if let Some(ps) = c.get(1).and_then(|w| at.get(w)) {
                let r: Vec<&str> = c[0].split('-').collect();
                seen.extend(ps.iter().filter_map(|&i| r.get(i)).map(|s| format!("{}˙", s.trim_end_matches(['ˊ', 'ˇ', 'ˋ', '˙']))).filter(|s| !lex.has_single(s)));
            }
        }
    }
    assert!(seen.contains("ㄏㄞ˙") && seen.contains("ㄓㄚ˙") && seen.len() >= 17, "{seen:?}");
    for s in &seen {
        // A syllable typed with keys of the digit alphabet alone (˙ㄓㄚ is 5 8 7) is the digit layer's while that layer is on (the next test); K takes it
        // with the layer off.
        let digits = keys_of(Layout::Standard, s).iter().all(|k| k.ch.is_ascii_digit());
        let mut e = app(true);
        if digits {
            e.set_english(false).unwrap();
        }
        let o = keys(&mut e, &["ㄏㄠˇ", s]);
        assert_eq!(syls(&e), ["ㄏㄠˇ", s.as_str()], "{s}");
        assert_eq!((o.preedit.chars().count(), o.english_run_utf16_len), (2, 0), "{s}: {}", o.preedit);
    }
}

/// docs/spec/keys-and-composition.md §3.3: K does not accept the neutral syllable as the first syllable of the composition (it reverts as before), nor one none of whose four tones has an entry.
#[test]
fn not_accepted_first_or_without_any_entry() {
    let mut e = app(true);
    let o = caps(&mut e, "c97");
    assert!(syls(&e).is_empty());
    assert_eq!(o.preedit, "c97");
    let lex = &*parts().lex;
    assert!(["ㄅㄧㄚ˙", "ㄅㄧㄚ", "ㄅㄧㄚˊ", "ㄅㄧㄚˇ", "ㄅㄧㄚˋ"].iter().all(|s| !lex.has_single(s)), "premise");
    let mut e = app(true);
    caps(&mut e, "xu4");
    caps(&mut e, "1u87");
    assert_eq!(syls(&e), ["ㄌㄧˋ"]);
}

/// docs/spec/keys-and-composition.md §3.3: with the switch off every key of ㄌㄧˋ ㄏㄞ˙ gives the branch point's output, field for field, and there is no 厲害; turning it off with the
/// syllable accepted stores it as its best alternative and keeps the composition.
#[test]
fn off_is_the_branch_point_and_turning_off_keeps_the_composition() {
    let mut off = app(false);
    assert_eq!(trace(&mut off, "xu4c97"), branch("xu4c97"));
    assert_eq!(syls(&off), ["ㄌㄧˋ"]);
    let mut e = app(true);
    caps(&mut e, "xu4c97");
    let o = e.set_tone3(false).unwrap();
    let now = syls(&e);
    assert!(now.len() == 2 && now[0] == "ㄌㄧˋ" && now[1].starts_with("ㄏㄞ") && !now[1].ends_with('˙'), "{now:?}");
    assert!(o.preedit.chars().count() == 2 && o.preedit != "厲害" || now[1] == "ㄏㄞˋ", "{}", o.preedit);
    // A colloquial word picked in the window is no word of the lexicon read with the switch off: unfixed, not an error.
    let mut e = app(true);
    keys(&mut e, &["ㄖㄜˋ", "ㄊㄥ", "ㄊㄥ"]);
    let o = press(&mut e, KeyKind::Down);
    find_and_pick(&mut e, o, "熱騰騰").expect("熱騰騰 is listed for ㄖㄜˋ ㄊㄥ ㄊㄥ");
    let o = e.set_tone3(false).unwrap();
    assert_eq!(syls(&e), ["ㄖㄜˋ", "ㄊㄥ", "ㄊㄥ"]);
    assert!(o.preedit.chars().count() == 3 && o.preedit != "熱騰騰", "{}", o.preedit);
}

/// docs/spec/keys-and-composition.md §3.3: the digit layer does not hand K its syllables: with the layer on (the App's engine), inputs with neutral syllables typed by digit keys alone
/// (`1 0 7` ˙ㄅㄢ, `2 0 7` ˙ㄉㄢ, `5 8 7` ˙ㄓㄚ) after a syllable give the same output with the switch on and off at every key.
#[test]
fn the_digit_layer_is_the_same_with_the_switch_on_and_off() {
    let inputs: [&[&str]; 4] = [&["ㄇㄧㄣˊ", "ㄍㄨㄛˊ", "107", "ㄋㄧㄢˊ"], &["ㄇㄣˊ", "ㄆㄞˊ", "207"], &["ㄉㄧˋ", "587"], &["ㄈㄤˊ", "ㄐㄧㄢ", "107"]];
    for parts in inputs {
        let (mut on, mut off) = (app(true), app(false));
        let mut ks: Vec<Key> = Vec::new();
        for p in parts {
            if p.is_ascii() {
                ks.extend(p.chars().map(|c| Key::ch(c, 0)));
            } else {
                ks.extend(keys_of(Layout::Standard, p));
            }
        }
        ks.push(Key::new(KeyKind::Enter));
        let mut sent = String::new();
        for (i, k) in ks.into_iter().enumerate() {
            let (a, b) = (on.key(k).unwrap(), off.key(k).unwrap());
            assert!(a == b, "{parts:?}: key {i} differs");
            sent.push_str(&a.commit);
        }
        assert!(sent.contains("107") || sent.contains("207") || sent.contains("587"), "{parts:?}: {sent}");
    }
}

/// docs/spec/keys-and-composition.md §3.3: ㄆㄧㄠˋ ㄌㄧㄤ˙: ˙ㄌㄧㄤ has no one-syllable entry and 漂亮 no colloquial row; K accepts the syllable and the neutral source reads the word under
/// ㄆㄧㄠˋ ㄌㄧㄤˋ. ㄒㄧㄠˊ ㄐㄧㄝ˙ and ㄓㄚˊ ㄧㄢˇ give the branch point's output at every key.
#[test]
fn the_neutral_source_reads_the_word_and_the_sandhi_examples_are_unchanged() {
    let lex = &*parts().lex;
    assert!(!lex.has_single("ㄌㄧㄤ˙") && !lex.has(&["ㄆㄧㄠˋ".to_string(), "ㄌㄧㄤ˙".to_string()], "漂亮"), "premise");
    let mut e = app(true);
    let o = keys(&mut e, &["ㄆㄧㄠˋ", "ㄌㄧㄤ˙"]);
    assert_eq!(syls(&e), ["ㄆㄧㄠˋ", "ㄌㄧㄤ˙"]);
    assert_eq!(o.preedit.chars().count(), 2);
    assert_eq!(o.preedit, "漂亮");
    for typed in ["vul6ru,7", "586u03"] {
        let mut e = app(true);
        assert_eq!(trace(&mut e, typed), branch(typed), "{typed}");
    }
}

/// The window lists the word of the neutral source after the exact words; picked, the span takes the lexicon reading it was found under, like any sandhi word.
/// Off, it is not listed.
#[test]
fn the_window_lists_the_colloquial_word() {
    for on in [true, false] {
        let mut e = app(on);
        if !on {
            keys(&mut e, &["ㄖㄜˋ", "ㄊㄥ", "ㄊㄥ"]);
            let o = press(&mut e, KeyKind::Down);
            assert!(find_and_pick(&mut e, o, "熱騰騰").is_none());
            continue;
        }
        keys(&mut e, &["ㄖㄜˋ", "ㄊㄥ", "ㄊㄥ"]);
        let o = press(&mut e, KeyKind::Down);
        find_and_pick(&mut e, o, "熱騰騰").expect("熱騰騰 is listed for ㄖㄜˋ ㄊㄥ ㄊㄥ");
        let mut e = app(true);
        caps(&mut e, "xu4c97");
        let o = press(&mut e, KeyKind::Down);
        find_and_pick(&mut e, o, "厲害").expect("厲害 is listed for ㄌㄧˋ ㄏㄞ˙");
        assert_eq!(syls(&e), ["ㄌㄧˋ", "ㄏㄞˋ"]);
        let o = press(&mut e, KeyKind::Enter);
        assert_eq!(o.commit, "厲害");
    }
}

/// docs/spec/decoder.md §6.6: a word read through K's alternatives pays `DELTA_NEUTRAL` once, is no fuzzy word, and is scored as under the lexicon reading it was found
/// under; the word the sandhi rule already read under Xˇ keeps that rule's penalty. The neutral source: a multi-syllable word, the penalty once.
#[test]
fn the_penalties_are_taken_once() {
    use core::lm::{decode_from_fuzzy_scored, Profile};
    use core::tone3::{positions, DELTA_NEUTRAL, DELTA_TONE3};
    let st = parts();
    let (lex, capped, lm): (&core::Lexicon, &core::lm::CappedLexicon, &core::lm::Lm) = (&st.lex, &st.capped, &st.lm);
    let v = |s: &[&str]| s.iter().map(|x| x.to_string()).collect::<Vec<_>>();
    let decode = |typed: &[String], on: bool| {
        let t3 = positions(typed, lex, on);
        decode_from_fuzzy_scored(capped, typed, lm, Profile::Chat, core::BEAM_S1, "<s>", true, 0, core::fuzzy::DELTA_SOUND, None, on.then_some((&t3[..], DELTA_TONE3))).unwrap()
    };
    let kd = DELTA_NEUTRAL;
    assert!(lex.has(&v(&["ㄏㄞˊ"]), "還") && !lex.has(&v(&["ㄏㄞˇ"]), "還") && lex.has(&v(&["ㄏㄞˇ"]), "海"), "premise");
    let nb = decode(&v(&["ㄏㄞ˙"]), true);
    let (score, ws) = nb.iter().find(|(_, ws)| ws[0].0 == "還").expect("還 is read through ㄏㄞˊ");
    assert_eq!((ws[0].2, ws[0].4), (kd, false), "K's penalty, once; no fuzzy marker");
    let (s0, _) = decode(&v(&["ㄏㄞˊ"]), false).into_iter().find(|(_, ws)| ws[0].0 == "還").unwrap();
    assert!((score - (s0 - kd)).abs() < 1e-9, "{score} vs {s0}");
    assert_eq!(nb.iter().find(|(_, ws)| ws[0].0 == "海").expect("海, through ㄏㄞˇ").1[0].2, DELTA_TONE3, "the sandhi rule's word keeps its penalty");
    // 知道 for ㄓ ˙ㄉㄠ: the neutral source, under ㄓ ㄉㄠˋ.
    let nb = decode(&v(&["ㄓ", "ㄉㄠ˙"]), true);
    let (score, ws) = nb.iter().find(|(_, ws)| ws.len() == 1 && ws[0].0 == "知道").expect("知道 is decoded");
    assert_eq!((ws[0].2, ws[0].4), (DELTA_NEUTRAL, false));
    let (s0, _) = decode(&v(&["ㄓ", "ㄉㄠˋ"]), false).into_iter().find(|(_, ws)| ws.len() == 1 && ws[0].0 == "知道").unwrap();
    assert!((score - (s0 - DELTA_NEUTRAL)).abs() < 1e-9, "{score} vs {s0}");
    assert!(decode(&v(&["ㄓ", "ㄉㄠ˙"]), false).iter().all(|(_, ws)| ws.iter().all(|w| w.0 != "知道")), "off: no neutral source");
    // A colloquial row is an entry of the typed reading: no penalty beside its score, and not read with the switch off.
    let typed = v(&["ㄖㄜˋ", "ㄊㄥ", "ㄊㄥ"]);
    let on = decode(&typed, true);
    let ws = &on.iter().find(|(_, ws)| ws.len() == 1 && ws[0].0 == "熱騰騰").expect("熱騰騰").1;
    assert_eq!((ws[0].2, ws[0].4), (0.0, false));
    assert!(decode(&typed, false).iter().all(|(_, ws)| ws.iter().all(|w| w.0 != "熱騰騰")), "off: the row is not read");
}

/// docs/spec/decoder.md §6.6: switching off stores an unaccepted neutral tail after an unaccepted second tone as Xˇ, judged on the syllables as typed (not after the
/// earlier syllable was rewritten to Xˇ); any other neutral syllable K took as its best-scoring alternative.
#[test]
fn switching_off_judges_the_tail_on_the_syllables_as_typed() {
    let mut e = app(true);
    e.set_english(false).unwrap();
    keys(&mut e, &["ㄓㄢˊ", "ㄉㄠ˙"]);
    assert_eq!(syls(&e), ["ㄓㄢˊ", "ㄉㄠ˙"], "premise");
    e.set_tone3(false).unwrap();
    assert_eq!(syls(&e), ["ㄓㄢˇ", "ㄉㄠˇ"]);
    let mut e = app(true);
    e.set_english(false).unwrap();
    keys(&mut e, &["ㄒㄧㄢˊ", "ㄉㄠ˙"]);
    assert_eq!(syls(&e), ["ㄒㄧㄢˊ", "ㄉㄠ˙"], "premise");
    e.set_tone3(false).unwrap();
    assert_eq!(syls(&e), ["ㄒㄧㄢˊ", "ㄉㄠˇ"]);
}
