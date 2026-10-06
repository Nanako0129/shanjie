# SP：前文＋半截注音的離線預測（issue #44，第一片）

規格是 `docs/contracts/sp-partial-zhuyin.md`（章節以 §N 標示）。這一片只做離線研究：不動核心、殼層、介面，不調任何參數。

## 方法

| 項目 | 做法 |
|---|---|
| 詞庫與模型 | 和 `reference/proto/lm_eval.py` 第 49–52 行相同的 `lex`（`mcbpmf-data.txt` ＋ `ime.OVERLAYS`，再 `lm.cap_overlay`）；`data/lm/bigram.sjlm`（啟動時驗 `data/bigram.sjlm.sha256`） |
| 候選（§2） | 按鍵序列 -> 單位 `(字元, 完成旗標, 聲調)`；前綴解讀與縮寫解讀取聯集，同字串取最高 lp（`Index` 依 (−lp, 音節數, 讀音, 詞) 排序，第一個就是最高分） |
| 分數（§1） | `lm.word(λ, v, W, lp_max)`，`v = lm.history(lm.context_key(前文), lm)`；λ：chat 0.5、formal 0.7 |
| 三組對照（§3.3） | a：分數 = lp；b：LM、`v = <s>`；c：LM＋前文 |
| 名次 | 悲觀名次（同分的對手都算在前面）；決定性順序 (−分數, 音節數, 讀音, 詞) 只用在前 64 名、第一名翻轉率、單字干擾 (ii) |
| 樣本（§3.1） | `ime.segment_words(lex, 句子)` 的每個詞界；前文 = 列的前文欄＋句中前面的詞；讀音取讀音欄 |
| 位置（§3.2） | P1–P4 是前綴按鍵序列的第 1、第一音節字元打完、第一音節完成、再多一鍵；A1、A2 是縮寫（每音節前 1／2 個字元）；縮寫查詢的單位都未完成 |
| 每次查詢時間 | 一次查詢 = 候選＋c 組分數＋前 64 名；只計 P1–P4、A1、A2 這些位置的查詢，不含 a／b 組與名次計算 |
| 檢查 | 單調性（每個樣本、每個前綴鍵，永遠執行，違反就 `SystemExit`）；`--check`：§4.2 (i)(ii) |

實作備註：

- 掃描深度的「後繼詞」用 `v` 本身（含 `<s>`），照 §3.2 (i) 字面；每個樣本的 `v` 都不是 `<s>`（前文非空的樣本）。
- 「前文非空」= 列的前文欄加句中前面的詞不是空字串。
- 掃描深度「整桶不到 9 個就記桶的大小」：完成的音節（聲調已按）後相容的字串很少，所以很多查詢掃完整桶；表中另列「只算湊滿 9 個的查詢」。
- `data/lm/bigram.sjlm` 不進版控；這個 worktree 用 symlink 指向主 checkout 的同一個檔案（hash 驗過）。
- 切尾集是 `experiments/s2h/split_tail.py` 在 `~/.cache/shanjie/work/s2h/` 已產生的 `cvtail.txt`／`wikitail.txt`（沒有重產）。

## 指令

```
python3 experiments/sp/test_predict.py
python3 experiments/sp/predict.py --rows eval/dev/user-typing.txt --set-name typing76 --profile chat --check
python3 experiments/sp/predict.py --rows eval/dev/user-reported.txt --set-name user-reported --profile formal --check
python3 experiments/sp/predict.py --rows ~/.cache/shanjie/work/s2h/cvtail.txt --set-name cvtail --profile chat --sample 600 --seed 20261007
python3 experiments/sp/predict.py --rows ~/.cache/shanjie/work/s2h/wikitail.txt --set-name wikitail --profile chat --sample 600 --seed 20261007
```

discordtune（私有，由 main 跑；只印統計數字，不印任何一列的內容）：

```
python3 experiments/sp/predict.py --rows <private path> --set-name discordtune --profile chat --sample 1000 --seed 20261007
```

每次單一集合單一設定約 1.5–2 分鐘（載入模型與詞庫約 30 秒）。

## 驗收紀錄

| 項目 | 結果 |
|---|---|
| §2 單元檢查與兩組附加測試 | `test_predict.py` 15 個測試全過（exit 0） |
| 突變 (i)–(v) | 各自讓指定的檢查失敗，見下表；程式碼已還原 |
| 一致性 (i)(ii)，typing76 兩種設定（438 個樣本） | 0 不符 |
| 一致性 (i)(ii)，user-reported 兩種設定（155 個樣本） | 0 不符 |
| 單調性，所有公開集合兩種設定 | 0 違反（cvtail 599、wikitail 600、typing76 438、user-reported 155 個樣本） |
| 查詢時間 p95 | 全部約 40–60 ms，遠低於 2 秒的停止條件 |

