//! docs/contracts/engine-replay.md section 6, item 2: the replay's rules, one test each, on the real engine, n-gram and small model.
//! Rows are lines of eval/dev/user-reported.txt (public, CC0), whose first display is known to be wrong for most of them; every
//! test states the display it relies on, so a model change fails there first. Each engine is about 700 MB, so the tests take turns.
use cli::common::{load_lenient, root};
use cli::replay::{build_engine, keys_of, move_cost, replay_row, summary_line, Cfg, Rerank, RowOut};
use core::engine::{Engine, Key, KeyKind};
use core::eval::Lenient;
use core::lm::Profile;
use std::process::Command;
use std::sync::{Mutex, MutexGuard};

static ONE_ENGINE_AT_A_TIME: Mutex<()> = Mutex::new(());

fn engine(learn: bool) -> (MutexGuard<'static, ()>, Engine) {
    let guard = ONE_ENGINE_AT_A_TIME.lock().unwrap_or_else(|e| e.into_inner());
    let r = root();
    let e = build_engine(&r, &r.join("data/lm/bigram.sjlm"), Some(&r.join("data/lm/tinylm.sjtl")), Profile::Chat, learn).expect("data/lm models");
    (guard, e)
}

/// `前文|句子|讀音`.
struct Row {
    ctx: &'static str,
    gold: &'static str,
    syls: Vec<String>,
}
fn row(line: &'static str) -> Row {
    let f: Vec<&str> = line.split('|').collect();
    Row { ctx: f[0], gold: f[1], syls: f[2].split_whitespace().map(String::from).collect() }
}
/// 我在重打 is shown for it; the gold text is 我再重打. Fix: Left, Left, Space, digit (cursor 4 -> 2).
fn zai() -> Row {
    row("我打錯了我看成號碼|我再重打|ㄨㄛˇ ㄗㄞˋ ㄔㄨㄥˊ ㄉㄚˇ")
}
/// 這加速度很快你看一下 is shown; the gold text has 家 at the second place. Fix: Home, Right, Right (3 keys, not 8 Lefts), Space, digit.
fn jia() -> Row {
    row("|這家速度很快你看一下|ㄓㄜˋ ㄐㄧㄚ ㄙㄨˋ ㄉㄨˋ ㄏㄣˇ ㄎㄨㄞˋ ㄋㄧˇ ㄎㄢˋ ㄧˊ ㄒㄧㄚˋ")
}
/// The gold 奕 sits on the third page of the window for ㄧˋ (checked by hand in the test), so the re-pick pages twice.
fn yi() -> Row {
    row("|奕|ㄧˋ")
}
/// Several rewrites of the small model while it is typed (3 with `Rerank::Each`, measured).
fn guqi() -> Row {
    row("|我好奇其他競品記憶體都佔多少|ㄨㄛˇ ㄏㄠˋ ㄑㄧˊ ㄑㄧˊ ㄊㄚ ㄐㄧㄥˋ ㄆㄧㄣˇ ㄐㄧˋ ㄧˋ ㄊㄧˇ ㄉㄡ ㄓㄢˋ ㄉㄨㄛ ㄕㄠˇ")
}
/// A first tone on every other syllable: ㄍㄣ ㄨㄛˇ ㄕㄨㄛ ㄨㄛˇ ㄗㄞˋ ㄏㄨㄢˋ.
fn tone1() -> Row {
    row("|跟我說我再換|ㄍㄣ ㄨㄛˇ ㄕㄨㄛ ㄨㄛˇ ㄗㄞˋ ㄏㄨㄢˋ")
}

const EACH: Cfg = Cfg { correct: false, rerank: Rerank::Each };
const FIX: Cfg = Cfg { correct: true, rerank: Rerank::Each };

fn go(e: &mut Engine, r: &Row, len: &Lenient, cfg: Cfg) -> RowOut {
    replay_row(e, r.ctx, &r.syls, r.gold, len, cfg).unwrap()
}

