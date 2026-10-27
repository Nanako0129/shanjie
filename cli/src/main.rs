use cli::common::{dev_rows, format_rowstats, levenshtein, load_lenient, read_rows, root, three_field_rows};
use core::colloquial::{self, Colloquial, ReadingChannel};
use core::engine::{capping_overlay_detailed, load_lexicon_extra_detailed, load_lexicon_packs_detailed, read_packs_detailed, PACK_ACG, PACK_IT, PACK_OOV};
use core::eval::*;
use core::learn::{context_key, SENTINEL};
use core::lm::{decode_from, history, CappedLexicon, Demote, Lm, Profile};
use core::tinylm::{KnPairing, RerankLevel, TinyLm, TinyLmError, TOPK};
use core::predict::{parse_units, predict, units_of, units_str, Index, Mode};
use core::{decode_beam, Error, Lexicon, NoLearning, Syls, BEAM_S1};
use std::collections::BTreeMap;
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

/// Option combinations of `lm`, checked before any file is read. The holdout never prints its sentences, so
/// `--dump` and `--dump-words` (`dump` is true for either) are refused there; `--rowstats` holds numbers only and is allowed.
fn check_lm_opts(set: Option<&str>, dump: bool, packs: u32, packs_dir: bool) -> Result<(), String> {
    if set == Some("holdout") && dump {
        return Err("--dump and --dump-words are not allowed with --set holdout".into());
    }
    // (--tinylm-scores with the holdout is refused in run_lm: its per-candidate record is what alpha and tau are tuned on.)
    if packs == 0 && packs_dir {
        return Err("--packs-dir needs --packs".into());
    }
    Ok(())
}

/// `--packs acg`, `--packs it`, `--packs acg,it`: sets the pack bits; anything else is refused.
fn pack_flag(v: &str) -> Result<u32, String> {
    v.split(',').try_fold(0, |bits, name| match name {
        "acg" => Ok(bits | PACK_ACG),
        "it" => Ok(bits | PACK_IT),
        _ => Err("--packs takes acg, it or acg,it".to_string()),
    })
}

/// The packs directory (`--packs-dir` or data/packs); a run that asks for a pack must fail loudly when its file is
/// missing (the engine tolerates it), so this is checked with the other options, before the model is read.
fn packs_dir_checked(packs: u32, packs_dir: Option<String>) -> Result<PathBuf, String> {
    let pdir = packs_dir.map_or_else(|| root().join("data/packs"), PathBuf::from);
    for (bit, name) in [(PACK_ACG, "acg"), (PACK_IT, "it")] {
        let file = pdir.join(format!("{name}-add.tsv"));
        if packs & bit != 0 && !file.is_file() {
            return Err(format!("--packs {name}: pack file not found: {}", file.display()));
        }
    }
    Ok(pdir)
}

