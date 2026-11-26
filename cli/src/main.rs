use cli::common::{dev_rows, dev_rows_raw, format_rowstats, levenshtein, load_accept, load_forms, load_forms_v2, load_lenient, read_rows, read_rows_raw, root, three_field_rows};
use core::colloquial::{self, Colloquial};
use core::engine::{Engine, Key, KeyKind, Layout, ResetMode, capping_overlay_detailed, load_lexicon_extra_detailed, load_reading_decode, load_lexicon_packs_detailed, read_packs_detailed, PACK_ACG, PACK_BEAUTY, PACK_FILES, PACK_IT, PACK_MEME, PACK_OOV, PACK_QUOTES};
use core::eval::*;
use core::learn::{context_key, SENTINEL};
use core::fuzzy::FUZZY_ALL;
use core::lm::{decode_from_fuzzy_scored, history, pool_order, rerank_pool, CappedLexicon, Demote, Lm, Profile, RecurGrams};
use core::tinylm::{manual_mask, KnPairing, RerankLevel, TinyLm, TinyLmError, BATCH, TOPK};
use core::predict::{parse_units, predict, units_of, units_str, Index, Mode, Unit};
use core::{decode_beam, Error, Lexicon, NoLearning, Syls, BEAM_S1};
use std::collections::{BTreeMap, HashMap};
use std::fmt::Write as _;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc;
use std::time::{Duration, Instant};

mod english;
mod english_next;

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
        // S2r: dev302 + typing76 rows with 一/不 typed in the other standard reading (tools/build_probe.py)
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

/// Option combinations of `lm`, checked before any file is read. The holdout never prints its sentences, so
/// `--dump`, `--dump-words` (`dump` is true for either) and `--picks` are refused there; `--rowstats` holds numbers only and is allowed.
fn check_lm_opts(set: Option<&str>, dump: bool, picks: bool, packs: u32, packs_dir: bool) -> Result<(), String> {
    if set == Some("holdout") && dump {
        return Err("--dump and --dump-words are not allowed with --set holdout".into());
    }
    // `--picks` writes each row's chosen sentence, row content like the dumps: refused with the holdout too, before any file is read.
    if set == Some("holdout") && picks {
        return Err("--picks is not allowed with --set holdout".into());
    }
    // (--tinylm-scores with the holdout is refused in run_lm: its per-candidate record is what alpha and tau are tuned on.)
    if packs == 0 && packs_dir {
        return Err("--packs-dir needs --packs".into());
    }
    Ok(())
}

/// `--judge`, `--accept` and `--rejudge` combinations (docs/spec/evaluation.md §3.5), checked before any file is read. The acceptable-sentence
/// file and the re-judging of picks are for rows that may be shown; the holdout refuses both (its `--judge v2 --rowstats` stays allowed: numbers only).
fn check_judge_opts(set: Option<&str>, v2: bool, accept: bool, rejudge: bool) -> Result<(), String> {
    if accept && !v2 {
        return Err("--accept needs --judge v2".into());
    }
    if set == Some("holdout") && accept {
        return Err("--accept is not allowed with --set holdout".into());
    }
    if set == Some("holdout") && rejudge {
        return Err("--rejudge is not allowed with --set holdout".into());
    }
    Ok(())
}

/// `--rejudge PICKS` input: one line per row, either a `--picks` line (`row TAB rank TAB sentence`, the row number checked) or the bare sentence.
fn parse_picks(text: &str, n: usize) -> Result<Vec<String>, String> {
    let lines: Vec<&str> = text.lines().collect();
    if lines.len() != n {
        return Err(format!("--rejudge: the picks file has {} lines for {n} rows", lines.len()));
    }
    lines
        .iter()
        .enumerate()
        .map(|(i, l)| match l.split('\t').collect::<Vec<_>>().as_slice() {
            [_] => Ok(l.to_string()),
            [row, _, sent] if row.parse::<usize>() == Ok(i + 1) => Ok(sent.to_string()),
            _ => Err(format!("--rejudge: picks line {} is not a pick of row {}", i + 1, i + 1)),
        })
        .collect()
}

/// `--rejudge PICKS` (docs/spec/evaluation.md §3.5): no decoding and no model: the sentence picked for each row is judged again under `--judge` and
/// `--accept`, and written as `--rowstats`. The summary line has the rows, the top1 and `top1_sha256` of the picks (the decoding run's own value
/// when the picks are its `--picks`), tagged `rejudge` and the judge's `+jv2:` tag.
#[allow(clippy::too_many_arguments)]
fn run_rejudge(picks: &str, dev: Option<usize>, rows_file: Option<String>, limit: Option<usize>, name: Option<String>, rowstats: Option<String>, v2: bool, accept: Option<String>, len: &Lenient) -> Result<(), String> {
    let (default_name, rows, raw) = match (dev, &rows_file) {
        (Some(n), None) => (format!("dev{n}"), dev_rows(n)?, accept.as_ref().map(|_| dev_rows_raw(n)).transpose()?),
        (None, Some(f)) => {
            let mut rows = read_rows(f)?;
            if let Some(n) = limit.filter(|&n| n != 0) {
                rows.truncate(n);
            }
            (Path::new(f).file_name().map_or(String::new(), |s| s.to_string_lossy().into_owned()), rows, accept.as_ref().map(|_| read_rows_raw(f, limit)).transpose()?)
        }
        _ => return Err("--rejudge needs exactly one of --dev, --rows".into()),
    };
    let judge = if v2 { Some(load_judge(accept.as_deref(), raw.as_deref())?) } else { None };
    let text = fs::read_to_string(picks).map_err(|e| format!("--rejudge: cannot read the picks file ({:?})", e.kind()))?;
    let picked = parse_picks(&text, rows.len())?;
    let rowstats = match rowstats {
        Some(f) => Some(fs::File::create(f).map_err(|e| format!("cannot create rowstats ({:?})", e.kind()))?),
        None => None,
    };
    let mut rs = Vec::new();
    for (i, ((truth, syls, _), pick)) in rows.iter().zip(&picked).enumerate() {
        let ok = match &judge {
            Some(j) => Judge2::new(&j.forms, len, truth, syls, j.accept.as_ref().map_or(&[][..], |a| a.get(i))).ok(pick),
            None => len.apply(pick) == len.apply(truth),
        };
        rs.push((ok, pick.clone(), truth.clone()));
    }
    if let Some(mut f) = rowstats {
        f.write_all(format_rowstats(&rs).as_bytes()).map_err(|e| format!("cannot write rowstats ({:?})", e.kind()))?;
    }
    let sha = sha256_hex(picked.join("\n").as_bytes());
    println!("## {}  rejudge{}  {{'n': {}, 'top1': {}, 'top1_sha256': '{sha}'}}", name.unwrap_or(default_name), judge.as_ref().map_or(String::new(), |j| j.tag.clone()), rows.len(), rs.iter().filter(|r| r.0).count());
    Ok(())
}

/// What `--judge v2` loads: the forms-v2 table, the row's acceptable sentences when `--accept` is given, and the summary tag `+jv2:<A>:<B>:<C>` (the
/// first 8 hex of forms.tsv, forms-extra.tsv and the accept file; `none` without one).
struct JudgeV2 {
    forms: Forms,
    accept: Option<Accept>,
    tag: String,
}

/// Loads the judge-v2 tables and the accept file (tied to `raw`, the rows' original text), before any output file is created.
fn load_judge(accept: Option<&str>, raw: Option<&[String]>) -> Result<JudgeV2, String> {
    let (forms, a, b) = load_forms_v2()?;
    let (accept, c) = match (accept, raw) {
        (Some(f), Some(rows)) => load_accept(f, rows).map(|(acc, c)| (Some(acc), c))?,
        _ => (None, "none".to_string()),
    };
    Ok(JudgeV2 { forms, accept, tag: format!("+jv2:{a}:{b}:{c}") })
}

/// `--packs acg|quotes|it|meme|beauty` or a comma list of them: sets the pack bits; anything else is refused.
fn pack_flag(v: &str) -> Result<u32, String> {
    v.split(',').try_fold(0, |bits, name| match name {
        "acg" => Ok(bits | PACK_ACG),
        "quotes" => Ok(bits | PACK_QUOTES),
        "it" => Ok(bits | PACK_IT),
        "meme" => Ok(bits | PACK_MEME),
        "beauty" => Ok(bits | PACK_BEAUTY),
        _ => Err("--packs takes acg, quotes, it, meme, beauty or a comma list of them".to_string()),
    })
}

/// The summary line's pack marker: `+acg`, `+quotes`, `+it`, `+meme`, `+beauty` in that order; nothing with no pack (the existing golden lines are untouched).
fn pack_tag(packs: u32) -> String {
    format!(
        "{}{}{}{}{}",
        if packs & PACK_ACG != 0 { "+acg" } else { "" },
        if packs & PACK_QUOTES != 0 { "+quotes" } else { "" },
        if packs & PACK_IT != 0 { "+it" } else { "" },
        if packs & PACK_MEME != 0 { "+meme" } else { "" },
        if packs & PACK_BEAUTY != 0 { "+beauty" } else { "" }
    )
}

/// The packs directory (`--packs-dir` or data/packs); a run that asks for a pack must fail loudly when its file is
/// missing (the engine tolerates it), so this is checked with the other options, before the model is read.
fn packs_dir_checked(packs: u32, packs_dir: Option<String>) -> Result<PathBuf, String> {
    let pdir = packs_dir.map_or_else(|| root().join("data/packs"), PathBuf::from);
    for (bit, file) in PACK_FILES {
        let path = pdir.join(file);
        if packs & bit != 0 && !path.is_file() {
            return Err(format!("--packs {}: pack file not found: {}", file.trim_end_matches("-add.tsv"), path.display()));
        }
    }
    Ok(pdir)
}

/// `--extra-overlay FILE` (evaluation only, docs/spec/lexicon-and-packs.md §3.6): a missing file stops the run naming the path,
/// checked with the other options before the model is read.
fn extra_overlay_checked(extra: Option<String>) -> Result<Option<PathBuf>, String> {
    let extra = extra.map(PathBuf::from);
    match extra.as_deref() {
        Some(p) if !p.is_file() => Err(format!("--extra-overlay: file not found: {}", p.display())),
        _ => Ok(extra),
    }
}

/// `--colloquial-core FILE`: a missing file stops the run naming the path, checked with the other options before the model is read.
fn colloquial_checked(path: Option<String>) -> Result<Option<PathBuf>, String> {
    let path = path.map(PathBuf::from);
    match path.as_deref() {
        Some(p) if !p.is_file() => Err(format!("--colloquial-core: file not found: {}", p.display())),
        _ => Ok(path),
    }
}

/// The lexicon with the chosen packs and the `--extra-overlay` rows, and the overlay text that caps the LM scores (the
/// same helpers as the engine's `new` and `load_lm`). The extra rows travel with the pack rows, so they are capped too.
/// `names` false is `--no-names`: names-add.tsv stays out of the lexicon (its words are then in no lexicon entry, so capping them changes nothing).
fn load_with_packs(dir: &Path, packs: u32, pdir: &Path, extra: Option<&Path>, names: bool) -> Result<(std::sync::Arc<Lexicon>, String), String> {
    // The pack rows are read once and reused for the cap; overlay-add.tsv is read again by capping_overlay, as load_lm does.
    let (lex, pack_text) = load_lexicon_extra_detailed(dir, Some((pdir, packs)), extra, names).map_err(|e| e.to_string())?;
    let overlay = capping_overlay_detailed(dir, &pack_text).map_err(|e| e.to_string())?;
    Ok((lex, overlay))
}

/// S7c-1 `--tinylm`: alpha and tau hold only for the n-gram scores they were tuned on (S7a: this model, its classes and the demotion
/// table on, and the Kneser-Ney side file it records, or none: docs/spec/models.md §3.5, §8.1). A different model hash, another side file
/// state, `--no-classes` or `--no-demote` is refused before anything is printed. The file is read, checked and paired by the
/// engine's loader (`TinyLm::load_paired`, S7c-2a). Returns the model, the load time (read + parse + the pairing checks and the file's hash) and the sha8 of the bytes it parsed.
fn load_tinylm(path: &str, lm: &Lm, classes: bool, demote: bool) -> Result<(TinyLm, Duration, String), String> {
    if !classes || !demote {
        return Err("--tinylm: this .sjtl was tuned with the class term and the demotion table on (drop --no-classes / --no-demote)".into());
    }
    let t0 = Instant::now();
    let tl = TinyLm::load_paired(Path::new(path), lm).map_err(|e| match e {
        // docs/spec/models.md §3.5, §8.1: each mismatch has its own remedy; --no-kn helps only when the file was tuned without KN.
        TinyLmError::KneserNey(KnPairing::TunedWithout) => {
            format!("--tinylm: {e} (use --no-kn, or a .sjtl exported with --kn-side for this kn.sjkn)")
        }
        TinyLmError::KneserNey(KnPairing::OtherSideFile) => format!("--tinylm: {e} (put the kn.sjkn it was tuned with beside the model)"),
        TinyLmError::KneserNey(KnPairing::TunedWith) => {
            format!("--tinylm: {e} (put the kn.sjkn it was tuned with beside the model and drop --no-kn)")
        }
        TinyLmError::OtherNgram => format!("--tinylm: {e}"),
        _ => e.to_string(),
    })?;
    let sha8 = tl.file_sha256()[..8].to_string();
    let load = t0.elapsed();
    Ok((tl, load, sha8))
}

/// `--pool-mask-diff` (docs/spec/evaluation.md): whether a row's mask differs from the one the exploration slice recur-rerank (research repository) used.
/// That patch took the readings of the reordered first path and made `manual_mask` with it as the base; this slice makes it once with the decoder's first
/// path as the base (`masked0`, pool order) and permutes it with the order. A row counts when it was reordered and the two masks differ at any position,
/// or when it was not reordered only because the new first candidate is masked (`unmasked_order`: `pool_order` without the mask gives one).
/// `reordered_readings` are the readings of the reordered first path (asked for only when the row was reordered and the lexicon has manual words).
fn mask_differs(
    reordered: &Option<(Vec<usize>, Vec<f64>)>, unmasked_order: bool, masked0: &[bool], top0: &[&str], reordered_readings: impl FnOnce() -> Vec<String>,
    manual: &core::ManualSet,
) -> bool {
    let Some((order, _)) = reordered else { return unmasked_order };
    if manual.is_empty() {
        return false;
    }
    let top: Vec<&str> = order.iter().map(|&k| top0[k]).collect();
    let theirs = manual_mask(&reordered_readings(), &top, manual);
    order.iter().zip(&theirs).any(|(&k, &m)| masked0[k] != m)
}

/// One `--tinylm-scores` line, the research scoring record without the key columns and the two other LLs:
/// `i, n, ng, ok, ed, glen`, then `c` (the 0-based index the rerank chose, 0 when it did not run), for n >= 2 `ll`, and `masked`, the
/// 0-based indices `tinylm::manual_mask` kept the rerank from choosing (docs/spec/decoder.md §7; empty when none, always present).
/// With a fuzzy-sound bit (`--fuzzy`, docs/spec/decoder.md §6.5) `n`, `ng`, `ok`, `ed`, `ll` and `masked` list the rerank pool in pool order, `c` is still an index
/// into the decoder's n-best (the dump's order), and `idx` gives each pool entry's n-best index. Numbers only.
/// `col` (`--colloquial-core`, only a row where the colloquial term ran: chat, the paired core or `--tinylm-paired`, and an `ll`) is the last key, one entry per pool candidate. See run_lm.
/// `extra` (`--tinylm-scores-topk`) continues the `ll` list past the 8 the rerank scored: one entry per further candidate, `null` for a
/// candidate the small model could not score (longer than its context); it is empty without the option, so the line is then unchanged.
#[allow(clippy::too_many_arguments)]
fn scores_line(i: usize, ng: &[f64], ok: &[bool], ed: &[u32], glen: usize, chosen: usize, ll: Option<&[f64]>, extra: &[Option<f64>], masked: &[bool], idx: Option<&[usize]>, col: Option<&[f64]>) -> Result<String, String> {
    // JSON has no inf / NaN (Python's json.loads rejects inf, and a NaN would slip through parity.py's tolerance test).
    if !ng.iter().chain(ll.unwrap_or(&[])).chain(extra.iter().flatten()).all(|x| x.is_finite()) {
        return Err(format!("--tinylm-scores: row {i} has a non-finite score"));
    }
    let list = |v: Vec<String>| format!("[{}]", v.join(", "));
    let mut s = format!(
        "{{\"i\": {i}, \"n\": {}, \"ng\": {}, \"ok\": {}, \"ed\": {}, \"glen\": {glen}, \"c\": {chosen}",
        ng.len(),
        list(ng.iter().map(|x| format!("{x:?}")).collect()),
        list(ok.iter().map(|&o| (o as u8).to_string()).collect()),
        list(ed.iter().map(u32::to_string).collect())
    );
    if let Some(ll) = ll {
        let all = ll.iter().map(|x| format!("{x:?}")).chain(extra.iter().map(|x| x.map_or("null".to_string(), |x| format!("{x:?}"))));
        s += &format!(", \"ll\": {}", list(all.collect()));
    }
    s += &format!(", \"masked\": {}", list(masked.iter().enumerate().filter(|(_, &m)| m).map(|(k, _)| k.to_string()).collect()));
    if let Some(idx) = idx {
        s += &format!(", \"idx\": {}", list(idx.iter().map(usize::to_string).collect()));
    }
    // The colloquial score of each candidate (last key, so a line without it is byte for byte what it was). A col that is not finite is `null`:
    // the engine then falls back to the plain pick (`colloquial::guarded_pick`) and `c` is that pick, not the fused one.
    if let Some(col) = col {
        s += &format!(", \"col\": {}", list(col.iter().map(|x| if x.is_finite() { format!("{x:?}") } else { "null".to_string() }).collect()));
    }
    Ok(s + "}\n")
}

/// Sets the flag when dropped, panic included: the threaded `run_lm` uses it to release workers that wait for the writer.
struct Release<'a>(&'a AtomicBool);

impl Drop for Release<'_> {
    fn drop(&mut self) {
        self.0.store(true, Ordering::Relaxed);
    }
}

/// What one row of `run_lm` hands to the writers: the counters it adds to and the text it adds to each output file.
#[derive(Default)]
struct RowOut {
    ok: bool,
    in64: bool,
    long: bool,
    /// `--tinylm-scores-topk`: candidates 9..N the small model could not score (longer than its context).
    no_ll: usize,
    /// `--pool-mask-diff`: the row is one of `mask_differs`.
    mask_diff: bool,
    rerank: Option<Duration>,
    /// The row's chosen candidate (the `top1_sha256` input and the rowstats `top1`).
    first: String,
    /// `--forms-rowstats`: the forms verdict of the row, and whether the single-character step was skipped on either side.
    forms_ok: bool,
    forms_no_char_step: bool,
    scores: String,
    dump: String,
    words: String,
    /// `--picks`: `row\trank\tsentence` of the row (`row\t-\t` when it has no candidate).
    picks: String,
}

