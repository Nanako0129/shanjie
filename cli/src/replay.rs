//! E-E real-engine replay (docs/contracts/engine-replay.md): rows typed key by key through one `core::engine::Engine`
//! built like the App's `Shell.build()`, with the user's re-picks simulated on request. Numbers only come out of here.
use core::engine::{Engine, Key, KeyKind, Layout, Output, PACK_ACG, PACK_IT, PAGE_SIZE};
use core::eval::{pyround, sha256_hex, Lenient};
use core::lm::Profile;
use core::tinylm::RerankLevel;
use core::Syls;
use std::path::Path;

/// When the small model's work is applied (contract section 3).
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Rerank {
    /// After every key, before the next one: the model always finishes in time.
    Each,
    /// Only after the last typing key, before any re-pick or Enter: the user typed faster than the model.
    Final,
}

#[derive(Clone, Copy)]
pub struct Cfg {
    pub correct: bool,
    pub rerank: Rerank,
}

/// What one row produced. `first` is the preedit once the last reading key (and, with `Rerank::Final`, the one apply) is in.
#[derive(Default)]
pub struct RowOut {
    pub first: String,
    pub committed: String,
    pub ideal: usize,
    pub actual: usize,
    pub picks: usize,
    pub first_ok: bool,
    pub final_ok: bool,
    /// `--correct` was asked for and the committed text is still not the gold text (no pick could fix it).
    pub unfixable: bool,
    /// The engine committed text before Enter (the composition limit); `first` and `committed` include it.
    pub autocommit: bool,
    /// `apply_rerank` calls that changed the preedit (contract section 3).
    pub rewrites: usize,
    /// `apply_rerank` calls that returned `Some` after a typing key other than the last one.
    pub typing_applies: usize,
    /// Stretches the learning guard kept out of the rerank job of the decode after the last reading key, read before any
    /// `final` apply or re-pick (contract section 5).
    pub guard_skips: usize,
}

/// The keys of one syllable: its symbols, then its tone key (Space for the first tone). `None` for a symbol without a key.
pub fn keys_of(syl: &str) -> Option<Vec<Key>> {
    let layout = Layout::Standard;
    let (mut v, mut toned) = (Vec::new(), false);
    for c in syl.chars() {
        match layout.key_of_tone(c) {
            Some(tk) => {
                v.push(Key::ch(tk, 0));
                toned = true;
            }
            None => v.push(Key::ch(layout.key_of_symbol(c)?, 0)),
        }
    }
    if !toned {
        v.push(Key::new(KeyKind::Space));
    }
    Some(v)
}

/// Keys to bring the cursor from `from` to `to` among `n` syllables: Left/Right one syllable each, or Home / End first.
pub fn move_cost(from: usize, to: usize, n: usize) -> usize {
    from.abs_diff(to).min(1 + to).min(1 + (n - to))
}

/// The packs the engine is built with: the App's defaults (`Shell.swift` `acgPackOn = true`, `itPackOn = true`). The bin's setting tag is
/// formatted from this mask, so the tag and the engine cannot drift apart.
pub const PACKS: u32 = PACK_ACG | PACK_IT;

/// The engine as `Shell.build()` makes it (the packs of `PACKS`; bigram, small model, colloquial table, demotion on, rerank on at the standard level,
/// profile), except the two recorded deviations: prediction row off and English mixing off. `tinylm` None is
/// `--no-tinylm`. `colloquial` is the colloquial table, loaded after the small model as the App does; the bin passes `data/lm/colloquial.sjlm`
/// by default and None for `--no-colloquial`. `learn` turns learning on once, in memory only (no learning file is opened here; the bin's
/// `--learn-file` opens a copy).
pub fn build_engine(root: &Path, lm: &Path, tinylm: Option<&Path>, colloquial: Option<&Path>, profile: Profile, learn: bool) -> Result<Engine, String> {
    build_engine_opts(root, lm, tinylm, colloquial, profile, learn, false)
}

