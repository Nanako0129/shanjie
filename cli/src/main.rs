use core::engine::load_lexicon;
use core::eval::*;
use core::learn::{context_key, SENTINEL};
use core::lm::{decode_from, history, CappedLexicon, Demote, Lm, Profile};
use core::predict::{parse_units, predict, units_of, units_str, Index, Mode};
use core::{decode_beam, Error, Lexicon, NoLearning, Syls, BEAM_S1};
use std::fs;
use std::io::Write;
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
        // S2r: dev302 + typing76 rows with 一/不 typed in the other standard reading (experiments/s2/build_probe.py)
        "probe" => ("探針集", dir_rows("eval/probe")?),
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

/// lm_eval.py `rows_of`: only lines with exactly three `|` fields (context|sentence|reading).
/// Each row is (sentence, reading, context text).
fn three_field_rows(text: &str) -> Vec<(String, Syls, String)> {
    text.lines()
        .filter_map(|l| {
            let p: Vec<&str> = l.split('|').collect();
            (p.len() == 3).then(|| (p[1].to_string(), p[2].split_whitespace().map(String::from).collect(), p[0].to_string()))
        })
        .collect()
}

/// S2c LM mode (docs/PLAN.md S2c): the one summary line of lm_eval.py, plus the optional `--dump`.
fn run_lm(args: &[String], len: &Lenient) -> Result<(), String> {
    let (mut lm_path, mut profile, mut name, mut dev, mut rows_file) = (None, None, None, None, None);
    let (mut limit, mut set, mut dump, mut ctx_mode, mut demote) = (None::<usize>, None, None, false, true);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut val = || it.next().cloned().ok_or_else(|| "missing option value".to_string());
        let num = |v: String| v.parse::<usize>().map_err(|_| format!("bad number (length {})", v.chars().count()));
        match a.as_str() {
            "--lm" => lm_path = Some(val()?),
            "--profile" => profile = Some(val()?),
            "--name" => name = Some(val()?),
            "--dev" => dev = Some(num(val()?)?),
            "--rows" => rows_file = Some(val()?),
            "--limit" => limit = Some(num(val()?)?),
            "--set" => set = Some(val()?),
            "--dump" => dump = Some(val()?),
            "--context" => ctx_mode = true,
            "--no-demote" => demote = false,
            _ => return Err("unknown argument".into()),
        }
    }
    let profile_name = profile.ok_or("--profile is required")?;
    let prof = match profile_name.as_str() {
        "chat" => Profile::Chat,
        "formal" => Profile::Formal,
        _ => return Err("--profile must be chat or formal".into()),
    };
    let lm = Lm::load(std::path::Path::new(&lm_path.ok_or("--lm is required")?)).map_err(|e| e.to_string())?;
    let dir = root().join("data/lexicon");
    let lex = load_lexicon(&dir).map_err(|_| "cannot load lexicon".to_string())?;
    let overlay = fs::read_to_string(dir.join("overlay-add.tsv")).map_err(|e| format!("cannot read overlay ({:?})", e.kind()))?;
    let demote_rows = fs::read_to_string(dir.join("demote.tsv")).map_err(|e| format!("cannot read demote.tsv ({:?})", e.kind()))?;
    let table = Demote::parse(&demote_rows).filter(|d| d.check(&lex)).ok_or("bad demote.tsv")?;
    // The table is always loaded, so a malformed one stops the run even with --no-demote.
    let capped = CappedLexicon::new(lex.clone(), &overlay, &lm, Some(&table)).ok_or("bad demote.tsv")?;

    let read = |p: &Path| fs::read_to_string(p).map_err(|e| format!("cannot read file ({:?})", e.kind()));
    // (display name, [(truth, reading)])
    let (default_name, rows): (String, Vec<(String, Syls, String)>) = match (dev, rows_file, set.as_deref()) {
        (Some(n), None, None) => {
            let mut files: Vec<PathBuf> = fs::read_dir(root().join("eval/dev"))
                .map_err(|e| format!("cannot read directory ({:?})", e.kind()))?
                .filter_map(|e| e.ok().map(|e| e.path()))
                .filter(|p| p.extension().is_some_and(|x| x == "txt"))
                .collect();
            files.sort();
            let mut rows = Vec::new();
            for f in files {
                rows.extend(three_field_rows(&read(&f)?));
            }
            rows.truncate(n);
            (format!("dev{n}"), rows)
        }
        (None, Some(f), None) => {
            let mut rows = three_field_rows(&read(Path::new(&f))?);
            if let Some(n) = limit.filter(|&n| n != 0) {
                rows.truncate(n);
            }
            (Path::new(&f).file_name().map_or(String::new(), |s| s.to_string_lossy().into_owned()), rows)
        }
        (None, None, Some("holdout")) => {
            // Summary line only: the sentences must never reach the output.
            if dump.is_some() {
                return Err("--dump is not allowed with --set holdout".into());
            }
            let (_, rows) = load_set(&lex, "holdout")?;
            let rows = rows
                .iter()
                .map(|r| Ok((r.sent.clone(), row_syllables(&lex, r).map_err(|e| e.to_string())?, r.ctx.clone())))
                .collect::<Result<Vec<_>, String>>()?;
            ("holdout".to_string(), rows)
        }
        _ => return Err("need exactly one of --dev, --rows, --set holdout".into()),
    };
    let name = name.unwrap_or(default_name);
    let mut dump = match dump {
        Some(f) => Some(std::io::BufWriter::new(fs::File::create(f).map_err(|e| format!("cannot create dump ({:?})", e.kind()))?)),
        None => None,
    };
    let (mut top1, mut o64, mut firsts) = (0usize, 0usize, Vec::new());
    for (i, (truth, syls, ctx)) in rows.iter().enumerate() {
        // S2h: the first word is conditioned on the row's context, cut like the engine cuts it.
        let left = if ctx_mode { Some(context_key(ctx)) } else { None };
        let start = left.as_deref().filter(|k| *k != SENTINEL).map_or("<s>", |k| history(k, &lm));
        let nb = decode_from(&capped, syls, &lm, prof, BEAM_S1, start, demote).map_err(|e| e.to_string())?;
        let mut surf: Vec<String> = nb.iter().map(|(_, ws)| ws.concat()).collect();
        let t = len.apply(truth);
        top1 += (len.apply(&surf[0]) == t) as usize;
        o64 += surf.iter().any(|s| len.apply(s) == t) as usize;
        if let Some(d) = dump.as_mut() {
            for (r, ((sc, _), s)) in nb.iter().zip(&surf).enumerate() {
                writeln!(d, "{}\t{}\t{s}\t{sc:?}", i + 1, r + 1).map_err(|_| "cannot write dump".to_string())?;
            }
        }
        firsts.push(surf.swap_remove(0));
    }
    if let Some(mut d) = dump {
        d.flush().map_err(|_| "cannot write dump".to_string())?;
    }
    let sha = sha256_hex(firsts.join("\n").as_bytes());
    println!("## {name}  lm-{profile_name}{}{}  {{'n': {}, 'top1': {top1}, 'oracle@64': {o64}, 'top1_sha256': '{sha}'}}", if ctx_mode { "+ctx" } else { "" }, if demote { "" } else { "-nodemote" }, rows.len());
    Ok(())
}