/// LM mode (docs/spec/evaluation.md §1.4): the one summary line of lm_eval.py, plus the optional `--dump` and `--rowstats`.
/// S7c-1: `--tinylm FILE.sjtl` reranks the first 8 candidates of every row with the small character model (docs/spec/semantic-rerank.md);
/// `--tinylm-scores OUT` writes numbers-only scoring records for them. `--dump-words OUT` writes, per row, the
/// word split of every `--dump` line (`row\trank\twords`, same ranks and order) after rank 0, the gold sentence split by the core's
/// `segment_words` (`-` when it cannot be split). `--rerank-level conservative|standard|aggressive` (default standard) picks which of
/// the file's (alpha, tau, delta) the rerank uses (docs/spec/semantic-rerank.md §9). `--threads N` (default 1) decodes the
/// rows on N threads sharing one copy of the models; the output files and the summary line are byte for byte those of N = 1. With N
/// above 1 the stderr tinylm latency line has other timings (measured under contention) and ends with " (threads N: measured under
/// contention)", N being the worker count after capping it at the number of 32-row blocks; its counts are the same, and with one
/// worker the line is as for N = 1. A panic in a row exits 101 on the inline path (N = 1, or one block) and exits 1 with "row N
/// panicked in a --threads worker" on the threaded path. Only the `lm` mode takes it: the other modes refuse it
/// as an unknown argument.
///
/// `--colloquial-core FILE.sjlm` (docs/spec/semantic-rerank.md §4.6): loads the colloquial table like the engine's
/// `load_colloquial` (word sets of k from `data/lexicon` and the `--packs` rows) and decides per run, by the engine's conditions (chat
/// profile, any rerank level (each with its own alpha, tau and delta: docs/spec/semantic-rerank.md §4.4), a `--tinylm` whose SHA-256 is the one
/// the term was measured with, or any `--tinylm` with `--tinylm-paired`), whether each row's rerank is `TinyLm::rerank_colloquial` (the word split of each of the first 8 candidates goes in,
/// and the `--rerank-level`) or the plain `rerank`. It refuses no setting, so
/// "everything else is unchanged" can be checked with the same option; a missing file stops the run before anything is read. The summary
/// line gains `+colloq:<sha8 of the table>`; in chat, when the term is not in effect (no `--tinylm`, or an unpaired one without `--tinylm-paired`), it is
/// `-colloq:<sha8>` instead (no `+colloq:` in the line) and stderr says why; formal keeps `+colloq:<sha8>`. The files follow the n-gram order as without the option; `--tinylm-scores` `c` is the pick.
/// `--tinylm-paired` (needs `--tinylm` and `--colloquial-core`) takes the loaded model as the paired one, so the term acts on it as it ships; the tag gains
/// `+pair:<sha8 of the .sjtl>` after the colloquial one. `--picks FILE`: per row `row\trank\tsentence`, the sentence the row is scored on and its n-best
/// index (the `--tinylm-scores` `c`; `row\t-\t` for a `--fuzzy` row with no candidate), in row order whatever `--threads`; refused with the holdout.
/// On a row where the term is in effect (chat, paired `.sjtl` or `--tinylm-paired`, an `ll`), `--tinylm-scores` ends with `"col": [...]`, the colloquial score of each
/// candidate in the line's order (`null` if not finite, then `c` is the plain pick), so the fused pick can be recomputed offline
/// (`colloquial::fused_pick` with `colloquial::cell(level)`; tools/lib/fusegrid.py `fuse_pick`). Other lines (formal, no `--colloquial-core`) are unchanged.
///
/// `--colloquial-first-unigram` (research): the colloquial lc scores the first word of each split with
/// the table's unigram estimate instead of P(w | `<s>`) (`Colloquial::set_first_unigram`); it needs `--colloquial-core`, and the tag becomes
/// `+colloq:<sha8>+fu`. The engine has no such switch.
///
/// The first word a table has no `<s>` bigram for is scored by the unigram estimate (SR-TW-M, `Colloquial::set_unseen_start_unigram`) by default, as in the
/// engine (docs/spec/semantic-rerank.md §4): the shipping setting, tag `+colloq:<sha8>` as before. `--colloquial-no-unseen-start-unigram` (research) turns it
/// off, tag `+colloq:<sha8>+us0`; it needs `--colloquial-core`. With `--colloquial-first-unigram` the first-word estimate covers every first word,
/// whether the SR-TW-M one is on or off (tag `+fu`).
///
/// `--tinylm-scores-topk N` (N >= 8, default 8; research): the `--tinylm-scores` `ll` covers the first N
/// candidates of the n-gram order instead of 8, the extra ones scored by `TinyLm::ll_many` under the key `rerank_ll` uses (a candidate over the model's context gets `null`, counted on stderr). The pick `c`, the
/// other score fields, the rowstats, the dump and the summary line are those of a run without it. It needs `--tinylm-scores`.
///
/// The reading term (docs/spec/decoder.md §5): the n-gram decoder adds `core::reading`'s term
/// (lambda 2.0, c 4, the sandhi readings of 一 and 不 free, the lexicon's floor-score readings estimated from the multi-character words, the 16 pairs of
/// `data/lexicon/std-exempt.tsv` free) to the path score of every single-character word, in every setting, so the beam, the n-best, the dump,
/// `--tinylm-scores` `ng` and the pick all see it. The table is built by `core::engine::load_reading_decode` from the merged lexicon and the data folder, the one
/// function `Engine::load_lm` calls too. The summary tag ends with `+rd+rx:<first 8 hex of std-exempt.tsv's SHA-256>+rm+rn` in every setting (`+rm`: the reading-mass rule). `--no-reading-decode`
/// (evaluation only) leaves the term out: the decoder is the one without it and the tag has no `+rd`, `+rx`, `+rm` or `+rn`. There is no reading-channel term in the
/// fusion (docs/spec/decoder.md §5; no `+rc` tag), so the colloquial tag is just `+colloq:<sha8>`.
///
/// `--auto-english on|off`, `--auto-theta F`, `--auto-kappa F`, `--auto-kappa-dup F` (docs/spec/english-mixed.md §16.4) are accepted and checked (on or off; a finite
/// number) and change nothing here: the comparison happens at a closing key, and this tool decodes readings. `english-sim` is where they act.
///
/// `--reading-table FILE` (evaluation only; experiments/reading-counts-b3 in the research repository; not in the engine or the C ABI): the reading term's t comes from
/// the file's `character TAB reading TAB t` rows (t before the `lambda * max(t, -c)` step) instead of the built-in table; a pair of the lexicon the file lacks
/// reads t = 0, and a malformed row, a character or reading the lexicon has no single-character pair for, or a pair twice stops the run naming the line. The tag
/// gains `+rt:<first 8 hex of the file's SHA-256>` after `+rd+rx:...+rm+rn`. `--dump-reading-table FILE` writes the built-in table (every (character, reading) pair of
/// a character with at least two single-character readings, t as the term's input, sorted, shortest round-trip float text) and stops, printing nothing; it refuses
/// to overwrite. A run with the file it wrote gives the picks and `top1_sha256` of a run without `--reading-table`. Both need the reading term (no `--no-reading-decode`).
///
/// Third-tone sandhi (docs/spec/decoder.md §6.6) is on: each row's position table is `tone3::positions` over its syllables, the decoder reads the sandhi source and
/// `path_readings` maps a sandhi word to its lexicon reading; the tag ends `+t3c` after the reading marks (`+uc` too). `--no-tone3` (evaluation only) hands the switch off
/// to the table function and leaves the mark out: the output is byte for byte that of the tree before the feature. `--tone3-delta D` (evaluation only, D >= 0, not with
/// `--no-tone3`) replaces `DELTA_TONE3`; off the default the mark is `+t3c:dD`.
/// Colloquial sandhi (docs/spec/decoder.md §6.6, §8.7) rides the same switch, hence the `c` of the mark: the position table carries the neutral syllables too, and the
/// `colloq` rows are read. `--neutral-delta D` (evaluation only, D >= 0, not with `--no-tone3`) replaces `DELTA_NEUTRAL`; off the default the mark gets `:nD`.
fn run_lm(args: &[String], len: &Lenient) -> Result<(), String> {
    let (mut lm_path, mut profile, mut name, mut dev, mut rows_file) = (None, None, None, None, None);
    let (mut limit, mut set, mut dump, mut ctx_mode, mut demote) = (None::<usize>, None, None, false, true);
    let mut dump_words = None::<String>;
    let (mut classes, mut kn, mut tri) = (true, true, true);
    // docs/spec/lexicon-and-packs.md §1.1, §3: the names layer is always on; `--no-names` (evaluation only: the per-layer breakdown, and
    // tools/build_names.py, which passes its own candidate list as --extra-overlay) leaves it out.
    let mut names = true;
    // `--no-recur` (evaluation only): leave the context recurrence term out (docs/spec/decoder.md §4.6); the pool weight goes with it.
    let mut recur = true;
    // `--no-pool-recur` (evaluation only): leave the context recurrence weight on the semantic correction's pool out (docs/spec/semantic-rerank.md §3.1.1).
    let mut pool_recur = true;
    // `--pool-mask-diff FILE` (evaluation only): the row numbers where the exploration's mask and this one differ (see `mask_differs`).
    let mut pool_mask_diff = None::<String>;
    let mut forms_rowstats = None::<String>;
    // docs/spec/evaluation.md §3.5: `--judge v1|v2` (default v1), `--accept FILE` (v2 only), `--rejudge PICKS` (no decoding).
    let (mut judge_v2, mut accept, mut rejudge) = (false, None::<String>, None::<String>);
    let (mut rowstats, mut tinylm, mut tinylm_scores) = (None::<String>, None::<String>, None::<String>);
    let mut scores_topk = None::<usize>;
    let mut level = None::<RerankLevel>;
    // docs/spec/lexicon-and-packs.md §4.1: `--packs acg` adds data/packs/acg-add.tsv (or `--packs-dir DIR`'s) to the lexicon and the cap; off by default.
    let (mut packs, mut packs_dir) = (0u32, None::<String>);
    let mut extra_overlay = None::<String>;
    let mut threads = 1usize;
    let mut colloquial_core = None::<String>;
    // `--tinylm-paired`: the loaded small model counts as the colloquial term's paired model (the SHA-256 condition of
    // `colloquial::applies` is taken as met; chat and a loaded table still required). Evaluation only; the engine is unchanged.
    let mut tinylm_paired = false;
    // `--picks FILE`: per row `row\trank\tsentence`, the sentence the row is scored on (rank = its n-best index, `--tinylm-scores` `c`).
    let mut picks = None::<String>;
    let mut first_unigram = false;
    let mut no_unseen_start = false;
    // docs/spec/decoder.md §6.1: `--fuzzy FLAGS` (the bits of shanjie_engine_set_fuzzy). Given, the summary line gets
    // `+fz:FLAGS` and a row no reading covers counts as wrong instead of stopping the run.
    let mut fuzzy = None::<u32>;
    // `--fuzzy-delta-sound D` (docs/spec/decoder.md §6.1, evaluation only): the fuzzy-sound penalty of this run; the summary tag gets `:dD` when it is not the default.
    let mut delta_sound = core::fuzzy::DELTA_SOUND;
    let mut delta_given = false;
    let mut reading_decode = true;
    // docs/spec/decoder.md §6.6: third-tone sandhi is on unless `--no-tone3`; the summary tag then ends `+t3c` (`+t3c:dD` with `--tone3-delta D`, evaluation only,
    // not the default `tone3::DELTA_TONE3`).
    let (mut tone3, mut delta_t3, mut delta_t3_given) = (true, core::tone3::DELTA_TONE3, false);
    // `--neutral-delta D` (evaluation only, D >= 0, not with `--no-tone3`): replaces `tone3::DELTA_NEUTRAL`, for the neutral source and the `colloq` rows alike.
    let mut neutral_delta = None::<f64>;
    let (mut reading_table, mut dump_reading_table) = (None::<String>, None::<String>);
    // `--unseen-ceiling K` (evaluation only, `CappedLexicon::with_packs_ceil`): a generated overlay word the model never saw is scored at most
    // log10(K / N). The summary line gets `+uc:K`; without the option the output is unchanged.
    let mut unseen_ceil = None::<f64>;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut val = || it.next().cloned().ok_or_else(|| "missing option value".to_string());
        let num = |v: String| v.parse::<usize>().map_err(|_| format!("bad number (length {})", v.chars().count()));
        match a.as_str() {
            "--threads" => match val().ok().and_then(|v| v.parse::<usize>().ok()) {
                Some(n) if n >= 1 => threads = n,
                _ => {
                    eprintln!("error: --threads takes a whole number of at least 1");
                    std::process::exit(2);
                }
            },
            "--lm" => lm_path = Some(val()?),
            "--profile" => profile = Some(val()?),
            "--name" => name = Some(val()?),
            "--dev" => dev = Some(num(val()?)?),
            "--rows" => rows_file = Some(val()?),
            "--limit" => limit = Some(num(val()?)?),
            "--set" => set = Some(val()?),
            "--dump" => dump = Some(val()?),
            "--dump-words" => dump_words = Some(val()?),
            "--rowstats" => rowstats = Some(val()?),
            "--forms-rowstats" => forms_rowstats = Some(val()?),
            "--judge" => {
                judge_v2 = match val()?.as_str() {
                    "v1" => false,
                    "v2" => true,
                    _ => return Err("--judge must be v1 or v2".into()),
                }
            }
            "--accept" => accept = Some(val()?),
            "--rejudge" => rejudge = Some(val()?),
            "--tinylm" => tinylm = Some(val()?),
            "--tinylm-scores" => tinylm_scores = Some(val()?),
            "--tinylm-scores-topk" => match val().ok().and_then(|v| v.parse::<usize>().ok()) {
                Some(n) if n >= TOPK => scores_topk = Some(n),
                _ => return Err(format!("--tinylm-scores-topk takes a whole number of at least {TOPK}")),
            },
            "--rerank-level" => {
                level = Some(match val()?.as_str() {
                    "conservative" => RerankLevel::Conservative,
                    "standard" => RerankLevel::Standard,
                    "aggressive" => RerankLevel::Aggressive,
                    _ => return Err("--rerank-level must be conservative, standard or aggressive".into()),
                })
            }
            "--context" => ctx_mode = true,
            "--no-demote" => demote = false,
            "--no-classes" => classes = false,
            "--no-kn" => kn = false,
            "--no-tri" => tri = false,
            "--no-names" => names = false,
            "--packs" => packs |= pack_flag(&val()?)?,
            "--packs-dir" => packs_dir = Some(val()?),
            "--extra-overlay" => extra_overlay = Some(val()?),
            "--colloquial-core" => colloquial_core = Some(val()?),
            "--tinylm-paired" => tinylm_paired = true,
            "--picks" => picks = Some(val()?),
            "--colloquial-first-unigram" => first_unigram = true,
            "--colloquial-no-unseen-start-unigram" => no_unseen_start = true,
            "--fuzzy-delta-sound" => {
                delta_sound = val()?.parse::<f64>().ok().filter(|d| d.is_finite() && *d > 0.0).ok_or("--fuzzy-delta-sound takes a number above 0")?;
                delta_given = true;
            }
            "--fuzzy" => fuzzy = Some(val()?.parse::<u32>().ok().filter(|f| f & !FUZZY_ALL == 0).ok_or("--fuzzy takes flags 0-31")?),
            "--no-recur" => recur = false,
            "--no-pool-recur" => pool_recur = false,
            "--pool-mask-diff" => pool_mask_diff = Some(val()?),
            "--no-reading-decode" => reading_decode = false,
            "--no-tone3" => tone3 = false,
            "--tone3-delta" => {
                delta_t3 = val()?.parse::<f64>().ok().filter(|d| d.is_finite() && *d >= 0.0).ok_or("--tone3-delta takes a number of at least 0")?;
                delta_t3_given = true;
            }
            "--neutral-delta" => neutral_delta = Some(val()?.parse::<f64>().ok().filter(|d| d.is_finite() && *d >= 0.0).ok_or("--neutral-delta takes a number of at least 0")?),
            "--reading-table" => reading_table = Some(val()?),
            "--unseen-ceiling" => unseen_ceil = Some(val()?.parse::<f64>().ok().filter(|k| k.is_finite() && *k > 0.0).ok_or("--unseen-ceiling takes a positive count")?),
            "--dump-reading-table" => dump_reading_table = Some(val()?),
            // docs/spec/english-mixed.md §16.4: the comparison's options, taken and checked so one option list serves this tool and `english-sim`. Decoding rows from
            // their readings has no closing key, so they change no byte of the output here.
            "--auto-english" => {
                if !matches!(val()?.as_str(), "on" | "off") {
                    return Err("--auto-english takes on or off".into());
                }
            }
            "--auto-theta" | "--auto-kappa" | "--auto-kappa-dup" => {
                val()?.parse::<f64>().ok().filter(|x| x.is_finite()).ok_or("--auto-theta, --auto-kappa and --auto-kappa-dup take a finite number")?;
            }
            _ => return Err("unknown argument".into()),
        }
    }
    check_lm_opts(set.as_deref(), dump.is_some() || dump_words.is_some(), picks.is_some(), packs, packs_dir.is_some())?;
    check_judge_opts(set.as_deref(), judge_v2, accept.is_some(), rejudge.is_some())?;
    if let Some(pk) = &rejudge {
        // No decoding: only --rowstats has anything to write.
        if dump.is_some() || dump_words.is_some() || picks.is_some() || tinylm_scores.is_some() || forms_rowstats.is_some() {
            return Err("--rejudge writes only --rowstats".into());
        }
        if set.is_some() {
            return Err("--rejudge takes the rows from --rows or --dev, not --set".into());
        }
        return run_rejudge(pk, dev, rows_file, limit, name, rowstats, judge_v2, accept, len);
    }
    if tinylm_scores.is_some() && tinylm.is_none() {
        return Err("--tinylm-scores needs --tinylm".into());
    }
    // Without a small model there is no pool: the file would silently stay empty.
    if pool_mask_diff.is_some() && tinylm.is_none() {
        return Err("--pool-mask-diff needs --tinylm".into());
    }
    // docs/spec/semantic-rerank.md §9: without a small model a level (standard included) would silently measure the n-gram alone.
    if level.is_some() && tinylm.is_none() {
        return Err("--rerank-level needs --tinylm".into());
    }
    // Without the sandhi source the penalty would silently change nothing.
    if delta_t3_given && !tone3 {
        return Err("--tone3-delta cannot be combined with --no-tone3".into());
    }
    if neutral_delta.is_some() && !tone3 {
        return Err("--neutral-delta cannot be combined with --no-tone3".into());
    }
    // Before the lexicon is loaded: the `colloq` rows' scores move with it.
    core::tone3::set_neutral_delta(neutral_delta);
    // The sound penalty only exists on a fuzzy-sound bit; without one the option would silently change nothing.
    if delta_given && fuzzy.unwrap_or(0) & core::fuzzy::FUZZY_SOUND == 0 {
        return Err("--fuzzy-delta-sound needs --fuzzy with a sound bit (0-3)".into());
    }
    let level = level.unwrap_or(RerankLevel::Standard);
    if scores_topk.is_some() && tinylm_scores.is_none() {
        return Err("--tinylm-scores-topk needs --tinylm-scores".into());
    }
    if scores_topk.is_some() && fuzzy.is_some() {
        // The extra candidates are the n-best's 9..N; with --fuzzy the rerank pool is not the first 8, so the two lists would not line up.
        return Err("--tinylm-scores-topk cannot be combined with --fuzzy".into());
    }
    if tinylm_scores.is_some() && set.as_deref() == Some("holdout") {
        return Err("--tinylm-scores is not allowed with --set holdout".into());
    }
    // The holdout prints numbers only through --rowstats; the row numbers of --pool-mask-diff are per-row output like --tinylm-scores.
    if pool_mask_diff.is_some() && set.as_deref() == Some("holdout") {
        return Err("--pool-mask-diff is not allowed with --set holdout".into());
    }
    // Without the pool weight (no --context, --no-recur or --no-pool-recur) no row is reordered: the file would silently stay empty.
    if pool_mask_diff.is_some() && !(ctx_mode && recur && pool_recur) {
        return Err("--pool-mask-diff needs --context and the pool weight (not --no-recur or --no-pool-recur)".into());
    }
    if (reading_table.is_some() || dump_reading_table.is_some()) && !reading_decode {
        return Err("--reading-table and --dump-reading-table need the reading term (not --no-reading-decode)".into());
    }
    if reading_table.is_some() && dump_reading_table.is_some() {
        return Err("--reading-table and --dump-reading-table cannot be combined".into());
    }
    let pdir = packs_dir_checked(packs, packs_dir)?;
    let extra_overlay = extra_overlay_checked(extra_overlay)?;
    let colloquial_core = colloquial_checked(colloquial_core)?;
    if first_unigram && colloquial_core.is_none() {
        return Err("--colloquial-first-unigram needs --colloquial-core".into());
    }
    if no_unseen_start && colloquial_core.is_none() {
        return Err("--colloquial-no-unseen-start-unigram needs --colloquial-core".into());
    }
    // Without a small model or a table the override would silently do nothing.
    if tinylm_paired && (tinylm.is_none() || colloquial_core.is_none()) {
        return Err("--tinylm-paired needs --tinylm and --colloquial-core".into());
    }
    // Summary-line marker `+xo:<sha8 of the file>`, only with the option, so a names-layer run never prints the baseline's label.
    let xo = match &extra_overlay {
        Some(p) => format!("+xo:{}", &sha256_hex(&fs::read(p).map_err(|e| format!("--extra-overlay: cannot read {} ({:?})", p.display(), e.kind()))?)[..8]),
        None => String::new(),
    };
    let profile_name = profile.ok_or("--profile is required")?;
    let prof = match profile_name.as_str() {
        "chat" => Profile::Chat,
        "formal" => Profile::Formal,
        _ => return Err("--profile must be chat or formal".into()),
    };
    let lm_path = lm_path.ok_or("--lm is required")?;
    // S2k: the class term is part of the model; classes.sjc beside it is required unless --no-classes.
    // A kn.sjkn beside the model (docs/spec/decoder.md §3.3) turns the Kneser-Ney term on unless --no-kn. The trigram table beside it
    // (docs/spec/decoder.md §3.6) is the profile's own, read unless --no-tri: `trigram.sjt3` for formal, `trigram-chat.sjt3` for chat
    // (the other profile's file is not read, so a bad one does not matter here). Both profiles score with Kneser-Ney and their table.
    let tri_profiles: &[Profile] = if tri { std::slice::from_ref(&prof) } else { &[] };
    let lm = Lm::load_opts(std::path::Path::new(&lm_path), classes, kn, tri_profiles).map_err(|e| e.to_string())?;
    let scoring = lm.scoring(prof);
    let rss_before = tinylm.is_some().then(rss_kb).flatten();
    let tl = tinylm.as_deref().map(|p| load_tinylm(p, &lm, classes, demote)).transpose()?;
    // docs/spec/semantic-rerank.md §2.1: a version 1 or 2 file has only the standard set; another level would silently measure the standard one.
    if level != RerankLevel::Standard && tl.as_ref().is_some_and(|(m, _, _)| !m.has_levels()) {
        eprintln!("error: --rerank-level: this .sjtl (version 1 or 2) has only the standard set; use a version 3 or 4 file or --rerank-level standard");
        std::process::exit(2);
    }
    let rss_after = tinylm.is_some().then(rss_kb).flatten();
    let dir = root().join("data/lexicon");
    // The colloquial table and the word sets of its k term, from the engine's own loader (the engine's pack rows are the `--packs` rows).
    let colloq = match &colloquial_core {
        Some(f) => {
            let pack_text = read_packs_detailed(&pdir, packs).map_err(|e| e.to_string())?;
            let mut co = Colloquial::load_from_dir(f, &dir, Some(&pack_text)).map_err(|e| format!("--colloquial-core: {e}"))?;
            co.set_first_unigram(first_unigram);
            co.set_unseen_start_unigram(!no_unseen_start);
            Some(co)
        }
        None => None,
    };
    // The engine's conditions for the term (the table is loaded by here): `colloquial::applies` is the engine's own test. `--tinylm-paired`
    // takes its model condition (the paired SHA-256) as met and keeps the profile one, so the term acts on the given model as it ships.
    let colloq_on = colloq
        .as_ref()
        .filter(|_| tl.as_ref().is_some_and(|(m, _, _)| colloquial::applies(prof, m) || (tinylm_paired && prof == Profile::Chat)));
    // A table loaded in chat whose term is not in effect is tagged `-colloq:` and named on stderr, so a check for `+colloq:` cannot
    // take it for the shipping setting (contract colloq-off-tag). Formal never applies the term, so its tag is left as it was.
    let colloq_off = prof == Profile::Chat && colloq.is_some() && colloq_on.is_none();
    if colloq_off {
        let why = if tl.is_none() { "no --tinylm is loaded" } else { "the --tinylm model is not the one the term is paired with" };
        eprintln!("warning: --colloquial-core is loaded but the colloquial term is off ({why}); the tag says -colloq:. To measure the term as it ships on this model, add --tinylm-paired.");
    }
    let (lex, overlay) = load_with_packs(&dir, packs, &pdir, extra_overlay.as_deref(), names)?;
    let demote_rows = fs::read_to_string(dir.join("demote.tsv")).map_err(|e| format!("cannot read demote.tsv ({:?})", e.kind()))?;
    let table = Demote::parse(&demote_rows).filter(|d| d.check(&lex)).ok_or("bad demote.tsv")?;
    // The table is always loaded, so a malformed one stops the run even with --no-demote.
    // The rows of the pack-oov packs (not `--extra-overlay`, not other packs): their words are unknown to the model and keep the pack's score (docs/spec/decoder.md §2.5).
    let pack_rows = read_packs_detailed(&pdir, packs & PACK_OOV).map_err(|e| e.to_string())?;
    let capped = CappedLexicon::with_packs_ceil(lex.clone(), &overlay, &pack_rows, &lm, Some(&table), unseen_ceil).ok_or("bad demote.tsv")?;
    // The reading term: the engine's own table (mcbpmf-data.txt for the mc estimate of the floor readings, std-exempt.tsv for the exemptions) and its tag.
    let (capped, rd_tag) = if reading_decode {
        let rd = load_reading_decode(&lex, &dir).map_err(|e| e.to_string())?;
        // The built-in table written out, then nothing else runs.
        if let Some(f) = &dump_reading_table {
            if Path::new(f).exists() {
                return Err("--dump-reading-table: the file exists (not overwritten)".into());
            }
            fs::write(f, rd.dump_table(&lex)).map_err(|e| format!("--dump-reading-table: cannot write the file ({:?})", e.kind()))?;
            return Ok(());
        }
        let mut tag = rd.tag();
        let rd = match &reading_table {
            Some(f) => {
                let bytes = fs::read(f).map_err(|e| format!("--reading-table: cannot read the file ({:?})", e.kind()))?;
                let text = String::from_utf8(bytes.clone()).map_err(|_| "--reading-table: not UTF-8".to_string())?;
                tag += &format!("+rt:{}", &sha256_hex(&bytes)[..8]);
                rd.with_table(&lex, &text).map_err(|e| format!("--reading-table: {e}"))?
            }
            None => rd,
        };
        (capped.with_reading(rd), tag)
    } else {
        (capped, String::new())
    };

    // `--accept` ties to the rows' original text; read here, where `dev` and `rows_file` are still whole (the holdout was refused above).
    let raw_rows = match (&accept, dev, &rows_file) {
        (Some(_), Some(n), None) => Some(dev_rows_raw(n)?),
        (Some(_), None, Some(f)) => Some(read_rows_raw(f, limit)?),
        _ => None,
    };
    // (display name, [(truth, reading)])
    let (default_name, rows): (String, Vec<(String, Syls, String)>) = match (dev, rows_file, set.as_deref()) {
        (Some(n), None, None) => (format!("dev{n}"), dev_rows(n)?),
        (None, Some(f), None) => {
            let mut rows = read_rows(&f)?;
            if let Some(n) = limit.filter(|&n| n != 0) {
                rows.truncate(n);
            }
            (Path::new(&f).file_name().map_or(String::new(), |s| s.to_string_lossy().into_owned()), rows)
        }
        (None, None, Some("holdout")) => {
            // Summary line and numbers-only rowstats: the sentences must never reach the output.
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
    // docs/spec/evaluation.md §3: the table is read only when the flag is given, and before any output file is created, so
    // a bad table never empties an existing --rowstats or other file.
    let forms = match &forms_rowstats {
        Some(_) => Some(load_forms()?),
        None => None,
    };
    // `--judge v2`: forms.tsv, forms-extra.tsv and the accept file are read (and the accept file checked against the rows) here, also before any output file.
    let v2 = if judge_v2 { Some(load_judge(accept.as_deref(), raw_rows.as_deref())?) } else { None };
    let jtag = v2.as_ref().map_or(String::new(), |j| j.tag.clone());
    let mut dump = match dump {
        Some(f) => Some(std::io::BufWriter::new(fs::File::create(f).map_err(|e| format!("cannot create dump ({:?})", e.kind()))?)),
        None => None,
    };
    let mut dump_words = match dump_words {
        Some(f) => Some(std::io::BufWriter::new(fs::File::create(f).map_err(|e| format!("cannot create word dump ({:?})", e.kind()))?)),
        None => None,
    };
    // Opened before decoding, like the dump: an unwritable path fails now, not after a whole (holdout) run.
    let rowstats = match rowstats {
        Some(f) => Some(fs::File::create(f).map_err(|e| format!("cannot create rowstats ({:?})", e.kind()))?),
        None => None,
    };
    let forms_rowstats = match forms_rowstats {
        Some(f) => Some(fs::File::create(f).map_err(|e| format!("cannot create forms rowstats ({:?})", e.kind()))?),
        None => None,
    };
    // Opened before decoding, like the dump.
    let mut picks = match picks {
        Some(f) => Some(std::io::BufWriter::new(fs::File::create(f).map_err(|e| format!("cannot create picks ({:?})", e.kind()))?)),
        None => None,
    };
    let mut mask_diff = match pool_mask_diff {
        Some(f) => Some(std::io::BufWriter::new(fs::File::create(f).map_err(|e| format!("cannot create pool mask diff ({:?})", e.kind()))?)),
        None => None,
    };
    let mut scores = match tinylm_scores {
        Some(f) => Some(std::io::BufWriter::new(fs::File::create(f).map_err(|e| format!("cannot create tinylm scores ({:?})", e.kind()))?)),
        None => None,
    };
    let (want_scores, want_dump, want_words, want_picks) = (scores.is_some(), dump.is_some(), dump_words.is_some(), picks.is_some());
    // `--threads N`: the rows are decoded in blocks of BLOCK contiguous rows; no more workers than blocks.
    const BLOCK: usize = 32;
    let (nrows, nblocks) = (rows.len(), rows.len().div_ceil(BLOCK));
    let threads = threads.min(nblocks.max(1));
    // One row, a function of the row alone (nothing carries over from one row to the next), so the rows can be decoded in any
    // order or at once. Everything the row writes is built here as text; the loop below puts it in the files, in row order.
    let recur_on = ctx_mode && recur;
    // docs/spec/semantic-rerank.md §3.1.1: the pool weight needs the decoder's term and a pool (a small model).
    let pool_on = recur_on && pool_recur && tl.is_some();
    let want_mask_diff = mask_diff.is_some();
    let work = |i: usize| -> Result<RowOut, String> {
        let (truth, syls, ctx) = &rows[i];
        // S2h: the first word is conditioned on the row's context, cut like the engine cuts it.
        let left = if ctx_mode { Some(context_key(ctx)) } else { None };
        let start = left.as_deref().filter(|k| *k != SENTINEL).map_or("<s>", |k| history(k, &lm));
        let mut out = RowOut::default();
        let grams = recur_on.then(|| RecurGrams::from_text(ctx));
        // docs/spec/decoder.md §6.6: the engine's position table (`tone3::positions`) over the row's syllables; empty with `--no-tone3`.
        let t3 = core::tone3::positions(syls, &lex, tone3);
        let t3_arg = tone3.then_some((t3.as_slice(), delta_t3));
        let scored = match decode_from_fuzzy_scored(&capped, syls, &lm, prof, BEAM_S1, start, demote, fuzzy.unwrap_or(0), delta_sound, grams.as_ref(), t3_arg) {
            Ok(nb) => nb,
            // --fuzzy: a row no reading covers is a wrong answer with an empty first candidate, and the set goes on.
            Err(Error::NoPath { .. }) if fuzzy.is_some() => {
                if want_words {
                    let gold = lex.segment_words(truth).map_or("-".to_string(), |ws| ws.iter().map(|(w, _)| w.as_str()).collect::<Vec<_>>().join(" "));
                    writeln!(out.words, "{}\t0\t{gold}", i + 1).unwrap();
                }
                if want_picks {
                    writeln!(out.picks, "{}\t-\t", i + 1).unwrap();
                }
                return Ok(out);
            }
            Err(e) => return Err(e.to_string()),
        };
        let mut surf: Vec<String> = scored.iter().map(|(_, ws)| ws.iter().map(|w| w.0).collect()).collect();
        let t = len.apply(truth);
        // The judgment of a candidate: v1 is the lenient comparison, v2 the judge of docs/spec/evaluation.md §3.5.
        let judge = v2.as_ref().map(|j| Judge2::new(&j.forms, len, truth, syls, j.accept.as_ref().map_or(&[][..], |a| a.get(i))));
        let ok = |s: &str| match &judge {
            Some(j) => j.ok(s),
            None => len.apply(s) == t,
        };
        // The row's chosen candidate: the first, or the small model's pick among the pool (below; the dump keeps the decoder's order).
        let mut chosen = 0;
        if let Some((tl, _, _)) = &tl {
            // The engine's rule (docs/spec/decoder.md §6.5, `rerank_pool`): the pool is the first 8 paths with no fuzzy word, in n-best order; a
            // fuzzy n-gram first path is not reranked (a pool of just that path: n 1, `c` 0, no `ll`). `pool` holds the paths' n-best indices.
            let pool: Vec<usize> = rerank_pool(&scored, TOPK).unwrap_or_else(|| vec![0]);
            let top0: Vec<&str> = pool.iter().map(|&p| surf[p].as_str()).collect();
            let ng0: Vec<f64> = pool.iter().map(|&p| scored[p].0).collect();
            // The key is the engine's (`TinyLm::key_for` of the row's context, the display before the one stretch being empty): the
            // context key for versions 1 to 4 (its SENTINEL is the empty k, in TinyLm::rerank), the last L characters as they are for
            // a model declaring L (docs/spec/semantic-rerank.md §5.2); without --context, no key.
            let t0 = Instant::now();
            let key = if ctx_mode { tl.key_for_len(ctx, top0.iter().take(TOPK).map(|c| c.chars().count()).max().unwrap_or(0)) } else { String::new() };
            let key = key.as_str();
            // The manual mask, once, in the decoder's order with its first path as the base (the engine's `Stretch::masked`; docs/spec/decoder.md §7):
            // `manual_mask` matches the manual words against the readings that path used.
            let used = if lex.manual().is_empty() { Vec::new() } else { lex.path_readings(syls, scored[pool[0]].1.iter().map(|w| (w.0, w.3)), &t3) };
            let masked0 = manual_mask(&used, &top0, lex.manual());
            // docs/spec/semantic-rerank.md §3.1.1: the pool weight and the order it gives (`lm::pool_order`, the engine's function); `view` holds the
            // pool positions in the order the rerank sees them (the pool's own when the weight does not apply).
            let bonus: Vec<f64> = top0.iter().map(|c| grams.as_ref().filter(|_| pool_on).map_or(0.0, |g| g.pool_bonus(c))).collect();
            let reordered = pool_order(&ng0, &bonus, &masked0);
            if want_mask_diff && pool_on {
                out.mask_diff = mask_differs(&reordered, pool_order(&ng0, &bonus, &[]).is_some(), &masked0, &top0, || {
                    lex.path_readings(syls, scored[pool[reordered.as_ref().map_or(0, |r| r.0[0])]].1.iter().map(|w| (w.0, w.3)), &t3)
                }, lex.manual());
            }
            let (view, ng): (Vec<usize>, Vec<f64>) = match &reordered {
                Some((o, s)) => (o.clone(), s.clone()),
                None => ((0..pool.len()).collect(), ng0.clone()),
            };
            // The n-best index of each candidate the rerank sees.
            let nbest: Vec<usize> = view.iter().map(|&k| pool[k]).collect();
            let top: Vec<&str> = view.iter().map(|&k| top0[k]).collect();
            let masked: Vec<bool> = view.iter().map(|&k| masked0[k]).collect();
            let picked = match colloq_on {
                Some(co) => {
                    let words: Vec<Vec<&str>> = nbest.iter().map(|&p| scored[p].1.iter().map(|w| w.0).collect()).collect();
                    tl.rerank_colloquial(&ng, &top, &words, key, co, level, &masked, &Default::default())
                }
                None => tl.rerank(&ng, &top, key, tl.params(prof, level), &masked, &Default::default()),
            };
            let ll = match picked {
                // A position outside the list is no choice: the rerank's first candidate.
                Ok((c, ll)) => {
                    chosen = nbest.get(c).copied().unwrap_or(nbest[0]);
                    ll
                }
                // A candidate longer than the model's context: this row keeps the n-gram first (counted on stderr), the set goes on.
                Err(TinyLmError::TooLong) => {
                    out.long = true;
                    None
                }
                Err(e) => return Err(e.to_string()),
            };
            if ll.is_some() {
                out.rerank = Some(t0.elapsed());
            }
            // `--tinylm-scores-topk N`: the candidates 9..N get an `ll` from `ll_many` under the key `TinyLm::rerank_ll` builds (SENTINEL = the
            // empty k; keep the two the same, the test scores_topk_extends_ll_and_changes_nothing_else pins it), apart from the pick, which stays
            // on the first 8. Scored in blocks of BATCH like `ll_many` does; a block with a candidate over the model's context is scored one by
            // one, so only that candidate loses its `ll` (null in the record, counted in `out.no_ll`).
            let mut extra: Vec<Option<f64>> = Vec::new();
            if want_scores && scores_topk.is_some_and(|n| n > TOPK) && ll.is_some() {
                let k = if key == SENTINEL { "" } else { key };
                let more: Vec<&str> = surf.iter().skip(TOPK).take(scores_topk.unwrap_or(TOPK) - TOPK).map(String::as_str).collect();
                for block in more.chunks(BATCH) {
                    let items: Vec<(&str, &str)> = block.iter().map(|c| (k, *c)).collect();
                    match tl.ll_many(&items) {
                        Ok(v) => extra.extend(v.into_iter().map(Some)),
                        Err(TinyLmError::TooLong) => {
                            for it in &items {
                                match tl.ll_many(std::slice::from_ref(it)) {
                                    Ok(v) => extra.push(Some(v[0])),
                                    Err(TinyLmError::TooLong) => {
                                        extra.push(None);
                                        out.no_ll += 1;
                                    }
                                    Err(e) => return Err(e.to_string()),
                                }
                            }
                        }
                        Err(e) => return Err(e.to_string()),
                    }
                }
            }
            if want_scores {
                // The engine's col, the one `rerank_colloquial` fused (same words, same table); only where it ran with an `ll`.
                let col: Option<Vec<f64>> = colloq_on.filter(|_| ll.is_some()).map(|co| nbest.iter().map(|&p| co.col(&scored[p].1.iter().map(|w| w.0).collect::<Vec<_>>())).collect());
                let oks: Vec<bool> = top.iter().map(|s| ok(s)).collect();
                let ed: Vec<u32> = top.iter().zip(&oks).map(|(s, &ok)| if ok { 0 } else { levenshtein(s, truth) }).collect();
                // With a sound bit, or a pool the recurrence weight reordered, the record lists the pool as the rerank saw it and says which n-best paths
                // it is (`idx`); without either the pool is the first 8, as before. `masked` is over the same list (the mask made with the decoder's
                // first path as the base) and `ng` is the score the rerank used (`ng + bonus` on a reordered row).
                let idx = (fuzzy.unwrap_or(0) & core::fuzzy::FUZZY_SOUND != 0 || reordered.is_some()).then_some(nbest.as_slice());
                out.scores = scores_line(i + 1, &ng, &oks, &ed, truth.chars().count(), chosen, ll.as_deref(), &extra, &masked, idx, col.as_deref())?;
            }
        }
        let pick = len.apply(&surf[chosen]);
        out.ok = ok(&surf[chosen]);
        if let Some(fm) = &forms {
            // Lenient first, then the forms table (a fixed order, docs/spec/evaluation.md §3); both sides use the row's own reading.
            let ((p, p_ran), (g, g_ran)) = (fm.apply_checked(&pick, syls), fm.apply_checked(&t, syls));
            out.forms_ok = p == g;
            out.forms_no_char_step = !(p_ran && g_ran);
        }
        out.in64 = surf.iter().any(|s| ok(s));
        if want_dump {
            for (r, ((sc, _), s)) in scored.iter().zip(&surf).enumerate() {
                writeln!(out.dump, "{}\t{}\t{s}\t{sc:?}", i + 1, r + 1).unwrap();   // a String: cannot fail
            }
        }
        if want_words {
            let gold = lex.segment_words(truth).map_or("-".to_string(), |ws| ws.iter().map(|(w, _)| w.as_str()).collect::<Vec<_>>().join(" "));
            writeln!(out.words, "{}\t0\t{gold}", i + 1).unwrap();
            for (r, (_, ws)) in scored.iter().enumerate() {
                writeln!(out.words, "{}\t{}\t{}", i + 1, r + 1, ws.iter().map(|w| w.0).collect::<Vec<_>>().join(" ")).unwrap();
            }
        }
        if want_picks {
            writeln!(out.picks, "{}\t{chosen}\t{}", i + 1, surf[chosen]).unwrap();
        }
        out.first = surf.swap_remove(chosen);
        Ok(out)
    };
    let (mut top1, mut o64, mut firsts, mut rs, mut rerank, mut long) = (0usize, 0usize, Vec::new(), Vec::new(), Vec::new(), 0usize);
    let mut no_ll = 0usize;
    let mut mask_diffs = 0usize;
    // Puts one row's output in the files and the counters; called in row order, so every file and the summary line are those of N = 1.
    let (mut frs, mut no_char_step) = (Vec::new(), 0usize);
    let mut emit = |i: usize, out: RowOut| -> Result<(), String> {
        top1 += out.ok as usize;
        o64 += out.in64 as usize;
        long += out.long as usize;
        no_ll += out.no_ll;
        if let (Some(w), true) = (mask_diff.as_mut(), out.mask_diff) {
            mask_diffs += 1;
            writeln!(w, "{}", i + 1).map_err(|_| "cannot write pool mask diff".to_string())?;
        }
        rerank.extend(out.rerank);
        if let Some(w) = scores.as_mut() {
            w.write_all(out.scores.as_bytes()).map_err(|_| "cannot write tinylm scores".to_string())?;
        }
        if let Some(d) = dump.as_mut() {
            d.write_all(out.dump.as_bytes()).map_err(|_| "cannot write dump".to_string())?;
        }
        if let Some(d) = picks.as_mut() {
            d.write_all(out.picks.as_bytes()).map_err(|_| "cannot write picks".to_string())?;
        }
        if let Some(d) = dump_words.as_mut() {
            d.write_all(out.words.as_bytes()).map_err(|_| "cannot write word dump".to_string())?;
        }
        if forms.is_some() {
            no_char_step += out.forms_no_char_step as usize;
            frs.push((out.forms_ok, out.first.clone(), rows[i].0.clone()));
        }
        rs.push((out.ok, out.first.clone(), rows[i].0.clone()));
        firsts.push(out.first);
        Ok(())
    };
    if threads == 1 {
        for i in 0..rows.len() {
            emit(i, work(i)?)?;
        }
    } else {
        // N workers take the next block of BLOCK rows from a shared counter and send its outputs back; the main thread writes the
        // blocks in row order, holding the ones that arrive early. The first error in row order is the error: a block stops at its
        // first error, workers claim no new block once any error is seen (a block already claimed is still decoded, so an earlier
        // error is not missed), and returning drops `rx`, which stops the workers at their next send. A panic in a row is turned into
        // that row's error. The early blocks held in memory are bounded: a worker does not start block b while
        // b >= written + lookahead (the lowest unwritten block is always within it, so the wait cannot deadlock).
        let lookahead = 4 * threads;
        let (next, written, failed, closed) = (AtomicUsize::new(0), AtomicUsize::new(0), AtomicBool::new(false), AtomicBool::new(false));
        std::thread::scope(|sc| {
            let (tx, rx) = mpsc::channel::<(usize, Vec<Result<RowOut, String>>)>();
            for _ in 0..threads {
                let (tx, work, next, written, failed, closed) = (tx.clone(), &work, &next, &written, &failed, &closed);
                sc.spawn(move || {
                    while !failed.load(Ordering::Relaxed) {
                        let b = next.fetch_add(1, Ordering::Relaxed);
                        if b >= nblocks {
                            return;
                        }
                        while b >= written.load(Ordering::Acquire) + lookahead {
                            if closed.load(Ordering::Relaxed) {
                                return;
                            }
                            std::thread::sleep(Duration::from_millis(1));
                        }
                        let mut outs = Vec::new();
                        for i in b * BLOCK..((b + 1) * BLOCK).min(nrows) {
                            let out = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| work(i)))
                                .unwrap_or_else(|_| Err(format!("row {} panicked in a --threads worker", i + 1)));
                            let bad = out.is_err();
                            outs.push(out);
                            if bad {
                                failed.store(true, Ordering::Relaxed);
                                break;
                            }
                        }
                        if tx.send((b, outs)).is_err() {
                            return;
                        }
                    }
                });
            }
            drop(tx);
            let _release = Release(&closed);   // also on a panic of this thread: the workers waiting in the lookahead loop leave
            let (mut early, mut want) = (BTreeMap::new(), 0);
            for (b, outs) in &rx {
                early.insert(b, outs);
                while let Some(outs) = early.remove(&want) {
                    for (i, out) in (want * BLOCK..).zip(outs) {
                        emit(i, out?)?;
                    }
                    want += 1;
                    written.store(want, Ordering::Release);
                }
            }
            Ok::<(), String>(())
        })?;
    }
    if let Some(mut w) = scores {
        w.flush().map_err(|_| "cannot write tinylm scores".to_string())?;
    }
    if let Some(mut w) = mask_diff {
        w.flush().map_err(|_| "cannot write pool mask diff".to_string())?;
    }
    if scores_topk.is_some_and(|n| n > TOPK) {
        // stderr only, in the style of the latency line below: candidates 9..N the small model could not score (`null` in the record).
        eprintln!("tinylm: --tinylm-scores-topk candidates without ll (candidate over the context) {no_ll}");
    }
    if let (Some((_, load, _)), false) = (&tl, rerank.is_empty()) {
        // Latency record, stderr only: one rerank = the forward pass over up to 8 candidates of a row (rows with fewer
        // than two candidates run nothing and are not counted); load = read + parse of the .sjtl, the file loaded once.
        rerank.sort();
        let ms = |d: Duration| d.as_secs_f64() * 1000.0;
        eprintln!(
            "tinylm: rerank n={} p50={:.3} ms p95={:.3} ms; rows kept first (candidate over the context) {long}; load {:.0} ms, rss {:?} -> {:?} KiB around the .sjtl load{}",
            rerank.len(),
            ms(rerank[rerank.len() / 2]),
            ms(rerank[(rerank.len() as f64 * 0.95) as usize]),
            ms(*load),
            rss_before,
            rss_after,
            // The percentiles of a parallel run are measured under contention; N = 1 prints the line as before (tools/bench.py parses it).
            if threads > 1 { format!(" (threads {threads}: measured under contention)") } else { String::new() }
        );
    }
    if let Some(mut d) = dump {
        d.flush().map_err(|_| "cannot write dump".to_string())?;
    }
    if let Some(mut d) = dump_words {
        d.flush().map_err(|_| "cannot write word dump".to_string())?;
    }
    if let Some(mut d) = picks {
        d.flush().map_err(|_| "cannot write picks".to_string())?;
    }
    if let Some(mut f) = rowstats {
        f.write_all(format_rowstats(&rs).as_bytes()).map_err(|e| format!("cannot write rowstats ({:?})", e.kind()))?;
    }
    if let Some(mut f) = forms_rowstats {
        f.write_all(format_rowstats(&frs).as_bytes()).map_err(|e| format!("cannot write forms rowstats ({:?})", e.kind()))?;
        eprintln!("forms: {no_char_step} rows without the single-character step (syllable count differs from the sentence length)");
    }
    let sha = sha256_hex(firsts.join("\n").as_bytes());
    // A non-standard level is a different setting from the shipping one: tools/bench.py compares the whole tag, so it fails their cells.
    let rl_tag = match level {
        RerankLevel::Standard => "",
        RerankLevel::Conservative => "+rl:conservative",
        RerankLevel::Aggressive => "+rl:aggressive",
    };
    // `--pool-mask-diff`: the number of rows it wrote, the last key of the summary (absent without the option).
    let md = if want_mask_diff { format!(", 'pool_mask_diff': {mask_diffs}") } else { String::new() };
    // `+t3c`: third-tone and colloquial sandhi; `:dD` and `:nD` for a `DELTA_TONE3` and a `DELTA_NEUTRAL` that are not the defaults.
    let n_tag = neutral_delta.filter(|d| *d != core::tone3::DELTA_NEUTRAL).map_or(String::new(), |d| format!(":n{d}"));
    let t3_tag = match (tone3, delta_t3 == core::tone3::DELTA_TONE3) {
        (false, _) => String::new(),
        (true, true) => format!("+t3c{n_tag}"),
        (true, false) => format!("+t3c:d{delta_t3}{n_tag}"),
    };
    let rd_tag = format!("{rd_tag}{}{t3_tag}", unseen_ceil.map_or(String::new(), |k| format!("+uc:{k}")));
    let fz = fuzzy.map_or(String::new(), |f| if delta_sound == core::fuzzy::DELTA_SOUND { format!("+fz:{f}") } else { format!("+fz:{f}:d{delta_sound}") });
    println!("## {name}  lm-{profile_name}{}{}{}{}{}{}{}{}{}{xo}{}{}{fz}{}{jtag}  {{'n': {}, 'top1': {top1}, 'oracle@64': {o64}, 'top1_sha256': '{sha}'{md}}}", if ctx_mode { "+ctx" } else { "" }, if recur_on { if pool_on { "+recur+pr" } else { "+recur" } } else { "" }, if demote { "" } else { "-nodemote" }, if classes { "" } else { "-noclasses" }, if names { "" } else { "-nonames" }, lm.kn_tag(prof).unwrap_or_default(), tl.as_ref().map_or(String::new(), |(m, _, sha8)| format!("+tl:{sha8}{}", m.boundary_k().map_or(String::new(), |k| format!("+sep:{k}")))), rl_tag, pack_tag(packs), if lm.uses_tri(scoring) { lm.tri_tag(prof).unwrap_or("") } else { "" }, colloq.as_ref().map_or(String::new(), |co| format!("{}colloq:{}{}", if colloq_off { "-" } else { "+" }, &co.sha256()[..8], if co.first_unigram() { "+fu" } else if co.unseen_start_unigram() { "" } else { "+us0" }) + &tl.as_ref().filter(|_| tinylm_paired).map_or(String::new(), |(_, _, sha8)| format!("+pair:{sha8}"))), rd_tag, rows.len());
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

