//! `english-sim` (docs/contracts/zhen-revert.md section 4.1; the typing model of docs/contracts/zhen-mixed.md section 4.2): a fixed-seed
//! run with the numbers written down. Change the typing model in cli/src/english.rs and these numbers change with it. Synthetic rows and
//! words only.
use std::process::Command;

fn run(args: &[&str]) -> String {
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).arg("english-sim").args(args).output().unwrap();
    assert!(out.status.success(), "english-sim failed");
    String::from_utf8(out.stdout).unwrap()
}

fn fails(args: &[&str]) -> bool {
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).arg("english-sim").args(args).output().unwrap();
    !out.status.success() && out.stdout.is_empty()
}

const LIST: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../eval/golden/english-tiny.txt");
const ROWS_SHA: &str = "93556f9e41a4054454d89f223362857134a0250856928da60071d24eea7cef21";

/// 400 identical rows of one tone-1 syllable, ㄊㄧㄠ (keys w u l, all in the standard layout's letter keys).
fn rows(name: &str) -> String {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-{}-{name}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|挑|ㄊㄧㄠ\n".repeat(400)).unwrap();
    rows.to_str().unwrap().into()
}

#[test]
fn fixed_seed_numbers() {
    let rows = rows("fixed");
    // rows syllables keys typos, then (mean, max) per 1000 syllables of a seed for revert a, b, c, split and english_final; five seeds from
    // 20261014. With the overwrite repair every wrong key in the occupied column of the right one is overwritten, which is a revert (a) the
    // simulator undoes: 3,008 mistyped keys over five seeds of 400 syllables, and 1,263 reverts per 1000 syllables (the max seed 1,290).
    let a = run(&["--rows", &rows, "--p", "0.5", "--repair", "overwrite"]);
    assert_eq!(a, format!("{ROWS_SHA}\t400\t400\t23122\t3008\t1263.000\t1290.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\n"));
    // The same again (the output depends on nothing but the arguments).
    assert_eq!(run(&["--rows", &rows, "--p", "0.5", "--repair", "overwrite"]), a);
    // Backspace repair: the wrong key is taken out before the right one goes in, so only a wrong key that lands in an occupied column reverts.
    assert_eq!(
        run(&["--rows", &rows, "--p", "0.5", "--repair", "backspace"]),
        format!("{ROWS_SHA}\t400\t400\t16996\t3008\t245.000\t275.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\n")
    );
    // Without mistakes nothing reverts (threshold 1 of section 4.2): five keys a row (w u l, Space, Enter) times 400 rows times 5 seeds.
    assert_eq!(
        run(&["--rows", &rows, "--p", "0"]),
        format!("{ROWS_SHA}\t400\t400\t10000\t0\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\n")
    );
    // One seed, one repeat: the first seed is the one the mean above starts from.
    let one = run(&["--rows", &rows, "--p", "0.5", "--seed", "1", "--repeat", "1"]);
    assert_eq!(one, format!("{ROWS_SHA}\t400\t400\t4602\t601\t1250.000\t1250.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\n"));
    // The switches: with the overwrite switch off the right key replaces the wrong one and nothing reverts; with the key-order switch off
    // alone the figures are those of both on (these rows have no key out of column order that the repair does not take out first).
    assert_eq!(
        run(&["--rows", &rows, "--p", "0.5", "--repair", "overwrite", "--overwrite", "off"]),
        format!("{ROWS_SHA}\t400\t400\t14470\t3008\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\n")
    );
    assert_eq!(run(&["--rows", &rows, "--p", "0.5", "--repair", "overwrite", "--key-order", "off"]), a);
    assert_eq!(
        run(&["--rows", &rows, "--p", "0.5", "--repair", "backspace", "--overwrite", "off", "--key-order", "off"]),
        format!("{ROWS_SHA}\t400\t400\t16506\t3008\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\n")
    );
    std::fs::remove_dir_all(std::path::Path::new(&rows).parent().unwrap()).unwrap();
}

#[test]
fn the_switches_take_on_or_off() {
    let rows = rows("switches");
    assert!(fails(&["--rows", &rows, "--overwrite", "yes"]));
    assert!(fails(&["--rows", &rows, "--key-order", "1"]));
    assert!(fails(&["--rows", &rows, "--inline"]), "the display option is gone");
    assert!(fails(&["--words", LIST, "--rows", &rows]), "--words is the recall mode's list; the engine reads no list");
    std::fs::remove_dir_all(std::path::Path::new(&rows).parent().unwrap()).unwrap();
}

