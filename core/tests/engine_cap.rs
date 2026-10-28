//! Composition cap (docs/contracts/composition-cap.md).
//! Release only: every test types 70-128 syllables with the small model's rerank, which took 957 s in the debug job on Linux (no
//! Accelerate there; PR #268's first CI run), over that job's 20-minute limit. The `core` job runs this file in release on macOS.
#![cfg(not(debug_assertions))]
use core::engine::*;
use core::eval::{parse_rows, usable};
use core::lm::Profile;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
fn data(f: &str) -> PathBuf {
    let p = root().join("data/lm").join(f);
    assert!(p.exists(), "data/lm/{f} is needed: see data/*.sha256 and docs/releases/README.md");
    p
}
fn keys_of(syl: &str) -> Vec<Key> {
    let layout = Layout::Standard;
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match layout.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(layout.key_of_symbol(c).expect("symbol has a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}
fn k(kind: KeyKind) -> Key {
    Key::new(kind)
}

/// The shipping engine of the App: n-gram, small model, colloquial table, rerank on, chat.
fn engine(rerank: bool) -> Engine {
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&data("bigram.sjlm")).unwrap();
    e.load_tinylm(&data("tinylm.sjtl")).unwrap();
    e.load_colloquial(&data("colloquial.sjlm")).unwrap();
    e.set_profile(Profile::Chat).unwrap();
    e.set_rerank(rerank).unwrap();
    e
}

fn dev_syls() -> Vec<Vec<String>> {
    static S: OnceLock<Vec<Vec<String>>> = OnceLock::new();
    S.get_or_init(|| {
        let lex = load_lexicon(&root().join("data/lexicon")).unwrap();
        let mut files: Vec<PathBuf> = std::fs::read_dir(root().join("eval/dev"))
            .unwrap()
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.extension().is_some_and(|x| x == "txt"))
            .collect();
        files.sort();
        let mut rows = Vec::new();
        for f in files {
            rows.extend(parse_rows(&std::fs::read_to_string(f).unwrap()).unwrap());
        }
        let mut rows = usable(&lex, rows);
        rows.truncate(302);
        rows.into_iter().map(|r| r.reading.clone().unwrap_or_else(|| lex.to_syllables(&r.sent).unwrap())).collect()
    })
    .clone()
}



/// The syllables of dev302 row `first`, then those of the following rows (from row `fill` on, wrapping) that fit, up to `n` in all;
/// rows with a syllable that is not plain zhuyin are skipped, and when no row fits what is left (after one round of the rows) a row's
/// front part does. One syllable per character of every row's sentence, so a character is a
/// syllable below.
fn material(first: usize, n: usize, fill: usize) -> Vec<String> {
    let rows = dev_syls();
    let mut out = rows[first - 1].clone();
    let mut i = fill - 1;
    let mut spins = 0;
    while out.len() < n {
        let r = &rows[i % rows.len()];
        let plain = r.iter().all(|s| s.chars().all(|c| ('ㄅ'..='ㄩ').contains(&c) || "ˊˇˋ˙".contains(c)));
        if spins >= rows.len() && plain && out.len() + r.len() > n {
            // No row fits the rest any more: a row's front part does.
            out.extend(r[..n - out.len()].iter().cloned());
        } else if out.len() + r.len() <= n && plain {
            out.extend(r.iter().cloned());
        }
        i += 1;
        spins += 1;
        assert!(spins < 2000, "no rows to fill the material");
    }
    out
}

/// The 70 syllables most tests type: dev302 row 25 (the n-gram has 城市 there, and with these rows after it the small model picks 程式),
/// then rows 47 on that fit. No punctuation; one character per syllable.
fn long_material() -> Vec<String> {
    material(25, 70, 47)
}

/// The composition-cap buffer H (`CAP_BUFFER`): a stretch past W is frozen down to W - H characters.
const H: usize = 16;

/// The small model's window minus 3 characters (docs/contracts/composition-cap.md section 1.2); the model of data/lm has ctx 64.
const W: usize = 61;

/// What the job of the latest decode holds: the most characters of any candidate (0: no job), run and applied, as `--rerank each`
/// does before the next key. Returns that and the output of the applied job when it changed the display.
fn settle(e: &mut Engine) -> (usize, Option<Output>) {
    match e.take_rerank_job() {
        Some(job) => (job.max_candidate_chars(), e.apply_rerank(job.run()).unwrap()),
        None => (0, None),
    }
}

/// One syllable; returns the output of the key that finished it (the others only fill the columns).
fn syllable(e: &mut Engine, s: &str) -> Output {
    let keys = keys_of(s);
    let (last, init) = keys.split_last().unwrap();
    for key in init {
        e.key(*key).unwrap();
    }
    e.key(*last).unwrap()
}

/// The frozen words as (start, end, word).
fn frozen(e: &Engine) -> Vec<(usize, usize, String)> {
    e.frozen_words()
}
/// The frozen head: the frozen words from syllable 0 on, one after the other; its text.
fn head_text(e: &Engine) -> String {
    let mut at = 0;
    let mut text = String::new();
    for (a, b, w) in frozen(e) {
        assert_eq!(a, at, "the frozen words start at 0 and follow each other");
        at = b;
        text.push_str(&w);
    }
    text
}

fn assert_frozen(e: &Engine, want: &[(usize, usize, &str)], what: &str) {
    let got = frozen(e);
    let got: Vec<(usize, usize, &str)> = got.iter().map(|f| (f.0, f.1, f.2.as_str())).collect();
    assert_eq!(got, want, "{what}");
}

#[test]
fn the_model_window_is_64() {
    assert_eq!(core::tinylm::TinyLm::load(&data("tinylm.sjtl")).unwrap().ctx(), W + 3);
}

/// Contract 2, test 1. 70 syllables, no punctuation, typed with `--rerank each` timing (each job runs and applies before the next
/// key). Row 25 is first: the n-gram has 城市 there and the small model shows 程式. From the 62nd key on, the head is frozen word by
/// word down to W - H characters: no candidate of any job is longer than W, the frozen words are what the previous key showed (the
/// n-gram decode of the key itself would show 城市 again, so this is the snapshot of the path at work), the output of the key is what
/// Enter sends, and the model's 程式 is still there after it left the window. `b` is the control: the same keys with rerank off, which
/// freezes the n-gram path (it shows 城市 in the head).
#[test]
fn the_head_of_a_long_composition_is_frozen_word_by_word() {
    let syls = long_material();
    assert_eq!(syls.len(), 70);
    let (mut a, mut b) = (engine(true), engine(false));
    let mut shown = String::new();
    let mut jobs = 0;
    for (i, s) in syls.iter().enumerate() {
        let n = i + 1;
        let raw = syllable(&mut a, s);
        // The output of the key is the composition Enter would send: the same preedit comes back from a key that decodes nothing.
        assert_eq!(a.key(k(KeyKind::Right)).unwrap().preedit, raw.preedit, "key {n}: the output is the state");
        let (job_max, applied) = settle(&mut a);
        let now = applied.map_or(raw.preedit, |o| o.preedit);
        syllable(&mut b, s);
        if n >= 62 {
            assert!(job_max <= W, "key {n}: a candidate of {job_max} characters is longer than the window allows");
            jobs += (job_max > 0) as usize;
            let head = head_text(&a);
            let len = head.chars().count();
            assert!(len >= n - W, "key {n}: the head holds {len} characters");
            assert_eq!(now.chars().take(len).collect::<String>(), head, "key {n}: the frozen words are the display");
            assert_eq!(shown.chars().take(len).collect::<String>(), head, "key {n}: the frozen words are what the key before showed");
        } else {
            assert!(frozen(&a).is_empty(), "key {n}: nothing is frozen while the stretch fits");
        }
        shown = now;
    }
    assert!(jobs > 0, "the jobs were made");
    let (head, b_head) = (head_text(&a), head_text(&b));
    assert!(head.chars().count() >= 62 - (W - H), "the head after the freeze at key 62 holds the {} characters over W - H: {head}", 62 - (W - H));
    assert!(head.contains("程式") && b_head.contains("城市"), "premise: the model's word {head} against the n-gram's {b_head}");
    assert_frozen(&a, &EXPECT_TEST1, "the frozen words after key 70");
    assert_eq!(a.key(k(KeyKind::Enter)).unwrap().commit, shown, "Enter sends the display");
}

/// Contract 2, test 2. 61 syllables fit; the cursor goes after the first one and a syllable is typed there. The new syllable and the
/// words after it are outside the common prefix of the syllables before and after the key: they are not frozen, nothing fails, and
/// the job (whose stretch is now longer than the window) runs.
#[test]
fn a_syllable_typed_in_front_of_the_stretch_freezes_nothing_after_it() {
    let syls = material(25, 62, 41);
    let mut e = engine(true);
    for s in &syls[..61] {
        syllable(&mut e, s);
        settle(&mut e);
    }
    assert!(frozen(&e).is_empty());
    e.key(k(KeyKind::Home)).unwrap();
    e.key(k(KeyKind::Right)).unwrap();
    let o = syllable(&mut e, &syls[61]);
    assert_eq!(o.preedit.chars().count(), 62);
    // Only a word wholly before the new syllable (the first one, when it is one character) can be frozen: it is the only one in the
    // common prefix.
    assert!(frozen(&e).iter().all(|f| f.1 <= 1), "frozen: {:?}", frozen(&e));
    let (_, applied) = settle(&mut e);
    let o = applied.unwrap_or(o);
    assert_eq!(o.preedit.chars().count(), 62);
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, o.preedit);
}