/// `--extra-overlay FILE` (evaluation only, names-lexicon contract section 3.5): a missing file stops the run naming the path,
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
/// table on, and the Kneser-Ney side file it records, or none: model-v6 section 2.7). A different model hash, another side file
/// state, `--no-classes` or `--no-demote` is refused before anything is printed. The file is read, checked and paired by the
/// engine's loader (`TinyLm::load_paired`, S7c-2a). Returns the model, the load time (read + parse + the pairing checks and the file's hash) and the sha8 of the bytes it parsed.
fn load_tinylm(path: &str, lm: &Lm, classes: bool, demote: bool) -> Result<(TinyLm, Duration, String), String> {
    if !classes || !demote {
        return Err("--tinylm: this .sjtl was tuned with the class term and the demotion table on (drop --no-classes / --no-demote)".into());
    }
    let t0 = Instant::now();
    let tl = TinyLm::load_paired(Path::new(path), lm).map_err(|e| match e {
        // model-v6 section 7 (P4-9): each mismatch has its own remedy; --no-kn helps only when the file was tuned without KN.
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

/// One `--tinylm-scores` line, the record of experiments/s7a-tinylm/score.py without the key columns and the two other LLs:
/// `i, n, ng, ok, ed, glen`, then `c` (the 0-based index the rerank chose, 0 when it did not run; tools/triage.py) and, for n >= 2, `ll`.
/// Numbers only.
fn scores_line(i: usize, ng: &[f64], ok: &[bool], ed: &[u32], glen: usize, chosen: usize, ll: Option<&[f64]>) -> Result<String, String> {
    // JSON has no inf / NaN (Python's json.loads rejects inf, and a NaN would slip through parity.py's tolerance test).
    if !ng.iter().chain(ll.unwrap_or(&[])).all(|x| x.is_finite()) {
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
        s += &format!(", \"ll\": {}", list(ll.iter().map(|x| format!("{x:?}")).collect()));
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
    rerank: Option<Duration>,
    /// The row's chosen candidate (the `top1_sha256` input and the rowstats `top1`).
    first: String,
    scores: String,
    dump: String,
    words: String,
}

/// S2c LM mode (docs/PLAN.md S2c): the one summary line of lm_eval.py, plus the optional `--dump` and `--rowstats`.
/// S7c-1: `--tinylm FILE.sjtl` reranks the first 8 candidates of every row with the small character model (docs/contracts/s7c-core-tinylm.md);
/// `--tinylm-scores OUT` writes score.py's numbers-only records for them. `--dump-words OUT` (tools/triage.py) writes, per row, the
/// word split of every `--dump` line (`row\trank\twords`, same ranks and order) after rank 0, the gold sentence split by the core's
/// `segment_words` (`-` when it cannot be split). `--rerank-level conservative|standard|aggressive` (default standard) picks which of
/// the file's (alpha, tau, delta) the rerank uses (docs/contracts/rerank-strength.md section 2.3). `--threads N` (default 1) decodes the
/// rows on N threads sharing one copy of the models; the output files and the summary line are byte for byte those of N = 1. With N
/// above 1 the stderr tinylm latency line has other timings (measured under contention) and ends with " (threads N: measured under
/// contention)", N being the worker count after capping it at the number of 32-row blocks; its counts are the same, and with one
/// worker the line is as for N = 1. A panic in a row exits 101 on the inline path (N = 1, or one block) and exits 1 with "row N
/// panicked in a --threads worker" on the threaded path. Only the `lm` mode takes it: the other modes refuse it
/// as an unknown argument.
///
/// `--colloquial-core FILE.sjlm` (docs/contracts/colloquial-term.md section 2.3): loads the colloquial table like the engine's
/// `load_colloquial` (word sets of k from `data/lexicon` and the `--packs` rows) and decides per run, by the engine's conditions (chat
/// profile, any rerank level (each with its own alpha, tau and delta: docs/contracts/colloquial-levels.md), a `--tinylm` whose SHA-256 is the one
/// the term was measured with), whether each row's rerank is `TinyLm::rerank_colloquial` (the word split of each of the first 8 candidates goes in,
/// and the `--rerank-level`) or the plain `rerank`. It refuses no setting, so
/// "everything else is unchanged" can be checked with the same option; a missing file stops the run before anything is read. The summary
/// line gains `+colloq:<sha8 of the table>`. The files follow the n-gram order as without the option; `--tinylm-scores` `c` is the pick.
///
/// `--no-reading-channel` (research only; docs/contracts/reading-channel.md section 2.2): where the colloquial term applies it is applied alone, the P4d
/// cell. Without it the reading-channel term goes with it (the cell `lex,1.0,4` of experiments/p5-reading-channel: each single-character word's reading
/// against that word's most common reading, from the lexicon this run loaded), `TinyLm::rerank_colloquial` being the one function for the CLI and the
/// engine. It changes nothing where the colloquial term does not apply. The summary tag marks the shipping behavior: where the colloquial term
/// applies and the channel is on it reads `+colloq:<sha8>+rc`; with the option, or where the term does not apply (formal or another
/// small model), just `+colloq:<sha8>`, so a P4d-cell run is never taken for the shipping setting. The option alone, without `--colloquial-core`,
/// changes nothing.
fn run_lm(args: &[String], len: &Lenient) -> Result<(), String> {
    let (mut lm_path, mut profile, mut name, mut dev, mut rows_file) = (None, None, None, None, None);
    let (mut limit, mut set, mut dump, mut ctx_mode, mut demote) = (None::<usize>, None, None, false, true);
    let mut dump_words = None::<String>;
    let (mut classes, mut kn, mut tri) = (true, true, true);
    // model-v6 section 2.5: the names layer is always on; `--no-names` (evaluation only: the section 3.1 breakdown, and
    // tools/build_names.py, which passes its own candidate list as --extra-overlay) leaves it out.
    let mut names = true;
    let (mut rowstats, mut tinylm, mut tinylm_scores) = (None::<String>, None::<String>, None::<String>);
    let mut level = None::<RerankLevel>;
    // acg-pack contract A.2: `--packs acg` adds data/packs/acg-add.tsv (or `--packs-dir DIR`'s) to the lexicon and the cap; off by default.
    let (mut packs, mut packs_dir) = (0u32, None::<String>);
    let mut extra_overlay = None::<String>;
    let mut threads = 1usize;
    let mut colloquial_core = None::<String>;
    let mut reading_channel = true;
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
            "--tinylm" => tinylm = Some(val()?),
            "--tinylm-scores" => tinylm_scores = Some(val()?),
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
            "--no-reading-channel" => reading_channel = false,
            _ => return Err("unknown argument".into()),
        }
    }
    check_lm_opts(set.as_deref(), dump.is_some() || dump_words.is_some(), packs, packs_dir.is_some())?;
    if tinylm_scores.is_some() && tinylm.is_none() {
        return Err("--tinylm-scores needs --tinylm".into());
    }
    // Section 9 P3-8: without a small model a level (standard included) would silently measure the n-gram alone.
    if level.is_some() && tinylm.is_none() {
        return Err("--rerank-level needs --tinylm".into());
    }
    let level = level.unwrap_or(RerankLevel::Standard);
    if tinylm_scores.is_some() && set.as_deref() == Some("holdout") {
        return Err("--tinylm-scores is not allowed with --set holdout".into());
    }
    let pdir = packs_dir_checked(packs, packs_dir)?;
    let extra_overlay = extra_overlay_checked(extra_overlay)?;
    let colloquial_core = colloquial_checked(colloquial_core)?;
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
    // A kn.sjkn beside the model (docs/contracts/kn-core.md) turns the Kneser-Ney term on unless --no-kn. The trigram table beside it
    // (docs/contracts/sj-lm-7-pr2.md) is the profile's own, read unless --no-tri: `trigram.sjt3` for formal, `trigram-chat.sjt3` for chat
    // (the other profile's file is not read, so a bad one does not matter here). Both profiles score with Kneser-Ney and their table.
    let tri_profiles: &[Profile] = if tri { std::slice::from_ref(&prof) } else { &[] };
    let lm = Lm::load_opts(std::path::Path::new(&lm_path), classes, kn, tri_profiles).map_err(|e| e.to_string())?;
    let scoring = lm.scoring(prof);
    let rss_before = tinylm.is_some().then(rss_kb).flatten();
    let tl = tinylm.as_deref().map(|p| load_tinylm(p, &lm, classes, demote)).transpose()?;
    // Section 2.3: a version 1 or 2 file has only the standard set; another level would silently measure the standard one.
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
            Some(Colloquial::load_from_dir(f, &dir, Some(&pack_text)).map_err(|e| format!("--colloquial-core: {e}"))?)
        }
        None => None,
    };
    // The engine's conditions for the term (the table is loaded by here): `colloquial::applies` is the engine's own test.
    let colloq_on = colloq.as_ref().filter(|_| tl.as_ref().is_some_and(|(m, _, _)| colloquial::applies(prof, m)));
    let (lex, overlay) = load_with_packs(&dir, packs, &pdir, extra_overlay.as_deref(), names)?;
    let demote_rows = fs::read_to_string(dir.join("demote.tsv")).map_err(|e| format!("cannot read demote.tsv ({:?})", e.kind()))?;
    let table = Demote::parse(&demote_rows).filter(|d| d.check(&lex)).ok_or("bad demote.tsv")?;
    // The table is always loaded, so a malformed one stops the run even with --no-demote.
    // The rows of the pack-oov packs (not `--extra-overlay`, not other packs): their words are unknown to the model and keep the pack's score (pack-oov contract).
    let pack_rows = read_packs_detailed(&pdir, packs & PACK_OOV).map_err(|e| e.to_string())?;
    let capped = CappedLexicon::with_packs(lex.clone(), &overlay, &pack_rows, &lm, Some(&table)).ok_or("bad demote.tsv")?;

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
    let mut scores = match tinylm_scores {
        Some(f) => Some(std::io::BufWriter::new(fs::File::create(f).map_err(|e| format!("cannot create tinylm scores ({:?})", e.kind()))?)),
        None => None,
    };
    let (want_scores, want_dump, want_words) = (scores.is_some(), dump.is_some(), dump_words.is_some());
    // `--threads N`: the rows are decoded in blocks of BLOCK contiguous rows; no more workers than blocks.
    const BLOCK: usize = 32;
    let (nrows, nblocks) = (rows.len(), rows.len().div_ceil(BLOCK));
    let threads = threads.min(nblocks.max(1));
    // One row, a function of the row alone (nothing carries over from one row to the next), so the rows can be decoded in any
    // order or at once. Everything the row writes is built here as text; the loop below puts it in the files, in row order.
    let work = |i: usize| -> Result<RowOut, String> {
        let (truth, syls, ctx) = &rows[i];
        // S2h: the first word is conditioned on the row's context, cut like the engine cuts it.
        let left = if ctx_mode { Some(context_key(ctx)) } else { None };
        let start = left.as_deref().filter(|k| *k != SENTINEL).map_or("<s>", |k| history(k, &lm));
        let nb = decode_from(&capped, syls, &lm, prof, BEAM_S1, start, demote).map_err(|e| e.to_string())?;
        let mut out = RowOut::default();
        let mut surf: Vec<String> = nb.iter().map(|(_, ws)| ws.concat()).collect();
        let t = len.apply(truth);
        // The row's chosen candidate: the first, or the small model's pick among the first 8 (the dump keeps the decoder's order).
        let mut chosen = 0;
        if let Some((tl, _, _)) = &tl {
            let top: Vec<&str> = surf.iter().take(TOPK).map(String::as_str).collect();
            let ng: Vec<f64> = nb.iter().take(TOPK).map(|(sc, _)| *sc).collect();
            // The key is the engine's (`TinyLm::key_for` of the row's context, the display before the one stretch being empty): the
            // context key for versions 1 to 4 (its SENTINEL is the empty k, in TinyLm::rerank), the last L characters as they are for
            // a model declaring L (docs/contracts/context-return.md section 2); without --context, no key.
            let t0 = Instant::now();
            let key = if ctx_mode { tl.key_for(ctx) } else { String::new() };
            let key = key.as_str();
            let picked = match colloq_on {
                Some(co) => {
                    let words: Vec<Vec<&str>> = nb.iter().take(TOPK).map(|(_, ws)| ws.iter().map(String::as_str).collect()).collect();
                    let rc = reading_channel.then_some(ReadingChannel { lex: &lex, syls });
                    tl.rerank_colloquial(&ng, &top, &words, key, co, rc, level)
                }
                None => tl.rerank(&ng, &top, key, tl.params(prof, level)),
            };
            let ll = match picked {
                Ok((c, ll)) => {
                    chosen = c;
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
            if want_scores {
                let oks: Vec<bool> = top.iter().map(|s| len.apply(s) == t).collect();
                let ed: Vec<u32> = top.iter().zip(&oks).map(|(s, &ok)| if ok { 0 } else { levenshtein(s, truth) }).collect();
                out.scores = scores_line(i + 1, &ng, &oks, &ed, truth.chars().count(), chosen, ll.as_deref())?;
            }
        }
        out.ok = len.apply(&surf[chosen]) == t;
        out.in64 = surf.iter().any(|s| len.apply(s) == t);
        if want_dump {
            for (r, ((sc, _), s)) in nb.iter().zip(&surf).enumerate() {
                writeln!(out.dump, "{}\t{}\t{s}\t{sc:?}", i + 1, r + 1).unwrap();   // a String: cannot fail
            }
        }
        if want_words {
            let gold = lex.segment_words(truth).map_or("-".to_string(), |ws| ws.iter().map(|(w, _)| w.as_str()).collect::<Vec<_>>().join(" "));
            writeln!(out.words, "{}\t0\t{gold}", i + 1).unwrap();
            for (r, (_, ws)) in nb.iter().enumerate() {
                writeln!(out.words, "{}\t{}\t{}", i + 1, r + 1, ws.join(" ")).unwrap();
            }
        }
        out.first = surf.swap_remove(chosen);
        Ok(out)
    };
    let (mut top1, mut o64, mut firsts, mut rs, mut rerank, mut long) = (0usize, 0usize, Vec::new(), Vec::new(), Vec::new(), 0usize);
    // Puts one row's output in the files and the counters; called in row order, so every file and the summary line are those of N = 1.
    let mut emit = |i: usize, out: RowOut| -> Result<(), String> {
        top1 += out.ok as usize;
        o64 += out.in64 as usize;
        long += out.long as usize;
        rerank.extend(out.rerank);
        if let Some(w) = scores.as_mut() {
            w.write_all(out.scores.as_bytes()).map_err(|_| "cannot write tinylm scores".to_string())?;
        }
        if let Some(d) = dump.as_mut() {
            d.write_all(out.dump.as_bytes()).map_err(|_| "cannot write dump".to_string())?;
        }
        if let Some(d) = dump_words.as_mut() {
            d.write_all(out.words.as_bytes()).map_err(|_| "cannot write word dump".to_string())?;
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
    if let (Some((_, load, _)), false) = (&tl, rerank.is_empty()) {
        // Latency record (S7c-1 item 6), stderr only: one rerank = the forward pass over up to 8 candidates of a row (rows with fewer
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
    if let Some(mut f) = rowstats {
        f.write_all(format_rowstats(&rs).as_bytes()).map_err(|e| format!("cannot write rowstats ({:?})", e.kind()))?;
    }
    let sha = sha256_hex(firsts.join("\n").as_bytes());
    // A non-standard level is a different setting from the shipping one: tools/bench.py and tools/triage.py compare the whole tag, so it fails their cells.
    let rl_tag = match level {
        RerankLevel::Standard => "",
        RerankLevel::Conservative => "+rl:conservative",
        RerankLevel::Aggressive => "+rl:aggressive",
    };
    println!("## {name}  lm-{profile_name}{}{}{}{}{}{}{}{}{}{xo}{}{}  {{'n': {}, 'top1': {top1}, 'oracle@64': {o64}, 'top1_sha256': '{sha}'}}", if ctx_mode { "+ctx" } else { "" }, if demote { "" } else { "-nodemote" }, if classes { "" } else { "-noclasses" }, if names { "" } else { "-nonames" }, lm.kn_tag(prof).unwrap_or_default(), tl.as_ref().map_or(String::new(), |(_, _, sha8)| format!("+tl:{sha8}")), rl_tag, if packs & PACK_ACG != 0 { "+acg" } else { "" }, if packs & PACK_IT != 0 { "+it" } else { "" }, if lm.uses_tri(scoring) { lm.tri_tag(prof).unwrap_or("") } else { "" }, colloq.as_ref().map_or(String::new(), |co| format!("+colloq:{}{}", &co.sha256()[..8], if colloq_on.is_some() && reading_channel { "+rc" } else { "" })), rows.len());
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
    check_lm_opts(None, false, packs, packs_dir.is_some())?;
    let pdir = packs_dir_checked(packs, packs_dir)?;
    let profile = match profile.ok_or("--profile is required")?.as_str() {
        "chat" => Profile::Chat,
        "formal" => Profile::Formal,
        _ => return Err("--profile must be chat or formal".into()),
    };
    // A kn.sjkn beside the model turns the Kneser-Ney term on unless --no-kn, as in the other modes: the App's prediction row
    // scores with it (Engine::recompute_pred -> predict -> Lm::word_by_id), at the profile's beta like decoding (formal 0.75,
    // chat the file's), and the Python reference (experiments/sp/predict.py) loads it the same way for chat.
    // The trigram table is never read here: the prediction row scores with the bigram whatever the profile (trigram-core.md (d)).
    let lm = Lm::load_opts(Path::new(&lm_path.ok_or("--lm is required")?), true, kn, &[]).map_err(|e| e.to_string())?;
    let dir = root().join("data/lexicon");
    let (lex, overlay) = load_with_packs(&dir, packs, &pdir, None, true)?;
    // Prediction scores use the capped lp only; demotion applies to decoding (sw §3).
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

fn run() -> Result<(), String> {
    let all: Vec<String> = std::env::args().skip(1).collect();
    // zhen-mixed section 4: a subcommand of its own, before anything the evaluation modes read.
    if all.first().map(String::as_str) == Some("english-sim") {
        return english::run(&all[1..], &root());
    }
    // english-next.md section 3.2: drives the engine with and without the table; reads no lenient table.
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lm_opts_holdout_allows_rowstats_not_dump() {
        // --rowstats is not an argument: it is allowed everywhere, holdout included.
        assert!(check_lm_opts(Some("holdout"), false, 0, false).is_ok());
        assert!(check_lm_opts(Some("holdout"), true, 0, false).is_err());
        assert!(check_lm_opts(None, true, 0, false).is_ok());
        assert!(check_lm_opts(None, false, 0, true).is_err(), "--packs-dir without --packs");
        assert!(check_lm_opts(None, false, PACK_ACG, true).is_ok());
    }

    /// it-pack contract 3.1: `--packs` takes acg, it, acg,it (and the flag can repeat); anything else is refused; a requested pack's file must exist.
    #[test]
    fn packs_flag_takes_acg_it_and_both() {
        assert_eq!(pack_flag("acg"), Ok(PACK_ACG));
        assert_eq!(pack_flag("it"), Ok(PACK_IT));
        assert_eq!(pack_flag("acg,it"), Ok(PACK_ACG | PACK_IT));
        assert_eq!(pack_flag("it,acg"), Ok(PACK_ACG | PACK_IT));
        assert!(pack_flag("").is_err() && pack_flag("quotes").is_err() && pack_flag("acg,").is_err());
        let dir = std::env::temp_dir().join(format!("shanjie-packs-flag-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("acg-add.tsv"), "").unwrap();
        let d = || Some(dir.to_string_lossy().to_string());
        assert!(packs_dir_checked(PACK_ACG, d()).is_ok());
        let e = packs_dir_checked(PACK_ACG | PACK_IT, d()).unwrap_err();
        assert!(e.contains("--packs it") && e.contains("it-add.tsv"), "{e}");
        fs::remove_dir_all(&dir).unwrap();
    }

    /// names-lexicon 3.5: `--extra-overlay` rows are capped like pack rows. Goes through the CLI's own load path
    /// (`load_with_packs`, then `CappedLexicon::new`); a word the corpus never saw is scored fixture score minus 1.0.
    /// If the rows reached only the lexicon and not the capping text, the score would stay -7.5. 鮮爭 is a made-up name (not in the lexicon, count 0: the names layer's
    /// real words are counted into the model since SJ-LM-7, so none of them is a word the corpus never saw); loading the same row twice is a parse error.
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

    #[test]
    fn extra_overlay_missing_file_names_the_path() {
        let missing = std::env::temp_dir().join("shanjie-no-such-extra-overlay.tsv");
        let err = extra_overlay_checked(Some(missing.to_str().unwrap().into())).unwrap_err();
        assert!(err.contains(missing.to_str().unwrap()), "{err}");
        assert_eq!(extra_overlay_checked(None), Ok(None));
    }
}
