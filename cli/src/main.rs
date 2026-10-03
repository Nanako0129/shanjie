use core::eval::*;
use core::{decode_beam, Error, Lexicon, NoLearning, BEAM_S1};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

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

fn oov_words(name: &str) -> Vec<String> {
    let p = root().join(format!("eval/{name}/oov_words.list"));
    fs::read_to_string(p).unwrap_or_default().lines().filter(|l| !l.is_empty()).map(String::from).collect()
}

/// S2v lenient table: SHANJIE_VARIANTS if set, else the repo's eval/variants.tsv; missing or bad is an error.
fn load_lenient() -> Result<Lenient, String> {
    // An empty value counts as unset, same as the Python reference (`os.environ.get(...) or default`).
    let p = std::env::var_os("SHANJIE_VARIANTS")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| root().join("eval/variants.tsv"));
    let text = fs::read_to_string(p).map_err(|e| format!("cannot read variants table ({:?})", e.kind()))?;
    Lenient::parse(&text).map_err(|e| e.to_string())
}

fn run() -> Result<(), String> {
    let len = load_lenient()?;
    let mut args = std::env::args().skip(1);
    if let (Some(a), Some(f), None) = (args.next(), args.next(), args.next()) {
        if a == "--lenient-dump" {
            // S2v parity check: one lenient(line) per input line, nothing else.
            let text = fs::read_to_string(f).map_err(|e| format!("cannot read file ({:?})", e.kind()))?;
            for line in text.lines() {
                println!("{}", len.apply(line));
            }
            return Ok(());
        }
    }
    let (mut sets, mut learn, mut check, mut bench) = (Vec::new(), false, false, false);
    let (mut in_set, mut no_overlay, mut limit, mut in_limit) = (false, false, None::<usize>, false);
    for a in std::env::args().skip(1) {
        match a.as_str() {
            "--set" => in_set = true,
            "--no-overlay" => (no_overlay, in_set, in_limit) = (true, false, false),
            "--limit" => (in_limit, in_set) = (true, false),
            _ if in_limit => {
                limit = Some(a.parse().map_err(|_| format!("bad --limit (length {})", a.chars().count()))?);
                in_limit = false;
            }
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
    let overlay = if no_overlay {
        None
    } else {
        Some(
            fs::read_to_string(root().join("data/lexicon/overlay-add.tsv"))
                .map_err(|e| format!("cannot read overlay ({:?})", e.kind()))?,
        )
    };
    let t_load = Instant::now();
    let lex = Lexicon::parse_with(&text, overlay.as_deref()).map_err(|e: Error| e.to_string())?;
    let load_time = t_load.elapsed();

    let mut loaded = Vec::new();
    for s in &sets {
        let (name, mut rows) = load_set(&lex, s)?;
        if s == "dev" {
            if let Some(n) = limit {
                rows.truncate(n);
            }
        }
        if rows.is_empty() {
            eprintln!("set is empty, skipped (length {})", s.chars().count());
            continue;
        }
        let (m, misses) = evaluate(&lex, &len, &rows, |_| {}).map_err(|e| e.to_string())?;
        println!("\n## {name}  unigram  {}", m.repr());
        // 保留集只印指標：錯句會把內容露給調整系統的人（PLAN 片 E 保留集規則）
        if name != "萌典例句" && name != "保留集" {
            for (t, o) in misses {
                println!("   ✗ {t} → {o}");
            }
        }
        if s == "dev" || s == "holdout" {
            let (extra, counts, miss) = s1_extra(&lex, &rows, &oov_words(s)).map_err(|e| e.to_string())?;
            println!("## {name}  extra  {extra}\n{counts}");
            if s == "dev" {
                for i in &miss {
                    println!("   oracle@64 miss: row {i}: {}", rows[i - 1].sent);
                }
            } else {
                println!("   oracle@64 miss rows: {miss:?}");
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
    if bench {
        // per-keystroke: decode every syllable prefix 1..n with the S1 path
        let mut lat: Vec<Duration> = Vec::new();
        for (_, rows) in &loaded {
            for r in rows {
                let syls = row_syllables(&lex, r).map_err(|e| e.to_string())?;
                for k in 1..=syls.len() {
                    let t = Instant::now();
                    decode_beam(&lex, &syls[..k], &mut NoLearning, BEAM_S1).map_err(|e| e.to_string())?;
                    lat.push(t.elapsed());
                }
            }
        }
        lat.sort();
        let ms = |d: Duration| d.as_secs_f64() * 1000.0;
        if !lat.is_empty() {
            println!(
                "## 延遲  per keystroke  {{'n': {}, 'p50_ms': {:.3}, 'p95_ms': {:.3}, 'load_ms': {:.1}}}",
                lat.len(),
                ms(lat[lat.len() / 2]),
                ms(lat[(lat.len() as f64 * 0.95) as usize]),
                ms(load_time)
            );
        }
    }
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        eprintln!("error: {e}");
        std::process::exit(1);
    }
}
