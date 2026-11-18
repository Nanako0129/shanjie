//! `english-sim` (docs/spec/english-mixed.md §13.3; the typing model of docs/spec/english-mixed.md §13.3): a fixed-seed
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
    // Without mistakes nothing reverts (threshold 1 of docs/spec/english-mixed.md §13.3): five keys a row (w u l, Space, Enter) times 400 rows times 5 seeds.
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
/// to the word. With both switches off only (c) is left: four words revert at Space (their columns make no syllable) and each is the whole
/// word, because the token is the key record, the replaced keys included (docs/spec/english-mixed.md §2; it was 1, `cup`, while the token
/// was the keys left in the columns); the other two end as Chinese.
#[test]
fn recall_counts_the_classes_and_the_probes() {
    let sha = "d4907dc4b4fffa8c0f450e3321b0837d90447af45747ef0cb49fee12fd9cbd80";
    let on = run(&["--words", LIST, "--recall"]);
    let lines: Vec<&str> = on.lines().collect();
    // All six count: `google` is in the table of official writings and shows as `Google`, which is its writing (docs/spec/english-mixed.md §4.4).
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
    assert_eq!(off.lines().next().unwrap(), format!("{sha}\t6\t4\t0\t0\t4\t2"));
    assert!(off.contains("probe\tcup\ttoken\n") && off.contains("probe\tgo\ttoken\n") && off.contains("probe\tok\t中文\n"), "{off}");
}

/// `--reversal-guard on` (docs/spec/english-mixed.md §1.1, §3.2): trigger (b) never fires, so the recall's (b) count is 0 (it is 1,
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

/// `--digits` (docs/spec/english-mixed.md §13.3): the engine with the digit table and the model; six more columns (three pairs), mean and max per 1000
/// syllables over the seeds: the digit characters left in what the rows commit, the keys after which a digit showed, and the extra Backspaces (docs/spec/english-mixed.md §13.3). Three synthetic
/// rows (ㄉㄢˋ ㄅㄚ ㄅㄞˇ, all keys in the digit alphabet or next to it), seed 20261014, two seeds. Typed right: no digit is left and none
/// shows. Mistakes repaired by Backspace leave none; by overwriting (the key replaces nothing in the buffer) digits are left and shown. The
/// real sets are run by main (the spec's numbers); these only pin the columns and the determinism.
#[test]
fn digits_columns() {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-{}-digits", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|蛋|ㄉㄢˋ\n|八|ㄅㄚ\n|百|ㄅㄞˇ\n").unwrap();
    let rows = rows.to_str().unwrap();
    let go = |p: &str, repair: &str| run(&["--rows", rows, "--digits", "--repeat", "2", "--p", p, "--repair", repair, "--auto-english", "off"]);
    let sha = "1265d1e6ad6805a31af90872f00b75447742d3a261cdfab90a0073fd6d30d348";
    let zeros = "0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000";
    assert_eq!(go("0", "overwrite"), format!("{sha}\t3\t3\t24\t0\t{zeros}\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0.000\t0\n"));
    assert_eq!(go("0.3", "backspace"), format!("{sha}\t3\t3\t34\t5\t{zeros}\t0.000\t0.000\t333.333\t666.667\t0.000\t0.000\t0.000\t0.000\t0\n"));
    let over = go("0.3", "overwrite");
    assert_eq!(over, format!("{sha}\t3\t3\t29\t5\t{zeros}\t2666.667\t4000.000\t2333.333\t3666.667\t0.000\t0.000\t0.000\t0.000\t0\n"));
    assert_eq!(go("0.3", "overwrite"), over, "the output depends on nothing but the arguments");
    // `--lm` goes with `--digits`; recall with `--digits` types the words on this engine and prints a fifth class (the comparison's) after `none`.
    assert!(fails(&["--words", LIST, "--recall", "--lm", "x"]));
    let plain = run(&["--words", LIST, "--recall", "--overwrite", "off", "--reversal-guard", "on"]);
    let full = run(&["--words", LIST, "--recall", "--overwrite", "off", "--reversal-guard", "on", "--digits", "--auto-english", "off"]);
    let (head, head5) = (plain.lines().next().unwrap(), full.lines().next().unwrap());
    assert_eq!((head.split('\t').count(), head5.split('\t').count()), (7, 8));
    assert!(head5.ends_with("\t0"), "no comparison with the switch off: {head5}");
    std::fs::remove_dir_all(&dir).unwrap();
}

