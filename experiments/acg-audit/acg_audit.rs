// Audit helper, kept as a record of the 2026-10-10 audit (docs/research-log.md); it builds as a cargo example once copied into cli/examples/. args: LM PACKS_DIR. stdin: `reading<TAB>word`. For each line and profile prints
// reading, word, profile, top-1 words (joined by +), top-1 total score, top-1 components (word:capped lp:count:in-vocab, |-separated),
// rank of the candidate that is exactly [word] (-1 if none in the beam), its total score, and the word's capped lp, count, in-vocab.
use core::engine::{capping_overlay, load_lexicon_packs, PACK_ACG};
use core::lm::{decode, CappedLexicon, Demote, Lm, Profile};
use std::io::BufRead;
use std::path::{Path, PathBuf};

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let (lm_path, pdir) = (&a[1], PathBuf::from(&a[2]));
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join("data/lexicon");
    let lm = Lm::load(Path::new(lm_path)).unwrap();
    let (lex, pack) = load_lexicon_packs(&dir, Some((&pdir, PACK_ACG))).unwrap();
    let overlay = capping_overlay(&dir, &pack).unwrap();
    let table = Demote::parse(&std::fs::read_to_string(dir.join("demote.tsv")).unwrap()).unwrap();
    let capped = CappedLexicon::new(lex, &overlay, &lm, Some(&table)).unwrap();
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        let (r, w) = line.split_once('\t').unwrap();
        let key: Vec<String> = r.split(' ').map(String::from).collect();
        let wlp = capped.best_lp(&key, w);
        for (pn, prof) in [("chat", Profile::Chat), ("formal", Profile::Formal)] {
            let cands = decode(&capped, &key, &lm, prof, 200).unwrap();
            let top = &cands[0];
            let mut pos = 0;
            let mut comps = Vec::new();
            for cw in &top.1 {
                let n = cw.chars().count();
                let lp = capped.best_lp(&key[pos..pos + n], cw);
                comps.push(format!("{cw}:{}:{}:{}", lp.map_or("NA".into(), |x| format!("{x:.6}")), lm.count(cw), lm.word_id(cw).is_some() as u8));
                pos += n;
            }
            let rank = cands.iter().position(|c| c.1.len() == 1 && c.1[0] == w).map_or(-1, |x| x as i64);
            let wt = if rank >= 0 { format!("{}", cands[rank as usize].0) } else { "NA".into() };
            println!("{r}\t{w}\t{pn}\t{}\t{}\t{}\t{rank}\t{wt}\t{}\t{}\t{}", top.1.join("+"), top.0, comps.join("|"), wlp.map_or("NA".into(), |x| format!("{x:.6}")), lm.count(w), lm.word_id(w).is_some() as u8);
        }
    }
}
