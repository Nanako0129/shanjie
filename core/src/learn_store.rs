//! S4 learning file (docs/contracts/s4-learning.md §4): `learning.tsv` in the directory the shell
//! passes (`~/Library/Application Support/shanjie/` in the app; a temporary directory in tests).
//!
//! Interface commit (contract §8 "介面先行"): signatures are final; bodies are the security-executor's.
//! Rules the bodies must follow are in §4: header `#shanjie-learning v1`, five tab-separated fields,
//! directory 0700 with an owner check, fixed temporary name `learning.tsv.tmp` (unlink, create_new,
//! O_NOFOLLOW, mode 0600, write, fsync, rename), load with O_NOFOLLOW|O_NONBLOCK, fstat regular file,
//! 16 MB cap, bad lines skipped one by one, a bad header renames the file to `learning.tsv.corrupt`
//! (0600, one copy). Never log a path or a record.

use crate::learn::Record;
use std::path::{Path, PathBuf};

pub const FILE: &str = "learning.tsv";
pub const TMP: &str = "learning.tsv.tmp";
pub const CORRUPT: &str = "learning.tsv.corrupt";
pub const HEADER: &str = "#shanjie-learning v1";
pub const MAX_BYTES: u64 = 16 * 1024 * 1024;

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

impl LearnStore {
    /// Creates `dir` (0700) if needed, checks its owner, loads `FILE`.
    pub fn open(dir: &Path) -> Result<(LearnStore, Vec<Record>, Opened), StoreError> {
        let _ = dir;
        todo!("S4 security-executor: §4 load")
    }
    /// Atomic replace of `FILE` with `records`.
    pub fn save(&self, _records: &[Record]) -> Result<(), StoreError> {
        todo!("S4 security-executor: §4 write")
    }
    /// Deletes FILE, TMP and CORRUPT (ENOENT is success); keeps the directory and its backup flag.
    pub fn clear(&self) -> Result<(), StoreError> {
        todo!("S4 security-executor: §4 clear")
    }
    pub fn dir(&self) -> &Path {
        &self.dir
    }
}