/// 78 syllables (`material(25, 78, 45)`) typed with rerank each: the freeze at key 62 left a head of 18 characters (…會議 已經, the last
/// word 已經 at 16-18), the free tail has R = 60 characters (the next freeze would come at key 78 + 1 = the tail past W = 61). The cursor is
/// moved to the boundary, in front of the tail.
fn long_composition_at_the_boundary() -> (Engine, Vec<String>, usize) {
    let syls = material(25, 78, 45);
    let mut e = engine(true);
    for s in &syls {
        syllable(&mut e, s);
        settle(&mut e);
    }
    let end = frozen(&e).last().expect("a frozen head").1;
    for _ in end..78 {
        e.key(k(KeyKind::Left)).unwrap();
    }
    (e, syls, end)
}

/// Contract 2, test 5 (revision 1). Rerank off, the small model loaded: no job is made, and the head is frozen all the same. 70
/// syllables: from key 62 on the frozen words lead the display and the free tail is at most W characters; up to key 61, where nothing
/// is frozen, the display is what an engine without the small model shows (the freeze needs no model: both agree on every key).
#[test]
fn rerank_off_freezes_the_head_and_shows_the_ngram_first() {
    let syls = long_material();
    let mut off = engine(false);
    let mut plain = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    plain.load_lm(&data("bigram.sjlm")).unwrap();
    for (i, s) in syls.iter().enumerate() {
        let (x, y) = (syllable(&mut off, s), syllable(&mut plain, s));
        let n = i + 1;
        assert!(off.take_rerank_job().is_none(), "no job with rerank off");
        assert_eq!(x.preedit, y.preedit, "key {n}: the model or its switch changes nothing for the n-gram first");
        assert_eq!(frozen(&off), frozen(&plain), "key {n}: the same frozen words");
        let head = head_text(&off);
        let len = head.chars().count();
        if n <= W {
            assert!(head.is_empty(), "key {n}: nothing frozen while the stretch fits");
        } else {
            assert!(len >= n - W && x.preedit.starts_with(&head), "key {n}: a frozen head of {len} leads the display");
        }
        assert!(n - len <= W, "key {n}: the free tail is {} characters", n - len);
    }
}

