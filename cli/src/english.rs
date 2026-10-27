//! `english-sim` (docs/contracts/zhen-mixed.md section 4): how often the English window opens by mistake while typing Chinese,
//! and how many words of a list open it at all. It drives the real engine (`Engine::load_english`, `set_english(true)`) and
//! reads `Engine::english_open()` after every Space: the trigger rule is the engine's, never repeated here.
//!
//!   english-sim --words FILE --rows FILE [--p 0.02] [--repair overwrite|backspace] [--seed 20261014] [--repeat 5] [--inline]
//!   english-sim --words FILE --recall
//!   either of them with --key-order (docs/contracts/zhen-keyorder.md section 4)
//!
//! Output is one TSV line and no content text (R2). Typing mode:
//!   rows_sha256  rows  syllables  keys  triggers  per_1000_mean  per_1000_max
//! `rows` and `syllables` are one pass over the rows; `keys` (every key event sent to the engine, repairs, Esc and Enter
//! included) and `triggers` are summed over the seeds; `per_1000` is triggers x 1000 / syllables of one seed, mean and max
//! over the seeds. With `--inline` (docs/contracts/zhen-inline.md section 3) two more columns follow,
//!   inline_per_1000_mean  inline_per_1000_max  autokeep_per_1000_mean  autokeep_per_1000_max
//! the same figure for the syllables after one of whose keys the engine's display rule D held (`Engine::english_inline_shown`,
//! asked after every key; a syllable counts once): how often the Zhuyin of a Chinese syllable would show as letters. The last two
//! count the auto-keeps (zhen-inline section 1.3, revision 2): the keys after which the engine reports that it turned the letters
//! into an English token (`Engine::english_autokept`, asked after every key, before the symbols are compared), per 1000 syllables
//! of one seed. Without `--inline` the line is the one above, unchanged. Recall mode:
//!   words_sha256  words  opened  top1  overwrite0  overwrite1_leftover_valid  overwrite2plus_leftover_valid  leftover_invalid
//! (`opened` and `top1`: the window opened / its first item is the word; the four classes partition the words, the last two
//! open it, the first two never do; the tool stops if the engine and this recount disagree.)
//!
//! With `--key-order` the engine has the key-order switch on (`Engine::set_english_key_order`) and the output grows; without it the
//! output is byte for byte the one described above. Typing mode: after the line above, one line per word of `TWO_LETTER`,
//!   two  <word>  per_1000_mean  per_1000_max
//! the same figure (per 1000 syllables of one seed, mean and max over the seeds) for the windows that opened for that word: the
//! engine's shadow (`Engine::english_window_shadow`) at the moment the window is open after Space is that two-letter word. Recall mode
//! prints only what the engine did, no recount (the four classes are the rules of the switch-off engine):
//!   recall  words  n  opened  top1      (the list)
//!   recall  two    n  opened  top1      (`TWO_LETTER`)
//!   probe   <word> 0|1                   (ok ls cd mv cup npm: whether the window opened)
//!
//! With `--digits` (docs/contracts/digits.md A.4.2) the engine is `Engine::new` on data/lexicon (digits.tsv read) with the model
//! (`--lm`, default data/lm/bigram.sjlm), and six columns follow (mean and max over the seeds, per 1000 syllables of one seed):
//!   digits_final_mean  digits_final_max  digits_shown_mean  digits_shown_max  digits_extra_bs_mean  digits_extra_bs_max
//! `final` counts the digit characters (0-9 . / -) in what the rows commit, which is the digit tokens left at the end (the rows are
//! Chinese); `extra_bs` (report only) counts the Backspaces the repair pressed because the screen still showed something in front of
//! the syllable (a digit token left by an illegal neighbour typo) when the symbol model of section 4.2 would have stopped; `shown` counts the keys after which `Engine::digits_shown()` was not 0, the keys that for a moment show a digit. The
//! engine decides what is a digit; the simulator only reads `Engine::digits_typed()` (the buffered keys) where it reads the columns
//! otherwise, and does not insist on its own plan for the symbols (a key may be a digit).
//!
//! With `--digits` the `backspace` repair reads the engine's preedit (digits.md A.10) instead of the model of section 4.2: it presses
//! Backspace until the preedit minus what it was when the syllable began is a prefix of the wanted symbols (or empty), and goes on while
//! the preedit no longer starts with that base. A person sees a digit token the engine made of a first key (ㄅ, ㄉ, ㄓ followed by a key that
//! cannot continue it) and presses Backspace once more. Without `--digits` the repair is exactly section 4.2's; `overwrite` presses no Backspace.
//!
//! The typing model (fixed; change it and the number in cli/tests/english_sim.rs together):
//! - Every Zhuyin key of a syllable is mistyped with probability `p`, but only when it has a neighbour: the keys left and right of
//!   it on its keyboard row, kept only if they are Zhuyin keys of the standard layout. One neighbour or two, drawn uniformly.
//!   Tone keys, Space, Backspace and the repair keys are never mistyped.
//! - Repair: `overwrite` presses the right key at once when the wrong key was in the same column (one overwrite), else it
//!   falls back to `backspace`: Backspace until the symbols (read from the engine, see `Sim::have`) are a prefix of the wanted
//!   ones, then the missing symbols in column order. After a repair the symbols are the wanted ones.
//! - A syllable ends with its tone key, or Space for tone 1. If the engine opened the window at that Space it is counted, then
//!   Esc (closes it, clears the shadow) and Space again finish the syllable, so engine and simulator stay in step.
//! - Auto-keep (`--inline` only counts it; the recovery happens either way, it can only occur with D): the key that made it
//!   started a new syllable, so Esc clears that symbol, Backspace and Esc take the English token the engine made (Backspace turns
//!   it into a run of one letter less, zhen-inline section 1.3g, Esc takes the run), and the interrupted
//!   syllable (its symbols up to the one being typed) is typed again from the start without a mistake, so the row goes on as if
//!   the mistake had been caught in time. A retyping that keeps again is counted and recovered again (it cannot happen without
//!   an overwrite; after three tries the run stops instead of looping).
//! - Rows with a reading the lexicon lacks, or a symbol without a key, are skipped (their number goes to stderr).
//! - Random numbers: splitmix64 from the seed (no crate); one draw decides a mistake, one more picks the neighbour.
//! `english-sim` (docs/contracts/zhen-revert.md section 4.1): how often the direct revert fires by mistake while typing Chinese, and what it
//! does with a list of words. It drives the real engine (`set_english(true)`, the switches `set_english_overwrite` and
//! `set_english_key_order`) and asks it what it did after every key (`Engine::english_reverted`, `Engine::english_split`): the rules
//! are the engine's, never repeated here.
//!
//!   english-sim --words FILE --rows FILE [--p 0.02] [--repair overwrite|backspace] [--seed 20261014] [--repeat 5]
//!               [--overwrite on|off] [--key-order on|off] [--digits [--lm FILE]]
//!   english-sim --words FILE --recall [--overwrite on|off] [--key-order on|off]
//!
//! `--overwrite` and `--key-order` (default on) are the engine's two switches; the third, the mixing switch, is on. With both off only
//! the Space case (c) is left (a syllable the lexicon lacks, at Space).
//!
//! Output is one TSV line and no content text (R2). Typing mode:
//!   rows_sha256  rows  syllables  keys  typos  revert_a_mean revert_a_max  revert_b_mean revert_b_max  revert_c_mean revert_c_max
//!   split_mean split_max  english_final_mean english_final_max
//! `rows` and `syllables` are one pass over the rows; `keys` (every key event sent to the engine, repairs, Esc and Enter included) and
//! `typos` (the mistyped keys the model injected) are summed over the seeds; the rest are per 1000 syllables of one seed, mean and max over
//! the seeds: `revert_a`, `revert_b`, `revert_c` the keys after which the engine reported a revert of that kind (an overwrite, a key out of
//! column order, a syllable the lexicon lacks), `split` the keys after which it reported a tone-key split of an English run, `english_final`
//! the rows whose committed text still holds an ASCII letter (an English token left at the end of a row that is Chinese).
//!
//! Recall mode: every word of the list typed letter by letter on an engine of its own, then Space:
//!   words_sha256  words  opened  revert_a  revert_b  revert_c  none
//! `opened`: the composition is exactly one English token equal to the word; the four classes partition the words by the first revert the
//! engine reported (`none`: it never reverted, the keys became Chinese). Then one line `probe <word> token|中文|其他` for each of
//! ok ls cd mv pr ai ui go cp cup npm gta6 rdr2 poe2 mp3 a4 i7 s7 (a fixed public list): whether the keys ended as one English token, as
//! Chinese syllables only, or as something else.
//!
//! With `--digits` (docs/contracts/digits.md A.4.2) the engine is `Engine::new` on data/lexicon (digits.tsv read) with the model
//! (`--lm`, default data/lm/bigram.sjlm), and six columns follow (mean and max over the seeds, per 1000 syllables of one seed):
//!   digits_final_mean  digits_final_max  digits_shown_mean  digits_shown_max  digits_extra_bs_mean  digits_extra_bs_max
//! `final` counts the digit characters (0-9 . / -) in what the rows commit, which is the digit tokens left at the end (the rows are
//! Chinese); `extra_bs` (report only) counts the Backspaces the repair pressed because the screen still showed something in front of
//! the syllable (a digit token left by an illegal neighbour typo) when the symbol model of section 4.2 would have stopped; `shown` counts the keys after which `Engine::digits_shown()` was not 0, the keys that for a moment show a digit. The
//! engine decides what is a digit; the simulator only reads `Engine::digits_typed()` (the buffered keys) where it reads the columns
//! otherwise, and does not insist on its own plan for the symbols (a key may be a digit).
//!
//! With `--digits` the `backspace` repair reads the engine's preedit (digits.md A.10) instead of the model of section 4.2: it presses
//! Backspace until the preedit minus what it was when the syllable began is a prefix of the wanted symbols (or empty), and goes on while
//! the preedit no longer starts with that base. A person sees a digit token the engine made of a first key (ㄅ, ㄉ, ㄓ followed by a key that
//! cannot continue it) and presses Backspace once more. Without `--digits` the repair is exactly section 4.2's; `overwrite` presses no Backspace.
//!
//! The typing model (fixed; change it and the number in cli/tests/english_sim.rs together):
//! - Every Zhuyin key of a syllable is mistyped with probability `p`, but only when it has a neighbour: the keys left and right of
//!   it on its keyboard row, kept only if they are Zhuyin keys of the standard layout. One neighbour or two, drawn uniformly.
//!   Tone keys, Space, Backspace and the repair keys are never mistyped.
//! - Repair: `overwrite` presses the right key at once when the wrong key was in the same column (one overwrite), else it
//!   falls back to `backspace`: Backspace until the symbols (read from the engine, see `Sim::have`) are a prefix of the wanted
//!   ones, then the missing symbols in column order. After a repair the symbols are the wanted ones.
//! - A syllable ends with its tone key, or Space for tone 1.
//! - A revert (zhen-revert section 4.1): the engine reports it after the key; it is counted by kind and undone: Esc (it brings the Zhuyin
//!   back), Esc again (the unfinished syllable goes; the composition is as before the syllable), or, when the key was the Space that made a
//!   token at once, Backspace and Esc (the token becomes a run, the run goes). The syllable is then typed again from the start without a
//!   mistake, so the row goes on as if the mistake had been caught in time. A retyping that reverts again is counted and undone again
//!   (it cannot happen without a wrong key; after three tries the run stops instead of looping). The model repairs a wrong key at once, so a
//!   revert at a tone key or Space (c) is not expected: the tone-key cost of (c) is not measured here (zhen-revert section 4.1).
//! - Rows with a reading the lexicon lacks, or a symbol without a key, are skipped (their number goes to stderr).
//! - Random numbers: splitmix64 from the seed (no crate); one draw decides a mistake, one more picks the neighbour.
use core::engine::{load_lexicon, Engine, Key, KeyKind, Layout, ResetMode, Revert, MAX_SYLLABLES};
use core::eval::{parse_rows, row_syllables, sha256_hex, usable};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::Arc;