| 突變 | 失敗的檢查（exit code 1） |
|---|---|
| (i) 未完成單位必須同聲調 | `test_prefix_nai_cha`（`ㄋ`→奶茶）等 |
| (ii) 縮寫拿掉音節數比對 | `test_abbreviation`（`ㄋ ㄔ` 不和三字詞相容） |
| (iii) 候選不去重 | `test_dedupe_once` |
| (iv) 完成旗標改成看調號 | `test_first_tone`（`ㄊㄚ`（完成）只和一聲相容） |
| (v) 前綴解讀略過前 k−1 個單位 | `predict.py` 在 typing76 上 `monotonicity: set grew at key 5`（exit 1）；單元測試 `test_skip_check_of_earlier_units_breaks_it` 也失敗 |

## 摘要（兩字以上的 W*；a／b／c = 詞庫分數／LM＋`<s>`／LM＋前文）

McNemar 是 c 對 b、P1／P3 的 hit@9，只計前文非空的樣本；方向與計數在各集合的完整輸出裡（c-only 是 c 對 b 錯）。
| 集合 | 設定 | n(>=2字) | P1 hit@9 a/b/c | P3 hit@9 a/b/c | KS(9) a/b/c | 後繼覆蓋(>=2字) | McNemar P1 p | McNemar P3 p | 查詢 p50/p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| cvtail | chat | 390 | 6.4%/4.6%/25.4% | 43.6%/42.6%/65.1% | 33.8%/33.5%/45.0% | 260/390 = 66.7% | 8.272e-25 | 2.765e-20 | 5.4/54.3 |
| cvtail | formal | 390 | 6.4%/3.6%/32.6% | 43.6%/41.3%/67.7% | 33.8%/32.7%/47.1% | 260/390 = 66.7% | 1.926e-34 | 3.09e-23 | 5.8/52.9 |
| wikitail | chat | 426 | 2.8%/1.9%/20.9% | 35.0%/41.5%/60.6% | 30.1%/32.1%/42.9% | 287/426 = 67.4% | 1.89e-22 | 1.101e-16 | 5.4/45.9 |
| wikitail | formal | 426 | 2.8%/2.1%/31.0% | 35.0%/41.5%/65.3% | 30.1%/32.1%/45.8% | 287/426 = 67.4% | 9.556e-35 | 1.606e-20 | 5.2/42.2 |
| typing76 | chat | 272 | 8.1%/4.4%/22.8% | 54.4%/54.4%/67.6% | 37.1%/37.4%/44.8% | 139/260 = 53.5% | 2.354e-14 | 4.818e-06 | 4.5/43.4 |
| typing76 | formal | 272 | 8.1%/5.5%/27.9% | 54.4%/54.0%/68.8% | 37.1%/37.3%/46.3% | 139/260 = 53.5% | 3.115e-15 | 2.397e-06 | 4.9/51.1 |
| user-reported | chat | 91 | 11.0%/9.9%/18.7% | 58.2%/57.1%/62.6% | 38.4%/38.7%/41.6% | 41/75 = 54.7% | 0.05737 | 0.3018 | 7.9/60.2 |
| user-reported | formal | 91 | 11.0%/9.9%/23.1% | 58.2%/50.5%/62.6% | 38.4%/37.3%/42.4% | 41/75 = 54.7% | 0.007538 | 0.01921 | 5.3/42.5 |

## 各集合完整輸出

以下是 predict.py 的原始輸出（只有統計數字）。

monotonicity: 0 violations in 599 samples

### cvtail  chat
rows total=600; dropped: samples: W* has no lexicon entry for that reading=1 | samples kept=599 (>=2 chars 390, 1 char 209)

#### hit@k, W* >= 2 chars (n=390)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=390) | 0.8% | 4.6% | 6.4% | 0.3% | 3.3% | 4.6% | 4.6% | 17.4% | 25.4% | 12.3% / 28.7% / 36.2% |
| P2 (n=390) | 3.1% | 19.2% | 27.7% | 1.8% | 16.9% | 26.7% | 13.8% | 39.7% | 51.5% | 25.9% / 48.7% / 55.6% |
| P3 (n=390) | 5.1% | 32.3% | 43.6% | 5.6% | 31.3% | 42.6% | 19.7% | 56.4% | 65.1% | 34.4% / 62.1% / 68.7% |
| P4 (n=390) | 57.7% | 84.4% | 91.3% | 54.1% | 84.9% | 91.5% | 71.0% | 89.0% | 93.6% | 71.0% / 89.0% / 93.6% |
| A1 (n=390) | 27.7% | 56.9% | 64.6% | 25.6% | 54.1% | 65.9% | 50.3% | 75.1% | 80.3% | 50.3% / 75.1% / 80.3% |
| A2 (n=390) | 67.9% | 91.0% | 93.1% | 66.9% | 88.2% | 93.6% | 79.7% | 94.6% | 95.9% | 79.7% / 94.6% / 95.9% |