/// docs/spec/english-mixed.md §13.3: with `--digits` the Backspace repair reads the engine's preedit. In the row ㄅㄨˋ a slip after the ㄅ (key `1`) to a key that
/// cannot follow it (ㄜ, ㄘ, ㄗ) makes the engine turn the `1` into a digit token; the repair presses one more Backspace for it and no digit
/// is left in the commit (final digits 0). The model-only repair (docs/spec/english-mixed.md §13.3) left it: the same run then gave a positive final column.
#[test]
fn digits_backspace_repair_reads_the_screen() {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-{}-digits-bs", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|不|ㄅㄨˋ\n".repeat(100)).unwrap();
    let out = run(&["--rows", rows.to_str().unwrap(), "--digits", "--repeat", "2", "--p", "0.5", "--repair", "backspace"]);
    let c: Vec<&str> = out.trim_end().split('\t').collect();
    assert_eq!(c.len(), 24, "{c:?}");
    assert_eq!(c[15..17], ["0.000", "0.000"], "no digit left in what the rows commit: {c:?}");
    let (shown, extra): (f64, f64) = (c[17].parse().unwrap(), c[19].parse().unwrap());
    assert!(shown > 0.0 && extra > 0.0, "premise: slips made digit tokens that cost an extra Backspace: {c:?}");
    assert_eq!(c[19..21], ["535.000", "570.000"], "{c:?}");
    std::fs::remove_dir_all(&dir).unwrap();
}

/// docs/spec/english-mixed.md §13.3: a row whose retyping keeps reverting is stopped, not the run. The real lexicon has no syllable that reverts when typed clean,
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

/// `--keys` (the reversal-digits probe, docs/spec/english-mixed.md §13.3): the engine as the App builds it, three fixed lines, ten columns each (the tenth, the decision points of docs/spec/english-mixed.md §16, is empty for the first two; `wlu6` closes at a decision point: one key out of column order).
/// The lines: `.u3` (a hurried ㄧㄡˇ: the digit layer first reads `.` as a digit, one token, and the syllable takes it back at the tone key: `有`; the engine
/// before the column-order rule gave `.以`), `8su06` after the prefix `ji3` (one unit before, then the digit and ㄋㄧㄢˊ), `wlu6` (a hurried ㄊㄧㄠˊ, which
/// only the reversal guard keeps from becoming English).
#[test]
fn keys_probe_columns() {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-keys-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let input = dir.join("keys.tsv");
    std::fs::write(&input, "a1\t\t\t.u3\na2\t\tji3\t8su06\na3\t\t\twlu6\n").unwrap();
    let (input, out) = (input.to_str().unwrap(), dir.join("out.tsv"));
    let o = out.to_str().unwrap();
    let sha = run(&["--keys", input, "--out", o]);
    assert_eq!(sha, "a649b13673ecaf5af43fcbff500b2072c85b4e31cbffe6c12a381eabf0909db7\t3\n");
    let want = "a1\t0\tㄧㄡˇ\t\t0\t\t0\t1\t有\t\na2\t1\t_punct_8 ㄋㄧㄢˊ\t\t0\t\t0\t1\t我8年\t\na3\t0\tㄊㄧㄠˊ\t\t0\t\t0\t0\t條\t3:0,0,1\n";
    assert_eq!(std::fs::read_to_string(&out).unwrap(), want);
    // An existing output is never overwritten.
    assert!(fails(&["--keys", input, "--out", o]));
    assert_eq!(std::fs::read_to_string(&out).unwrap(), want);
    // The switches default to the App's: the same output with them spelled out, a different one with the guard off (`wlu6` reverts to English).
    let (spelled, off) = (dir.join("spelled.tsv"), dir.join("off.tsv"));
    run(&["--keys", input, "--out", spelled.to_str().unwrap(), "--overwrite", "off", "--key-order", "on", "--reversal-guard", "on"]);
    assert_eq!(std::fs::read_to_string(&spelled).unwrap(), want);
    // `--rho` sets the two penalties: the constants (7, 4) give the bytes no option gives; with 0 the rejoin is free, and `8g4` (8是 with the constants) is read as ㄕㄚˋ
    // (煞) with the 8 taken back; a bad value is refused.
    let (rho74, rho0, one) = (dir.join("rho74.tsv"), dir.join("rho0.tsv"), dir.join("one.tsv"));
    run(&["--keys", input, "--out", rho74.to_str().unwrap(), "--rho", "7,4"]);
    assert_eq!(std::fs::read_to_string(&rho74).unwrap(), want, "--rho 7,4 is the constants");
    std::fs::write(&one, "r1\t\t\t8g4\n").unwrap();
    run(&["--keys", one.to_str().unwrap(), "--out", rho0.to_str().unwrap(), "--rho", "0,0"]);
    assert_eq!(std::fs::read_to_string(&rho0).unwrap(), "r1\t0\tㄕㄚˋ\t\t0\t\t0\t1\t煞\t\n", "--rho 0,0");
    for bad in ["8", "8,", "x,4", "-1,4", "8,inf", "8,4,2"] {
        assert!(fails(&["--keys", input, "--out", dir.join("bad.tsv").to_str().unwrap(), "--rho", bad]), "--rho {bad}");
    }
    assert!(fails(&["--rows", input, "--rho", "7,4"]));
    run(&["--keys", input, "--out", off.to_str().unwrap(), "--reversal-guard", "off"]);
    assert!(std::fs::read_to_string(&off).unwrap().ends_with("a3\t0\t\t\t4\t2:b\t0\t0\twlu6\t\n"));
    std::fs::remove_dir_all(&dir).unwrap();
}

