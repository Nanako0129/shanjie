//! tinylm_b1_probe: the SEC-12 check of the B1 purgeable slabs (docs/spec/semantic-rerank.md §2.4), run inside a signed throwaway bundle with the
//! shipping App Sandbox entitlements and the Hardened Runtime, so that what is checked is what the input method gets: that `vm_allocate` with
//! `VM_FLAGS_PURGABLE` and `vm_purgable_control` work there (no silent fallback to the portable path) and that the restored matrices leave
//! phys_footprint between uses. No Sparkle, no engine: the core's public `TinyLm` API only. macOS only; on every other target `main` is empty (so
//! `cargo check --examples` passes for Linux, Windows and iOS). CI builds it and does not run it.
//!
//! Modes (exit 0 only when every check passes; each check prints one `tinylm-b1 probe:` line):
//!   --b1-probe [--sjtl <file>] [--container-id <id>] [--pause-secs <n>]
//!       1. the environment: HOME is `.../Library/Containers/<id>/Data` (the sandbox is on; default id com.nyanako.inputmethod.shanjie.probe-itemc)
//!          and this process's code signature has the Hardened Runtime flag (`csops`, CS_RUNTIME);
//!       2. the footprint before loading (base), after `TinyLm::load` (loaded), its peak while 2 rounds of 200 fixed pairs are scored on the default
//!          (Accelerate) path, sampled every millisecond (peak), and after them (idle): loaded - base <= 5.0 MiB, idle - base <= 5.0 MiB,
//!          peak - base <= 30.0 MiB;
//!       3. `TinyLm::purgeable_stats()`: 33 slabs and `PurgeableStats::check` (no fallback, every slab allocated, pinned, made volatile, and
//!          volatile now);
//!       4. no file appeared under `$HOME/Library/Caches` while it ran.
//!       The model defaults to `../Resources/tinylm.sjtl` beside the executable (the bundle layout). `--pause-secs` sleeps before exiting, after
//!       printing the pid, for an outside `/usr/bin/footprint <pid>`.
//!   --b1-probe --no-env-check ...   the same measurements outside a bundle, without step 1 and 4; always exits 3 (never a SEC-12 pass).
//!   --self-check-fail-open          judges synthetic stats with one fallback with the same function: must exit non-zero (the judge cannot pass
//!                                   a fallback).

#[cfg(target_os = "macos")]
mod probe {
    use core::tinylm::{PurgeableStats, TinyLm};
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::Arc;
    use std::time::Duration;

    const PREFIX: &str = "tinylm-b1 probe:";
    const DEFAULT_ID: &str = "com.nyanako.inputmethod.shanjie.probe-itemc";
    /// The shipped model: the embedding and 4 matrices per layer, 8 layers.
    const SLABS: usize = 33;
    const MAX_LOADED_MIB: f64 = 5.0;
    const MAX_IDLE_MIB: f64 = 5.0;
    const MAX_PEAK_MIB: f64 = 30.0;
    const PAIRS: usize = 200;
    const ROUNDS: usize = 2;
    /// <sys/codesign.h> (xnu): `csops` operation and status flag.
    const CS_OPS_STATUS: u32 = 0;
    const CS_RUNTIME: u32 = 0x0001_0000;
    const CHARS: &str = "的一是不了人我在有他這中大來上國個到說們為子和你地出道也時年得就那要下以生會自著去之過家學對可她裡後小麼心多天而能好都然沒日於起還發成事只作當想看文無開手十用主行方又如前所本見經頭面公同三已老從動兩長知民樣現分將外但身些與高意進把法此實回二理美點月明其種聲全工己話兒者向情部正名定女問力機給等幾很業最間新什打便位因重被走電四第門相次東政海口使教西再平真聽世氣信北少關";

    extern "C" {
        fn proc_pid_rusage(pid: i32, flavor: i32, buffer: *mut u64) -> i32;
        fn getpid() -> i32;
        fn csops(pid: i32, ops: u32, useraddr: *mut u32, usersize: usize) -> i32;
        fn malloc_zone_pressure_relief(zone: *mut std::ffi::c_void, goal: usize) -> usize;
        fn mstats() -> MStats;
    }

    /// <malloc/malloc.h> `struct mstats`.
    #[repr(C)]
    struct MStats {
        bytes_total: usize,
        chunks_used: usize,
        bytes_used: usize,
        chunks_free: usize,
        bytes_free: usize,
    }

    fn heap() -> MStats {
        // SAFETY: returns a struct of counters by value; no pointer is involved.
        unsafe { mstats() }
    }

    /// phys_footprint in bytes (`rusage_info_v2`: 16 bytes of uuid, then u64 fields; the footprint is the 8th).
    fn footprint() -> u64 {
        let mut b = [0u64; 64];
        // SAFETY: flavor 2 fills a `rusage_info_v2`, which fits in 64 u64.
        let rc = unsafe { proc_pid_rusage(getpid(), 2, b.as_mut_ptr()) };
        assert_eq!(rc, 0, "proc_pid_rusage");
        b[9]
    }