/// V3 core (docs/spec/prediction.md §2): `--predict <file> --lm <file> --profile chat|formal [--predict-time]`.
/// The file is eval/golden/sp-predict.txt (only its `## ` query lines are read); output has the same format.
/// `--packs acg` / `--packs-dir DIR` as in `lm`: the pack's words join the lexicon and the cap.
/// `--predict-time` prints p50/p95/max of the predict calls (index build excluded) and the index cost on stderr
/// (RSS before and after one index, and after a second one).
fn run_predict(args: &[String]) -> Result<(), String> {
    let (mut file, mut lm_path, mut profile, mut time) = (None, None, None, false);
    let (mut packs, mut packs_dir, mut kn) = (0u32, None::<String>, true);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut val = || it.next().cloned().ok_or_else(|| "missing option value".to_string());
        match a.as_str() {
            "--predict" => file = Some(val()?),
            "--lm" => lm_path = Some(val()?),
            "--profile" => profile = Some(val()?),
            "--predict-time" => time = true,
            "--no-kn" => kn = false,
            "--packs" => packs |= pack_flag(&val()?)?,
            "--packs-dir" => packs_dir = Some(val()?),
            _ => return Err("unknown argument".into()),
        }
    }
    check_lm_opts(None, false, false, packs, packs_dir.is_some())?;
    let pdir = packs_dir_checked(packs, packs_dir)?;
    let profile = match profile.ok_or("--profile is required")?.as_str() {
        "chat" => Profile::Chat,
        "formal" => Profile::Formal,
        _ => return Err("--profile must be chat or formal".into()),
    };
    // A kn.sjkn beside the model turns the Kneser-Ney term on unless --no-kn, as in the other modes: the App's prediction row
    // scores with it (Engine::recompute_pred -> predict -> Lm::word_by_id), at the profile's beta like decoding (formal 0.75,
    // chat the file's), and the Python reference (reference/proto/predict.py) loads it the same way for chat.
    // The trigram table is never read here: the prediction row scores with the bigram whatever the profile (docs/spec/decoder.md §3.4 (d)).
    let lm = Lm::load_opts(Path::new(&lm_path.ok_or("--lm is required")?), true, kn, &[]).map_err(|e| e.to_string())?;
    let dir = root().join("data/lexicon");
    let (lex, overlay) = load_with_packs(&dir, packs, &pdir, None, true)?;
    // Prediction scores use the capped lp only; demotion applies to decoding (docs/spec/decoder.md §2.4).
    let pack_rows = read_packs_detailed(&pdir, packs & PACK_OOV).map_err(|e| e.to_string())?;
    let capped = CappedLexicon::with_packs(lex.clone(), &overlay, &pack_rows, &lm, None).ok_or("cannot build the capped lexicon")?;
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
        let cands = predict(&idx, &lm, lm.scoring(profile), v, &u, mode, 9);
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