/// Contract 2, test 6. Two sentences of 40 characters with a punctuation mark between: deleting the mark joins them into a stretch of
/// 80. The same key freezes the head of the first sentence word by word until the stretch fits.
#[test]
fn deleting_a_mark_between_two_long_sentences_freezes_the_front_one() {
    let (first, second) = (material(25, 40, 40), material(27, 40, 120));
    let mut e = engine(true);
    for s in &first {
        syllable(&mut e, s);
        settle(&mut e);
    }
    e.key(Key::ch(',', MOD_SHIFT)).unwrap();
    settle(&mut e);
    for s in &second {
        syllable(&mut e, s);
        settle(&mut e);
    }
    assert!(frozen(&e).is_empty());
    let before = e.key(k(KeyKind::End)).unwrap().preedit;
    assert_eq!(before.chars().count(), 81);
    for _ in 0..40 {
        e.key(k(KeyKind::Left)).unwrap();
    }
    let raw = e.key(k(KeyKind::Backspace)).unwrap();
    assert_eq!(raw.preedit.chars().count(), 80);
    let head = head_text(&e);
    let len = head.chars().count();
    assert!(len >= 80 - (W - H), "the head holds {len} characters: the stretch of 80 is cut to {}, not more than W - H = {}", 80 - len, W - H);
    assert_eq!(before.chars().take(len).collect::<String>(), head, "the frozen words are what was shown");
    assert_eq!(raw.preedit.chars().take(len).collect::<String>(), head);
    let (job_max, _) = settle(&mut e);
    assert!(job_max <= W, "{job_max} characters in a candidate");
}