/// S-bench 7.1: the unigram path over a `前文|句子|讀音` rows file, same output as lm_eval.py's dump and summary
/// (reference/proto/unigram_eval.py): top 64 per row as `列號\t名次\tsurface\t分數`, summary profile `unigram`.
fn run_unigram_rows(lex: &Lexicon, len: &Lenient, rows_file: String, dump: Option<String>) -> Result<(), String> {
    let text = fs::read_to_string(&rows_file).map_err(|e| format!("cannot read file ({:?})", e.kind()))?;
    let rows = three_field_rows(&text);
    let mut dump = match dump {
        Some(f) => Some(std::io::BufWriter::new(fs::File::create(f).map_err(|e| format!("cannot create dump ({:?})", e.kind()))?)),
        None => None,
    };
    let (mut top1, mut o64, mut firsts) = (0usize, 0usize, Vec::new());
    for (i, (truth, syls, _)) in rows.iter().enumerate() {
        let nb = decode_beam(lex, syls, &mut NoLearning, BEAM_S1).map_err(|e| e.to_string())?;
        let surf: Vec<String> = nb.iter().take(64).map(|(_, ws)| ws.concat()).collect();
        let first = surf.first().ok_or("no candidates for a row")?.clone();
        let t = len.apply(truth);
        top1 += (len.apply(&first) == t) as usize;
        o64 += surf.iter().any(|s| len.apply(s) == t) as usize;
        if let Some(d) = dump.as_mut() {
            for (r, ((sc, _), s)) in nb.iter().zip(&surf).enumerate() {
                writeln!(d, "{}\t{}\t{s}\t{sc:?}", i + 1, r + 1).map_err(|_| "cannot write dump".to_string())?;
            }
        }
        firsts.push(first);
    }
    if let Some(mut d) = dump {
        d.flush().map_err(|_| "cannot write dump".to_string())?;
    }
    let name = Path::new(&rows_file).file_stem().map_or(String::new(), |s| s.to_string_lossy().into_owned());
    let sha = sha256_hex(firsts.join("\n").as_bytes());
    println!("## {name}  unigram  {{'n': {}, 'top1': {top1}, 'oracle@64': {o64}, 'top1_sha256': '{sha}'}}", rows.len());
    Ok(())
}

