use core::eval::*;
use core::{Error, Lexicon};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

/// (display name, rows). Messages never include row text (R2).
fn load_set(lex: &Lexicon, name: &str) -> Result<(&'static str, Vec<Row>), String> {
    let root = root();
    let read = |p: &Path| fs::read_to_string(p).map_err(|e| format!("cannot read file ({:?})", e.kind()));
    let dir_rows = |d: &str| -> Result<Vec<Row>, String> {
        let mut files: Vec<PathBuf> = fs::read_dir(root.join(d))
            .map_err(|e| format!("cannot read directory ({:?})", e.kind()))?
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.extension().is_some_and(|x| x == "txt"))
            .collect();
        files.sort();
        let mut rows = Vec::new();
        for f in files {
            rows.extend(parse_rows(&read(&f)?).map_err(|e| e.to_string())?);
        }
        Ok(rows)
    };
    let sets = root.join("eval/sets");
    Ok(match name {
        "trap" => ("同音陷阱集", parse_rows(&read(&sets.join("trap.txt"))?).map_err(|e| e.to_string())?),
        "daily" => ("日常驗證集", parse_rows(&read(&sets.join("daily.txt"))?).map_err(|e| e.to_string())?),
        "moedict" => ("萌典例句", parse_moedict(&read(&sets.join("moedict.txt"))?)),
        "dev" => ("開發集", dir_rows("eval/dev")?),
        "holdout" => ("保留集", dir_rows("eval/holdout")?),
        _ => return Err(format!("unknown set name (length {})", name.chars().count())),
    })
    .map(|(n, r)| (n, usable(lex, r)))
}

fn run() -> Result<(), String> {
    let (mut sets, mut learn, mut check, mut bench) = (Vec::new(), false, false, false);
    let mut in_set = false;
    for a in std::env::args().skip(1) {
        match a.as_str() {
            "--set" => in_set = true,
            "--learn-sim" => (learn, in_set) = (true, false),
            "--check-readings" => (check, in_set) = (true, false),
            "--bench" => (bench, in_set) = (true, false),
            _ if in_set && !a.starts_with("--") => sets.push(a),
            _ => return Err("unknown argument".into()),
        }
    }
    if sets.is_empty() {
        sets = ["trap", "daily", "moedict"].map(String::from).to_vec();
    }
    let text = fs::read_to_string(root().join("data/lexicon/mcbpmf-data.txt"))
        .map_err(|e| format!("cannot read lexicon ({:?})", e.kind()))?;
    let lex = Lexicon::parse(&text).map_err(|e: Error| e.to_string())?;

    let mut latency: Vec<Duration> = Vec::new();
    let mut loaded = Vec::new();
    for s in &sets {
        let (name, rows) = load_set(&lex, s)?;
        if rows.is_empty() {
            eprintln!("set is empty, skipped (length {})", s.chars().count());
            continue;
        }
        let (m, misses) = evaluate(&lex, &rows, |d| latency.push(d)).map_err(|e| e.to_string())?;
        println!("\n## {name}  unigram  {}", m.repr());
        // 保留集只印指標：錯句會把內容露給調整系統的人（PLAN 片 E 保留集規則）
        if name != "萌典例句" && name != "保留集" {
            for (t, o) in misses {
                println!("   ✗ {t} → {o}");
            }
        }
        loaded.push((name, rows));
    }
    if learn {
        print!("{}", simulate(&lex).map_err(|e| e.to_string())?);
    }
    if check {
        for (name, rows) in &loaded {
            let (n, bad) = check_readings(&lex, rows);
            if n > 0 {
                println!("## 讀音檢查  {name}  {{'rows': {n}, 'mismatch': {bad}}}");
            }
        }
    }
    if bench && !latency.is_empty() {
        latency.sort();
        let ms = |d: Duration| d.as_secs_f64() * 1000.0;
        println!(
            "## 延遲  decode per sentence  {{'n': {}, 'p50_ms': {:.3}, 'p95_ms': {:.3}}}",
            latency.len(),
            ms(latency[latency.len() / 2]),
            ms(latency[(latency.len() as f64 * 0.95) as usize])
        );
    }
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        eprintln!("error: {e}");
        std::process::exit(1);
    }
}