/// Contract 2, test 7. The 128th unit sends everything; the 127th does not.
#[test]
fn the_128th_unit_sends_and_the_127th_does_not() {
    let mut e = engine(true);
    for i in 1..MAX_SYLLABLES {
        let o = syllable(&mut e, "ㄋㄧˇ");
        assert!(o.handled && o.commit.is_empty() && o.preedit.chars().count() == i, "unit {i}");
        settle(&mut e);
    }
    let o = syllable(&mut e, "ㄋㄧˇ");
    assert!(o.handled && o.commit.chars().count() == MAX_SYLLABLES && o.preedit.is_empty(), "the 128th unit sends everything");
    assert!(e.take_rerank_job().is_none() && frozen(&e).is_empty());
}

/// Contract 2, test 8. After a job changed the display, ← and ↓ leave the display, the job and the decode count alone; and a key whose
/// stretch fits decodes once, with rerank on and off alike (the freeze does not depend on the switch since revision 1).
#[test]
fn keys_that_decode_nothing_leave_the_state_alone_and_a_fitting_key_decodes_once() {
    let syls = long_material();
    let (mut a, mut b) = (engine(true), engine(false));
    for (i, s) in syls.iter().enumerate() {
        let (da, db) = (a.decodes(), b.decodes());
        syllable(&mut a, s);
        syllable(&mut b, s);
        let (xa, xb) = (a.decodes() - da, b.decodes() - db);
        assert_eq!(xa, xb, "key {}: the same decodes with rerank on and off", i + 1);
        if i < W {
            assert_eq!(xa, 1, "key {}: the stretch fits, one decode", i + 1);
        } else {
            assert!(xa <= 2, "key {}: {xa} decodes", i + 1);
        }
        settle(&mut a);
    }
    assert!(!frozen(&a).is_empty());
    // A changed display to hold on to: the model shows 程式 where the n-gram shows 城市 (the frozen head keeps it).
    let shown = a.key(k(KeyKind::End)).unwrap().preedit;
    assert!(shown.contains("程式"));
    let decodes = a.decodes();
    let job = a.take_rerank_job();
    assert!(job.is_none(), "the job was taken and applied");
    for key in [k(KeyKind::Left), k(KeyKind::Left), k(KeyKind::Down)] {
        let o = a.key(key).unwrap();
        assert_eq!(o.preedit, shown, "the display after {:?}", key.kind as u32);
        assert!(a.take_rerank_job().is_none());
        assert_eq!(a.decodes(), decodes, "no decode");
    }
}