const ROWS: [&str; 4] = ["1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"];
const TONE_MARKS: [char; 4] = ['ˊ', 'ˇ', 'ˋ', '˙'];
const LAYOUT: Layout = Layout::Standard;
/// The most Backspaces one repair presses (a composition holds `MAX_SYLLABLES` units at most, and each press takes one symbol or one unit; the
/// 24 over it are the margin the bound had at 40 units: 64).
const MAX_REPAIR_BACKSPACES: u32 = MAX_SYLLABLES as u32 + 24;
/// The most times one syllable is typed again after a revert before the run stops (a clean typing cannot revert).
const MAX_RETRIES: usize = 3;
/// The words `probe` lines are printed for (a fixed public list, zhen-revert section 4.1).
const PROBES: [&str; 18] = ["ok", "ls", "cd", "mv", "pr", "ai", "ui", "go", "cp", "cup", "npm", "gta6", "rdr2", "poe2", "mp3", "a4", "i7", "s7"];

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
    /// Uniform in [0, 1).
    fn unit(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
}

/// The Zhuyin keys next to `key` on its row (standard layout), tone keys and non-Zhuyin keys dropped.
fn neighbours(key: char) -> Vec<char> {
    let Some(row) = ROWS.iter().find(|r| r.contains(key)) else { return Vec::new() };
    let cs: Vec<char> = row.chars().collect();
    let j = cs.iter().position(|&c| c == key).unwrap();
    [j.checked_sub(1), Some(j + 1)].into_iter().flatten().filter_map(|i| cs.get(i).copied()).filter(|&c| LAYOUT.symbol_of(c).is_some()).collect()
}

