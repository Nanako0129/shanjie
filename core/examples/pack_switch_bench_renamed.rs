//! pack_switch_bench: the measurements of the pack switch (shared parts: docs/spec/c-abi.md §5.4): what the new engine built from another engine's loaded
//! parts (`Engine::share`, `Engine::new_shared`) costs the engine that keeps typing, how long it takes against building from the files, and what
//! two engines alive at once hold. Public API only; it reads the repo's `data/lexicon`, `data/packs` and `data/lm`. CI builds it and does not run it.
//!
//! `cargo build --release --locked -p core --example pack_switch_bench`, then, from the repo root, in an idle window:
//!   B=target/release/examples/pack_switch_bench
//!   $B typing  data/lexicon data/packs data/lm <from mask> <to mask> <rounds>    every round: p95 per key without and with a build
//!   $B build   data/lexicon data/packs data/lm <from mask> <to mask> <rounds>    shared and from-files builds alternating, in this process
//!   $B memory  data/lexicon data/packs data/lm <mask a> <mask b> <switches> shared|files   footprint over the switches (macOS only)
//!   $B phases  data/lexicon data/packs data/lm <mask> <rounds>                   each construction step alone (reported only)
//! Every line starts with `pack-switch bench:` and has `name=value` fields; the first line of a run is the load at the start, the last the load at
//! the end. `typing` and `build` check their own criterion and exit 1 when a row fails; `memory` only reports (two processes are compared by hand).
use core::engine::*;
use std::path::{Path, PathBuf};
use std::time::Instant;

const L: Layout = Layout::Standard;
const PREFIX: &str = "pack-switch bench:";
/// The fixed sentence of `typing`: 12 syllables, then Enter.
const SENTENCE: [&str; 12] = ["ㄐㄧㄣ", "ㄊㄧㄢ", "ㄊㄧㄢ", "ㄑㄧˋ", "ㄓㄣ", "ㄏㄠˇ", "ㄨㄛˇ", "ㄇㄣ˙", "ㄑㄩˋ", "ㄍㄨㄥ", "ㄩㄢˊ", "ㄨㄢˊ"];
const IDLE_SENTENCES: usize = 200;
/// Criteria for `typing`: while a build runs on another thread, the typing engine's p95 per key must be at most this factor of its p95 without a build, and below one
/// frame (`MAX_BUILDING_P95_US`).
const MAX_RATIO: f64 = 1.25;
const MAX_BUILDING_P95_US: f64 = 16000.0;
/// A shared build must be at least this much faster than building from the files (model 278 + small model 44 + reading table 59, ms).
const MIN_SAVED_MS: f64 = 300.0;

struct Dirs {
    lex: PathBuf,
    packs: PathBuf,
    lm: PathBuf,
}

impl Dirs {
    fn packs_arg(&self, mask: u32) -> Option<(&Path, u32)> {
        (mask != 0).then_some((self.packs.as_path(), mask))
    }
}

/// What the shell does without sharing: `new_with_packs`, `load_lm`, `load_tinylm`, `load_colloquial`, then the settings that change the work
/// of a key (the rerank on).
fn build_files(d: &Dirs, mask: u32) -> Engine {
    let mut e = Engine::new_with_packs(&d.lex, L, d.packs_arg(mask)).expect("new_with_packs");
    e.load_lm(&d.lm.join("bigram.sjlm")).expect("load_lm");
    e.load_tinylm(&d.lm.join("tinylm.sjtl")).expect("load_tinylm");
    e.load_colloquial(&d.lm.join("colloquial.sjlm")).expect("load_colloquial");
    e.set_rerank(true).expect("set_rerank");
    e
}

fn build_shared(shared: &Shared, d: &Dirs, mask: u32) -> Engine {
    let mut e = Engine::new_shared(shared, L, d.packs_arg(mask)).expect("new_shared");
    e.set_rerank(true).expect("set_rerank");
    e
}

