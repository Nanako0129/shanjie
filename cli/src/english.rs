//! `english-sim` (docs/contracts/zhen-mixed.md section 4): how often the English window opens by mistake while typing Chinese,
//! and how many words of a list open it at all. It drives the real engine (`Engine::load_english`, `set_english(true)`) and
//! reads `Engine::english_open()` after every Space: the trigger rule is the engine's, never repeated here.
//!
//!   english-sim --words FILE --rows FILE [--p 0.02] [--repair overwrite|backspace] [--seed 20261014] [--repeat 5] [--inline]
//!   english-sim --words FILE --recall
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
use core::engine::{load_lexicon, Engine, Key, KeyKind, Layout, ResetMode};
use core::eval::{parse_rows, row_syllables, sha256_hex, usable};
use core::Lexicon;
use std::path::{Path, PathBuf};
use std::sync::Arc;

const ROWS: [&str; 4] = ["1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"];
const TONE_MARKS: [char; 4] = ['ˊ', 'ˇ', 'ˋ', '˙'];
const LAYOUT: Layout = Layout::Standard;

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
    triggers: u64,
    /// `--inline`: ask the engine for rule D after every key; `d_held` is whether it held after any key of this syllable.
    inline: bool,
    d_held: bool,
    /// Auto-keeps the engine reported (counted whether or not `--inline` prints them), and the symbols of the syllable being typed
    /// up to the key in progress: what a recovery types again.
    autokeeps: u64,
    want: Vec<char>,
    p: f64,
    repair: Repair,
    rng: Rng,
}

impl Sim {
    /// One key to the engine, counted. Whether the key made an auto-keep (counted too): asked of the engine after every key,
    /// before the symbols are compared, and never worked out here.
    fn raw(&mut self, k: Key) -> Result<bool, String> {
        self.keys += 1;
        self.engine.key(k).map_err(|_| "the engine failed on a key".to_string())?;
        // The rules are the engine's, never repeated here.
        self.d_held |= self.inline && self.engine.english_inline_shown();
        let kept = self.engine.english_autokept();
        self.autokeeps += kept as u64;
        Ok(kept)
    }

    /// One key of the typing. `Ok(true)` when it made an auto-keep and the recovery has run: the syllable then stands as typed
    /// without a mistake up to `self.want`, and the caller does not send what is left of this symbol.
    fn send(&mut self, k: Key) -> Result<bool, String> {
        if !self.raw(k)? {
            return Ok(false);
        }
        for _ in 0..3 {
            self.raw(Key::new(KeyKind::Esc))?;
            self.raw(Key::new(KeyKind::Backspace))?;
            // Backspace behind an English token now takes one letter and opens a run with the rest (zhen-inline section 1.3g;
            // an auto-kept word has 3 letters at least, so the run is not empty): Esc takes the run, and the token is gone.
            self.raw(Key::new(KeyKind::Esc))?;
            let mut kept = false;
            for sym in self.want.clone() {
                if self.raw(Key::ch(LAYOUT.key_of_symbol(sym).unwrap(), 0))? {
                    kept = true;
                    break;
                }
            }
            if !kept {
                return Ok(true);
            }
        }
        Err("an auto-keep kept coming back on a typing without mistakes".into())
    }

    fn zhuyin(&mut self, sym: char) -> Result<bool, String> {
        self.send(Key::ch(LAYOUT.key_of_symbol(sym).unwrap(), 0))
    }

    /// The symbols of the unfinished syllable in display order, read from the engine, so the simulator does not work them out itself.
    fn have(&self) -> Vec<char> {
        self.engine.english_columns().iter().flatten().copied().collect()
    }

