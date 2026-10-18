//! Helpers `shanjie-eval` and `shanjie-replay` share: the repo root, the lenient table and the rowstats writer
//! (docs/contracts/eval-stats.md section 1). Moved out of cli/src/main.rs unchanged, so the eval output bytes stay what they were.
use core::eval::Lenient;
use std::fs;
use std::path::{Path, PathBuf};

pub fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

/// S2v lenient table: SHANJIE_VARIANTS if set, else the repo's eval/variants.tsv; missing or bad is an error.
pub fn load_lenient() -> Result<Lenient, String> {
    // An empty value counts as unset, same as the Python reference (`os.environ.get(...) or default`).
    let p = std::env::var_os("SHANJIE_VARIANTS")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| root().join("eval/variants.tsv"));
    let text = fs::read_to_string(p).map_err(|e| format!("cannot read variants table ({:?})", e.kind()))?;
    Lenient::parse(&text).map_err(|e| e.to_string())
}

/// Levenshtein distance over Unicode scalar values.
pub fn levenshtein(a: &str, b: &str) -> u32 {
    let b: Vec<char> = b.chars().collect();
    let mut prev: Vec<u32> = (0..=b.len() as u32).collect();
    for (i, x) in a.chars().enumerate() {
        let mut cur = vec![i as u32 + 1];
        for (j, y) in b.iter().enumerate() {
            cur.push((prev[j + 1] + 1).min(cur[j] + 1).min(prev[j] + (x != *y) as u32));
        }
        prev = cur;
    }
    prev[b.len()]
}

/// (errors, gold length) of one row (docs/contracts/eval-stats.md §1). `ok` is the lenient top-1 judgment made by the
/// caller; a row that is ok has 0 errors even when the first candidate differs from the gold text.
pub fn row_stats(top1: &str, gold: &str, ok: bool) -> (u32, u32) {
    (if ok { 0 } else { levenshtein(top1, gold) }, gold.chars().count() as u32)
}

/// The one writer of a rowstats file: `row\tok\terrors\tgold_len` per row, rows counted from 1. Digits only.
pub fn format_rowstats(rows: &[(bool, String, String)]) -> String {
    let mut out = String::new();
    for (i, (ok, top1, gold)) in rows.iter().enumerate() {
        let (e, n) = row_stats(top1, gold, *ok);
        out += &format!("{}\t{}\t{e}\t{n}\n", i + 1, *ok as u8);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn row_stats_zero_for_ok_rows() {
        assert_eq!(row_stats("他很好", "她很好", true), (0, 3));
        assert_eq!(row_stats("他很好", "她很好", false), (1, 3));
        assert_eq!(row_stats("", "𠮷野家", false), (3, 3));
    }

    #[test]
    fn rowstats_rows_count_from_one() {
        assert_eq!(format_rowstats(&[(true, "a".into(), "a".into()), (false, "ab".into(), "ac".into())]), "1\t1\t0\t1\n2\t0\t1\t2\n");
    }
}
