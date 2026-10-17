//! The engine side of digits in the composition (docs/contracts/digits.md A.3.1, A.3.4-A.3.6): the run of digit-alphabet keys, its
//! buffer of at most four keys, how the scoring of `crate::digits` is turned into tokens, an unfinished syllable and the display.
//! Every key of a run goes through `Engine::digit_run_key` (hooked in `dispatch` before everything but the entered row and Tab); a
//! key that opens a run through `digit_start`. Unreached unless `digits_active`: the engine is then the one of the slice before.
//!
//! The buffer's tail (the keys that read as the unfinished syllable) is mirrored in `pend` (and the shadow), so everything that reads
//! the unfinished syllable (the preedit, the prediction row) sees it as it sees any other; the keys that read as digits are only in
//! `DRun::buf` and in the preedit until they are flushed to tokens.

use super::*;
use crate::digits::{is_alphabet, Decision, Legal, Scorer, MAX_BUF};
use crate::predict::DigitWords;

/// A run of digit-alphabet keys (digits.md A.3.1 "串").
pub(super) struct DRun {
    /// The history word of the text before the run (`lm::history(context_key(..))`, `<s>` for none), fixed when the run starts.
    pub ctx: String,
    /// Keys already tokens in the composition (they are digits).
    pub emitted: Vec<char>,
    /// Keys not yet tokens, at most `MAX_BUF`.
    pub buf: Vec<char>,
    /// How many of the buffered keys show as digits (the rest are the tail).
    pub m: usize,
}

/// What is built once per (lexicon, model): the legal tails and the best entry of every word the table can name.
pub(super) struct DigitCache {
    key: (usize, (u64, usize, usize)),
    pub legal: Legal,
    pub words: DigitWords,
}

/// The other reading of the digit keys that a window can end with (digits.md A.3.6).
pub(super) enum DigitAlt {
    /// The syllable that just closed has these digit keys instead.
    Digits(String),
    /// The last `n` digit tokens could have been this syllable.
    Syllable { n: usize, syl: String },
}

/// One scoring call, for the golden test of the scores (test and tool hook).
#[derive(Clone, PartialEq)] // no Debug: holds input text (R2)
pub struct DigitEvent {
    pub ctx: String,
    pub buf: String,
    pub flush: usize,
    pub splits: Vec<(usize, usize, f64)>,
    pub choice: Option<(usize, usize)>,
}

pub(super) fn full_width(d: char) -> char {
    match d {
        '0'..='9' => char::from_u32('０' as u32 + (d as u32 - '0' as u32)).unwrap_or(d),
        '.' => '．',
        '/' => '／',
        '-' => '－',
        _ => d,
    }
}

/// Everything a scoring call needs, owned (so the engine can be changed while it is held).
pub(super) struct DigitEnv {
    pub st: LmState,
    pub table: Arc<DigitTable>,
    pub cache: Arc<DigitCache>,
}

impl DigitEnv {
    pub fn scorer(&self) -> Scorer<'_> {
        Scorer { table: &self.table, lm: &self.st.lm, idx: self.st.capped.predict_index(&self.st.lm), legal: &self.cache.legal }
    }
}

fn token_reading(k: char) -> String {
    format!("{PUNCT_PREFIX}{k}")
}

impl Engine {
    /// Digits.md A.3.1 "生效": the shared English-mixing switch, no abbreviation composer, the standard layout, and the two things the
    /// scores need (the table and a model). Without them every key is the key of the slice before.
    pub(super) fn digits_active(&self) -> bool {
        self.english && !self.abbr && self.layout == Layout::Standard && self.digit_table.is_some() && self.lm.is_some()
    }

    /// Test and tool hook: the table (shared across engines), or none.
    #[doc(hidden)]
    pub fn set_digit_table(&mut self, table: Option<Arc<DigitTable>>) {
        self.digit_table = table;
        self.digit_cache = None;
    }

