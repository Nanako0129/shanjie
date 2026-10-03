//! Port of reference/proto/eval.py: rows, metrics, learning simulation, Python-compatible formatting.

use crate::*;

pub struct Row {
    pub ctx: String,
    pub sent: String,
    /// Confirmed reading (third column), used instead of to_syllables when present.
    pub reading: Option<Syls>,
}

/// `前文|句子[|讀音]` lines; blank and `#` lines skipped.
pub fn parse_rows(text: &str) -> Result<Vec<Row>, Error> {
    let mut rows = Vec::new();
    for line in text.lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (ctx, rest) = line
            .split_once('|')
            .ok_or(Error::MissingSeparator { line_len: line.chars().count() })?;
        let (sent, reading) = match rest.split_once('|') {
            Some((s, r)) => (s, Some(r.split_whitespace().map(String::from).collect())),
            None => (rest, None),
        };
        rows.push(Row { ctx: ctx.into(), sent: sent.into(), reading });
    }
    Ok(rows)
}

/// moedict: whitespace-separated sentences, empty context.
pub fn parse_moedict(text: &str) -> Vec<Row> {
    text.split_whitespace()
        .map(|s| Row { ctx: String::new(), sent: s.into(), reading: None })
        .collect()
}

/// Rows whose reading is unavailable are dropped (confirmed-reading rows always stay).
pub fn usable(lex: &Lexicon, rows: Vec<Row>) -> Vec<Row> {
    rows.into_iter().filter(|r| r.reading.is_some() || lex.to_syllables(&r.sent).is_some()).collect()
}

/// The S0 char map. Lenient comparison applies it first, then the MOE variant table (`Lenient`).
pub fn char_map(s: &str) -> String {
    const FROM: &str = "她妳它牠嘗周臺裏";
    const TO: &str = "他你他他嚐週台裡";
    s.chars()
        .map(|c| FROM.chars().position(|f| f == c).map_or(c, |p| TO.chars().nth(p).unwrap()))
        .collect()
}

/// Lenient comparison (docs/PLAN.md S2v): `char_map`, then greedy left-to-right longest match over
/// eval/variants.tsv, replacing each variant with its canonical form. The table is already normalized
/// by tools/build_variants.py; loading does no conversion and rejects bad or duplicate lines.
pub struct Lenient {
    table: HashMap<String, String>,
    longest: usize,
}

impl Lenient {
    pub fn parse(text: &str) -> Result<Lenient, Error> {
        let mut table = HashMap::new();
        for (i, line) in text.lines().enumerate() {
            if line.starts_with('#') {
                continue;
            }
            let bad = Error::BadVariants { line: i + 1 };
            let mut it = line.split('\t');
            let (Some(v), Some(c), None) = (it.next(), it.next(), it.next()) else { return Err(bad) };
            if v.is_empty() || c.is_empty() || table.insert(v.to_string(), c.to_string()).is_some() {
                return Err(bad);
            }
        }
        let longest = table.keys().map(|k| k.chars().count()).max().unwrap_or(0);
        Ok(Lenient { table, longest })
    }

    pub fn apply(&self, s: &str) -> String {
        let cs: Vec<char> = char_map(s).chars().collect();
        let (mut out, mut i) = (String::new(), 0);
        'outer: while i < cs.len() {
            for n in (1..=self.longest.min(cs.len() - i)).rev() {
                let key: String = cs[i..i + n].iter().collect();
                if let Some(c) = self.table.get(&key) {
                    out.push_str(c);
                    i += n;
                    continue 'outer;
                }
            }
            out.push(cs[i]);
            i += 1;
        }
        out
    }
}

/// Python round(x, k) then repr(): exact decimal rounding, shortest round-trip, `1.0` not `1`.
pub fn pyround(x: f64, k: usize) -> String {
    let r: f64 = format!("{x:.k$}").parse().unwrap();
    let s = format!("{r}");
    if s.contains('.') { s } else { format!("{s}.0") }
}

pub struct Metrics {
    pub n: usize,
    pub sent_acc: f64,
    pub lenient_acc: f64,
    pub char_acc: f64,
    pub oracle: f64,
}