/// A syllable as (column, symbol) pairs in typing order and its tone (None: tone 1, Space); `None` when a character has no key.
fn parse_syllable(syl: &str) -> Option<(Vec<(usize, char)>, Option<char>)> {
    let mut syms = Vec::new();
    let mut tone = None;
    for c in syl.chars() {
        if TONE_MARKS.contains(&c) {
            tone = Some(c);
        } else {
            syms.push(LAYOUT.symbol_of(LAYOUT.key_of_symbol(c)?)?);
        }
    }
    Some((syms, tone))
}

#[derive(Clone, Copy, PartialEq)]
enum Repair {
    Overwrite,
    Backspace,
}

struct Sim {
    engine: Engine,
    keys: u64,
    /// The mistyped keys injected.
    typos: u64,
    /// Reverts the engine reported, by kind (a, b, c), and tone-key splits it reported.
    reverts: [u64; 3],
    splits: u64,
    /// Rows whose committed text still held an ASCII letter after the Enter that ended them.
    english_final: u64,
    /// The symbols of the syllable being typed up to the key in progress: what a retyping types again.
    want: Vec<char>,
    /// `--digits`: the engine is the digit-feature one; the counters below are read after every key.
    digits: bool,
    /// `--digits` (digits.md A.10): the preedit after the latest key, and the preedit when the syllable began; the Backspace repair reads
    /// what the person sees. `extra_bs` counts the Backspaces the engine's screen needed beyond the symbol model's (a digit token left in front).
    last_preedit: String,
    base_preedit: String,
    last_commit: String,
    extra_bs: u64,
    final_digits: u64,
    shown_keys: u64,
    p: f64,
    repair: Repair,
    rng: Rng,
}