    /// Test and tool hook: read `digits.tsv` at `path`; false (nothing changes) when it is missing or malformed.
    #[doc(hidden)]
    pub fn load_digits(&mut self, path: &Path) -> bool {
        let Some(t) = std::fs::read_to_string(path).ok().and_then(|t| DigitTable::parse(&t)) else { return false };
        self.set_digit_table(Some(Arc::new(t)));
        true
    }

    /// Test hook: the readings of the composition's units (`_punct_2` for a digit token).
    #[doc(hidden)]
    pub fn digits_syls(&self) -> Vec<String> {
        self.syls.clone()
    }

    /// Simulator hook: digit keys of the run after the latest `key()` that show as digits (the run's keys, flushed ones included;
    /// the run that key ended counts as it ended). 0 when no run was touched. Cleared at the start of every `key()`.
    #[doc(hidden)]
    pub fn digits_shown(&self) -> usize {
        self.d_shown
    }

    /// Simulator hook: the symbols of the open run's buffered keys in typing order (the keys the person typed for the unfinished
    /// syllable, whichever way they are shown); `None` without a run.
    #[doc(hidden)]
    pub fn digits_typed(&self) -> Option<Vec<char>> {
        self.drun.as_ref().map(|r| r.buf.iter().filter_map(|&k| Layout::Standard.symbol_of(k).map(|x| x.1)).collect())
    }

    /// Simulator hook: digit tokens that the latest `key()` put into the composition. Cleared at the start of every `key()`.
    #[doc(hidden)]
    pub fn digits_emitted(&self) -> usize {
        self.d_emitted
    }

    /// Golden-test hook: record (and take) the scoring calls.
    #[doc(hidden)]
    pub fn set_digits_trace(&mut self, on: bool) {
        self.dtrace = on.then(Vec::new);
    }

    #[doc(hidden)]
    pub fn take_digits_trace(&mut self) -> Vec<DigitEvent> {
        self.dtrace.as_mut().map(std::mem::take).unwrap_or_default()
    }

    /// The table, the model and what is built from them (once per lexicon and model); `None` when the feature has no data.
    pub(super) fn digit_env(&mut self) -> Option<DigitEnv> {
        let (st, table) = (self.lm.clone()?, self.digit_table.clone()?);
        let key = (Arc::as_ptr(&st.capped) as usize, st.lm.identity());
        if let Some(c) = self.digit_cache.clone().filter(|c| c.key == key) {
            return Some(DigitEnv { st, table, cache: c });
        }
        let idx = st.capped.predict_index(&st.lm);
        let cache = Arc::new(DigitCache { key, legal: Legal::new(st.capped.readings()), words: idx.digit_words(&table.all_words()) });
        self.digit_cache = Some(cache.clone());
        Some(DigitEnv { st, table, cache })
    }

    /// The text before the cursor as the context sees it: the left context and the display up to the cursor.
    fn text_before(&self, units: usize) -> String {
        let chars: Vec<char> = self.display.chars().collect();
        let at = (0..units).map(|i| self.token_width(i)).sum::<usize>().min(chars.len());
        format!("{}{}", self.left, chars[..at].iter().collect::<String>())
    }

    /// `lm::history(context_key(text))` as an owned string (`<s>` for none).
    fn history_of(&self, st: &LmState, text: &str) -> String {
        let key = context_key(text);
        history(if key == crate::learn::SENTINEL { "" } else { &key }, &st.lm).to_string()
    }

    /// The run's context when it starts at the cursor.
    fn run_ctx(&self, st: &LmState) -> String {
        self.history_of(st, &self.text_before(self.cursor))
    }

    /// Digits.md A.3.5: the history word `v` of the digit context at the cursor start, or `None` when the text before the cursor does not
    /// end in digits. An open run says so by its own counts and its `ctx`; without one, the digit tokens left of the cursor do, with the
    /// text before the first of them as the context.
    pub(super) fn digit_pred_v(&self, st: &LmState) -> Option<String> {
        if !self.digits_active() {
            return None;
        }
        if let Some(r) = &self.drun {
            return (r.emitted.len() + r.m >= 1).then(|| r.ctx.clone());
        }
        let group = (0..self.cursor).rev().take_while(|&i| digit_of(&self.syls[i]).is_some()).count();
        (group > 0).then(|| self.history_of(st, &self.text_before(self.cursor - group)))
    }

