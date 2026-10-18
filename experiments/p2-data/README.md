# p2-data: public candidate-sentence pool for Phase 2

Experiment branch `exp/p2-data` (from main 16050e54), never merged. Builds the pool of chat-like public sentences that
`docs/iteration-plan.md` section 5 later ranks with a Moore-Lewis register score (computed elsewhere, with private data).
Code only is tracked here. Every download, the pool and the manifest live outside the repo in `~/.cache/shanjie/p2-public/`
(stdlib only; no corpus text is committed).

| file | what it does |
|---|---|
| `fetch.py` | downloads the four sources into `~/.cache/shanjie/p2-public/raw/`; sequential, 1 s pause, retries with backoff, resumable; logs URL and SHA-256 of every file to `raw/fetched.jsonl` |
| `clean.py` | raw files -> `pool.tsv` (`source<TAB>source_id<TAB>sentence`), `flags.tsv`, `attribution.tsv`, `manifest.json` |
| `test_p2.py` | 23 unit tests, synthetic strings only |

## Commands

```
python3 -I experiments/p2-data/fetch.py yodas
python3 -I experiments/p2-data/fetch.py tatoeba
python3 -I experiments/p2-data/fetch.py massive
python3 -I experiments/p2-data/fetch.py wildchat --budget 400   # repeat until it prints "wildchat done"
python3 -I experiments/p2-data/clean.py                         # [--contain-min N] [--out DIR]
python3 -m unittest discover -s experiments/p2-data
```

Outputs in `~/.cache/shanjie/p2-public/`: `pool.tsv`, `flags.tsv` (`source, source_id, TWPhrases keys found`), `attribution.tsv`
(Tatoeba sentence id -> author; YODAS id -> YouTube watch URL), `manifest.json` (fetched URLs and hashes, raw counts, counts after every step,
guard files with SHA-256, OpenCC file hashes).

## Sources and how each was read

| source | licence | how |
|---|---|---|
| WildChat-1M, Taiwan | ODC-BY | datasets-server `/filter` with `where="country"='Taiwan'` (`country` is a top-level column; the nested `conversation[].country` cannot be filtered; the column name needs double quotes). Only `role == user` turns are stored. `source_id` = `conversation_hash:turn_index`. |
| YODAS zh000 manual | CC BY 3.0 | the 33 `data/zh000/text/*.txt` files (60 MB, no audio) from the Hub tree API. Line = `<11-char video id>-<seg>-<start>-<end> <text>` (units of 10 ms). `source_id` = `video_id:first_segment`. The text files carry no channel; only the video id is kept (watch URL in `attribution.tsv`). |
| Tatoeba cmn | CC BY 2.0 FR | per-language exports `cmn_sentences_detailed` and `cmn_transcriptions` (same data as the global files, 1/30 of the size). `source_id` = sentence id, authors in `attribution.tsv`. |
| MASSIVE zh-TW | CC BY 4.0 | `utt` of all three partitions, `source_id` = `partition-id`. |

## Decisions (judgement calls are marked J)

1. **J MASSIVE does not come from the datasets-server.** `/rows` answers "Cannot get the config names ... Dataset scripts are no longer
   supported" (the dataset is a `massive.py` script), and the parquet branch needs pyarrow. I took the file the script itself downloads,
   `amazon-massive-dataset-1.1.tar.gz` from the dataset's S3 bucket (the version HF serves for single locales), extracted only
   `zh-TW.jsonl` (16,520 lines; 16,521 sentences after splitting) and deleted the tarball (its hash is in `fetched.jsonl`).
2. **J WildChat is partial.** The filter index of the datasets-server answered "the dataset index is loading" for most requests for hours,
   so a page took 1 to 10 minutes. 83 of 105 pages are in (8,300 of 10,484 Taiwan conversations, 79%; 8,257 distinct hashes, the
   dataset itself repeats 43). The rest would add about 1,000 sentences (this part yielded 3,491 from 8,300 conversations), so I stopped.
   Resume: run the same `fetch.py wildchat` command, then `clean.py`.
3. WildChat-1M is the non-toxic release: the turn-level `toxic` flag is 0 for every Taiwan turn. 17 `redacted` turns (PII replaced by random
   names) are dropped. Taiwan user turns are mostly English or pasted text: 202,633 of 215,925 pieces fail the Han-length test.
