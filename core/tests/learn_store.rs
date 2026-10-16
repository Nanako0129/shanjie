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
        rec("^>$", "ㄐㄧㄣ-ㄊㄧㄢ", "今天", 0.75, 20_001),
        rec("", "ㄓㄢˋ-ㄐㄧ", "戰績", 2.5, 20_002),
        rec("他們", "_punct_，", "，", 0.125, 0),
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
    assert!(records == sample(), "records differ (contents not printed: R2)");
}

#[test]
fn records_that_would_not_read_back_are_never_written() {
    let (_p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    let bad = vec![
        rec("中文", "ㄅㄚˇ", "把\n#shanjie-learning v1", 1.0, 1),
        rec("中文", "ㄅㄚˇ", "a\tb", 1.0, 1),
        rec("abc", "ㄅㄚˇ", "把", 1.0, 1),
        rec("中文字", "ㄅㄚˇ", "把", 1.0, 1),
        rec("中文", "", "把", 1.0, 1),
        rec("中文", "ㄅㄚˇ", "", 1.0, 1),
        rec("中文", "ㄅㄚˇ", "把", f64::NAN, 1),
        rec("中文", "ㄅㄚˇ", "把", -1.0, 1),
        rec("中文", "ㄅㄚˇ", "把", 1.0, -1),
    ];
    let mut all = bad.clone();
    all.push(rec("中文", "ㄅㄚˇ", "把", 1.0, 1));
    store.save(&all).unwrap();
    let text = fs::read_to_string(dir.join(FILE)).unwrap();
    assert_eq!(text, format!("{HEADER}\n中文\tㄅㄚˇ\t把\t1\t1\n"));
}

/// Contract section 12: a single-character record under ^ or the global key (from a file written before it) is skipped at
/// load, so it counts toward nothing, and no save or append writes one.
#[test]
fn single_character_global_records_are_not_loaded_or_written() {
    let (_p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    let old = rec("", "ㄗㄞˋ", "再", 1.0, 20_000);
    let kept = rec("", "ㄗㄞˋ-ㄐㄧㄢˋ", "再見", 1.0, 20_000);
    store.save(&[old.clone(), kept.clone()]).unwrap();
    assert_eq!(fs::read_to_string(dir.join(FILE)).unwrap(), format!("{HEADER}\n\tㄗㄞˋ-ㄐㄧㄢˋ\t再見\t1\t20000\n"));
    let body = format!("{HEADER}\n\tㄗㄞˋ-ㄐㄧㄢˋ\t再見\t1\t20000\n");
    store.append(&[old.clone()]).unwrap();
    assert_eq!(fs::read_to_string(dir.join(FILE)).unwrap(), body, "append writes nothing for it");
    // a file that has one: skipped on load (counted as a bad line), the rest loads
    fs::write(dir.join(FILE), format!("{HEADER}\n\tㄗㄞˋ\t再\t1\t20000\n^\tㄗㄞˋ\t再\t1\t20000\n{}", body.split_once('\n').unwrap().1)).unwrap();
    let (store, records, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!(opened, Opened::Loaded { skipped: 2 });
    assert!(records == vec![kept], "only the two-character record is loaded");
    // and a full rewrite of what was loaded no longer has the line
    store.save(&records).unwrap();
    assert_eq!(fs::read_to_string(dir.join(FILE)).unwrap(), body);
}

/// Contract section 14 (rules 7 and 8), the one migration point `valid`: the synthetic sentence-start keys
/// survive a save and a load as written; a single character under a synthetic key, under a one-Han key, and
/// every bare "^" record are not loaded (counted as bad lines), and a full rewrite leaves none of them in the file.
#[test]
fn synthetic_keys_round_trip_and_old_records_are_dropped() {
    let (_p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    let two = "ㄨㄛˇ-ㄕˋ";
    let kept = vec![
        rec("^>$", two, "我試", 1.0, 20_000),
        rec("^>ㄕˋ", two, "我試", 1.0, 20_000),
        rec("^>ㄉㄜ˙", two, "我的", 1.0, 20_000),
        rec("^>_punct_，", two, "我是", 1.0, 20_000),
        rec("^>ㄒㄧㄢ-ㄕㄥ", two, "我是", 1.0, 20_000), // a reading may hold "-": the key field is not split
        rec("他們", "ㄗㄞˋ", "再", 1.0, 20_000),         // a single character under exactly two Han characters
    ];
    let dropped = vec![
        rec("^>ㄕˋ", "ㄗㄞˋ", "再", 1.0, 20_000), // a single character under a synthetic key
        rec("他", "ㄗㄞˋ", "再", 1.0, 20_000),    // a single character under a one-Han key
        rec("^", two, "我試", 1.0, 20_000),       // every bare "^" record (written before rule 8)
        rec("^", "ㄗㄞˋ", "再", 1.0, 20_000),
        rec("^>", two, "我試", 1.0, 20_000),      // a synthetic key with no reading
        rec("^>ㄕ\tˋ", two, "我試", 1.0, 20_000),
    ];
    let all: Vec<Record> = dropped.iter().take(1).chain(&kept).chain(dropped.iter().skip(1)).cloned().collect();
    store.save(&all).unwrap();
    let want = format!("{HEADER}\n{}", kept.iter().map(|r| format!("{}\t{}\t{}\t1\t20000\n", r.context, r.reading.join("-"), r.word)).collect::<String>());
    assert_eq!(fs::read_to_string(dir.join(FILE)).unwrap(), want, "save writes no illegal key");
    // a file that has the old records: they are skipped at load, and a full rewrite of what loaded drops them
    let mut text = want.clone();
    for r in &dropped {
        let _ = std::fmt::Write::write_fmt(&mut text, format_args!("{}\t{}\t{}\t1\t20000\n", r.context, r.reading.join("-"), r.word));
    }
    fs::write(dir.join(FILE), text).unwrap();
    let (store, records, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!(opened, Opened::Loaded { skipped: dropped.len() });
    assert!(records == kept, "records differ (contents not printed: R2)");
    let l = Learner::from_records(records.clone());
    assert!(l.words_of(&["ㄗㄞˋ".to_string()]) == ["再"], "only the one under 他們 is visible");
    assert!(l.records().iter().all(|r| r.context != "^" && r.context != "他"));
    store.save(&records).unwrap();
    assert_eq!(fs::read_to_string(dir.join(FILE)).unwrap(), want, "the rewrite leaves none of them");
    let (_, again, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!(opened, Opened::Loaded { skipped: 0 });
    assert!(again == kept, "records differ (contents not printed: R2)");
}

#[test]
fn bad_lines_are_skipped_one_by_one() {
    let (_p, dir) = temp();
    fs::create_dir(&dir).unwrap();
    let lines = [
        HEADER,
        "中文\tㄅㄚˇ\t把\t1\t20000",     // good
        "中文\tㄅㄚˇ\t把\tNaN\t20000",   // NaN
        "中文\tㄅㄚˇ\t把\tinf\t20000",   // inf
        "中文\tㄅㄚˇ\t把\t-1\t20000",    // negative
        "中文字\tㄅㄚˇ\t把\t1\t20000", // context over 2 characters
        "a\tㄅㄚˇ\t把\t1\t20000",      // context not Han
        "中文\tㄅㄚˇ\t把\t1\tx",         // bad day
        "中文\tㄅㄚˇ\t把\t1",            // 4 fields
        "中文\tㄅㄚˇ\t把\t1\t1\tx",      // 6 fields
        "^>$\tㄅㄚˇ-ㄅㄚˇ\t把手\t0.5\t20001", // good, sentence start (synthetic key)
        "\tㄅㄚˇ-ㄅㄚˇ\t把手\t0.5\t20001", // good, global (two characters)
        "",                           // blank: not counted
    ];
    let mut bytes = lines.join("\n").into_bytes();
    bytes.extend_from_slice(b"\n\xff\xfe\tbad utf-8\n");
    fs::write(dir.join(FILE), bytes).unwrap();
    let (_, records, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!(opened, Opened::Loaded { skipped: 9 });
    assert!(records == vec![
        rec("中文", "ㄅㄚˇ", "把", 1.0, 20_000),
        rec("^>$", "ㄅㄚˇ-ㄅㄚˇ", "把手", 0.5, 20_001),
        rec("", "ㄅㄚˇ-ㄅㄚˇ", "把手", 0.5, 20_001),
    ], "records differ (contents not printed: R2)");
}

#[test]
fn bad_header_is_renamed_to_corrupt_0600_one_copy() {
    let (_p, dir) = temp();
    fs::create_dir(&dir).unwrap();
    for (i, body) in ["#shanjie-learning v2\n", "not a header\n中文\tㄅㄚˇ\t把\t1\t1\n", ""].iter().enumerate() {
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
    let mut body = format!("{HEADER}\n中文\tㄅㄚˇ\t把\t1\t1\n").into_bytes();
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
    let content = format!("{HEADER}\n中文\tㄅㄚˇ\t把\t1\t1\n");
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
    assert!(LearnStore::open(&dir).unwrap().1 == sample(), "records differ (contents not printed: R2)");
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
    store.save(&[rec("中文", "ㄅㄚˇ", "把", 1.0, today - 100), rec("中文", "ㄅㄚˇ", "吧", 1.0, today)]).unwrap();
    let (_, records, _) = LearnStore::open(&dir).unwrap();
    let mut learner = Learner::from_records(records);
    learner.prune(today);
    store.save(learner.records()).unwrap();
    let (_, records, _) = LearnStore::open(&dir).unwrap();
    assert!(records == vec![rec("中文", "ㄅㄚˇ", "吧", 1.0, today)], "records differ (contents not printed: R2)");
}

/// §6.13: the same record appended twice, then reopened: one record, the later weight and day.
#[test]
fn append_twice_then_open_keeps_the_last_line() {
    let (_p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    store.save(&sample()).unwrap();
    store.append(&[rec("管把", "ㄅㄚˇ", "把", 2.0, 20_010)]).unwrap();
    store.append(&[rec("管把", "ㄅㄚˇ", "把", 3.5, 20_020), rec("中文", "ㄅㄚˇ", "吧", 1.0, 20_020)]).unwrap();
    assert_eq!(fs::read_to_string(dir.join(FILE)).unwrap().lines().count(), 1 + 4 + 3);
    assert_eq!(mode(&dir.join(FILE)), 0o600);
    let (_, records, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!(opened, Opened::Loaded { skipped: 0 });
    let mut want = sample();
    want[0] = rec("管把", "ㄅㄚˇ", "把", 3.5, 20_020);
    want.push(rec("中文", "ㄅㄚˇ", "吧", 1.0, 20_020));
    assert!(records == want, "records differ (contents not printed: R2)");
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

/// New-word B §2.3: a "#" record round-trips when its word is 2-4 Han characters with one syllable each; a 1-character
/// or 5-character word, a non-Han character or a syllable count that differs from the word's length is never written
/// and is skipped at load. The single-character rules are unchanged.
#[test]
fn new_word_records_need_two_to_four_han_characters_one_syllable_each() {
    let (_p, dir) = temp();
    let (store, _, _) = LearnStore::open(&dir).unwrap();
    let good = vec![
        rec("#", "ㄅㄢˇ-ㄎㄨㄥˋ", "版控", 1.0, 20_000),
        rec("#", "ㄩㄝˋ-ㄈㄨˋ-ㄐㄧㄣ", "月付金", 0.5, 20_001),
        rec("#", "ㄕˋ-ㄐㄧㄥˋ-ㄅㄨˋ-ㄅㄨˋ", "拭鏡布布", 2.0, 20_002),
    ];
    let bad = vec![
        rec("#", "ㄅㄢˇ", "版", 1.0, 20_000),
        rec("#", "ㄅㄢˇ-ㄎㄨㄥˋ-ㄅㄢˇ-ㄎㄨㄥˋ-ㄅㄢˇ", "版控版控版", 1.0, 20_000),
        rec("#", "ㄅㄢˇ-ㄎㄨㄥˋ-ㄅㄢˇ", "版控", 1.0, 20_000),
        rec("#", "ㄅㄢˇ", "版控", 1.0, 20_000),
        rec("#", "ㄅㄢˇ-ㄟ", "版a", 1.0, 20_000),
        rec("#", "ㄅㄢˇ-ㄎㄨㄥˋ", "版，", 1.0, 20_000),
        rec("##", "ㄅㄢˇ-ㄎㄨㄥˋ", "版控", 1.0, 20_000),
    ];
    let mut all = bad.clone();
    all.extend(good.clone());
    store.save(&all).unwrap();
    let text = fs::read_to_string(dir.join(FILE)).unwrap();
    assert_eq!(text.lines().count(), 1 + good.len(), "only the valid new-word records are written");
    assert!(text.contains("#\tㄅㄢˇ-ㄎㄨㄥˋ\t版控\t1\t20000\n"));
    let (_, records, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!(opened, Opened::Loaded { skipped: 0 });
    assert!(records == good, "new-word records differ (contents not printed: R2)");
    // The same bad lines written by hand are skipped one by one at load.
    let lines: String = bad.iter().map(|r| format!("{}\t{}\t{}\t{}\t{}\n", r.context, r.reading.join("-"), r.word, r.weight, r.day)).collect();
    fs::write(dir.join(FILE), format!("{HEADER}\n{lines}#\tㄅㄢˇ-ㄎㄨㄥˋ\t版控\t1\t20000\n")).unwrap();
    let (_, records, opened) = LearnStore::open(&dir).unwrap();
    assert_eq!(opened, Opened::Loaded { skipped: bad.len() });
    assert!(records == good[..1], "only the valid line loads");
}

/// New-word B §2.3, old versions: before new-word B, `valid()` accepted a context only when `context_key(context)`
/// equals it (or it is the global ""). `context_key("#")` is "^", so an older version skips every "#" line at load and
/// its first full rewrite drops them. The rule as it stood is restated here, since the old code is gone.
#[test]
fn old_versions_skip_new_word_lines() {
    use core::learn::{context_key, GLOBAL, NEWWORD};
    let old_context_ok = |c: &str| c == GLOBAL || context_key(c) == c;
    assert_ne!(context_key(NEWWORD), NEWWORD);
    assert!(!old_context_ok(NEWWORD), "an old version would load a new-word line");
    assert!(old_context_ok("管把") && old_context_ok("^") && old_context_ok(""), "the old rule itself");
}

/// New-word B §2.3: new words are capped at NEWWORD_CAP apart from S4's CAPACITY, lowest weight first, and decay and
/// prune like S4 records.
#[test]
fn new_words_have_their_own_cap_and_decay() {
    use core::learn::{NEWWORD_CAP, PRUNE_FLOOR};
    let han: Vec<char> = "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥天地玄黃宇宙洪荒日月盈昃".chars().collect();
    let mut records = Vec::new();
    'fill: for a in &han {
        for b in &han {
            for c in &han {
                let w = (records.len() as f64 + 1.0) / 1000.0 + 0.1; // all live, distinct weights
                records.push(rec("#", "ㄚ-ㄚ-ㄚ", &format!("{a}{b}{c}"), w, 20_000));
                if records.len() == NEWWORD_CAP + 5 {
                    break 'fill;
                }
            }
        }
    }
    records.push(rec("管把", "ㄅㄚˇ", "把", 0.06, 20_000));
    records.push(rec("#", "ㄅㄢˇ-ㄎㄨㄥˋ", "版控", 1.0, 20_000 - 100)); // decayed below PRUNE_FLOOR
    let mut l = Learner::from_records(records);
    l.prune(20_000);
    let new: Vec<&Record> = l.records().iter().filter(|r| r.context == "#").collect();
    assert_eq!(new.len(), NEWWORD_CAP);
    assert!(new.iter().all(|r| r.weight > 0.1 + 5.0 / 1000.0), "the five lightest went");
    assert!(l.records().iter().any(|r| r.word == "把"), "S4 records are not counted against NEWWORD_CAP");
    assert!(!l.records().iter().any(|r| r.word == "版控"), "decayed below {PRUNE_FLOOR}: pruned");
}