/// Rule: `ideal` is the symbol keys plus the tone key (Space for the first tone) plus Enter, and without a re-pick `actual` is
/// exactly the keys typed plus Enter, so the key score of a perfect row is 1. Hand counts: 我再重打 3+3+4+3+1 = 14,
/// 跟我說我再換 3+3+4+3+3+4+1 = 21 (three Space tones).
#[test]
fn ideal_and_actual_are_the_hand_count() {
    let (_g, mut e) = engine(false);
    let len = load_lenient().unwrap();
    for (r, keys) in [(zai(), 14), (tone1(), 21)] {
        let o = go(&mut e, &r, &len, EACH);
        assert_eq!((o.ideal, o.actual, o.picks), (keys, keys, 0), "{}", r.syls.join(" "));
    }
    // Without --correct the first display is what is sent.
    let o = go(&mut e, &zai(), &len, EACH);
    assert!(!o.first_ok && !o.final_ok && o.committed == o.first, "the model shows 我在重打, which is sent as it is");
    assert!(!o.unfixable, "unfixable is only for --correct");
}

#[test]
fn cursor_move_cost_takes_the_shortest_route() {
    assert_eq!(move_cost(4, 2, 4), 2); // Left, Left
    assert_eq!(move_cost(10, 2, 10), 3); // Home, Right, Right
    assert_eq!(move_cost(0, 9, 10), 2); // End, Left
    assert_eq!(move_cost(3, 3, 10), 0);
    assert_eq!(move_cost(2, 4, 10), 2); // Right, Right
    assert_eq!(keys_of("ㄨㄛˇ").unwrap().len(), 3);
    assert_eq!(keys_of("ㄍㄣ").unwrap().len(), 3);
}

/// Rule (section 4): a wrong first display with the gold word in the window at the cursor is fixed by moving the cursor (the
/// shortest way), Space to open, Space for each page before the item, and the item's digit; `actual` is the typed keys plus those plus
/// Enter, and what is sent is the gold text. 我再重打: Left, Left, Space, digit = 4; the 這家 row: Home, Right, Right, Space, digit = 5;
/// 奕 on the third page: Space, Space, Space, digit = 4 (no move: the cursor is already at the end).
#[test]
fn a_repick_costs_the_moves_the_window_and_the_digit() {
    let (_g, mut e) = engine(false);
    let len = load_lenient().unwrap();
    // The page by hand: the first Space opens page one, two more page to the third, which lists 奕 (18 items before it).
    let mut third = None;
    for k in keys_of("ㄧˋ").unwrap().into_iter().chain([Key::new(KeyKind::Space); 3]) {
        let o = e.key(k).unwrap();
        third = Some((o.first, o.candidates.iter().any(|w| w == "奕")));
    }
    assert_eq!(third, Some((18, true)), "奕 is on the third page");
    e.key(Key::new(KeyKind::Esc)).unwrap();
    e.key(Key::new(KeyKind::Esc)).unwrap(); // clears the composition
    for (r, extra) in [(zai(), 4), (jia(), 5), (yi(), 4)] {
        let o = go(&mut e, &r, &len, FIX);
        assert!(!o.first_ok, "the model shows the wrong word first");
        assert!(o.final_ok && !o.unfixable && o.committed == r.gold, "sent text is the gold text");
        assert_eq!((o.picks, o.actual), (1, o.ideal + extra), "{}", r.gold);
    }
}

/// Rule: gold text that no window offers (才 is not a reading of ㄗㄞˋ), or of another length than the reading, is not fixed: no
/// pick, the shown text is sent, the row is `unfixable`, and the loop ends.
#[test]
fn a_gold_text_no_window_offers_is_unfixable() {
    let (_g, mut e) = engine(false);
    let len = load_lenient().unwrap();
    for gold in ["我才重打", "我再重打吧"] {
        let r = Row { gold, ..zai() };
        let o = go(&mut e, &r, &len, FIX);
        assert!(o.unfixable && !o.final_ok && o.picks == 0, "{gold}");
        assert_eq!((o.committed == o.first, o.actual), (true, o.ideal), "{gold}: what is shown is sent, no key is counted for the search");
    }
}

