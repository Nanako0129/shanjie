//! `english-sim` (docs/contracts/zhen-mixed.md section 4.1, `--inline`: zhen-inline.md section 3, with the auto-keep columns of
//! revision 2): a fixed-seed run with the numbers written down. Change the typing
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
    // Longer words with the same starts and no word of their own: rule D holds on the way to them, the window never opens.
    std::fs::write(dir.join("longer.txt"), "ewuklzz\nqwuklzz\n").unwrap();
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
    // `--inline` (docs/contracts/zhen-inline.md section 3) adds the two columns of the display rule D, asked of the engine after every
    // key, and the two of the auto-keeps (revision 2, all 0 here: the words are only reached at their last key), and leaves the rest
    // of the line as it is. Here D holds exactly in the syllables that trigger (the two words are the only
    // ones, so no longer start exists): the same 214 over five seeds.
    let inline = run(&["--words", &words, "--rows", &rows, "--p", "0.5", "--repair", "overwrite", "--inline"]);
    assert_eq!(inline, format!("{ROWS_SHA}\t400\t400\t14898\t214\t107.000\t117.500\t107.000\t117.500\t0.000\t0.000\n"));
    // Words that are only longer than what is typed: D holds (the Zhuyin would show as letters), nothing triggers.
    let longer = std::path::Path::new(&rows).parent().unwrap().join("longer.txt");
    let longer = longer.to_str().unwrap();
    assert_eq!(
        run(&["--words", longer, "--rows", &rows, "--p", "0.5", "--repair", "overwrite", "--inline"]),
        format!("{ROWS_SHA}\t400\t400\t14470\t0\t0.000\t0.000\t107.000\t117.500\t0.000\t0.000\n")
    );
    // The backspace repair and no mistakes: zero, in both columns.
    assert_eq!(run(&["--words", longer, "--rows", &rows, "--p", "0.5", "--repair", "backspace", "--inline"]), format!("{ROWS_SHA}\t400\t400\t16506\t0\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\n"));
    assert_eq!(run(&["--words", longer, "--rows", &rows, "--p", "0", "--inline"]), format!("{ROWS_SHA}\t400\t400\t10000\t0\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\n"));
    std::fs::remove_dir_all(std::path::Path::new(&rows).parent().unwrap()).unwrap();
}

#[test]
fn inline_is_for_the_typing_mode() {
    let list = concat!(env!("CARGO_MANIFEST_DIR"), "/../eval/golden/english-tiny.txt");
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).arg("english-sim").args(["--words", list, "--recall", "--inline"]).output().unwrap();
    assert!(!out.status.success() && out.stdout.is_empty());
}

#[test]
fn recall_counts_the_classes_and_the_opening_words() {
    // eval/golden/english-tiny.txt: cup (no overwrite), law (one, valid leftover), google (four, valid leftover), dip, hello and
    // rust (a leftover that is not a syllable): four open, all with the word first.
    let list = concat!(env!("CARGO_MANIFEST_DIR"), "/../eval/golden/english-tiny.txt");
    let sha = "d4907dc4b4fffa8c0f450e3321b0837d90447af45747ef0cb49fee12fd9cbd80";
    assert_eq!(run(&["--words", list, "--recall"]), format!("{sha}\t6\t4\t4\t1\t1\t1\t3\n"));
}