impl Sim {
    /// A simulator with every counter at zero; `digits` is off (set it after).
    fn new(engine: Engine, p: f64, repair: Repair, seed: u64) -> Sim {
        Sim {
            engine,
            keys: 0,
            typos: 0,
            reverts: [0; 3],
            splits: 0,
            english_final: 0,
            want: Vec::new(),
            digits: false,
            last_preedit: String::new(),
            base_preedit: String::new(),
            last_commit: String::new(),
            extra_bs: 0,
            final_digits: 0,
            shown_keys: 0,
            p,
            repair,
            rng: Rng(seed),
        }
    }

    /// One key to the engine, counted. What the engine reports about it (a revert, a split) is asked after every key, and counted
    /// here; the revert is returned for the caller to undo.
    fn raw(&mut self, k: Key) -> Result<Option<Revert>, String> {
        self.keys += 1;
        let o = self.engine.key(k).map_err(|_| "the engine failed on a key".to_string())?;
        self.last_preedit = o.preedit;
        self.last_commit = o.commit;
        if self.digits {
            self.final_digits += self.last_commit.chars().filter(|c| core::digits::is_alphabet(*c)).count() as u64;
            self.shown_keys += (self.engine.digits_shown() > 0) as u64;
        }
        self.splits += self.engine.english_split() as u64;
        let reverted = self.engine.english_reverted();
        if let Some(kind) = reverted {
            self.reverts[kind as usize] += 1;
        }
        Ok(reverted)
    }

    /// Takes the revert of a key back: the composition is as it was before the syllable began, and the syllable's symbols up to `self.want`
    /// are typed again without a mistake (a retyping that reverts again is undone again, three times at most). `space`: the key was the
    /// Space that made the token at once.
    fn recover(&mut self, mut kind: Revert, mut space: bool) -> Result<(), String> {
        for _ in 0..MAX_RETRIES {
            if kind == Revert::Missing && space {
                self.raw(Key::new(KeyKind::Backspace))?; // the token becomes a run of one letter less
            } else {
                self.raw(Key::new(KeyKind::Esc))?; // the Zhuyin comes back
            }
            self.raw(Key::new(KeyKind::Esc))?; // and goes: the run, or the unfinished syllable
            let mut again = None;
            for sym in self.want.clone() {
                if let Some(k) = self.raw(Key::ch(LAYOUT.key_of_symbol(sym).unwrap(), 0))? {
                    again = Some(k);
                    break;
                }
            }
            match again {
                None => return Ok(()),
                Some(k) => (kind, space) = (k, false),
            }
        }
        Err("a revert kept coming back on a typing without mistakes".into())
    }

    /// One key of the typing. `Ok(true)` when it reverted and the recovery has run: the syllable then stands as typed without a mistake
    /// up to `self.want`, and the caller does not send what is left of this symbol.
    fn send(&mut self, k: Key) -> Result<bool, String> {
        match self.raw(k)? {
            None => Ok(false),
            Some(kind) => self.recover(kind, false).map(|()| true),
        }
    }

    fn zhuyin(&mut self, sym: char) -> Result<bool, String> {
        self.send(Key::ch(LAYOUT.key_of_symbol(sym).unwrap(), 0))
    }

    /// The symbols of the unfinished syllable in display order, read from the engine, so the simulator does not work them out itself.
    fn have(&self) -> Vec<char> {
        if let Some(typed) = self.engine.digits_typed() {
            return typed;
        }
        self.engine.english_columns().iter().flatten().copied().collect()
    }

    /// The symbols the Backspace repair counts as typed. Without `--digits`: the engine's columns (zhen-mixed section 4.2). With it
    /// (digits.md A.10): the preedit behind where the syllable began, as the person sees it; a preedit that no longer starts with that
    /// base is not a prefix of anything wanted, so the repair goes on.
    fn repair_have(&self) -> Vec<char> {
        if !self.digits {
            return self.have();
        }
        self.last_preedit.strip_prefix(self.base_preedit.as_str()).map_or(vec!['\0'], |r| r.chars().collect())
    }

