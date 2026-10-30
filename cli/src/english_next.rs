//! `--english-next ROWS --table FILE [--lm FILE]` (docs/contracts/english-next.md section 3.2): the engine, with and without the
//! table, on rows `e|w|reading` (reading: space-separated syllables, tools/readings.py). Each row starts from an empty composition and sends
//! Shift+(first letter of e), the other letters, Space, and reads the prediction row (i); then the first Zhuyin key of w's first syllable
//! (standard layout) and reads it again (ii, iii). Standard layout, English mixing on, prediction on, the rest as `replay::build_engine`
//! makes the App's engine (ACG and IT+AI packs, bigram, small model and colloquial table when the files exist, demotion and rerank on, no learning).
//! Both arms are the same engine built without the data directory's table; an untimed warm-up pass, then one timed pass without the table, then the table is loaded (`Engine::load_english_next`, an invalid table is an error) and one timed pass with it.
//!
//! Output, no row text (R2), one TSV line per profile (chat, formal), then the memory line:
//!   english-next  profile  rows  no_token  base_i base_ii base_iii  table_i table_ii table_iii  imp_i wor_i  imp_ii wor_ii  imp_iii wor_iii  p95_base_ms p95_table_ms
//!   rss_kib  before_table_load  after_table_load_and_one_pass
//! (i) w is in the row before any Zhuyin; (ii) w is in the row after the first Zhuyin key; (iii) w is its first item. `imp` counts the rows
//! the table got right that the baseline did not, `wor` the reverse (the inputs of the exact McNemar test). A row where the Space did not
//! leave exactly one English token counts as `no_token` and as a miss in both arms. p95 is over every key of the pass, single thread.
use core::engine::{Engine, Key, KeyKind, ResetMode, MOD_SHIFT};
use core::lm::Profile;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

struct Row {
    e: String,
    w: String,
    first: char,
}

/// The rows; ones that are not `e|w|reading`, whose e is not 2-32 ASCII letters or whose first syllable has no Zhuyin key are skipped (counted).
fn parse(text: &str) -> (Vec<Row>, usize) {
    let (mut rows, mut skipped) = (Vec::new(), 0);
    for line in text.lines().filter(|l| !l.is_empty()) {
        let f: Vec<&str> = line.split('|').collect();
        let first = f.get(2).and_then(|r| r.split_whitespace().next()).and_then(|s| s.chars().find_map(|c| core::engine::Layout::Standard.key_of_symbol(c)));
        match (f.as_slice(), first) {
            ([e, w, _], Some(first)) if (2..=32).contains(&e.len()) && e.bytes().all(|b| b.is_ascii_alphabetic()) && !w.is_empty() => {
                rows.push(Row { e: e.to_ascii_lowercase(), w: w.to_string(), first })
            }
            _ => skipped += 1,
        }
    }
    (rows, skipped)
}

/// Hits (i, ii, iii) of one row, or `None` when the Space did not leave one English token.
fn one_row(e: &mut Engine, r: &Row, times: &mut Vec<Duration>) -> Result<Option<[bool; 3]>, String> {
    e.reset(ResetMode::Discard);
    let mut press = |e: &mut Engine, k: Key| -> Result<Vec<String>, String> {
        let t = Instant::now();
        let o = e.key(k).map_err(|_| "engine error on a key".to_string())?;
        times.push(t.elapsed());
        Ok(o.candidates)
    };
    for (i, c) in r.e.chars().enumerate() {
        press(e, Key::ch(c, if i == 0 { MOD_SHIFT } else { 0 }))?;
    }
    let row = press(e, Key::new(KeyKind::Space))?;
    let syls = e.digits_syls();
    if syls.len() != 1 || !syls[0].starts_with("_punct_") {
        return Ok(None);
    }
    let after = press(e, Key::ch(r.first, 0))?;
    Ok(Some([row.contains(&r.w), after.contains(&r.w), after.first() == Some(&r.w)]))
}

fn p95(mut t: Vec<Duration>) -> f64 {
    t.sort();
    t.get(t.len().saturating_sub(1) * 95 / 100).map_or(0.0, |d| d.as_secs_f64() * 1000.0)
}

pub fn run(args: &[String], root: &Path) -> Result<(), String> {
    let (mut rows_file, mut table, mut lm) = (None::<PathBuf>, None::<PathBuf>, root.join("data/lm/bigram.sjlm"));
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut val = || it.next().map(PathBuf::from).ok_or_else(|| "missing option value".to_string());
        match a.as_str() {
            "--english-next" => rows_file = Some(val()?),
            "--table" => table = Some(val()?),
            "--lm" => lm = val()?,
            _ => return Err("unknown argument".into()),
        }
    }
    let (rows_file, table) = (rows_file.ok_or("--english-next needs a rows file")?, table.ok_or("--table is required")?);
    let text = std::fs::read_to_string(&rows_file).map_err(|e| format!("cannot read file ({:?})", e.kind()))?;
    let (rows, skipped) = parse(&text);
    if skipped > 0 {
        eprintln!("skipped {skipped} rows");
    }
    if rows.is_empty() {
        return Err("no row to measure".into());
    }
    let (tl, co) = (root.join("data/lm/tinylm.sjtl"), root.join("data/lm/colloquial.sjlm"));
    let mut rss = None;
    for (name, profile) in [("chat", Profile::Chat), ("formal", Profile::Formal)] {
        let mut e = cli::replay::build_engine_opts(root, &lm, tl.is_file().then_some(tl.as_path()), co.is_file().then_some(co.as_path()), profile, false, true)?;
        e.set_english(true).map_err(|_| "cannot switch English mixing on".to_string())?;
        e.set_prediction(true).map_err(|_| "cannot switch the prediction row on".to_string())?;
        let pass = |e: &mut Engine| -> Result<(Vec<Option<[bool; 3]>>, f64), String> {
            let mut times = Vec::new();
            let hits = rows.iter().map(|r| one_row(e, r, &mut times)).collect::<Result<Vec<_>, _>>()?;
            Ok((hits, p95(times)))
        };
        pass(&mut e)?; // untimed warm-up, so neither arm pays for cold caches
        let (base, p_base) = pass(&mut e)?;
        let before = crate::rss_kb();
        e.load_english_next(&table)?;
        let (with, p_table) = pass(&mut e)?;
        rss = rss.or(before.zip(crate::rss_kb()));
        if base.iter().map(Option::is_some).ne(with.iter().map(Option::is_some)) {
            return Err("the arms disagree on which rows made an English token".into());
        }
        let hit = |v: &[Option<[bool; 3]>], k: usize| v.iter().filter(|h| h.is_some_and(|h| h[k])).count();
        let flip = |k: usize, up: bool| base.iter().zip(&with).filter(|(b, w)| b.is_some_and(|b| b[k]) != up && w.is_some_and(|w| w[k]) == up).count();
        let mut line = format!("english-next\t{name}\t{}\t{}", rows.len(), base.iter().filter(|h| h.is_none()).count());
        for v in [&base, &with] {
            line += &format!("\t{}\t{}\t{}", hit(v, 0), hit(v, 1), hit(v, 2));
        }
        for k in 0..3 {
            line += &format!("\t{}\t{}", flip(k, true), flip(k, false));
        }
        println!("{line}\t{p_base:.3}\t{p_table:.3}");
    }
    if let Some((a, b)) = rss {
        println!("rss_kib\t{a}\t{b}");
    }
    Ok(())
}
