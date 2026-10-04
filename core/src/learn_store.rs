//! S4 learning file (docs/contracts/s4-learning.md §4): `learning.tsv` in the directory the shell
//! passes (`~/Library/Application Support/shanjie/` in the app; a temporary directory in tests).
//!
//! Format: the header `#shanjie-learning v1`, then one record per line, five tab-separated fields:
//! context key, reading (syllables joined by `-`, as in the lexicon), word, weight, day. A record
//! that would not read back as itself (a tab, newline or control character in a field, a `-` or an
//! empty syllable in the reading, a bad context, weight or day) is never written and is skipped one
//! line at a time on load. When a (context, reading, word) appears on several lines, the last one
//! wins: `append` adds newer versions of records at the end (revision one, §4 and §11).
//!
//! Directory 0700 with an owner check. Two write paths (the engine picks one, §4):
//! - `save`, the full rewrite: the fixed temporary name `learning.tsv.tmp` (unlink, create_new,
//!   O_NOFOLLOW, mode 0600), write, F_BARRIERFSYNC (F_FULLFSYNC if that fails), rename;
//! - `append`: an existing file only, O_WRONLY|O_APPEND|O_NOFOLLOW|O_NONBLOCK, refused unless fstat
//!   shows a regular file of ours, no group/other bits, one link, at least a header's length, and
//!   room under 16 MB; one write, no fsync; a short write is a failure.
//!
//! Loads open with O_NOFOLLOW|O_NONBLOCK, require a regular file by fstat and cap it at 16 MB. A
//! bad header or an oversized file is renamed to `learning.tsv.corrupt` (0600, one copy). Nothing
//! here logs: no path and no record ever leaves this module except as data.

use crate::learn::{context_key, Record, GLOBAL};
use std::fmt::Write as _;
use std::fs::{self, DirBuilder, File, OpenOptions, Permissions};
use std::io::{ErrorKind, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

pub const FILE: &str = "learning.tsv";
pub const TMP: &str = "learning.tsv.tmp";
pub const CORRUPT: &str = "learning.tsv.corrupt";
pub const HEADER: &str = "#shanjie-learning v1";
pub const MAX_BYTES: u64 = 16 * 1024 * 1024;
/// Lines appended since the last full rewrite that force the next write to be a full rewrite (§4):
/// bounds how many superseded lines the file and the load carry. A learning Enter appends at most 3
/// lines, so at least 341 appending Enters pass between two rewrites.
pub const JOURNAL_MAX: usize = 1024;

// <fcntl.h> values. std has no names for them and the crate has no libc dependency; the shipping
// target is macOS only (CI builds and tests core on macOS). The symlink and FIFO tests in
// core/tests/learn_store.rs fail if a value is wrong.
#[cfg(target_os = "macos")]
const O_NOFOLLOW: i32 = 0x0100;
#[cfg(target_os = "macos")]
const O_NONBLOCK: i32 = 0x0004;
#[cfg(target_os = "macos")]
const ELOOP: i32 = 62;
#[cfg(target_os = "macos")]
const F_FULLFSYNC: i32 = 51;
#[cfg(target_os = "macos")]
const F_BARRIERFSYNC: i32 = 85;

extern "C" {
    fn geteuid() -> u32;
    fn fcntl(fd: i32, cmd: i32, ...) -> i32;
}

/// F_BARRIERFSYNC, else F_FULLFSYNC (§4): orders the data before the rename without waiting for the
/// drive's cache flush on the key path (Rust's `sync_all` is F_FULLFSYNC on macOS, p99 35 ms in
/// §11). False when both fail.
fn barrier(f: &File) -> bool {
    // SAFETY: a valid open descriptor; neither command takes an argument.
    unsafe { fcntl(f.as_raw_fd(), F_BARRIERFSYNC) != -1 || fcntl(f.as_raw_fd(), F_FULLFSYNC) != -1 }
}

/// Latest accepted day (9999-12-31 as days since 1970-01-01); earlier than 0 is rejected too.
const MAX_DAY: i64 = 2_932_896;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StoreError {
    /// Any I/O failure (creating the directory, writing, renaming, deleting other than ENOENT).
    Io,
    /// The directory exists but is not owned by this user.
    Ownership,
}

/// What `open` found, for tests and for the shell's status display.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Opened {
    /// No file yet: start empty.
    Fresh,
    /// Loaded; `skipped` bad lines were ignored.
    Loaded { skipped: usize },
    /// Header wrong, not a regular file, or over MAX_BYTES: renamed to CORRUPT (when it was a file),
    /// start empty.
    Corrupt,
}