fn syl_keys(syl: &str) -> Vec<Key> {
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match L.key_of_tone(c) {
            Some(tk) if "ˊˇˋ˙".contains(c) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            _ => v.push(Key::ch(L.key_of_symbol(c).expect("symbol has a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}

/// The sentence as keys, built once.
fn sentence_keys() -> Vec<Key> {
    let mut keys: Vec<Key> = SENTENCE.iter().flat_map(|s| syl_keys(s)).collect();
    keys.push(Key::new(KeyKind::Enter));
    keys
}

/// Types the sentence once; pushes every key's latency (microseconds).
fn type_once(e: &mut Engine, keys: &[Key], lat: &mut Vec<f64>) {
    for &k in keys {
        let t = Instant::now();
        e.key(k).expect("key");
        lat.push(t.elapsed().as_secs_f64() * 1e6);
    }
}

fn p95(v: &mut [f64]) -> f64 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v[((v.len() - 1) as f64 * 0.95) as usize]
}

fn spread(v: &[f64]) -> (f64, f64, f64) {
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    (s[0], s[s.len() / 2], s[s.len() - 1])
}

fn ms(t: Instant) -> f64 {
    t.elapsed().as_secs_f64() * 1e3
}

/// The one-minute load average (the system's), `na` where there is none.
fn load1() -> String {
    #[cfg(unix)]
    {
        extern "C" {
            fn getloadavg(avg: *mut f64, n: i32) -> i32;
        }
        let mut a = [0f64; 3];
        // SAFETY: `a` holds the three doubles getloadavg(3) writes at most.
        if unsafe { getloadavg(a.as_mut_ptr(), 3) } >= 1 {
            return format!("{:.2}", a[0]);
        }
    }
    "na".to_string()
}

fn say(fields: &str) {
    println!("{PREFIX} {fields}");
}

// ---- the process footprint (macOS) ----

/// phys_footprint in MiB, `None` off macOS. rusage_info_v2: 16 bytes of uuid, then u64 fields (resident the 7th, footprint the 8th).
fn footprint_mib() -> Option<f64> {
    #[cfg(target_os = "macos")]
    {
        extern "C" {
            fn proc_pid_rusage(pid: i32, flavor: i32, buffer: *mut u64) -> i32;
            fn getpid() -> i32;
        }
        let mut b = [0u64; 64];
        // SAFETY: flavor 2 fills a `rusage_info_v2`, which fits in 64 u64.
        let rc = unsafe { proc_pid_rusage(getpid(), 2, b.as_mut_ptr()) };
        assert_eq!(rc, 0, "proc_pid_rusage");
        Some(b[9] as f64 / 1048576.0)
    }
    #[cfg(not(target_os = "macos"))]
    None
}

/// Polls the footprint every millisecond; `lap` returns the largest since the last lap (MiB), `None` off macOS.
struct Sampler {
    stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
    peak: std::sync::Arc<std::sync::atomic::AtomicU64>,
    h: Option<std::thread::JoinHandle<()>>,
}

impl Sampler {
    fn start() -> Sampler {
        use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
        let (stop, peak) = (std::sync::Arc::new(AtomicBool::new(false)), std::sync::Arc::new(AtomicU64::new(0)));
        let (s, p) = (stop.clone(), peak.clone());
        let h = std::thread::spawn(move || {
            while !s.load(Ordering::Relaxed) {
                if let Some(m) = footprint_mib() {
                    p.fetch_max((m * 1024.0) as u64, Ordering::Relaxed);
                }
                std::thread::sleep(std::time::Duration::from_millis(1));
            }
        });
        Sampler { stop, peak, h: Some(h) }
    }

    fn lap(&self) -> Option<f64> {
        let kib = self.peak.swap(0, std::sync::atomic::Ordering::Relaxed);
        footprint_mib().map(|_| kib as f64 / 1024.0)
    }
}

impl Drop for Sampler {
    fn drop(&mut self) {
        self.stop.store(true, std::sync::atomic::Ordering::Relaxed);
        if let Some(h) = self.h.take() {
            let _ = h.join();
        }
    }
}

fn mib(v: Option<f64>) -> String {
    v.map_or("na".to_string(), |m| format!("{m:.0}"))
}

// ---- modes ----

/// p95 of the typing engine's keys without a build and while another thread builds the target engine by sharing, `rounds` times.
fn typing(d: &Dirs, from: u32, to: u32, rounds: usize) -> bool {
    let keys = sentence_keys();
    let mut src = build_files(d, from);
    let mut ok = true;
    for round in 1..=rounds {
        let mut idle = Vec::new();
        for _ in 0..IDLE_SENTENCES {
            type_once(&mut src, &keys, &mut idle);
        }
        let idle_p95 = p95(&mut idle);
        let shared = src.share().expect("share");
        let (done, mut building) = (std::sync::atomic::AtomicBool::new(false), Vec::new());
        let (built, build_ms) = std::thread::scope(|s| {
            let h = s.spawn(|| {
                let t = Instant::now();
                let e = build_shared(&shared, d, to);
                done.store(true, std::sync::atomic::Ordering::Release);
                (e, ms(t))
            });
            while !done.load(std::sync::atomic::Ordering::Acquire) {
                type_once(&mut src, &keys, &mut building);
            }
            h.join().unwrap()
        });
        let building_p95 = p95(&mut building);
        let ratio = building_p95 / idle_p95;
        let pass = ratio <= MAX_RATIO && building_p95 < MAX_BUILDING_P95_US;
        ok &= pass;
        say(&format!(
            "typing from={from} to={to} round={round} idle_p95_us={idle_p95:.0} building_p95_us={building_p95:.0} ratio={ratio:.3} build_ms={build_ms:.0} keys_while_building={} pass={}",
            building.len(),
            pass as u8
        ));
        drop(built);
        drop(shared);
    }
    ok
}

/// The target engine built by sharing and from the files, alternating, each on its own thread, `rounds` times: from the call to the engine in hand.
fn build(d: &Dirs, from: u32, to: u32, rounds: usize) -> bool {
    let src = build_files(d, from);
    let shared = src.share().expect("share");
    let (mut sh, mut fi) = (Vec::new(), Vec::new());
    for _ in 0..rounds {
        for shared_path in [true, false] {
            let t = Instant::now();
            let e = std::thread::scope(|s| s.spawn(|| if shared_path { build_shared(&shared, d, to) } else { build_files(d, to) }).join().unwrap());
            let took = ms(t);
            drop(e);
            if shared_path { &mut sh } else { &mut fi }.push(took);
        }
    }
    let ((smin, smed, smax), (fmin, fmed, fmax)) = (spread(&sh), spread(&fi));
    let saved = fmed - smed;
    let pass = saved >= MIN_SAVED_MS;
    say(&format!(
        "build from={from} to={to} shared_ms_min={smin:.0} shared_ms_median={smed:.0} shared_ms_max={smax:.0} files_ms_min={fmin:.0} files_ms_median={fmed:.0} files_ms_max={fmax:.0} saved_ms={saved:.0} pass={}",
        pass as u8
    ));
    pass
}

/// A fresh process: the engine of mask `a` from the files, then `switches` switches between the masks, each built on another thread (sharing, or
/// loading every file again) and the old engine freed after the new one is in hand; the footprint is sampled every millisecond.
fn memory(d: &Dirs, a: u32, b: u32, switches: usize, shared_path: bool) {
    let sampler = Sampler::start();
    let mut cur = build_files(d, a);
    std::thread::sleep(std::time::Duration::from_millis(300));
    let one = footprint_mib();
    sampler.lap();
    let mut both = Vec::new();
    for i in 0..switches {
        let to = if i % 2 == 0 { b } else { a };
        let new = if shared_path {
            let shared = cur.share().expect("share");
            std::thread::scope(|s| s.spawn(|| build_shared(&shared, d, to)).join().unwrap())
        } else {
            std::thread::scope(|s| s.spawn(|| build_files(d, to)).join().unwrap())
        };
        both.push(sampler.lap());
        drop(std::mem::replace(&mut cur, new));
        std::thread::sleep(std::time::Duration::from_millis(50));
        sampler.lap();
    }
    let max = |v: &[Option<f64>]| v.iter().flatten().copied().fold(None, |m: Option<f64>, x| Some(m.map_or(x, |m| m.max(x))));
    let tail = &both[both.len().saturating_sub(4)..];
    say(&format!(
        "memory path={} one_engine_mib={} first_both_mib={} peak_mib={} steady_mib={}",
        if shared_path { "shared" } else { "files" },
        mib(one),
        mib(both[0]),
        mib(max(&both)),
        mib(max(tail))
    ));
}

/// Each construction step alone, `rounds` times (reported only).
fn phases(d: &Dirs, mask: u32, rounds: usize) {
    let steps = ["new_with_packs", "load_lm", "load_tinylm", "load_colloquial", "share", "new_shared"];
    let mut t: Vec<Vec<f64>> = vec![Vec::new(); steps.len()];
    for _ in 0..rounds {
        let i = Instant::now();
        let mut e = Engine::new_with_packs(&d.lex, L, d.packs_arg(mask)).expect("new_with_packs");
        t[0].push(ms(i));
        let i = Instant::now();
        e.load_lm(&d.lm.join("bigram.sjlm")).expect("load_lm");
        t[1].push(ms(i));
        let i = Instant::now();
        e.load_tinylm(&d.lm.join("tinylm.sjtl")).expect("load_tinylm");
        t[2].push(ms(i));
        let i = Instant::now();
        e.load_colloquial(&d.lm.join("colloquial.sjlm")).expect("load_colloquial");
        t[3].push(ms(i));
        let i = Instant::now();
        let shared = e.share().expect("share");
        t[4].push(ms(i));
        let i = Instant::now();
        let n = Engine::new_shared(&shared, L, d.packs_arg(mask)).expect("new_shared");
        t[5].push(ms(i));
        drop((n, shared, e));
    }
    for (name, v) in steps.iter().zip(&t) {
        let (min, med, max) = spread(v);
        say(&format!("phases mask={mask} step={name} ms_min={min:.1} ms_median={med:.1} ms_max={max:.1}"));
    }
}

fn usage() -> ! {
    eprintln!("usage: pack_switch_bench typing|build <lexicon> <packs> <models> <from> <to> <rounds>\n       pack_switch_bench memory <lexicon> <packs> <models> <mask a> <mask b> <switches> shared|files\n       pack_switch_bench phases <lexicon> <packs> <models> <mask> <rounds>");
    std::process::exit(2)
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() < 6 {
        usage();
    }
    let d = Dirs { lex: args[1].clone().into(), packs: args[2].clone().into(), lm: args[3].clone().into() };
    let num = |i: usize| args.get(i).and_then(|s| s.parse::<usize>().ok()).unwrap_or_else(|| usage());
    say(&format!("load when=start load1={}", load1()));
    let pass = match args[0].as_str() {
        "typing" => typing(&d, num(4) as u32, num(5) as u32, num(6)),
        "build" => build(&d, num(4) as u32, num(5) as u32, num(6)),
        "memory" => {
            memory(&d, num(4) as u32, num(5) as u32, num(6), match args.get(7).map(String::as_str) {
                Some("shared") => true,
                Some("files") => false,
                _ => usage(),
            });
            true
        }
        "phases" => {
            phases(&d, num(4) as u32, num(5));
            true
        }
        _ => usage(),
    };
    say(&format!("load when=end load1={}", load1()));
    std::process::exit(if pass { 0 } else { 1 });
}
