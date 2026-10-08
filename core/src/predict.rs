//! V3 core (docs/contracts/v3-core-predict.md): successor-first prediction candidates for typed zhuyin keys.
//! Port of experiments/sp/predict.py (units, compatibility, Index) and predict3.py (reference, order_s);
//! the Python code is ground truth, scores come from `Lm::word` unchanged.

use crate::lm::{CappedLexicon, Lm};
use std::collections::{HashMap, HashSet};
use std::cmp::Ordering;

const TONES: [char; 4] = ['ˊ', 'ˇ', 'ˋ', '˙'];

/// One typed syllable slot: zhuyin characters, whether a tone key closed it, and that tone (None: first tone).
#[derive(Clone, PartialEq)]
pub struct Unit {
    pub chars: Vec<char>,
    pub done: bool,
    pub tone: Option<char>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Prefix reading only.
    P,
    /// Union of the prefix and the abbreviation reading.
    PA,
}

/// predict.py `units_of`: a tone key (space is the first tone) completes the open unit; the next character opens a new one.
pub fn units_of(keys: &str) -> Vec<Unit> {
    let mut out: Vec<Unit> = Vec::new();
    for k in keys.chars() {
        let open = out.last_mut().filter(|u| !u.done);
        if k == ' ' || TONES.contains(&k) {
            if let Some(u) = open {
                (u.done, u.tone) = (true, (k != ' ').then_some(k));
            }
        } else if let Some(u) = open {
            u.chars.push(k);
        } else {
            out.push(Unit { chars: vec![k], done: false, tone: None });
        }
    }
    out
}

/// Golden text of a unit sequence: space-separated `chars/done(0|1)/tone`.
pub fn units_str(units: &[Unit]) -> String {
    let one = |u: &Unit| format!("{}/{}/{}", u.chars.iter().collect::<String>(), u.done as u8, u.tone.map_or(String::new(), String::from));
    units.iter().map(one).collect::<Vec<_>>().join(" ")
}

pub fn parse_units(s: &str) -> Option<Vec<Unit>> {
    s.split(' ')
        .map(|t| {
            let mut p = t.split('/');
            let (c, d, tone) = (p.next()?, p.next()?, p.next()?);
            let done = match d {
                "0" => false,
                "1" => true,
                _ => return None,
            };
            Some(Unit { chars: c.chars().collect(), done, tone: tone.chars().next() })
        })
        .collect()
}

struct Syl {
    chars: Vec<char>,
    tone: Option<char>,
}

/// predict.py `parse_syl`; a syllable without zhuyin characters is None.
fn parse_syl(s: &str) -> Option<Syl> {
    let mut chars: Vec<char> = s.chars().collect();
    let tone = if chars.last().is_some_and(|c| TONES.contains(c)) {
        chars.pop()
    } else if chars.first() == Some(&'˙') {
        Some(chars.remove(0))
    } else {
        None
    };
    (!chars.is_empty()).then_some(Syl { chars, tone })
}

/// A completed unit for a typed syllable text such as `ㄋㄧˇ` or `˙ㄅㄚ`; None when it has no zhuyin character.
pub fn unit_of_syllable(s: &str) -> Option<Unit> {
    parse_syl(s).map(|y| Unit { chars: y.chars, done: true, tone: y.tone })
}

fn unit_ok(u: &Unit, s: &Syl) -> bool {
    if u.done {
        s.chars == u.chars && s.tone == u.tone
    } else {
        s.chars.starts_with(&u.chars)
    }
}

/// Whether `predict` in prefix mode could return a word read `reading` for `units`: a cheap string test on the first
/// syllable first, then the same compatibility test as the scan. Never false for a reading `predict` would return.
pub fn reading_matches(units: &[Unit], reading: &[String]) -> bool {
    let Some(first) = units.first() else { return false };
    let head: String = first.chars.iter().collect();
    if reading.len() < units.len() || !reading[0].trim_start_matches('˙').starts_with(&head) {
        return false;
    }
    let Some((_, init)) = units.split_last() else { return false };
    init.iter().all(|u| u.done) && units.iter().zip(reading).all(|(u, y)| parse_syl(y).is_some_and(|s| unit_ok(u, &s)))
}