    fn mib(b: u64) -> f64 {
        b as f64 / 1_048_576.0
    }

    fn pairs() -> Vec<(String, String)> {
        let chars: Vec<char> = CHARS.chars().collect();
        let mut st = 0x5eed_b1b1_0000_0001u64;
        let mut next = |n: usize| {
            st = st.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            ((st >> 33) % n as u64) as usize
        };
        (0..PAIRS)
            .map(|_| {
                let (kl, cl) = (next(41), 1 + next(8));
                ((0..kl).map(|_| chars[next(chars.len())]).collect(), (0..cl).map(|_| chars[next(chars.len())]).collect())
            })
            .collect()
    }

    /// Every path under `dir` (empty when it does not exist).
    fn tree(dir: &Path) -> Vec<PathBuf> {
        let mut out = Vec::new();
        let mut todo = vec![dir.to_path_buf()];
        while let Some(d) = todo.pop() {
            let Ok(rd) = std::fs::read_dir(&d) else { continue };
            for e in rd.flatten() {
                let p = e.path();
                if e.file_type().is_ok_and(|t| t.is_dir()) {
                    todo.push(p.clone());
                }
                out.push(p);
            }
        }
        out.sort();
        out
    }

    /// One check: prints it and returns whether it passed.
    fn report(name: &str, ok: bool, detail: &str) -> bool {
        println!("{PREFIX} check={name} {detail} result={}", if ok { "ok" } else { "FAIL" });
        ok
    }

    /// The SEC-12 verdict on the stats: the slab count of the shipped model and `PurgeableStats::check`.
    fn judge(s: &PurgeableStats) -> bool {
        let verdict = if s.slab_count != SLABS { Err("slab_count") } else { s.check() };
        report(
            "purgeable",
            verdict.is_ok(),
            &format!(
                "slab_count={} alloc_ok={} pin_ok={} volatile_ok={} empty_rebuilds={} fallbacks={} currently_volatile={} failed={}",
                s.slab_count,
                s.alloc_ok,
                s.pin_ok,
                s.volatile_ok,
                s.empty_rebuilds,
                s.fallbacks,
                s.currently_volatile,
                verdict.err().unwrap_or("-")
            ),
        )
    }

    fn arg_after(args: &[String], flag: &str) -> Option<String> {
        args.iter().position(|a| a == flag).and_then(|i| args.get(i + 1)).cloned()
    }

