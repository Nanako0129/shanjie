//! `shanjie-replay` (docs/contracts/engine-replay.md): rows typed key by key through the real engine, set up like the App.
//! Research tool, not shipped. Output is numbers only: the summary line on stdout, `--rowstats`, `--keys` and `--firsts` files.
//!
//! shanjie-replay (--rows FILE | --dev N) [--profile chat|formal] [--no-tinylm] [--learn off|on] [--rerank each|final]
//!                [--learn-file FILE] [--correct] [--rowstats FILE] [--keys FILE] [--firsts FILE]
use cli::common::{dev_rows, format_rowstats, load_lenient, read_rows, root};
use cli::replay::{build_engine, format_keys, replay_row, summary_line, Cfg, Rerank, RowIn};
use core::eval::sha256_hex;
use core::learn_store::{Opened, FILE};
use core::lm::Profile;
use std::fs;
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

/// Whether two paths are the same file (same device and inode, so hard links and symlinks count); a path that does not exist is not.
fn same_file(a: &str, b: &str) -> bool {
    match (fs::metadata(a), fs::metadata(b)) {
        (Ok(x), Ok(y)) => (x.dev(), x.ino()) == (y.dev(), y.ino()),
        _ => false,
    }
}

/// A private (0700) per-run directory holding the copy of the `--learn-file`; removed when dropped, also on error paths.
struct TempDir(PathBuf);
impl TempDir {
    fn new() -> Result<TempDir, String> {
        let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_nanos());
        let dir = std::env::temp_dir().join(format!("shanjie-replay-learn-{}-{nanos}", std::process::id()));
        fs::DirBuilder::new().mode(0o700).create(&dir).map_err(|e| format!("cannot create temporary directory ({:?})", e.kind()))?;
        Ok(TempDir(dir))
    }
}
impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn sha8(p: &Path) -> Result<String, String> {
    Ok(sha256_hex(&fs::read(p).map_err(|e| format!("cannot read model file ({:?})", e.kind()))?)[..8].to_string())
}