    /// The keys as digit tokens at the cursor, one unit each. Returns what a 40th unit sent, if it did. The previous key's tail is not part
    /// of that commit (`take_commit` sends what `shown` shows, unfinished symbols included): it is dropped first, and every caller sets
    /// the tail it wants after this call (`set_pend_from`, `clear_pend`, or `apply_pred_at` which clears it).
    fn emit_tokens(&mut self, keys: &[char]) -> Result<String, EngineError> {
        if !keys.is_empty() {
            self.clear_pend();
        }
        let mut commit = String::new();
        for &k in keys {
            self.d_emitted += 1;
            commit.push_str(&self.insert_token(token_reading(k), Some(k.to_string()), None)?.commit);
        }
        Ok(commit)
    }

    /// The unfinished syllable is the keys `tail` (mirrored in the columns and the shadow); none clears it.
    fn set_pend_from(&mut self, tail: &[char]) {
        self.clear_pend();
        for &k in tail {
            if let Some((col, sym)) = Layout::Standard.symbol_of(k) {
                self.shadow_push(k, false);
                self.pend[col] = Some(sym);
            }
        }
    }

    fn trace(&mut self, run: &DRun, flush: usize, dec: &Decision) {
        if let Some(t) = &mut self.dtrace {
            t.push(DigitEvent {
                ctx: run.ctx.clone(),
                buf: run.buf.iter().collect(),
                flush,
                splits: dec.splits.iter().map(|s| (s.m, s.t, s.score)).collect(),
                choice: dec.pick.map(|i| (dec.splits[i].m, dec.splits[i].t)),
            });
        }
    }

    /// (m, t) of the pick; with no legal split at all (not reachable for a key of the alphabet) every key is a digit.
    fn picked(dec: &Decision, n: usize) -> (usize, usize) {
        dec.pick.map_or((n, 0), |i| (dec.splits[i].m, dec.splits[i].t))
    }

    /// The buffered keys become tokens and the run ends (a setting that ends English mixing, `reset(Commit)`, a boundary key). Returns what a
    /// 40th unit sent and whether there was a run.
    pub(super) fn digit_flush(&mut self) -> Result<(String, bool), EngineError> {
        self.last_digits = None; // A.3.6: the other reading of a syllable that closed goes with a setting that ends the feature
        let Some(run) = self.drun.take() else { return Ok((String::new(), false)) };
        let commit = self.emit_tokens(&run.buf)?;
        self.d_shown = run.emitted.len() + run.buf.len();
        self.clear_pend();
        self.pred_dirty = true;
        Ok((commit, true))
    }

    /// `set_english(false)` and `set_abbreviation(true)`: the flushed digits first in the commit, the row recomputed for what stands now.
    pub(super) fn settle_with(&mut self, (commit, had_run): (String, bool)) -> Result<Output, EngineError> {
        if had_run {
            self.recompute_pred();
        }
        let mut o = self.settle_run()?;
        o.commit.insert_str(0, &commit);
        Ok(o)
    }

