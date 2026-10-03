# L: compact lexicon layout

Pure refactor of `core/src/lib.rs`: every output is byte-identical to the S0/S1 behavior. Scores stay `f64`. Std only, no `unsafe`, no mmap; data is parsed from the text files at run time.

## Layout

| Field | Type | Meaning |
|---|---|---|
| `syl_ids` / `syl_names` | `HashMap<String,u32>` / `Vec<String>` | syllable interning (about 1.5k distinct) |
| `key_pool` | `Vec<u32>` | syllable ids of all distinct readings, back to back |
| `readings` | `Vec<Reading{key_off,key_len,start}>` | distinct readings, sorted by id sequence; 12 B each; entries of reading r are `ents[start .. next.start]` |
| `ents` | `Vec<Ent{off,len,score}>` | 16 B each, grouped by reading, score-descending inside a group (stable) |
| `words` | `String` | one UTF-8 pool; `(off,len)` slices it |
| `by_word` | `Vec<u32>` | entry position of each distinct word's winner, sorted by word bytes; 4 B each |

Lookups use binary search: reading by id slice (`&[String]` is mapped to ids once per decode, unknown syllable = `u32::MAX`), word by bytes. A winner's reading is found by `partition_point` on `readings[].start`.

## Parse (no old maps are ever built)

1. Base and overlay rows append to a file-order `Raw` array (24 B each), a key pool and the word pool.
2. Per-word winner: stable sort by word, scan each run, replace only on strictly greater score (first wins ties).
3. Stable sort by reading key keeps file order inside a group; the overlay duplicate check runs per group on that order (an overlay row equal to any earlier row of the reading, base or overlay; the earliest offender in file order is reported); then one stable `sort_by` on score per group with the original comparator.
4. A bad overlay row stops overlay parsing but is reported only if no earlier duplicate exists, which keeps the old first-error order. Then `EmptyLexicon`.

## Memory accounting (505k entries)

Final: ents 8 MB + readings/keys a few MB + word pool about 5 MB + by_word 2 MB. Parse transient: `Raw` 12 MB + key pool + index vectors. Measured peak RSS of `shanjie-eval --set dev --limit 302`: 86,933,504 bytes with overlay, 37,371,904 bytes with `--no-overlay` (old: about 375 MiB / 127 MiB). The input text files held by the caller are part of that peak.

## Public surface

Fields are private except `max_len`. Accessors: `reading_count()`, `entries(&[String]) -> Vec<(&str,f64)>`, `word_info(&str) -> Option<(Syls,f64)>`.

## Guard tests (assertions unchanged)

- `learner_word_score_takes_last_duplicate`
- `overlay_duplicate_within_overlay_is_error`
- `overlay_bad_rows_report_length_only`

References compared byte for byte by `cli/tests/golden.rs`: `unigram.txt`, `s1-dev302.txt`, `s1-dev302-nooverlay.txt`, `s1-overlay-sets.txt`.