/// The three abbreviated typing styles `--abbr` measures: A initials only, B no initial, C no tone key.
#[derive(Clone, Copy, PartialEq, Debug)]
enum Abbr {
    A,
    B,
    C,
}

const ABBR_TONES: [char; 4] = ['ˊ', 'ˇ', 'ˋ', '˙'];

fn is_zhuyin(c: char) -> bool {
    ('ㄅ'..='ㄩ').contains(&c)
}

/// The symbols typed for one syllable of a reading. No style types the tone (the neutral mark included). A keeps the first symbol (the
/// initial, or the first symbol of a syllable without one); B drops the initial unless it is the whole syllable; C keeps every symbol.
fn abbr_syllable(syl: &str, style: Abbr) -> String {
    let s: Vec<char> = syl.chars().filter(|c| !ABBR_TONES.contains(c)).collect();
    let initial = s.first().is_some_and(|c| ('ㄅ'..='ㄙ').contains(c));
    match style {
        Abbr::A => s.iter().take(1).collect(),
        Abbr::B if initial && s.len() > 1 => s[1..].iter().collect(),
        _ => s.into_iter().collect(),
    }
}

/// The column of a Zhuyin symbol: 0 initial, 1 medial (ㄧㄨㄩ), 2 final. No style types a tone, so there is no fourth column here.
fn abbr_column(c: char) -> usize {
    match c {
        'ㄅ'..='ㄙ' => 0,
        'ㄧ' | 'ㄨ' | 'ㄩ' => 1,
        _ => 2,
    }
}

/// How the abbreviation composer joins `units` (one per syllable, in typing order), simulated with its rule: a symbol goes into the last
/// unit's column when that column is empty, otherwise it opens a new unit (docs/spec/prediction.md §6). A merge is the first symbol of a
/// syllable landing in the unit before it. `None`: no merge; `Some(true)`: every merge is backward (the symbol's column comes before the
/// last filled column of that unit, as ㄍ after ㄧ in 一個 typed A); `Some(false)`: at least one merge is forward (ㄢ after ㄒㄧ in 西安
/// typed C). `run_abbr` checks that this predicts exactly the words the engine's composer merges.
fn abbr_merge_kind(units: &[String]) -> Option<bool> {
    let (mut filled, mut any, mut forward) = ([false; 3], false, false);
    for u in units {
        for (k, c) in u.chars().enumerate() {
            let col = abbr_column(c);
            if filled[col] || !filled.iter().any(|&f| f) {
                filled = [false; 3];
            } else if k == 0 {
                any = true;
                forward |= filled[col + 1..].iter().all(|&f| !f);
            }
            filled[col] = true;
        }
    }
    any.then_some(!forward)
}

/// `--abbr-mix` keeps at most this many cuts of one word's symbols.
const ABBR_MIX_CUTS: usize = 64;

/// Every cut of `symbols` (in typing order) into units that keeps the typing order: inside a unit the columns (`abbr_column`) only go
/// strictly forward, so a symbol whose column is not after the one typed before it must open a unit, and any other symbol may join the
/// unit or open one. No cut has more than `ABBR_MAX_UNITS` units. Order: the cut that joins wherever it may first, then by the number of
/// units, a later split before an earlier one. At most `ABBR_MIX_CUTS` cuts; the flag says the list was cut short there.
fn abbr_cuts(symbols: &[char]) -> (Vec<Vec<String>>, bool) {
    let free = |i: usize| abbr_column(symbols[i]) > abbr_column(symbols[i - 1]);
    // The places a split is a choice, the latest first.
    let opt: Vec<usize> = (1..symbols.len()).rev().filter(|&i| free(i)).collect();
    let forced = symbols.len().saturating_sub(1) - opt.len();
    let mut out: Vec<Vec<String>> = Vec::new();
    if symbols.is_empty() {
        return (out, false);
    }
    for j in 0..=opt.len() {
        if forced + 1 + j > core::engine::ABBR_MAX_UNITS {
            break;
        }
        // Every choice of `j` places of `opt`, in lexicographic order of their indexes.
        let mut pick: Vec<usize> = (0..j).collect();
        loop {
            if out.len() == ABBR_MIX_CUTS {
                return (out, true);
            }
            let mut cut: Vec<String> = Vec::new();
            for (i, &c) in symbols.iter().enumerate() {
                if i == 0 || !free(i) || pick.iter().any(|&p| opt[p] == i) {
                    cut.push(String::new());
                }
                cut.last_mut().expect("the first symbol opens a unit").push(c);
            }
            out.push(cut);
            let Some(t) = (0..j).rev().find(|&t| pick[t] != t + opt.len() - j) else { break };
            pick[t] += 1;
            for u in t + 1..j {
                pick[u] = pick[u - 1] + 1;
            }
        }
    }
    (out, false)
}

/// Greedy longest match, left to right: (start, length, found). `has(start, length)` says whether the lexicon has the characters with the
/// syllables of that span as one entry. A position where not even the single character is an entry is one piece of length 1, not found.
fn abbr_segment(n: usize, has: impl Fn(usize, usize) -> bool) -> Vec<(usize, usize, bool)> {
    let (mut out, mut i) = (Vec::new(), 0);
    while i < n {
        let len = (1..=n - i).rev().find(|&l| has(i, l));
        out.push((i, len.unwrap_or(1), len.is_some()));
        i += len.unwrap_or(1);
    }
    out
}

/// Style B's offline sets (no engine): every lexicon entry under the `-`-joined B rewriting of its reading, each list in the prediction
/// index's order (-lp, word length, reading, word) with a word listed once, at its best compatible reading. `readings` is
/// `CappedLexicon::readings`; a reading with a syllable that has no Zhuyin symbol is left out, as in the index.
fn abbr_b_sets<'a>(readings: impl Iterator<Item = (Vec<&'a str>, Vec<(&'a str, f64)>)>) -> HashMap<String, Vec<&'a str>> {
    let mut sets: HashMap<String, Vec<(f64, usize, String, &str)>> = HashMap::new();
    for (key, words) in readings {
        let units: Vec<String> = key.iter().map(|s| abbr_syllable(s, Abbr::B)).collect();
        if units.iter().any(String::is_empty) {
            continue;
        }
        let list = sets.entry(units.join("-")).or_default();
        list.extend(words.into_iter().map(|(w, lp)| (lp, w.chars().count(), key.join("-"), w)));
    }
    sets.into_iter()
        .map(|(k, mut v)| {
            v.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal).then(a.1.cmp(&b.1)).then_with(|| a.2.cmp(&b.2)).then(a.3.cmp(b.3)));
            let mut seen = std::collections::HashSet::new();
            (k, v.into_iter().filter(|e| seen.insert(e.3)).map(|e| e.3).collect())
        })
        .collect()
}

/// 1-based rank of `word` in the offline set of its B units; `None` when the set does not hold it.
fn abbr_b_rank(sets: &HashMap<String, Vec<&str>>, b_units: &[String], word: &str) -> Option<usize> {
    sets.get(&b_units.join("-"))?.iter().position(|w| *w == word).map(|p| p + 1)
}

/// The engine `--abbr` types into: `replay::build_engine`'s (standard layout, the App's default packs, chat, English mixing and learning off)
/// without the small model, the colloquial table and the English table (none of them is read by the row of unfinished units), with the
/// prediction row and the abbreviation composer on.
fn abbr_engine(lm: &Path) -> Result<Engine, String> {
    let mut e = cli::replay::build_engine_packs(&root(), lm, None, None, Profile::Chat, false, true, cli::replay::PACKS)?;
    e.set_prediction(true).map_err(|_| "cannot switch the prediction row on".to_string())?;
    e.set_abbreviation(true).map_err(|_| "cannot switch the abbreviation composer on".to_string())?;
    Ok(e)
}

