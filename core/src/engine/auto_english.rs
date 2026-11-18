//! The engine side of "English or Zhuyin at the closing key" (docs/spec/english-mixed.md §16): which closing keys are decision points, the
//! comparison of `crate::english_auto` on the engine's model, what a win of English does (exactly what revert (c) does at the same key),
//! and the Esc right after the token it made at Space. Unreached without the table, a model and the core switch: the engine is then the
//! one of the slice before, bit for bit.

use super::*;
use crate::digits::{is_alphabet, lp_z_words};
use crate::english_auto::{lp_z, takes_english, valid};
use crate::predict::units_of;

/// One comparison the engine made, for tests and the measuring tools (no Debug: holds input text, R2).
#[derive(Clone, PartialEq)]
pub struct AutoEvent {
    pub ctx: String,
    pub e: String,
    /// (n_ow, n_dup, n_rev)
    pub n: (u32, u32, u32),
    pub lp_e: f64,
    /// log10 of the sum of P(w | C) over the compatible words, the reading term of the best of them, and lpZ (both and the penalties).
    pub sum: f64,
    pub term: f64,
    pub lp_z: f64,
    pub english: bool,
}

impl Engine {
    /// docs/spec/english-mixed.md §16.7: the core switch of the comparison (default on). Off, every key is the key of the engine without it.
    /// Returns the snapshot.
    pub fn set_english_auto(&mut self, on: bool) -> Result<Output, EngineError> {
        self.auto_on = on;
        self.setting_changed();
        self.handled()
    }

    /// Test and tool hook: the table (shared across engines), or none.
    #[doc(hidden)]
    pub fn set_auto_table(&mut self, table: Option<Arc<AutoTable>>) {
        self.auto_table = table;
    }

    /// Test and tool hook: read `english.tsv` at `path`; false (nothing changes) when it is missing or malformed.
    #[doc(hidden)]
    pub fn load_auto_english(&mut self, path: &Path) -> bool {
        let Some(t) = std::fs::read_to_string(path).ok().and_then(|t| AutoTable::parse(&t)) else { return false };
        self.auto_table = Some(Arc::new(t));
        true
    }

    /// Tool hook: whether a table is loaded.
    #[doc(hidden)]
    pub fn auto_table_loaded(&self) -> bool {
        self.auto_table.is_some()
    }

    /// Tool hook (the grid's `--auto-theta`, `--auto-kappa`, `--auto-kappa-dup`): the knobs of this engine, the constants in one nothing has set.
    #[doc(hidden)]
    pub fn set_auto_params(&mut self, p: AutoParams) {
        self.auto_params = p;
    }

    /// Tool hook: (n_ow, n_dup, n_rev) when the latest `key()` closed a syllable at a decision point (whatever the switch and the table say).
    #[doc(hidden)]
    pub fn english_auto_point(&self) -> Option<(u32, u32, u32)> {
        self.auto_point
    }

    /// Tool hook: the comparison the latest `key()` made, if it made one.
    #[doc(hidden)]
    pub fn english_auto_event(&self) -> Option<&AutoEvent> {
        self.auto_event.as_ref()
    }

    /// docs/spec/english-mixed.md §16.1: the counts of the syllable `syl` about to be made from the unfinished one, when its closing key is a decision
    /// point: the lexicon has it with an entry that holds a Han character, and a key was replaced or the reversal guard took one out of column order.
    /// Not a syllable Esc brought back, nor one whose first key the digit layer laid down.
    pub(super) fn auto_point_of(&self, syl: &str) -> Option<(u32, u32, u32)> {
        if !self.shadow_on() || self.pend_plain || self.typed.chars().next().is_none_or(is_alphabet) {
            return None;
        }
        let n_ow = (self.typed.chars().count() - self.shadow_key.iter().flatten().count()) as u32;
        let n = (n_ow, self.pend_dup, u32::from(self.pend_rev));
        let han = self.lex.entries(&[syl.to_string()]).iter().any(|(w, _)| w.chars().any(is_han));
        (han && (n.0 > 0 || n.2 > 0)).then_some(n)
    }

    /// docs/spec/english-mixed.md §16.2, §16.3: at a decision point, English when lpE > lpZ + theta. `Some`: the output of the key as English (Space: one
    /// token, as revert (c) at Space, marked for Esc; a tone key: an open run with the restore point, as revert (c) at a tone key). `None`: Zhuyin, as today.
    /// `score` is the syllable the Zhuyin side is scored as: `syl` itself, or the third tone of an unaccepted syllable (docs/spec/english-mixed.md §16.1).
    pub(super) fn auto_english(&mut self, tone: usize, syl: &str, score: &str, n: (u32, u32, u32)) -> Result<Option<Output>, EngineError> {
        if !self.auto_on {
            return Ok(None);
        }
        let (Some(table), Some(st)) = (self.auto_table.clone(), self.lm.clone()) else { return Ok(None) };
        let mut e = self.typed.clone();
        e.extend((tone > 0).then(|| TONE_KEYS_STANDARD[tone - 1]));
        if !valid(&e) {
            return Ok(None);
        }
        let ctx = self.run_ctx(&st);
        let lp_e = table.lp_e(&ctx, &e);
        if lp_e == f64::NEG_INFINITY {
            return Ok(None); // nothing to compare: Zhuyin wins any tie
        }
        // The syllable as `Legal::tail_units` hands it to the scorer: its symbols, then the tone mark or a space.
        let units = units_of(&if tone == 0 { format!("{score} ") } else { score.to_string() });
        let (sum, best) = lp_z_words(&st.lm, st.capped.predict_index(&st.lm), st.lm.scoring(self.profile), &ctx, &units);
        let term = match (best, st.capped.reading_table().filter(|_| self.reading_decode)) {
            (Some(w), Some(rd)) => rd.term(&self.lex, score, w),
            _ => 0.0,
        };
        let z = lp_z(sum, term, n.0, n.1, n.2, &self.auto_params);
        let english = takes_english(lp_e, z, &self.auto_params);
        self.auto_event = Some(AutoEvent { ctx, e: e.clone(), n, lp_e, sum, term, lp_z: z, english });
        if !english {
            return Ok(None);
        }
        if tone > 0 {
            let point = (self.pend, self.shadow_key, self.typed.clone());
            return self.revert_to(Revert::Auto, e, Some(point)).map(Some);
        }
        self.clear_pend();
        self.run = e.clone();
        self.run_rev = true;
        self.reverted = Some(Revert::Auto);
        let commit = self.end_run()?;
        if commit.is_empty() {
            self.last_auto = Some((self.cursor - 1, e, syl.to_string()));
        }
        Ok(Some(self.view(true, commit)))
    }

    /// docs/spec/english-mixed.md §16.6: Esc right after the token the comparison made at Space. The token goes and the syllable its keys are today
    /// (the Zhuyin replay of the key record and Space) comes in its place by rule 10; its keys are the short-word mark, so Down lists them last.
    pub(super) fn auto_undo(&mut self, i: usize, e: String, syl: String) -> Result<Output, EngineError> {
        self.cursor = i;
        self.remove_syllable(i)?;
        self.pred_dirty = true;
        let o = self.insert_token(syl, None, None)?;
        if o.commit.is_empty() {
            self.last_short = Some((self.cursor - 1, e));
        }
        Ok(o)
    }
}