/// Contract 2, test 9 (revision 1). Four arms type 127 syllables (no punctuation; the 128th would send them) and check, after every key,
/// that no free stretch is longer than W, and, once after the 127th, that the keys that decoded twice (`Engine::decodes` up by 2) number
/// at most ceil((127 - W) / H) + 1: the freeze runs down to W - H, so about one key in H freezes. The arms: (a) small model loaded, rerank
/// on; (b) loaded, rerank off; (c) no small model (W is 61 then: the first freeze is at the 62nd key); (d) loaded, rerank on, demotion
/// table off (no jobs). H = 0 makes the freeze come at every key past W (65 double decodes), and gating the freeze on `jobs_on()`, on the
/// small model or on the demotion table leaves (b), (c) or (d) with free stretches up to 127.
/// `Engine::decodes` counts the calls of `refresh`, the one place that decodes (settings and `clear_all` do not count). A key that types at the
/// end decodes once, and the freeze adds one: the left neighbour of the cursor is a free word there, so repick-freeze 7.4's extra tentative
/// decodes (a frozen neighbour touching the cursor) do not occur, and the bound counts keys with two decodes.
#[test]
fn the_freeze_has_a_buffer_and_needs_neither_the_rerank_nor_the_model() {
    let syls = material(25, MAX_SYLLABLES - 1, 47);
    assert_eq!(syls.len(), MAX_SYLLABLES - 1);
    let bound = (MAX_SYLLABLES - 1 - W).div_ceil(H) + 1;
    for arm in ["a rerank on", "b rerank off", "c no small model", "d no demotion table"] {
        let mut e = match arm.as_bytes()[0] {
            b'a' => engine(true),
            b'b' => engine(false),
            b'c' => {
                let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
                e.load_lm(&data("bigram.sjlm")).unwrap();
                e
            }
            _ => {
                let mut e = engine(true);
                e.set_demote(false).unwrap();
                e
            }
        };
        let mut doubles = 0;
        for (i, s) in syls.iter().enumerate() {
            let n = i + 1;
            let before = e.decodes();
            let o = syllable(&mut e, s);
            let per_key = e.decodes() - before;
            doubles += (per_key >= 2) as usize;
            settle(&mut e);
            let (len, head) = (o.preedit.chars().count(), head_text(&e).chars().count());
            assert_eq!(len, n, "{arm}, key {n}: one character a syllable");
            assert!(len - head <= W, "{arm}, key {n}: a free stretch of {} characters", len - head);
            assert_eq!(head > 0, n > W, "{arm}, key {n}: the first freeze comes at key {}", W + 1);
        }
        assert!(doubles <= bound, "{arm}: {doubles} keys decoded twice, the bound is {bound}");
        assert!(doubles >= 1, "{arm}: the freeze ran");
    }
}

/// Contract 3 and the stop condition: a word the user picked is not touched by the freeze. The first two syllables get a pick that is
/// not the default; the freeze starts after it.
#[test]
fn a_word_the_user_picked_is_never_frozen_over() {
    let syls = long_material();
    let mut e = engine(true);
    syllable(&mut e, &syls[0]);
    syllable(&mut e, &syls[1]);
    let first = e.key(k(KeyKind::Space)).unwrap(); // the window of the two syllables
    let want = first.candidates[1].clone();
    e.key(k(KeyKind::Right)).unwrap();
    let o = e.key(k(KeyKind::Enter)).unwrap();
    assert!(o.preedit.starts_with(&want), "premise: the pick is on screen: {}", o.preedit);
    for s in &syls[2..] {
        syllable(&mut e, s);
        settle(&mut e);
    }
    let head = frozen(&e);
    assert!(!head.is_empty() && head.iter().all(|f| f.0 >= want.chars().count()), "frozen after the pick: {head:?}");
    assert!(e.key(k(KeyKind::End)).unwrap().preedit.starts_with(&want), "the picked word is where it was");
}

const EXPECT_TEST1: [(usize, usize, &str); 7] = [(0, 2, "執行"), (2, 4, "這個"), (4, 6, "程式"), (6, 8, "他是"), (8, 11, "我媽媽"), (11, 13, "這個"), (13, 17, "數學公式")];


const HEAD18: [(usize, usize, &str); 9] = [
    (0, 2, "執行"),
    (2, 4, "這個"),
    (4, 6, "程式"),
    (6, 8, "結果"),
    (8, 10, "不如"),
    (10, 12, "預期"),
    (12, 14, "你的"),
    (14, 16, "帳單"),
    (16, 18, "已經"),
];
const TAIL_R: usize = 60;