/// Revision 2 (zhen-inline section 3): the word `ewuk` is reached by the mistakes `e` for w and `k` for l (both overwritten), and the
/// key that repairs the `k` (`l`) goes nowhere from a whole word: the engine keeps `ewuk` as an English token and begins a
/// syllable. The simulator counts it, presses Esc and Backspace, types ㄊㄧㄠ again without a mistake and goes on.
#[test]
fn autokeep_columns() {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-{}-keep", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let (rows, words) = (dir.join("rows.txt"), dir.join("words.txt"));
    std::fs::write(&rows, "|挑|ㄊㄧㄠ\n".repeat(400)).unwrap();
    std::fs::write(&words, "ewuk\n").unwrap();
    let (rows, words) = (rows.to_str().unwrap(), words.to_str().unwrap());
    let cols = |args: &[&str]| -> Vec<String> {
        let mut a = vec!["--words", words, "--rows", rows];
        a.extend_from_slice(args);
        run(&a).trim_end().split('\t').map(String::from).collect()
    };
    // With mistakes, overwritten: auto-keeps are counted. The 11 columns are the 7 of before, the 2 of rule D and the 2 new ones. Here
    // every syllable that shows letters (the only word is `ewuk`) is one that is kept, so the D and auto-keep columns agree; the rate is
    // about the chance of `e` for w (1/4) and `k` for l (1/4), 1/16 of the 400 syllables a seed, 55.5 per 1000 over five seeds.
    let c = cols(&["--p", "0.5", "--repair", "overwrite", "--inline"]);
    assert_eq!(c.len(), 11, "{c:?}");
    let (mean, max): (f64, f64) = (c[9].parse().unwrap(), c[10].parse().unwrap());
    assert!(mean > 0.0 && max >= mean, "{c:?}");
    assert_eq!(c[7..], ["55.500", "70.000", "55.500", "70.000"], "{c:?}");
    // Without mistakes nothing is kept; the Backspace repair never reaches a whole word followed by a key (it rebuilds the letters).
    assert_eq!(cols(&["--p", "0", "--inline"])[9..], ["0.000", "0.000"]);
    assert_eq!(cols(&["--p", "0.5", "--repair", "backspace", "--inline"])[9..], ["0.000", "0.000"]);
    // Without `--inline` the line has no new column (and the recovery still runs: the keys are counted).
    assert_eq!(cols(&["--p", "0.5", "--repair", "overwrite"]).len(), 7);
    std::fs::remove_dir_all(&dir).unwrap();
}

/// `--digits` (docs/contracts/digits.md A.4.2): the engine with the digit table and the model; six more columns (three pairs), mean and max per 1000
/// syllables over the seeds: the digit characters left in what the rows commit, the keys after which a digit showed, and the extra Backspaces (A.10). Three synthetic
/// rows (ㄉㄢˋ ㄅㄚ ㄅㄞˇ, all keys in the digit alphabet or next to it), seed 20261014, two seeds. Typed right: no digit is left and none
/// shows. Mistakes repaired by Backspace leave none; by overwriting (the key replaces nothing in the buffer) digits are left and shown. The
/// real sets are run by main (the contract's numbers); these only pin the columns and the determinism.
#[test]
fn digits_columns() {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-{}-digits", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|蛋|ㄉㄢˋ\n|八|ㄅㄚ\n|百|ㄅㄞˇ\n").unwrap();
    let list = concat!(env!("CARGO_MANIFEST_DIR"), "/../eval/golden/english-tiny.txt");
    let rows = rows.to_str().unwrap();
    let go = |p: &str, repair: &str| run(&["--words", list, "--rows", rows, "--digits", "--repeat", "2", "--p", p, "--repair", repair]);
    let sha = "1265d1e6ad6805a31af90872f00b75447742d3a261cdfab90a0073fd6d30d348";
    assert_eq!(go("0", "overwrite"), format!("{sha}\t3\t3\t24\t0\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\n"));
    assert_eq!(go("0.3", "backspace"), format!("{sha}\t3\t3\t34\t0\t0.000\t0.000\t0.000\t0.000\t333.333\t666.667\t0.000\t0.000\n"));
    let over = go("0.3", "overwrite");
    assert_eq!(over, format!("{sha}\t3\t3\t29\t0\t0.000\t0.000\t2666.667\t4000.000\t2333.333\t3666.667\t0.000\t0.000\n"));
    assert_eq!(go("0.3", "overwrite"), over, "the output depends on nothing but the arguments");
    // `--digits` is for the typing mode, and `--lm` goes with it.
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).arg("english-sim").args(["--words", list, "--recall", "--digits"]).output().unwrap();
    assert!(!out.status.success() && out.stdout.is_empty());
    std::fs::remove_dir_all(&dir).unwrap();
}

