//! Digits.md A.4.1 #40 and #50: with the feature off, or without its table, the engine is the engine of the slice before, field for field.
//! `digits-off.txt` (core/tests/fixtures) is the output of the base commit of the digits PR for the scripts of rows #1-#39
//! (`common/digits_scripts.rs`); this file only uses what that commit has, so the same file generates it there.
//!
//! `digits-off-en.txt` (the switch on, no table) was that commit's output too until zhen-revert (docs/contracts/zhen-revert.md section 2.3):
//! without the digit layer the digit keys are Zhuyin keys like any other, and with English mixing on a key over the symbol in its column
//! (`1 2`: ㄅ then ㄉ) or in front of a filled column reverts to the raw keys, so these scripts now stay as the keys typed (an English run
//! in the composition) where they used to make Zhuyin. It was regenerated with the command below by zhen-revert PR 1; 26 of the 39 rows
//! differ, all of them rows of digit keys typed without the table. With the table (digits.md A.4.1 #1-#39 on the real layer) nothing
//! changes: `engine_digits.rs`.
//!
//! Regenerated again by zhen-reapply (docs/contracts/zhen-reapply.md section 1.2): the shipping defaults now have the reversal guard on, so a
//! key in front of a filled column is no longer English; rows r06 (`ˇ.1` now `ˇㄅㄡ`) and r33 (`2 0 u` now `ㄉㄧㄢ` with its candidates) changed, no other.
//!
//!   DIGITS_FIXTURE_DIR=<dir> cargo test --release --locked -p core --test engine_digits_off -- --test-threads=1
//!
//! writes `digits-off.txt` and `digits-off-en.txt` into <dir> (the tests then pass trivially); without the variable they compare.
use core::engine::*;
use std::path::{Path, PathBuf};

#[path = "common/digits_scripts.rs"]
mod scripts;

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

/// Recipe A: the real data directory (in the digits tree it holds digits.tsv, which `Engine::new` reads), the model,
/// `set_english(false)`.
fn engine_off() -> Engine {
    let mut e = Engine::new(&root().join("data/lexicon"), Layout::Standard).unwrap();
    e.load_lm(&root().join("data/lm/bigram.sjlm")).unwrap();
    e.set_english(false).unwrap();
    e
}

/// Recipe B: a data directory without digits.tsv (links to the other files), the model, `set_english(true)`.
fn engine_no_table(tag: &str) -> Engine {
    let dir = std::env::temp_dir().join(format!("shanjie-digits-no-table-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    for f in std::fs::read_dir(root().join("data/lexicon")).unwrap().map(|f| f.unwrap().file_name()) {
        if f != "digits.tsv" {
            std::os::unix::fs::symlink(root().join("data/lexicon").join(&f), dir.join(&f)).unwrap();
        }
    }
    let mut e = Engine::new(&dir, Layout::Standard).expect("an engine builds without digits.tsv");
    e.load_lm(&root().join("data/lm/bigram.sjlm")).unwrap(); // reads the overlay and demote files again
    std::fs::remove_dir_all(&dir).unwrap();
    e.set_english(true).unwrap();
    e
}

/// Every row of #1-#39 through a fresh engine's state (`reset(Discard)` between rows): one line per key.
fn outputs(e: &mut Engine) -> String {
    let mut out = String::new();
    for (name, script) in scripts::rows() {
        e.reset(ResetMode::Discard);
        e.set_left_context("");
        for tok in script.split_whitespace() {
            let o = scripts::send(e, tok);
            out.push_str(&format!("{name}\t{tok}\t{}\n", scripts::line(&o)));
        }
    }
    out
}

fn check(file: &str, got: String) {
    if let Ok(dir) = std::env::var("DIGITS_FIXTURE_DIR") {
        std::fs::write(Path::new(&dir).join(file), got).unwrap();
        return;
    }
    let want = std::fs::read_to_string(root().join("core/tests/fixtures").join(file)).unwrap();
    let (g, w): (Vec<&str>, Vec<&str>) = (got.lines().collect(), want.lines().collect());
    assert_eq!(g.len(), w.len(), "number of outputs");
    for (a, b) in g.iter().zip(&w) {
        assert_eq!(a, b, "output differs from the base commit's");
    }
}

/// #40: the switch off (the core's default): rows #1-#39, every output as the engine before the slice gave it.
#[test]
fn row40_switch_off_equals_the_base_commit() {
    check("digits-off.txt", outputs(&mut engine_off()));
}

/// #50: the switch on but no table: the engine builds without an error and every output is the base commit's with the switch on.
#[test]
fn row50_no_table_equals_the_base_commit_with_english_on() {
    check("digits-off-en.txt", outputs(&mut engine_no_table("a")));
}
