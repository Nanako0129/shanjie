//! A whole file mapped read-only (docs/spec/decoder.md §3.5, docs/spec/semantic-rerank.md §2.2): the n-gram model and its side files
//! (bigram/colloquial `.sjlm`, `classes.sjc`, `kn.sjkn`, the trigram tables; `Mapped::open`, re-exported by `trigram.rs` next to
//! `MappedBytes` for `lm.rs`) and the small model's `.sjtl` (`tinylm.rs`, `Mapped::open_bounded` with a cap). The only read-only
//! mapping in the crate. One open, the checks on that same handle, then one mapping of exactly the length checked, so the file
//! checked is the file mapped. R2: errors carry a kind only.
#![deny(unsafe_op_in_unsafe_fn)]

use std::ffi::c_void;
use std::ops::Deref;
#[cfg(unix)]
use std::os::fd::AsRawFd;
use std::path::Path;

/// `O_NONBLOCK`: opening a FIFO for reading must not wait for a writer (it is rejected right after, as not a regular file).
#[cfg(target_os = "linux")]
const O_NONBLOCK: i32 = 0o4000;
#[cfg(all(unix, not(target_os = "linux")))]
const O_NONBLOCK: i32 = 0x0004; // macOS and the BSDs

// <sys/mman.h>: PROT_READ and MAP_PRIVATE have the same values on macOS, x86_64 Linux and aarch64 Linux (the generic values; this block is Unix only);
// `off_t` is 64 bits on all three. std has no names for them and the crate has no libc dependency.
#[cfg(unix)]
const PROT_READ: i32 = 1;
#[cfg(unix)]
const MAP_PRIVATE: i32 = 2;
/// `mmap` returns this (all bits set), not null, on failure.
#[cfg(unix)]
const MAP_FAILED: *mut c_void = !0usize as *mut c_void;

#[cfg(unix)]
extern "C" {
    fn mmap(addr: *mut c_void, len: usize, prot: i32, flags: i32, fd: i32, off: i64) -> *mut c_void;
    fn munmap(addr: *mut c_void, len: usize) -> i32;
}

/// Why `Mapped::open_bounded` refused. `Io`: the file cannot be opened, is not a regular (disk) file, or the mapping failed; `Empty`: 0
/// bytes; `TooLarge`: over the caller's cap, or over `isize::MAX` (what a slice may span; on a 32-bit target a length that does not fit is refused
/// here, never truncated).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum MapError {
    Io,
    Empty,
    TooLarge,
}

/// The length checks of `open_bounded`, on the 64-bit length the file system reported: 0 is `Empty`, past `max` or past `isize::MAX` is
/// `TooLarge`; otherwise the length as `usize`. Separate so the bounds a host cannot make a file for (`isize::MAX`) are tested.
fn checked_len(len64: u64, max: Option<u64>) -> Result<usize, MapError> {
    if len64 == 0 {
        return Err(MapError::Empty);
    }
    if max.is_some_and(|m| len64 > m) {
        return Err(MapError::TooLarge);
    }
    let len = isize::try_from(len64).map_err(|_| MapError::TooLarge)?;
    usize::try_from(len).map_err(|_| MapError::TooLarge)
}

/// A whole file mapped read-only. The pages are clean file-backed pages: the system can drop them under memory pressure and read
/// them back, and they are not part of the process's phys_footprint (the point of docs/spec/decoder.md §3.5).
/// If the file is truncated or rewritten in place while it is mapped, reading a page past the new end is a SIGBUS. The install
/// paths never do that: scripts/install-ime.sh, the installer and the Homebrew cask replace the whole bundle by rename; the Windows
/// installer stops the host before it replaces a file.
pub(crate) struct Mapped {
    ptr: *mut c_void,
    len: usize,
}

// SAFETY: this handle only reads, so sharing it between threads is a plain shared read; that the bytes stay the same is the
// caller's contract (`Mapped::open_bounded`: no in-place rewrite or truncation while mapped).
unsafe impl Send for Mapped {}
unsafe impl Sync for Mapped {}

