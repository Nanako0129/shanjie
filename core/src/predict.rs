//! V3 core (docs/contracts/v3-core-predict.md): successor-first prediction candidates for typed zhuyin keys.
//! Port of experiments/sp/predict.py (units, compatibility, Index) and predict3.py (reference, order_s);
//! the Python code is ground truth, scores come from `Lm::word` unchanged.

use crate::lm::{CappedLexicon, Lm};
use std::collections::HashMap;
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

fn unit_ok(u: &Unit, s: &Syl) -> bool {
    if u.done {
        s.chars == u.chars && s.tone == u.tone
    } else {
        s.chars.starts_with(&u.chars)
    }
}

fn compat_prefix(units: &[Unit], syls: &[Syl]) -> bool {
    let Some((_, init)) = units.split_last() else { return false };
    syls.len() >= units.len() && init.iter().all(|u| u.done) && units.iter().zip(syls).all(|(u, s)| unit_ok(u, s))
}

fn compat_abbr(units: &[Unit], syls: &[Syl]) -> bool {
    !units.is_empty() && syls.len() == units.len() && units.iter().all(|u| !u.done) && units.iter().zip(syls).all(|(u, s)| unit_ok(u, s))
}

struct Ent<'a> {
    word: &'a str,
    lp: f64,
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
    /// Per reading: parsed syllables and the `-`-joined text.
    rds: Vec<(Vec<Syl>, String)>,
    by_prefix: HashMap<Vec<char>, Vec<u32>>,
    nwords: usize,
}

impl<'a> Index<'a> {
    pub fn new(lex: &'a CappedLexicon, lm: &Lm) -> Index<'a> {
        let (mut ents, mut rds, mut wix) = (Vec::new(), Vec::new(), HashMap::new());
        for (key, words) in lex.readings() {
            let Some(syls) = key.iter().map(|s| parse_syl(s)).collect::<Option<Vec<_>>>() else { continue };
            rds.push((syls, key.join("-")));
            for (word, lp) in words {
                let next = wix.len() as u32;
                let wix = *wix.entry(word).or_insert(next);
                let (rd, wlen, wid) = (rds.len() as u32 - 1, word.chars().count() as u32, lm.word_id(word).unwrap_or(u32::MAX));
                ents.push(Ent { word, lp, rd, wlen, wid, wix });
            }
        }
        ents.sort_by(|a, b| {
            b.lp.partial_cmp(&a.lp).unwrap_or(Ordering::Equal).then(a.wlen.cmp(&b.wlen)).then_with(|| rds[a.rd as usize].1.cmp(&rds[b.rd as usize].1)).then(a.word.cmp(b.word))
        });
        let mut by_prefix: HashMap<Vec<char>, Vec<u32>> = HashMap::new();
        for (i, e) in ents.iter().enumerate() {
            let c0 = &rds[e.rd as usize].0[0].chars;
            for j in 1..=c0.len() {
                by_prefix.entry(c0[..j].to_vec()).or_default().push(i as u32);
            }
        }
        Index { ents, rds, by_prefix, nwords: wix.len() }
    }
}

/// Candidates of `units` in order S, at most `limit`: (word, score, is a successor of `v`). `v` is the history word
/// (`lm::history`, maybe `<s>`). Tier one holds the words listed after `v`, tier two the rest; each tier is ordered by
/// (-score, word length, reading, word), where the reading is the smallest compatible one reaching the word's best
/// compatible score. That score (`lp_max`) is the first compatible entry of the word in bucket order.
pub fn predict(idx: &Index, lm: &Lm, lam: f64, v: &str, units: &[Unit], mode: Mode, limit: usize) -> Vec<(String, f64, bool)> {
    let Some(bucket) = units.first().and_then(|u| idx.by_prefix.get(u.chars.as_slice())) else { return Vec::new() };
    let (succ, ctx) = (lm.successor_ids(v), lm.context_of(v));
    let mut seen = vec![false; idx.nwords];
    let mut tiers: [Vec<(f64, &Ent)>; 2] = Default::default();
    for &i in bucket {
        let e = &idx.ents[i as usize];
        let syls = &idx.rds[e.rd as usize].0;
        if (compat_prefix(units, syls) || (mode == Mode::PA && compat_abbr(units, syls))) && !std::mem::replace(&mut seen[e.wix as usize], true) {
            let wid = (e.wid != u32::MAX).then_some(e.wid);
            tiers[if wid.is_some_and(|w| succ.binary_search(&w).is_ok()) { 0 } else { 1 }].push((lm.word_by_id(lam, ctx, wid, e.lp), e));
        }
    }
    let mut out = Vec::new();
    for (t, tier) in tiers.iter_mut().enumerate() {
        let order = |a: &(f64, &Ent), b: &(f64, &Ent)| {
            b.0.partial_cmp(&a.0).unwrap_or(Ordering::Equal).then(a.1.wlen.cmp(&b.1.wlen)).then_with(|| idx.rds[a.1.rd as usize].1.cmp(&idx.rds[b.1.rd as usize].1)).then(a.1.word.cmp(b.1.word))
        };
        if tier.len() > limit && limit > 0 {
            tier.select_nth_unstable_by(limit - 1, order);
            tier.truncate(limit);
        }
        tier.sort_by(order);
        out.extend(tier.iter().map(|&(s, e)| (e.word.to_string(), s, t == 0)));
    }
    out.truncate(limit);
    out
}
