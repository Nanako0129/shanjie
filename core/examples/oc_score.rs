//! 0c measurement program (docs/contracts/oc-experimental.md section 2, A3): it scores `ITEMS.tsv` rows with `core::oc::Oc` and does
//! nothing else (no CLI, no engine). ITEMS.tsv is what `tools/oc_ref.py --export` writes: `row<TAB>prefix<TAB>syllables (space-separated)
//! <TAB>candidates (|-separated)`.
//!
//!   cargo run --release -p core --example oc_score -- --sjoc F.sjoc --items ITEMS.tsv --out SCORES.jsonl
//!       one line per row, `{"i": row, "ll": [log10 per candidate] or null}` (null: the row cannot be scored); for
//!       `tools/oc_ref.py --compare ... --rust SCORES.jsonl`
//!   cargo run --release -p core --example oc_score -- --sjoc F.sjoc --items ITEMS.tsv --latency [--no-context] [--tinylm M.sjtl]
//!       one thread, `score()` timed once per scorable row, 3 rounds; p50 and p95 per round and the median of the 3 (p95 = element
//!       int(n * 0.95) of the sorted times, as the CLI); with --tinylm the same rows' M scoring (`ll_many` of the row's prefix and
//!       candidates) is timed the same way; --no-context replaces every prefix by the empty string
//!   cargo run --release -p core --example oc_score -- --sjoc F.sjoc --items ITEMS.tsv --mem --tinylm M.sjtl     (macOS only)
//!       resident_size and resident_size_max (MACH_TASK_BASIC_INFO) and phys_footprint (TASK_VM_INFO) at the start, after loading 0c and
//!       scoring every scorable row, after also loading M and scoring every one of those rows with M; the 0c, combined and peak RSS
//!       increases, and the 250 MiB gate on the peak increase
//!   Flags that do not belong to the chosen mode (--no-context with --out/--mem, --tinylm with --out) are an error (exit 2); so is a
//!   non-finite score (the row number is named, nothing more is written).
use core::oc::Oc;
use core::tinylm::TinyLm;
use std::path::Path;
use std::time::Instant;

struct Item {
    row: u64,
    prefix: String,
    syllables: Vec<String>,
    cands: Vec<String>,
}

fn read_items(path: &str) -> Vec<Item> {
    let text = std::fs::read_to_string(path).unwrap_or_else(|_| die(&format!("cannot read {path}")));
    text.lines()
        .map(|l| {
            let f: Vec<&str> = l.split('\t').collect();
            if f.len() != 4 {
                die(&format!("{path}: a row does not have 4 tab-separated fields"));
            }
            let row = f[0].parse().unwrap_or_else(|_| die(&format!("{path}: row number {:?}", f[0])));
            Item { row, prefix: f[1].to_string(), syllables: f[2].split(' ').map(String::from).collect(), cands: f[3].split('|').map(String::from).collect() }
        })
        .collect()
}

fn die(msg: &str) -> ! {
    eprintln!("oc_score: {msg}");
    std::process::exit(2)
}

fn refs(v: &[String]) -> Vec<&str> {
    v.iter().map(String::as_str).collect()
}

/// A non-finite score is fatal here (it must never reach SCORES.jsonl or a timing).
fn score(oc: &Oc, it: &Item, prefix: &str) -> Vec<Option<f64>> {
    oc.try_score(prefix, &refs(&it.syllables), &refs(&it.cands)).unwrap_or_else(|e| die(&format!("row {}: {e}", it.row)))
}

/// M's scoring of one row: the row's prefix is k (M drops what does not fit), every candidate is c.
fn score_m(m: &TinyLm, it: &Item, prefix: &str) {
    let pairs: Vec<(&str, &str)> = it.cands.iter().map(|c| (prefix, c.as_str())).collect();
    // an error (a candidate longer than M's context) is not timed work, only the call is
    let _ = std::hint::black_box(m.ll_many(std::hint::black_box(&pairs)));
}

