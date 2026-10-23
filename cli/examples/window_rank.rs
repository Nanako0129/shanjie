//! The candidate-window rank measurement of docs/contracts/reading-decoder.md section 2.6 (numbers only; evaluation, not shipped).
//!
//!   cargo run --release --locked -p cli --example window_rank -- --rows FILE [--name NAME] [--packs acg] [--detail FILE]
//!
//! For every row of the file (`前文|句子|讀音`, the rows of `shanjie-eval --rows`) and every character position of the sentence, the window of the
//! position's correct reading alone (a composition of that one syllable, prediction off) is listed in the lexicon's order (before) and in the
//! reading term's order (after, `Lexicon::entries_by_reading`, the one function the engine's window, the English windows and the digit layer use);
//! the correct character's place in each, counting from 1 in the list with each word once, is its rank. A row whose character count is not its
//! syllable count is skipped whole; a position whose character the lexicon lacks under its reading is not counted. The lexicon is the engine's
//! (`load_lexicon_packs`, with the acg pack for `--packs acg`).
//!
//! Output: one summary line of counts, no characters. `--detail FILE` also writes `row<TAB>position<TAB>before<TAB>after` per counted position
//! (numbers only; row and position count from 1).
use cli::common::{read_rows, root};
use core::engine::{load_lexicon_packs, PACK_ACG};
use std::io::Write;

/// The first page of the window (`PAGE_SIZE` of core/src/engine.rs).
const PAGE: usize = 9;

fn rank(list: &[(&str, f64)], ch: &str) -> Option<usize> {
    let mut seen = std::collections::HashSet::new();
    let mut n = 0;
    for (w, _) in list {
        if seen.insert(*w) {
            n += 1;
            if *w == ch {
                return Some(n);
            }
        }
    }
    None
}

fn main() -> Result<(), String> {
    let (mut rows_file, mut name, mut packs, mut detail) = (None::<String>, None::<String>, 0u32, None::<String>);
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        let mut val = || it.next().ok_or_else(|| "missing option value".to_string());
        match a.as_str() {
            "--rows" => rows_file = Some(val()?),
            "--name" => name = Some(val()?),
            "--packs" => {
                if val()? != "acg" {
                    return Err("--packs takes acg".into());
                }
                packs = PACK_ACG;
            }
            "--detail" => detail = Some(val()?),
            _ => return Err("unknown argument".into()),
        }
    }
    let rows_file = rows_file.ok_or("--rows is required")?;
    let rows = read_rows(&rows_file)?;
    let name = name.unwrap_or_else(|| std::path::Path::new(&rows_file).file_name().map_or(String::new(), |s| s.to_string_lossy().into_owned()));
    let packs_dir = root().join("data/packs");
    let (lex, _) = load_lexicon_packs(&root().join("data/lexicon"), (packs != 0).then_some((packs_dir.as_path(), packs))).map_err(|e| format!("{e:?}"))?;
    let mut out = detail.map(|f| std::fs::File::create(f).map(std::io::BufWriter::new).map_err(|e| format!("cannot create detail ({:?})", e.kind()))).transpose()?;
    let (mut skipped_rows, mut not_in_lexicon, mut positions) = (0usize, 0usize, 0usize);
    let (mut better, mut worse, mut same, mut dropped) = (0usize, 0usize, 0usize, 0usize);
    for (i, (truth, syls, _)) in rows.iter().enumerate() {
        let chars: Vec<char> = truth.chars().collect();
        if chars.len() != syls.len() {
            skipped_rows += 1;
            continue;
        }
        for (j, (c, syl)) in chars.iter().zip(syls).enumerate() {
            let (key, ch) = (std::slice::from_ref(syl), c.to_string());
            let (before, after) = (rank(&lex.entries(key), &ch), rank(&lex.entries_by_reading(key), &ch));
            let (Some(b), Some(a)) = (before, after) else {
                not_in_lexicon += 1;
                continue;
            };
            positions += 1;
            better += (a < b) as usize;
            worse += (a > b) as usize;
            same += (a == b) as usize;
            dropped += (b <= PAGE && a > PAGE) as usize;
            if let Some(f) = out.as_mut() {
                writeln!(f, "{}\t{}\t{b}\t{a}", i + 1, j + 1).map_err(|e| format!("cannot write detail ({:?})", e.kind()))?;
            }
        }
    }
    println!(
        "## window_rank {name}  {{'rows': {}, 'rows_skipped': {skipped_rows}, 'positions': {positions}, 'not_in_lexicon': {not_in_lexicon}, 'better': {better}, 'worse': {worse}, 'same': {same}, 'dropped_from_first_page': {dropped}}}",
        rows.len()
    );
    Ok(())
}