/// Recall mode: every word of eval/golden/english-tiny.txt typed letter by letter on an engine of its own, then Space. The first revert of
/// the six words is (a) four times (google, dip, hello, rust: a key over a symbol in its column), (b) once (law: `a`, the initial, after the
/// final `l`) and (c) once (cup: typed in order, and the syllable ㄏㄧㄣ is missing at Space); every one ends as one English token equal
/// to the word. With both switches off only (c) is left, and `cup` is the one word that ends as a token.
#[test]
fn recall_counts_the_classes_and_the_probes() {
    let sha = "d4907dc4b4fffa8c0f450e3321b0837d90447af45747ef0cb49fee12fd9cbd80";
    let on = run(&["--words", LIST, "--recall"]);
    let lines: Vec<&str> = on.lines().collect();
    assert_eq!(lines[0], format!("{sha}\t6\t6\t4\t1\t1\t0"));
    // The probes, a fixed public list in a fixed order: the words that revert, the short ones that never do (ai, ui, mp3), and the missing
    // syllables (go, cp, cup).
    let want = [
        ("ok", "token"), ("ls", "token"), ("cd", "token"), ("mv", "token"), ("pr", "token"), ("ai", "中文"), ("ui", "中文"), ("go", "token"),
        ("cp", "token"), ("cup", "token"), ("npm", "token"), ("gta6", "token"), ("rdr2", "token"), ("poe2", "token"), ("mp3", "中文"),
        ("a4", "token"), ("i7", "token"), ("s7", "token"),
    ];
    assert_eq!(lines.len(), 1 + want.len(), "{on}");
    for (line, (w, what)) in lines[1..].iter().zip(want) {
        assert_eq!(*line, format!("probe\t{w}\t{what}"));
    }
    let off = run(&["--words", LIST, "--recall", "--overwrite", "off", "--key-order", "off"]);
    assert_eq!(off.lines().next().unwrap(), format!("{sha}\t6\t1\t0\t0\t4\t2"));
    assert!(off.contains("probe\tcup\ttoken\n") && off.contains("probe\tgo\ttoken\n") && off.contains("probe\tok\t中文\n"), "{off}");
}

/// `--reversal-guard on` (docs/contracts/zhen-reapply.md section 1.2): trigger (b) never fires, so the recall's (b) count is 0 (it is 1,
/// `law`, with the guard off above) and `ls`, which only a reversal types, is Chinese. The switch is off by default in this tool.
#[test]
fn reversal_guard_stops_trigger_b() {
    let on = run(&["--words", LIST, "--recall", "--reversal-guard", "on"]);
    let head: Vec<&str> = on.lines().next().unwrap().split('\t').collect();
    assert_eq!(head[4], "0", "no (b) revert: {on}");
    assert!(on.contains("probe\tls\t中文\n") && on.contains("probe\tok\ttoken\n"), "{on}");
    assert!(fails(&["--words", LIST, "--recall", "--reversal-guard", "1"]));
    // Words with a key in front of a filled column (`t i m e`: ㄔ ㄛ then ㄩ): with the guard on the keys are kept in the order typed, so each
    // ends as one English token equal to the word (`opened` counts them), never `tmie`.
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-{}-typed", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let list = dir.join("words.txt");
    std::fs::write(&list, "time\nuser\nlaw\nhome\nmake\n").unwrap();
    let typed = run(&["--words", list.to_str().unwrap(), "--recall", "--reversal-guard", "on"]);
    let head: Vec<&str> = typed.lines().next().unwrap().split('\t').collect();
    assert_eq!((head[1], head[2]), ("5", "5"), "{typed}");
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
    let rows = rows.to_str().unwrap();
    let go = |p: &str, repair: &str| run(&["--rows", rows, "--digits", "--repeat", "2", "--p", p, "--repair", repair]);
    let sha = "1265d1e6ad6805a31af90872f00b75447742d3a261cdfab90a0073fd6d30d348";
    let zeros = "0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000";
    assert_eq!(go("0", "overwrite"), format!("{sha}\t3\t3\t24\t0\t{zeros}\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\n"));
    assert_eq!(go("0.3", "backspace"), format!("{sha}\t3\t3\t34\t5\t{zeros}\t0.000\t0.000\t333.333\t666.667\t0.000\t0.000\n"));
    let over = go("0.3", "overwrite");
    assert_eq!(over, format!("{sha}\t3\t3\t29\t5\t{zeros}\t2666.667\t4000.000\t2333.333\t3666.667\t0.000\t0.000\n"));
    assert_eq!(go("0.3", "overwrite"), over, "the output depends on nothing but the arguments");
    // `--digits` is for the typing mode, and `--lm` goes with it.
    assert!(fails(&["--words", LIST, "--recall", "--digits"]));
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
    let out = run(&["--rows", rows.to_str().unwrap(), "--digits", "--repeat", "2", "--p", "0.5", "--repair", "backspace"]);
    let c: Vec<&str> = out.trim_end().split('\t').collect();
    assert_eq!(c.len(), 21, "{c:?}");
    assert_eq!(c[15..17], ["0.000", "0.000"], "no digit left in what the rows commit: {c:?}");
    let (shown, extra): (f64, f64) = (c[17].parse().unwrap(), c[19].parse().unwrap());
    assert!(shown > 0.0 && extra > 0.0, "premise: slips made digit tokens that cost an extra Backspace: {c:?}");
    assert_eq!(c[19..], ["535.000", "570.000"], "{c:?}");
    std::fs::remove_dir_all(&dir).unwrap();
}