impl Mapped {
    /// `open_bounded` without a cap, for the n-gram files (`lm.rs`): with no cap `TooLarge` can only mean past `isize::MAX`. Each caller
    /// names the error for its file: `map_or_empty` turns `Empty` into an empty buffer (the parser reports `Format`, as a `std::fs::read`
    /// of it did) and `Io` or `TooLarge` into `LmError::Io`; `kn.sjkn` is `KnSide` and the trigram tables `TriTable` for any refusal.
    ///
    /// # Safety
    /// As `open_bounded`.
    pub(crate) unsafe fn open(path: &Path) -> Result<Mapped, MapError> {
        // SAFETY: the caller's promise, passed on.
        unsafe { Self::open_bounded(path, None) }
    }

    /// Maps the regular file at `path` read-only. Opened once (Unix: `O_NONBLOCK`, so a FIFO does not block, and no `O_NOFOLLOW`: a
    /// development checkout links its model files; Windows: `GENERIC_READ` with `FILE_SHARE_READ` only), then that handle is checked (a
    /// regular file, on Windows also `FILE_TYPE_DISK`) and its 64-bit length bounded (`checked_len`) before anything is mapped; Windows
    /// maps exactly the checked length.
    ///
    /// # Safety
    /// The file must not be truncated or rewritten in place while the mapping is alive: a page past a new end is a SIGBUS, and
    /// bytes that change after the caller's parse checked them break the invariants its lookups rely on. Replacing the file by
    /// rename is fine (the mapping keeps the old inode). Windows refuses deletion, truncation and replacement while a view exists,
    /// but not an in-place write by a process that opens the file for writing later; that stays the caller's promise.
    #[cfg(unix)]
    pub(crate) unsafe fn open_bounded(path: &Path, max: Option<u64>) -> Result<Mapped, MapError> {
        use std::os::unix::fs::OpenOptionsExt;
        let f = std::fs::OpenOptions::new().read(true).custom_flags(O_NONBLOCK).open(path).map_err(|_| MapError::Io)?;
        let meta = f.metadata().map_err(|_| MapError::Io)?;
        if !meta.is_file() {
            return Err(MapError::Io);
        }
        let len = checked_len(meta.len(), max)?;
        // SAFETY: a valid descriptor and a checked non-zero length; the kernel keeps the mapping after the descriptor is closed (dropped below).
        let ptr = unsafe { mmap(std::ptr::null_mut(), len, PROT_READ, MAP_PRIVATE, f.as_raw_fd(), 0) };
        if ptr == MAP_FAILED {
            return Err(MapError::Io);
        }
        Ok(Mapped { ptr, len })
    }

    /// Windows: the same contract through a file mapping: `CreateFileMappingW(PAGE_READONLY)` of the checked length (high and low
    /// 32 bits) and `MapViewOfFile(FILE_MAP_READ)` of that many bytes. The mapping object's handle is closed at once; the view keeps the
    /// section alive until `drop`.
    ///
    /// # Safety
    /// As the Unix `open_bounded`.
    #[cfg(windows)]
    pub(crate) unsafe fn open_bounded(path: &Path, max: Option<u64>) -> Result<Mapped, MapError> {
        use std::os::windows::fs::OpenOptionsExt;
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::Storage::FileSystem::{GetFileType, FILE_SHARE_READ, FILE_TYPE_DISK};
        use windows_sys::Win32::System::Memory::{CreateFileMappingW, MapViewOfFile, FILE_MAP_READ, PAGE_READONLY};
        let f = std::fs::OpenOptions::new().read(true).share_mode(FILE_SHARE_READ).open(path).map_err(|_| MapError::Io)?;
        let meta = f.metadata().map_err(|_| MapError::Io)?;
        let h = f.as_raw_handle();
        // SAFETY: `h` is the open handle `f` owns for the whole function.
        if !meta.is_file() || unsafe { GetFileType(h) } != FILE_TYPE_DISK {
            return Err(MapError::Io);
        }
        let len64 = meta.len();
        let len = checked_len(len64, max)?;
        // SAFETY: a valid read-only handle; the size is the checked non-zero length of the file; no name, default security.
        let map = unsafe { CreateFileMappingW(h, std::ptr::null(), PAGE_READONLY, (len64 >> 32) as u32, len64 as u32, std::ptr::null()) };
        if map.is_null() {
            return Err(MapError::Io);
        }
        // SAFETY: `map` is the mapping object just created, of `len` bytes; the view is exactly those bytes from offset 0.
        let view = unsafe { MapViewOfFile(map, FILE_MAP_READ, 0, 0, len) };
        // SAFETY: `map` is closed once; the view (if any) holds its own reference to the section.
        unsafe { CloseHandle(map) };
        if view.Value.is_null() {
            return Err(MapError::Io);
        }
        Ok(Mapped { ptr: view.Value, len })
    }
}