    /// One syllable typed.
    fn syllable(&mut self, syms: &[(usize, char)], tone: Option<char>) -> Result<(), String> {
        self.base_preedit = self.last_preedit.clone();
        for (i, &(col, sym)) in syms.iter().enumerate() {
            let key = LAYOUT.key_of_symbol(sym).unwrap();
            let near = neighbours(key);
            let want: Vec<char> = syms[..=i].iter().map(|s| s.1).collect();
            self.want = want.clone();
            // A labeled block: a revert and its recovery leave the syllable typed up to `want` already, so the rest of this
            // symbol's keys are not sent.
            'symbol: {
                if self.rng.unit() < self.p && !near.is_empty() {
                    self.typos += 1;
                    let wrong = near[(self.rng.next() % near.len() as u64) as usize];
                    let (wcol, _) = LAYOUT.symbol_of(wrong).unwrap();
                    if self.send(Key::ch(wrong, 0))? {
                        break 'symbol;
                    }
                    if self.repair == Repair::Overwrite && wcol == col {
                        self.zhuyin(sym)?;
                    } else {
                        // Backspace until the symbols are a prefix of the wanted ones. Which symbol it takes is the engine's: the last
                        // in display order (final, medial, initial) by rule 11. Each key is a step that shortens the symbols or empties
                        // them, so the loop ends with them empty at the latest.
                        // An empty preedit ends it as well: a slip that made the last unit sent the whole composition (A.10), so the preedit
                        // never starts with `base_preedit` again, and the syllable is typed again from its first symbol below.
                        let mut presses = 0;
                        while !want.starts_with(&self.repair_have()) && !self.last_preedit.is_empty() {
                            presses += 1;
                            if presses > MAX_REPAIR_BACKSPACES {
                                return Err("the Backspace repair did not settle".into());
                            }
                            if self.digits && want.starts_with(&self.have()) {
                                self.extra_bs += 1; // the symbol model would stop here; the screen still shows something before the syllable
                            }
                            self.send(Key::new(KeyKind::Backspace))?;
                        }
                        if self.last_preedit.is_empty() {
                            self.base_preedit.clear();
                        }
                        for &(_, s) in &syms[self.repair_have().len()..=i] {
                            if self.zhuyin(s)? {
                                break 'symbol;
                            }
                        }
                    }
                } else {
                    self.zhuyin(sym)?;
                }
            }
            // The simulator's plan and the engine's columns must agree here; if the engine ever repairs differently the run stops
            // instead of counting a typing the model did not intend.
            if !self.digits && self.have() != want {
                return Err("the engine's symbols differ from the typing model's after a key".into());
            }
        }
        self.want = syms.iter().map(|s| s.1).collect();
        let end = match tone {
            Some(mark) => Key::ch(LAYOUT.key_of_tone(mark).unwrap(), 0),
            None => Key::new(KeyKind::Space),
        };
        for _ in 0..MAX_RETRIES {
            match self.raw(end)? {
                None => return Ok(()),
                Some(kind) => self.recover(kind, tone.is_none())?,
            }
        }
        Err("a revert kept coming back on the end of a syllable typed without mistakes".into())
    }
}

/// `--digits`: the whole engine as the shell builds it (the table is read from the data directory), with the model.
fn engine_digits(root: &Path, lm: &Path, words: &Path, switches: Switches) -> Result<Engine, String> {
    let mut e = Engine::new(&root.join("data/lexicon"), LAYOUT).map_err(|_| "cannot build the engine")?;
    e.load_lm(lm).map_err(|_| "cannot load the model")?;
    e.load_english(words).map_err(|_| "cannot load the word list".to_string())?;
    switches.apply(&mut e)?;
    Ok(e)
}

fn engine_with(lex: &Arc<Lexicon>, words: &Path, switches: Switches) -> Result<Engine, String> {
    let mut e = Engine::with_lexicon(lex.clone(), LAYOUT);
    e.load_english(words).map_err(|_| "cannot load the word list".to_string())?;
    switches.apply(&mut e)?;
    Ok(e)
}

/// The engine's two revert switches (the mixing switch is always on here).
#[derive(Clone, Copy)]
struct Switches {
    overwrite: bool,
    key_order: bool,
}

impl Switches {
    fn apply(self, e: &mut Engine) -> Result<(), String> {
        e.set_english(true).map_err(|_| "cannot switch English mixing on".to_string())?;
        e.set_english_overwrite(self.overwrite).map_err(|_| "cannot set the overwrite switch".to_string())?;
        e.set_english_key_order(self.key_order).map_err(|_| "cannot set the key-order switch".to_string())?;
        Ok(())
    }
}