/// `--swap` and `--tone-early` (docs/spec/english-mixed.md §13.3): hurried syllables through the App's engine (`--digits`), one more column at the end, the hurried
/// syllables after which the composition's last unit is not the syllable, per 1000 syllables of the rows (mean over the seeds). Four rows: 有 ㄧㄡˇ, 爬 ㄆㄚˊ,
/// 頭 ㄊㄡˊ, 他 ㄊㄚ (tone 1: no tone key to press early). Every syllable swapped (`--swap 1`): all four come out as typed in order with the guard on, none with it off.
/// Every tone key early (`--tone-early 1`, three syllables): 爬 and 頭 come back, 有 does not (ㄧˇ is a syllable of its own, so no English revert for the next key
/// to undo): 250 per 1000. The columns before are the ones of `--digits`.
#[test]
fn hurried_syllables_column() {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-{}-hurried", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|有|ㄧㄡˇ\n|爬|ㄆㄚˊ\n|頭|ㄊㄡˊ\n|他|ㄊㄚ\n").unwrap();
    let rows = rows.to_str().unwrap();
    let sha = "9301720785562ee7cef6718d4e426bc93e6e11f4f74478b997593c3341ece987";
    let go = |extra: &[&str]| {
        let mut a = vec!["--rows", rows, "--digits", "--overwrite", "off", "--reversal-guard", "on", "--p", "0", "--repeat", "3", "--auto-english", "off"];
        a.extend(extra);
        run(&a)
    };
    let z = "0.000\t0.000";
    // rows syllables keys typos | revert a, b, c, split, english_final | digits final, shown, extra | hurried_bad | auto_english (a mean and a max), auto_points
    assert_eq!(go(&["--swap", "1"]), format!("{sha}\t4\t4\t48\t0\t{z}\t{z}\t{z}\t{z}\t{z}\t{z}\t2000.000\t2000.000\t{z}\t0.000\t{z}\t0\n"));
    assert_eq!(go(&["--tone-early", "1"]), format!("{sha}\t4\t4\t48\t0\t{z}\t{z}\t500.000\t500.000\t{z}\t{z}\t{z}\t{z}\t{z}\t250.000\t{z}\t0\n"));
    assert_eq!(
        go(&["--swap", "1", "--reversal-guard", "off"]),
        format!("{sha}\t4\t4\t48\t0\t{z}\t{z}\t500.000\t500.000\t{z}\t{z}\t{z}\t2000.000\t2000.000\t{z}\t1000.000\t{z}\t0\n")
    );
    assert_eq!(go(&["--swap", "0.5", "--tone-early", "0.5"]), format!("{sha}\t4\t4\t48\t0\t{z}\t{z}\t166.667\t250.000\t{z}\t{z}\t{z}\t1333.333\t1500.000\t{z}\t0.000\t{z}\t0\n"));
    // Without both options the line has no extra column; the options are for the typing mode and between 0 and 1.
    assert_eq!(go(&[]).matches('\t').count(), 4 + 10 + 6 + 3);
    assert!(fails(&["--words", LIST, "--recall", "--swap", "0.5"]));
    assert!(fails(&["--rows", rows, "--swap", "1.5"]));
    std::fs::remove_dir_all(std::path::Path::new(rows).parent().unwrap()).unwrap();
}