    /// One syllable typed; whether rule D held after any of its keys is left in `self.d_held` (the caller counts and clears it).
    fn syllable(&mut self, syms: &[(usize, char)], tone: Option<char>) -> Result<(), String> {
        for (i, &(col, sym)) in syms.iter().enumerate() {
            let key = LAYOUT.key_of_symbol(sym).unwrap();
            let near = neighbours(key);
            let want: Vec<char> = syms[..=i].iter().map(|s| s.1).collect();
            self.want = want.clone();
            // A labeled block: an auto-keep and its recovery leave the syllable typed up to `want` already, so the rest of this
            // symbol's keys are not sent.
            'symbol: {
                if self.rng.unit() < self.p && !near.is_empty() {
                    let wrong = near[(self.rng.next() % near.len() as u64) as usize];
                    let (wcol, _) = LAYOUT.symbol_of(wrong).unwrap();
                    if self.send(Key::ch(wrong, 0))? {
                        break 'symbol;
                    }
                    if self.repair == Repair::Overwrite && wcol == col {
                        self.zhuyin(sym)?;
                    } else {
                        // Backspace until the symbols are a prefix of the wanted ones. Which symbol it takes is the engine's: the last
                        // in display order (final, medial, initial) by rule 11. While letters show (rule D, asked of the engine) the
                        // Backspace instead turns the letters left into an English run and the unfinished syllable is gone
                        // (zhen-inline section 1.3g): Esc takes the run, and the symbols are typed again from the start below. Each
                        // key is a step that shortens the symbols or empties them, so the loop ends with them empty at the latest.
                        while !want.starts_with(&self.have()) {
                            let letters = self.engine.english_inline_shown();
                            self.send(Key::new(KeyKind::Backspace))?;
                            if letters {
                                self.raw(Key::new(KeyKind::Esc))?;
                            }
                        }
                        for &(_, s) in &syms[self.have().len()..=i] {
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
            if self.have() != want {
                return Err("the engine's symbols differ from the typing model's after a key".into());
            }
        }
        match tone {
            Some(mark) => self.send(Key::ch(LAYOUT.key_of_tone(mark).unwrap(), 0)).map(|_| ()),
            None => {
                self.send(Key::new(KeyKind::Space))?;
                if self.engine.english_open() {
                    self.triggers += 1;
                    self.send(Key::new(KeyKind::Esc))?;
                    self.send(Key::new(KeyKind::Space))?;
                }
                Ok(())
            }
        }
    }
}

fn engine_with(lex: &Arc<Lexicon>, words: &Path) -> Result<Engine, String> {
    let mut e = Engine::with_lexicon(lex.clone(), LAYOUT);
    e.load_english(words).map_err(|_| "cannot load the word list".to_string())?;
    e.set_english(true).map_err(|_| "cannot switch the English window on".to_string())?;
    Ok(e)
}

/// (mean, max) of per-seed values.
fn mean_max(v: &[f64]) -> (f64, f64) {
    (v.iter().sum::<f64>() / v.len() as f64, v.iter().cloned().fold(0.0, f64::max))
}

fn read(path: &Path) -> Result<Vec<u8>, String> {
    std::fs::read(path).map_err(|e| format!("cannot read file ({:?})", e.kind()))
}

pub fn run(args: &[String], root: &Path) -> Result<(), String> {
    let (mut words, mut rows, mut recall, mut inline) = (None::<PathBuf>, None::<PathBuf>, false, false);
    let (mut p, mut repair, mut seed, mut repeat) = (0.02f64, Repair::Overwrite, 20261014u64, 5u64);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut val = || it.next().cloned().ok_or_else(|| "missing option value".to_string());
        match a.as_str() {
            "--words" => words = Some(val()?.into()),
            "--rows" => rows = Some(val()?.into()),
            "--recall" => recall = true,
            "--inline" => inline = true,
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
    if recall && inline {
        return Err("--inline is for the typing mode".into());
    }
    let lex = load_lexicon(&root.join("data/lexicon")).map_err(|_| "cannot load the lexicon")?;
    if recall {
        return run_recall(&lex, &words);
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
    let (mut per_1000, mut inline_1000, mut autokeep_1000, mut keys, mut triggers) = (Vec::new(), Vec::new(), Vec::new(), 0u64, 0u64);
    for i in 0..repeat {
        let mut sim = Sim { engine: engine_with(&lex, &words)?, keys: 0, triggers: 0, inline, d_held: false, autokeeps: 0, want: Vec::new(), p, repair, rng: Rng(seed.wrapping_add(i)) };
        let mut shown_syllables = 0u64;
        for row in &parsed {
            sim.engine.reset(ResetMode::Discard);
            for (syms, tone) in row {
                sim.d_held = false;
                sim.syllable(syms, *tone)?;
                shown_syllables += sim.d_held as u64;
            }
            sim.send(Key::new(KeyKind::Enter))?;
        }
        per_1000.push(sim.triggers as f64 * 1000.0 / syllables as f64);
        inline_1000.push(shown_syllables as f64 * 1000.0 / syllables as f64);
        autokeep_1000.push(sim.autokeeps as f64 * 1000.0 / syllables as f64);
        keys += sim.keys;
        triggers += sim.triggers;
    }
    let (mean, max) = mean_max(&per_1000);
    let extra = if inline {
        let ((imean, imax), (amean, amax)) = (mean_max(&inline_1000), mean_max(&autokeep_1000));
        format!("\t{imean:.3}\t{imax:.3}\t{amean:.3}\t{amax:.3}")
    } else {
        String::new()
    };
    println!("{rows_sha}\t{}\t{syllables}\t{keys}\t{triggers}\t{mean:.3}\t{max:.3}{extra}", parsed.len());
    Ok(())
}

/// Section 4.5: every word of the list typed letter by letter, then Space.
fn run_recall(lex: &Arc<Lexicon>, words: &Path) -> Result<(), String> {
    let bytes = read(words)?;
    let sha = sha256_hex(&bytes);
    let list: Vec<String> = String::from_utf8(bytes).map_err(|_| "the list is not UTF-8")?.lines().map(String::from).collect();
    let mut e = engine_with(lex, words)?;
    let (mut opened, mut top1, mut classes) = (0usize, 0usize, [0usize; 4]);
    for w in &list {
        e.reset(ResetMode::Discard);
        for c in w.chars() {
            e.key(Key::ch(c, 0)).map_err(|_| "the engine failed on a key")?;
        }
        let o = e.key(Key::new(KeyKind::Space)).map_err(|_| "the engine failed on a key")?;
        let open = e.english_open();
        opened += open as usize;
        top1 += (open && o.candidates.first() == Some(w)) as usize;
        // The recount: overwrites by column, and whether the leftover syllable (tone 1) is in the lexicon.
        let (mut col, mut over) = ([None::<char>; 3], 0usize);
        for c in w.chars() {
            let (i, sym) = LAYOUT.symbol_of(c).ok_or("a letter without a Zhuyin key")?;
            over += col[i].is_some() as usize;
            col[i] = Some(sym);
        }
        let leftover: String = col.iter().flatten().collect();
        let valid = !lex.entries(&[leftover]).is_empty();
        classes[match (over, valid) {
            (0, _) => 0,
            (1, true) => 1,
            (_, true) => 2,
            (_, false) => 3,
        }] += 1;
    }
    if opened != classes[2] + classes[3] {
        return Err("the engine and the recount disagree on which words open".into());
    }
    println!("{sha}\t{}\t{opened}\t{top1}\t{}\t{}\t{}\t{}", list.len(), classes[0], classes[1], classes[2], classes[3]);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Zhen-inline section 4.1, simulator: a row that triggers auto-keeps ends with the same text as the same row typed without a
    /// mistake (a text is checked here, in the module, because the tool's output never carries content, R2).
    #[test]
    fn autokeep_recovery_leaves_the_row_as_if_typed_clean() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let lex = load_lexicon(&root.join("data/lexicon")).unwrap();
        let dir = std::env::temp_dir().join(format!("shanjie-english-sim-unit-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("words.txt"), "ewuk\n").unwrap();
        let (syms, tone) = parse_syllable("ㄊㄧㄠ").unwrap();
        let mk = |p: f64| Sim {
            engine: engine_with(&lex, &dir.join("words.txt")).unwrap(),
            keys: 0,
            triggers: 0,
            inline: true,
            d_held: false,
            autokeeps: 0,
            want: Vec::new(),
            p,
            repair: Repair::Overwrite,
            rng: Rng(20261014),
        };
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
        assert!(sim.autokeeps >= 1, "premise: the rows keep a word");
        // The same with the Backspace repair: while letters show (rule D) the engine's Backspace opens an English run (section 1.3g),
        // which the repair must take with Esc and then type the symbols again; the row ends as if typed clean all the same.
        let mut sim = Sim { repair: Repair::Backspace, ..mk(0.5) };
        for i in 0..400 {
            assert_eq!(row(&mut sim), clean, "backspace repair, row {i}");
        }
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