/// (mean, max) of per-seed values.
fn mean_max(v: &[f64]) -> (f64, f64) {
    (v.iter().sum::<f64>() / v.len() as f64, v.iter().cloned().fold(0.0, f64::max))
}

fn read(path: &Path) -> Result<Vec<u8>, String> {
    std::fs::read(path).map_err(|e| format!("cannot read file ({:?})", e.kind()))
}

fn on_off(v: String) -> Result<bool, String> {
    match v.as_str() {
        "on" => Ok(true),
        "off" => Ok(false),
        _ => Err("--overwrite and --key-order take on or off".into()),
    }
}

pub fn run(args: &[String], root: &Path) -> Result<(), String> {
    let (mut words, mut rows, mut recall) = (None::<PathBuf>, None::<PathBuf>, false);
    let (mut digits, mut lm) = (false, None::<PathBuf>);
    let mut switches = Switches { overwrite: true, key_order: true };
    let (mut p, mut repair, mut seed, mut repeat) = (0.02f64, Repair::Overwrite, 20261014u64, 5u64);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut val = || it.next().cloned().ok_or_else(|| "missing option value".to_string());
        match a.as_str() {
            "--words" => words = Some(val()?.into()),
            "--rows" => rows = Some(val()?.into()),
            "--recall" => recall = true,
            "--digits" => digits = true,
            "--overwrite" => switches.overwrite = on_off(val()?)?,
            "--key-order" => switches.key_order = on_off(val()?)?,
            "--lm" => lm = Some(val()?.into()),
            "--p" => p = val()?.parse().map_err(|_| "bad --p")?,
            "--repair" => {
                repair = match val()?.as_str() {
                    "overwrite" => Repair::Overwrite,
                    "backspace" => Repair::Backspace,
                    _ => return Err("--repair must be overwrite or backspace".into()),
                }
            }
            "--seed" => seed = val()?.parse().map_err(|_| "bad --seed")?,
            "--repeat" => repeat = val()?.parse().map_err(|_| "bad --repeat")?,
            _ => return Err("unknown argument".into()),
        }
    }
    if !(0.0..=1.0).contains(&p) || repeat == 0 {
        return Err("--p must be in [0, 1] and --repeat at least 1".into());
    }
    let words = words.ok_or("--words is required")?;
    if recall == rows.is_some() {
        return Err("give exactly one of --rows and --recall".into());
    }
    if (digits || lm.is_some()) && recall {
        return Err("--digits is for the typing mode".into());
    }
    if lm.is_some() && !digits {
        return Err("--lm goes with --digits".into());
    }
    let lex = load_lexicon(&root.join("data/lexicon")).map_err(|_| "cannot load the lexicon")?;
    if recall {
        return run_recall(&lex, &words, switches);
    }

    let rows_bytes = read(&rows.unwrap())?;
    let rows_sha = sha256_hex(&rows_bytes);
    let rows = usable(&lex, parse_rows(&String::from_utf8(rows_bytes).map_err(|_| "rows are not UTF-8")?).map_err(|e| e.to_string())?);
    let mut parsed = Vec::new();
    let mut skipped = 0usize;
    for r in &rows {
        let syls = row_syllables(&lex, r).map_err(|e| e.to_string())?;
        let one: Option<Vec<_>> = syls
            .iter()
            .map(|s| parse_syllable(s).filter(|_| !lex.entries(std::slice::from_ref(s)).is_empty()))
            .collect();
        match one {
            Some(v) if !v.is_empty() => parsed.push(v),
            _ => skipped += 1,
        }
    }
    if skipped > 0 {
        eprintln!("skipped {skipped} rows");
    }
    let syllables: usize = parsed.iter().map(Vec::len).sum();
    if syllables == 0 {
        return Err("no row to simulate".into());
    }
    let (mut keys, mut typos) = (0u64, 0u64);
    let mut per_1000: [Vec<f64>; 5] = Default::default(); // a, b, c, split, english_final
    let (mut final_1000, mut shown_1000, mut extra_1000) = (Vec::new(), Vec::new(), Vec::new());
    let lm = lm.unwrap_or_else(|| root.join("data/lm/bigram.sjlm"));
    for i in 0..repeat {
        let engine = if digits { engine_digits(root, &lm, &words, switches)? } else { engine_with(&lex, &words, switches)? };
        let mut sim = Sim::new(engine, p, repair, seed.wrapping_add(i));
        sim.digits = digits;
        for row in &parsed {
            sim.engine.reset(ResetMode::Discard);
            sim.last_preedit.clear();
            for (syms, tone) in row {
                sim.syllable(syms, *tone)?;
            }
            sim.raw(Key::new(KeyKind::Enter))?;
            sim.english_final += sim.last_commit.chars().any(|c| c.is_ascii_alphabetic()) as u64;
        }
        let per = |n: u64| n as f64 * 1000.0 / syllables as f64;
        for (v, n) in per_1000.iter_mut().zip([sim.reverts[0], sim.reverts[1], sim.reverts[2], sim.splits, sim.english_final]) {
            v.push(per(n));
        }
        final_1000.push(per(sim.final_digits));
        shown_1000.push(per(sim.shown_keys));
        extra_1000.push(per(sim.extra_bs));
        keys += sim.keys;
        typos += sim.typos;
    }
    let mut cols = String::new();
    for v in &per_1000 {
        let (mean, max) = mean_max(v);
        cols += &format!("\t{mean:.3}\t{max:.3}");
    }
    if digits {
        let ((fmean, fmax), (smean, smax), (emean, emax)) = (mean_max(&final_1000), mean_max(&shown_1000), mean_max(&extra_1000));
        cols += &format!("\t{fmean:.3}\t{fmax:.3}\t{smean:.3}\t{smax:.3}\t{emean:.3}\t{emax:.3}");
    }
    println!("{rows_sha}\t{}\t{syllables}\t{keys}\t{typos}{cols}", parsed.len());
    Ok(())
}