fn compat_prefix(units: &[Unit], syls: &[u32], table: &[Syl]) -> bool {
    let Some((_, init)) = units.split_last() else { return false };
    syls.len() >= units.len() && init.iter().all(|u| u.done) && units.iter().zip(syls).all(|(u, &s)| unit_ok(u, &table[s as usize]))
}

fn compat_abbr(units: &[Unit], syls: &[u32], table: &[Syl]) -> bool {
    !units.is_empty() && syls.len() == units.len() && units.iter().all(|u| !u.done) && units.iter().zip(syls).all(|(u, &s)| unit_ok(u, &table[s as usize]))
}

struct Ent<'a> {
    word: &'a str,
    lp: f64,
    /// Reading number; readings are numbered in the order of their `-`-joined text, so it is also the reading's sort rank.
    rd: u32,
    wlen: u32,
    /// Id in the language model (u32::MAX: not in it) and a dense id per distinct word, for dedupe.
    wid: u32,
    wix: u32,
}

/// Lexicon entries sorted by (-lp, word length, reading, word) and bucketed by every prefix of the first syllable's
/// characters (predict.py `Index`). Entries with a syllable that has no zhuyin character are left out.
pub struct Index<'a> {
    ents: Vec<Ent<'a>>,
    /// Distinct syllables, numbered in text order. Reading `r` is `pool[off[r]..off[r + 1]]`.
    syls: Vec<Syl>,
    /// Text of each syllable in `syls`, same numbering.
    names: Vec<&'a str>,
    pool: Vec<u32>,
    off: Vec<u32>,
    by_prefix: HashMap<Vec<char>, Vec<u32>>,
}