    /// A key that opens a run (hook 2 of `dispatch`): a key of the alphabet with no unfinished syllable and no window. `None`: not ours.
    pub(super) fn digit_start(&mut self, k: Key) -> Result<Option<Output>, EngineError> {
        if !(self.digits_active() && k.kind == KeyKind::Char && k.modifiers == 0 && is_alphabet(k.ch)) {
            return Ok(None);
        }
        if self.pend.iter().any(Option::is_some) || !self.pend_prev.is_empty() || self.cands.is_some() || !self.run.is_empty() {
            return Ok(None);
        }
        let Some(env) = self.digit_env() else { return Ok(None) };
        let ctx = self.run_ctx(&env.st);
        let c = k.ch;
        self.pred_dirty = true;
        if Layout::Standard.key_tone_mark(c).is_some() {
            // A tone key cannot be a tail alone: a digit token that opens the run (replaces rule 22a and rule 16's eating).
            let commit = self.emit_tokens(&[c])?;
            self.d_shown = 1;
            self.drun = Some(DRun { ctx, emitted: vec![c], buf: Vec::new(), m: 0 });
            return Ok(Some(self.view(true, commit)));
        }
        let mut run = DRun { ctx, emitted: Vec::new(), buf: vec![c], m: 0 };
        let dec = env.scorer().decide(&run.ctx, &run.emitted, &run.buf, None, None, false, true);
        self.trace(&run, 0, &dec);
        run.m = Self::picked(&dec, 1).0;
        self.set_pend_from(&run.buf[run.m..]);
        self.d_shown = run.m;
        self.drun = Some(run);
        Ok(Some(self.view(true, String::new())))
    }

    /// Hook 1 of `dispatch`: every key while a run is open (digits.md A.3.4).
    pub(super) fn digit_run_key(&mut self, k: Key) -> Result<Output, EngineError> {
        let c = (k.kind == KeyKind::Char && k.modifiers == 0).then_some(k.ch);
        match (k.kind, c) {
            (KeyKind::Char, Some(c)) if is_alphabet(c) => self.drun_alphabet(c),
            (KeyKind::Space, _) => self.drun_space(k),
            (KeyKind::Char, Some(c)) if Layout::Standard.symbol_of(c).is_some() => self.drun_tail_key(c),
            (KeyKind::Backspace, _) => self.drun_backspace(k),
            // Not in A.3.4's list of boundary keys, so as today: behind an unfinished syllable the key does nothing.
            (KeyKind::Up, _) => {
                self.pred_dirty = true; // the row was taken at the top of `dispatch`
                self.handled()
            }
            (KeyKind::Esc, _) => {
                // Same as rule 12: the buffer and the tail go, the tokens stay.
                self.drun = None;
                self.clear_pend();
                self.pred_dirty = true;
                self.handled()
            }
            _ => self.drun_boundary(k),
        }
    }

    /// A key of the alphabet: it joins the buffer (the oldest key leaves it as a token when there are five) and the splits are scored again.
    fn drun_alphabet(&mut self, c: char) -> Result<Output, EngineError> {
        let (Some(env), Some(mut run)) = (self.digit_env(), self.drun.take()) else { return self.handled() };
        let prev_d = run.emitted.len() + run.m;
        run.buf.push(c);
        let mut flushed = Vec::new();
        while run.buf.len() > MAX_BUF {
            flushed.push(run.buf.remove(0));
        }
        run.emitted.extend(&flushed);
        let commit = self.emit_tokens(&flushed)?;
        let tone = Layout::Standard.key_tone_mark(c).is_some();
        let dec = env.scorer().decide(&run.ctx, &run.emitted, &run.buf, None, Some(prev_d), tone, true);
        self.trace(&run, flushed.len(), &dec);
        let (m, t) = Self::picked(&dec, run.buf.len());
        if tone && t > 0 {
            // The tail is a complete syllable: the run ends, the syllable is made as rule 10 makes it.
            let tone_no = Layout::Standard.tone_of(c).unwrap_or(0);
            let tail = run.buf[m..run.buf.len() - 1].to_vec();
            return self.drun_finish(run, m, &tail, tone_no, commit);
        }
        run.m = m;
        self.set_pend_from(&run.buf[m..]);
        self.d_shown = run.emitted.len() + m;
        self.drun = Some(run);
        self.pred_dirty = true;
        Ok(self.view(true, commit))
    }

