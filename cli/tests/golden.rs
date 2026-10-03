use std::process::Command;

fn run(args: &[&str]) -> String {
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(args).output().unwrap();
    assert!(out.status.success());
    String::from_utf8(out.stdout).unwrap()
}

#[test]
fn matches_golden_byte_for_byte() {
    let golden = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../eval/golden/unigram.txt")).unwrap();
    assert_eq!(run(&["--set", "trap", "daily", "moedict", "--learn-sim", "--no-overlay"]), golden);
}

#[test]
fn check_readings_flag_adds_nothing_without_readings_columns() {
    // No shipped set has a third column, so the flag must add no lines yet.
    let a = run(&["--set", "trap", "daily", "moedict"]);
    assert_eq!(run(&["--set", "trap", "daily", "moedict", "--check-readings"]), a);
}