#### KS(k), W* >= 2 chars: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 15.6% / 125.9 | 24.9% / 194.1 | 33.8% / 256.9 |
| b | 14.8% / 118.5 | 25.2% / 195.4 | 33.5% / 253.3 |
| c | 23.1% / 175.6 | 34.8% / 261.0 | 45.0% / 331.5 |

#### hit@k, W* 1 char (n=209)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=209) | 40.2% | 54.5% | 56.5% | 14.4% | 28.7% | 33.0% | 50.7% | 74.6% | 78.9% | - |
| P2 (n=209) | 53.6% | 84.2% | 87.6% | 52.6% | 67.5% | 70.3% | 69.4% | 90.0% | 94.3% | - |
| P3 (n=209) | 79.9% | 92.8% | 95.7% | 64.6% | 94.7% | 95.7% | 84.7% | 95.2% | 98.1% | - |

#### KS(k), W* 1 char: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 13.6% / 42.1 | 17.4% / 54.5 | 18.7% / 59.3 |
| b | 5.5% / 17.2 | 8.4% / 26.8 | 10.8% / 35.4 |
| c | 18.1% / 57.4 | 23.4% / 74.6 | 26.1% / 83.3 |

successor coverage (non-empty context): all 428/599 = 71.5%; >=2 chars 260/390 = 66.7%; samples whose v is <s> despite non-empty context: 0
successor count of v (non-empty context): p50=970 p95=46108 max=98504
single-char interference (ii): P3 top-1 is >=2 syllables: 9/209 = 4.3%
abbreviation not typable (>=2 chars, some later syllable has no initial): 63/390 = 16.2%
top-1 flip rate (>=2 chars): 833/2372 = 35.1%
scan depth (all keys + A1/A2, 4600 queries): p50=595 p95=30975 max=33247; bucket exhausted before 9: 932
scan depth, only queries that reached 9 (3668): p50=296 p95=13074 max=30679
McNemar P1 hit@9, c vs b (>=2 chars, non-empty context, n=390): c-only=81 b-only=0 p=8.272e-25; hit c=99 b=18
McNemar P3 hit@9, c vs b (>=2 chars, non-empty context, n=390): c-only=96 b-only=8 p=2.765e-20; hit c=254 b=166
query time (ms, 3385 position queries, candidates+score+top64): p50=5.4 p95=54.3 max=153.2

monotonicity: 0 violations in 599 samples

### cvtail  formal
rows total=600; dropped: samples: W* has no lexicon entry for that reading=1 | samples kept=599 (>=2 chars 390, 1 char 209)

#### hit@k, W* >= 2 chars (n=390)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=390) | 0.8% | 4.6% | 6.4% | 0.3% | 2.1% | 3.6% | 8.5% | 25.1% | 32.6% | 15.4% / 33.6% / 40.8% |
| P2 (n=390) | 3.1% | 19.2% | 27.7% | 2.3% | 15.4% | 24.1% | 18.2% | 47.4% | 54.9% | 30.5% / 52.3% / 59.2% |
| P3 (n=390) | 5.1% | 32.3% | 43.6% | 5.6% | 29.5% | 41.3% | 25.6% | 61.8% | 67.7% | 37.2% / 63.6% / 70.5% |
| P4 (n=390) | 57.7% | 84.4% | 91.3% | 52.6% | 83.6% | 91.0% | 71.5% | 89.2% | 94.4% | 71.5% / 89.2% / 94.4% |
| A1 (n=390) | 27.7% | 56.9% | 64.6% | 23.1% | 49.5% | 63.3% | 52.1% | 75.6% | 80.8% | 52.1% / 75.6% / 80.8% |
| A2 (n=390) | 67.9% | 91.0% | 93.1% | 64.9% | 88.2% | 92.8% | 80.0% | 94.9% | 95.9% | 80.0% / 94.9% / 95.9% |

#### KS(k), W* >= 2 chars: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 15.6% / 125.9 | 24.9% / 194.1 | 33.8% / 256.9 |
| b | 14.5% / 117.2 | 24.0% / 187.2 | 32.7% / 248.2 |
| c | 25.8% / 193.8 | 37.8% / 280.5 | 47.1% / 345.6 |

#### hit@k, W* 1 char (n=209)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=209) | 40.2% | 54.5% | 56.5% | 13.4% | 28.2% | 29.7% | 53.6% | 77.0% | 79.9% | - |
| P2 (n=209) | 53.6% | 84.2% | 87.6% | 27.8% | 66.5% | 68.4% | 70.3% | 90.0% | 94.3% | - |
| P3 (n=209) | 79.9% | 92.8% | 95.7% | 62.7% | 92.8% | 93.8% | 85.2% | 94.7% | 98.6% | - |

#### KS(k), W* 1 char: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 13.6% / 42.1 | 17.4% / 54.5 | 18.7% / 59.3 |
| b | 5.0% / 15.8 | 7.8% / 24.9 | 9.8% / 32.1 |
| c | 18.9% / 60.3 | 24.0% / 76.6 | 26.4% / 84.2 |