/// Contract 2, test 3. At the boundary a syllable is typed that makes a word with the last frozen word (已經 + ㄧㄡˇ: 已經有). The word
/// is released as repick-freeze section 6.1 says, and released, the stretch (已經, the new syllable, R = 60 characters: 63) is longer
/// than W, so the same key freezes 已經 again: the frozen words are those of before, and no candidate is longer than W. Without the
/// second freeze the list lacks 已經 and the job holds 63 characters. (At 78 syllables: the freeze of key 62 left the head of 18
/// characters and the next freeze is not due before the tail is past W.)
#[test]
fn a_frozen_word_that_merges_with_the_new_syllable_is_frozen_again() {
    let (mut e, _, end) = long_composition_at_the_boundary();
    assert_eq!(end, 18);
    assert_frozen(&e, &HEAD18, "before");
    let raw = syllable(&mut e, "ㄧㄡˇ");
    assert_frozen(&e, &HEAD18, "after the key: the merge is cancelled, 已經 is frozen again");
    let (job_max, applied) = settle(&mut e);
    assert!(job_max <= W, "{job_max} characters in a candidate: the stretch fits");
    assert_eq!(2 + 1 + TAIL_R, 63, "R = {TAIL_R} makes the released stretch longer than W");
    let shown = applied.map_or(raw.preedit, |o| o.preedit);
    assert!(shown.starts_with("執行這個程式結果不如預期你的帳單已經"), "{shown}");
    assert_eq!(shown.chars().count(), 79);
    e.key(k(KeyKind::End)).unwrap();
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, shown);
}

/// Contract 2, test 4. At the boundary Backspace takes the 經 out of the frozen 已經 (repick-freeze section 15: the 已 stays as a
/// frozen piece), and the next syllable typed frees the piece and, as the stretch (已, the new syllable, R = 60 characters: 62) is longer
/// than W, freezes it again. Without the second freeze the list lacks 已 and the job holds 62 characters.
#[test]
fn a_piece_left_by_backspace_is_frozen_again_when_a_syllable_follows() {
    let (mut e, _, _) = long_composition_at_the_boundary();
    let mut with_piece: Vec<_> = HEAD18[..8].to_vec();
    with_piece.push((16, 17, "已"));
    e.key(k(KeyKind::Backspace)).unwrap();
    assert_frozen(&e, &with_piece, "the piece 已 is left");
    settle(&mut e);
    let raw = syllable(&mut e, "ㄏㄠˇ");
    assert_frozen(&e, &with_piece, "after the key: 已 is frozen again");
    let (job_max, applied) = settle(&mut e);
    assert!(job_max <= W, "{job_max} characters in a candidate: the stretch fits");
    assert_eq!(1 + 1 + TAIL_R, 62, "R = {TAIL_R} makes the released stretch longer than W");
    let shown = applied.map_or(raw.preedit, |o| o.preedit);
    assert!(shown.starts_with("執行這個程式結果不如預期你的帳單已"), "{shown}");
    assert_eq!(shown.chars().count(), 78);
    e.key(k(KeyKind::End)).unwrap();
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, shown);
}

/// Contract 2, test 2b (revision 2). The same state as test 2 (a free stretch of 61 characters), the cursor at the start: Home, then
/// ㄉㄧㄢˋ ㄋㄠˇ ㄓㄨㄛ. At the second syllable the stretch is past W and the longest common prefix is 1, and the word typed first (電/店) ends
/// less than `CAP_EDGE` syllables before it: it is not frozen, so the next syllables still make 電腦桌 with it (it used to be frozen as
/// the single character and show 店腦). With `CAP_EDGE` = 0 this fails.
#[test]
fn the_word_being_typed_in_the_middle_is_not_frozen_as_a_single_character() {
    let syls = material(25, 61, 47);
    let mut e = engine(true);
    for s in &syls {
        syllable(&mut e, s);
        settle(&mut e);
    }
    assert!(frozen(&e).is_empty());
    e.key(k(KeyKind::Home)).unwrap();
    for s in ["ㄉㄧㄢˋ", "ㄋㄠˇ", "ㄓㄨㄛ"] {
        let o = syllable(&mut e, s);
        let (_, applied) = settle(&mut e);
        let shown = applied.map_or(o.preedit, |o| o.preedit);
        assert!(frozen(&e).iter().all(|f| f.0 >= 3), "after {s}: frozen {:?} (shown {shown})", frozen(&e));
    }
    let shown = e.key(k(KeyKind::End)).unwrap().preedit;
    assert!(shown.starts_with("電腦桌"), "{shown}");
}