/// Section 4.1: a row whose retyping keeps reverting is stopped, not the run. The real lexicon has no syllable that reverts when typed clean,
/// so the row is made to keep reverting with `--retries 0` (a revert is stopped at once): with p 0.5 and the overwrite repair about half
/// of 60 identical rows revert. The run exits 0, every row is still in the figures, and stderr names each stopped row (number, seed and
/// kind, no text) and gives the total; the stdout columns are the same 15 as ever.
#[test]
fn a_row_that_keeps_reverting_is_stopped_and_the_run_goes_on() {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-{}-stuck", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|挑|ㄊㄧㄠ\n".repeat(60)).unwrap();
    let rows = rows.to_str().unwrap();
    let go = |retries: &str| {
        let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval"))
            .arg("english-sim")
            .args(["--rows", rows, "--p", "0.5", "--repair", "overwrite", "--seed", "1", "--repeat", "1", "--retries", retries])
            .output()
            .unwrap();
        (out.status.success(), String::from_utf8(out.stdout).unwrap(), String::from_utf8(out.stderr).unwrap())
    };
    let (ok, stdout, stderr) = go("0");
    assert!(ok, "the run exits 0: {stderr}");
    let cols: Vec<&str> = stdout.trim_end().split('\t').collect();
    assert_eq!(cols.len(), 15, "{stdout}");
    assert_eq!(cols[1..3], ["60", "60"], "every row is in the figures");
    let lines: Vec<&str> = stderr.lines().filter(|l| l.starts_with("row ")).collect();
    let summary: Vec<&str> = stderr.lines().filter(|l| l.starts_with("stopped ")).collect();
    assert!(!lines.is_empty() && lines.len() < 60, "premise: some rows stop and some do not: {stderr}");
    assert!(lines.iter().all(|l| l.contains("of seed 1") && l.contains("(a)") && l.contains("was stopped") && !l.contains('挑')), "{stderr}");
    assert_eq!(summary, [format!("stopped {} rows over 1 seeds (their counts so far stay in the figures, their English left is not counted)", lines.len())]);
    // The rows after a stopped one are typed: some row numbered after the first stopped row did not stop.
    let stopped: Vec<usize> = lines.iter().map(|l| l.split_whitespace().nth(1).unwrap().parse().unwrap()).collect();
    assert!((stopped[0] + 1..=60).any(|n| !stopped.contains(&n)), "{stopped:?}");
    // Each stopped row reverted once (a): the figure is exactly the number of stopped rows per 1000 syllables.
    let a: f64 = cols[5].parse().unwrap();
    assert!((a - lines.len() as f64 * 1000.0 / 60.0).abs() < 0.01, "{a} vs {}", lines.len());
    // The same run with the default retries stops nothing and writes nothing about rows.
    let (ok, stdout, stderr) = go("3");
    assert!(ok && !stderr.contains("stopped") && stdout.split('\t').count() == 15, "{stderr}");
    std::fs::remove_dir_all(&dir).unwrap();
}
