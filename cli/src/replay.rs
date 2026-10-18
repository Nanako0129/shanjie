//! E-E real-engine replay (docs/contracts/engine-replay.md): rows typed key by key through one `core::engine::Engine`
//! built like the App's `Shell.build()`, with the user's re-picks simulated on request. Numbers only come out of here.
use core::engine::{
    load_lexicon_packs_detailed, Engine, Key, KeyKind, Layout, Output, PACK_ACG, PAGE_SIZE,
};
use core::eval::{parse_rows, pyround, row_syllables, sha256_hex, usable, Lenient};
use core::lm::Profile;
use core::tinylm::RerankLevel;
use core::Syls;
use std::fs;
use std::path::{Path, PathBuf};

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
    /// `apply_rerank` calls that changed the preedit (contract section 3).
    pub rewrites: usize,
    /// `apply_rerank` calls that returned `Some` after a typing key other than the last one.
    pub typing_applies: usize,
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

/// The Enter key (the replay's last key; also for hand-written loops in tests).
pub fn enter() -> Key {
    Key::new(KeyKind::Enter)
}

/// Keys to bring the cursor from `from` to `to` among `n` syllables: Left/Right one syllable each, or Home / End first.
pub fn move_cost(from: usize, to: usize, n: usize) -> usize {
    from.abs_diff(to).min(1 + to).min(1 + (n - to))
}

/// The engine as `Shell.build()` makes it (acg pack, bigram, small model, demotion on, rerank on at the standard level, profile),
/// except the two recorded deviations: prediction row off and English mixing off (the word list is never loaded). `tinylm` None is
/// `--no-tinylm`. `learn` turns learning on once, in memory only (no learning file is opened).
pub fn build_engine(root: &Path, lm: &Path, tinylm: Option<&Path>, profile: Profile, learn: bool) -> Result<Engine, String> {
    fn fail<T>(what: &'static str) -> impl Fn(T) -> String {
        move |_| format!("{what} failed")
    }
    let packs = root.join("data/packs");
    let mut e = Engine::new_with_packs(&root.join("data/lexicon"), Layout::Standard, Some((&packs, PACK_ACG))).map_err(fail("engine build"))?;
    e.load_lm(lm).map_err(fail("load_lm"))?;
    if let Some(t) = tinylm {
        e.load_tinylm(t).map_err(fail("load_tinylm"))?;
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
        self.cur = Some(self.e.key(k).map_err(|_| "engine error on a key".to_string())?);
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

    /// Contract section 4: from j = i + 1 rightwards, the first cursor position whose window lists an item that covers syllable `i`
    /// and spells the gold text over its own span; `(j, index in the window's list)`. The scan opens and closes windows (uncounted);
    /// it leaves the cursor at the position it stopped on and the window closed.
    fn find(&mut self, i: usize, gold: &[char], len: &Lenient) -> Result<Option<(usize, usize)>, String> {
        for j in i + 1..=self.n {
            self.move_to(j)?;
            self.press(Key::new(KeyKind::Space), false)?;
            if self.cur().selected.is_none() {
                continue;
            }
            let mut list = vec![String::new(); self.cur().total as usize];
            loop {
                let (first, page) = (self.cur().first as usize, self.cur().candidates.clone());
                let end = first + page.len();
                for (k, w) in page.into_iter().enumerate() {
                    list[first + k] = w;
                }
                if end >= list.len() {
                    break;
                }
                self.press(Key::new(KeyKind::Space), false)?; // next page
            }
            self.press(Key::new(KeyKind::Esc), false)?;
            let hit = list.iter().position(|w| {
                let l = w.chars().count();
                l >= j - i && l <= j && len.apply(w) == len.apply(&gold[j - l..j].iter().collect::<String>())
            });
            if let Some(idx) = hit {
                return Ok(Some((j, idx)));
            }
        }
        Ok(None)
    }

    /// Contract section 4: re-pick until the shown text is the gold text (lenient) or no pick can fix the first difference.
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
            let c0 = self.cursor;
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
        }
        Ok(())
    }
}