successor coverage (non-empty context): all 428/599 = 71.5%; >=2 chars 260/390 = 66.7%; samples whose v is <s> despite non-empty context: 0
successor count of v (non-empty context): p50=970 p95=46108 max=98504
single-char interference (ii): P3 top-1 is >=2 syllables: 12/209 = 5.7%
abbreviation not typable (>=2 chars, some later syllable has no initial): 63/390 = 16.2%
top-1 flip rate (>=2 chars): 798/2372 = 33.6%
scan depth (all keys + A1/A2, 4600 queries): p50=595 p95=30975 max=33247; bucket exhausted before 9: 932
scan depth, only queries that reached 9 (3668): p50=296 p95=13074 max=30679
McNemar P1 hit@9, c vs b (>=2 chars, non-empty context, n=390): c-only=113 b-only=0 p=1.926e-34; hit c=127 b=14
McNemar P3 hit@9, c vs b (>=2 chars, non-empty context, n=390): c-only=113 b-only=10 p=3.09e-23; hit c=264 b=161
query time (ms, 3385 position queries, candidates+score+top64): p50=5.8 p95=52.9 max=142.0

monotonicity: 0 violations in 600 samples

### wikitail  chat
rows total=600; dropped: none | samples kept=600 (>=2 chars 426, 1 char 174)

#### hit@k, W* >= 2 chars (n=426)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=426) | 0.0% | 0.2% | 2.8% | 0.0% | 1.4% | 1.9% | 4.0% | 15.5% | 20.9% | 10.8% / 24.6% / 30.3% |
| P2 (n=426) | 0.7% | 12.0% | 18.3% | 2.1% | 14.1% | 23.2% | 17.4% | 37.3% | 47.2% | 25.6% / 47.7% / 51.4% |
| P3 (n=426) | 2.6% | 25.4% | 35.0% | 6.8% | 28.9% | 41.5% | 23.7% | 52.3% | 60.6% | 33.6% / 58.2% / 63.6% |
| P4 (n=426) | 49.1% | 77.7% | 86.4% | 53.1% | 81.9% | 88.0% | 65.3% | 87.1% | 92.7% | 65.3% / 87.1% / 92.7% |
| A1 (n=426) | 24.4% | 49.1% | 58.2% | 23.0% | 53.1% | 62.9% | 49.1% | 70.2% | 78.2% | 49.1% / 70.2% / 78.2% |
| A2 (n=426) | 59.2% | 87.1% | 91.5% | 61.7% | 88.0% | 92.0% | 74.4% | 92.5% | 95.8% | 74.4% / 92.5% / 95.8% |

#### KS(k), W* >= 2 chars: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 14.2% / 118.3 | 22.1% / 178.9 | 30.1% / 239.4 |
| b | 16.3% / 132.9 | 24.1% / 193.7 | 32.1% / 252.6 |
| c | 24.4% / 191.1 | 34.2% / 266.4 | 42.9% / 329.3 |

#### hit@k, W* 1 char (n=174)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=174) | 19.0% | 44.8% | 52.9% | 11.5% | 34.5% | 44.8% | 39.1% | 64.9% | 73.0% | - |
| P2 (n=174) | 40.2% | 70.1% | 79.3% | 39.1% | 66.1% | 75.9% | 67.8% | 86.8% | 89.1% | - |
| P3 (n=174) | 64.9% | 85.6% | 92.0% | 62.1% | 87.4% | 90.8% | 82.8% | 94.3% | 97.7% | - |

#### KS(k), W* 1 char: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 6.6% / 20.7 | 11.8% / 39.1 | 16.6% / 54.6 |
| b | 4.3% / 13.8 | 9.1% / 29.3 | 13.9% / 46.0 |
| c | 13.2% / 43.7 | 19.0% / 63.8 | 23.4% / 78.2 |

successor coverage (non-empty context): all 435/600 = 72.5%; >=2 chars 287/426 = 67.4%; samples whose v is <s> despite non-empty context: 0
successor count of v (non-empty context): p50=921 p95=98504 max=98504
single-char interference (ii): P3 top-1 is >=2 syllables: 8/174 = 4.6%
abbreviation not typable (>=2 chars, some later syllable has no initial): 63/426 = 14.8%
top-1 flip rate (>=2 chars): 1018/2698 = 37.7%
scan depth (all keys + A1/A2, 4858 queries): p50=789 p95=30975 max=33247; bucket exhausted before 9: 1103
scan depth, only queries that reached 9 (3755): p50=311 p95=15115 max=31141
McNemar P1 hit@9, c vs b (>=2 chars, non-empty context, n=426): c-only=83 b-only=2 p=1.89e-22; hit c=89 b=8
McNemar P3 hit@9, c vs b (>=2 chars, non-empty context, n=426): c-only=93 b-only=12 p=1.101e-16; hit c=258 b=177
query time (ms, 3426 position queries, candidates+score+top64): p50=5.4 p95=45.9 max=204.5