/// `build_engine`; `no_english_next` drops the data directory's `english-next.tsv` before the model loads, so the engine has neither the table
/// nor the word cache built from it (`--english-next` measures the cost of loading it).
pub fn build_engine_opts(root: &Path, lm: &Path, tinylm: Option<&Path>, colloquial: Option<&Path>, profile: Profile, learn: bool, no_english_next: bool) -> Result<Engine, String> {
    fn fail<T>(what: &'static str) -> impl Fn(T) -> String {
        move |_| format!("{what} failed")
    }
    let packs = root.join("data/packs");
    let mut e = Engine::new_with_packs(&root.join("data/lexicon"), Layout::Standard, Some((&packs, PACKS))).map_err(fail("engine build"))?;
    if no_english_next {
        e.set_english_next(None);
    }
    e.load_lm(lm).map_err(fail("load_lm"))?;
    if let Some(t) = tinylm {
        e.load_tinylm(t).map_err(fail("load_tinylm"))?;
    }
    // After load_tinylm, as Shell.build() does (docs/contracts/colloquial-term.md section 2.3).
    if let Some(c) = colloquial {
        e.load_colloquial(c).map_err(fail("load_colloquial"))?;
    }
    e.set_demote(true).map_err(fail("set_demote"))?;
    e.set_prediction(false).map_err(fail("set_prediction"))?;
    e.set_abbreviation(false).map_err(fail("set_abbreviation"))?;
    e.set_english(false).map_err(fail("set_english"))?;
    e.set_candidate_vertical(false).map_err(fail("set_candidate_vertical"))?;
    e.set_rerank(true).map_err(fail("set_rerank"))?;
    e.set_rerank_level(RerankLevel::Standard).map_err(fail("set_rerank_level"))?;
    e.set_profile(profile).map_err(fail("set_profile"))?;
    if learn {
        e.set_learning(true);
    }
    Ok(e)
}

struct Session<'a> {
    e: &'a mut Engine,
    cur: Option<Output>,
    /// Text the engine committed before Enter (the composition limit, digit rows), in order.
    committed: String,
    /// The longest word a window can list: no window further right than `i` + this can cover `i`.
    max_len: usize,
    keys: usize,
    rewrites: usize,
    cursor: usize,
    n: usize,
}