    /// The tail of the pick closes a syllable (`tone_no` 0: Space): the first `m` buffered keys become tokens, the syllable is made, the run ends.
    fn drun_finish(&mut self, run: DRun, m: usize, symbols: &[char], tone_no: usize, mut commit: String) -> Result<Output, EngineError> {
        commit.push_str(&self.emit_tokens(&run.buf[..m])?);
        self.d_shown = run.emitted.len() + m;
        self.set_pend_from(symbols);
        let tail_keys: String = run.buf[m..].iter().collect();
        let mut o = self.finish_syllable(tone_no, Vec::new())?;
        // A.3.6: the keys stay available as the other reading of this syllable, unless the syllable went out with a 40-unit commit.
        if o.commit.is_empty() {
            self.last_digits = Some(tail_keys);
        }
        o.commit.insert_str(0, &commit);
        Ok(o)
    }

    fn drun_space(&mut self, k: Key) -> Result<Output, EngineError> {
        let (Some(env), Some(run)) = (self.digit_env(), self.drun.take()) else { return self.handled() };
        let prev_d = run.emitted.len() + run.m;
        let dec = env.scorer().decide(&run.ctx, &run.emitted, &run.buf, Some(' '), Some(prev_d), true, true);
        self.trace(&run, 0, &dec);
        let (m, t) = Self::picked(&dec, run.buf.len());
        if t > 0 {
            let tail = run.buf[m..].to_vec();
            return self.drun_finish(run, m, &tail, 0, String::new());
        }
        // The digits win: the buffer becomes tokens and the Space is sent again (it types a space after digits).
        let commit = self.emit_tokens(&run.buf)?;
        self.d_shown = run.emitted.len() + run.buf.len();
        self.clear_pend();
        let mut o = self.dispatch(k)?;
        o.commit.insert_str(0, &commit);
        Ok(o)
    }

    /// A Zhuyin key that is not in the alphabet can only be in the tail: the best legal tail ends the run; none: every key is a digit and the key
    /// opens a syllable of its own (rule 14).
    fn drun_tail_key(&mut self, c: char) -> Result<Output, EngineError> {
        let (Some(env), Some(run)) = (self.digit_env(), self.drun.take()) else { return self.handled() };
        let prev_d = run.emitted.len() + run.m;
        let dec = env.scorer().decide(&run.ctx, &run.emitted, &run.buf, Some(c), Some(prev_d), false, true);
        self.trace(&run, 0, &dec);
        self.pred_dirty = true;
        let Some(pick) = dec.pick else {
            let commit = self.emit_tokens(&run.buf)?;
            self.d_shown = run.emitted.len() + run.buf.len();
            self.clear_pend();
            if let Some((col, sym)) = Layout::Standard.symbol_of(c) {
                self.start_syllable(c, col, sym);
            }
            return Ok(self.view(true, commit));
        };
        let m = dec.splits[pick].m;
        let commit = self.emit_tokens(&run.buf[..m])?;
        self.d_shown = run.emitted.len() + m;
        let tail: Vec<char> = run.buf[m..].iter().copied().chain([c]).collect();
        self.set_pend_from(&tail);
        Ok(self.view(true, commit))
    }

    /// Backspace takes the last buffered key and scores again; with nothing buffered the run ends and the key is the ordinary one (rules 11, 18).
    fn drun_backspace(&mut self, k: Key) -> Result<Output, EngineError> {
        let (Some(env), Some(mut run)) = (self.digit_env(), self.drun.take()) else { return self.handled() };
        if run.buf.is_empty() {
            self.clear_pend();
            return self.dispatch(k);
        }
        run.buf.pop();
        if run.buf.is_empty() {
            run.m = 0;
            self.clear_pend();
        } else {
            // Only the display is scored again: no tail may close a syllable (that is a key's job).
            let dec = env.scorer().decide(&run.ctx, &run.emitted, &run.buf, None, None, false, false);
            self.trace(&run, 0, &dec);
            run.m = Self::picked(&dec, run.buf.len()).0;
            self.set_pend_from(&run.buf[run.m..]);
        }
        self.d_shown = run.emitted.len() + run.m;
        if !(run.buf.is_empty() && run.emitted.is_empty()) {
            self.drun = Some(run);
        }
        self.pred_dirty = true;
        self.handled()
    }