/// Per round: the sorted per-row times (ms) -> (p50, p95).
fn rounds(items: &[&Item], mut f: impl FnMut(&Item)) -> (Vec<f64>, Vec<f64>) {
    let (mut p50, mut p95) = (Vec::new(), Vec::new());
    for _ in 0..3 {
        let mut ms: Vec<f64> = items
            .iter()
            .map(|it| {
                let t = Instant::now();
                f(it);
                t.elapsed().as_secs_f64() * 1000.0
            })
            .collect();
        ms.sort_by(|a, b| a.partial_cmp(b).unwrap());
        p50.push(ms[ms.len() / 2]);
        p95.push(ms[(ms.len() as f64 * 0.95) as usize]);
    }
    (p50, p95)
}

fn report(name: &str, (p50, p95): (Vec<f64>, Vec<f64>)) {
    let median = |v: &[f64]| {
        let mut s = v.to_vec();
        s.sort_by(|a, b| a.partial_cmp(b).unwrap());
        s[1]
    };
    let show = |v: &[f64]| v.iter().map(|x| format!("{x:.2}")).collect::<Vec<_>>().join(", ");
    println!("{name}: rounds p50 [{}] p95 [{}] ms; median p50 {:.2} ms, p95 {:.2} ms", show(&p50), show(&p95), median(&p50), median(&p95));
}

#[cfg(target_os = "macos")]
mod mach {
    //! task_info (<mach/task_info.h>): the two numbers the contract's memory gate names, read by offset in an 8-byte-aligned buffer.
    //! mach_task_basic_info is { u64 virtual_size; u64 resident_size; u64 resident_size_max; ... }, so those are u64 index 1 and 2. task_vm_info is
    //! { u64 virtual_size; i32 region_count; i32 page_size; u64 resident_size; ... } with phys_footprint at byte 144 (u64 index 18);
    //! checked on this macOS by reading a 150 MiB file into the heap: both numbers rose by 150 MiB.
    extern "C" {
        static mach_task_self_: u32;
        fn task_info(task: u32, flavor: u32, info: *mut u64, count: *mut u32) -> i32;
    }
    const MACH_TASK_BASIC_INFO: u32 = 20;
    const TASK_VM_INFO: u32 = 22;

    fn info(flavor: u32) -> [u64; 64] {
        let mut buf = [0u64; 64];
        let mut count = 128u32; // in 4-byte units
        // SAFETY: `buf` is 512 bytes and `count` says so; the kernel writes at most that and updates `count`.
        let rc = unsafe { task_info(mach_task_self_, flavor, buf.as_mut_ptr(), &mut count) };
        assert_eq!(rc, 0, "task_info({flavor}) failed");
        buf
    }

    /// (resident_size, phys_footprint, resident_size_max) in bytes.
    pub fn now() -> (u64, u64, u64) {
        let b = info(MACH_TASK_BASIC_INFO);
        (b[1], info(TASK_VM_INFO)[18], b[2])
    }
}

#[cfg(target_os = "macos")]
fn mem(sjoc: &str, items: &[Item], tinylm: &str) {
    const MIB: f64 = (1 << 20) as f64;
    let p0 = mach::now();
    let oc = Oc::load(Path::new(sjoc)).unwrap_or_else(|_| die("cannot load the .sjoc"));
    // every row 0c can score (an unscorable row returns before the forward pass)
    let scorable: Vec<&Item> = items.iter().filter(|it| score(&oc, it, &it.prefix)[0].is_some()).collect();
    if scorable.is_empty() {
        die("no scorable row in the items");
    }
    let p1 = mach::now();
    let m = TinyLm::load(Path::new(tinylm)).unwrap_or_else(|_| die("cannot load the .sjtl"));
    scorable.iter().for_each(|it| score_m(&m, it, &it.prefix));
    let p2 = mach::now();
    let mib = |x: u64| x as f64 / MIB;
    println!("rows scored {} of {}", scorable.len(), items.len());
    println!("{:<30}{:>10}{:>20}{:>16}", "point", "RSS MiB", "phys_footprint MiB", "RSS max MiB");
    for (name, p) in [("start", p0), ("0c loaded, all rows scored", p1), ("M loaded, all rows scored", p2)] {
        println!("{name:<30}{:>10.1}{:>20.1}{:>16.1}", mib(p.0), mib(p.1), mib(p.2));
    }
    let (one, both, peak) = ((mib(p1.0) - mib(p0.0), mib(p1.1) - mib(p0.1)), (mib(p2.0) - mib(p0.0), mib(p2.1) - mib(p0.1)), mib(p2.2) - mib(p0.0));
    println!("0c alone:      RSS +{:.1} MiB, phys_footprint +{:.1} MiB", one.0, one.1);
    println!("0c and M:      RSS +{:.1} MiB, phys_footprint +{:.1} MiB", both.0, both.1);
    println!("peak:          RSS max {:.1} MiB = start RSS +{peak:.1} MiB", mib(p2.2));
    println!("gate (peak RSS increase <= 250 MiB): {}", if peak <= 250.0 { "within" } else { "EXCEEDED" });
}