/// The five-field lines of the valid records, appended to `buf`.
fn lines(records: &[Record], buf: &mut String) {
    for r in records.iter().filter(|r| valid(r)) {
        let _ = writeln!(buf, "{}\t{}\t{}\t{}\t{}", r.context, r.reading.join("-"), r.word, r.weight, r.day);
    }
}

pub struct LearnStore {
    dir: PathBuf,
}

fn io<T>(r: std::io::Result<T>) -> Result<T, StoreError> {
    r.map_err(|_| StoreError::Io)
}

/// Removes `p`; a missing file is success.
fn remove(p: &Path) -> Result<(), StoreError> {
    match fs::remove_file(p) {
        Err(e) if e.kind() != ErrorKind::NotFound => Err(StoreError::Io),
        _ => Ok(()),
    }
}

fn clean(s: &str) -> bool {
    !s.is_empty() && !s.chars().any(char::is_control)
}

/// Whether `r` survives a write and a read unchanged; the one rule for both directions.
fn valid(r: &Record) -> bool {
    (r.context == GLOBAL || context_key(&r.context) == r.context)
        && !r.reading.is_empty()
        && r.reading.iter().all(|s| clean(s) && !s.contains('-'))
        && clean(&r.word)
        && r.weight.is_finite()
        && r.weight >= 0.0
        && (0..=MAX_DAY).contains(&r.day)
}

fn parse(line: &str) -> Option<Record> {
    let f: Vec<&str> = line.split('\t').collect();
    let &[context, reading, word, weight, day] = f.as_slice() else { return None };
    let r = Record {
        context: context.to_string(),
        reading: reading.split('-').map(str::to_string).collect(),
        word: word.to_string(),
        weight: weight.parse().ok()?,
        day: day.parse().ok()?,
    };
    valid(&r).then_some(r)
}

impl LearnStore {
    /// Creates `dir` (0700) if needed, checks its owner, loads `FILE`.
    pub fn open(dir: &Path) -> Result<(LearnStore, Vec<Record>, Opened), StoreError> {
        match DirBuilder::new().recursive(true).mode(0o700).create(dir) {
            Err(e) if e.kind() != ErrorKind::AlreadyExists => return Err(StoreError::Io),
            _ => {}
        }
        // lstat: a symlink in place of the directory is refused rather than followed.
        let md = io(fs::symlink_metadata(dir))?;
        if !md.is_dir() {
            return Err(StoreError::Io);
        }
        // SAFETY: geteuid has no preconditions and cannot fail.
        if md.uid() != unsafe { geteuid() } {
            return Err(StoreError::Ownership);
        }
        if md.mode() & 0o777 != 0o700 {
            io(fs::set_permissions(dir, Permissions::from_mode(0o700)))?;
        }
        let store = LearnStore { dir: dir.to_path_buf() };
        let (records, opened) = store.load()?;
        Ok((store, records, opened))
    }

    fn load(&self) -> Result<(Vec<Record>, Opened), StoreError> {
        let path = self.dir.join(FILE);
        // O_NONBLOCK: a FIFO opens at once instead of waiting for a writer; O_NOFOLLOW: a symlink
        // fails with ELOOP instead of being read.
        let f = match OpenOptions::new().read(true).custom_flags(O_NOFOLLOW | O_NONBLOCK).open(&path) {
            Ok(f) => f,
            Err(e) if e.kind() == ErrorKind::NotFound => return Ok((Vec::new(), Opened::Fresh)),
            // Not a file: nothing to keep. The first save renames over the link itself.
            Err(e) if e.raw_os_error() == Some(ELOOP) => return Ok((Vec::new(), Opened::Corrupt)),
            Err(_) => return Err(StoreError::Io),
        };
        let md = io(f.metadata())?;
        if !md.is_file() {
            return Ok((Vec::new(), Opened::Corrupt));
        }
        let mut bytes = Vec::new();
        if md.len() <= MAX_BYTES {
            io((&f).take(MAX_BYTES + 1).read_to_end(&mut bytes))?;
        }
        let mut lines = bytes.split(|&b| b == b'\n');
        if md.len() > MAX_BYTES || bytes.len() as u64 > MAX_BYTES || lines.next() != Some(HEADER.as_bytes()) {
            self.quarantine(&path, &f)?;
            return Ok((Vec::new(), Opened::Corrupt));
        }
        let (mut records, mut skipped) = (Vec::<Record>::new(), 0);
        // Last line wins for a repeated (context, reading, word): appends carry the newer version.
        let mut at = std::collections::HashMap::new();
        for line in lines.filter(|l| !l.is_empty()) {
            let Some(r) = std::str::from_utf8(line).ok().and_then(parse) else {
                skipped += 1;
                continue;
            };
            match at.entry((r.context.clone(), r.reading.clone(), r.word.clone())) {
                std::collections::hash_map::Entry::Occupied(e) => records[*e.get()] = r,
                std::collections::hash_map::Entry::Vacant(e) => {
                    e.insert(records.len());
                    records.push(r);
                }
            }
        }
        Ok((records, Opened::Loaded { skipped }))
    }

