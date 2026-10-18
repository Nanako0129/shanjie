//! `shanjie-replay` (docs/contracts/engine-replay.md): rows typed key by key through the real engine, set up like the App.
//! Research tool, not shipped. Output is numbers only: the summary line on stdout, `--rowstats`, `--keys` and `--firsts` files.
//!
//! shanjie-replay (--rows FILE | --dev N) [--profile chat|formal] [--no-tinylm] [--learn off|on] [--rerank each|final]
//!                [--correct] [--rowstats FILE] [--keys FILE] [--firsts FILE]
use cli::common::{format_rowstats, load_lenient, root};
use cli::replay::{build_engine, format_keys, load_rows, replay_row, summary_line, Cfg, Rerank};
use core::eval::sha256_hex;
use core::lm::Profile;
use std::fs;
use std::path::Path;

fn sha8(p: &Path) -> Result<String, String> {
    Ok(sha256_hex(&fs::read(p).map_err(|e| format!("cannot read model file ({:?})", e.kind()))?)[..8].to_string())
}

fn run() -> Result<(), String> {
    let (mut rows_file, mut dev, mut profile_name) = (None::<String>, None::<usize>, "chat".to_string());
    let (mut tl, mut learn, mut cfg) = (true, false, Cfg { correct: false, rerank: Rerank::Each });
    let (mut rowstats, mut keys, mut firsts) = (None::<String>, None::<String>, None::<String>);
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
    // Opened before the run, like the CLI's outputs: an unwritable path fails now, not after the whole set.
    let create = |f: Option<String>| f.map(|f| fs::File::create(f).map_err(|e| format!("cannot create output file ({:?})", e.kind()))).transpose();
    let (rowstats, keys, firsts) = (create(rowstats)?, create(keys)?, create(firsts)?);
    let rows = load_rows(&root, rows_file.as_deref(), dev)?;
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
        "replay-{profile_name}+ctx+acg+nopred+noen+lm:{lm8}{}{}{}{}",
        tl8.as_ref().map_or("-notinylm".to_string(), |h| format!("+tl:{h}")),
        if learn { "+learn" } else { "" },
        if cfg.rerank == Rerank::Final { "+rerank:final" } else { "" },
        if cfg.correct { "+correct" } else { "" },
    );
    let mut e = build_engine(&root, &lm_path, tl.then_some(tl_path.as_path()), profile, learn)?;
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