/// Rule: with learning on, the pick of one row is kept for the next row on the same engine (same sentence typed twice: the second
/// first display is the first one's fix, nothing to re-pick); with learning off the second row is wrong again and is re-picked.
#[test]
fn learning_carries_a_pick_to_the_next_row_only_when_on() {
    let len = load_lenient().unwrap();
    for (learn, kept) in [(true, true), (false, false)] {
        let (_g, mut e) = engine(learn);
        let (a, b) = (go(&mut e, &zai(), &len, FIX), go(&mut e, &zai(), &len, FIX));
        assert!(!a.first_ok && a.final_ok && a.picks == 1, "the first time it is re-picked");
        assert_eq!((b.first_ok, b.picks), (kept, !kept as usize), "learn {learn}");
        assert!(b.final_ok);
        assert_eq!(b.actual, b.ideal + if kept { 0 } else { 4 });
    }
}

/// What a hand-written take, run, apply loop sees: (`Some` results after keys other than the last, those that changed the preedit,
/// the preedit after the last key's apply). `each` applies after every key, otherwise only after the last one.
fn by_hand(e: &mut Engine, r: &Row, each: bool) -> (usize, usize, String) {
    e.set_left_context(r.ctx);
    let keys: Vec<_> = r.syls.iter().flat_map(|s| keys_of(s).unwrap()).collect();
    let (mut mid, mut changed, mut shown) = (0, 0, String::new());
    for (i, k) in keys.iter().enumerate() {
        let last = i + 1 == keys.len();
        let o = e.key(*k).unwrap();
        shown = o.preedit.clone();
        if each || last {
            if let Some(job) = e.take_rerank_job() {
                if let Some(a) = e.apply_rerank(job.run()).unwrap() {
                    mid += !last as usize;
                    changed += (a.preedit != o.preedit) as usize;
                    shown = a.preedit;
                }
            }
        }
    }
    e.key(Key::new(KeyKind::Enter)).unwrap();
    (mid, changed, shown)
}

/// Rule (section 3): with `each` the small model's work is applied between the keys (here several times while the row is typed);
/// with `final` nothing is applied while typing and at most once at the end, so `rewrites` <= 1. Counted by a hand-written loop on
/// a second engine, which the replay's counters must agree with, first display included.
#[test]
fn rerank_each_applies_while_typing_and_final_only_at_the_end() {
    let len = load_lenient().unwrap();
    let r = guqi();
    let (_g, mut e) = engine(false);
    let each = go(&mut e, &r, &len, EACH);
    let fin = go(&mut e, &r, &len, Cfg { correct: false, rerank: Rerank::Final });
    let (mid, changed, shown) = by_hand(&mut e, &r, true);
    assert!(each.rewrites >= 2, "this row is rewritten at least twice while it is typed (3 measured)");
    assert_eq!((each.typing_applies, each.rewrites, &each.first), (mid, changed, &shown));
    let (_, changed, shown) = by_hand(&mut e, &r, false);
    assert!(fin.rewrites <= 1, "final applies once, after the last key, not while typing");
    assert_eq!((fin.rewrites, &fin.first), (changed, &shown));
}