// ---- docs/spec/english-mixed.md §16: English or Zhuyin at the closing key ----

const AUTO_FIXTURE: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../core/tests/fixtures/english-tiny.tsv");

/// The comparison in the typing mode: 400 rows of ㄍㄜ (keys `e k`), p 0.02, mistakes mended by overwriting. A mended slip leaves the keys `wek`, `rek`
/// or `elk` over the syllable: a decision point. The table here has rows for those three strings, and theta -100 lets English win every comparison, so
/// `auto_english` is positive; with the core switch off, or without a table, it is 0 and the decision points are counted all the same. The simulator
/// takes each English back with one Esc: no row is left with English in it. The constants spelled out give the bytes no option gives.
#[test]
fn auto_english_columns() {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-{}-auto", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let (rows, table) = (dir.join("rows.txt"), dir.join("english.tsv"));
    std::fs::write(&rows, "|哥|ㄍㄜ\n".repeat(400)).unwrap();
    std::fs::write(&table, "T\t100000\nN\t3000\nA\t0\nE\twek\t1000\nE\trek\t1000\nE\telk\t1000\n").unwrap();
    let (rows, table) = (rows.to_str().unwrap(), table.to_str().unwrap());
    let go = |extra: &[&str]| {
        let mut a = vec!["--rows", rows, "--digits", "--p", "0.02", "--repair", "overwrite", "--overwrite", "off", "--key-order", "on", "--reversal-guard", "on", "--repeat", "2"];
        a.extend(extra);
        let out = run(&a);
        let c: Vec<String> = out.trim_end().split('\t').map(String::from).collect();
        assert_eq!(c.len(), 24, "{c:?}");
        (out, c)
    };
    let (_, forced) = go(&["--auto-table", table, "--auto-theta", "-100"]);
    let (mean, max, points): (f64, f64, u64) = (forced[21].parse().unwrap(), forced[22].parse().unwrap(), forced[23].parse().unwrap());
    assert!(mean > 0.0 && max >= mean && points > 0, "{forced:?}");
    assert_eq!(forced[13..15], ["0.000", "0.000"], "every English was taken back: {forced:?}");
    let (_, off) = go(&["--auto-table", table, "--auto-theta", "-100", "--auto-english", "off"]);
    assert_eq!((&off[21..23], &off[23]), (&["0.000".to_string(), "0.000".to_string()][..], &forced[23]), "{off:?}");
    let (_, none) = go(&["--auto-theta", "-100", "--auto-english", "off"]);
    assert_eq!(none[5..], off[5..], "the switch off is the engine without a table");
    // The constants (core/src/english_auto.rs: theta 1, kappa 2, kappa_dup 3) as options change no byte.
    let (plain, _) = go(&["--auto-table", table]);
    let (spelled, _) = go(&["--auto-table", table, "--auto-english", "on", "--auto-theta", "1", "--auto-kappa", "2", "--auto-kappa-dup", "3"]);
    assert_eq!(plain, spelled);
    // The options need the engine with a model, a finite number, on or off, and a table that parses.
    assert!(fails(&["--rows", rows, "--auto-theta", "1"]));
    for bad in [&["--auto-theta", "x"][..], &["--auto-kappa", "inf"], &["--auto-kappa-dup", "nan"], &["--auto-english", "maybe"], &["--auto-table", rows]] {
        let mut a = vec!["--rows", rows, "--digits"];
        a.extend(bad);
        assert!(fails(&a), "{bad:?}");
    }
    std::fs::remove_dir_all(&dir).unwrap();
}