/// Types `word` letter by letter and Space on a fresh engine; the first revert the engine reported, and what the composition is then.
fn type_word(e: &mut Engine, word: &str) -> Result<(Option<Revert>, Vec<String>, String), String> {
    e.reset(ResetMode::Discard);
    let mut first = None;
    let mut last = None;
    for k in word.chars().map(|c| Key::ch(c, 0)).chain([Key::new(KeyKind::Space)]) {
        last = Some(e.key(k).map_err(|_| "the engine failed on a key")?);
        first = first.or(e.english_reverted());
    }
    Ok((first, e.syllables().to_vec(), last.map(|o| o.preedit).unwrap_or_default()))
}

/// Section 4.1, recall: every word of the list typed letter by letter, then Space, and the probes.
fn run_recall(lex: &Arc<Lexicon>, words: &Path, switches: Switches) -> Result<(), String> {
    let bytes = read(words)?;
    let sha = sha256_hex(&bytes);
    let list: Vec<String> = String::from_utf8(bytes).map_err(|_| "the list is not UTF-8")?.lines().map(String::from).collect();
    let mut e = engine_with(lex, words, switches)?;
    let (mut opened, mut classes) = (0usize, [0usize; 4]);
    for w in &list {
        let (first, syls, preedit) = type_word(&mut e, w)?;
        opened += (syls == [format!("{}{w}", core::learn::PUNCT_PREFIX)] && preedit == *w) as usize;
        classes[first.map_or(3, |k| k as usize)] += 1;
    }
    println!("{sha}\t{}\t{opened}\t{}\t{}\t{}\t{}", list.len(), classes[0], classes[1], classes[2], classes[3]);
    for w in PROBES {
        let (_, syls, _) = type_word(&mut e, w)?;
        let token = format!("{}{w}", core::learn::PUNCT_PREFIX);
        let what = if syls == [token] {
            "token"
        } else if !syls.is_empty() && syls.iter().all(|s| !s.starts_with(core::learn::PUNCT_PREFIX)) {
            "中文"
        } else {
            "其他"
        };
        println!("probe\t{w}\t{what}");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use core::engine::MOD_SHIFT;

    const ALL_ON: Switches = Switches { overwrite: true, key_order: true };

    /// Zhen-revert section 4.1, simulator: a row with mistakes that revert ends with the same text as the same row typed without a
    /// mistake (a text is checked here, in the module, because the tool's output never carries content, R2). With the overwrite repair a
    /// wrong key `q` for `w` (the same column) is written over by `w`, which is a revert (a) the simulator undoes.
    #[test]
    fn revert_recovery_leaves_the_row_as_if_typed_clean() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let lex = load_lexicon(&root.join("data/lexicon")).unwrap();
        let words = root.join("eval/golden/english-tiny.txt");
        let (syms, tone) = parse_syllable("ㄊㄧㄠ").unwrap();
        let mk = |p: f64| Sim::new(engine_with(&lex, &words, ALL_ON).unwrap(), p, Repair::Overwrite, 20261014);
        let row = |sim: &mut Sim| {
            sim.engine.reset(ResetMode::Discard);
            sim.syllable(&syms, tone).unwrap();
            sim.engine.key(Key::new(KeyKind::Enter)).unwrap().commit
        };
        let clean = row(&mut mk(0.0));
        assert!(!clean.is_empty() && clean.chars().count() == 1);
        let mut sim = mk(0.5);
        for i in 0..400 {
            assert_eq!(row(&mut sim), clean, "row {i}");
        }
        assert!(sim.reverts[Revert::Overwrite as usize] >= 1 && sim.typos >= 1, "premise: the rows revert");
        assert_eq!(sim.splits, 0);
        // Without the switch the same rows never revert (the key replaces), and come out the same.
        let mut off = Sim::new(engine_with(&lex, &words, Switches { overwrite: false, key_order: false }).unwrap(), 0.5, Repair::Overwrite, 20261014);
        for i in 0..400 {
            assert_eq!(row(&mut off), clean, "off, row {i}");
        }
        assert_eq!(off.reverts, [0; 3]);
    }

    /// The Backspace repair under reverts: a final that lands in the occupied medial column is an overwrite (a): the simulator undoes the
    /// revert and types the syllable again, so every row still ends as if typed clean.
    #[test]
    fn backspace_repair_leaves_the_row_as_if_typed_clean() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let lex = load_lexicon(&root.join("data/lexicon")).unwrap();
        let words = root.join("eval/golden/english-tiny.txt");
        let mut sim = Sim::new(engine_with(&lex, &words, ALL_ON).unwrap(), 0.0, Repair::Backspace, 20261014);
        // Medial then final (`ㄍㄨㄛ`): the final's neighbour on the keyboard row is a medial, so a slip lands in the occupied medial column.
        for syl in ["ㄍㄨㄛ", "ㄏㄨㄛ", "ㄌㄨㄛ", "ㄕㄨㄛ"] {
            let (syms, tone) = parse_syllable(syl).unwrap();
            let mut row = |sim: &mut Sim| {
                sim.engine.reset(ResetMode::Discard);
                sim.syllable(&syms, tone).unwrap();
                sim.engine.key(Key::new(KeyKind::Enter)).unwrap().commit
            };
            sim.p = 0.0;
            let clean = row(&mut sim);
            assert!(!clean.is_empty());
            sim.p = 0.5;
            for i in 0..300 {
                assert_eq!(row(&mut sim), clean, "{syl} row {i}");
            }
        }
        assert!(sim.reverts.iter().sum::<u64>() > 0, "premise: some slips reverted");
    }

    /// The counting itself: a revert is counted by the kind the engine reports, once per key, and nothing is counted for the keys that
    /// follow. `l` then `s` is out of order (b); `o` then `k` is the same column (a); `c u p` Space is a syllable the lexicon lacks (c).
    #[test]
    fn reverts_are_counted_by_kind() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let lex = load_lexicon(&root.join("data/lexicon")).unwrap();
        let words = root.join("eval/golden/english-tiny.txt");
        let mut sim = Sim::new(engine_with(&lex, &words, ALL_ON).unwrap(), 0.0, Repair::Overwrite, 1);
        for (keys, kind) in [("l s", Revert::Order), ("o k", Revert::Overwrite), ("c u p _", Revert::Missing)] {
            sim.engine.reset(ResetMode::Discard);
            sim.reverts = [0; 3];
            for t in keys.split(' ') {
                sim.raw(if t == "_" { Key::new(KeyKind::Space) } else { Key::ch(t.chars().next().unwrap(), 0) }).unwrap();
            }
            let mut want = [0; 3];
            want[kind as usize] = 1;
            assert_eq!(sim.reverts, want, "{keys:?}");
        }
        // Space made the token at once: the undo is Backspace and Esc, and the composition is empty again.
        let mut sim = Sim::new(engine_with(&lex, &words, ALL_ON).unwrap(), 0.0, Repair::Overwrite, 1);
        sim.want = vec![];
        for c in "cup".chars() {
            sim.raw(Key::ch(c, 0)).unwrap();
        }
        let kind = sim.raw(Key::new(KeyKind::Space)).unwrap().unwrap();
        sim.recover(kind, true).unwrap();
        assert!(sim.engine.syllables().is_empty() && sim.last_preedit.is_empty());
    }

    /// Digits.md A.10, the Backspace repair when a slip makes the last unit: the slip after the ㄅ turns the `1` into a digit token, which is
    /// the last unit, so the engine sends the whole composition and the preedit never again starts with `base_preedit`. The repair stops
    /// at the empty composition and types the syllable again (it used to press Backspace for ever).
    #[test]
    fn backspace_repair_ends_when_the_last_unit_sent_the_composition() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let mut sim = Sim::new(
            engine_digits(root, &root.join("data/lm/bigram.sjlm"), &root.join("eval/golden/english-tiny.txt"), ALL_ON).unwrap(),
            1.0,
            Repair::Backspace,
            20261014,
        );
        sim.digits = true;
        let (syms, tone) = parse_syllable("ㄅㄨˋ").unwrap();
        for _ in 0..20 {
            sim.engine.reset(ResetMode::Discard);
            sim.last_preedit.clear();
            for _ in 0..MAX_SYLLABLES - 1 {
                sim.raw(Key::ch(',', MOD_SHIFT)).unwrap();
            }
            sim.syllable(&syms, tone).unwrap();
        }
        assert!(sim.final_digits > 0, "premise: a slip made a digit the last unit, and it was sent");
    }
}