monotonicity: 0 violations in 600 samples

### wikitail  formal
rows total=600; dropped: none | samples kept=600 (>=2 chars 426, 1 char 174)

#### hit@k, W* >= 2 chars (n=426)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=426) | 0.0% | 0.2% | 2.8% | 0.0% | 1.4% | 2.1% | 9.2% | 23.2% | 31.0% | 14.6% / 31.7% / 39.2% |
| P2 (n=426) | 0.7% | 12.0% | 18.3% | 2.6% | 14.6% | 22.3% | 23.0% | 44.4% | 52.3% | 30.8% / 51.4% / 56.8% |
| P3 (n=426) | 2.6% | 25.4% | 35.0% | 7.7% | 29.1% | 41.5% | 28.9% | 58.0% | 65.3% | 38.3% / 62.7% / 67.4% |
| P4 (n=426) | 49.1% | 77.7% | 86.4% | 55.6% | 83.1% | 88.5% | 67.4% | 87.6% | 93.0% | 67.4% / 87.6% / 93.0% |
| A1 (n=426) | 24.4% | 49.1% | 58.2% | 24.6% | 52.6% | 62.0% | 51.9% | 72.5% | 78.4% | 51.9% / 72.5% / 78.4% |
| A2 (n=426) | 59.2% | 87.1% | 91.5% | 62.7% | 87.3% | 92.5% | 74.9% | 92.5% | 95.8% | 74.9% / 92.5% / 95.8% |

#### KS(k), W* >= 2 chars: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 14.2% / 118.3 | 22.1% / 178.9 | 30.1% / 239.4 |
| b | 16.9% / 137.3 | 24.4% / 196.0 | 32.1% / 252.3 |
| c | 27.3% / 212.0 | 38.0% / 294.1 | 45.8% / 350.5 |

#### hit@k, W* 1 char (n=174)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=174) | 19.0% | 44.8% | 52.9% | 13.2% | 33.3% | 39.7% | 42.0% | 69.0% | 77.6% | - |
| P2 (n=174) | 40.2% | 70.1% | 79.3% | 36.2% | 63.8% | 73.6% | 69.5% | 89.7% | 92.5% | - |
| P3 (n=174) | 64.9% | 85.6% | 92.0% | 58.0% | 86.8% | 90.2% | 83.9% | 96.0% | 97.7% | - |

#### KS(k), W* 1 char: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 6.6% / 20.7 | 11.8% / 39.1 | 16.6% / 54.6 |
| b | 4.2% / 13.2 | 8.3% / 26.4 | 12.4% / 40.2 |
| c | 15.0% / 50.0 | 20.1% / 67.2 | 24.8% / 82.8 |

successor coverage (non-empty context): all 435/600 = 72.5%; >=2 chars 287/426 = 67.4%; samples whose v is <s> despite non-empty context: 0
successor count of v (non-empty context): p50=921 p95=98504 max=98504
single-char interference (ii): P3 top-1 is >=2 syllables: 10/174 = 5.7%
abbreviation not typable (>=2 chars, some later syllable has no initial): 63/426 = 14.8%
top-1 flip rate (>=2 chars): 940/2698 = 34.8%
scan depth (all keys + A1/A2, 4858 queries): p50=789 p95=30975 max=33247; bucket exhausted before 9: 1103
scan depth, only queries that reached 9 (3755): p50=311 p95=15115 max=31141
McNemar P1 hit@9, c vs b (>=2 chars, non-empty context, n=426): c-only=125 b-only=2 p=9.556e-35; hit c=132 b=9
McNemar P3 hit@9, c vs b (>=2 chars, non-empty context, n=426): c-only=116 b-only=15 p=1.606e-20; hit c=278 b=177
query time (ms, 3426 position queries, candidates+score+top64): p50=5.2 p95=42.2 max=100.9

monotonicity: 0 violations in 438 samples; consistency (i)(ii): 0 mismatches

### typing76  chat
rows total=76; dropped: samples: W* has no lexicon entry for that reading=2 | samples kept=438 (>=2 chars 272, 1 char 166)

#### hit@k, W* >= 2 chars (n=272)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=272) | 1.1% | 3.3% | 8.1% | 0.4% | 3.3% | 4.4% | 2.2% | 17.3% | 22.8% | 11.4% / 27.2% / 33.5% |
| P2 (n=272) | 1.8% | 21.0% | 33.8% | 4.8% | 20.6% | 36.4% | 12.1% | 39.7% | 52.9% | 27.2% / 51.5% / 61.0% |
| P3 (n=272) | 5.1% | 40.4% | 54.4% | 9.9% | 44.1% | 54.4% | 19.9% | 58.1% | 67.6% | 38.6% / 63.2% / 71.7% |
| P4 (n=272) | 60.7% | 90.8% | 94.9% | 61.8% | 92.3% | 96.0% | 71.3% | 94.1% | 96.3% | 71.3% / 94.1% / 96.3% |
| A1 (n=272) | 29.4% | 61.8% | 73.5% | 30.1% | 60.7% | 73.2% | 47.4% | 75.4% | 81.6% | 47.4% / 75.4% / 81.6% |
| A2 (n=272) | 71.7% | 93.8% | 97.4% | 73.2% | 91.5% | 97.4% | 81.2% | 94.9% | 97.8% | 81.2% / 94.9% / 97.8% |