/// The generation filter of `cap_after`: a stretch that stays longer than W after a key that decoded (a syllable typed in front, where the
/// common prefix is too short to freeze anything) must not be frozen by the keys that decode nothing after it. ← and ↓ leave the preedit,
/// the frozen words and the decode count alone. Without the filter the ← key would freeze the head of the 62-character stretch.
#[test]
fn keys_that_decode_nothing_do_not_freeze_a_stretch_that_stays_too_long() {
    let syls = material(25, 62, 47);
    let mut e = engine(true);
    for s in &syls[..61] {
        syllable(&mut e, s);
        settle(&mut e);
    }
    e.key(k(KeyKind::Home)).unwrap();
    for _ in 0..5 {
        e.key(k(KeyKind::Right)).unwrap();
    }
    let o = syllable(&mut e, &syls[61]);
    assert_eq!(o.preedit.chars().count(), 62);
    assert!(frozen(&e).is_empty(), "premise: the edit is too close to anything the stretch could freeze: {:?}", frozen(&e));
    let (_, applied) = settle(&mut e);
    let shown = applied.map_or(o.preedit, |o| o.preedit);
    let decodes = e.decodes();
    for key in [k(KeyKind::Left), k(KeyKind::Down)] {
        let o = e.key(key).unwrap();
        assert_eq!(o.preedit, shown, "preedit after {}", key.kind as u32);
        assert!(frozen(&e).is_empty());
        assert_eq!(e.decodes(), decodes, "no decode after {}", key.kind as u32);
    }
}

/// `forgot`, the ⌘⌫ guard. A pick taught under 帳單 makes the word before the tail show as the taught one and freezes it with the head; the
/// window's ⌘⌫ on it at the boundary (R = 60 characters behind it) releases and forgets it. The stretch (the word, R) is then past W and the
/// frozen word is the first one the freeze would put back, from the path shown before the key: the guard stops there, so the forget shows
/// (the decode without the record), the word is not frozen again and the key does not decode twice.
#[test]
fn a_forgotten_head_word_is_not_frozen_back_in_the_same_key() {
    const DAY: i64 = 20_000;
    let build = || {
        let mut e = engine(false);
        e.set_today(Some(DAY));
        e.set_learning(true);
        e
    };
    let mut e = build();
    // Teach: after 帳單, ㄧˇ ㄐㄧㄥ shows its first candidate; the second is picked and committed.
    e.set_left_context("帳單");
    for s in ["ㄧˇ", "ㄐㄧㄥ"] {
        syllable(&mut e, s);
    }
    let o = e.key(k(KeyKind::Space)).unwrap();
    let default = o.candidates[o.selected.unwrap()].clone();
    // The first other candidate of two characters (the window lists the prediction row's longer words first).
    let taught = loop {
        let o = e.key(k(KeyKind::Right)).unwrap();
        let w = o.candidates[o.selected.unwrap()].clone();
        if w.chars().count() == 2 && w != default {
            break w;
        }
    };
    e.key(k(KeyKind::Enter)).unwrap();
    assert_eq!(e.key(k(KeyKind::Enter)).unwrap().commit, taught);
    assert_eq!(e.learner().records().len(), 1);
    e.set_left_context("");
    let syls = material(25, 78, 45);
    for s in &syls {
        syllable(&mut e, s);
        settle(&mut e);
    }
    let end = frozen(&e).last().unwrap().1;
    let last = frozen(&e).last().unwrap().clone();
    assert!(last.2 == taught && last.1 - last.0 == 2, "premise: the taught word {taught} is the last frozen word: {:?}", frozen(&e));
    for _ in end..78 {
        e.key(k(KeyKind::Left)).unwrap();
    }
    let mut o = e.key(k(KeyKind::Space)).unwrap();
    for _ in 0..50 {
        if o.candidates[o.selected.unwrap()] == taught {
            break;
        }
        o = e.key(k(KeyKind::Right)).unwrap();
    }
    assert_eq!(o.candidates[o.selected.unwrap()], taught);
    let decodes = e.decodes();
    let forget = Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND };
    let o = e.key(forget).unwrap();
    assert_eq!(e.learner().records().len(), 0, "the record is gone");
    let now: String = o.preedit.chars().skip(last.0).take(2).collect();
    assert_eq!(now, "已經", "the forget shows (the decode without the record) instead of {taught}");
    assert!(frozen(&e).iter().all(|f| f.1 <= last.0), "the word is not frozen again: {:?}", frozen(&e));
    assert_eq!(e.decodes() - decodes, 1, "one decode: nothing was frozen after it");
}