/// Resident set size in KiB (ps), for the index memory measurement.
fn rss_kb() -> Option<u64> {
    let out = std::process::Command::new("ps").args(["-o", "rss=", "-p", &std::process::id().to_string()]).output().ok()?;
    String::from_utf8(out.stdout).ok()?.trim().parse().ok()
}

/// V3 core (docs/contracts/v3-core-predict.md): `--predict <file> --lm <file> --profile chat|formal [--predict-time]`.
/// The file is eval/golden/sp-predict.txt (only its `## ` query lines are read); output has the same format.
/// `--predict-time` prints p50/p95/max of the predict calls (index build excluded) and the index cost on stderr
/// (RSS before and after one index, and after a second one).
fn run_predict(args: &[String]) -> Result<(), String> {
    let (mut file, mut lm_path, mut profile, mut time) = (None, None, None, false);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut val = || it.next().cloned().ok_or_else(|| "missing option value".to_string());
        match a.as_str() {
            "--predict" => file = Some(val()?),
            "--lm" => lm_path = Some(val()?),
            "--profile" => profile = Some(val()?),
            "--predict-time" => time = true,
            _ => return Err("unknown argument".into()),
        }
    }
    let lam = match profile.ok_or("--profile is required")?.as_str() {
        "chat" => Profile::Chat,
        "formal" => Profile::Formal,
        _ => return Err("--profile must be chat or formal".into()),
    }
    .lambda();
    let lm = Lm::load(Path::new(&lm_path.ok_or("--lm is required")?)).map_err(|e| e.to_string())?;
    let dir = root().join("data/lexicon");
    let lex = load_lexicon(&dir).map_err(|_| "cannot load lexicon".to_string())?;
    let overlay = fs::read_to_string(dir.join("overlay-add.tsv")).map_err(|e| format!("cannot read overlay ({:?})", e.kind()))?;
    // Prediction scores use the capped lp only; demotion applies to decoding (sw §3).
    let capped = CappedLexicon::new(lex.clone(), &overlay, &lm, None).ok_or("cannot build the capped lexicon")?;
    let text = fs::read_to_string(file.ok_or("--predict is required")?).map_err(|e| format!("cannot read file ({:?})", e.kind()))?;
    let (rss0, t_idx) = (rss_kb(), Instant::now());
    let idx = Index::new(&capped, &lm);
    let (idx_time, rss1) = (t_idx.elapsed(), rss_kb());
    let (mut out, mut lat) = (String::new(), Vec::new());
    for line in text.lines().filter_map(|l| l.strip_prefix("## ")) {
        let f: Vec<&str> = line.split('\t').collect();
        let [mode, v, keys, units] = f[..] else { return Err("bad query line".into()) };
        let mode = match mode {
            "P" => Mode::P,
            "PA" => Mode::PA,
            _ => return Err("bad mode".into()),
        };
        let u = parse_units(units).ok_or("bad unit sequence")?;
        if keys != "-" && units_str(&units_of(keys)) != units {
            return Err("units_of differs from the query's unit sequence".into());
        }
        let t = Instant::now();
        let cands = predict(&idx, &lm, lam, v, &u, mode, 9);
        lat.push(t.elapsed());
        out += &format!("## {}\t{v}\t{keys}\t{units}\n", if mode == Mode::P { "P" } else { "PA" });
        for (w, s, succ, _) in cands {
            out += &format!("{w}\t{s:?}\t{}\n", succ as u8);
        }
    }
    std::io::stdout().write_all(out.as_bytes()).map_err(|_| "cannot write output".to_string())?;
    if time {
        // A second index shows what one index keeps resident once the build temporaries are freed.
        let second = Index::new(&capped, &lm);
        eprintln!("rss after the second index: {:?} KiB", rss_kb());
        drop(second);
    }
    if time && !lat.is_empty() {
        lat.sort();
        let ms = |d: Duration| d.as_secs_f64() * 1000.0;
        eprintln!(
            "predict: n={} p50={:.3} ms p95={:.3} ms max={:.3} ms; index build {:.0} ms, rss {:?} -> {:?} KiB",
            lat.len(),
            ms(lat[lat.len() / 2]),
            ms(lat[(lat.len() as f64 * 0.95) as usize]),
            ms(lat[lat.len() - 1]),
            ms(idx_time),
            rss0,
            rss1
        );
    }
    Ok(())
}