impl Metrics {
    /// repr() of the Python dict.
    pub fn repr(&self) -> String {
        format!(
            "{{'n': {}, 'sent_acc': {}, 'lenient_acc': {}, 'char_acc': {}, 'oracle@{BEAM}': {}}}",
            self.n,
            pyround(self.sent_acc, 3),
            pyround(self.lenient_acc, 3),
            pyround(self.char_acc, 4),
            pyround(self.oracle, 3)
        )
    }
}

pub fn row_syllables(lex: &Lexicon, r: &Row) -> Result<Syls, Error> {
    match &r.reading {
        Some(s) => Ok(s.clone()),
        None => lex.to_syllables(&r.sent).ok_or(Error::NoPath { len: r.sent.chars().count() }),
    }
}

/// Returns metrics and misses (truth, output). `on_decode` receives each decode duration.
pub fn evaluate(
    lex: &Lexicon,
    len: &Lenient,
    rows: &[Row],
    mut on_decode: impl FnMut(std::time::Duration),
) -> Result<(Metrics, Vec<(String, String)>), Error> {
    let (mut sent_ok, mut char_ok, mut chars, mut oracle, mut len_ok) = (0usize, 0usize, 0usize, 0usize, 0usize);
    let mut misses = Vec::new();
    for r in rows {
        let syls = row_syllables(lex, r)?;
        let t = std::time::Instant::now();
        let nbest = decode(lex, &syls, &mut NoLearning)?;
        on_decode(t.elapsed());
        let texts: Vec<String> = nbest.iter().map(|(_, w)| w.concat()).collect();
        oracle += texts.contains(&r.sent) as usize;
        let out = &texts[0];
        sent_ok += (out == &r.sent) as usize;
        len_ok += (len.apply(out) == len.apply(&r.sent)) as usize;
        char_ok += out.chars().zip(r.sent.chars()).filter(|(a, b)| a == b).count();
        chars += r.sent.chars().count();
        if out != &r.sent {
            misses.push((r.sent.clone(), out.clone()));
        }
    }
    let n = rows.len();
    let f = |a: usize, b: usize| a as f64 / b as f64;
    Ok((
        Metrics { n, sent_acc: f(sent_ok, n), lenient_acc: f(len_ok, n), char_acc: f(char_ok, chars), oracle: f(oracle, n) },
        misses,
    ))
}

/// S1 path (beam 64): the `extra` line payload and the 1-based rows missed at @64.
pub fn s1_extra(lex: &Lexicon, rows: &[Row], oov_words: &[String]) -> Result<(String, String, Vec<usize>), Error> {
    let (mut o16, mut o64, mut oov_n, mut oov_ok, mut oov_o64) = (0usize, 0usize, 0usize, 0usize, 0usize);
    let mut misses = Vec::new();
    for (i, r) in rows.iter().enumerate() {
        let nbest = decode_beam(lex, &row_syllables(lex, r)?, &mut NoLearning, BEAM_S1)?;
        let texts: Vec<String> = nbest.iter().map(|(_, w)| w.concat()).collect();
        let (h16, h64) = (texts.iter().take(16).any(|t| *t == r.sent), texts.contains(&r.sent));
        o16 += h16 as usize;
        o64 += h64 as usize;
        if !h64 {
            misses.push(i + 1);
        }
        if oov_words.iter().any(|w| r.sent.contains(w.as_str())) {
            oov_n += 1;
            oov_ok += (texts[0] == r.sent) as usize;
            oov_o64 += h64 as usize;
        }
    }
    let f = |a: usize, b: usize| if b == 0 { 0.0 } else { a as f64 / b as f64 };
    let n = rows.len();
    Ok((
        format!(
            "{{'oracle@16': {}, 'oracle@64': {}, 'oov_n': {oov_n}, 'oov_sent_acc': {}, 'oov_oracle@64': {}}}",
            pyround(f(o16, n), 3),
            pyround(f(o64, n), 3),
            pyround(f(oov_ok, oov_n), 3),
            pyround(f(oov_o64, oov_n), 3)
        ),
        format!("   counts: oracle@16 {o16}/{n}, oracle@64 {o64}/{n}, oov_sent_acc {oov_ok}/{oov_n}, oov_oracle@64 {oov_o64}/{oov_n}"),
        misses,
    ))
}