#### KS(k), W* >= 2 chars: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 15.5% / 113.2 | 27.3% / 195.6 | 37.1% / 262.5 |
| b | 16.8% / 120.6 | 28.2% / 201.5 | 37.4% / 264.0 |
| c | 22.0% / 154.8 | 35.4% / 250.0 | 44.8% / 314.0 |

#### hit@k, W* 1 char (n=166)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=166) | 20.5% | 39.2% | 48.8% | 20.5% | 31.9% | 46.4% | 41.0% | 68.1% | 72.9% | - |
| P2 (n=166) | 61.4% | 88.6% | 91.6% | 57.2% | 83.7% | 90.4% | 73.5% | 93.4% | 94.0% | - |
| P3 (n=166) | 81.9% | 97.6% | 98.8% | 75.9% | 96.4% | 98.8% | 89.2% | 98.2% | 99.4% | - |

#### KS(k), W* 1 char: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 7.4% / 23.5 | 11.3% / 37.3 | 18.7% / 62.0 |
| b | 7.5% / 24.1 | 9.3% / 30.1 | 17.5% / 57.2 |
| c | 14.9% / 49.4 | 21.2% / 69.9 | 26.5% / 87.3 |

successor coverage (non-empty context): all 269/420 = 64.0%; >=2 chars 139/260 = 53.5%; samples whose v is <s> despite non-empty context: 0
successor count of v (non-empty context): p50=802 p95=14499 max=98504
single-char interference (ii): P3 top-1 is >=2 syllables: 8/166 = 4.8%
abbreviation not typable (>=2 chars, some later syllable has no initial): 35/272 = 12.9%
top-1 flip rate (>=2 chars): 587/1589 = 36.9%
scan depth (all keys + A1/A2, 3267 queries): p50=501 p95=30975 max=33247; bucket exhausted before 9: 601
scan depth, only queries that reached 9 (2666): p50=278 p95=13673 max=32148
McNemar P1 hit@9, c vs b (>=2 chars, non-empty context, n=260): c-only=51 b-only=1 p=2.354e-14; hit c=60 b=10
McNemar P3 hit@9, c vs b (>=2 chars, non-empty context, n=260): c-only=49 b-only=13 p=4.818e-06; hit c=176 b=140
query time (ms, 2462 position queries, candidates+score+top64): p50=4.5 p95=43.4 max=77.4

monotonicity: 0 violations in 438 samples; consistency (i)(ii): 0 mismatches

### typing76  formal
rows total=76; dropped: samples: W* has no lexicon entry for that reading=2 | samples kept=438 (>=2 chars 272, 1 char 166)

#### hit@k, W* >= 2 chars (n=272)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=272) | 1.1% | 3.3% | 8.1% | 0.4% | 2.6% | 5.5% | 5.5% | 20.2% | 27.9% | 13.6% / 30.5% / 36.8% |
| P2 (n=272) | 1.8% | 21.0% | 33.8% | 5.9% | 21.7% | 35.3% | 15.8% | 41.5% | 55.5% | 29.0% / 52.2% / 61.8% |
| P3 (n=272) | 5.1% | 40.4% | 54.4% | 9.2% | 41.2% | 54.0% | 22.8% | 60.3% | 68.8% | 39.3% / 64.0% / 71.3% |
| P4 (n=272) | 60.7% | 90.8% | 94.9% | 62.5% | 91.2% | 95.6% | 71.0% | 94.1% | 96.3% | 71.0% / 94.1% / 96.3% |
| A1 (n=272) | 29.4% | 61.8% | 73.5% | 26.8% | 58.1% | 71.7% | 51.5% | 75.0% | 81.2% | 51.5% / 75.0% / 81.2% |
| A2 (n=272) | 71.7% | 93.8% | 97.4% | 68.8% | 90.4% | 94.9% | 80.9% | 94.9% | 97.8% | 80.9% / 94.9% / 97.8% |

#### KS(k), W* >= 2 chars: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 15.5% / 113.2 | 27.3% / 195.6 | 37.1% / 262.5 |
| b | 16.9% / 121.3 | 27.7% / 198.2 | 37.3% / 264.0 |
| c | 23.5% / 164.7 | 36.2% / 254.8 | 46.3% / 323.9 |