    /// Any other key ends the run: the buffer becomes tokens (a tail without a tone is not a word, so its keys are digits), the row is
    /// recomputed for them (a window opened by this key lists it), and the key is handled as it would be.
    fn drun_boundary(&mut self, k: Key) -> Result<Output, EngineError> {
        let (commit, _) = self.digit_flush()?;
        if k.kind == KeyKind::Enter && !commit.is_empty() && self.syls.is_empty() {
            return Ok(self.view(true, commit)); // Enter that sent everything stops there (a 40th unit can leave tokens in a new composition)
        }
        self.pred_dirty = false;
        self.recompute_pred();
        let mut o = self.dispatch(k)?;
        o.commit.insert_str(0, &commit);
        Ok(o)
    }

    /// A row item is chosen while a run is open (digits.md A.3.5): the buffered keys that show as digits become tokens (the item starts
    /// behind them), the tail is consumed like `pend`. Returns what a 40th unit sent.
    pub(super) fn digit_pre_pick(&mut self, p: &mut Pred) -> Result<String, EngineError> {
        let Some(run) = self.drun.take() else { return Ok(String::new()) };
        let mut commit = String::new();
        if run.m > 0 {
            commit = self.emit_tokens(&run.buf[..run.m])?;
            p.start = self.cursor; // only the cursor start is queried while digits are buffered
            // The item was keyed under the word before the buffered digits; behind digit tokens that is SENTINEL (the digits are not
            // Han), which is how `recompute_pred` keys an item once they are tokens.
            p.key = context_key(&self.text_before(self.cursor));
        }
        self.d_shown = run.emitted.len() + run.m;
        Ok(commit)
    }

    /// Digits.md A.3.6, the second form: the longest syllable (at most four keys, the last a tone key) that the digit tokens ending at
    /// `end` could be, as (tokens, syllable, its first word).
    pub(super) fn digit_syllable_alt(&mut self, end: usize) -> Option<(usize, String, String)> {
        let env = self.digit_env()?;
        let keys: Vec<char> = (0..end).rev().map_while(|i| digit_of(&self.syls[i])).take(MAX_BUF).collect::<Vec<_>>().into_iter().rev().collect();
        for n in (2..=keys.len()).rev() {
            let tail = &keys[keys.len() - n..];
            let (body, last) = (&tail[..n - 1], tail[n - 1]);
            let Some(mark) = Layout::Standard.key_tone_mark(last) else { continue };
            let Some(mut syl) = body.iter().map(|&k| Layout::Standard.symbol_of(k).map(|x| x.1)).collect::<Option<String>>() else { continue };
            syl.push(mark);
            if env.cache.legal.has_han_syllable(&syl) {
                let word = self.lex.entries(std::slice::from_ref(&syl)).first().map(|(w, _)| w.to_string())?;
                return Some((n, syl, word));
            }
        }
        None
    }

    /// The last item of a window (digits.md A.3.6) was chosen. Returns what a 40th unit sent.
    pub(super) fn choose_digit_alt(&mut self, alt: DigitAlt, word: String) -> Result<String, EngineError> {
        self.pred_dirty = true;
        match alt {
            DigitAlt::Digits(keys) => {
                // The syllable before the cursor goes, its digit keys come in its place one unit each.
                self.cursor = self.cursor.saturating_sub(1);
                self.remove_syllable(self.cursor)?;
                let keys: Vec<char> = keys.chars().collect();
                self.emit_tokens(&keys)
            }
            DigitAlt::Syllable { n, syl } => {
                for _ in 0..n {
                    self.cursor = self.cursor.saturating_sub(1);
                    self.remove_syllable(self.cursor)?;
                }
                // Like the Chinese item of the English window: the word is fixed, nothing is learned.
                Ok(self.insert_token(syl, Some(word), None)?.commit)
            }
        }
    }
}