impl Session<'_> {
    fn cur(&self) -> &Output {
        self.cur.as_ref().expect("a key was pressed")
    }

    /// One key into the engine; only `counted` keys are part of `actual` (a scanning key is the tool looking, not the user typing).
    fn press(&mut self, k: Key, counted: bool) -> Result<(), String> {
        let o = self.e.key(k).map_err(|_| "engine error on a key".to_string())?;
        if k.kind != KeyKind::Enter {
            self.committed.push_str(&o.commit);
        }
        self.cur = Some(o);
        self.keys += counted as usize;
        Ok(())
    }

    /// take, run, apply; whether `apply_rerank` returned `Some`. A change of the preedit counts as a rewrite.
    fn rerank(&mut self) -> Result<bool, String> {
        let Some(job) = self.e.take_rerank_job() else { return Ok(false) };
        let before = self.cur().preedit.clone();
        match self.e.apply_rerank(job.run()).map_err(|_| "engine error on apply_rerank".to_string())? {
            Some(o) => {
                self.rewrites += (o.preedit != before) as usize;
                self.cur = Some(o);
                Ok(true)
            }
            None => Ok(false),
        }
    }

    fn move_to(&mut self, j: usize) -> Result<(), String> {
        while self.cursor < j {
            self.press(Key::new(KeyKind::Right), false)?;
            self.cursor += 1;
        }
        while self.cursor > j {
            self.press(Key::new(KeyKind::Left), false)?;
            self.cursor -= 1;
        }
        Ok(())
    }

    /// Contract section 4: from j = i + 1 rightwards (to `i` + the longest word at most), the first cursor position whose window lists an
    /// item that covers syllable `i` and spells the gold text over its own span; `(j, index in the window's list)`. The scan opens
    /// windows page by page up to the first such item and closes them (uncounted); it leaves the cursor at the position it stopped on.
    fn find(&mut self, i: usize, gold: &[char], len: &Lenient) -> Result<Option<(usize, usize)>, String> {
        for j in i + 1..=self.n.min(i + self.max_len) {
            self.move_to(j)?;
            self.press(Key::new(KeyKind::Space), false)?;
            if self.cur().selected.is_none() {
                continue;
            }
            let (total, mut hit) = (self.cur().total as usize, None);
            loop {
                let (first, page) = (self.cur().first as usize, &self.cur().candidates);
                hit = hit.or_else(|| {
                    page.iter().position(|w| {
                        let l = w.chars().count();
                        l >= j - i && l <= j && len.apply(w) == len.apply(&gold[j - l..j].iter().collect::<String>())
                    })
                    .map(|k| first + k)
                });
                if hit.is_some() || first + page.len() >= total {
                    break;
                }
                self.press(Key::new(KeyKind::Space), false)?; // next page
            }
            self.press(Key::new(KeyKind::Esc), false)?;
            if let Some(idx) = hit {
                return Ok(Some((j, idx)));
            }
        }
        Ok(None)
    }

    /// Contract section 4: re-pick until the shown text is the gold text (lenient) or no pick can fix the first difference. `gold` is
    /// the part of the sentence the composition holds (what was committed before is not in it), the cursor is at its end.
    fn correct(&mut self, gold: &str, len: &Lenient, rerank: Rerank, picks: &mut usize) -> Result<(), String> {
        let (graw, gl): (Vec<char>, Vec<char>) = (gold.chars().collect(), len.apply(gold).chars().collect());
        for _ in 0..2 * graw.len() {
            let shown: Vec<char> = len.apply(&self.cur().preedit).chars().collect();
            if shown == gl {
                break;
            }
            // One character per syllable is how a pick's span is known; any other length is not fixable.
            if shown.len() != gl.len() || gl.len() != self.n {
                break;
            }
            let i = (0..gl.len()).find(|&i| shown[i] != gl[i]).expect("the texts differ");
            let (c0, before) = (self.cursor, self.cur().preedit.clone());
            let Some((j, idx)) = self.find(i, &graw, len)? else { break };
            self.keys += move_cost(c0, j, self.n);
            self.press(Key::new(KeyKind::Space), true)?; // opens on the first page
            for _ in 0..idx / PAGE_SIZE {
                self.press(Key::new(KeyKind::Space), true)?;
            }
            self.press(Key::ch(char::from(b'1' + (idx % PAGE_SIZE) as u8), 0), true)?; // a digit chooses on the spot
            *picks += 1;
            if rerank == Rerank::Each {
                self.rerank()?;
            }
            // A pick that leaves the text as it was would be chosen again: not fixable.
            if self.cur().preedit == before {
                break;
            }
        }
        Ok(())
    }
}