#### hit@k, W* 1 char (n=166)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=166) | 20.5% | 39.2% | 48.8% | 18.7% | 28.3% | 45.8% | 45.2% | 69.9% | 74.7% | - |
| P2 (n=166) | 61.4% | 88.6% | 91.6% | 50.0% | 82.5% | 87.3% | 75.3% | 94.0% | 94.0% | - |
| P3 (n=166) | 81.9% | 97.6% | 98.8% | 71.7% | 95.2% | 97.6% | 86.1% | 98.2% | 99.4% | - |

#### KS(k), W* 1 char: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 7.4% / 23.5 | 11.3% / 37.3 | 18.7% / 62.0 |
| b | 6.7% / 21.1 | 9.5% / 30.7 | 16.5% / 53.0 |
| c | 16.3% / 53.6 | 21.4% / 69.9 | 27.1% / 89.2 |

successor coverage (non-empty context): all 269/420 = 64.0%; >=2 chars 139/260 = 53.5%; samples whose v is <s> despite non-empty context: 0
successor count of v (non-empty context): p50=802 p95=14499 max=98504
single-char interference (ii): P3 top-1 is >=2 syllables: 13/166 = 7.8%
abbreviation not typable (>=2 chars, some later syllable has no initial): 35/272 = 12.9%
top-1 flip rate (>=2 chars): 566/1589 = 35.6%
scan depth (all keys + A1/A2, 3267 queries): p50=501 p95=30975 max=33247; bucket exhausted before 9: 601
scan depth, only queries that reached 9 (2666): p50=278 p95=13673 max=32148
McNemar P1 hit@9, c vs b (>=2 chars, non-empty context, n=260): c-only=65 b-only=4 p=3.115e-15; hit c=74 b=13
McNemar P3 hit@9, c vs b (>=2 chars, non-empty context, n=260): c-only=56 b-only=16 p=2.397e-06; hit c=179 b=139
query time (ms, 2462 position queries, candidates+score+top64): p50=4.9 p95=51.1 max=84.6

monotonicity: 0 violations in 155 samples; consistency (i)(ii): 0 mismatches

### user-reported  chat
rows total=34; dropped: samples: W* has no lexicon entry for that reading=3 | samples kept=155 (>=2 chars 91, 1 char 64)

#### hit@k, W* >= 2 chars (n=91)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=91) | 2.2% | 5.5% | 11.0% | 3.3% | 5.5% | 9.9% | 6.6% | 14.3% | 18.7% | 11.0% / 25.3% / 28.6% |
| P2 (n=91) | 6.6% | 25.3% | 38.5% | 6.6% | 27.5% | 39.6% | 14.3% | 35.2% | 44.0% | 25.3% / 41.8% / 51.6% |
| P3 (n=91) | 8.8% | 45.1% | 58.2% | 13.2% | 42.9% | 57.1% | 24.2% | 50.5% | 62.6% | 35.2% / 58.2% / 71.4% |
| P4 (n=91) | 63.7% | 92.3% | 94.5% | 63.7% | 90.1% | 94.5% | 71.4% | 92.3% | 94.5% | 71.4% / 92.3% / 94.5% |
| A1 (n=91) | 39.6% | 70.3% | 81.3% | 35.2% | 70.3% | 80.2% | 52.7% | 73.6% | 85.7% | 52.7% / 73.6% / 85.7% |
| A2 (n=91) | 67.0% | 93.4% | 96.7% | 71.4% | 91.2% | 96.7% | 72.5% | 96.7% | 96.7% | 72.5% / 96.7% / 96.7% |

#### KS(k), W* >= 2 chars: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 16.8% / 117.6 | 27.3% / 191.2 | 38.4% / 268.1 |
| b | 17.9% / 124.2 | 27.5% / 192.3 | 38.7% / 271.4 |
| c | 22.6% / 156.0 | 33.0% / 229.7 | 41.6% / 287.9 |

#### hit@k, W* 1 char (n=64)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=64) | 20.3% | 35.9% | 45.3% | 17.2% | 34.4% | 43.8% | 25.0% | 45.3% | 54.7% | - |
| P2 (n=64) | 46.9% | 81.2% | 84.4% | 40.6% | 70.3% | 76.6% | 46.9% | 78.1% | 85.9% | - |
| P3 (n=64) | 68.8% | 90.6% | 93.8% | 53.1% | 92.2% | 95.3% | 62.5% | 92.2% | 95.3% | - |

#### KS(k), W* 1 char: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 6.5% / 20.3 | 10.9% / 34.4 | 16.8% / 54.7 |
| b | 5.6% / 17.2 | 9.9% / 31.2 | 15.1% / 48.4 |
| c | 8.3% / 26.6 | 14.1% / 45.3 | 20.1% / 65.6 |

