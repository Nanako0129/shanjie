//! S-bench key replay (docs/contracts/sbench.md 1.2). Built by tools/bench.py against one exported version of `core`.
//! usage: replay <lexicon dir> <lm file> <rows file> <commits out>
//! Types every row's reading as standard-layout key presses plus Enter, times each key, writes one committed
//! string per row to <commits out>, and prints one JSON line (keys, load_ms, p95_us, max_us).
use core::engine::*;
use std::time::Instant;

/// Reading -> key presses; same rule as core/tests/engine_lm.rs `keys_of`.
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

fn main() {
    let a: Vec<String> = std::env::args().skip(1).collect();
    let layout = Layout::Standard;
    let t = Instant::now();
    let mut e = Engine::new(std::path::Path::new(&a[0]), layout).unwrap();
    e.load_lm(std::path::Path::new(&a[1])).unwrap();
    let load_ms = t.elapsed().as_secs_f64() * 1e3;

    let text = std::fs::read_to_string(&a[2]).unwrap();
    let (mut times, mut out) = (Vec::new(), String::new());
    for l in text.lines() {
        let p: Vec<&str> = l.split('|').collect();
        if p.len() != 3 {
            continue;
        }
        let mut keys: Vec<Key> = p[2].split_whitespace().flat_map(|s| keys_of(layout, s)).collect();
        keys.push(Key::new(KeyKind::Enter));
        let mut s = String::new();
        for k in keys {
            let t = Instant::now();
            let o = e.key(k).unwrap();
            times.push(t.elapsed().as_secs_f64() * 1e6);
            s.push_str(&o.commit);
        }
        out.push_str(&s);
        out.push('\n');
    }
    std::fs::write(&a[3], out).unwrap();
    times.sort_by(|x, y| x.partial_cmp(y).unwrap());
    let n = times.len();
    println!("{{\"keys\":{n},\"load_ms\":{load_ms:.1},\"p95_us\":{:.1},\"max_us\":{:.1}}}", times[n * 95 / 100], times[n - 1]);
}
