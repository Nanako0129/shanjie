//! What the rerank tests share (engine_rerank.rs, engine_fuzzy.rs, engine_fuzzy_rerank.rs): the data paths, the n-gram model without its
//! Kneser-Ney side file, the toy small model of eval/golden/tinylm-tiny/ patched to pair with it, and the syllable-typing helpers.
#![allow(dead_code)]
use core::engine::*;
use core::lm::{CappedLexicon, Demote, Lm};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, OnceLock};

pub fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}
/// The shipped n-gram model as data/lm holds it (with kn.sjkn since model-v6); fails loudly without it.
pub fn real_lm_path() -> PathBuf {
    let p = root().join("data/lm/bigram.sjlm");
    assert!(p.exists() && root().join("data/lm/classes.sjc").exists(), "data/lm/bigram.sjlm and classes.sjc are needed: see engine_lm.rs");
    p
}
/// data/lm/bigram.sjlm and classes.sjc symlinked into a temporary directory without kn.sjkn, for the version 1 toy.
// ponytail: the directory (two symlinks) is left in the temp dir; a OnceLock never drops.
pub fn lm_path() -> PathBuf {
    static P: OnceLock<PathBuf> = OnceLock::new();
    P.get_or_init(|| {
        let real = real_lm_path();
        let d = std::env::temp_dir().join(format!("shanjie-rerank-nokn-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        for f in ["bigram.sjlm", "classes.sjc"] {
            let _ = std::fs::remove_file(d.join(f));
            std::os::unix::fs::symlink(real.with_file_name(f), d.join(f)).unwrap();
        }
        d.join("bigram.sjlm")
    })
    .clone()
}

pub struct Shared {
    pub lex: Arc<Lexicon>,
    pub lm: Arc<Lm>,
    pub capped: Arc<CappedLexicon>,
}
/// The capped lexicon has the demotion table, as `Engine::load_lm` builds it.
pub fn shared_for(lm_file: &Path) -> Shared {
    let dir = root().join("data/lexicon");
    let lex = load_lexicon(&dir).unwrap();
    let lm = Lm::load(lm_file).unwrap();
    let overlay = capping_overlay(&dir, "").unwrap(); // as Engine::load_lm: overlay-add.tsv + names-add.tsv
    let demote = Demote::parse(&std::fs::read_to_string(dir.join("demote.tsv")).unwrap()).unwrap();
    let capped = Arc::new(CappedLexicon::new(lex.clone(), &overlay, &lm, Some(&demote)).unwrap());
    Shared { lex, lm: Arc::new(lm), capped }
}
/// Loaded once per test binary, without the Kneser-Ney side file (`lm_path`).
pub fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| shared_for(&lm_path()))
}

pub fn sha_bytes(file: &Path) -> Vec<u8> {
    let h = core::eval::sha256_hex(&std::fs::read(file).unwrap());
    (0..32).map(|i| u8::from_str_radix(&h[2 * i..2 * i + 2], 16).unwrap()).collect()
}

/// A temporary file, deleted on drop; derefs to its path.
pub struct TempFile(pub PathBuf);
impl Drop for TempFile {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
impl std::ops::Deref for TempFile {
    type Target = Path;
    fn deref(&self) -> &Path {
        &self.0
    }
}

/// The toy model with `alpha` / `tau` for both profiles and the n-gram hash of `lm` (None: left as recorded, i.e. kn-tiny's).
pub fn sjtl_for(alpha: f64, tau: f64, lm: Option<&Path>) -> TempFile {
    sjtl_with_vocab(alpha, tau, lm, "")
}
/// `sjtl_for` with the characters of `chars` added to the toy's vocabulary (the version 1 header is 100 bytes, then one u32 code point
/// per character): the toy knows about 117 characters and reads every other as one unknown, so candidates made of strangers all get the
/// same LL. The characters take the places of the vocabulary's last entries (each keeps that entry's embedding row, so the LLs differ
/// from one character to the next); one the vocabulary has already is left alone.
pub fn sjtl_with_vocab(alpha: f64, tau: f64, lm: Option<&Path>, chars: &str) -> TempFile {
    let mut b = std::fs::read(root().join("eval/golden/tinylm-tiny/tiny-int8.sjtl")).unwrap();
    if let Some(lm) = lm {
        b[68..100].copy_from_slice(&sha_bytes(lm));
    }
    for at in [36, 52] {
        b[at..at + 8].copy_from_slice(&alpha.to_le_bytes());
        b[at + 8..at + 16].copy_from_slice(&tau.to_le_bytes());
    }
    let v = u32::from_le_bytes(b[32..36].try_into().unwrap()) as usize - 3;
    let known: Vec<u32> = (0..v).map(|i| u32::from_le_bytes(b[100 + 4 * i..104 + 4 * i].try_into().unwrap())).collect();
    let mut fresh: Vec<char> = Vec::new();
    for c in chars.chars() {
        if !known.contains(&(c as u32)) && !fresh.contains(&c) {
            fresh.push(c);
        }
    }
    assert!(fresh.len() <= v, "more new characters than the vocabulary has places");
    for (n, c) in fresh.iter().enumerate() {
        let at = 100 + 4 * (v - 1 - n);
        b[at..at + 4].copy_from_slice(&(*c as u32).to_le_bytes());
    }
    temp(b)
}
/// `b` in its own temporary file (tests run in parallel), removed when the guard drops.
pub fn temp(b: Vec<u8>) -> TempFile {
    static N: AtomicUsize = AtomicUsize::new(0);
    let p = std::env::temp_dir().join(format!("shanjie-rerank-{}-{}.sjtl", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
    std::fs::write(&p, b).unwrap();
    TempFile(p)
}
/// A version 1 toy file (`sjtl_for`) as version 4 (`left` None) or version 5 declaring the left-context length `left`
/// (docs/contracts/context-return.md section 2): its 100 header bytes, both deltas 0, the standard triples for the conservative
/// and aggressive levels too (so every level scores as before), an all-zero side file hash (tuned without KN, as the toy), then L.
pub fn upgraded(v1: &Path, left: Option<u32>) -> TempFile {
    let b = std::fs::read(v1).unwrap();
    let triple = |at: usize| [&b[at..at + 16], &0f64.to_le_bytes()[..]].concat();
    let (chat, formal) = (triple(36), triple(52));
    let mut h = [&b[..100], &[0; 16][..], &chat, &chat, &formal, &formal, &[0; 32][..]].concat();
    let version: u32 = match left {
        Some(l) => {
            h.extend_from_slice(&l.to_le_bytes());
            5
        }
        None => 4,
    };
    h[7] = b'0' + version as u8;
    h[8..12].copy_from_slice(&version.to_le_bytes());
    temp([h.as_slice(), &b[100..]].concat())
}

/// The keys of one syllable: its symbols, then a tone key or Space.
pub fn keys_of(layout: Layout, syl: &str) -> Vec<Key> {
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match layout.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(layout.key_of_symbol(c).expect("symbol has a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}
/// Types the syllables; the last output. Every key must be accepted without an internal error.
pub fn type_syls_in<S: AsRef<str>>(e: &mut Engine, layout: Layout, syls: &[S]) -> Output {
    let mut last = None;
    for syl in syls {
        for key in keys_of(layout, syl.as_ref()) {
            last = Some(e.key(key).expect("no internal error"));
        }
    }
    last.unwrap()
}
/// `type_syls_in` on the standard layout.
pub fn type_syls<S: AsRef<str>>(e: &mut Engine, syls: &[S]) -> Output {
    type_syls_in(e, Layout::Standard, syls)
}