/// `--keys` with the comparison (the fixture table, the constants): `speech_` is the English token, key 6 (the Space) marked `e`, and the tenth column
/// names the decision point with its counts; with the core switch off it is the Chinese syllable and still a decision point (how the probe builder
/// finds its rows). After 我們用 the same.
#[test]
fn keys_probe_auto_english() {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-keys-auto-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let input = dir.join("keys.tsv");
    std::fs::write(&input, "p1\t\t\tspeech_\np2\t我們用\t\tspeech_\np3\t\t\tai_\n").unwrap();
    let input = input.to_str().unwrap();
    let (on, off) = (dir.join("on.tsv"), dir.join("off.tsv"));
    run(&["--keys", input, "--out", on.to_str().unwrap(), "--auto-table", AUTO_FIXTURE]);
    assert_eq!(
        std::fs::read_to_string(&on).unwrap(),
        "p1\t0\t_punct_speech\t\t0\t6:e\t0\t0\tspeech\t6:4,1,0\np2\t0\t_punct_speech\t\t0\t6:e\t0\t0\tspeech\t6:4,1,0\np3\t0\tㄇㄛ\t\t0\t\t0\t0\t摸\t\n"
    );
    run(&["--keys", input, "--out", off.to_str().unwrap(), "--auto-table", AUTO_FIXTURE, "--auto-english", "off"]);
    let off = std::fs::read_to_string(&off).unwrap();
    let rows: Vec<Vec<&str>> = off.lines().map(|l| l.split('\t').collect()).collect();
    assert!(rows.len() == 3 && rows.iter().all(|r| r.len() == 10));
    assert_eq!((rows[0][2], rows[0][5], rows[0][9]), ("ㄘㄣ", "", "6:4,1,0"));
    assert_eq!((rows[1][2], rows[1][5], rows[1][9]), ("ㄘㄣ", "", "6:4,1,0"));
    std::fs::remove_dir_all(&dir).unwrap();
}

/// `shanjie-eval` itself takes the four options and checks them; decoding rows has no closing key, so the output does not change.
#[test]
fn the_main_mode_takes_the_options_and_changes_nothing() {
    let dir = std::env::temp_dir().join(format!("shanjie-english-sim-{}-main", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let rows = dir.join("rows.txt");
    std::fs::write(&rows, "|今天天氣很好|ㄐㄧㄣ ㄊㄧㄢ ㄊㄧㄢ ㄑㄧˋ ㄏㄣˇ ㄏㄠˇ\n").unwrap();
    let eval = |extra: &[&str]| {
        let mut a = vec!["--lm", "data/lm/bigram.sjlm", "--rows", rows.to_str().unwrap(), "--profile", "chat"];
        a.extend(extra);
        Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(&a).current_dir(concat!(env!("CARGO_MANIFEST_DIR"), "/..")).output().unwrap()
    };
    let base = eval(&[]);
    assert!(base.status.success() && !base.stdout.is_empty());
    let with = eval(&["--auto-english", "off", "--auto-theta", "1", "--auto-kappa", "1.5", "--auto-kappa-dup", "0"]);
    assert!(with.status.success() && with.stdout == base.stdout);
    for bad in [&["--auto-english", "yes"][..], &["--auto-theta", "x"], &["--auto-kappa"], &["--auto-kappa-dup", "inf"]] {
        assert!(!eval(bad).status.success(), "{bad:?}");
    }
    std::fs::remove_dir_all(&dir).unwrap();
}
