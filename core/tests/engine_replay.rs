//! S3a contract 7.2: replay the 302 rows of `--set dev --limit 302` as key presses, both layouts.
use core::engine::*;
use core::eval::{parse_rows, usable};
use core::{decode_beam, NoLearning, BEAM_S1};
use std::path::{Path, PathBuf};
use std::time::Instant;

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf()
}

/// Reading -> key presses: symbols in initial/medial/final order (already so in the string), then the tone key.
fn keys_of(layout: Layout, syl: &str) -> Vec<Key> {
    let mut v = Vec::new();
    let mut toned = false;
    for c in syl.chars() {
        match layout.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(layout.key_of_symbol(c).expect("symbol has a key"), 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    v
}

fn replay(layout: Layout) {
    let lex = load_lexicon(&root().join("data/lexicon")).unwrap();
    let mut files: Vec<PathBuf> = std::fs::read_dir(root().join("eval/dev"))
        .unwrap()
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "txt"))
        .collect();
    files.sort();
    let mut rows = Vec::new();
    for f in files {
        rows.extend(parse_rows(&std::fs::read_to_string(f).unwrap()).unwrap());
    }
    let mut rows = usable(&lex, rows);
    rows.truncate(302);
    assert!(rows.len() == 302, "dev set has 302 usable rows");

    let mut e = Engine::with_lexicon(lex.clone(), layout);
    let mut times = Vec::new();
    for (i, r) in rows.iter().enumerate() {
        let syls = match &r.reading {
            Some(s) => s.clone(),
            None => lex.to_syllables(&r.sent).expect("usable row"),
        };
        let want = decode_beam(&lex, &syls, &mut NoLearning, BEAM_S1).unwrap()[0].1.concat();
        let mut keys: Vec<Key> = syls.iter().flat_map(|s| keys_of(layout, s)).collect();
        keys.push(Key::new(KeyKind::Enter));
        let mut got = String::new();
        for key in keys {
            let t = Instant::now();
            let o = e.key(key).unwrap();
            times.push(t.elapsed());
            got.push_str(&o.commit);
        }
        assert!(got == want, "replay mismatch at row {}", i + 1);
    }
    times.sort();
    let p95 = times[times.len() * 95 / 100];
    println!("keys {} p95 {:?} max {:?}", times.len(), p95, times[times.len() - 1]);
    #[cfg(not(debug_assertions))]
    assert!(p95 < std::time::Duration::from_millis(16), "p95 over 16 ms");
}

#[test]
fn replay_standard_302_of_302() {
    replay(Layout::Standard);
}
#[test]
fn replay_eten_302_of_302() {
    replay(Layout::Eten);
}