/// The alignment guard on the path shown before the key: a composition typed before any model was set has no path (the next `load_lm`
/// does not decode it again). The key after the load decodes it and, there being no path of the old composition to freeze words from,
/// freezes nothing (the stretch stays past W until a key that has a path), without failing.
#[test]
fn a_composition_typed_before_a_model_has_no_path_to_freeze_from() {
    let syls = long_material();
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    for s in &syls[..64] {
        syllable(&mut e, s);
    }
    e.load_lm(&data("bigram.sjlm")).unwrap();
    let o = syllable(&mut e, &syls[64]);
    assert_eq!(o.preedit.chars().count(), 65);
    assert!(frozen(&e).is_empty(), "no path before the key: {:?}", frozen(&e));
    // The next key has a path and freezes.
    syllable(&mut e, &syls[65]);
    assert!(!frozen(&e).is_empty());
}

/// The rows of the ⌘⌫ forgets are built after the freeze's decode, not from the decode before it: a stretch left past W (a syllable typed
/// in front), then the cursor to the end and ⌘⌫ on the prediction row's item and on a row item of the window. Each freezes the head in that
/// key (two decodes: the forget's and the freeze's) and shows rows equal to those of a fresh recompute.
#[test]
fn rows_rebuilt_by_a_forget_come_from_the_decode_after_the_freeze() {
    let syls = material(25, 62, 47);
    let forget = Key { kind: KeyKind::Backspace, ch: '\0', modifiers: MOD_COMMAND };
    for window in [false, true] {
        let mut e = engine(false);
        for s in &syls[..61] {
            syllable(&mut e, s);
        }
        e.key(k(KeyKind::Home)).unwrap();
        for _ in 0..5 {
            e.key(k(KeyKind::Right)).unwrap();
        }
        syllable(&mut e, &syls[61]);
        assert!(frozen(&e).is_empty(), "premise: a stretch past W is left");
        e.key(k(KeyKind::End)).unwrap();
        let end = e.set_prediction(true).unwrap(); // the row at the end (a decode outside a key: it freezes nothing)
        assert!(!end.candidates.is_empty() && frozen(&e).is_empty(), "premise: a prediction row, stretch still past W");
        let o = if window { e.key(k(KeyKind::Space)).unwrap() } else { e.key(k(KeyKind::Tab)).unwrap() };
        assert!(o.selected.is_some(), "premise: an item is selected");
        let decodes = e.decodes();
        let o = e.key(forget).unwrap();
        assert!(!frozen(&e).is_empty(), "the head froze in the forget's key");
        assert_eq!(e.decodes() - decodes, 2, "the forget's decode and the freeze's");
        if window {
            assert!(!o.candidates.is_empty());
        } else {
            let row = o.candidates.clone();
            let fresh = e.set_prediction(true).unwrap().candidates;
            assert_eq!(row, fresh, "the row of the forget is the row of the decode after the freeze");
        }
    }
}