successor coverage (non-empty context): all 75/128 = 58.6%; >=2 chars 41/75 = 54.7%; samples whose v is <s> despite non-empty context: 0
successor count of v (non-empty context): p50=1247 p95=21916 max=98504
single-char interference (ii): P3 top-1 is >=2 syllables: 6/64 = 9.4%
abbreviation not typable (>=2 chars, some later syllable has no initial): 14/91 = 15.4%
top-1 flip rate (>=2 chars): 199/533 = 37.3%
scan depth (all keys + A1/A2, 1135 queries): p50=632 p95=30496 max=33247; bucket exhausted before 9: 224
scan depth, only queries that reached 9 (911): p50=301 p95=14113 max=28440
McNemar P1 hit@9, c vs b (>=2 chars, non-empty context, n=75): c-only=11 b-only=3 p=0.05737; hit c=14 b=6
McNemar P3 hit@9, c vs b (>=2 chars, non-empty context, n=75): c-only=10 b-only=5 p=0.3018; hit c=48 b=43
query time (ms, 866 position queries, candidates+score+top64): p50=7.9 p95=60.2 max=211.3

monotonicity: 0 violations in 155 samples; consistency (i)(ii): 0 mismatches

### user-reported  formal
rows total=34; dropped: samples: W* has no lexicon entry for that reading=3 | samples kept=155 (>=2 chars 91, 1 char 64)

#### hit@k, W* >= 2 chars (n=91)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=91) | 2.2% | 5.5% | 11.0% | 3.3% | 6.6% | 9.9% | 5.5% | 18.7% | 23.1% | 14.3% / 24.2% / 29.7% |
| P2 (n=91) | 6.6% | 25.3% | 38.5% | 6.6% | 26.4% | 38.5% | 16.5% | 36.3% | 45.1% | 26.4% / 41.8% / 53.8% |
| P3 (n=91) | 8.8% | 45.1% | 58.2% | 13.2% | 40.7% | 50.5% | 25.3% | 49.5% | 62.6% | 35.2% / 58.2% / 72.5% |
| P4 (n=91) | 63.7% | 92.3% | 94.5% | 61.5% | 89.0% | 95.6% | 70.3% | 92.3% | 94.5% | 70.3% / 92.3% / 94.5% |
| A1 (n=91) | 39.6% | 70.3% | 81.3% | 35.2% | 64.8% | 75.8% | 52.7% | 74.7% | 83.5% | 52.7% / 74.7% / 83.5% |
| A2 (n=91) | 67.0% | 93.4% | 96.7% | 65.9% | 90.1% | 96.7% | 71.4% | 96.7% | 96.7% | 71.4% / 96.7% / 96.7% |

#### KS(k), W* >= 2 chars: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 16.8% / 117.6 | 27.3% / 191.2 | 38.4% / 268.1 |
| b | 17.6% / 123.1 | 27.0% / 190.1 | 37.3% / 262.6 |
| c | 22.6% / 157.1 | 34.0% / 235.2 | 42.4% / 293.4 |

#### hit@k, W* 1 char (n=64)
| pos | a@1 | a@5 | a@9 | b@1 | b@5 | b@9 | c@1 | c@5 | c@9 | filtered(>=2 syl) c@1/5/9 |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 (n=64) | 20.3% | 35.9% | 45.3% | 15.6% | 34.4% | 42.2% | 25.0% | 45.3% | 54.7% | - |
| P2 (n=64) | 46.9% | 81.2% | 84.4% | 39.1% | 65.6% | 73.4% | 43.8% | 76.6% | 84.4% | - |
| P3 (n=64) | 68.8% | 90.6% | 93.8% | 53.1% | 92.2% | 95.3% | 60.9% | 90.6% | 95.3% | - |

#### KS(k), W* 1 char: mean KS / net keys saved per 100 words
| arm | KS(1) | KS(3) | KS(9) |
|---|---|---|---|
| a | 6.5% / 20.3 | 10.9% / 34.4 | 16.8% / 54.7 |
| b | 5.1% / 15.6 | 9.5% / 29.7 | 14.7% / 46.9 |
| c | 8.5% / 26.6 | 13.3% / 42.2 | 19.7% / 64.1 |

successor coverage (non-empty context): all 75/128 = 58.6%; >=2 chars 41/75 = 54.7%; samples whose v is <s> despite non-empty context: 0
successor count of v (non-empty context): p50=1247 p95=21916 max=98504
single-char interference (ii): P3 top-1 is >=2 syllables: 7/64 = 10.9%
abbreviation not typable (>=2 chars, some later syllable has no initial): 14/91 = 15.4%
top-1 flip rate (>=2 chars): 194/533 = 36.4%
scan depth (all keys + A1/A2, 1135 queries): p50=632 p95=30496 max=33247; bucket exhausted before 9: 224
scan depth, only queries that reached 9 (911): p50=301 p95=14113 max=28440
McNemar P1 hit@9, c vs b (>=2 chars, non-empty context, n=75): c-only=15 b-only=3 p=0.007538; hit c=18 b=6
McNemar P3 hit@9, c vs b (>=2 chars, non-empty context, n=75): c-only=15 b-only=4 p=0.01921; hit c=48 b=37
query time (ms, 866 position queries, candidates+score+top64): p50=5.3 p95=42.5 max=83.2