fn run() -> Result<(), String> {
    let len = load_lenient()?;
    let all: Vec<String> = std::env::args().skip(1).collect();
    if all.iter().any(|a| a == "--predict") {
        return run_predict(&all);
    }
    if all.iter().any(|a| a == "--lm") {
        return run_lm(&all, &len);
    }
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
    // S-bench 7.1: `--rows <file>` / `--dump <file>` switch the unigram path to row mode (see run_unigram_rows).
    let (mut rows_file, mut dump_file) = (None::<String>, None::<String>);
    let mut rest = Vec::new();
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--rows" => rows_file = Some(it.next().ok_or("missing option value")?),
            "--dump" => dump_file = Some(it.next().ok_or("missing option value")?),
            _ => rest.push(a),
        }
    }
    let (mut sets, mut learn, mut check, mut bench) = (Vec::new(), false, false, false);
    let (mut in_set, mut no_overlay, mut limit, mut in_limit) = (false, false, None::<usize>, false);
    for a in rest {
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
    let row_mode = rows_file.is_some() || dump_file.is_some();
    if sets.is_empty() && !row_mode {
        sets = ["trap", "daily", "moedict"].map(String::from).to_vec();
    }
    // The shipped lexicon is the engine's load_lexicon (base + overlay-add.tsv + sandhi-add.tsv, S2r);
    // --no-overlay parses the base alone. load_ms includes reading the files in both cases.
    let t_load = Instant::now();
    let lex = if no_overlay {
        let text = fs::read_to_string(root().join("data/lexicon/mcbpmf-data.txt"))
            .map_err(|e| format!("cannot read lexicon ({:?})", e.kind()))?;
        std::sync::Arc::new(Lexicon::parse_with(&text, None).map_err(|e: Error| e.to_string())?)
    } else {
        load_lexicon(&root().join("data/lexicon")).map_err(|_| "cannot load lexicon".to_string())?
    };
    let load_time = t_load.elapsed();

    if row_mode {
        if !(sets.is_empty() && !learn && !check && !bench && limit.is_none()) {
            return Err("--rows/--dump cannot be combined with --set, --limit, --learn-sim, --check-readings or --bench".into());
        }
        return run_unigram_rows(&lex, &len, rows_file.ok_or("--dump needs --rows")?, dump_file);
    }

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