    pub fn main() -> i32 {
        let args: Vec<String> = std::env::args().collect();
        if args.iter().any(|a| a == "--self-check-fail-open") {
            let synthetic = PurgeableStats {
                slab_count: SLABS,
                alloc_ok: SLABS,
                pin_ok: 2 * SLABS,
                volatile_ok: 2 * SLABS,
                empty_rebuilds: 0,
                fallbacks: 1,
                currently_volatile: SLABS,
            };
            return if judge(&synthetic) { 0 } else { 1 };
        }
        if !args.iter().any(|a| a == "--b1-probe") {
            eprintln!("usage: tinylm_b1_probe --b1-probe [--sjtl <file>] [--container-id <id>] [--pause-secs <n>] [--no-env-check] | --self-check-fail-open");
            return 2;
        }
        let env_check = !args.iter().any(|a| a == "--no-env-check");
        let id = arg_after(&args, "--container-id").unwrap_or_else(|| DEFAULT_ID.to_string());
        let sjtl = match arg_after(&args, "--sjtl") {
            Some(p) => PathBuf::from(p),
            None => {
                let exe = std::env::current_exe().expect("current_exe");
                exe.parent().expect("executable folder").join("../Resources/tinylm.sjtl")
            }
        };
        let pause: u64 = arg_after(&args, "--pause-secs").map(|s| s.parse().expect("--pause-secs")).unwrap_or(0);
        let mut ok = true;

        let home = PathBuf::from(std::env::var_os("HOME").unwrap_or_default());
        let caches = home.join("Library/Caches");
        if env_check {
            let want = format!("/Library/Containers/{id}/Data");
            ok &= report("sandbox", home.to_string_lossy().ends_with(&want), &format!("home_in_container={}", home.to_string_lossy().ends_with(&want)));
            let mut flags = 0u32;
            // SAFETY: CS_OPS_STATUS writes one u32 of status flags for this process into `flags`.
            let rc = unsafe { csops(getpid(), CS_OPS_STATUS, &mut flags, std::mem::size_of::<u32>()) };
            ok &= report("hardened_runtime", rc == 0 && flags & CS_RUNTIME != 0, &format!("csops_rc={rc} cs_flags=0x{flags:08x}"));
        }
        let caches_before = tree(&caches);

        let heap0 = heap();
        let base = footprint();
        let model = match TinyLm::load(&sjtl) {
            Ok(m) => m,
            Err(e) => {
                report("load", false, &format!("error={e:?}"));
                return 1;
            }
        };
        let loaded = footprint();
        let owned = pairs();
        let items: Vec<(&str, &str)> = owned.iter().map(|(k, c)| (k.as_str(), c.as_str())).collect();
        let (stop, peak) = (Arc::new(AtomicBool::new(false)), Arc::new(AtomicU64::new(loaded)));
        let sampler = {
            let (stop, peak) = (stop.clone(), peak.clone());
            std::thread::spawn(move || {
                while !stop.load(Ordering::Relaxed) {
                    peak.fetch_max(footprint(), Ordering::Relaxed);
                    std::thread::sleep(Duration::from_millis(1));
                }
            })
        };
        // round 1 is cold (every slab restored); round 2 is warm. `peak` covers both; the round-2 numbers are reported apart (below).
        let (mut sums, mut pre2, mut peak2) = (Vec::new(), 0u64, 0u64);
        for r in 0..ROUNDS {
            if r == ROUNDS - 1 {
                pre2 = footprint();
                peak2 = peak.swap(pre2, Ordering::Relaxed);
            }
            let ll = model.ll_many(&items).expect("ll_many");
            peak.fetch_max(footprint(), Ordering::Relaxed);
            sums.push(ll.iter().sum::<f64>());
        }
        stop.store(true, Ordering::Relaxed);
        sampler.join().expect("sampler");
        let (peak1, last) = (peak2, peak.load(Ordering::Relaxed));
        let peak = peak1.max(last);
        let idle = footprint();
        // SAFETY: NULL asks every malloc zone to return its free pages; nothing is freed that is in use.
        unsafe { malloc_zone_pressure_relief(std::ptr::null_mut(), 0) };
        let relieved = footprint();
        let d = |x: u64| mib(x.saturating_sub(base));
        println!(
            "{PREFIX} base_mib={:.1} loaded_mib={:.1} peak_mib={:.1} idle_mib={:.1} rounds={ROUNDS} pairs={PAIRS} sums_equal={}",
            mib(base),
            mib(loaded),
            mib(peak),
            mib(idle),
            sums.windows(2).all(|w| w[0] == w[1])
        );
        // Diagnostics only (not in the verdict), to tell the model's memory from the allocator's: the live heap the run added (malloc's bytes in
        // use, after minus before loading: the model's small tensors and tables) against the heap malloc holds free (the forward pass's freed
        // temporaries it keeps), the footprint after malloc_zone_pressure_relief, and how far the warm round rose above the footprint it started
        // from (the one pinned matrix plus that round's temporaries).
        let heap1 = heap();
        println!(
            "{PREFIX} diag live_heap_delta_mib={:.2} heap_total_delta_mib={:.2} heap_free_mib={:.2} relieved_delta_mib={:.2} cold_peak_delta_mib={:.2} warm_peak_over_start_mib={:.2} chunks_used={} chunks_free={}",
            (heap1.bytes_used as f64 - heap0.bytes_used as f64) / 1_048_576.0,
            (heap1.bytes_total as f64 - heap0.bytes_total as f64) / 1_048_576.0,
            mib(heap1.bytes_free as u64),
            d(relieved),
            d(peak1),
            mib(last.saturating_sub(pre2)),
            heap1.chunks_used,
            heap1.chunks_free
        );
        ok &= report("loaded", d(loaded) <= MAX_LOADED_MIB, &format!("delta_mib={:.2} max={MAX_LOADED_MIB}", d(loaded)));
        ok &= report("idle", d(idle) <= MAX_IDLE_MIB, &format!("delta_mib={:.2} max={MAX_IDLE_MIB}", d(idle)));
        ok &= report("peak", d(peak) <= MAX_PEAK_MIB, &format!("delta_mib={:.2} max={MAX_PEAK_MIB}", d(peak)));
        ok &= judge(&model.purgeable_stats());
        if env_check {
            let added: Vec<_> = tree(&caches).into_iter().filter(|p| !caches_before.contains(p)).collect();
            ok &= report("caches", added.is_empty(), &format!("new_entries={}", added.len()));
        }
        if pause > 0 {
            println!("{PREFIX} pid={} pausing {pause} s", std::process::id());
            std::thread::sleep(Duration::from_secs(pause));
        }
        drop(model);
        if !env_check {
            println!("{PREFIX} environment not checked: not a SEC-12 result");
            return 3;
        }
        println!("{PREFIX} verdict={}", if ok { "PASS" } else { "FAIL" });
        if ok {
            0
        } else {
            1
        }
    }
}

fn main() {
    #[cfg(target_os = "macos")]
    std::process::exit(probe::main());
}