/// One row through `e` (contract section 1): left context, the reading key by key, the first display, the re-picks when asked for, Enter.
/// `gold` is the row's sentence, compared with the display through `len`. Errors carry no row text. Text the engine commits while the
/// row is typed (composition limit) is kept: it is part of the first display and of what is sent, and no pick can change it.
pub fn replay_row(e: &mut Engine, ctx: &str, syls: &[String], gold: &str, len: &Lenient, cfg: Cfg) -> Result<RowOut, String> {
    let per: Vec<Vec<Key>> = syls.iter().map(|s| keys_of(s).ok_or("a reading symbol has no key")).collect::<Result<_, _>>()?;
    // ends[s]: how many keys have been typed when syllable s is complete.
    let ends: Vec<usize> = per.iter().scan(0, |t, v| Some({ *t += v.len(); *t })).collect();
    let typed = per.concat();
    if typed.is_empty() {
        return Err("a row without a reading".into());
    }
    e.set_left_context(ctx);
    let max_len = e.max_window_len();
    let mut s = Session { e, cur: None, committed: String::new(), max_len, keys: 0, rewrites: 0, cursor: syls.len(), n: syls.len() };
    let (mut out, last) = (RowOut { ideal: typed.len() + 1, ..RowOut::default() }, typed.len() - 1);
    let mut committed_syls = 0; // syllables complete when the engine last committed
    for (k, key) in typed.into_iter().enumerate() {
        let before = s.committed.len();
        s.press(key, true)?;
        if k == last {
            out.guard_skips = s.e.guard_skips();
        }
        if s.committed.len() > before {
            out.autocommit = true;
            committed_syls = ends.iter().filter(|&&end| end <= k + 1).count();
        }
        match cfg.rerank {
            Rerank::Each => out.typing_applies += (s.rerank()? && k != last) as usize,
            Rerank::Final if k == last => {
                s.rerank()?;
            }
            Rerank::Final => {}
        }
    }
    out.first = format!("{}{}", s.committed, s.cur().preedit);
    out.first_ok = len.apply(&out.first) == len.apply(gold);
    // The composition holds the syllables after the committed ones, one character each, or the row is not fixable.
    if cfg.correct && s.committed.chars().count() == committed_syls {
        (s.n, s.cursor) = (syls.len() - committed_syls, syls.len() - committed_syls);
        let rest: String = gold.chars().skip(committed_syls).collect();
        s.correct(&rest, len, cfg.rerank, &mut out.picks)?;
    }
    s.press(Key::new(KeyKind::Enter), true)?;
    out.committed = format!("{}{}", s.committed, s.cur().commit);
    out.final_ok = len.apply(&out.committed) == len.apply(gold);
    out.unfixable = cfg.correct && !out.final_ok;
    (out.actual, out.rewrites) = (s.keys, s.rewrites);
    Ok(out)
}

pub struct RowIn {
    pub ctx: String,
    pub sent: String,
    pub syls: Syls,
}

/// `--keys` file: `row\tideal\tactual\tpicks\tfinal_ok\trewrites` per row, rows counted from 1. Digits only.
pub fn format_keys(outs: &[RowOut]) -> String {
    outs.iter()
        .enumerate()
        .map(|(i, o)| format!("{}\t{}\t{}\t{}\t{}\t{}\n", i + 1, o.ideal, o.actual, o.picks, o.final_ok as u8, o.rewrites))
        .collect()
}

/// The summary line (contract section 5). `tag` is the setting marker, `gold_chars` the characters of all gold sentences.
/// `top1_sha256` is that of `shanjie-eval --lm`: the first displays joined with `\n`.
pub fn summary_line(name: &str, tag: &str, outs: &[RowOut], gold_chars: usize) -> String {
    let sum = |f: fn(&RowOut) -> usize| outs.iter().map(f).sum::<usize>();
    let (ideal, actual, picks) = (sum(|o| o.ideal), sum(|o| o.actual), sum(|o| o.picks));
    let ratio = |a: usize, b: usize, k: f64| if b == 0 { 0.0 } else { a as f64 * k / b as f64 };
    let firsts: Vec<&str> = outs.iter().map(|o| o.first.as_str()).collect();
    format!(
        "## {name}  {tag}  {{'n': {}, 'top1': {}, 'final_ok': {}, 'unfixable': {}, 'autocommits': {}, 'kyss': {}, 'picks_per100': {}, 'rewrites': {}, 'guard_skips': {}, 'guard_rows': {}, 'top1_sha256': '{}'}}",
        outs.len(),
        sum(|o| o.first_ok as usize),
        sum(|o| o.final_ok as usize),
        sum(|o| o.unfixable as usize),
        sum(|o| o.autocommit as usize),
        pyround(ratio(ideal, actual, 1.0), 4),
        pyround(ratio(picks, gold_chars, 100.0), 2),
        sum(|o| o.rewrites),
        sum(|o| o.guard_skips),
        sum(|o| (o.guard_skips > 0) as usize),
        sha256_hex(firsts.join("\n").as_bytes()),
    )
}