/// One word typed from a clean engine: `reset`, the left context, then the units' symbols as keys of the standard layout. Returns the
/// units the composer made (`Engine::pending_units`) and the prediction row after the last key.
fn abbr_type(e: &mut Engine, left: &str, units: &[String]) -> Result<(Vec<String>, Vec<String>), String> {
    e.reset(ResetMode::Discard);
    e.set_left_context(left);
    let mut row = Vec::new();
    for c in units.iter().flat_map(|u| u.chars()) {
        let k = Layout::Standard.key_of_symbol(c).ok_or("a reading holds a symbol with no key")?;
        let o = e.key(Key::ch(k, 0)).map_err(|_| "engine error on a key".to_string())?;
        if o.selected.is_some() || !o.commit.is_empty() {
            return Err("a Zhuyin key opened a window or committed text".into());
        }
        row = o.candidates;
    }
    let got = e.pending_units();
    // The composer may regroup the symbols, never lose one: anything else is not the abbreviation composer (the caller must not count it as a merge).
    let sorted = |u: &[String]| {
        let mut v: Vec<char> = u.iter().flat_map(|s| s.chars()).collect();
        v.sort_unstable();
        v
    };
    if sorted(&got) != sorted(units) {
        return Err("the composer did not keep the typed symbols as unfinished units".into());
    }
    Ok((got, row))
}

/// One measured item of `--abbr`: a segmented word, or a character no entry covers.
struct AbbrItem {
    len: usize,
    /// Index into `ABBR_EXCLUDED`; `None`: measurable.
    excluded: Option<usize>,
    b_eq_c: bool,
    /// 1-based rank in the engine's row (at most 9) and in the unlimited `predict` list.
    row_rank: Option<usize>,
    full_rank: Option<usize>,
    keys_full: usize,
    keys_abbr: usize,
    /// Style B's offline rank; the outer `None`: not looked up (another style, a stray character, a Zhuyin-letter word, over 10 units).
    b_rank: Option<Option<usize>>,
    /// For a composer merge (`excl_merged`): `true` when every merge in the word is backward (`abbr_merge_kind`).
    merge_backward: Option<bool>,
}

/// Exclusion reasons in the order they are tested (the first that holds counts).
const ABBR_EXCLUDED: [&str; 4] = ["excl_zhuyin_word", "excl_unsegmented", "excl_over10", "excl_merged"];

/// (rank 1, ranks 2-9, rank 10 and later, absent, sum of 1/rank over ranks 1-9) of a list of ranks.
fn abbr_buckets(ranks: impl Iterator<Item = Option<usize>>) -> ([usize; 4], f64) {
    let (mut b, mut rr) = ([0usize; 4], 0.0);
    for r in ranks {
        b[match r {
            Some(1) => 0,
            Some(2..=9) => 1,
            Some(_) => 2,
            None => 3,
        }] += 1;
        rr += r.filter(|&r| r <= 9).map_or(0.0, |r| 1.0 / r as f64);
    }
    (b, rr)
}

/// One summary line body for a subset of items. `top1`, `top9` and `mrr` read the engine's row; the four `rank*` buckets the unlimited list.
/// The key counts cover the measurable words: Zhuyin and tone keys only (first tone: the space bar), no key that selects from the row.
fn abbr_summary(items: &[&AbbrItem]) -> String {
    let ok: Vec<&&AbbrItem> = items.iter().filter(|i| i.excluded.is_none()).collect();
    let mut s = format!("{{'words': {}", items.len());
    for (k, name) in ABBR_EXCLUDED.iter().enumerate() {
        s += &format!(", '{name}': {}", items.iter().filter(|i| i.excluded == Some(k)).count());
    }
    let ((row, rr), (full, _)) = (abbr_buckets(ok.iter().map(|i| i.row_rank)), abbr_buckets(ok.iter().map(|i| i.full_rank)));
    let mrr = if ok.is_empty() { 0.0 } else { rr / ok.len() as f64 };
    s += &format!(", 'measurable': {}, 'top1': {}, 'top9': {}, 'mrr': {mrr:.4}", ok.len(), row[0], row[0] + row[1]);
    s += &format!(", 'rank1': {}, 'rank2_9': {}, 'rank10plus': {}, 'absent': {}", full[0], full[1], full[2], full[3]);
    s + &format!(", 'keys_full': {}, 'keys_abbr': {}}}", ok.iter().map(|i| i.keys_full).sum::<usize>(), ok.iter().map(|i| i.keys_abbr).sum::<usize>())
}

/// Style B's offline line body for a subset: the words looked up, how many are missing from their own set (must be 0), and the ranks.
fn abbr_b_summary(items: &[&AbbrItem]) -> String {
    let ranks: Vec<Option<usize>> = items.iter().filter_map(|i| i.b_rank).collect();
    let (b, rr) = abbr_buckets(ranks.iter().copied());
    let mrr = if ranks.is_empty() { 0.0 } else { rr / ranks.len() as f64 };
    format!("{{'words': {}, 'missing': {}, 'top1': {}, 'top9': {}, 'mrr': {mrr:.4}, 'rank1': {}, 'rank2_9': {}, 'rank10plus': {}}}", ranks.len(), b[3], b[0], b[0] + b[1], b[0], b[1], b[2])
}

/// One candidate as `predict` returns it: (word, score, is a successor of the history word, reading).
type AbbrCand = (String, f64, bool, Vec<String>);

/// `--abbr-mix`: the candidates of several cuts as one list, each with the index of the list it came from. A word is kept once, at its
/// best entry (a successor before a non-successor, then the higher score; equal: the earlier list). The order is `tiered`'s of
/// core/src/predict.rs: successors first, then the higher score, the shorter word, the reading, the word. The reading is compared as its
/// `-`-joined text, not by the index's reading number (which the tool cannot see); the numbers follow that text's order, so it is the same order.
fn abbr_mix<'a>(lists: &[&'a [AbbrCand]]) -> Vec<(&'a AbbrCand, usize)> {
    let mut best: HashMap<&str, (&AbbrCand, usize)> = HashMap::new();
    for (k, list) in lists.iter().enumerate() {
        for c in list.iter() {
            let better = |o: &AbbrCand| (c.2 && !o.2) || (c.2 == o.2 && c.1 > o.1);
            if best.get(c.0.as_str()).is_none_or(|(o, _)| better(o)) {
                best.insert(&c.0, (c, k));
            }
        }
    }
    let mut out: Vec<(&AbbrCand, usize)> = best.into_values().collect();
    out.sort_by(|(a, _), (b, _)| {
        (b.2.cmp(&a.2))
            .then(b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal))
            .then_with(|| a.0.chars().count().cmp(&b.0.chars().count()))
            .then_with(|| a.3.join("-").cmp(&b.3.join("-")))
            .then_with(|| a.0.cmp(&b.0))
    });
    out
}

/// `--abbr-beta`: the knives of each cut, the units it has beyond the cut that joins wherever it may (the one with the fewest units;
/// `abbr_cuts` always keeps it). That is the number of optional split places the cut uses: a forced split is in every cut, so it is no knife.
fn abbr_knives(cuts: &[Vec<String>]) -> Vec<usize> {
    let joined = cuts.iter().map(Vec::len).min().unwrap_or(0);
    cuts.iter().map(|c| c.len() - joined).collect()
}

/// `--abbr-beta`: `abbr_mix` on adjusted scores. An entry of `lists[k]` scores `beta * knives[k]` less than `predict` gave it; the adjusted
/// score picks a word's best entry and orders the list. Successors still come first, so `beta` only moves entries inside a tier. Written
/// apart from `abbr_mix` on purpose: `run_abbr` checks that `beta` 0 gives what `abbr_mix` gives.
fn abbr_mix_beta<'a>(lists: &[&'a [AbbrCand]], knives: &[usize], beta: f64) -> Vec<(&'a AbbrCand, usize)> {
    let adj = |c: &AbbrCand, k: usize| c.1 - beta * knives[k] as f64;
    let mut best: HashMap<&str, (&AbbrCand, usize)> = HashMap::new();
    for (k, list) in lists.iter().enumerate() {
        for c in list.iter() {
            let better = |&(o, ko): &(&AbbrCand, usize)| (c.2 && !o.2) || (c.2 == o.2 && adj(c, k) > adj(o, ko));
            if best.get(c.0.as_str()).is_none_or(better) {
                best.insert(&c.0, (c, k));
            }
        }
    }
    let mut out: Vec<(&AbbrCand, usize)> = best.into_values().collect();
    out.sort_by(|&(a, ka), &(b, kb)| {
        (b.2.cmp(&a.2))
            .then(adj(b, kb).partial_cmp(&adj(a, ka)).unwrap_or(std::cmp::Ordering::Equal))
            .then_with(|| a.0.chars().count().cmp(&b.0.chars().count()))
            .then_with(|| a.3.join("-").cmp(&b.3.join("-")))
            .then_with(|| a.0.cmp(&b.0))
    });
    out
}

/// The unlimited `predict` list of each cut (all units unfinished; `Mode::PA` with two or more units, `Mode::P` with one), on the engine's
/// own index, model and scoring, with the history word of `left`.
fn abbr_cut_lists(e: &Engine, left: &str, cuts: &[Vec<String>]) -> Result<Vec<Vec<AbbrCand>>, String> {
    e.with_predict_parts(|idx, lm, cfg| {
        let key = context_key(left);
        let v = history(if key == SENTINEL { "" } else { &key }, lm);
        cuts.iter()
            .map(|cut| {
                let u: Vec<Unit> = cut.iter().map(|u| Unit { chars: u.chars().collect(), done: false, tone: None }).collect();
                predict(idx, lm, cfg, v, &u, if u.len() > 1 { Mode::PA } else { Mode::P }, usize::MAX)
            })
            .collect()
    })
    .ok_or_else(|| "the engine has no model".to_string())
}

/// One word of `--abbr-mix`.
struct AbbrMix {
    /// A composer merge (`excl_merged` without the option).
    merged: bool,
    /// Before mixing, measurable words only: the 1-based rank in the engine's row (at most 9) and in its unlimited `predict` list.
    row_before: Option<usize>,
    full_before: Option<usize>,
    /// The 1-based rank in the mixed list.
    after: Option<usize>,
    /// The mixed list's entry of the word is the one the answer's own cut (a unit per character) gives.
    by_answer_cut: bool,
    cuts: usize,
    capped: bool,
    /// The answer's own cut is not among the cuts kept (only possible when `capped`).
    answer_cut_dropped: bool,
}

/// The counts of the mixed list over a subset: [words, rank 1, ranks 2-9, rank 10 and later, absent, in the first nine by the answer's
/// own cut, in the first nine by another cut], and the sum of 1/rank over ranks 1-9.
fn abbr_mix_counts(m: &[&AbbrMix]) -> ([usize; 7], f64) {
    let (b, rr) = abbr_buckets(m.iter().map(|x| x.after));
    let own = m.iter().filter(|x| x.after.is_some_and(|r| r <= 9) && x.by_answer_cut).count();
    ([m.len(), b[0], b[1], b[2], b[3], own, b[0] + b[1] - own], rr)
}

fn abbr_mix_summary(m: &[&AbbrMix]) -> String {
    let (c, rr) = abbr_mix_counts(m);
    let mrr = if m.is_empty() { 0.0 } else { rr / m.len() as f64 };
    let s = format!("{{'words': {}, 'top1': {}, 'top9': {}, 'mrr': {mrr:.4}, 'rank1': {}, 'rank2_9': {}, 'rank10plus': {}, 'absent': {}", c[0], c[1], c[1] + c[2], c[1], c[2], c[3], c[4]);
    s + &format!(", 'top9_by_answer_cut': {}, 'top9_by_other_cut': {}}}", c[5], c[6])
}

/// How the rank of each word moved from `before` to `after` (no rank is the worst): [better, same, worse, and the worse ones by where the
/// mixed list has them: ranks 2-9, rank 10 and later, absent].
fn abbr_mix_moved(m: &[&AbbrMix], before: impl Fn(&AbbrMix) -> Option<usize>, after: impl Fn(&AbbrMix) -> Option<usize>) -> [usize; 6] {
    let mut c = [0usize; 6];
    for x in m {
        let (b, a) = (before(x).unwrap_or(usize::MAX), after(x).unwrap_or(usize::MAX));
        c[match a.cmp(&b) {
            std::cmp::Ordering::Less => 0,
            std::cmp::Ordering::Equal => 1,
            std::cmp::Ordering::Greater => 2,
        }] += 1;
        if a > b {
            c[match x.after {
                Some(2..=9) => 3,
                Some(_) => 4,
                None => 5,
            }] += 1;
        }
    }
    c
}

/// The lines `--abbr-mix` adds (docs/spec/prediction.md §6.1), or the sum that does not add up.
fn abbr_mix_lines(mixes: &[AbbrMix], tag: &str) -> Result<Vec<String>, String> {
    let all: Vec<&AbbrMix> = mixes.iter().collect();
    let (ok, merged): (Vec<&AbbrMix>, Vec<&AbbrMix>) = all.iter().copied().partition(|x| !x.merged);
    let (ca, co, cm) = (abbr_mix_counts(&all).0, abbr_mix_counts(&ok).0, abbr_mix_counts(&merged).0);
    if (0..7).any(|k| ca[k] != co[k] + cm[k]) || [ca, co, cm].iter().any(|c| c[1] + c[2] + c[3] + c[4] != c[0]) {
        return Err("--abbr-mix: the groups do not add up".into());
    }
    let mut lines: Vec<String> = [("all", &all), ("measurable", &ok), ("merged", &merged)].iter().map(|(g, m)| format!("## mix:{g}  {tag}  {}", abbr_mix_summary(m))).collect();
    // The measurable words, before and after mixing: by the row (the first nine; beyond it is no rank) and by the unlimited lists.
    let row = abbr_mix_moved(&ok, |x| x.row_before, |x| x.after.filter(|&r| r <= 9));
    let full = abbr_mix_moved(&ok, |x| x.full_before, |x| x.after);
    if [row, full].iter().any(|c| c[0] + c[1] + c[2] != ok.len() || c[3] + c[4] + c[5] != c[2]) {
        return Err("--abbr-mix: the moved words do not add up".into());
    }
    let moved = |c: [usize; 6]| format!("'better': {}, 'same': {}, 'worse': {}, 'worse_rank2_9': {}, 'worse_rank10plus': {}, 'worse_absent': {}", c[0], c[1], c[2], c[3], c[4], c[5]);
    lines.push(format!("## mix:moved-row  {tag}  {{'words': {}, {}}}", ok.len(), moved(row)));
    lines.push(format!("## mix:moved-full  {tag}  {{'words': {}, {}}}", ok.len(), moved(full)));
    let n = |f: fn(&AbbrMix) -> bool| all.iter().filter(|x| f(x)).count();
    lines.push(format!(
        "## mix:cuts  {tag}  {{'words': {}, 'cuts': {}, 'one_cut': {}, 'max_cuts': {ABBR_MIX_CUTS}, 'capped': {}, 'answer_cut_dropped': {}}}",
        all.len(),
        all.iter().map(|x| x.cuts).sum::<usize>(),
        n(|x| x.cuts == 1),
        n(|x| x.capped),
        n(|x| x.answer_cut_dropped)
    ));
    Ok(lines)
}

/// One line of `--abbr-mix-dump`, 16 tab-separated columns, one line per word of `## mix:all`:
/// 1-4 `mode`, `history`, `-`, `units`: the query of the answer's own cut, the columns of a `--abbr-dump` (`--predict`) query line without its `## `;
/// 5 `word`: the answer;
/// 6 `class`: `measurable` or `merged` (a composer merge), the split of `## mix:measurable` and `## mix:merged`;
/// 7 `before`: the answer's 1-based rank before mixing, in the unlimited `predict` list of its own cut (what `## mix:moved-full` reads; over 9 is
///   no rank for `## mix:moved-row`); `-` for a merged word, which had no rank before mixing;
/// 8-16 `top1`…`top9`: the first nine words of the mixed list (`abbr_mix`, no β), an empty column where the list is shorter.
fn abbr_mix_dump_line(query: &str, word: &str, merged: bool, before: Option<usize>, top: &[&str]) -> String {
    let mut s = format!("{query}\t{word}\t{}\t{}", if merged { "merged" } else { "measurable" }, before.map_or("-".to_string(), |r| r.to_string()));
    for k in 0..9 {
        s.push('\t');
        s += top.get(k).copied().unwrap_or("");
    }
    s + "\n"
}

/// `--abbr-time`: the day of the synthetic records and of the engine that reads them (as `DAY` of core/tests/engine_predict.rs), so no record
/// has decayed.
const ABBR_TIME_DAY: i64 = 20_000;

/// `--abbr-time`: fills `dir` with the learning file of core/tests/engine_predict.rs `timing_typing76_keys_with_taught_picks_and_50k` and
/// leaves `e` reading it with learning on. As that test does (`teach_typing76_picks`, `synthetic_records`), on eval/dev/user-typing.txt: for each
/// sample, after the first key of its first syllable and after the first key of its second, the row's second item is picked (Tab, Right,
/// Enter) and committed (Enter), with the composer off; then synthetic records up to `CAPACITY` on the samples' readings (every run of 1 to 4
/// syllables) under two-character keys. The engine is `--abbr`'s (packs, chat), not the test's. Returns the number of records `e` holds.
fn abbr_time_store(e: &mut Engine, dir: &Path) -> Result<usize, String> {
    let (l, bad) = (Layout::Standard, |_| "engine error on a key".to_string());
    let text = fs::read_to_string(root().join("eval/dev/user-typing.txt")).map_err(|e| format!("cannot read eval/dev/user-typing.txt ({:?})", e.kind()))?;
    let lines: Vec<Vec<&str>> = text.lines().filter(|l| !l.is_empty()).map(|l| l.split('|').collect()).collect();
    if lines.iter().any(|f| f.len() != 3) {
        return Err("eval/dev/user-typing.txt: a line is not context|sentence|reading".into());
    }
    // A syllable's keys; the first tone ends with the space bar.
    let syl_keys = |syl: &str| -> Vec<Key> {
        let mut v: Vec<Key> = syl.chars().filter_map(|c| if ABBR_TONES.contains(&c) { l.key_of_tone(c) } else { l.key_of_symbol(c) }).map(|k| Key::ch(k, 0)).collect();
        if !syl.chars().any(|c| ABBR_TONES.contains(&c)) {
            v.push(Key::new(KeyKind::Space));
        }
        v
    };
    e.reset(ResetMode::Discard);
    e.set_today(Some(ABBR_TIME_DAY));
    e.set_learning(true);
    e.set_abbreviation(false).map_err(bad)?;
    e.learning_open(dir).map_err(|_| "cannot open the learning directory".to_string())?;
    for f in &lines {
        let syls: Vec<&str> = f[2].split(' ').collect();
        for upto in 0..syls.len().min(2) {
            e.set_left_context(f[0]);
            let mut keys: Vec<Key> = syls[..upto].iter().flat_map(|y| syl_keys(y)).collect();
            keys.extend(syl_keys(syls[upto]).into_iter().take(1));
            let mut row = 0;
            for k in keys {
                row = e.key(k).map_err(bad)?.candidates.len();
            }
            if row > 1 {
                for k in [KeyKind::Tab, KeyKind::Right, KeyKind::Enter, KeyKind::Enter] {
                    e.key(Key::new(k)).map_err(bad)?;
                }
            } else {
                e.reset(ResetMode::Discard);
            }
        }
    }
    let taught = e.learner().records().len();
    if taught == 0 {
        return Err("--abbr-time: no pick was learned".into());
    }
    let mut readings: Vec<Vec<String>> = Vec::new();
    for f in &lines {
        let syls: Vec<String> = f[2].split(' ').map(String::from).collect();
        for n in 1..=syls.len().min(4) {
            for st in 0..=(syls.len() - n) {
                if !readings.contains(&syls[st..st + n].to_vec()) {
                    readings.push(syls[st..st + n].to_vec());
                }
            }
        }
    }
    let keys: Vec<String> = "我你他想要去吃喝一杯這那有是不了的們".chars().flat_map(|a| "我你他想要去吃喝一杯這那".chars().map(move |b| format!("{a}{b}"))).collect();
    let store_err = |_| "cannot write the learning file".to_string();
    let (store, mut records, _) = core::learn_store::LearnStore::open(dir).map_err(store_err)?;
    records.extend((0..core::learn::CAPACITY.saturating_sub(taught)).map(|i| core::learn::Record {
        context: keys[i % keys.len()].clone(),
        reading: readings[i % readings.len()].clone(),
        word: format!("測{i}"),
        weight: 1.0,
        day: ABBR_TIME_DAY,
    }));
    store.save(&records).map_err(store_err)?;
    e.learning_open(dir).map_err(|_| "cannot reopen the learning directory".to_string())?;
    e.set_abbreviation(true).map_err(bad)?;
    e.reset(ResetMode::Discard);
    Ok(e.learner().records().len())
}