impl Deref for Mapped {
    type Target = [u8];
    #[inline]
    fn deref(&self) -> &[u8] {
        // SAFETY: `ptr` maps `len` readable bytes until `drop`; `len <= isize::MAX` (`checked_len`).
        unsafe { std::slice::from_raw_parts(self.ptr as *const u8, self.len) }
    }
}

impl Drop for Mapped {
    fn drop(&mut self) {
        // SAFETY: exactly the range `mmap` returned, unmapped once.
        #[cfg(unix)]
        let _ = unsafe { munmap(self.ptr, self.len) };
        // SAFETY: exactly the view `MapViewOfFile` returned, unmapped once.
        #[cfg(windows)]
        let _ = unsafe {
            windows_sys::Win32::System::Memory::UnmapViewOfFile(windows_sys::Win32::System::Memory::MEMORY_MAPPED_VIEW_ADDRESS { Value: self.ptr })
        };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// SEC-05, SEC-07: the length bounds (0 is `Empty`; past the cap or past `isize::MAX`, which no test host can make a file of, is `TooLarge`;
    /// at the cap it passes), then the handle checks on real files: a directory and a FIFO are `Io`, an empty file `Empty`, a sparse file over the cap
    /// `TooLarge`, and a symlink to a good file maps (no `O_NOFOLLOW`: a development checkout links its models), with the same bytes.
    #[test]
    fn mapped_open_bounded_validates_u64_and_isize_max_bounds() {
        assert_eq!(checked_len(0, None), Err(MapError::Empty));
        assert_eq!(checked_len(10, Some(9)), Err(MapError::TooLarge));
        assert_eq!(checked_len(9, Some(9)), Ok(9));
        assert_eq!(checked_len(isize::MAX as u64 + 1, None), Err(MapError::TooLarge));
        assert_eq!(checked_len(u64::MAX, None), Err(MapError::TooLarge));
        assert_eq!(checked_len(isize::MAX as u64, None), Ok(isize::MAX as usize));

        let dir = std::env::temp_dir().join(format!("shanjie-mmap-bounds-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let ok = dir.join("ok.bin");
        std::fs::write(&ok, b"abcdef").unwrap();
        let open = |p: &Path, max: Option<u64>| unsafe { Mapped::open_bounded(p, max) }.map(|m| m.to_vec());
        assert_eq!(open(&ok, Some(6)), Ok(b"abcdef".to_vec()), "at the cap");
        assert_eq!(open(&ok, Some(5)).err(), Some(MapError::TooLarge), "over the cap");
        assert_eq!(open(&dir, None).err(), Some(MapError::Io), "a directory");
        let empty = dir.join("empty.bin");
        std::fs::write(&empty, b"").unwrap();
        assert_eq!(open(&empty, None).err(), Some(MapError::Empty), "empty");
        // `open` (the n-gram files): the same checks with no cap.
        assert_eq!(unsafe { Mapped::open(&empty) }.err(), Some(MapError::Empty), "open: empty");
        assert_eq!(unsafe { Mapped::open(&dir) }.err(), Some(MapError::Io), "open: a directory");
        assert_eq!(unsafe { Mapped::open(&ok) }.map(|m| m.to_vec()), Ok(b"abcdef".to_vec()), "open: no cap");
        let big = dir.join("big.bin");
        std::fs::File::create(&big).unwrap().set_len((64 << 20) + 1).unwrap();
        assert_eq!(open(&big, Some(64 << 20)).err(), Some(MapError::TooLarge), "sparse, over the cap");
        assert_eq!(open(&dir.join("missing"), None).err(), Some(MapError::Io), "missing");
        #[cfg(unix)]
        {
            let link = dir.join("link.bin");
            std::os::unix::fs::symlink(&ok, &link).unwrap();
            assert_eq!(open(&link, None), Ok(b"abcdef".to_vec()), "a symlink is followed");
            let fifo = dir.join("fifo");
            assert!(std::process::Command::new("mkfifo").arg(&fifo).status().unwrap().success(), "mkfifo");
            assert_eq!(open(&fifo, None).err(), Some(MapError::Io), "a FIFO, refused without blocking");
        }
        std::fs::remove_dir_all(dir).unwrap();
    }
}