/// digits.md A.10: with `--digits` the Backspace repair reads the engine's preedit. In the row ㄅㄨˋ a slip after the ㄅ (key `1`) to a key that
/// cannot follow it (ㄜ, ㄘ, ㄗ) makes the engine turn the `1` into a digit token; the repair presses one more Backspace for it and no digit
/// is left in the commit (final digits 0). The model-only repair of section 4.2 left it: the same run then gave a positive final column.
#[test]
fn digits_backspace_repair_reads_the_screen() {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-{}-digits-bs", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|不|ㄅㄨˋ\n".repeat(100)).unwrap();
    let list = concat!(env!("CARGO_MANIFEST_DIR"), "/../eval/golden/english-tiny.txt");
    let out = run(&["--words", list, "--rows", rows.to_str().unwrap(), "--digits", "--repeat", "2", "--p", "0.5", "--repair", "backspace"]);
    let c: Vec<&str> = out.trim_end().split('\t').collect();
    assert_eq!(c.len(), 13, "{c:?}");
    assert_eq!(c[7..9], ["0.000", "0.000"], "no digit left in what the rows commit: {c:?}");
    let (shown, extra): (f64, f64) = (c[9].parse().unwrap(), c[11].parse().unwrap());
    assert!(shown > 0.0 && extra > 0.0, "premise: slips made digit tokens that cost an extra Backspace: {c:?}");
    assert_eq!(c[11..], ["535.000", "570.000"], "{c:?}");
    std::fs::remove_dir_all(&dir).unwrap();
}

/// Zhen-keyorder section 4, typing mode: the line of before, then one `two` line for every word of `TWO_LETTER` (the core's list, in its
/// order) with the mean and max per 1000 syllables; without `--key-order` the output is the one line of before. Typed right (`--p 0`)
/// the engine with the switch on opens nothing. These rows are one three-symbol syllable, so the shadow holds three keys at Space whatever the
/// repair (it only loses a key with a symbol), and no figure leaves 0; the counting itself is pinned in the module test of
/// cli/src/english.rs (`two_letter_windows_are_counted_by_word`).
#[test]
fn key_order_typing_prints_every_two_letter_word() {
    let (rows, words) = files("keyorder");
    for args in [&["--p", "0"][..], &["--p", "0.5", "--repair", "overwrite"], &["--p", "0.5", "--repair", "backspace"]] {
        let mut a = vec!["--words", words.as_str(), "--rows", rows.as_str()];
        a.extend_from_slice(args);
        let plain = run(&a);
        assert_eq!(plain.lines().count(), 1, "no flag, one line: {plain}");
        a.push("--key-order");
        let out = run(&a);
        let lines: Vec<&str> = out.lines().collect();
        assert_eq!(lines.len(), 1 + core::engine::TWO_LETTER.len(), "{out}");
        for (line, word) in lines[1..].iter().zip(core::engine::TWO_LETTER) {
            let f: Vec<&str> = line.split('\t').collect();
            assert_eq!((f.len(), f[0], f[1]), (4, "two", *word), "{line}");
            let (mean, max): (f64, f64) = (f[2].parse().unwrap(), f[3].parse().unwrap());
            assert!(0.0 <= mean && mean <= max, "{line}");
            assert_eq!((mean, max), (0.0, 0.0), "{line}");
        }
        if args.len() == 2 {
            assert_eq!(lines[0], plain.trim_end(), "clean typing: the first line is the old one");
        }
    }
    std::fs::remove_dir_all(std::path::Path::new(&rows).parent().unwrap()).unwrap();
}

/// Zhen-keyorder section 4, recall mode with the switch on: only `opened` and `top1`, for the list and for `TWO_LETTER`, and the six
/// probes; the tool does not stop for a disagreement with the recount of the switch-off rules (it makes none). On
/// eval/golden/english-tiny.txt all six words open, with the word first (law and hello have evidence by key order; cup opens
/// with none, on Space, because ㄏㄧㄣ is not a syllable). Of the 24 two-letter words five are typed in column order (rm cp ui go ci), so
/// 19 open. `npm` is not in that list: 0.
#[test]
fn key_order_recall_reports_what_the_engine_does() {
    let list = concat!(env!("CARGO_MANIFEST_DIR"), "/../eval/golden/english-tiny.txt");
    let want = "recall\twords\t6\t6\t6\nrecall\ttwo\t24\t19\t19\nprobe\tok\t1\nprobe\tls\t1\nprobe\tcd\t1\nprobe\tmv\t1\nprobe\tcup\t1\nprobe\tnpm\t0\n";
    assert_eq!(run(&["--words", list, "--recall", "--key-order"]), want);
    // Without the flag: the line of before (a different engine, a different report).
    assert_eq!(run(&["--words", list, "--recall"]).lines().count(), 1);
}