/// `--abbr-time`: types every word (left context, keys, whether `abbr_cuts` hit `ABBR_MIX_CUTS`) from a reset engine and returns the time of
/// each `Engine::key` call: all of them, and those of the words at the cap. The first word is typed once more before, untimed.
fn abbr_time_pass(e: &mut Engine, words: &[(String, Vec<Key>, bool)]) -> Result<(Vec<Duration>, Vec<Duration>), String> {
    let (mut all, mut capped) = (Vec::new(), Vec::new());
    for (n, (left, keys, cap)) in words.iter().take(1).chain(words).enumerate() {
        e.reset(ResetMode::Discard);
        e.set_left_context(left);
        for &k in keys {
            let t = Instant::now();
            let o = e.key(k);
            let d = t.elapsed();
            o.map_err(|_| "engine error on a key".to_string())?;
            if n > 0 {
                all.push(d);
                if *cap {
                    capped.push(d);
                }
            }
        }
    }
    e.reset(ResetMode::Discard);
    Ok((all, capped))
}

/// One line of `--abbr-time`, the same fields in every cell and every build so two runs compare line by line: the learning records the
/// engine holds, the words typed, the `Engine::key` calls timed, and their p50, p95 (the sorted times at len/2 and len*95/100, as
/// core/tests/engine_predict.rs) and max in milliseconds (`None` without a key).
fn abbr_time_line(cell: &str, tag: &str, records: usize, words: usize, times: &mut [Duration]) -> String {
    times.sort();
    let ms = |i: usize| times.get(i).map_or("None".to_string(), |d| format!("{:.3}", d.as_secs_f64() * 1000.0));
    let n = times.len();
    format!("## time:{cell}  {tag}  {{'records': {records}, 'words': {words}, 'keys': {n}, 'p50_ms': {}, 'p95_ms': {}, 'max_ms': {}}}", ms(n / 2), ms(n * 95 / 100), ms(n.wrapping_sub(1)))
}

/// `--abbr` cuts a row character by character against its reading, so the two must have the same length. The error names the file and
/// line and gives both counts, never the row's text.
fn abbr_row_lengths_match(chars: usize, syllables: usize, file: &str, line: usize) -> Result<(), String> {
    if chars == syllables {
        return Ok(());
    }
    Err(format!("{file}:{line}: the sentence has {chars} characters but the reading has {syllables} syllables"))
}