/// One row through `e` (contract section 1): left context, the reading key by key, the first display, the re-picks when asked for, Enter.
/// `gold` is the row's sentence, compared with the display through `len`. Errors carry no row text.
pub fn replay_row(e: &mut Engine, ctx: &str, syls: &[String], gold: &str, len: &Lenient, cfg: Cfg) -> Result<RowOut, String> {
    let typed: Vec<Key> = syls
        .iter()
        .map(|s| keys_of(s).ok_or("a reading symbol has no key"))
        .collect::<Result<Vec<_>, _>>()?
        .concat();
    if typed.is_empty() {
        return Err("a row without a reading".into());
    }
    e.set_left_context(ctx);
    let mut s = Session { e, cur: None, keys: 0, rewrites: 0, cursor: syls.len(), n: syls.len() };
    let (mut out, last) = (RowOut { ideal: typed.len() + 1, ..RowOut::default() }, typed.len() - 1);
    for (k, key) in typed.into_iter().enumerate() {
        s.press(key, true)?;
        match cfg.rerank {
            Rerank::Each => out.typing_applies += (s.rerank()? && k != last) as usize,
            Rerank::Final if k == last => {
                s.rerank()?;
            }
            Rerank::Final => {}
        }
    }
    out.first = s.cur().preedit.clone();
    out.first_ok = len.apply(&out.first) == len.apply(gold);
    if cfg.correct {
        s.correct(gold, len, cfg.rerank, &mut out.picks)?;
    }
    s.press(Key::new(KeyKind::Enter), true)?;
    out.committed = s.cur().commit.clone();
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

/// `--rows FILE` or `--dev N` (the first N usable rows of eval/dev/*.txt in name order), read by `core::eval::parse_rows` and `usable`
/// with the lexicon the engine uses (acg pack on). Messages carry no row text.
pub fn load_rows(root: &Path, file: Option<&str>, dev: Option<usize>) -> Result<Vec<RowIn>, String> {
    let read = |p: &Path| fs::read_to_string(p).map_err(|e| format!("cannot read file ({:?})", e.kind()));
    let mut rows = Vec::new();
    match (file, dev) {
        (Some(f), None) => rows = parse_rows(&read(Path::new(f))?).map_err(|e| e.to_string())?,
        (None, Some(_)) => {
            let mut files: Vec<PathBuf> = fs::read_dir(root.join("eval/dev"))
                .map_err(|e| format!("cannot read directory ({:?})", e.kind()))?
                .filter_map(|e| e.ok().map(|e| e.path()))
                .filter(|p| p.extension().is_some_and(|x| x == "txt"))
                .collect();
            files.sort();
            for f in files {
                rows.extend(parse_rows(&read(&f)?).map_err(|e| e.to_string())?);
            }
        }
        _ => return Err("need exactly one of --rows, --dev".into()),
    }
    let (lex, _) = load_lexicon_packs_detailed(&root.join("data/lexicon"), Some((&root.join("data/packs"), PACK_ACG))).map_err(|e| e.to_string())?;
    let mut rows = usable(&lex, rows);
    rows.truncate(dev.unwrap_or(usize::MAX));
    rows.iter()
        .map(|r| Ok(RowIn { ctx: r.ctx.clone(), sent: r.sent.clone(), syls: row_syllables(&lex, r).map_err(|e| e.to_string())? }))
        .collect()
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
        "## {name}  {tag}  {{'n': {}, 'top1': {}, 'final_ok': {}, 'unfixable': {}, 'kyss': {}, 'picks_per100': {}, 'rewrites': {}, 'top1_sha256': '{}'}}",
        outs.len(),
        sum(|o| o.first_ok as usize),
        sum(|o| o.final_ok as usize),
        sum(|o| o.unfixable as usize),
        pyround(ratio(ideal, actual, 1.0), 4),
        pyround(ratio(picks, gold_chars, 100.0), 2),
        sum(|o| o.rewrites),
        sha256_hex(firsts.join("\n").as_bytes()),
    )
}