/// Rule (section 1.1.3): a reading longer than the composition limit (40 syllables) makes the engine commit text before Enter. That
/// text is part of the first display and of what is sent, `autocommits` counts the row, and a re-pick works on the syllables still
/// in the composition (here 我再重打, four, so Left, Left, Space, digit as without the commit), not on the 44.
#[test]
fn text_committed_mid_row_is_kept_counted_and_not_re_picked() {
    let (_g, mut e) = engine(false);
    let len = load_lenient().unwrap();
    let filler = "你好".repeat(20);
    let mut syls: Vec<String> = ["ㄋㄧˇ", "ㄏㄠˇ"].repeat(20).into_iter().map(String::from).collect();
    syls.extend(zai().syls);
    let long = Row { ctx: "", gold: Box::leak(format!("{filler}我再重打").into_boxed_str()), syls };
    let o = go(&mut e, &long, &len, EACH);
    assert!(o.autocommit && o.first.chars().count() == 44, "the 40 committed characters come first");
    assert!(o.first.starts_with(&filler) && !o.first_ok, "the display ends in 我在重打, which is wrong");
    let short = go(&mut e, &zai(), &len, EACH);
    assert!(!short.autocommit);
    let fixed = go(&mut e, &long, &len, FIX);
    assert!(fixed.final_ok && fixed.committed == long.gold && fixed.picks == 1);
    assert_eq!(fixed.actual, fixed.ideal + 4);
    assert!(summary_line("x", "t", &[o, short], 0).contains("'autocommits': 1,"));
}

/// Rule (section 4): a pick that leaves the shown text as it was ends the re-picking. The table below makes 我在重 and 我再重 differ
/// as whole strings although the first character is right, so the first difference is reported at 我, and picking 我 changes nothing.
#[test]
fn a_pick_that_changes_nothing_ends_the_repicking() {
    let (_g, mut e) = engine(false);
    let len = Lenient::parse("我在重\tZZZ\n").unwrap();
    let o = replay_row(&mut e, zai().ctx, &zai().syls, zai().gold, &len, FIX).unwrap();
    assert!(!o.final_ok && o.unfixable);
    assert_eq!((o.picks, o.actual), (1, o.ideal + 4), "one pick (Home, Right, Space, digit), then it stops");
}