fn run() -> Result<(), String> {
    let (mut rows_file, mut dev, mut profile_name) = (None::<String>, None::<usize>, "chat".to_string());
    let (mut tl, mut learn, mut cfg) = (true, false, Cfg { correct: false, rerank: Rerank::Each });
    let (mut rowstats, mut keys, mut firsts) = (None::<String>, None::<String>, None::<String>);
    let mut learn_file = None::<String>;
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        let mut val = || it.next().ok_or_else(|| "missing option value".to_string());
        match a.as_str() {
            "--rows" => rows_file = Some(val()?),
            "--dev" => dev = Some(val()?.parse().map_err(|_| "bad number for --dev")?),
            "--profile" => profile_name = val()?,
            "--no-tinylm" => tl = false,
            "--learn" => {
                learn = match val()?.as_str() {
                    "off" => false,
                    "on" => true,
                    _ => return Err("--learn must be off or on".into()),
                }
            }
            "--rerank" => {
                cfg.rerank = match val()?.as_str() {
                    "each" => Rerank::Each,
                    "final" => Rerank::Final,
                    _ => return Err("--rerank must be each or final".into()),
                }
            }
            "--learn-file" => learn_file = Some(val()?),
            "--correct" => cfg.correct = true,
            "--rowstats" => rowstats = Some(val()?),
            "--keys" => keys = Some(val()?),
            "--firsts" => firsts = Some(val()?),
            _ => return Err("unknown argument".into()),
        }
    }
    let profile = match profile_name.as_str() {
        "chat" => Profile::Chat,
        "formal" => Profile::Formal,
        _ => return Err("--profile must be chat or formal".into()),
    };
    let root = root();
    let len = load_lenient()?;
    // An output that is the rows file would be emptied before the rows are read, and one that is the learning file would empty
    // it: refuse, comparing the files themselves (device and inode). `--firsts` could print learned words of the private file.
    for (opt, f) in [("--rowstats", &rowstats), ("--keys", &keys), ("--firsts", &firsts)] {
        for (what, src) in [("--rows", &rows_file), ("--learn-file", &learn_file)] {
            if let (Some(f), Some(src)) = (f, src) {
                if same_file(f, src) {
                    eprintln!("error: {opt} is the same file as {what}");
                    std::process::exit(2);
                }
            }
        }
    }
    if learn_file.is_some() && firsts.is_some() {
        return Err("--firsts cannot be combined with --learn-file (first displays can contain learned words)".into());
    }
    // The CLI's own readers (shared in cli::common), so the rows and their numbers are shanjie-eval's.
    let rows: Vec<RowIn> = match (&rows_file, dev) {
        (Some(f), None) => read_rows(f)?,
        (None, Some(n)) => dev_rows(n)?,
        _ => return Err("need exactly one of --rows, --dev".into()),
    }
    .into_iter()
    .map(|(sent, syls, ctx)| RowIn { ctx, sent, syls })
    .collect();
    let name = match (&rows_file, dev) {
        (Some(f), _) => Path::new(f).file_name().map_or(String::new(), |s| s.to_string_lossy().into_owned()),
        (_, n) => format!("dev{}", n.unwrap_or(0)),
    };
    let (lm_path, tl_path) = (root.join("data/lm/bigram.sjlm"), root.join("data/lm/tinylm.sjtl"));
    // The model hashes make the result traceable to its files: all on stderr, the two that decide the choice in the summary tag.
    let lm8 = sha8(&lm_path)?;
    let tl8 = if tl { Some(sha8(&tl_path)?) } else { None };
    let others: Vec<String> = ["classes.sjc", "kn.sjkn", "trigram.sjt3"]
        .iter()
        .filter(|f| lm_path.with_file_name(f).is_file())
        .map(|f| Ok(format!("{f}:{}", sha8(&lm_path.with_file_name(f))?)))
        .collect::<Result<_, String>>()?;
    eprintln!("models: bigram.sjlm:{lm8} {} tinylm.sjtl:{}", others.join(" "), tl8.as_deref().unwrap_or("none"));
    let tag = format!(
        "replay-{profile_name}+ctx+acg+nopred+noen+lm:{lm8}{}{}{}{}{}",
        tl8.as_ref().map_or("-notinylm".to_string(), |h| format!("+tl:{h}")),
        if learn { "+learn" } else { "" },
        if cfg.rerank == Rerank::Final { "+rerank:final" } else { "" },
        if cfg.correct { "+correct" } else { "" },
        if learn_file.is_some() { "+learnfile" } else { "" },
    );
    let mut e = build_engine(&root, &lm_path, tl.then_some(tl_path.as_path()), profile, learn)?;
    // The file is copied (bytes only) into a private temporary directory under the name the store loads; core opens, and with
    // `--learn on` writes, only the copy. Anything but a normal load is an error: a replay without the learnings would mislead.
    if let Some(f) = &learn_file {
        // Bytes only (no mode, flags, ACLs or xattrs of the source), read before any output file is created. The learner is in
        // memory after `learning_open`, so the copy is removed right away; with `--learn on` later saves fail harmlessly
        // against the missing directory.
        let bytes = fs::read(f).map_err(|e| format!("cannot read --learn-file ({:?})", e.kind()))?;
        let tmp = TempDir::new()?;
        let mut copy = fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(tmp.0.join(FILE)).map_err(|e| format!("cannot write temporary copy ({:?})", e.kind()))?;
        copy.write_all(&bytes).map_err(|e| format!("cannot write temporary copy ({:?})", e.kind()))?;
        drop(copy);
        let opened = e.learning_open(&tmp.0).map_err(|err| format!("learning_open failed ({err:?})"))?;
        if let Err(err) = fs::remove_dir_all(&tmp.0) {
            eprintln!("warning: cannot remove temporary directory ({:?})", err.kind());
        }
        let Opened::Loaded { skipped } = opened else { return Err("--learn-file is not a valid learning file".into()) };
        let n = e.learner().records().len();
        eprintln!("learning file: {n} records, {skipped} lines skipped");
        if n == 0 {
            return Err("--learn-file has no usable records".into());
        }
    }
    // Opened after the rows are read, like the CLI's outputs are not touched by a failed start.
    let create = |f: Option<String>| f.map(|f| fs::File::create(f).map_err(|e| format!("cannot create output file ({:?})", e.kind()))).transpose();
    let (rowstats, keys, firsts) = (create(rowstats)?, create(keys)?, create(firsts)?);
    let mut outs = Vec::with_capacity(rows.len());
    for (i, r) in rows.iter().enumerate() {
        outs.push(replay_row(&mut e, &r.ctx, &r.syls, &r.sent, &len, cfg).map_err(|m| format!("row {}: {m}", i + 1))?);
    }
    let write = |f: Option<fs::File>, text: String| -> Result<(), String> {
        use std::io::Write;
        f.map_or(Ok(()), |mut f| f.write_all(text.as_bytes()).map_err(|e| format!("cannot write output file ({:?})", e.kind())))
    };
    write(rowstats, format_rowstats(&outs.iter().zip(&rows).map(|(o, r)| (o.first_ok, o.first.clone(), r.sent.clone())).collect::<Vec<_>>()))?;
    write(keys, format_keys(&outs))?;
    write(firsts, outs.iter().map(|o| format!("{}\n", o.first)).collect())?;
    let gold_chars = rows.iter().map(|r| r.sent.chars().count()).sum();
    println!("{}", summary_line(&name, &tag, &outs, gold_chars));
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        eprintln!("error: {e}");
        std::process::exit(1);
    }
}
