//! docs/contracts/s4-learning.md §6.13 (R5): the learning file's storage, in temporary directories
//! only. Nothing here touches ~/Library/Application Support.

use core::learn::{Learner, Record};
use core::learn_store::{LearnStore, Opened, StoreError, CORRUPT, FILE, HEADER, MAX_BYTES, TMP};
use std::fs;
use std::os::unix::fs::{symlink, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::mpsc;
use std::time::Duration;

/// A fresh parent directory under the system temporary directory; the store directory is inside
/// it and does not exist yet.
fn temp() -> (PathBuf, PathBuf) {
    use std::sync::atomic::{AtomicUsize, Ordering};
    static N: AtomicUsize = AtomicUsize::new(0);
    let parent = std::env::temp_dir().join(format!(
        "shanjie-learn-store-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::SeqCst)
    ));
    let _ = fs::remove_dir_all(&parent);
    fs::create_dir_all(&parent).unwrap();
    let dir = parent.join("shanjie");
    (parent, dir)
}

fn mode(p: &Path) -> u32 {
    fs::symlink_metadata(p).unwrap().permissions().mode() & 0o777
}

fn rec(context: &str, reading: &str, word: &str, weight: f64, day: i64) -> Record {
    Record {
        context: context.into(),
        reading: reading.split('-').map(String::from).collect(),
        word: word.into(),
        weight,
        day,
    }
}

fn sample() -> Vec<Record> {
    vec![
        rec("管把", "ㄅㄚˇ", "把", 1.0, 20_000),
        rec("^", "ㄐㄧㄣ-ㄊㄧㄢ", "今天", 0.75, 20_001),
        rec("", "ㄓㄢˋ-ㄐㄧ", "戰績", 2.5, 20_002),
        rec("他", "_punct_，", "，", 0.125, 0),
    ]
}

/// `open` on a path the test controls, with a time limit: a FIFO must never block it.
fn open_within(dir: &Path, secs: u64) -> Result<(LearnStore, Vec<Record>, Opened), StoreError> {
    let (tx, rx) = mpsc::channel();
    let d = dir.to_path_buf();
    std::thread::spawn(move || {
        let _ = tx.send(LearnStore::open(&d));
    });
    rx.recv_timeout(Duration::from_secs(secs)).expect("LearnStore::open blocked")
}

#[test]
fn fresh_directory_is_0700_and_starts_empty() {
    let (_p, dir) = temp();
    let (store, records, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!(opened, Opened::Fresh);
    assert!(records.is_empty());
    assert_eq!(store.dir(), dir.as_path());
    assert_eq!(mode(&dir), 0o700);
}

#[test]
fn existing_directory_of_ours_is_chmodded_to_0700() {
    let (_p, dir) = temp();
    fs::create_dir(&dir).unwrap();
    fs::set_permissions(&dir, fs::Permissions::from_mode(0o755)).unwrap();
    LearnStore::open(&dir).unwrap();
    assert_eq!(mode(&dir), 0o700);
}

#[test]
fn directory_owned_by_another_user_is_refused() {
    // /usr is root-owned on macOS; the check must refuse it before any write or chmod.
    assert_eq!(LearnStore::open(Path::new("/usr")).err(), Some(StoreError::Ownership));
}

#[test]
fn directory_that_is_a_symlink_or_a_file_is_refused() {
    let (p, dir) = temp();
    let real = p.join("real");
    fs::create_dir(&real).unwrap();
    symlink(&real, &dir).unwrap();
    assert_eq!(LearnStore::open(&dir).err(), Some(StoreError::Io));
    let file = p.join("plain");
    fs::write(&file, b"x").unwrap();
    assert_eq!(LearnStore::open(&file).err(), Some(StoreError::Io));
}

#[test]
fn save_then_open_round_trips_with_0600_and_no_temporary_left() {
    let (_p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    store.save(&sample()).unwrap();
    assert_eq!(mode(&dir.join(FILE)), 0o600);
    assert!(!dir.join(TMP).exists());
    let text = fs::read_to_string(dir.join(FILE)).unwrap();
    assert!(text.starts_with(&format!("{HEADER}\n")));
    assert!(text.lines().skip(1).all(|l| l.split('\t').count() == 5), "five fields per line");
    let (_, records, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!(opened, Opened::Loaded { skipped: 0 });
    assert_eq!(records, sample());
}

#[test]
fn records_that_would_not_read_back_are_never_written() {
    let (_p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    let bad = vec![
        rec("中", "ㄅㄚˇ", "把\n#shanjie-learning v1", 1.0, 1),
        rec("中", "ㄅㄚˇ", "a\tb", 1.0, 1),
        rec("abc", "ㄅㄚˇ", "把", 1.0, 1),
        rec("中文字", "ㄅㄚˇ", "把", 1.0, 1),
        rec("中", "", "把", 1.0, 1),
        rec("中", "ㄅㄚˇ", "", 1.0, 1),
        rec("中", "ㄅㄚˇ", "把", f64::NAN, 1),
        rec("中", "ㄅㄚˇ", "把", -1.0, 1),
        rec("中", "ㄅㄚˇ", "把", 1.0, -1),
    ];
    let mut all = bad.clone();
    all.push(rec("中", "ㄅㄚˇ", "把", 1.0, 1));
    store.save(&all).unwrap();
    let text = fs::read_to_string(dir.join(FILE)).unwrap();
    assert_eq!(text, format!("{HEADER}\n中\tㄅㄚˇ\t把\t1\t1\n"));
}

#[test]
fn bad_lines_are_skipped_one_by_one() {
    let (_p, dir) = temp();
    fs::create_dir(&dir).unwrap();
    let lines = [
        HEADER,
        "中\tㄅㄚˇ\t把\t1\t20000",     // good
        "中\tㄅㄚˇ\t把\tNaN\t20000",   // NaN
        "中\tㄅㄚˇ\t把\tinf\t20000",   // inf
        "中\tㄅㄚˇ\t把\t-1\t20000",    // negative
        "中文字\tㄅㄚˇ\t把\t1\t20000", // context over 2 characters
        "a\tㄅㄚˇ\t把\t1\t20000",      // context not Han
        "中\tㄅㄚˇ\t把\t1\tx",         // bad day
        "中\tㄅㄚˇ\t把\t1",            // 4 fields
        "中\tㄅㄚˇ\t把\t1\t1\tx",      // 6 fields
        "^\tㄅㄚˇ\t把\t0.5\t20001",    // good, sentence start
        "\tㄅㄚˇ\t把\t0.5\t20001",     // good, global
        "",                           // blank: not counted
    ];
    let mut bytes = lines.join("\n").into_bytes();
    bytes.extend_from_slice(b"\n\xff\xfe\tbad utf-8\n");
    fs::write(dir.join(FILE), bytes).unwrap();
    let (_, records, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!(opened, Opened::Loaded { skipped: 9 });
    assert_eq!(records, vec![
        rec("中", "ㄅㄚˇ", "把", 1.0, 20_000),
        rec("^", "ㄅㄚˇ", "把", 0.5, 20_001),
        rec("", "ㄅㄚˇ", "把", 0.5, 20_001),
    ]);
}

#[test]
fn bad_header_is_renamed_to_corrupt_0600_one_copy() {
    let (_p, dir) = temp();
    fs::create_dir(&dir).unwrap();
    for (i, body) in ["#shanjie-learning v2\n", "not a header\n中\tㄅㄚˇ\t把\t1\t1\n", ""].iter().enumerate() {
        fs::write(dir.join(FILE), body).unwrap();
        fs::set_permissions(dir.join(FILE), fs::Permissions::from_mode(0o644)).unwrap();
        let (_, records, opened) = LearnStore::open(&dir).unwrap();
        assert_eq!(opened, Opened::Corrupt, "case {i}");
        assert!(records.is_empty());
        assert!(!dir.join(FILE).exists());
        assert_eq!(fs::read_to_string(dir.join(CORRUPT)).unwrap(), *body, "the latest copy replaces the older one");
        assert_eq!(mode(&dir.join(CORRUPT)), 0o600);
    }
    let names: Vec<_> = fs::read_dir(&dir).unwrap().map(|e| e.unwrap().file_name()).collect();
    assert_eq!(names, vec![std::ffi::OsString::from(CORRUPT)], "one copy only");
}

#[test]
fn oversized_file_is_corrupt_without_being_read() {
    let (_p, dir) = temp();
    fs::create_dir(&dir).unwrap();
    let f = fs::File::create(dir.join(FILE)).unwrap();
    std::io::Write::write_all(&mut &f, format!("{HEADER}\n").as_bytes()).unwrap();
    f.set_len(MAX_BYTES + 1).unwrap(); // sparse
    drop(f);
    let (_, records, opened) = open_within(&dir, 10).unwrap();
    assert_eq!(opened, Opened::Corrupt);
    assert!(records.is_empty());
    assert_eq!(mode(&dir.join(CORRUPT)), 0o600);
    assert!(!dir.join(FILE).exists());
}

#[test]
fn file_exactly_at_the_cap_still_loads() {
    let (_p, dir) = temp();
    fs::create_dir(&dir).unwrap();
    let mut body = format!("{HEADER}\n中\tㄅㄚˇ\t把\t1\t1\n").into_bytes();
    body.resize(MAX_BYTES as usize, b'\n');
    fs::write(dir.join(FILE), body).unwrap();
    let (_, records, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!(opened, Opened::Loaded { skipped: 0 });
    assert_eq!(records.len(), 1);
}

#[test]
fn symlink_in_place_of_the_file_is_not_followed() {
    let (p, dir) = temp();
    fs::create_dir(&dir).unwrap();
    let target = p.join("elsewhere.tsv");
    let content = format!("{HEADER}\n中\tㄅㄚˇ\t把\t1\t1\n");
    fs::write(&target, &content).unwrap();
    fs::set_permissions(&target, fs::Permissions::from_mode(0o644)).unwrap();
    symlink(&target, dir.join(FILE)).unwrap();
    let (store, records, opened) = open_within(&dir, 10).unwrap();
    assert_eq!(opened, Opened::Corrupt);
    assert!(records.is_empty(), "nothing read through the link");
    assert_eq!(mode(&target), 0o644, "no chmod through the link");
    assert!(!dir.join(CORRUPT).exists());
    store.save(&sample()).unwrap();
    assert!(fs::symlink_metadata(dir.join(FILE)).unwrap().file_type().is_file(), "the link itself is replaced");
    assert_eq!(fs::read_to_string(&target).unwrap(), content, "nothing written through the link");
}

#[test]
fn symlink_in_place_of_the_temporary_is_not_written_through() {
    let (p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    let target = p.join("victim");
    fs::write(&target, b"keep").unwrap();
    symlink(&target, dir.join(TMP)).unwrap();
    store.save(&sample()).unwrap();
    assert_eq!(fs::read(&target).unwrap(), b"keep");
    assert!(!dir.join(TMP).exists());
    assert_eq!(LearnStore::open(&dir).unwrap().1, sample());
}

#[test]
fn fifo_in_place_of_the_file_does_not_block() {
    let (_p, dir) = temp();
    fs::create_dir(&dir).unwrap();
    let st = Command::new("/usr/bin/mkfifo").arg(dir.join(FILE)).status().unwrap();
    assert!(st.success());
    let (store, records, opened) = open_within(&dir, 10).unwrap();
    assert_eq!(opened, Opened::Corrupt);
    assert!(records.is_empty());
    store.save(&sample()).unwrap();
    assert!(fs::symlink_metadata(dir.join(FILE)).unwrap().file_type().is_file());
}

#[test]
fn failed_write_leaves_the_old_file_whole() {
    let (_p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    store.save(&sample()).unwrap();
    let before = fs::read(dir.join(FILE)).unwrap();

    // The temporary cannot be created: a non-empty directory sits at its name.
    fs::create_dir(dir.join(TMP)).unwrap();
    fs::write(dir.join(TMP).join("x"), b"x").unwrap();
    assert_eq!(store.save(&sample()[..1]), Err(StoreError::Io));
    assert_eq!(fs::read(dir.join(FILE)).unwrap(), before);
    fs::remove_dir_all(dir.join(TMP)).unwrap();

    // The rename fails: a non-empty directory sits at the file's name. The temporary is removed.
    fs::remove_file(dir.join(FILE)).unwrap();
    fs::create_dir(dir.join(FILE)).unwrap();
    fs::write(dir.join(FILE).join("x"), b"x").unwrap();
    assert_eq!(store.save(&sample()), Err(StoreError::Io));
    assert!(!dir.join(TMP).exists(), "no half-written temporary left");
}

#[test]
fn clear_removes_the_files_and_keeps_the_directory() {
    let (_p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    store.save(&sample()).unwrap();
    fs::write(dir.join(TMP), b"t").unwrap();
    fs::write(dir.join(CORRUPT), b"c").unwrap();
    store.clear().unwrap();
    for name in [FILE, TMP, CORRUPT] {
        assert!(!dir.join(name).exists());
    }
    assert!(dir.is_dir());
    assert_eq!(mode(&dir), 0o700);
    store.clear().unwrap(); // nothing left: ENOENT is success
    let (_, records, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!((records.len(), opened), (0, Opened::Fresh));
}

#[test]
fn clear_reports_a_failure_but_still_removes_the_rest() {
    let (_p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    fs::create_dir(dir.join(FILE)).unwrap(); // cannot be removed as a file
    fs::write(dir.join(TMP), b"t").unwrap();
    fs::write(dir.join(CORRUPT), b"c").unwrap();
    assert_eq!(store.clear(), Err(StoreError::Io));
    assert!(!dir.join(TMP).exists());
    assert!(!dir.join(CORRUPT).exists());
}

/// §6.13 "100 days old records are pruned on load and save": the pruning is Learner::prune
/// (core/src/learn.rs, the executor's), so this runs once both halves are merged.
#[test]
fn records_100_days_old_are_pruned_across_save_and_load() {
    let (_p, dir) = temp();
    let today = 20_100;
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    store.save(&[rec("中", "ㄅㄚˇ", "把", 1.0, today - 100), rec("中", "ㄅㄚˇ", "吧", 1.0, today)]).unwrap();
    let (_, records, _) = LearnStore::open(&dir).unwrap();
    let mut learner = Learner::from_records(records);
    learner.prune(today);
    store.save(learner.records()).unwrap();
    let (_, records, _) = LearnStore::open(&dir).unwrap();
    assert_eq!(records, vec![rec("中", "ㄅㄚˇ", "吧", 1.0, today)]);
}

/// §6.13: the same record appended twice, then reopened: one record, the later weight and day.
#[test]
fn append_twice_then_open_keeps_the_last_line() {
    let (_p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    store.save(&sample()).unwrap();
    store.append(&[rec("管把", "ㄅㄚˇ", "把", 2.0, 20_010)]).unwrap();
    store.append(&[rec("管把", "ㄅㄚˇ", "把", 3.5, 20_020), rec("中", "ㄅㄚˇ", "吧", 1.0, 20_020)]).unwrap();
    assert_eq!(fs::read_to_string(dir.join(FILE)).unwrap().lines().count(), 1 + 4 + 3);
    assert_eq!(mode(&dir.join(FILE)), 0o600);
    let (_, records, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!(opened, Opened::Loaded { skipped: 0 });
    let mut want = sample();
    want[0] = rec("管把", "ㄅㄚˇ", "把", 3.5, 20_020);
    want.push(rec("中", "ㄅㄚˇ", "吧", 1.0, 20_020));
    assert_eq!(records, want);
}

/// §4: append never creates the file and never takes it past 16 MB.
#[test]
fn append_needs_an_existing_file_with_room() {
    let (_p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    assert_eq!(store.append(&sample()), Err(StoreError::Io));
    assert!(!dir.join(FILE).exists(), "no headerless file created");
    let f = fs::File::create(dir.join(FILE)).unwrap();
    std::io::Write::write_all(&mut &f, format!("{HEADER}\n").as_bytes()).unwrap();
    f.set_len(MAX_BYTES - 10).unwrap(); // sparse
    drop(f);
    fs::set_permissions(dir.join(FILE), fs::Permissions::from_mode(0o600)).unwrap();
    assert_eq!(store.append(&sample()[..1]), Err(StoreError::Io));
    assert_eq!(fs::metadata(dir.join(FILE)).unwrap().len(), MAX_BYTES - 10);
}
