//! `Lexicon::is_overlay_only` against the word sets `Colloquial::with_table` builds from the files (the way k was counted before the lexicon
//! carried the bits): every word of every shipped file, no pack and all five packs, must agree (docs/spec/semantic-rerank.md §4).
use core::colloquial::{Colloquial, ColloquialTable};
use core::engine::{load_lexicon_packs, read_packs_detailed, PACK_ACG, PACK_BEAUTY, PACK_IT, PACK_MEME, PACK_QUOTES};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::Arc;

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

fn read(p: PathBuf) -> String {
    std::fs::read_to_string(&p).unwrap_or_else(|_| panic!("cannot read {}", p.display()))
}

#[test]
fn bits_equal_the_file_sets_for_every_word() {
    let table = root().join("data/lm/colloquial.sjlm");
    assert!(table.exists(), "data/lm/colloquial.sjlm is needed: see data/*.sha256");
    let table = Arc::new(ColloquialTable::load(&table).unwrap());
    let (dir, packs) = (root().join("data/lexicon"), root().join("data/packs"));
    let base = read(dir.join("mcbpmf-data.txt"));
    for mask in [0, PACK_ACG | PACK_QUOTES | PACK_IT | PACK_MEME | PACK_BEAUTY] {
        let (lex, _) = load_lexicon_packs(&dir, Some((&packs, mask))).unwrap();
        let pack_text = read_packs_detailed(&packs, mask).unwrap();
        let overlay = std::fs::File::open(dir.join("overlay-add.tsv")).unwrap();
        let old = Colloquial::with_table(table.clone(), &base, std::io::BufReader::new(overlay), Some(&pack_text)).unwrap();
        let mut words: BTreeSet<String> = BTreeSet::new();
        for l in base.lines() {
            words.extend(l.split_whitespace().nth(1).map(String::from));
        }
        let mut texts = vec![read(dir.join("overlay-add.tsv")), read(dir.join("sandhi-add.tsv")), read(dir.join("names-add.tsv")), pack_text];
        texts.push(String::new());
        for t in &texts {
            words.extend(t.lines().filter_map(|l| l.split('\t').nth(1)).map(String::from));
        }
        words.insert("不在任何表".to_string());
        let (mut yes, mut diff) = (0, Vec::new());
        for w in &words {
            let was = old.k(&[w.as_str()]) > 0;
            yes += was as usize;
            if lex.is_overlay_only(w) != was {
                diff.push(w.clone());
            }
        }
        assert!(diff.is_empty(), "mask {mask}: {} words differ, first {:?}", diff.len(), &diff[..diff.len().min(5)]);
        assert!(yes > 1000, "mask {mask}: only {yes} overlay-only words, the comparison is empty");
        // the three colloquial-tagged rows of overlay-add.tsv are counted like the old set did (unless the base has the word)
        for l in read(dir.join("overlay-add.tsv")).lines().filter(|l| l.ends_with("\tcolloq")) {
            let w = l.split('\t').nth(1).unwrap();
            assert_eq!(lex.is_overlay_only(w), old.k(&[w]) > 0, "{w}");
        }
    }
}