/// `## 讀音檢查` payload: (rows with a confirmed reading, rows where to_syllables disagrees).
pub fn check_readings(lex: &Lexicon, rows: &[Row]) -> (usize, usize) {
    let with: Vec<&Row> = rows.iter().filter(|r| r.reading.is_some()).collect();
    let bad = with.iter().filter(|r| lex.to_syllables(&r.sent) != r.reading).count();
    (with.len(), bad)
}

const LEARN_CASES: [(&str, &[&str], &[&str]); 3] = [
    ("蛋糕你嚐嚐看", &["輸入法常常選錯字", "他常常遲到", "我們常常見面"], &["蛋糕你嚐嚐", "湯你嚐嚐"]),
    ("下週是期中考", &["其中一個沒來", "我們在其中找到答案"], &["期中報告明天交", "下週是期中考"]),
    ("大家有異議嗎", &["這件事很有意義", "人生的意義"], &["你有異議嗎", "大家有異議嗎"]),
];

fn pad_right(s: &str, w: usize) -> String {
    format!("{s}{}", " ".repeat(w.saturating_sub(s.chars().count())))
}
fn pad_left(s: &str, w: usize) -> String {
    format!("{}{s}", " ".repeat(w.saturating_sub(s.chars().count())))
}

fn top_text(lex: &Lexicon, s: &str, l: &mut dyn Learner) -> Result<Vec<String>, Error> {
    let syls = lex.to_syllables(s).ok_or(Error::NoPath { len: s.chars().count() })?;
    Ok(decode(lex, &syls, l)?.swap_remove(0).1)
}

pub fn simulate(lex: &Lexicon) -> Result<String, Error> {
    let mut o = String::from("\n## 學習模擬：在情境 A 改選冷門詞一次（或三次）後，其他句子的正確率\n");
    o += &format!("{}{}{}{}\n", pad_right("策略", 14), pad_left("改選次數", 6), pad_left("常用詞句", 10), pad_left("冷門詞句", 10));
    for times in [1, 3] {
        for name in ["NoLearning", "GlobalBoost", "ContextKeyed", "Promotion"] {
            let (mut good, mut bad, mut good_n, mut bad_n) = (0, 0, 0, 0);
            for (teach, commons, rares) in LEARN_CASES {
                let mut l: Box<dyn Learner + '_> = match name {
                    "NoLearning" => Box::new(NoLearning),
                    "GlobalBoost" => Box::new(GlobalBoost::new(lex)),
                    "ContextKeyed" => Box::new(ContextKeyed::new(lex)),
                    _ => Box::new(Promotion::new(lex)),
                };
                let words = lex.segment_words(teach).ok_or(Error::NoPath { len: teach.chars().count() })?;
                for _ in 0..times {
                    let out_words = top_text(lex, teach, l.as_mut())?;
                    for (i, (w, syls)) in words.iter().enumerate() {
                        if !out_words.contains(w) {
                            let prev = if i > 0 { words[i - 1].0.as_str() } else { "<s>" };
                            l.observe(prev, syls, w);
                        }
                    }
                }
                for s in commons {
                    good += (top_text(lex, s, l.as_mut())?.concat() == *s) as usize;
                    good_n += 1;
                }
                for s in rares {
                    bad += (top_text(lex, s, l.as_mut())?.concat() == *s) as usize;
                    bad_n += 1;
                }
            }
            o += &format!(
                "{}{}{}{}\n",
                pad_right(name, 14),
                pad_left(&times.to_string(), 6),
                format!("{}/{}", pad_left(&good.to_string(), 7), pad_right(&good_n.to_string(), 4)),
                format!("{}/{}", pad_left(&bad.to_string(), 7), pad_right(&bad_n.to_string(), 4)),
            );
        }
    }
    Ok(o)
}