4. **YODAS merging** (`yodas_units`): consecutive segments of one video are joined without a space (a space only between two ASCII
   alphanumerics) until: another video, a skipped segment number, a pause of at least 0.5 s between fragments (`YODAS_GAP` = 50; the
   80th percentile of the gaps measured on the first file), a fragment ending in sentence-final punctuation, or adding the next fragment
   would pass 40 Han characters. Captions have almost no punctuation, so most unit borders are pauses: a unit can start or end
   mid-sentence. 428,646 lines -> 173,989 units.
5. **Tatoeba native Traditional = the sentence has a Hans transcription.** Tatoeba stores one script and generates the other as a
   transcription. Measured on today's dump: 89,190 sentences, 39,758 with a Hans transcription (= native Traditional), 49,428 with a Hant
   one (native Simplified), 0 with both, 4 with neither. The survey's 39,761 of 89,290 is an older dump (100 fewer sentences now; the
   Traditional count differs by 3). The count is reproduced to within 0.01%.
6. **Splitting** is on `。！？!?` and line breaks only (not on commas, `；`, `…`, or ASCII `.`). Leading list markers (`- `, `* `, `#`, `1.`, `2)`,
   not `3.5`) and stray closing quotes are removed. Whitespace is collapsed and removed next to any CJK character.
7. **Filter order** (each step counted): split, 4-40 Han (`U+4E00-U+9FFF`), Han >= 70% of non-space characters (punctuation counts in the
   denominator), URL / bare domain / e-mail / `@handle`, digits (NFKC first: a run of 7 or more, or a phone-shaped string with 8 or more
   digits; a date such as `2026-10-18` stays), code-like (`{}<>\|` backtick `$^`, `==`, `!=`, `=>`, `->`, `::`, `()`, `a.b(`), Simplified-only, dedupe, guard.
   Because the Han ratio runs before the URL test, most URL sentences are already gone at the ratio step; the later steps catch the rest.
8. **J Simplified-only set = the project's reduced set**, `build_counts.TRAD_SIMP_ONLY` (3,795 characters, read from
   `STCharacters.txt`, hash-checked), not the full table (3,809). The full table maps 14 characters that are standard in Taiwan
   (`群 床 秘 峰 粽 么 庄 晒 洒 灶 痒 肴 虱 霉`, the S7f review finding of 2026-10-12), so it would delete 社群, 起床, 秘密. Sentences the
   full set would have dropped additionally: WildChat 94, YODAS 921, Tatoeba 285, MASSIVE 64 (`kept_but_full_simp_only_would_drop`).
   Known blind spot of a table lookup: half-converted text keeps characters that are valid in both scripts (`后 里 周 只 准 着`), e.g.
   `兩只手`, `準 -> 准`; these pass. YODAS in particular still has some.
9. **TWPhrases is a flag only** (`flags.tsv`; keys whose Taiwan form differs, longest match): 4,870 pool sentences.
10. **Dedupe** key = NFKC (full-width -> half-width), all whitespace removed, case folded; first occurrence wins in the order
    WildChat, YODAS, Tatoeba, MASSIVE (so a duplicate is attributed to the earlier source).