impl<'a> Index<'a> {
    pub fn new(lex: &'a CappedLexicon, lm: &Lm) -> Index<'a> {
        // Syllables get first-seen ids while scanning, then are renumbered in text order. Joined text compares like the
        // syllable sequence because `-` sorts below every syllable character.
        let (mut names, mut seen, mut parsed): (Vec<&str>, HashMap<&str, u32>, Vec<Option<Syl>>) = Default::default();
        let (mut pool, mut off, mut ents) = (Vec::new(), vec![0u32], Vec::new());
        for (key, words) in lex.readings() {
            let ids: Vec<u32> = key
                .iter()
                .map(|&s| {
                    *seen.entry(s).or_insert_with(|| {
                        names.push(s);
                        parsed.push(parse_syl(s));
                        names.len() as u32 - 1
                    })
                })
                .collect();
            if ids.iter().any(|&i| parsed[i as usize].is_none()) {
                continue;
            }
            pool.extend(ids);
            off.push(pool.len() as u32);
            let rd = off.len() as u32 - 2;
            for (word, lp) in words {
                let (wlen, wid) = (word.chars().count() as u32, lm.word_id(word).unwrap_or(u32::MAX));
                ents.push(Ent { word, lp, rd, wlen, wid, wix: 0 });
            }
        }
        let mut by_name: Vec<u32> = (0..names.len() as u32).collect();
        by_name.sort_unstable_by_key(|&i| names[i as usize]);
        let mut new_id = vec![0u32; names.len()];
        for (k, &i) in by_name.iter().enumerate() {
            new_id[i as usize] = k as u32;
        }
        let syls: Vec<Syl> = by_name.iter().map(|&i| parsed[i as usize].take().unwrap_or(Syl { chars: Vec::new(), tone: None })).collect();
        let names: Vec<&str> = by_name.iter().map(|&i| names[i as usize]).collect();
        for p in &mut pool {
            *p = new_id[*p as usize];
        }
        // Renumber readings in text order.
        let slice = |r: u32| &pool[off[r as usize] as usize..off[r as usize + 1] as usize];
        let mut by_text: Vec<u32> = (0..off.len() as u32 - 1).collect();
        by_text.sort_unstable_by(|&a, &b| slice(a).cmp(slice(b)));
        let (mut new_pool, mut new_off, mut rank) = (Vec::with_capacity(pool.len()), vec![0u32], vec![0u32; by_text.len()]);
        for (k, &r) in by_text.iter().enumerate() {
            rank[r as usize] = k as u32;
            new_pool.extend_from_slice(slice(r));
            new_off.push(new_pool.len() as u32);
        }
        let (pool, off) = (new_pool, new_off);
        for e in &mut ents {
            e.rd = rank[e.rd as usize];
        }
        // Dense word ids by sorting.
        let mut by_word: Vec<u32> = (0..ents.len() as u32).collect();
        by_word.sort_unstable_by(|&a, &b| ents[a as usize].word.cmp(ents[b as usize].word));
        let (mut next, mut prev) = (0u32, None);
        for &i in &by_word {
            let w = ents[i as usize].word;
            next += (prev.is_some_and(|p| p != w)) as u32;
            prev = Some(w);
            ents[i as usize].wix = next;
        }
        drop(by_word);
        ents.sort_by(|a, b| b.lp.partial_cmp(&a.lp).unwrap_or(Ordering::Equal).then(a.wlen.cmp(&b.wlen)).then(a.rd.cmp(&b.rd)).then(a.word.cmp(b.word)));
        let mut by_prefix: HashMap<Vec<char>, Vec<u32>> = HashMap::new();
        for (i, e) in ents.iter().enumerate() {
            let c0 = &syls[pool[off[e.rd as usize] as usize] as usize].chars;
            for j in 1..=c0.len() {
                by_prefix.entry(c0[..j].to_vec()).or_default().push(i as u32);
            }
        }
        Index { ents, syls, names, pool, off, by_prefix }
    }
}

/// Candidates of `units` in order S, at most `limit`: (word, score, is a successor of `v`, reading). The reading is the
/// syllable texts of the compatible reading that reached the word's `lp_max` (the one the order uses). `v` is the history word
/// (`lm::history`, maybe `<s>`). Tier one holds the words listed after `v`, tier two the rest; each tier is ordered by
/// (-score, word length, reading, word), where the reading is the smallest compatible one reaching the word's best
/// compatible score. That score (`lp_max`) is the first compatible entry of the word in bucket order.
pub fn predict(idx: &Index, lm: &Lm, lam: f64, v: &str, units: &[Unit], mode: Mode, limit: usize) -> Vec<(String, f64, bool, Vec<String>)> {
    let Some(bucket) = units.first().and_then(|u| idx.by_prefix.get(u.chars.as_slice())) else { return Vec::new() };
    let (succ, ctx) = (lm.successor_ids(v), lm.context_of(v));
    let mut seen = HashSet::new();
    let mut tiers: [Vec<(f64, &Ent)>; 2] = Default::default();
    for &i in bucket {
        let e = &idx.ents[i as usize];
        let syls = &idx.pool[idx.off[e.rd as usize] as usize..idx.off[e.rd as usize + 1] as usize];
        if (compat_prefix(units, syls, &idx.syls) || (mode == Mode::PA && compat_abbr(units, syls, &idx.syls))) && seen.insert(e.wix) {
            let wid = (e.wid != u32::MAX).then_some(e.wid);
            tiers[if wid.is_some_and(|w| succ.binary_search(&w).is_ok()) { 0 } else { 1 }].push((lm.word_by_id(lam, ctx, wid, e.lp), e));
        }
    }
    let mut out: Vec<(&Ent, f64, bool)> = Vec::new();
    for (t, tier) in tiers.iter_mut().enumerate() {
        let order = |a: &(f64, &Ent), b: &(f64, &Ent)| {
            b.0.partial_cmp(&a.0).unwrap_or(Ordering::Equal).then(a.1.wlen.cmp(&b.1.wlen)).then(a.1.rd.cmp(&b.1.rd)).then(a.1.word.cmp(b.1.word))
        };
        if tier.len() > limit && limit > 0 {
            tier.select_nth_unstable_by(limit - 1, order);
            tier.truncate(limit);
        }
        tier.sort_by(order);
        out.extend(tier.iter().map(|&(s, e)| (e, s, t == 0)));
    }
    out.truncate(limit);
    out.into_iter()
        .map(|(e, s, succ)| {
            let r = &idx.pool[idx.off[e.rd as usize] as usize..idx.off[e.rd as usize + 1] as usize];
            (e.word.to_string(), s, succ, r.iter().map(|&i| idx.names[i as usize].to_string()).collect())
        })
        .collect()
}
