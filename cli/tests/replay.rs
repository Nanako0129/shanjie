//! docs/contracts/engine-replay.md section 6, item 2: the replay's rules, one test each, on the real engine, n-gram and small model.
//! Rows are lines of eval/dev/user-reported.txt (public, CC0), whose first display is known to be wrong for most of them; every
//! test states the display it relies on, so a model change fails there first. Each engine is about 700 MB, so the tests take turns.
use cli::common::{load_lenient, root};
use cli::replay::{build_engine, keys_of, move_cost, replay_row, Cfg, Rerank, RowOut};
use core::engine::Engine;
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
/// Enter, and what is sent is the gold text. 我再重打: Left, Left, Space, digit = 4; the 這家 row: Home, Right, Right, Space, digit = 5.
#[test]
fn a_repick_costs_the_moves_the_window_and_the_digit() {
    let (_g, mut e) = engine(false);
    let len = load_lenient().unwrap();
    for (r, extra) in [(zai(), 4), (jia(), 5)] {
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
    e.key(cli::replay::enter()).unwrap();
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
    assert!(each.typing_applies >= 1, "the row has rewrites while it is typed");
    assert_eq!((each.typing_applies, each.rewrites, &each.first), (mid, changed, &shown));
    let (mid, changed, shown) = by_hand(&mut e, &r, false);
    assert_eq!((fin.typing_applies, mid), (0, 0));
    assert!(fin.rewrites <= 1);
    assert_eq!((fin.rewrites, &fin.first), (changed, &shown));
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