/// `--abbr A|B|C --rows FILE [--rows FILE …] --lm FILE [--abbr-dump FILE]`: how the prediction row ranks each word of the rows when it is
/// typed abbreviated (A: initials only; B: without the initial; C: without the tone key). Measures only; nothing here changes the engine.
///
/// Rows are `context|sentence|reading` lines, one character per syllable; a line seen before (in any file) is dropped. Each sentence is cut
/// greedily into the longest lexicon entries whose reading is the row's syllables, tones included (the App's default packs are in the
/// lexicon); a character no entry covers is its own excluded item. Every word starts from a reset engine with the left context "the row's
/// context + the sentence before the word", and its abbreviated symbols are typed on the standard layout. Fixed settings (in the summary
/// tag): chat, packs acg,it,meme,beauty, prediction row and abbreviation composer on, English mixing and learning off.
///
/// A word is excluded, by the first reason that holds, when its text holds a Zhuyin symbol, when it has more than `ABBR_MAX_UNITS`
/// syllables, or when the units the composer made (`Engine::pending_units`) are not one per syllable (a composer merge). For the others the
/// row gives top1, top9 and MRR, and one unlimited `predict` call on the engine's own index, model and scoring
/// (`Engine::with_predict_parts`; same history word, units and mode) gives the word's full rank. The run fails, printing no number, when a row
/// is not the first nine of that list, when a measurable word of A or C is absent from it, or when style B's offline set misses its own word.
///
/// Output: one line per subset (`all`, the word lengths 1, 2, 3-4, 5-10, and for B the words whose B keys equal their C keys and the others,
/// each by length too); for B also the offline lines (`abbr_b_sets`, every word with at most 10 syllables and no Zhuyin symbol, merged or not).
/// A row whose sentence length and syllable count differ stops the run with its file and line (`abbr_row_lengths_match`); so does a
/// word the composer simulation (`abbr_merge_kind`) and the engine disagree on. The last line, `## merges`, splits the composer merges
/// into backward-only and has-forward.
/// `--abbr-dump` writes each measurable word's query and row in the format of `--predict` without the score columns, so
/// `--predict DUMP --lm … --profile chat --packs acg,it,meme,beauty` must print the same words.
///
/// `--abbr-mix` (docs/spec/prediction.md §6.1) changes none of the above and adds lines after them. For every measurable word and every
/// composer merge it takes each cut of the typed symbols that keeps the typing order (`abbr_cuts`; the composer's own units are not one
/// of them when it merged backward), calls the unlimited `predict` on each (`abbr_cut_lists`) and mixes the lists (`abbr_mix`). The run
/// fails when a measurable word with a single cut has a mixed list that is not that cut's `predict` list, or when the groups do not add up.
///
/// `--abbr-beta B[,B…]` (needs `--abbr-mix`) prints one group of the mix lines per value instead, tagged `beta=B`: the lists are mixed on
/// the score minus B for every knife of the entry's cut (`abbr_knives`, `abbr_mix_beta`). The cuts and their `predict` lists are computed
/// once per word. When a value is 0 the run also mixes with `abbr_mix` and fails unless that group's lines are the same.
/// `--abbr-half select|read` measures only the 1st, 3rd, 5th… (`select`) or the 2nd, 4th, 6th… (`read`) of the distinct rows, in the order
/// they were read, and adds a `## half` line after `## rows`. Without the two options every output is what it was before them.
///
/// `--abbr-mix-dump FILE` (needs `--abbr-mix`) writes one line per word of `## mix:all`: its class, its rank before mixing and the first nine
/// of the list `abbr_mix` gives (no β, whatever `--abbr-beta` says); the columns are `abbr_mix_dump_line`'s.
/// `--abbr-time` (needs `--abbr C`) adds three lines last, `## time:off`, `## time:learn50k` and `## time:learn50k-capped`
/// (`abbr_time_line`): the time of every `Engine::key` call when each word that is typed (measurable or merged) is typed again from a reset
/// engine. `off`: the engine as above; `learn50k`: learning on with the file of `abbr_time_store` (in a temporary directory, removed
/// after); `learn50k-capped`: of that pass, the keys of the words whose `abbr_cuts` list hit `ABBR_MIX_CUTS`.
/// Without the two options every output is what it was before them.
fn run_abbr(args: &[String]) -> Result<(), String> {
    let (mut style, mut files, mut lm_path, mut dump, mut mix) = (None, Vec::new(), None, None, false);
    let (mut betas, mut half): (Option<Vec<f64>>, Option<(usize, &str)>) = (None, None);
    let (mut mix_dump, mut time): (Option<String>, bool) = (None, false);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut val = || it.next().cloned().ok_or_else(|| "missing option value".to_string());
        match a.as_str() {
            "--abbr" => {
                style = Some(match val()?.as_str() {
                    "A" => Abbr::A,
                    "B" => Abbr::B,
                    "C" => Abbr::C,
                    _ => return Err("--abbr takes A, B or C".into()),
                })
            }
            "--rows" => files.push(val()?),
            "--lm" => lm_path = Some(val()?),
            "--abbr-dump" => dump = Some(val()?),
            "--abbr-mix" => mix = true,
            "--abbr-mix-dump" => mix_dump = Some(val()?),
            "--abbr-time" => time = true,
            "--abbr-beta" => {
                let ok = |s: &str| s.parse::<f64>().ok().filter(|b| b.is_finite() && !b.is_sign_negative());
                betas = Some(val()?.split(',').map(ok).collect::<Option<Vec<f64>>>().ok_or("--abbr-beta takes non-negative numbers separated by commas")?);
            }
            "--abbr-half" => {
                half = Some(match val()?.as_str() {
                    "select" => (0, "select"),
                    "read" => (1, "read"),
                    _ => return Err("--abbr-half takes select or read".into()),
                })
            }
            _ => return Err("unknown argument".into()),
        }
    }
    let (style, lm_path) = (style.ok_or("--abbr is required")?, PathBuf::from(lm_path.ok_or("--lm is required")?));
    if files.is_empty() {
        return Err("--abbr needs --rows".into());
    }
    if betas.is_some() && !mix {
        return Err("--abbr-beta needs --abbr-mix".into());
    }
    if mix_dump.is_some() && !mix {
        return Err("--abbr-mix-dump needs --abbr-mix".into());
    }
    if time && style != Abbr::C {
        return Err("--abbr-time needs --abbr C".into());
    }
    // How each word's lists are mixed, a group of lines each: every β, and `abbr_mix` itself (`None`, last) when there is no β, one of them is
    // 0, or `--abbr-mix-dump` reads it (it prints no line of its own next to a β).
    let mut variants: Vec<Option<f64>> = betas.iter().flatten().map(|&b| Some(b)).collect();
    if betas.as_ref().is_none_or(|b| b.contains(&0.0)) || mix_dump.is_some() {
        variants.push(None);
    }
    // `origin[k]` is the file and 1-based line of `rows[k]`, so a bad row can be named without printing its text.
    let (mut seen, mut rows, mut origin, mut read) = (std::collections::HashSet::new(), Vec::new(), Vec::new(), 0usize);
    for f in &files {
        let text = fs::read_to_string(f).map_err(|e| format!("cannot read file ({:?})", e.kind()))?;
        for (n, line) in text.lines().enumerate().filter(|(_, l)| l.split('|').count() == 3) {
            read += 1;
            if seen.insert(line.to_string()) {
                for r in three_field_rows(line) {
                    rows.push(r);
                    origin.push((f.as_str(), n + 1));
                }
            }
        }
    }
    let distinct = rows.len();
    if let Some((h, _)) = half {
        // The row's place among the distinct rows decides its half; every word of the row goes with it.
        let (mut k, mut j) = (0, 0);
        rows.retain(|_| (k % 2 == h, k += 1).0);
        origin.retain(|_| (j % 2 == h, j += 1).0);
    }
    let packs = cli::replay::PACKS;
    let pdir = packs_dir_checked(packs, None)?;
    // The tool's own lexicon, for cutting words and for style B's offline sets: built as `--predict` builds it (the engine's is private, and its
    // model with it, so the model file is read twice). The row and the full rank come from the engine alone.
    let lm = Lm::load(&lm_path).map_err(|e| e.to_string())?;
    let (lex, overlay) = load_with_packs(&root().join("data/lexicon"), packs, &pdir, None, true)?;
    let pack_rows = read_packs_detailed(&pdir, packs & PACK_OOV).map_err(|e| e.to_string())?;
    let capped = CappedLexicon::with_packs(lex, &overlay, &pack_rows, &lm, None).ok_or("cannot build the capped lexicon")?;
    let sets = if style == Abbr::B { abbr_b_sets(capped.readings(&lm)) } else { HashMap::new() };
    let mut e = abbr_engine(&lm_path)?;
    let (mut items, mut out, mut bad_rows, mut absent) = (Vec::new(), String::new(), Vec::new(), Vec::new());
    // `mixes[k]` is the words mixed as `variants[k]` says.
    let (mut mixes, mut bad_mix): (Vec<Vec<AbbrMix>>, usize) = (variants.iter().map(|_| Vec::new()).collect(), 0);
    // `--abbr-mix-dump`'s lines; `--abbr-time`'s words (left context, keys, at the cap of `abbr_cuts`).
    let (mut mix_out, mut timed): (String, Vec<(String, Vec<Key>, bool)>) = (String::new(), Vec::new());
    for ((sent, syls, ctx), &(file, line)) in rows.iter().zip(&origin) {
        let chars: Vec<char> = sent.chars().collect();
        abbr_row_lengths_match(chars.len(), syls.len(), file, line)?;
        let text = |i: usize, l: usize| chars[i..i + l].iter().collect::<String>();
        for (i, l, found) in abbr_segment(chars.len(), |i, l| capped.entries(&syls[i..i + l]).iter().any(|(w, _)| *w == text(i, l))) {
            let (word, reading) = (text(i, l), &syls[i..i + l]);
            let units: Vec<String> = reading.iter().map(|s| abbr_syllable(s, style)).collect();
            let b_units: Vec<String> = reading.iter().map(|s| abbr_syllable(s, Abbr::B)).collect();
            let mut item = AbbrItem {
                len: l,
                excluded: None,
                b_eq_c: b_units.iter().zip(reading).all(|(b, s)| *b == abbr_syllable(s, Abbr::C)),
                row_rank: None,
                full_rank: None,
                keys_full: reading.iter().map(|s| abbr_syllable(s, Abbr::C).chars().count() + 1).sum(),
                keys_abbr: units.iter().map(|u| u.chars().count()).sum(),
                b_rank: None,
                merge_backward: None,
            };
            item.excluded = if found && word.chars().any(is_zhuyin) {
                Some(0)
            } else if !found {
                Some(1)
            } else if l > core::engine::ABBR_MAX_UNITS {
                Some(2)
            } else {
                None
            };
            if item.excluded.is_none() {
                if style == Abbr::B {
                    item.b_rank = Some(abbr_b_rank(&sets, &b_units, &word));
                }
                let left = format!("{ctx}{}", text(0, i));
                if time {
                    let symbols: Vec<char> = units.iter().flat_map(|u| u.chars()).collect();
                    let keys: Option<Vec<Key>> = symbols.iter().map(|&c| Layout::Standard.key_of_symbol(c).map(|k| Key::ch(k, 0))).collect();
                    timed.push((left.clone(), keys.ok_or("a reading holds a symbol with no key")?, abbr_cuts(&symbols).1));
                }
                let (got, row) = abbr_type(&mut e, &left, &units)?;
                let kind = abbr_merge_kind(&units);
                // The simulated rule must name exactly the words the engine merges (a word is not printed, only counted).
                if (got != units) != kind.is_some() {
                    return Err(format!("{file}:{line}: the composer simulation and the engine disagree on a merge"));
                }
                if got != units {
                    item.excluded = Some(3);
                    item.merge_backward = kind;
                } else {
                    let u: Vec<Unit> = units.iter().map(|u| Unit { chars: u.chars().collect(), done: false, tone: None }).collect();
                    let mode = if u.len() > 1 { Mode::PA } else { Mode::P };
                    let (v, full) = e
                        .with_predict_parts(|idx, lm, cfg| {
                            let key = context_key(&left);
                            let v = history(if key == SENTINEL { "" } else { &key }, lm).to_string();
                            let full: Vec<String> = predict(idx, lm, cfg, &v, &u, mode, usize::MAX).into_iter().map(|x| x.0).collect();
                            (v, full)
                        })
                        .ok_or("the engine has no model")?;
                    let query = format!("{}\t{v}\t-\t{}", if mode == Mode::P { "P" } else { "PA" }, units_str(&u));
                    if row[..] != full[..full.len().min(9)] {
                        bad_rows.push(query.clone());
                    }
                    item.row_rank = row.iter().position(|w| *w == word).map(|p| p + 1);
                    item.full_rank = full.iter().position(|w| *w == word).map(|p| p + 1);
                    if item.full_rank.is_none() {
                        absent.push(format!("{query}\t{word}"));
                    }
                    out += &format!("## {query}\n");
                    row.iter().for_each(|w| out += &format!("{w}\n"));
                }
                if mix {
                    let (mut cuts, capped) = abbr_cuts(&units.iter().flat_map(|u| u.chars()).collect::<Vec<char>>());
                    // The answer's own cut goes first, so an entry it gives as well as another cut counts as its own (`abbr_mix` keeps the earlier list).
                    let own = cuts.iter().position(|c| *c == units);
                    if let Some(p) = own {
                        cuts[..=p].rotate_right(1);
                    }
                    // The cuts and their lists once; each variant only mixes them again.
                    let lists = abbr_cut_lists(&e, &left, &cuts)?;
                    let (lists, knives): (Vec<&[AbbrCand]>, Vec<usize>) = (lists.iter().map(Vec::as_slice).collect(), abbr_knives(&cuts));
                    let (merged, mut bad) = (item.excluded.is_some(), false);
                    for (v, m) in variants.iter().zip(mixes.iter_mut()) {
                        let mixed = match v {
                            None => abbr_mix(&lists),
                            Some(beta) => abbr_mix_beta(&lists, &knives, *beta),
                        };
                        bad |= !merged && cuts.len() == 1 && !mixed.iter().map(|(c, _)| &c.0).eq(lists[0].iter().map(|c| &c.0));
                        let at = mixed.iter().position(|(c, _)| c.0 == word);
                        if v.is_none() && mix_dump.is_some() {
                            let u: Vec<Unit> = units.iter().map(|u| Unit { chars: u.chars().collect(), done: false, tone: None }).collect();
                            let key = context_key(&left);
                            let h = e.with_predict_parts(|_, lm, _| history(if key == SENTINEL { "" } else { &key }, lm).to_string()).ok_or("the engine has no model")?;
                            let query = format!("{}\t{h}\t-\t{}", if u.len() > 1 { "PA" } else { "P" }, units_str(&u));
                            let top: Vec<&str> = mixed.iter().take(9).map(|(c, _)| c.0.as_str()).collect();
                            mix_out += &abbr_mix_dump_line(&query, &word, merged, item.full_rank, &top);
                        }
                        m.push(AbbrMix {
                            merged,
                            row_before: item.row_rank,
                            full_before: item.full_rank,
                            after: at.map(|p| p + 1),
                            by_answer_cut: at.is_some_and(|p| own.is_some() && mixed[p].1 == 0),
                            cuts: cuts.len(),
                            capped,
                            answer_cut_dropped: own.is_none(),
                        });
                    }
                    if bad {
                        bad_mix += 1;
                        eprintln!("{file}:{line}: the mixed list of a single cut is not its predict list");
                    }
                }
            }
            items.push(item);
        }
    }
    if let Some(f) = dump {
        fs::write(f, &out).map_err(|e| format!("cannot write the dump ({:?})", e.kind()))?;
    }
    if let Some(f) = mix_dump {
        fs::write(f, &mix_out).map_err(|e| format!("cannot write the mix dump ({:?})", e.kind()))?;
    }
    // The stops (the rows are public development rows, so the offending queries are named).
    if !bad_rows.is_empty() {
        bad_rows.iter().for_each(|q| eprintln!("row differs from predict: {q}"));
        return Err(format!("{} rows are not the first nine of the unlimited predict list", bad_rows.len()));
    }
    if style != Abbr::B && !absent.is_empty() {
        absent.iter().for_each(|q| eprintln!("absent: {q}"));
        return Err(format!("{} measurable words are not among the candidates", absent.len()));
    }
    let missing = items.iter().filter(|i| i.b_rank == Some(None)).count();
    if missing > 0 {
        return Err(format!("style B's offline set misses {missing} of its own words"));
    }
    let tag = format!("abbr-{style:?}  lm-chat{}", pack_tag(packs));
    if bad_mix > 0 {
        return Err(format!("{bad_mix} single-cut words have a mixed list that is not the predict list"));
    }
    let mut mix_lines = Vec::new();
    for (v, m) in variants.iter().zip(&mixes).filter(|_| mix) {
        match v {
            None if betas.is_none() => mix_lines = abbr_mix_lines(m, &tag)?,
            None => {}
            Some(beta) => {
                let t = format!("beta={beta}  {tag}");
                let lines = abbr_mix_lines(m, &t)?;
                // β = 0 is the mix without β: the same lines under the same tag (`abbr_mix`'s words are the last of `mixes`).
                if *beta == 0.0 && mixes.last().map(|plain| abbr_mix_lines(plain, &t)) != Some(Ok(lines.clone())) {
                    return Err("--abbr-beta 0 does not give the lines of --abbr-mix without it".into());
                }
                mix_lines.extend(lines);
            }
        }
    }
    println!("## rows  {tag}  {{'read': {read}, 'distinct': {distinct}}}");
    if let Some((_, name)) = half {
        println!("## half  {tag}  {{'half': '{name}', 'rows': {}}}", rows.len());
    }
    type Pick = fn(&AbbrItem) -> bool;
    let groups: &[(&str, Pick)] = if style == Abbr::B { &[("", |_| true), ("b=c:", |i| i.b_eq_c), ("b!=c:", |i| !i.b_eq_c)] } else { &[("", |_| true)] };
    let lens: [(&str, Pick); 5] = [("all", |_| true), ("len1", |i| i.len == 1), ("len2", |i| i.len == 2), ("len3-4", |i| (3..=4).contains(&i.len)), ("len5-10", |i| (5..=10).contains(&i.len))];
    for (g, in_group) in groups {
        for (l, in_len) in lens {
            let sub: Vec<&AbbrItem> = items.iter().filter(|i| in_group(i) && in_len(i)).collect();
            println!("## {g}{l}  {tag}  {}", abbr_summary(&sub));
            if style == Abbr::B {
                println!("## {g}{l}  {tag}  b-offline  {}", abbr_b_summary(&sub));
            }
        }
    }
    // docs/spec/prediction.md §6.1: the composer merges split by kind (the line above stay as they were; this one is added last).
    let merged: Vec<&AbbrItem> = items.iter().filter(|i| i.excluded == Some(3)).collect();
    let backward = merged.iter().filter(|i| i.merge_backward == Some(true)).count();
    println!("## merges  {tag}  {{'merged': {}, 'backward_only': {backward}, 'has_forward': {}}}", merged.len(), merged.len() - backward);
    mix_lines.iter().for_each(|l| println!("{l}"));
    if time {
        let words = timed.len();
        let (mut off, _) = abbr_time_pass(&mut e, &timed)?;
        println!("{}", abbr_time_line("off", &tag, 0, words, &mut off));
        let dir = std::env::temp_dir().join(format!("shanjie-abbr-time-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let run = abbr_time_store(&mut e, &dir).and_then(|n| Ok((n, abbr_time_pass(&mut e, &timed)?)));
        let _ = fs::remove_dir_all(&dir);
        let (n, (mut all, mut capped)) = run?;
        println!("{}", abbr_time_line("learn50k", &tag, n, words, &mut all));
        println!("{}", abbr_time_line("learn50k-capped", &tag, n, timed.iter().filter(|w| w.2).count(), &mut capped));
    }
    Ok(())
}

fn run() -> Result<(), String> {
    let all: Vec<String> = std::env::args().skip(1).collect();
    // The abbreviated-typing measurement: drives the engine; reads no lenient table.
    if all.iter().any(|a| a == "--abbr") {
        return run_abbr(&all);
    }
    // docs/spec/english-mixed.md §13.3: a subcommand of its own, before anything the evaluation modes read.
    if all.first().map(String::as_str) == Some("english-sim") {
        return english::run(&all[1..], &root());
    }
    // docs/spec/english-mixed.md §13.4: drives the engine with and without the table; reads no lenient table.
    if all.iter().any(|a| a == "--english-next") {
        return english_next::run(&all, &root());
    }
    let len = load_lenient()?;
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
        load_lexicon_packs_detailed(&root().join("data/lexicon"), None).map(|(lex, _)| lex).map_err(|e| e.to_string())?
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
        // 保留集只印指標：錯句會把內容露給調整系統的人（docs/spec/evaluation.md §1.4 的保留集規則）
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

#[cfg(test)]
mod tests {
    use super::*;

    /// `--pool-mask-diff`: D 我們他去 (holds the manual word 我們), R 我們你好 (holds 我們 and the manual word 你好) and X 你們他去 (holds neither). Reordered
    /// to R, D, X: with D as the base (this slice) only X is masked; with R as the base (the exploration patch) D is masked too, so the row counts. With
    /// only 我們 manual the two agree; a row not reordered counts only when the order without the mask would have been one; no manual word, no difference.
    #[test]
    fn mask_differs_compares_the_two_bases() {
        let top0 = ["我們他去", "我們你好", "你們他去"];
        let both = core::ManualSet::from_pairs([("我們", "ㄨㄛˇ-ㄇㄣ˙"), ("你好", "ㄋㄧˇ-ㄏㄠˇ")]);
        let one = core::ManualSet::from_pairs([("我們", "ㄨㄛˇ-ㄇㄣ˙")]);
        let syl = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let (d_read, r_read) = (syl(&["ㄨㄛˇ", "ㄇㄣ˙", "ㄊㄚ", "ㄑㄩˋ"]), syl(&["ㄨㄛˇ", "ㄇㄣ˙", "ㄋㄧˇ", "ㄏㄠˇ"]));
        let reordered = Some((vec![1, 0, 2], vec![0.0, -1.0, -3.0]));
        for manual in [&both, &one] {
            let masked0 = manual_mask(&d_read, &top0, manual);
            assert_eq!(masked0, [false, false, true]);
            let differs = mask_differs(&reordered, true, &masked0, &top0, || r_read.clone(), manual);
            assert_eq!(differs, std::ptr::eq(manual, &both));
        }
        assert!(mask_differs(&None, true, &[false, true, false], &top0, || unreachable!(), &both), "not reordered because the new first is masked");
        assert!(!mask_differs(&None, false, &[false; 3], &top0, || unreachable!(), &both), "not reordered at all");
        assert!(!mask_differs(&reordered, true, &[false; 3], &top0, || unreachable!(), &core::ManualSet::default()), "no manual word");
    }

    #[test]
    fn lm_opts_holdout_allows_rowstats_not_dump() {
        // --rowstats is not an argument: it is allowed everywhere, holdout included.
        assert!(check_lm_opts(Some("holdout"), false, false, 0, false).is_ok());
        assert!(check_lm_opts(Some("holdout"), true, false, 0, false).is_err());
        assert!(check_lm_opts(Some("holdout"), false, true, 0, false).is_err(), "--picks with the holdout");
        assert!(check_lm_opts(None, false, true, 0, false).is_ok());
        assert!(check_lm_opts(None, true, false, 0, false).is_ok());
        assert!(check_lm_opts(None, false, false, 0, true).is_err(), "--packs-dir without --packs");
        assert!(check_lm_opts(None, false, false, PACK_ACG, true).is_ok());
    }

    #[test]
    fn judge_opts_accept_needs_v2_and_the_holdout_refuses_accept_and_rejudge() {
        assert!(check_judge_opts(None, false, false, false).is_ok());
        assert!(check_judge_opts(None, true, true, false).is_ok());
        assert!(check_judge_opts(None, false, true, false).is_err(), "--accept without --judge v2");
        assert!(check_judge_opts(Some("holdout"), true, false, false).is_ok(), "--judge v2 --rowstats on the holdout stays allowed");
        assert!(check_judge_opts(Some("holdout"), true, true, false).is_err(), "--accept with --set holdout");
        assert!(check_judge_opts(Some("holdout"), true, false, true).is_err(), "--rejudge with --set holdout");
        assert!(check_judge_opts(None, true, false, true).is_ok());
    }

    #[test]
    fn picks_file_takes_picks_lines_and_bare_sentences() {
        assert_eq!(parse_picks("1\t0\t你好\n2\t-\t\n", 2), Ok(vec!["你好".to_string(), String::new()]));
        assert_eq!(parse_picks("你好\n再見\n", 2), Ok(vec!["你好".to_string(), "再見".to_string()]));
        assert!(parse_picks("你好\n", 2).is_err(), "row count");
        assert!(parse_picks("2\t0\t你好\n", 1).is_err(), "row number");
        assert!(parse_picks("1\t0\t你好\t多\n", 1).is_err(), "four fields");
    }

    /// `--packs` takes acg, quotes, it, meme, beauty and comma lists (and the flag can repeat); anything else is refused; a requested pack's file must exist; the summary marker follows the bits.
    #[test]
    fn packs_flag_takes_acg_quotes_it_and_lists() {
        assert_eq!(pack_flag("acg"), Ok(PACK_ACG));
        assert_eq!(pack_flag("quotes"), Ok(PACK_QUOTES));
        assert_eq!(pack_flag("it"), Ok(PACK_IT));
        assert_eq!(pack_flag("meme"), Ok(PACK_MEME));
        assert_eq!(pack_flag("beauty"), Ok(PACK_BEAUTY));
        assert_eq!(pack_flag("acg,it,meme,beauty"), Ok(PACK_ACG | PACK_IT | PACK_MEME | PACK_BEAUTY));
        assert_eq!(pack_flag("acg,it"), Ok(PACK_ACG | PACK_IT));
        assert_eq!(pack_flag("it,quotes,acg"), Ok(PACK_ACG | PACK_QUOTES | PACK_IT));
        assert!(pack_flag("").is_err() && pack_flag("quote").is_err() && pack_flag("acg,").is_err());
        assert_eq!((pack_tag(0), pack_tag(PACK_QUOTES), pack_tag(PACK_ACG | PACK_QUOTES | PACK_IT)), (String::new(), "+quotes".to_string(), "+acg+quotes+it".to_string()));
        assert_eq!(pack_tag(PACK_ACG | PACK_IT | PACK_MEME | PACK_BEAUTY), "+acg+it+meme+beauty");
        assert_eq!(pack_tag(PACK_ACG | PACK_QUOTES | PACK_IT | PACK_MEME | PACK_BEAUTY), "+acg+quotes+it+meme+beauty");
        let dir = std::env::temp_dir().join(format!("shanjie-packs-flag-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("acg-add.tsv"), "").unwrap();
        let d = || Some(dir.to_string_lossy().to_string());
        assert!(packs_dir_checked(PACK_ACG, d()).is_ok());
        let e = packs_dir_checked(PACK_ACG | PACK_IT, d()).unwrap_err();
        assert!(e.contains("--packs it") && e.contains("it-add.tsv"), "{e}");
        let e = packs_dir_checked(PACK_MEME, d()).unwrap_err();
        assert!(e.contains("--packs meme") && e.contains("meme-add.tsv"), "{e}");
        let e = packs_dir_checked(PACK_BEAUTY, d()).unwrap_err();
        assert!(e.contains("--packs beauty") && e.contains("beauty-add.tsv"), "{e}");
        let e = packs_dir_checked(PACK_QUOTES, d()).unwrap_err();
        assert!(e.contains("--packs quotes") && e.contains("quotes-add.tsv"), "{e}");
        fs::remove_dir_all(&dir).unwrap();
    }

    /// docs/spec/lexicon-and-packs.md §3.6: `--extra-overlay` rows are capped like pack rows. Goes through the CLI's own load path
    /// (`load_with_packs`, then `CappedLexicon::new`); a word the corpus never saw is scored fixture score minus 1.0.
    /// If the rows reached only the lexicon and not the capping text, the score would stay -7.5. 鮮爭 is a made-up name (not in the lexicon, count 0: the names layer's
    /// real words are counted into the model since 通語 SJ-LM-7-9.7M, so none of them is a word the corpus never saw); loading the same row twice is a parse error.
    #[test]
    fn extra_overlay_rows_are_capped_like_pack_rows() {
        let lm = Lm::load(&root().join("data/lm/bigram.sjlm")).expect("data/lm/bigram.sjlm and classes.sjc are required (see cli/tests/golden.rs)");
        assert_eq!(lm.count("鮮爭"), 0, "the fixture word must be one the corpus never saw");
        let file = std::env::temp_dir().join(format!("shanjie-extra-overlay-{}.tsv", std::process::id()));
        fs::write(&file, "ㄒㄧㄢ-ㄓㄥ\t鮮爭\t-7.5\tnames\n").unwrap();
        let (dir, pdir) = (root().join("data/lexicon"), root().join("data/packs"));
        let key = ["ㄒㄧㄢ".to_string(), "ㄓㄥ".to_string()];
        let score = |extra: Option<&Path>| {
            let (lex, overlay) = load_with_packs(&dir, 0, &pdir, extra, false).unwrap();
            CappedLexicon::new(lex, &overlay, &lm, None).unwrap().best_lp(&key, "鮮爭")
        };
        let (with, without) = (score(Some(&file)), score(None));
        fs::remove_file(&file).unwrap();
        assert_eq!(with, Some(-8.5));
        assert_eq!(without, None);
    }

    /// `--abbr`, ABBR-1: the composer simulation splits merges by kind. 一個 typed A (ㄍ after ㄧ) and 一起 typed C (ㄑ after ㄧ) are backward;
    /// 西安 typed C (ㄢ after ㄒㄧ) is forward; 答案 typed C does not merge (ㄢ's column is taken by ㄚ); a single syllable never merges.
    #[test]
    fn abbr_merge_kind_splits_backward_and_forward_merges() {
        let u = |s: &str| s.split(' ').map(String::from).collect::<Vec<_>>();
        assert_eq!(abbr_merge_kind(&u("ㄧ ㄍ")), Some(true));
        assert_eq!(abbr_merge_kind(&u("ㄧ ㄑㄧ")), Some(true));
        assert_eq!(abbr_merge_kind(&u("ㄒㄧ ㄢ")), Some(false));
        assert_eq!(abbr_merge_kind(&u("ㄉㄚ ㄢ")), None);
        assert_eq!(abbr_merge_kind(&u("ㄍㄨㄛ")), None);
        // A backward merge then a forward one in the same word counts as forward.
        assert_eq!(abbr_merge_kind(&u("ㄨ ㄉ ㄢ")), Some(false));
    }

    /// `--abbr`: a row whose sentence and reading differ in length stops the run with the file and line named and both counts, no text.
    #[test]
    fn abbr_names_the_file_and_line_of_a_row_whose_lengths_differ() {
        assert_eq!(abbr_row_lengths_match(3, 3, "eval/dev/x.txt", 7), Ok(()));
        let e = abbr_row_lengths_match(4, 3, "eval/dev/x.txt", 7).unwrap_err();
        assert_eq!(e, "eval/dev/x.txt:7: the sentence has 4 characters but the reading has 3 syllables");
    }

    /// `--abbr`: the rewriting table. The six example words, the neutral tone (dropped wherever it is written) and a word written with
    /// Zhuyin letters (rewritten like any reading; `run_abbr` excludes it by its text).
    #[test]
    fn abbr_rewrites_each_syllable_by_the_table() {
        let all = |reading: &str| [Abbr::A, Abbr::B, Abbr::C].map(|st| reading.split(' ').map(|s| abbr_syllable(s, st)).collect::<Vec<_>>().join(" "));
        for (reading, want) in [
            ("ㄐㄧㄣ ㄊㄧㄢ", ["ㄐ ㄊ", "ㄧㄣ ㄧㄢ", "ㄐㄧㄣ ㄊㄧㄢ"]),
            ("ㄢ ㄑㄩㄢˊ", ["ㄢ ㄑ", "ㄢ ㄩㄢ", "ㄢ ㄑㄩㄢ"]),
            ("ㄓ ㄉㄠˋ", ["ㄓ ㄉ", "ㄓ ㄠ", "ㄓ ㄉㄠ"]),
            ("ㄕˋ ㄑㄧㄥˊ", ["ㄕ ㄑ", "ㄕ ㄧㄥ", "ㄕ ㄑㄧㄥ"]),
            ("ㄉㄜ˙", ["ㄉ", "ㄜ", "ㄉㄜ"]),
            ("ㄨㄛˇ ㄇㄣ˙", ["ㄨ ㄇ", "ㄨㄛ ㄣ", "ㄨㄛ ㄇㄣ"]),
            ("˙ㄅㄚ", ["ㄅ", "ㄚ", "ㄅㄚ"]),
            ("ㄏㄠˇ ㄉㄜ˙", ["ㄏ ㄉ", "ㄠ ㄜ", "ㄏㄠ ㄉㄜ"]),
        ] {
            assert_eq!(all(reading), want, "{reading}");
        }
        assert!("好ㄉ".chars().any(is_zhuyin) && "ㄅㄅ".chars().any(is_zhuyin) && !"好的ˇ".chars().any(is_zhuyin), "the Zhuyin-letter word test");
    }

    /// `--abbr`: longest entry first; a character no entry covers is one piece that is not found, and cutting goes on behind it.
    #[test]
    fn abbr_segment_takes_the_longest_entry_and_steps_over_a_stray_character() {
        // Positions 0-1 and 3 are entries; so is 0 alone (shorter, not taken). Position 2 is none.
        let has = |i: usize, l: usize| matches!((i, l), (0, 2) | (0, 1) | (3, 1));
        assert_eq!(abbr_segment(4, has), [(0, 2, true), (2, 1, false), (3, 1, true)]);
        assert!(abbr_segment(0, has).is_empty());
    }

    /// `--abbr`: the units the engine's composer really makes for 一個 typed A and 一起 typed C are not one per syllable (a merge); for 答案
    /// typed C and 今天 typed A they are.
    #[test]
    fn abbr_detects_a_composer_merge() {
        let mut e = abbr_engine(&root().join("data/lm/bigram.sjlm")).expect("data/lm/bigram.sjlm and classes.sjc are required (see cli/tests/golden.rs)");
        for (reading, style, want, merged) in [
            ("ㄧˊ ㄍㄜ˙", Abbr::A, vec!["ㄍㄧ"], true),
            ("ㄧˋ ㄑㄧˇ", Abbr::C, vec!["ㄑㄧ", "ㄧ"], true),
            ("ㄉㄚˊ ㄢˋ", Abbr::C, vec!["ㄉㄚ", "ㄢ"], false),
            ("ㄐㄧㄣ ㄊㄧㄢ", Abbr::A, vec!["ㄐ", "ㄊ"], false),
        ] {
            let units: Vec<String> = reading.split(' ').map(|s| abbr_syllable(s, style)).collect();
            let (got, _) = abbr_type(&mut e, "", &units).unwrap();
            assert_eq!(got, want, "{reading} {style:?}");
            assert_eq!(got != units, merged, "{reading} {style:?}: what run_abbr tests");
            assert_eq!(abbr_merge_kind(&units).is_some(), merged, "{reading} {style:?}: the simulation agrees with the engine");
        }
        let units = vec!["ㄐ".to_string(), "ㄊ".to_string()];
        assert!(abbr_type(&mut e, "", &units).unwrap().1.contains(&"今天".to_string()));
    }

    /// `--abbr-mix-dump` and `--abbr-time`: every line has the same columns whatever the word has (no rank before, a short list, no key).
    #[test]
    fn abbr_mix_dump_and_time_lines_have_fixed_fields() {
        let a = abbr_mix_dump_line("PA\t我\t-\tㄒ ㄧㄢ", "西安", false, Some(12), &["先", "西安"]);
        let b = abbr_mix_dump_line("P\t\t-\tㄍㄧ", "一個", true, None, &["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]);
        assert_eq!(a, "PA\t我\t-\tㄒ ㄧㄢ\t西安\tmeasurable\t12\t先\t西安\t\t\t\t\t\t\t\n");
        assert_eq!(b, "P\t\t-\tㄍㄧ\t一個\tmerged\t-\t一\t二\t三\t四\t五\t六\t七\t八\t九\n");
        assert!([&a, &b].iter().all(|l| l.matches('\t').count() == 15 && l.ends_with('\n')));
        let ms = |v: &[u64]| v.iter().map(|&m| Duration::from_millis(m)).collect::<Vec<_>>();
        let mut t: Vec<u64> = (1..=100).rev().collect();
        t[0] = 250;
        assert_eq!(abbr_time_line("off", "T", 0, 7, &mut ms(&t)), "## time:off  T  {'records': 0, 'words': 7, 'keys': 100, 'p50_ms': 51.000, 'p95_ms': 96.000, 'max_ms': 250.000}");
        assert_eq!(abbr_time_line("x", "T", 5, 0, &mut []), "## time:x  T  {'records': 5, 'words': 0, 'keys': 0, 'p50_ms': None, 'p95_ms': None, 'max_ms': None}");
    }

    /// `--abbr-mix`: the cuts keep the typing order. 一個 typed A (ㄧ then ㄍ) has one cut, never ㄍㄧ; 西安 typed C has four, the joined one
    /// first, then the fewer units, the later split first; the unit and cut limits hold.
    #[test]
    fn abbr_cuts_keep_the_typing_order() {
        let cuts = |s: &str| abbr_cuts(&s.chars().collect::<Vec<_>>());
        let u = |s: &str| s.split(' ').map(String::from).collect::<Vec<_>>();
        assert_eq!(cuts("ㄧㄍ"), (vec![u("ㄧ ㄍ")], false));
        assert_eq!(cuts("ㄒㄧㄢ"), (vec![u("ㄒㄧㄢ"), u("ㄒㄧ ㄢ"), u("ㄒ ㄧㄢ"), u("ㄒ ㄧ ㄢ")], false));
        assert_eq!(cuts("ㄢㄢ"), (vec![u("ㄢ ㄢ")], false), "the same column opens a unit");
        assert_eq!(cuts(""), (vec![], false));
        // Ten syllables typed C, three symbols each: 9 forced splits, so only the joined cut fits in ABBR_MAX_UNITS.
        assert_eq!(cuts(&"ㄒㄧㄢ".repeat(10)).0, [vec!["ㄒㄧㄢ".to_string(); 10]]);
        // Four syllables: 8 free places, 3 forced splits, at most 6 more units: 1 + 8 + 28 + 56 + … cuts, cut short at 64.
        let (many, capped) = cuts(&"ㄒㄧㄢ".repeat(4));
        assert!(capped && many.len() == ABBR_MIX_CUTS && many[0] == vec!["ㄒㄧㄢ".to_string(); 4]);
        assert!(many.windows(2).all(|w| w[0].len() <= w[1].len() && w[0] != w[1]) && many.iter().all(|c| c.concat() == "ㄒㄧㄢ".repeat(4)));
    }

    /// `--abbr-mix`, the merge on made-up lists (no model): a word is kept once at its best entry (successor first, then score, then the
    /// earlier list); the order is successors, score, word length, reading text, word.
    #[test]
    fn abbr_mix_keeps_the_best_entry_and_orders_like_tiered() {
        let c = |w: &str, s: f64, succ: bool, r: &str| (w.to_string(), s, succ, r.split('-').map(String::from).collect::<Vec<_>>());
        let a = [c("甲", -1.0, false, "ㄐㄧㄚˇ"), c("乙丙", -2.0, false, "ㄧˇ-ㄅㄧㄥˇ"), c("丁", -5.0, true, "ㄉㄧㄥ"), c("同", -3.0, false, "ㄊㄨㄥˊ")];
        let b = [c("乙丙", -1.5, false, "ㄧˋ-ㄅㄧㄥˇ"), c("甲", -1.0, false, "ㄐㄧㄚˋ"), c("戊", -2.0, false, "ㄨˋ"), c("己", -2.0, false, "ㄐㄧˇ"), c("同", -3.0, false, "ㄊㄨㄥˋ")];
        let got: Vec<(&str, f64, String, usize)> = abbr_mix(&[&a[..], &b[..]]).into_iter().map(|(x, k)| (x.0.as_str(), x.1, x.3.join("-"), k)).collect();
        let want = [
            ("丁", -5.0, "ㄉㄧㄥ", 0),        // the successor first, whatever its score
            ("甲", -1.0, "ㄐㄧㄚˇ", 0),       // equal in both lists: the earlier list's entry
            ("乙丙", -1.5, "ㄧˋ-ㄅㄧㄥˇ", 1), // the better score, with its reading
            ("己", -2.0, "ㄐㄧˇ", 1),         // same score and length: the reading text, ㄐ before ㄨ
            ("戊", -2.0, "ㄨˋ", 1),
            ("同", -3.0, "ㄊㄨㄥˊ", 0),
        ];
        assert_eq!(got, want.map(|(w, s, r, k)| (w, s, r.to_string(), k)));
        // Same score, length and reading: the word decides; a successor entry beats a better-scored entry of the same word.
        let (p, q) = ([c("乙", -1.0, false, "ㄧˇ"), c("丙", -9.0, true, "ㄅㄧㄥˇ")], [c("丙", -1.0, false, "ㄅㄧㄥˇ"), c("一", -1.0, false, "ㄧˇ")]);
        assert_eq!(abbr_mix(&[&q[..], &p[..]]).iter().map(|(x, k)| (x.0.as_str(), *k)).collect::<Vec<_>>(), [("丙", 1), ("一", 0), ("乙", 1)]);
        assert!(abbr_mix(&[]).is_empty());
    }

    /// `--abbr-mix` on the model: 一個 typed A. The composer's unit is ㄍㄧ, the only cut in typing order is ㄧ, ㄍ; the mixed list is that
    /// cut's `predict` list, holds 一個, and no entry reads the keys backward. 西安 typed C: the four cuts' lists mix into one with 西安 and
    /// the one-syllable ㄒㄧㄢ words. ㄒ ㄧ: a word both cuts give (ㄒㄧ… as a prefix, ㄒ… ㄧ… as an abbreviation) is listed once.
    #[test]
    fn abbr_mix_of_the_initials_of_yi_ge_never_reads_them_backward() {
        let mut e = abbr_engine(&root().join("data/lm/bigram.sjlm")).expect("data/lm/bigram.sjlm and classes.sjc are required (see cli/tests/golden.rs)");
        let u = |s: &str| s.split(' ').map(String::from).collect::<Vec<_>>();
        assert_eq!(abbr_type(&mut e, "", &u("ㄧ ㄍ")).unwrap().0, u("ㄍㄧ"));
        let (cuts, _) = abbr_cuts(&['ㄧ', 'ㄍ']);
        assert_eq!(cuts, [u("ㄧ ㄍ")]);
        let lists = abbr_cut_lists(&e, "", &cuts).unwrap();
        let mixed = abbr_mix(&[&lists[0][..]]);
        assert!(mixed.iter().map(|(c, _)| *c).eq(lists[0].iter()), "one cut: the mixed list is its predict list, in its order");
        assert!(mixed.iter().any(|(c, _)| c.0 == "一個"));
        assert!(mixed.iter().all(|(c, _)| c.3.len() == 2 && c.3[0].starts_with('ㄧ') && c.3[1].starts_with('ㄍ')), "every entry reads ㄧ then ㄍ");

        let mix_of = |symbols: &[char]| {
            let lists = abbr_cut_lists(&e, "", &abbr_cuts(symbols).0).unwrap();
            let mixed: Vec<AbbrCand> = abbr_mix(&lists.iter().map(Vec::as_slice).collect::<Vec<_>>()).into_iter().map(|(c, _)| c.clone()).collect();
            assert_eq!(mixed.iter().map(|c| &c.0).collect::<std::collections::HashSet<_>>().len(), mixed.len(), "a word is listed once");
            assert!(mixed.windows(2).all(|w| w[0].2 >= w[1].2 && (w[0].2 != w[1].2 || w[0].1 >= w[1].1)), "successors first, then by score");
            (lists, mixed)
        };
        let (_, mixed) = mix_of(&['ㄒ', 'ㄧ', 'ㄢ']);
        assert!(mixed.iter().any(|c| c.0 == "西安") && mixed.iter().any(|c| c.0 == "先"));
        let (lists, mixed) = mix_of(&['ㄒ', 'ㄧ']);
        let both = lists[0].iter().filter(|c| lists[1].iter().any(|d| d.0 == c.0)).count();
        assert!(both > 0 && mixed.len() == lists[0].len() + lists[1].len() - both, "words that two cuts give are kept once");
    }

    /// `--abbr-beta`: a knife is an optional split a cut uses. 西安 typed C: 0, 1, 1, 2; a forced split (ㄒ after ㄢ) is no knife; the
    /// order of the cuts does not matter (`run_abbr` moves the answer's cut to the front).
    #[test]
    fn abbr_knives_count_the_optional_splits() {
        let cuts = |s: &str| abbr_cuts(&s.chars().collect::<Vec<_>>()).0;
        assert_eq!(abbr_knives(&cuts("ㄒㄧㄢ")), [0, 1, 1, 2]);
        assert_eq!(abbr_knives(&cuts("ㄧㄍ")), [0]);
        assert_eq!(cuts("ㄢㄒㄧ"), [vec!["ㄢ".to_string(), "ㄒㄧ".to_string()], vec!["ㄢ".to_string(), "ㄒ".to_string(), "ㄧ".to_string()]]);
        assert_eq!(abbr_knives(&cuts("ㄢㄒㄧ")), [0, 1]);
        let mut moved = cuts("ㄒㄧㄢ");
        moved[..=3].rotate_right(1);
        assert_eq!(abbr_knives(&moved), [2, 0, 1, 1]);
        assert!(abbr_knives(&[]).is_empty());
    }

    /// `--abbr-beta` on made-up lists: 腳 of the joined cut against 就要 that a cut with one knife brings in. β 0 is `abbr_mix`; a β larger
    /// than the gap puts the knife's words behind; a word both cuts give changes to the entry with the better adjusted score; a successor
    /// stays first whatever β is.
    #[test]
    fn abbr_mix_beta_lowers_the_entries_of_cuts_with_knives() {
        let c = |w: &str, s: f64, succ: bool, r: &str| (w.to_string(), s, succ, r.split('-').map(String::from).collect::<Vec<_>>());
        let joined = [c("腳", -3.0, false, "ㄐㄧㄠˇ"), c("叫", -3.5, false, "ㄐㄧㄠˋ"), c("兩讀", -4.0, false, "ㄐㄧㄠ")];
        let split = [c("就要", -2.0, false, "ㄐㄧㄡˋ-ㄧㄠˋ"), c("兩讀", -3.2, false, "ㄐ-ㄧㄠ"), c("接", -9.0, true, "ㄐㄧㄝ-ㄧㄠ")];
        let (lists, knives) = ([&joined[..], &split[..]], [0, 1]);
        let words = |beta: f64| abbr_mix_beta(&lists, &knives, beta).into_iter().map(|(x, k)| (x.0.as_str(), k)).collect::<Vec<_>>();
        assert_eq!(words(0.0), [("接", 1), ("就要", 1), ("腳", 0), ("兩讀", 1), ("叫", 0)]);
        assert_eq!(words(0.0), abbr_mix(&lists).into_iter().map(|(x, k)| (x.0.as_str(), k)).collect::<Vec<_>>());
        // −2 − 0.5 still beats 腳; 兩讀 at −3.2 − 0.5 = −3.7 is still its better entry but falls behind 叫.
        assert_eq!(words(0.5), [("接", 1), ("就要", 1), ("腳", 0), ("叫", 0), ("兩讀", 1)]);
        // β 2: 就要 at −4 goes last, 兩讀 is now the joined cut's entry (−4 against −5.2; the same score and length as 就要, so the reading text decides, ㄐㄧㄠ first).
        assert_eq!(words(2.0), [("接", 1), ("腳", 0), ("叫", 0), ("兩讀", 0), ("就要", 1)]);
        // The answer's cut first, as `run_abbr` passes them: the knives go with their lists.
        let got: Vec<(&str, usize)> = abbr_mix_beta(&[&split[..], &joined[..]], &[1, 0], 2.0).into_iter().map(|(x, k)| (x.0.as_str(), k)).collect();
        assert_eq!(got, [("接", 0), ("腳", 1), ("叫", 1), ("兩讀", 1), ("就要", 0)]);
        // Equal adjusted scores: the earlier list keeps the word.
        assert_eq!(abbr_mix_beta(&[&split[..], &joined[..]], &[1, 0], 0.8).iter().find(|(x, _)| x.0 == "兩讀").map(|x| x.1), Some(0));
    }

    /// `--abbr-beta 0` on the model: 西安 typed C, four cuts with 0 to 2 knives. The mixed list is `abbr_mix`'s entry for entry; a large β
    /// keeps the same words and puts every entry of the joined cut before every non-successor entry of the cuts with knives.
    #[test]
    fn abbr_mix_beta_zero_is_abbr_mix_on_the_model() {
        let e = abbr_engine(&root().join("data/lm/bigram.sjlm")).expect("data/lm/bigram.sjlm and classes.sjc are required (see cli/tests/golden.rs)");
        let cuts = abbr_cuts(&['ㄒ', 'ㄧ', 'ㄢ']).0;
        let lists = abbr_cut_lists(&e, "", &cuts).unwrap();
        let (lists, knives): (Vec<&[AbbrCand]>, Vec<usize>) = (lists.iter().map(Vec::as_slice).collect(), abbr_knives(&cuts));
        let plain = abbr_mix(&lists);
        assert!(plain.iter().any(|(_, k)| *k > 0) && plain.iter().any(|(_, k)| *k == 0));
        assert_eq!(abbr_mix_beta(&lists, &knives, 0.0), plain);
        let far = abbr_mix_beta(&lists, &knives, 1000.0);
        assert_eq!(far.len(), plain.len());
        let first_knife = far.iter().position(|(c, k)| !c.2 && knives[*k] > 0).unwrap();
        assert!(first_knife > 0 && far[first_knife..].iter().all(|(c, k)| c.2 || knives[*k] > 0), "the joined cut's entries come first in the tier");
    }

    /// `--abbr-mix`: the groups add up, the moved words count by the row and by the full lists, and the cut counts are reported.
    #[test]
    fn abbr_mix_lines_count() {
        let m = |merged, row_before, full_before, after, by_answer_cut, cuts| AbbrMix { merged, row_before, full_before, after, by_answer_cut, cuts, capped: cuts == 64, answer_cut_dropped: false };
        let mixes = [m(false, Some(1), Some(1), Some(3), true, 1), m(false, None, Some(12), Some(30), true, 2), m(false, Some(4), Some(4), Some(2), false, 64), m(true, None, None, Some(1), true, 2), m(true, None, None, None, false, 4)];
        let lines = abbr_mix_lines(&mixes, "T").unwrap();
        assert_eq!(lines[0], "## mix:all  T  {'words': 5, 'top1': 1, 'top9': 3, 'mrr': 0.3667, 'rank1': 1, 'rank2_9': 2, 'rank10plus': 1, 'absent': 1, 'top9_by_answer_cut': 2, 'top9_by_other_cut': 1}");
        assert_eq!(lines[1], "## mix:measurable  T  {'words': 3, 'top1': 0, 'top9': 2, 'mrr': 0.2778, 'rank1': 0, 'rank2_9': 2, 'rank10plus': 1, 'absent': 0, 'top9_by_answer_cut': 1, 'top9_by_other_cut': 1}");
        assert_eq!(lines[2], "## mix:merged  T  {'words': 2, 'top1': 1, 'top9': 1, 'mrr': 0.5000, 'rank1': 1, 'rank2_9': 0, 'rank10plus': 0, 'absent': 1, 'top9_by_answer_cut': 1, 'top9_by_other_cut': 0}");
        assert_eq!(lines[3], "## mix:moved-row  T  {'words': 3, 'better': 1, 'same': 1, 'worse': 1, 'worse_rank2_9': 1, 'worse_rank10plus': 0, 'worse_absent': 0}");
        assert_eq!(lines[4], "## mix:moved-full  T  {'words': 3, 'better': 1, 'same': 0, 'worse': 2, 'worse_rank2_9': 1, 'worse_rank10plus': 1, 'worse_absent': 0}");
        assert_eq!(lines[5], "## mix:cuts  T  {'words': 5, 'cuts': 73, 'one_cut': 1, 'max_cuts': 64, 'capped': 1, 'answer_cut_dropped': 0}");
        assert_eq!(lines.len(), 6);
    }

    /// `--abbr B`, the offline sets: an entry is in the set of its reading's B rewriting, syllable for syllable; a word is listed once, at its
    /// best compatible reading; the order is (-lp, word length, reading, word). 知道 is found with ㄓ ㄠ (a syllable that is only an initial keeps it).
    #[test]
    fn abbr_b_offline_sets_rank_by_the_index_order() {
        let readings = vec![
            (vec!["ㄓ", "ㄉㄠˋ"], vec![("知道", -3.0), ("之道", -5.0)]),
            (vec!["ㄓˊ", "ㄉㄠˋ"], vec![("直到", -2.5)]),
            (vec!["ㄓˇ", "ㄏㄠˇ"], vec![("只好", -3.5)]),
            (vec!["ㄓ", "ㄏㄠˋ"], vec![("之道", -4.0)]), // made up: 之道's better reading, also compatible
            (vec!["ㄓ", "ㄧㄠˋ"], vec![("只要", -1.0)]),  // ㄓ ㄧㄠ: another set
            (vec!["ㄐㄧㄣ", "ㄊㄧㄢ"], vec![("今天", -2.0)]),
            (vec!["ㄒㄧㄣ", "ㄒㄧㄢ"], vec![("新鮮", -2.0)]),
            (vec!["ㄑㄧㄣ", "ㄌㄧㄢˊ"], vec![("清廉", -2.0)]),
            (vec!["ㄧㄣ", "ㄧㄢˊ"], vec![("陰嚴", -1.0), ("音", -0.5)]),
            (vec!["ㄉㄜ˙"], vec![("的", -1.0)]),
            (vec!["ㄜˋ"], vec![("餓", -3.0)]),
            (vec!["ㄉㄜˊ"], vec![("得", -2.0), ("的", -9.0)]),
            (vec!["˙"], vec![("˙", -5.0)]),
        ];
        let sets = abbr_b_sets(readings.into_iter());
        let units = |s: &str| s.split(' ').map(String::from).collect::<Vec<_>>();
        assert_eq!(sets[&"ㄓ-ㄠ".to_string()], ["直到", "知道", "只好", "之道"], "之道 once, at -4.0");
        assert_eq!(abbr_b_rank(&sets, &units("ㄓ ㄠ"), "知道"), Some(2));
        assert_eq!(abbr_b_rank(&sets, &units("ㄓ ㄠ"), "只要"), None, "ㄓ ㄧㄠ is another set");
        // Same score: the shorter word, then the reading text (ㄐ < ㄑ < ㄒ), then the word.
        assert_eq!(sets[&"ㄧㄣ-ㄧㄢ".to_string()], ["音", "陰嚴", "今天", "清廉", "新鮮"]);
        assert_eq!(abbr_b_rank(&sets, &units("ㄧㄣ ㄧㄢ"), "今天"), Some(3));
        assert_eq!(sets[&"ㄜ".to_string()], ["的", "得", "餓"], "one syllable; the neutral tone is dropped");
        assert_eq!(abbr_b_rank(&sets, &units("ㄜ"), "的"), Some(1));
        assert!(!sets.contains_key(""), "a reading without a Zhuyin symbol is left out");
    }

    /// `--abbr`: the four rank buckets add up to the measurable words; top1, top9 and MRR read the row; the reasons add up to the excluded.
    #[test]
    fn abbr_summary_counts() {
        let item = |excluded, row_rank, full_rank| AbbrItem { len: 2, excluded, b_eq_c: false, row_rank, full_rank, keys_full: 7, keys_abbr: 2, b_rank: Some(full_rank), merge_backward: None };
        let items = [item(None, Some(1), Some(1)), item(None, Some(4), Some(4)), item(None, None, Some(12)), item(None, None, None), item(Some(1), None, None), item(Some(3), None, None)];
        let refs: Vec<&AbbrItem> = items.iter().collect();
        assert_eq!(
            abbr_summary(&refs),
            "{'words': 6, 'excl_zhuyin_word': 0, 'excl_unsegmented': 1, 'excl_over10': 0, 'excl_merged': 1, 'measurable': 4, 'top1': 1, 'top9': 2, 'mrr': 0.3125, 'rank1': 1, 'rank2_9': 1, 'rank10plus': 1, 'absent': 1, 'keys_full': 28, 'keys_abbr': 8}"
        );
        assert_eq!(abbr_b_summary(&refs), "{'words': 6, 'missing': 3, 'top1': 1, 'top9': 2, 'mrr': 0.2083, 'rank1': 1, 'rank2_9': 1, 'rank10plus': 1}");
    }

    #[test]
    fn extra_overlay_missing_file_names_the_path() {
        let missing = std::env::temp_dir().join("shanjie-no-such-extra-overlay.tsv");
        let err = extra_overlay_checked(Some(missing.to_str().unwrap().into())).unwrap_err();
        assert!(err.contains(missing.to_str().unwrap()), "{err}");
        assert_eq!(extra_overlay_checked(None), Ok(None));
    }
}