/// Rule (section 1.1): an output path that is the rows file is refused before anything is written (exit 2, file as it was), also
/// when spelled with `..` or through a symlink (paths are canonicalized; a hard link is not caught, contract section 10); the
/// outputs are created only once the rows have been read.
#[test]
fn an_output_that_is_the_rows_file_is_refused() {
    let dir = std::env::temp_dir().join(format!("shanjie-replay-refuse-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let rows = dir.join("rows.txt");
    let text = "|我再重打|ㄨㄛˇ ㄗㄞˋ ㄔㄨㄥˊ ㄉㄚˇ\n";
    std::fs::write(&rows, text).unwrap();
    let alias = format!("{}/./rows.txt", dir.display());
    for opt in ["--rowstats", "--keys", "--firsts"] {
        let out = Command::new(env!("CARGO_BIN_EXE_shanjie-replay")).args(["--rows", rows.to_str().unwrap(), opt, &alias]).output().unwrap();
        assert_eq!(out.status.code(), Some(2), "{opt}");
        assert_eq!(std::fs::read_to_string(&rows).unwrap(), text, "{opt}: the rows file is untouched");
    }
    let new = dir.join("never.txt");
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-replay")).args(["--rows", dir.join("missing.txt").to_str().unwrap(), "--keys", new.to_str().unwrap()]).output().unwrap();
    assert_eq!(out.status.code(), Some(1));
    assert!(!new.exists(), "no output file before the rows are read");
    std::fs::remove_dir_all(&dir).unwrap();
}

fn run(bin: &str, args: &[&str]) -> String {
    let out = Command::new(bin).args(args).output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8(out.stdout).unwrap()
}
fn sha_of(summary: &str) -> &str {
    summary.split("'top1_sha256': '").nth(1).and_then(|s| s.split('\'').next()).expect("a top1_sha256")
}

/// Rule (section 5/6): `--rowstats` and `--keys` hold digits, tabs and newlines only; the rowstats file is byte for byte the CLI's
/// for the same rows file (same writer; here the first displays are also the CLI's choices, so every column is equal) and the
/// summary's `top1_sha256` is the CLI's. `--keys` has the six columns for every row, with every option on.
#[test]
fn outputs_are_numbers_only_and_the_rowstats_are_the_clis() {
    let dir = std::env::temp_dir().join(format!("shanjie-replay-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let p = |n: &str| dir.join(n).to_str().unwrap().to_string();
    let rows = std::fs::read_to_string(root().join("eval/dev/user-reported.txt")).unwrap().lines().take(20).collect::<Vec<_>>().join("\n") + "\n";
    std::fs::write(p("rows.txt"), rows).unwrap();
    let _g = ONE_ENGINE_AT_A_TIME.lock().unwrap_or_else(|e| e.into_inner());
    let replay = env!("CARGO_BIN_EXE_shanjie-replay");
    let eval = env!("CARGO_BIN_EXE_shanjie-eval");
    let r = root();
    let lm = r.join("data/lm/bigram.sjlm");
    let tl = r.join("data/lm/tinylm.sjtl");
    let cli = run(eval, &["--lm", lm.to_str().unwrap(), "--profile", "chat", "--context", "--packs", "acg", "--tinylm", tl.to_str().unwrap(), "--rows", &p("rows.txt"), "--rowstats", &p("cli.rs")]);
    let mine = run(replay, &["--rows", &p("rows.txt"), "--rowstats", &p("rep.rs"), "--keys", &p("rep.keys")]);
    assert_eq!(std::fs::read(p("cli.rs")).unwrap(), std::fs::read(p("rep.rs")).unwrap());
    assert_eq!(sha_of(&cli), sha_of(&mine));
    let all = run(replay, &["--rows", &p("rows.txt"), "--correct", "--learn", "on", "--rerank", "final", "--profile", "formal", "--rowstats", &p("all.rs"), "--keys", &p("all.keys")]);
    assert!(all.contains("+learn") && all.contains("+rerank:final") && all.contains("+correct") && all.contains("replay-formal"));
    for f in ["rep.rs", "rep.keys", "all.rs", "all.keys"] {
        let b = std::fs::read(p(f)).unwrap();
        assert!(!b.is_empty() && b.iter().all(|c| c.is_ascii_digit() || *c == b'\t' || *c == b'\n'), "{f}");
        let text = String::from_utf8(b).unwrap();
        assert_eq!(text.lines().count(), 20, "{f}");
        let cols = if f.ends_with("keys") { 6 } else { 4 };
        assert!(text.lines().all(|l| l.split('\t').count() == cols), "{f}");
    }
    std::fs::remove_dir_all(&dir).unwrap();
}

/// `--learn-file` tests: a scratch dir per test, and TMPDIR pointed into it so the tool's temporary directory is countable.
fn scratch(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("shanjie-replay-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(dir.join("tmp")).unwrap();
    dir
}
fn replay_with_learn(dir: &std::path::Path, learn: &std::path::Path, extra: &[&str]) -> std::process::Output {
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|再見|ㄗㄞˋ ㄐㄧㄢˋ\n").unwrap();
    let mut args = vec!["--rows", rows.to_str().unwrap(), "--learn-file", learn.to_str().unwrap()];
    args.extend_from_slice(extra);
    Command::new(env!("CARGO_BIN_EXE_shanjie-replay")).env("TMPDIR", dir.join("tmp")).args(args).output().unwrap()
}
fn text(o: &std::process::Output) -> String {
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    String::from_utf8(o.stdout.clone()).unwrap()
}

/// Rules: the loaded file changes the choice (without it 再見 is shown for ㄗㄞˋ ㄐㄧㄢˋ, measured; the file holds one record,
/// context `^>$` (sentence start, nothing after) for the word 在建 with weight 50 and today's day, so 在建 is shown); the
/// source file is only read (bytes and mtime as before, also with `--learn on`); the temporary directory is gone afterwards;
/// the tag says `+learnfile`.
#[test]
fn learn_file_changes_the_choice_and_the_source_is_untouched() {
    let dir = scratch("learnfile");
    let src = dir.join("mine.tsv");
    let today = core::learn::local_day();
    std::fs::write(&src, format!("#shanjie-learning v1\n^>$\tㄗㄞˋ-ㄐㄧㄢˋ\t在建\t50\t{today}\n")).unwrap();
    let (bytes, mtime) = (std::fs::read(&src).unwrap(), std::fs::metadata(&src).unwrap().modified().unwrap());
    let _g = ONE_ENGINE_AT_A_TIME.lock().unwrap_or_else(|e| e.into_inner());
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|再見|ㄗㄞˋ ㄐㄧㄢˋ\n").unwrap();
    let firsts = dir.join("firsts.txt");
    let plain = text(&Command::new(env!("CARGO_BIN_EXE_shanjie-replay")).args(["--rows", rows.to_str().unwrap(), "--firsts", firsts.to_str().unwrap()]).output().unwrap());
    assert_eq!(std::fs::read_to_string(&firsts).unwrap(), "再見\n");
    for extra in [&[][..], &["--learn", "on"][..]] {
        let with = text(&replay_with_learn(&dir, &src, &["--firsts", firsts.to_str().unwrap()].iter().chain(extra).copied().collect::<Vec<_>>()));
        assert!(with.contains("+learnfile") && !plain.contains("+learnfile"));
        assert_eq!(std::fs::read_to_string(&firsts).unwrap(), "在建\n", "{extra:?}: the learned word changes the first display");
        assert_eq!(std::fs::read(&src).unwrap(), bytes, "{extra:?}: source bytes");
        assert_eq!(std::fs::metadata(&src).unwrap().modified().unwrap(), mtime, "{extra:?}: source mtime");
        assert_eq!(std::fs::read_dir(dir.join("tmp")).unwrap().count(), 0, "{extra:?}: temporary directory removed");
    }
    std::fs::remove_dir_all(&dir).unwrap();
}

/// Rule: a file that is not a valid learning file (bad header) is an error, not a replay without the learnings; the source stays
/// as it was (core's quarantine renames the copy only) and no temporary directory is left.
#[test]
fn a_corrupt_learn_file_is_an_error() {
    let dir = scratch("learncorrupt");
    let src = dir.join("bad.tsv");
    std::fs::write(&src, "not a learning file\n").unwrap();
    let _g = ONE_ENGINE_AT_A_TIME.lock().unwrap_or_else(|e| e.into_inner());
    let out = replay_with_learn(&dir, &src, &[]);
    assert_eq!(out.status.code(), Some(1));
    assert_eq!(std::fs::read(&src).unwrap(), b"not a learning file\n");
    assert!(!dir.join("bad.tsv.corrupt").exists());
    assert_eq!(std::fs::read_dir(dir.join("tmp")).unwrap().count(), 0, "temporary directory removed on the error path");
    std::fs::remove_dir_all(&dir).unwrap();
}

/// Rule: an output that is the learning file is refused (exit 2) before anything is created, like the rows file.
#[test]
fn an_output_that_is_the_learn_file_is_refused() {
    let dir = scratch("learnrefuse");
    let src = dir.join("mine.tsv");
    std::fs::write(&src, "#shanjie-learning v1\n").unwrap();
    for opt in ["--rowstats", "--keys", "--firsts"] {
        let out = replay_with_learn(&dir, &src, &[opt, src.to_str().unwrap()]);
        assert_eq!(out.status.code(), Some(2), "{opt}");
        assert_eq!(std::fs::read(&src).unwrap(), b"#shanjie-learning v1\n", "{opt}: untouched");
    }
    std::fs::remove_dir_all(&dir).unwrap();
}