#[cfg(not(target_os = "macos"))]
fn mem(_: &str, _: &[Item], _: &str) {
    die("--mem needs task_info: macOS only")
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let opt = |name: &str| args.iter().position(|a| a == name).map(|i| args.get(i + 1).cloned().unwrap_or_else(|| die(&format!("{name} needs a value"))));
    let flag = |name: &str| args.iter().any(|a| a == name);
    let known = ["--sjoc", "--items", "--out", "--latency", "--no-context", "--tinylm", "--mem"];
    for (i, a) in args.iter().enumerate() {
        let value_of_previous = i > 0 && ["--sjoc", "--items", "--out", "--tinylm"].contains(&args[i - 1].as_str());
        if !value_of_previous && !known.contains(&a.as_str()) {
            die(&format!("unknown argument {a}"));
        }
    }
    let (Some(sjoc), Some(items_path)) = (opt("--sjoc"), opt("--items")) else { die("--sjoc and --items are required") };
    let (out, latency, mem_mode) = (opt("--out"), flag("--latency"), flag("--mem"));
    if [out.is_some(), latency, mem_mode].iter().filter(|x| **x).count() != 1 {
        die("exactly one of --out FILE / --latency / --mem");
    }
    if flag("--no-context") && !latency {
        die("--no-context only goes with --latency");
    }
    if opt("--tinylm").is_some() && out.is_some() {
        die("--tinylm does not go with --out");
    }
    let items = read_items(&items_path);
    let tinylm = opt("--tinylm");
    if mem_mode {
        mem(&sjoc, &items, &tinylm.unwrap_or_else(|| die("--mem needs --tinylm")));
        return;
    }
    let oc = Oc::load(Path::new(&sjoc)).unwrap_or_else(|e| die(&e.to_string()));
    if let Some(out) = out {
        // all rows first: a non-finite score exits before the file is created
        let mut text = String::new();
        for it in &items {
            // `score` is all Some (finite, so valid JSON) or all None
            let ll = match score(&oc, it, &it.prefix).as_slice() {
                xs @ [Some(_), ..] => format!("[{}]", xs.iter().map(|x| x.unwrap().to_string()).collect::<Vec<_>>().join(", ")),
                _ => "null".to_string(),
            };
            text += &format!("{{\"i\": {}, \"ll\": {ll}}}\n", it.row);
        }
        std::fs::write(&out, text).unwrap_or_else(|_| die(&format!("cannot write {out}")));
        return;
    }
    let no_context = flag("--no-context");
    let ctx = |it: &Item| if no_context { String::new() } else { it.prefix.clone() };
    let timed: Vec<&Item> = items.iter().filter(|it| score(&oc, it, &ctx(it))[0].is_some()).collect();
    if timed.is_empty() {
        die("no scorable row in the items");
    }
    println!("timed rows {} of {} (scorable), context {}", timed.len(), items.len(), if no_context { "none" } else { "as in the items" });
    report("0c", rounds(&timed, |it| {
        std::hint::black_box(score(&oc, it, &ctx(it)));
    }));
    if let Some(path) = tinylm {
        let m = TinyLm::load(Path::new(&path)).unwrap_or_else(|_| die("cannot load the .sjtl"));
        report("M ", rounds(&timed, |it| score_m(&m, it, &ctx(it))));
    }
}