    /// Renames the open file to CORRUPT (replacing an older copy), then 0600 through the open
    /// descriptor, so the mode lands on the renamed file and never on anything a path points to.
    fn quarantine(&self, path: &Path, f: &File) -> Result<(), StoreError> {
        io(fs::rename(path, self.dir.join(CORRUPT)))?;
        io(f.set_permissions(Permissions::from_mode(0o600)))
    }

    /// Full rewrite: atomic replace of `FILE` with `records`. A failed barrier is a failure.
    pub fn save(&self, records: &[Record]) -> Result<(), StoreError> {
        self.rewrite(records, false)
    }

    /// Full rewrite for a forget (§4): renames even when both barriers fail, since leaving the
    /// forgotten word on disk is worse than a less durable file (privacy over durability).
    pub fn save_forgetting(&self, records: &[Record]) -> Result<(), StoreError> {
        self.rewrite(records, true)
    }

    fn rewrite(&self, records: &[Record], rename_unsynced: bool) -> Result<(), StoreError> {
        let mut buf = String::with_capacity(64 + records.len() * 40);
        buf.push_str(HEADER);
        buf.push('\n');
        // Straight into `buf`: a String per record took 2-3 times as long at CAPACITY (measured
        // 2026-10-05).
        lines(records, &mut buf);
        let tmp = self.dir.join(TMP);
        remove(&tmp)?;
        let written = (|| {
            let mut f = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .custom_flags(O_NOFOLLOW)
                .open(&tmp)?;
            f.write_all(buf.as_bytes())?;
            if !barrier(&f) && !rename_unsynced {
                return Err(std::io::Error::other("sync"));
            }
            drop(f);
            fs::rename(&tmp, self.dir.join(FILE))
        })();
        if written.is_err() {
            let _ = fs::remove_file(&tmp);
            return Err(StoreError::Io);
        }
        Ok(())
    }

    /// Appends `records` (the ones a commit changed) to the existing `FILE` in one write (§4). Any
    /// refusal or a short write is `Err`; the caller then does a full rewrite, which also replaces
    /// whatever a short write left at the end.
    pub fn append(&self, records: &[Record]) -> Result<(), StoreError> {
        let mut buf = String::new();
        lines(records, &mut buf);
        // O_NOFOLLOW: a symlink fails with ELOOP; O_NONBLOCK: a FIFO without a reader fails with
        // ENXIO instead of waiting. No O_CREAT: a missing file is a failure, not a new headerless file.
        let mut f = io(OpenOptions::new().append(true).custom_flags(O_NOFOLLOW | O_NONBLOCK).open(self.dir.join(FILE)))?;
        let md = io(f.metadata())?;
        // SAFETY: geteuid has no preconditions and cannot fail.
        let ours = md.uid() == unsafe { geteuid() };
        // At least the header and its newline: a file emptied by hand gets a full rewrite instead of
        // headerless lines that the next load would quarantine whole.
        let fits = md.len() > HEADER.len() as u64 && md.len() + buf.len() as u64 <= MAX_BYTES;
        if !md.is_file() || !ours || md.mode() & 0o077 != 0 || md.nlink() != 1 || !fits {
            return Err(StoreError::Io);
        }
        match f.write(buf.as_bytes()) {
            Ok(n) if n == buf.len() => Ok(()),
            _ => Err(StoreError::Io),
        }
    }

    /// Deletes FILE, TMP and CORRUPT (ENOENT is success); keeps the directory and its backup flag.
    /// Every file is attempted even after a failure.
    pub fn clear(&self) -> Result<(), StoreError> {
        [FILE, TMP, CORRUPT].iter().fold(Ok(()), |acc, name| acc.and(remove(&self.dir.join(name))))
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }
}
