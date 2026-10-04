//! S4 learning file (docs/contracts/s4-learning.md §4): `learning.tsv` in the directory the shell
//! passes (`~/Library/Application Support/shanjie/` in the app; a temporary directory in tests).
//!
//! Format: the header `#shanjie-learning v1`, then one record per line, five tab-separated fields:
//! context key, reading (syllables joined by `-`, as in the lexicon), word, weight, day. A record
//! that would not read back as itself (a tab, newline or control character in a field, a `-` or an
//! empty syllable in the reading, a bad context, weight or day) is never written and is skipped one
//! line at a time on load.
//!
//! Directory 0700 with an owner check; writes go to the fixed temporary name `learning.tsv.tmp`
//! (unlink, create_new, O_NOFOLLOW, mode 0600, write, fsync, rename); loads open with
//! O_NOFOLLOW|O_NONBLOCK, require a regular file by fstat and cap it at 16 MB. A bad header or an
//! oversized file is renamed to `learning.tsv.corrupt` (0600, one copy). Nothing here logs: no path
//! and no record ever leaves this module except as data.

use crate::learn::{context_key, Record, GLOBAL};
use std::fmt::Write as _;
use std::fs::{self, DirBuilder, File, OpenOptions, Permissions};
use std::io::{ErrorKind, Read, Write};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

pub const FILE: &str = "learning.tsv";
pub const TMP: &str = "learning.tsv.tmp";
pub const CORRUPT: &str = "learning.tsv.corrupt";
pub const HEADER: &str = "#shanjie-learning v1";
pub const MAX_BYTES: u64 = 16 * 1024 * 1024;

// <fcntl.h> values. std has no names for them and the crate has no libc dependency; the shipping
// target is macOS only (CI builds and tests core on macOS). The symlink and FIFO tests in
// core/tests/learn_store.rs fail if a value is wrong.
#[cfg(target_os = "macos")]
const O_NOFOLLOW: i32 = 0x0100;
#[cfg(target_os = "macos")]
const O_NONBLOCK: i32 = 0x0004;
#[cfg(target_os = "macos")]
const ELOOP: i32 = 62;

extern "C" {
    fn geteuid() -> u32;
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
        let (mut records, mut skipped) = (Vec::new(), 0);
        for line in lines.filter(|l| !l.is_empty()) {
            match std::str::from_utf8(line).ok().and_then(parse) {
                Some(r) => records.push(r),
                None => skipped += 1,
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

    /// Atomic replace of `FILE` with `records`.
    pub fn save(&self, records: &[Record]) -> Result<(), StoreError> {
        let mut buf = String::with_capacity(64 + records.len() * 40);
        buf.push_str(HEADER);
        buf.push('\n');
        // Straight into `buf`: a String per record took 2-3 times as long at CAPACITY (measured
        // 2026-10-05), on every learning Enter.
        for r in records.iter().filter(|r| valid(r)) {
            let _ = writeln!(buf, "{}\t{}\t{}\t{}\t{}", r.context, r.reading.join("-"), r.word, r.weight, r.day);
        }
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
            f.sync_all()?;
            drop(f);
            fs::rename(&tmp, self.dir.join(FILE))
        })();
        if written.is_err() {
            let _ = fs::remove_file(&tmp);
            return Err(StoreError::Io);
        }
        Ok(())
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
