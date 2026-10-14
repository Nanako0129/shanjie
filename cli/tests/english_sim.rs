//! `english-sim` (docs/contracts/zhen-mixed.md section 4.1): a fixed-seed run with the numbers written down. Change the typing
//! model in cli/src/english.rs and these numbers change with it. Synthetic rows and words only.
use std::process::Command;

fn run(args: &[&str]) -> String {
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).arg("english-sim").args(args).output().unwrap();
    assert!(out.status.success(), "english-sim failed");
    String::from_utf8(out.stdout).unwrap()
}

/// 400 identical rows of one tone-1 syllable, ㄊㄧㄠ (keys w u l, all in the standard layout's letter keys), and the two words
/// the mistakes `e`/`q` for w overwritten, `k` for l overwritten can type: ewukl, qwukl. Each needs two overwrites, which the
/// leftover (a valid syllable) requires, so only a repair by overwriting reaches them.
fn files(name: &str) -> (String, String) {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-{}-{name}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let rows = dir.join("rows.txt");
    let words = dir.join("words.txt");
    std::fs::write(&rows, "|挑|ㄊㄧㄠ\n".repeat(400)).unwrap();
    std::fs::write(&words, "ewukl\nqwukl\n").unwrap();
    (rows.to_str().unwrap().into(), words.to_str().unwrap().into())
}

const ROWS_SHA: &str = "93556f9e41a4054454d89f223362857134a0250856928da60071d24eea7cef21";

#[test]
fn fixed_seed_numbers() {
    let (rows, words) = files("fixed");
    // rows syllables keys triggers per_1000_mean per_1000_max; five seeds from 20261014. per_1000 = triggers x 1000 / syllables
    // of a seed: 214 triggers over five seeds of 400 syllables is a mean of 214 x 1000 / (400 x 5) = 107 (the max seed is 117.5).
    let a = run(&["--words", &words, "--rows", &rows, "--p", "0.5", "--repair", "overwrite"]);
    assert_eq!(a, format!("{ROWS_SHA}\t400\t400\t14898\t214\t107.000\t117.500\n"));
    // The same again (the output depends on nothing but the arguments).
    assert_eq!(run(&["--words", &words, "--rows", &rows, "--p", "0.5", "--repair", "overwrite"]), a);
    // Backspace repair never reaches the two words (the wrong l is deleted, not overwritten).
    assert_eq!(run(&["--words", &words, "--rows", &rows, "--p", "0.5", "--repair", "backspace"]), format!("{ROWS_SHA}\t400\t400\t16506\t0\t0.000\t0.000\n"));
    // Without mistakes nothing triggers: five keys a row (w u l, Space, Enter) times 400 rows times 5 seeds.
    assert_eq!(run(&["--words", &words, "--rows", &rows, "--p", "0"]), format!("{ROWS_SHA}\t400\t400\t10000\t0\t0.000\t0.000\n"));
    // One seed, one repeat: the first seed is the one the mean above starts from.
    let one = run(&["--words", &words, "--rows", &rows, "--p", "0.5", "--seed", "1", "--repeat", "1"]);
    assert_eq!(one, format!("{ROWS_SHA}\t400\t400\t2964\t45\t112.500\t112.500\n"));
    std::fs::remove_dir_all(std::path::Path::new(&rows).parent().unwrap()).unwrap();
}

#[test]
fn recall_counts_the_classes_and_the_opening_words() {
    // eval/golden/english-tiny.txt: cup (no overwrite), law (one, valid leftover), google (four, valid leftover), dip, hello and
    // rust (a leftover that is not a syllable): four open, all with the word first.
    let list = concat!(env!("CARGO_MANIFEST_DIR"), "/../eval/golden/english-tiny.txt");
    let sha = "d4907dc4b4fffa8c0f450e3321b0837d90447af45747ef0cb49fee12fd9cbd80";
    assert_eq!(run(&["--words", list, "--recall"]), format!("{sha}\t6\t4\t4\t1\t1\t1\t3\n"));
}