11. **Leakage guard** (`Guard`). Matching is on the counting layer used by `tools/holdout_guard.py`: Han runs, `build_counts.fold`, plus
    the `build_counts.convert` form of the entry. Two kinds of entry:
    - containment: a pool sentence is dropped when one of its Han runs contains an entry run whole. Entries are every Han run of every
      `|`-separated field (context and sentence) of row files. Holdout rows (`eval/holdout/**/*.txt`, all three shards) go in through
      `holdout_guard.build_matcher` (sentence field) and `holdout_guard.read_lines` (context field) with a minimum of 4 characters,
      exactly as holdout_guard does; only counts leave the process.
    - exact: the Han characters of the pool sentence joined equal an entry's. Used for every entry of 4 or more characters and, alone,
      for word lists, `.tsv` lines without `|`, and every string of the `eval/bench/**/*.json` files.
    - **J non-holdout row entries shorter than 8 Han (`CONTAIN_MIN`) are exact-only.** Containment from 4 characters made the lytune
      and tuning sets remove thousands of YODAS sentences that merely contain a greeting-sized eval sentence, i.e. the register wanted.
      Those are counted (`kept_but_contain_a_short_eval_sentence`, below) and `--contain-min 4` reproduces the strict behaviour.
    - Files read (counts and SHA-256 in the manifest): `eval/holdout/**/*.txt`; `eval/dev/**/*.txt|*.list`; `eval/bench/**` (txt, tsv, json);
      `eval/{rerank,probe,learn,sets}` and `eval/golden/manual-words-rows.txt`; the tuning sets `cvtune.txt`, `wikitune.txt` (and any
      `wikijudge*.txt`) wherever a copy sits under `~/.cache/shanjie` (`s7c2c-public/tune/`, `session/up3/`, `reading-aware/.../probe/`); `e2-public/*.txt`
      (`lytune.txt`, `wikitypo.txt`). There is no `wikijudge*` file on this machine. Byte-identical copies are read once (9 skipped). Derived
      files (`*.rowstats.tsv`, `*.dump`, `*.rs`, `bench/rows/*`) hold scores and candidate lists, not evaluation sentences, and are not read.

## Measured counts (copied from `manifest.json`; sentences kept after each step)

| step | WildChat | YODAS | Tatoeba | MASSIVE |
|---|---:|---:|---:|---:|
| raw units | 8,300 conversations, 13,860 user turns (17 redacted dropped) | 428,646 lines -> 173,989 units, 2,039 videos | 89,190 sentences -> 39,758 native Traditional | 16,521 utterances |
| split | 215,925 | 180,197 | 40,960 | 16,521 |
| 4-40 Han | 13,292 | 173,769 | 39,724 | 15,594 |
| Han ratio | 9,368 | 167,066 | 39,242 | 14,528 |
| URL / mail / handle | 9,368 | 167,064 | 39,242 | 14,528 |
| digits / phone | 9,368 | 167,044 | 39,242 | 14,528 |
| code-like | 9,315 | 167,005 | 39,241 | 14,528 |
| Simplified-only | 4,103 | 91,766 | 39,068 | 14,519 |
| dedupe | 3,499 | 90,188 | 38,950 | 13,495 |
| guard = **pool** | **3,491** | **90,080** | **36,839** | **13,485** |

Pool: **143,895 sentences**, 3,005,914 Han characters; 1,138 YODAS videos and 208 Tatoeba authors to credit. Flagged by TWPhrases: 4,870
(WildChat 151, YODAS 3,437, Tatoeba 512, MASSIVE 770). This is below the plan's about 300k: the four sources, after the Traditional test, give
about half.

Guard, sentences dropped (first matching set in the order holdout, dev, bench, eval-other, tune, e2):

| source | holdout | dev | bench | eval-other | tune | e2 |
|---|---:|---:|---:|---:|---:|---:|
| WildChat | 3 | 0 | 0 | 0 | 3 | 2 |
| YODAS | 85 | 0 | 1 | 0 | 9 | 13 |
| Tatoeba | 11 | 12 | 3 | 1 | 2,074 | 10 |
| MASSIVE | 0 | 0 | 1 | 0 | 8 | 1 |

Tatoeba against the cvtune tuning set: 1,112 of its 3,791 sentences are also Tatoeba sentences (checked by exact equality), which explains
the 2,074. Kept but containing a shorter (4-7 character) evaluation sentence, not dropped: YODAS 6,139 (e2 4,259, tune 1,740, bench 108, dev 30,
eval-other 2), Tatoeba 1,313, MASSIVE 315, WildChat 23 (sum of the labels; a sentence can count under several).

## Things to know before using the pool

- One WildChat turn (`2cfd695e8309fb3215fc454ede9ba942:0`) is a pasted subtitle file and gives 747 of the 3,491 WildChat sentences; no per-unit
  cap was applied. YODAS videos give up to 859 sentences each.
- YODAS sentences are spoken, unpunctuated fragments joined by timing (decision 4), and keep some half-converted Simplified (decision 8).
- Redistribution needs a NOTICE: ODC-BY (WildChat), CC BY 3.0 with video ids (YODAS), CC BY 2.0 FR with sentence ids and authors (Tatoeba),
  CC BY 4.0 (MASSIVE). `attribution.tsv` carries the ids; the YODAS text files have no channel name.
