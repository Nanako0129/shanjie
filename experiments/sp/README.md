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
- `data/lm/bigram.sjlm` 不進版控；用的是模型 E（model-v2，hash 等於 `data/bigram.sjlm.sha256`，`predict.py` 啟動時核對）。
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
| discordtune（私有，main 跑） | chat | 2,822 | 13.0%/9.9%/20.8% | 58.1%/54.2%/64.1% | 38.8%/37.3%/42.3% | 1,218/2,466 = 49.4% | 3.496e-61 | 6.037e-47 | 4.0/41.4 |

discordtune 只記統計數字（1,000 列抽樣、4,740 個樣本，丟掉 2 個；單調性 0 違反）。另外：A1 hit@9 81.4%、A2 97.1%（c）；單字干擾 (ii) 7.7%；縮寫打不出來 15.3%；第一名翻轉率 37.8%；`v` 的後繼詞數 p50 1,164、p95 31,529。

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

## 第二片：候選只由前文的後繼詞產生（`predict2.py`）

規格是 `docs/contracts/sp2-successor-prediction.md`。候選 C = 前文歷史詞 `v` 在模型裡的後繼詞，再用已打的注音篩選；分數和第一片的 c 組相同。P 模式只用前綴解讀（預設），PA 模式是前綴與縮寫的聯集（縮寫設定打開）。對照組是第一片的 c 組（全部相容字串、同一個分數），在同一批樣本、同一個位置上算。

```
python3 experiments/sp/test_predict2.py
python3 experiments/sp/predict2.py --rows eval/dev/user-typing.txt --set-name typing76 --profile chat --check
python3 experiments/sp/predict2.py --rows ~/.cache/shanjie/work/s2h/cvtail.txt --set-name cvtail --profile chat --sample 600 --seed 20261007
```
discordtune（私有，main 跑，只印統計數字）：`python3 experiments/sp/predict2.py --rows <private path> --set-name discordtune --profile chat --sample 1000 --seed 20261007 --mode both`

### 驗收紀錄

| 項目 | 結果 |
|---|---|
| 單元檢查 (i)–(v) | `test_predict2.py` 6 個測試全過（exit 0） |
| 突變 (a) 拿掉後繼詞過濾 | exit 1：(i)、(iii) 失敗 |
| 突變 (b) P 模式改用聯集 | exit 1：(ii) 失敗 |
| 一致性（typing76 全部樣本，兩種設定） | 0 不符：C 等於「該模式的相容集合 ∩ succ(v)」，另一條路從後繼詞與詞庫全部讀音算；分數和 `lm.word(λ, v, W, 相容讀音的最高 lp)` 逐位元相同，PA 也和 c 組的分數相同 |
| 單調性（P 模式，所有公開集合，兩種設定） | 0 違反 |

一致性的參考集合：契約寫「c 組的相容集合 ∩ succ(v)」；c 組是聯集，所以 P 模式的參考是「前綴相容 ∩ succ(v)」，PA 模式才是 c 組的集合。

### 摘要（母體 M：兩字以上、`v ≠ <s>`）

P1／P3 是 P 模式（V3）對 c 組的 hit@9；A1、A2 是 PA 模式。P1–P4 的 P 與 PA 結果相同（第一個音節沒打完時縮寫解讀只多出單字，已被前綴涵蓋；第一個音節完成後縮寫解讀是空的）。McNemar 是 V3-P 對 c 組。

| 集合 | 設定 | M | P1 顯示率 | P1 hit@9 V3／c | P3 顯示率 | P3 hit@9 V3／c | P1 空顯示 | A1 hit@9（PA） | A2 hit@9（PA） | KS(9) | 翻轉率 | McNemar P1 p | McNemar P3 p | 查詢 p50/p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cvtail | chat | 390 | 95.1% | 28.7%／25.4% | 87.2% | 56.2%／65.1% | 29.9% | 62.6% | 66.2% | 36.0% | 32.0% | 0.019 | 6.9e-07 | 4.3/46.3 |
| cvtail | formal | 390 | 95.1% | 32.1%／32.6% | 87.2% | 57.4%／67.7% | 29.9% | 62.8% | 66.2% | 36.9% | 30.7% | 0.75 | 4.6e-10 | 4.6/51.6 |
| wikitail | chat | 426 | 96.2% | 27.9%／20.9% | 85.4% | 59.4%／60.6% | 30.0% | 64.3% | 67.1% | 37.5% | 32.0% | 1.9e-09 | 0.56 | 5.0/49.8 |
| wikitail | formal | 426 | 96.2% | 32.6%／31.0% | 85.4% | 60.3%／65.3% | 30.0% | 64.1% | 67.1% | 38.6% | 30.2% | 0.016 | 0.00019 | 4.3/41.4 |
| typing76 | chat | 260 | 95.0% | 24.2%／23.1% | 81.5% | 50.0%／67.7% | 43.7% | 52.7% | 53.5% | 30.2% | 34.3% | 0.58 | 1e-11 | 3.6/35.3 |
| typing76 | formal | 260 | 95.0% | 27.3%／28.5% | 81.5% | 50.0%／68.8% | 43.7% | 52.7% | 53.5% | 30.8% | 32.7% | 0.45 | 3.6e-15 | 2.3/22.9 |
| user-reported | chat | 75 | 96.0% | 21.3%／18.7% | 84.0% | 48.0%／64.0% | 43.1% | 53.3% | 54.7% | 29.1% | 34.2% | 0.5 | 0.00049 | 3.3/31.7 |
| user-reported | formal | 75 | 96.0% | 24.0%／24.0% | 84.0% | 49.3%／64.0% | 43.1% | 53.3% | 54.7% | 29.9% | 32.9% | 1 | 0.0034 | 7.5/66.3 |
| discordtune（私有，main 跑） | chat | 2,466 | 95.1% | 20.6%／21.3% | 80.0% | 43.5%／64.4% | 48.1% | 47.6% | 49.1% | 26.4% | 34.9% | 0.12 | 1.4e-130 | 3.4/38.7 |

- **第一鍵（P1）**：V3 沒有一格顯著較差，cvtail 聊天、wikitail 兩種設定顯著較好（候選少，干擾少）。
- **第一個音節打完（P3）**：V3 在 8 格裡有 7 格顯著較差（少 5–19 個百分點），只有 wikitail 聊天沒有差別。正解不是前文後繼詞的樣本，V3 永遠給不出來。
- **空顯示**：P1 有顯示的樣本裡，30–44% 的 C 不含正解。
- 母體 S0（`v = <s>`、前文空）只有 typing76 12 個、user-reported 16 個；S1 在所有集合都是 0。完整表在「第二片各集合完整輸出」。
- 查詢時間每次執行會差兩到三成，只當量級參考。
- discordtune 只記統計數字：1,000 列抽樣、4,740 個樣本（M 2,466、S0 356、S1 0、U 1,918），單調性 0 違反。第一片 c 組的 A1 81.4%、A2 97.1%、KS(9) 42.3% 是兩字以上的全部 2,822 個樣本（含前文空的 356 個），和這裡的母體 M（2,466 個）不同，只能當量級參考；同母體的配對比較只有 P1、P3 的 McNemar。

## 第二片各集合完整輸出

以下是 predict2.py 的原始輸出（只有統計數字）。

monotonicity (mode P): 0 violations in 599 samples

### cvtail  chat
rows total=600; dropped: samples: W* has no lexicon entry for that reading=1 | samples kept=599; populations: M=390, S0=0, S1=0, U=209

#### M  mode P  (n=390)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 390 | 95.1% | 5.4% | 21.5% | 28.7% | 5.7% | 22.6% | 30.2% | 29.9% | 74/2884/7268 |
| P2 | 390 | 89.7% | 13.8% | 40.3% | 47.9% | 15.4% | 44.9% | 53.4% | 25.7% | 14/555/6360 |
| P3 | 390 | 87.2% | 20.3% | 51.5% | 56.2% | 23.2% | 59.1% | 64.4% | 23.5% | 7/190/745 |
| P4 | 390 | 72.1% | 55.9% | 64.1% | 65.9% | 77.6% | 89.0% | 91.5% | 7.5% | 1/16/119 |

#### M  mode PA  (n=390)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 390 | 95.1% | 5.4% | 21.5% | 28.7% | 5.7% | 22.6% | 30.2% | 29.9% | 74/2884/7268 |
| P2 | 390 | 89.7% | 13.8% | 40.3% | 47.9% | 15.4% | 44.9% | 53.4% | 25.7% | 14/555/6360 |
| P3 | 390 | 87.2% | 20.3% | 51.5% | 56.2% | 23.2% | 59.1% | 64.4% | 23.5% | 7/190/745 |
| P4 | 390 | 72.1% | 55.9% | 64.1% | 65.9% | 77.6% | 89.0% | 91.5% | 7.5% | 1/16/119 |
| A1 | 390 | 78.5% | 43.3% | 59.7% | 62.6% | 55.2% | 76.1% | 79.7% | 15.0% | 3/88/358 |
| A2 | 390 | 70.0% | 59.2% | 66.2% | 66.2% | 84.6% | 94.5% | 94.5% | 4.8% | 1/21/110 |

M mode P: KS(1) 18.7% / 134.4 | KS(3) 28.3% / 202.8 | KS(9) 36.0% / 253.8 (mean KS / net keys saved per 100 words); top-1 flip rate 562/1757 = 32.0% (key pairs where both keys display)

#### U  mode P  (n=209)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 209 | 94.3% | 47.8% | 67.9% | 69.4% | 50.8% | 72.1% | 73.6% | 14.7% | 21/1239/6613 |
| P2 | 209 | 88.0% | 64.1% | 75.6% | 78.5% | 72.8% | 85.9% | 89.1% | 8.7% | 4/188/6613 |
| P3 | 209 | 85.6% | 73.2% | 79.4% | 80.4% | 85.5% | 92.7% | 93.9% | 6.1% | 2/30/592 |

#### U  mode PA  (n=209)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 209 | 94.3% | 47.8% | 67.9% | 69.4% | 50.8% | 72.1% | 73.6% | 14.7% | 21/1239/6613 |
| P2 | 209 | 88.0% | 64.1% | 75.6% | 78.5% | 72.8% | 85.9% | 89.1% | 8.7% | 4/188/6613 |
| P3 | 209 | 85.6% | 73.2% | 79.4% | 80.4% | 85.5% | 92.7% | 93.9% | 6.1% | 2/30/592 |
| A1 | 209 | 94.3% | 47.8% | 67.9% | 69.4% | 50.8% | 72.1% | 73.6% | 14.7% | 21/1239/6613 |
| A2 | 209 | 88.0% | 63.6% | 74.2% | 76.6% | 72.3% | 84.2% | 87.0% | 8.7% | 4/207/6613 |
McNemar P1 hit@9, V3-P vs reference arm c (population M, n=390): V3-only=20 c-only=7 p=0.01916; hit V3=112 c=99
McNemar P3 hit@9, V3-P vs reference arm c (population M, n=390): V3-only=8 c-only=43 p=6.867e-07; hit V3=219 c=254
query time (ms, 5572 position queries, C+score+top64; varies 20-30% between runs): p50=4.3 p95=46.3 max=206.6

monotonicity (mode P): 0 violations in 599 samples

### cvtail  formal
rows total=600; dropped: samples: W* has no lexicon entry for that reading=1 | samples kept=599; populations: M=390, S0=0, S1=0, U=209

#### M  mode P  (n=390)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 390 | 95.1% | 8.2% | 24.4% | 32.1% | 8.6% | 25.6% | 33.7% | 29.9% | 74/2884/7268 |
| P2 | 390 | 89.7% | 17.9% | 43.6% | 49.2% | 20.0% | 48.6% | 54.9% | 25.7% | 14/555/6360 |
| P3 | 390 | 87.2% | 25.4% | 53.6% | 57.4% | 29.1% | 61.5% | 65.9% | 23.5% | 7/190/745 |
| P4 | 390 | 72.1% | 56.4% | 64.4% | 66.2% | 78.3% | 89.3% | 91.8% | 7.5% | 1/16/119 |

#### M  mode PA  (n=390)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 390 | 95.1% | 8.2% | 24.4% | 32.1% | 8.6% | 25.6% | 33.7% | 29.9% | 74/2884/7268 |
| P2 | 390 | 89.7% | 17.9% | 43.6% | 49.2% | 20.0% | 48.6% | 54.9% | 25.7% | 14/555/6360 |
| P3 | 390 | 87.2% | 25.4% | 53.6% | 57.4% | 29.1% | 61.5% | 65.9% | 23.5% | 7/190/745 |
| P4 | 390 | 72.1% | 56.4% | 64.4% | 66.2% | 78.3% | 89.3% | 91.8% | 7.5% | 1/16/119 |
| A1 | 390 | 78.5% | 43.8% | 60.0% | 62.8% | 55.9% | 76.5% | 80.1% | 15.0% | 3/88/358 |
| A2 | 390 | 70.0% | 59.2% | 66.2% | 66.2% | 84.6% | 94.5% | 94.5% | 4.8% | 1/21/110 |

M mode P: KS(1) 20.8% / 149.5 | KS(3) 30.1% / 213.8 | KS(9) 36.9% / 260.3 (mean KS / net keys saved per 100 words); top-1 flip rate 540/1757 = 30.7% (key pairs where both keys display)

#### U  mode P  (n=209)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 209 | 94.3% | 50.7% | 67.9% | 70.3% | 53.8% | 72.1% | 74.6% | 14.7% | 21/1239/6613 |
| P2 | 209 | 88.0% | 65.1% | 75.6% | 78.9% | 73.9% | 85.9% | 89.7% | 8.7% | 4/188/6613 |
| P3 | 209 | 85.6% | 73.2% | 79.4% | 80.4% | 85.5% | 92.7% | 93.9% | 6.1% | 2/30/592 |

#### U  mode PA  (n=209)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 209 | 94.3% | 50.7% | 67.9% | 70.3% | 53.8% | 72.1% | 74.6% | 14.7% | 21/1239/6613 |
| P2 | 209 | 88.0% | 65.1% | 75.6% | 78.9% | 73.9% | 85.9% | 89.7% | 8.7% | 4/188/6613 |
| P3 | 209 | 85.6% | 73.2% | 79.4% | 80.4% | 85.5% | 92.7% | 93.9% | 6.1% | 2/30/592 |
| A1 | 209 | 94.3% | 50.7% | 67.9% | 70.3% | 53.8% | 72.1% | 74.6% | 14.7% | 21/1239/6613 |
| A2 | 209 | 88.0% | 65.1% | 74.2% | 77.0% | 73.9% | 84.2% | 87.5% | 8.7% | 4/207/6613 |
McNemar P1 hit@9, V3-P vs reference arm c (population M, n=390): V3-only=4 c-only=6 p=0.7539; hit V3=125 c=127
McNemar P3 hit@9, V3-P vs reference arm c (population M, n=390): V3-only=3 c-only=43 p=4.622e-10; hit V3=224 c=264
query time (ms, 5572 position queries, C+score+top64; varies 20-30% between runs): p50=4.6 p95=51.6 max=326.2

monotonicity (mode P): 0 violations in 600 samples

### wikitail  chat
rows total=600; dropped: none | samples kept=600; populations: M=426, S0=0, S1=0, U=174

#### M  mode P  (n=426)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 426 | 96.2% | 5.9% | 20.4% | 27.9% | 6.1% | 21.2% | 29.0% | 30.0% | 58/5255/7268 |
| P2 | 426 | 90.1% | 20.7% | 41.8% | 50.2% | 22.9% | 46.4% | 55.7% | 25.3% | 10/636/6613 |
| P3 | 426 | 85.4% | 26.3% | 54.2% | 59.4% | 30.8% | 63.5% | 69.5% | 21.2% | 6/197/794 |
| P4 | 426 | 71.8% | 57.3% | 66.2% | 66.9% | 79.7% | 92.2% | 93.1% | 6.2% | 1/13/58 |

#### M  mode PA  (n=426)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 426 | 96.2% | 5.9% | 20.4% | 27.9% | 6.1% | 21.2% | 29.0% | 30.0% | 58/5255/7268 |
| P2 | 426 | 90.1% | 20.7% | 41.8% | 50.2% | 22.9% | 46.4% | 55.7% | 25.3% | 10/636/6613 |
| P3 | 426 | 85.4% | 26.3% | 54.2% | 59.4% | 30.8% | 63.5% | 69.5% | 21.2% | 6/197/794 |
| P4 | 426 | 71.8% | 57.3% | 66.2% | 66.9% | 79.7% | 92.2% | 93.1% | 6.2% | 1/13/58 |
| A1 | 426 | 79.6% | 47.2% | 61.0% | 64.3% | 59.3% | 76.7% | 80.8% | 15.3% | 2/129/225 |
| A2 | 426 | 71.1% | 59.9% | 66.2% | 67.1% | 84.2% | 93.1% | 94.4% | 5.3% | 1/21/160 |

M mode P: KS(1) 22.7% / 172.1 | KS(3) 30.7% / 232.2 | KS(9) 37.5% / 279.8 (mean KS / net keys saved per 100 words); top-1 flip rate 658/2055 = 32.0% (key pairs where both keys display)

#### U  mode P  (n=174)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 174 | 96.6% | 43.1% | 64.9% | 71.3% | 44.6% | 67.3% | 73.8% | 11.9% | 34/454/6613 |
| P2 | 174 | 91.4% | 66.1% | 79.9% | 81.0% | 72.3% | 87.4% | 88.7% | 6.9% | 6/275/6613 |
| P3 | 174 | 89.7% | 74.7% | 83.9% | 85.1% | 83.3% | 93.6% | 94.9% | 5.1% | 3/33/369 |

#### U  mode PA  (n=174)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 174 | 96.6% | 43.1% | 64.9% | 71.3% | 44.6% | 67.3% | 73.8% | 11.9% | 34/454/6613 |
| P2 | 174 | 91.4% | 66.1% | 79.9% | 81.0% | 72.3% | 87.4% | 88.7% | 6.9% | 6/275/6613 |
| P3 | 174 | 89.7% | 74.7% | 83.9% | 85.1% | 83.3% | 93.6% | 94.9% | 5.1% | 3/33/369 |
| A1 | 174 | 96.6% | 43.1% | 64.9% | 71.3% | 44.6% | 67.3% | 73.8% | 11.9% | 34/454/6613 |
| A2 | 174 | 93.1% | 59.8% | 78.2% | 79.3% | 64.2% | 84.0% | 85.2% | 8.6% | 9/293/6613 |
McNemar P1 hit@9, V3-P vs reference arm c (population M, n=426): V3-only=30 c-only=0 p=1.863e-09; hit V3=119 c=89
McNemar P3 hit@9, V3-P vs reference arm c (population M, n=426): V3-only=21 c-only=26 p=0.5601; hit V3=253 c=258
query time (ms, 5652 position queries, C+score+top64; varies 20-30% between runs): p50=5.0 p95=49.8 max=246.2

monotonicity (mode P): 0 violations in 600 samples

### wikitail  formal
rows total=600; dropped: none | samples kept=600; populations: M=426, S0=0, S1=0, U=174

#### M  mode P  (n=426)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 426 | 96.2% | 9.6% | 23.9% | 32.6% | 10.0% | 24.9% | 33.9% | 30.0% | 58/5255/7268 |
| P2 | 426 | 90.1% | 23.9% | 44.1% | 52.1% | 26.6% | 49.0% | 57.8% | 25.3% | 10/636/6613 |
| P3 | 426 | 85.4% | 29.8% | 55.4% | 60.3% | 34.9% | 64.8% | 70.6% | 21.2% | 6/197/794 |
| P4 | 426 | 71.8% | 57.7% | 66.4% | 67.1% | 80.4% | 92.5% | 93.5% | 6.2% | 1/13/58 |

#### M  mode PA  (n=426)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 426 | 96.2% | 9.6% | 23.9% | 32.6% | 10.0% | 24.9% | 33.9% | 30.0% | 58/5255/7268 |
| P2 | 426 | 90.1% | 23.9% | 44.1% | 52.1% | 26.6% | 49.0% | 57.8% | 25.3% | 10/636/6613 |
| P3 | 426 | 85.4% | 29.8% | 55.4% | 60.3% | 34.9% | 64.8% | 70.6% | 21.2% | 6/197/794 |
| P4 | 426 | 71.8% | 57.7% | 66.4% | 67.1% | 80.4% | 92.5% | 93.5% | 6.2% | 1/13/58 |
| A1 | 426 | 79.6% | 48.1% | 61.5% | 64.1% | 60.5% | 77.3% | 80.5% | 15.3% | 2/129/225 |
| A2 | 426 | 71.1% | 59.6% | 66.2% | 67.1% | 83.8% | 93.1% | 94.4% | 5.3% | 1/21/160 |

M mode P: KS(1) 24.4% / 184.0 | KS(3) 32.7% / 246.2 | KS(9) 38.6% / 287.6 (mean KS / net keys saved per 100 words); top-1 flip rate 620/2055 = 30.2% (key pairs where both keys display)

#### U  mode P  (n=174)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 174 | 96.6% | 43.7% | 67.2% | 72.4% | 45.2% | 69.6% | 75.0% | 11.9% | 34/454/6613 |
| P2 | 174 | 91.4% | 67.2% | 80.5% | 82.8% | 73.6% | 88.1% | 90.6% | 6.9% | 6/275/6613 |
| P3 | 174 | 89.7% | 75.3% | 85.1% | 85.1% | 84.0% | 94.9% | 94.9% | 5.1% | 3/33/369 |

#### U  mode PA  (n=174)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 174 | 96.6% | 43.7% | 67.2% | 72.4% | 45.2% | 69.6% | 75.0% | 11.9% | 34/454/6613 |
| P2 | 174 | 91.4% | 67.2% | 80.5% | 82.8% | 73.6% | 88.1% | 90.6% | 6.9% | 6/275/6613 |
| P3 | 174 | 89.7% | 75.3% | 85.1% | 85.1% | 84.0% | 94.9% | 94.9% | 5.1% | 3/33/369 |
| A1 | 174 | 96.6% | 43.7% | 67.2% | 72.4% | 45.2% | 69.6% | 75.0% | 11.9% | 34/454/6613 |
| A2 | 174 | 93.1% | 63.8% | 78.7% | 81.6% | 68.5% | 84.6% | 87.7% | 8.6% | 9/293/6613 |
McNemar P1 hit@9, V3-P vs reference arm c (population M, n=426): V3-only=7 c-only=0 p=0.01562; hit V3=139 c=132
McNemar P3 hit@9, V3-P vs reference arm c (population M, n=426): V3-only=5 c-only=26 p=0.0001922; hit V3=257 c=278
query time (ms, 5652 position queries, C+score+top64; varies 20-30% between runs): p50=4.3 p95=41.4 max=104.7

monotonicity (mode P): 0 violations in 438 samples; consistency: 0 mismatches

### typing76  chat
rows total=76; dropped: samples: W* has no lexicon entry for that reading=2 | samples kept=438; populations: M=260, S0=12, S1=0, U=166

#### M  mode P  (n=260)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 260 | 95.0% | 3.5% | 19.2% | 24.2% | 3.6% | 20.2% | 25.5% | 43.7% | 48/1579/7268 |
| P2 | 260 | 85.8% | 13.5% | 37.3% | 43.1% | 15.7% | 43.5% | 50.2% | 37.7% | 11/414/6613 |
| P3 | 260 | 81.5% | 21.2% | 45.8% | 50.0% | 25.9% | 56.1% | 61.3% | 34.4% | 5/88/794 |
| P4 | 260 | 59.2% | 46.2% | 52.7% | 53.1% | 77.9% | 89.0% | 89.6% | 9.7% | 1/15/40 |

#### M  mode PA  (n=260)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 260 | 95.0% | 3.5% | 19.2% | 24.2% | 3.6% | 20.2% | 25.5% | 43.7% | 48/1579/7268 |
| P2 | 260 | 85.8% | 13.5% | 37.3% | 43.1% | 15.7% | 43.5% | 50.2% | 37.7% | 11/414/6613 |
| P3 | 260 | 81.5% | 21.2% | 45.8% | 50.0% | 25.9% | 56.1% | 61.3% | 34.4% | 5/88/794 |
| P4 | 260 | 59.2% | 46.2% | 52.7% | 53.1% | 77.9% | 89.0% | 89.6% | 9.7% | 1/15/40 |
| A1 | 260 | 70.0% | 36.5% | 51.9% | 52.7% | 52.2% | 74.2% | 75.3% | 23.6% | 3/63/225 |
| A2 | 260 | 59.2% | 50.4% | 53.1% | 53.5% | 85.1% | 89.6% | 90.3% | 9.7% | 1/10/93 |

M mode P: KS(1) 16.0% / 107.7 | KS(3) 24.6% / 168.1 | KS(9) 30.2% / 205.8 (mean KS / net keys saved per 100 words); top-1 flip rate 340/991 = 34.3% (key pairs where both keys display)

#### S0  mode P  (n=12)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 12 | 100.0% | 0.0% | 16.7% | 16.7% | 0.0% | 16.7% | 16.7% | 0.0% | 9029/12037/12037 |
| P2 | 12 | 100.0% | 0.0% | 50.0% | 66.7% | 0.0% | 50.0% | 66.7% | 0.0% | 410/2657/2657 |
| P3 | 12 | 100.0% | 0.0% | 50.0% | 66.7% | 0.0% | 50.0% | 66.7% | 0.0% | 218/649/649 |
| P4 | 12 | 100.0% | 66.7% | 91.7% | 91.7% | 66.7% | 91.7% | 91.7% | 0.0% | 17/34/34 |

#### S0  mode PA  (n=12)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 12 | 100.0% | 0.0% | 16.7% | 16.7% | 0.0% | 16.7% | 16.7% | 0.0% | 9029/12037/12037 |
| P2 | 12 | 100.0% | 0.0% | 50.0% | 66.7% | 0.0% | 50.0% | 66.7% | 0.0% | 410/2657/2657 |
| P3 | 12 | 100.0% | 0.0% | 50.0% | 66.7% | 0.0% | 50.0% | 66.7% | 0.0% | 218/649/649 |
| P4 | 12 | 100.0% | 66.7% | 91.7% | 91.7% | 66.7% | 91.7% | 91.7% | 0.0% | 17/34/34 |
| A1 | 12 | 100.0% | 33.3% | 75.0% | 91.7% | 33.3% | 75.0% | 91.7% | 0.0% | 212/308/308 |
| A2 | 12 | 100.0% | 91.7% | 91.7% | 100.0% | 91.7% | 91.7% | 100.0% | 0.0% | 3/87/87 |

S0 mode P: KS(1) 15.2% / 108.3 | KS(3) 34.6% / 241.7 | KS(9) 45.0% / 308.3 (mean KS / net keys saved per 100 words); top-1 flip rate 24/69 = 34.8% (key pairs where both keys display)

#### U  mode P  (n=166)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 166 | 97.0% | 39.2% | 67.5% | 70.5% | 40.4% | 69.6% | 72.7% | 15.5% | 38/483/11644 |
| P2 | 166 | 92.2% | 66.9% | 81.3% | 81.9% | 72.5% | 88.2% | 88.9% | 11.1% | 5/92/410 |
| P3 | 166 | 88.0% | 75.9% | 81.9% | 81.9% | 86.3% | 93.2% | 93.2% | 6.8% | 4/39/216 |

#### U  mode PA  (n=166)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 166 | 97.0% | 39.2% | 67.5% | 70.5% | 40.4% | 69.6% | 72.7% | 15.5% | 38/483/11644 |
| P2 | 166 | 92.2% | 66.9% | 81.3% | 81.9% | 72.5% | 88.2% | 88.9% | 11.1% | 5/92/410 |
| P3 | 166 | 88.0% | 75.9% | 81.9% | 81.9% | 86.3% | 93.2% | 93.2% | 6.8% | 4/39/216 |
| A1 | 166 | 97.0% | 39.2% | 67.5% | 70.5% | 40.4% | 69.6% | 72.7% | 15.5% | 38/483/11644 |
| A2 | 166 | 92.8% | 64.5% | 78.9% | 80.7% | 69.5% | 85.1% | 87.0% | 11.7% | 8/172/560 |
McNemar P1 hit@9, V3-P vs reference arm c (population M, n=260): V3-only=8 c-only=5 p=0.5811; hit V3=63 c=60
McNemar P3 hit@9, V3-P vs reference arm c (population M, n=260): V3-only=3 c-only=49 p=1.043e-11; hit V3=130 c=176
query time (ms, 4048 position queries, C+score+top64; varies 20-30% between runs): p50=3.6 p95=35.3 max=221.9

monotonicity (mode P): 0 violations in 438 samples; consistency: 0 mismatches

### typing76  formal
rows total=76; dropped: samples: W* has no lexicon entry for that reading=2 | samples kept=438; populations: M=260, S0=12, S1=0, U=166

#### M  mode P  (n=260)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 260 | 95.0% | 6.2% | 20.4% | 27.3% | 6.5% | 21.5% | 28.7% | 43.7% | 48/1579/7268 |
| P2 | 260 | 85.8% | 16.9% | 36.5% | 44.2% | 19.7% | 42.6% | 51.6% | 37.7% | 11/414/6613 |
| P3 | 260 | 81.5% | 23.1% | 45.4% | 50.0% | 28.3% | 55.7% | 61.3% | 34.4% | 5/88/794 |
| P4 | 260 | 59.2% | 45.8% | 52.7% | 53.1% | 77.3% | 89.0% | 89.6% | 9.7% | 1/15/40 |

#### M  mode PA  (n=260)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 260 | 95.0% | 6.2% | 20.4% | 27.3% | 6.5% | 21.5% | 28.7% | 43.7% | 48/1579/7268 |
| P2 | 260 | 85.8% | 16.9% | 36.5% | 44.2% | 19.7% | 42.6% | 51.6% | 37.7% | 11/414/6613 |
| P3 | 260 | 81.5% | 23.1% | 45.4% | 50.0% | 28.3% | 55.7% | 61.3% | 34.4% | 5/88/794 |
| P4 | 260 | 59.2% | 45.8% | 52.7% | 53.1% | 77.3% | 89.0% | 89.6% | 9.7% | 1/15/40 |
| A1 | 260 | 70.0% | 39.2% | 51.5% | 52.7% | 56.0% | 73.6% | 75.3% | 23.6% | 3/63/225 |
| A2 | 260 | 59.2% | 50.4% | 53.1% | 53.5% | 85.1% | 89.6% | 90.3% | 9.7% | 1/10/93 |

M mode P: KS(1) 17.2% / 116.2 | KS(3) 25.1% / 171.5 | KS(9) 30.8% / 210.0 (mean KS / net keys saved per 100 words); top-1 flip rate 324/991 = 32.7% (key pairs where both keys display)

#### S0  mode P  (n=12)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 12 | 100.0% | 0.0% | 16.7% | 16.7% | 0.0% | 16.7% | 16.7% | 0.0% | 9029/12037/12037 |
| P2 | 12 | 100.0% | 0.0% | 58.3% | 66.7% | 0.0% | 58.3% | 66.7% | 0.0% | 410/2657/2657 |
| P3 | 12 | 100.0% | 0.0% | 58.3% | 66.7% | 0.0% | 58.3% | 66.7% | 0.0% | 218/649/649 |
| P4 | 12 | 100.0% | 66.7% | 91.7% | 91.7% | 66.7% | 91.7% | 91.7% | 0.0% | 17/34/34 |

#### S0  mode PA  (n=12)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 12 | 100.0% | 0.0% | 16.7% | 16.7% | 0.0% | 16.7% | 16.7% | 0.0% | 9029/12037/12037 |
| P2 | 12 | 100.0% | 0.0% | 58.3% | 66.7% | 0.0% | 58.3% | 66.7% | 0.0% | 410/2657/2657 |
| P3 | 12 | 100.0% | 0.0% | 58.3% | 66.7% | 0.0% | 58.3% | 66.7% | 0.0% | 218/649/649 |
| P4 | 12 | 100.0% | 66.7% | 91.7% | 91.7% | 66.7% | 91.7% | 91.7% | 0.0% | 17/34/34 |
| A1 | 12 | 100.0% | 58.3% | 75.0% | 91.7% | 58.3% | 75.0% | 91.7% | 0.0% | 212/308/308 |
| A2 | 12 | 100.0% | 91.7% | 91.7% | 100.0% | 91.7% | 91.7% | 100.0% | 0.0% | 3/87/87 |

S0 mode P: KS(1) 15.2% / 108.3 | KS(3) 32.5% / 225.0 | KS(9) 45.0% / 308.3 (mean KS / net keys saved per 100 words); top-1 flip rate 24/69 = 34.8% (key pairs where both keys display)

#### U  mode P  (n=166)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 166 | 97.0% | 44.0% | 68.1% | 71.7% | 45.3% | 70.2% | 73.9% | 15.5% | 38/483/11644 |
| P2 | 166 | 92.2% | 68.1% | 81.9% | 81.9% | 73.9% | 88.9% | 88.9% | 11.1% | 5/92/410 |
| P3 | 166 | 88.0% | 75.3% | 81.9% | 81.9% | 85.6% | 93.2% | 93.2% | 6.8% | 4/39/216 |

#### U  mode PA  (n=166)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 166 | 97.0% | 44.0% | 68.1% | 71.7% | 45.3% | 70.2% | 73.9% | 15.5% | 38/483/11644 |
| P2 | 166 | 92.2% | 68.1% | 81.9% | 81.9% | 73.9% | 88.9% | 88.9% | 11.1% | 5/92/410 |
| P3 | 166 | 88.0% | 75.3% | 81.9% | 81.9% | 85.6% | 93.2% | 93.2% | 6.8% | 4/39/216 |
| A1 | 166 | 97.0% | 44.0% | 68.1% | 71.7% | 45.3% | 70.2% | 73.9% | 15.5% | 38/483/11644 |
| A2 | 166 | 92.8% | 65.7% | 79.5% | 80.7% | 70.8% | 85.7% | 87.0% | 11.7% | 8/172/560 |
McNemar P1 hit@9, V3-P vs reference arm c (population M, n=260): V3-only=2 c-only=5 p=0.4531; hit V3=71 c=74
McNemar P3 hit@9, V3-P vs reference arm c (population M, n=260): V3-only=0 c-only=49 p=3.553e-15; hit V3=130 c=179
query time (ms, 4048 position queries, C+score+top64; varies 20-30% between runs): p50=2.3 p95=22.9 max=63.0

monotonicity (mode P): 0 violations in 155 samples

### user-reported  chat
rows total=34; dropped: samples: W* has no lexicon entry for that reading=3 | samples kept=155; populations: M=75, S0=16, S1=0, U=64

#### M  mode P  (n=75)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 75 | 96.0% | 6.7% | 17.3% | 21.3% | 6.9% | 18.1% | 22.2% | 43.1% | 78/655/6613 |
| P2 | 75 | 89.3% | 14.7% | 33.3% | 37.3% | 16.4% | 37.3% | 41.8% | 38.8% | 14/294/6613 |
| P3 | 75 | 84.0% | 25.3% | 40.0% | 48.0% | 30.2% | 47.6% | 57.1% | 34.9% | 5/49/794 |
| P4 | 75 | 58.7% | 49.3% | 54.7% | 54.7% | 84.1% | 93.2% | 93.2% | 6.8% | 1/6/27 |

#### M  mode PA  (n=75)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 75 | 96.0% | 6.7% | 17.3% | 21.3% | 6.9% | 18.1% | 22.2% | 43.1% | 78/655/6613 |
| P2 | 75 | 89.3% | 14.7% | 33.3% | 37.3% | 16.4% | 37.3% | 41.8% | 38.8% | 14/294/6613 |
| P3 | 75 | 84.0% | 25.3% | 40.0% | 48.0% | 30.2% | 47.6% | 57.1% | 34.9% | 5/49/794 |
| P4 | 75 | 58.7% | 49.3% | 54.7% | 54.7% | 84.1% | 93.2% | 93.2% | 6.8% | 1/6/27 |
| A1 | 75 | 74.7% | 44.0% | 50.7% | 53.3% | 58.9% | 67.9% | 71.4% | 26.8% | 3/45/157 |
| A2 | 75 | 65.3% | 49.3% | 54.7% | 54.7% | 75.5% | 83.7% | 83.7% | 16.3% | 1/19/39 |

M mode P: KS(1) 17.8% / 120.0 | KS(3) 24.2% / 164.0 | KS(9) 29.1% / 196.0 (mean KS / net keys saved per 100 words); top-1 flip rate 102/298 = 34.2% (key pairs where both keys display)

#### S0  mode P  (n=16)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 16 | 100.0% | 6.2% | 12.5% | 18.8% | 6.2% | 12.5% | 18.8% | 6.2% | 10022/13127/13127 |
| P2 | 16 | 100.0% | 12.5% | 37.5% | 43.8% | 12.5% | 37.5% | 43.8% | 6.2% | 984/13127/13127 |
| P3 | 16 | 100.0% | 18.8% | 43.8% | 56.2% | 18.8% | 43.8% | 56.2% | 6.2% | 290/2617/2617 |
| P4 | 16 | 100.0% | 75.0% | 81.2% | 87.5% | 75.0% | 81.2% | 87.5% | 6.2% | 22/102/102 |

#### S0  mode PA  (n=16)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 16 | 100.0% | 6.2% | 12.5% | 18.8% | 6.2% | 12.5% | 18.8% | 6.2% | 10022/13127/13127 |
| P2 | 16 | 100.0% | 12.5% | 37.5% | 43.8% | 12.5% | 37.5% | 43.8% | 6.2% | 984/13127/13127 |
| P3 | 16 | 100.0% | 18.8% | 43.8% | 56.2% | 18.8% | 43.8% | 56.2% | 6.2% | 290/2617/2617 |
| P4 | 16 | 100.0% | 75.0% | 81.2% | 87.5% | 75.0% | 81.2% | 87.5% | 6.2% | 22/102/102 |
| A1 | 16 | 100.0% | 62.5% | 87.5% | 87.5% | 62.5% | 87.5% | 87.5% | 6.2% | 143/465/465 |
| A2 | 16 | 100.0% | 81.2% | 93.8% | 93.8% | 81.2% | 93.8% | 93.8% | 6.2% | 4/55/55 |

S0 mode P: KS(1) 25.8% / 187.5 | KS(3) 34.0% / 243.8 | KS(9) 42.7% / 306.2 (mean KS / net keys saved per 100 words); top-1 flip rate 29/94 = 30.9% (key pairs where both keys display)

#### U  mode P  (n=64)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 64 | 98.4% | 25.0% | 45.3% | 53.1% | 25.4% | 46.0% | 54.0% | 28.6% | 80/11406/13127 |
| P2 | 64 | 92.2% | 42.2% | 60.9% | 64.1% | 45.8% | 66.1% | 69.5% | 23.7% | 9/6613/13127 |
| P3 | 64 | 82.8% | 51.6% | 68.8% | 68.8% | 62.3% | 83.0% | 83.0% | 15.1% | 6/380/1157 |

#### U  mode PA  (n=64)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 64 | 98.4% | 25.0% | 45.3% | 53.1% | 25.4% | 46.0% | 54.0% | 28.6% | 80/11406/13127 |
| P2 | 64 | 92.2% | 42.2% | 60.9% | 64.1% | 45.8% | 66.1% | 69.5% | 23.7% | 9/6613/13127 |
| P3 | 64 | 82.8% | 51.6% | 68.8% | 68.8% | 62.3% | 83.0% | 83.0% | 15.1% | 6/380/1157 |
| A1 | 64 | 98.4% | 25.0% | 45.3% | 53.1% | 25.4% | 46.0% | 54.0% | 28.6% | 80/11406/13127 |
| A2 | 64 | 95.3% | 37.5% | 57.8% | 62.5% | 39.3% | 60.7% | 65.6% | 26.2% | 19/1610/13127 |
McNemar P1 hit@9, V3-P vs reference arm c (population M, n=75): V3-only=2 c-only=0 p=0.5; hit V3=16 c=14
McNemar P3 hit@9, V3-P vs reference arm c (population M, n=75): V3-only=0 c-only=12 p=0.0004883; hit V3=36 c=48
query time (ms, 1422 position queries, C+score+top64; varies 20-30% between runs): p50=3.3 p95=31.7 max=78.0

monotonicity (mode P): 0 violations in 155 samples

### user-reported  formal
rows total=34; dropped: samples: W* has no lexicon entry for that reading=3 | samples kept=155; populations: M=75, S0=16, S1=0, U=64

#### M  mode P  (n=75)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 75 | 96.0% | 5.3% | 20.0% | 24.0% | 5.6% | 20.8% | 25.0% | 43.1% | 78/655/6613 |
| P2 | 75 | 89.3% | 17.3% | 33.3% | 38.7% | 19.4% | 37.3% | 43.3% | 38.8% | 14/294/6613 |
| P3 | 75 | 84.0% | 25.3% | 40.0% | 49.3% | 30.2% | 47.6% | 58.7% | 34.9% | 5/49/794 |
| P4 | 75 | 58.7% | 49.3% | 54.7% | 54.7% | 84.1% | 93.2% | 93.2% | 6.8% | 1/6/27 |

#### M  mode PA  (n=75)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 75 | 96.0% | 5.3% | 20.0% | 24.0% | 5.6% | 20.8% | 25.0% | 43.1% | 78/655/6613 |
| P2 | 75 | 89.3% | 17.3% | 33.3% | 38.7% | 19.4% | 37.3% | 43.3% | 38.8% | 14/294/6613 |
| P3 | 75 | 84.0% | 25.3% | 40.0% | 49.3% | 30.2% | 47.6% | 58.7% | 34.9% | 5/49/794 |
| P4 | 75 | 58.7% | 49.3% | 54.7% | 54.7% | 84.1% | 93.2% | 93.2% | 6.8% | 1/6/27 |
| A1 | 75 | 74.7% | 42.7% | 50.7% | 53.3% | 57.1% | 67.9% | 71.4% | 26.8% | 3/45/157 |
| A2 | 75 | 65.3% | 49.3% | 54.7% | 54.7% | 75.5% | 83.7% | 83.7% | 16.3% | 1/19/39 |

M mode P: KS(1) 18.0% / 122.7 | KS(3) 25.1% / 169.3 | KS(9) 29.9% / 201.3 (mean KS / net keys saved per 100 words); top-1 flip rate 98/298 = 32.9% (key pairs where both keys display)

#### S0  mode P  (n=16)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 16 | 100.0% | 6.2% | 18.8% | 18.8% | 6.2% | 18.8% | 18.8% | 6.2% | 10022/13127/13127 |
| P2 | 16 | 100.0% | 12.5% | 37.5% | 43.8% | 12.5% | 37.5% | 43.8% | 6.2% | 984/13127/13127 |
| P3 | 16 | 100.0% | 18.8% | 43.8% | 56.2% | 18.8% | 43.8% | 56.2% | 6.2% | 290/2617/2617 |
| P4 | 16 | 100.0% | 75.0% | 81.2% | 87.5% | 75.0% | 81.2% | 87.5% | 6.2% | 22/102/102 |

#### S0  mode PA  (n=16)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 16 | 100.0% | 6.2% | 18.8% | 18.8% | 6.2% | 18.8% | 18.8% | 6.2% | 10022/13127/13127 |
| P2 | 16 | 100.0% | 12.5% | 37.5% | 43.8% | 12.5% | 37.5% | 43.8% | 6.2% | 984/13127/13127 |
| P3 | 16 | 100.0% | 18.8% | 43.8% | 56.2% | 18.8% | 43.8% | 56.2% | 6.2% | 290/2617/2617 |
| P4 | 16 | 100.0% | 75.0% | 81.2% | 87.5% | 75.0% | 81.2% | 87.5% | 6.2% | 22/102/102 |
| A1 | 16 | 100.0% | 62.5% | 87.5% | 87.5% | 62.5% | 87.5% | 87.5% | 6.2% | 143/465/465 |
| A2 | 16 | 100.0% | 81.2% | 93.8% | 93.8% | 81.2% | 93.8% | 93.8% | 6.2% | 4/55/55 |

S0 mode P: KS(1) 25.8% / 187.5 | KS(3) 34.0% / 243.8 | KS(9) 42.7% / 306.2 (mean KS / net keys saved per 100 words); top-1 flip rate 29/94 = 30.9% (key pairs where both keys display)

#### U  mode P  (n=64)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 64 | 98.4% | 25.0% | 45.3% | 51.6% | 25.4% | 46.0% | 52.4% | 28.6% | 80/11406/13127 |
| P2 | 64 | 92.2% | 39.1% | 60.9% | 64.1% | 42.4% | 66.1% | 69.5% | 23.7% | 9/6613/13127 |
| P3 | 64 | 82.8% | 50.0% | 68.8% | 68.8% | 60.4% | 83.0% | 83.0% | 15.1% | 6/380/1157 |

#### U  mode PA  (n=64)
| pos | n | display | hit@1 | hit@5 | hit@9 | shown hit@1 | shown hit@5 | shown hit@9 | empty display | |C| p50/p95/max |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 64 | 98.4% | 25.0% | 45.3% | 51.6% | 25.4% | 46.0% | 52.4% | 28.6% | 80/11406/13127 |
| P2 | 64 | 92.2% | 39.1% | 60.9% | 64.1% | 42.4% | 66.1% | 69.5% | 23.7% | 9/6613/13127 |
| P3 | 64 | 82.8% | 50.0% | 68.8% | 68.8% | 60.4% | 83.0% | 83.0% | 15.1% | 6/380/1157 |
| A1 | 64 | 98.4% | 25.0% | 45.3% | 51.6% | 25.4% | 46.0% | 52.4% | 28.6% | 80/11406/13127 |
| A2 | 64 | 95.3% | 35.9% | 57.8% | 60.9% | 37.7% | 60.7% | 63.9% | 26.2% | 19/1610/13127 |
McNemar P1 hit@9, V3-P vs reference arm c (population M, n=75): V3-only=0 c-only=0 p=1; hit V3=18 c=18
McNemar P3 hit@9, V3-P vs reference arm c (population M, n=75): V3-only=1 c-only=12 p=0.003418; hit V3=37 c=48
query time (ms, 1422 position queries, C+score+top64; varies 20-30% between runs): p50=7.5 p95=66.3 max=187.8

## 第三片：後繼詞優先、其餘相容字串補在後面（`predict3.py`）

規格是 `docs/contracts/sp3-successor-first.md`。候選集合和第一片的 c 組相同（詞庫裡和查詢相容的全部字串）；**S** 把前文歷史詞 `v` 的後繼詞依分數排在前面，其餘相容字串依分數接在後面。S 的前段就是第二片的 V3。名次一律悲觀，S 第二層的名次加上第一層的個數。樣本、位置、母體與第二片相同，完整輸出表中的 V3 欄是第二片的 V3 在該表模式下的結果（P 表只用前綴解讀，PA 表是前綴與縮寫的聯集），同一批樣本重算。

```
python3 experiments/sp/test_predict3.py
python3 experiments/sp/predict3.py --rows eval/dev/user-typing.txt --set-name typing76 --profile chat --check
python3 experiments/sp/predict3.py --rows ~/.cache/shanjie/work/s2h/cvtail.txt --set-name cvtail --profile chat --sample 600 --seed 20261007
```
discordtune（私有，main 跑，只印統計數字）：`python3 experiments/sp/predict3.py --rows <private path> --set-name discordtune --profile chat --sample 1000 --seed 20261007 --mode both`

### 驗收紀錄

| 項目 | 結果 |
|---|---|
| 單元檢查（兩層、第二層名次、沒有後繼詞時等於 c） | `test_predict3.py` 3 個測試全過（exit 0） |
| 突變 (a) S 改成全部依分數排序 | exit 1：`test_tiers` 失敗（`test_second_tier_rank_adds_first_tier` 也失敗） |
| 突變 (b) 第二層名次不加第一層的個數 | exit 1：`test_second_tier_rank_adds_first_tier` 失敗 |
| 一致性（typing76 全部 438 個樣本，兩種設定） | 0 不符：S 與 c 的候選集合相同；S 前段的集合等於 `predict2.candidates` 的集合；前段分數逐位元相同 |
| 查詢時間（停止條件 p95 > 2 秒） | 公開集合 p95 最高 105 ms |

### 摘要（母體 M：兩字以上、`v ≠ <s>`）

hit@9 的分母是母體 M 的全部樣本。P1、P3 是 P 模式（只用前綴解讀），A1 是 PA 模式（前綴與縮寫的聯集）。McNemar 是 S 對 c（P1、P3、A1），以及 S 對 V3（P3），雙尾精確檢定。

| 集合 | 設定 | M | P1 hit@9 S／c | P3 hit@9 S／c／V3 | A1 hit@9 S／c（PA） | KS(9) S／c | 翻轉率 S／c | McNemar P1 p | McNemar P3 p | McNemar A1 p | P3 S 對 V3 p | 查詢 p50/p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cvtail | chat | 390 | 30.0%／25.4% | 67.2%／65.1%／56.2% | 81.5%／80.3% | 46.5%／45.0% | 34.2%／35.1% | 1.2e-4 | 0.0078 | 0.062 | 2.3e-13 | 7.0/84.8 |
| cvtail | formal | 390 | 33.3%／32.6% | 68.5%／67.7%／57.4% | 81.8%／80.8% | 47.4%／47.1% | 33.3%／33.6% | 0.38 | 0.25 | 0.12 | 2.3e-13 | 5.3/65.0 |
| wikitail | chat | 426 | 27.9%／20.9% | 65.5%／60.6%／59.4% | 78.9%／78.2% | 45.4%／42.9% | 36.0%／37.7% | 1.9e-9 | 9.5e-7 | 0.25 | 3.0e-8 | 7.2/86.5 |
| wikitail | formal | 426 | 32.6%／31.0% | 66.4%／65.3%／60.3% | 78.6%／78.4% | 46.5%／45.8% | 34.6%／34.8% | 0.016 | 0.062 | 1 | 3.0e-8 | 7.0/82.9 |
| typing76 | chat | 260 | 26.2%／23.1% | 68.5%／67.7%／50.0% | 80.8%／81.2% | 45.8%／44.8% | 36.9%／37.0% | 0.0078 | 0.62 | 1 | 7.1e-15 | 5.8/71.5 |
| typing76 | formal | 260 | 29.2%／28.5% | 68.5%／68.8%／50.0% | 80.8%／80.8% | 46.5%／46.4% | 35.9%／35.7% | 0.5 | 1 | 1 | 7.1e-15 | 7.7/95.5 |
| user-reported | chat | 75 | 21.3%／18.7% | 64.0%／64.0%／48.0% | 82.7%／84.0% | 41.1%／41.2% | 38.2%／38.9% | 0.5 | 1 | 1 | 4.9e-4 | 8.9/105.0 |
| user-reported | formal | 75 | 24.0%／24.0% | 65.3%／64.0%／49.3% | 82.7%／82.7% | 41.9%／42.2% | 37.3%／37.5% | 1 | 1 | 1 | 4.9e-4 | 6.1/64.3 |
| discordtune（私有，main 跑） | chat | 2,466 | 22.4%／21.3% | 63.9%／64.4%／43.5% | 81.0%／81.3% | 42.7%／42.5% | 37.6%／37.9% | 7.3e-4 | 0.13 | 0.44 | 3.8e-152 | 5.6/73.4 |

- **S 沒有一格顯著比 c 差**。公開集合的 8 格裡，三個 S 對 c 的檢定中 c 對、S 錯的樣本最多 2 個。
- **discordtune**（1,000 列抽樣、4,740 個樣本，母體 M 2,466）：P1 S 顯著較好（S 對、c 錯 44，c 對、S 錯 17）；P3 S 少 0.5 個百分點、不顯著（21 對 33）；A1 不顯著（18 對 24）。依排序規則，c 對、S 錯只會發生在正解不是後繼詞、分數卻高於部分後繼詞的時候：S 把那些後繼詞排在它前面，擠出前 9 名。
- **第一鍵（P1）**：S 在 4 格顯著較好（cvtail 聊天、wikitail 兩種設定、typing76 聊天），多 1.6–7.0 個百分點；其餘持平。
- **第一個音節打完（P3）**：S 在 cvtail 聊天、wikitail 聊天顯著較好，其餘持平。對 V3，S 在 8 格都顯著較好（多 6.1–18.5 個百分點）：第二片的 V3 在這裡較差，是因為正解不是後繼詞時給不出來，補上其餘相容字串後就補回來了。
- **縮寫（A1）**：S 與 c 沒有顯著差別。
- **KS(9)**：S 在 6 格略高（最多 2.5 個百分點），user-reported 低 0.1–0.3 個百分點。翻轉率 S 和 c 相差不到 2 個百分點。
- 查詢時間每次執行會差兩到三成，只當量級參考。

## 第三片各集合完整輸出

以下是 predict3.py 的原始輸出（只有統計數字）。

samples 599

### cvtail  chat
rows total=600; dropped: samples: W* has no lexicon entry for that reading=1 | samples kept=599; populations: M=390, S0=0, S1=0, U=209

#### M  mode P  (n=390)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 390 | 5.6% | 22.6% | 30.0% | 4.6% | 17.4% | 25.4% | 28.7% |
| P2 | 390 | 14.4% | 44.9% | 54.4% | 13.8% | 39.7% | 51.5% | 47.9% |
| P3 | 390 | 21.3% | 60.3% | 67.2% | 19.7% | 56.4% | 65.1% | 56.2% |
| P4 | 390 | 71.5% | 89.2% | 94.1% | 71.0% | 89.0% | 93.6% | 65.9% |

#### M  mode PA  (n=390)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 390 | 5.6% | 22.6% | 30.0% | 4.6% | 17.4% | 25.4% | 28.7% |
| P2 | 390 | 14.4% | 44.9% | 54.4% | 13.8% | 39.7% | 51.5% | 47.9% |
| P3 | 390 | 21.3% | 60.3% | 67.2% | 19.7% | 56.4% | 65.1% | 56.2% |
| P4 | 390 | 71.5% | 89.2% | 94.1% | 71.0% | 89.0% | 93.6% | 65.9% |
| A1 | 390 | 51.8% | 75.1% | 81.5% | 50.3% | 75.1% | 80.3% | 62.6% |
| A2 | 390 | 80.5% | 94.9% | 95.9% | 79.7% | 94.6% | 95.9% | 66.2% |
M S (mode P): KS(1) 23.9% / 180.5 | KS(3) 36.3% / 271.5 | KS(9) 46.5% / 341.8 (mean KS / net keys saved per 100 words); top-1 flip rate 812/2372 = 34.2%
M c (mode P): KS(1) 23.1% / 175.6 | KS(3) 34.8% / 261.0 | KS(9) 45.0% / 331.5 (mean KS / net keys saved per 100 words); top-1 flip rate 833/2372 = 35.1%

#### U  mode P  (n=209)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 209 | 49.8% | 76.6% | 78.0% | 50.7% | 74.6% | 78.9% | 69.4% |
| P2 | 209 | 69.9% | 90.0% | 93.3% | 69.4% | 90.0% | 94.3% | 78.5% |
| P3 | 209 | 85.2% | 94.7% | 98.6% | 84.7% | 95.2% | 98.1% | 80.4% |

#### U  mode PA  (n=209)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 209 | 49.8% | 76.6% | 78.0% | 50.7% | 74.6% | 78.9% | 69.4% |
| P2 | 209 | 69.9% | 90.0% | 93.3% | 69.4% | 90.0% | 94.3% | 78.5% |
| P3 | 209 | 85.2% | 94.7% | 98.6% | 84.7% | 95.2% | 98.1% | 80.4% |
| A1 | 209 | 49.8% | 76.6% | 78.0% | 50.7% | 74.6% | 78.9% | 69.4% |
| A2 | 209 | 69.4% | 88.0% | 91.4% | 68.9% | 88.0% | 91.9% | 76.6% |
McNemar P P1 hit@9, S vs c (population M, n=390): first-only=20 second-only=2 p=0.0001211; hit first=117 second=99
McNemar P P3 hit@9, S vs c (population M, n=390): first-only=8 second-only=0 p=0.007812; hit first=262 second=254
McNemar PA A1 hit@9, S vs c (population M, n=390): first-only=5 second-only=0 p=0.0625; hit first=318 second=313
McNemar P P3 hit@9, S vs V3 (population M, n=390): first-only=43 second-only=0 p=2.274e-13; hit first=262 second=219
query time (ms, 5572 position queries, candidates+score+tiers; varies 20-30% between runs): p50=7.0 p95=84.8 max=516.7

samples 599

### cvtail  formal
rows total=600; dropped: samples: W* has no lexicon entry for that reading=1 | samples kept=599; populations: M=390, S0=0, S1=0, U=209

#### M  mode P  (n=390)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 390 | 8.5% | 25.4% | 33.3% | 8.5% | 25.1% | 32.6% | 32.1% |
| P2 | 390 | 18.5% | 48.2% | 55.6% | 18.2% | 47.4% | 54.9% | 49.2% |
| P3 | 390 | 26.4% | 62.3% | 68.5% | 25.6% | 61.8% | 67.7% | 57.4% |
| P4 | 390 | 72.1% | 89.5% | 94.4% | 71.5% | 89.2% | 94.4% | 66.2% |

#### M  mode PA  (n=390)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 390 | 8.5% | 25.4% | 33.3% | 8.5% | 25.1% | 32.6% | 32.1% |
| P2 | 390 | 18.5% | 48.2% | 55.6% | 18.2% | 47.4% | 54.9% | 49.2% |
| P3 | 390 | 26.4% | 62.3% | 68.5% | 25.6% | 61.8% | 67.7% | 57.4% |
| P4 | 390 | 72.1% | 89.5% | 94.4% | 71.5% | 89.2% | 94.4% | 66.2% |
| A1 | 390 | 52.3% | 75.4% | 81.8% | 52.1% | 75.6% | 80.8% | 62.8% |
| A2 | 390 | 80.5% | 94.9% | 95.9% | 80.0% | 94.9% | 95.9% | 66.2% |
M S (mode P): KS(1) 26.0% / 195.6 | KS(3) 38.1% / 282.6 | KS(9) 47.4% / 348.2 (mean KS / net keys saved per 100 words); top-1 flip rate 790/2372 = 33.3%
M c (mode P): KS(1) 25.8% / 193.8 | KS(3) 37.8% / 280.5 | KS(9) 47.1% / 345.6 (mean KS / net keys saved per 100 words); top-1 flip rate 798/2372 = 33.6%

#### U  mode P  (n=209)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 209 | 52.6% | 76.6% | 78.9% | 53.6% | 77.0% | 79.9% | 70.3% |
| P2 | 209 | 70.8% | 90.0% | 93.8% | 70.3% | 90.0% | 94.3% | 78.9% |
| P3 | 209 | 85.2% | 94.7% | 98.6% | 85.2% | 94.7% | 98.6% | 80.4% |

#### U  mode PA  (n=209)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 209 | 52.6% | 76.6% | 78.9% | 53.6% | 77.0% | 79.9% | 70.3% |
| P2 | 209 | 70.8% | 90.0% | 93.8% | 70.3% | 90.0% | 94.3% | 78.9% |
| P3 | 209 | 85.2% | 94.7% | 98.6% | 85.2% | 94.7% | 98.6% | 80.4% |
| A1 | 209 | 52.6% | 76.6% | 78.9% | 53.6% | 77.0% | 79.9% | 70.3% |
| A2 | 209 | 70.8% | 88.0% | 91.9% | 70.3% | 88.5% | 91.9% | 77.0% |
McNemar P P1 hit@9, S vs c (population M, n=390): first-only=4 second-only=1 p=0.375; hit first=130 second=127
McNemar P P3 hit@9, S vs c (population M, n=390): first-only=3 second-only=0 p=0.25; hit first=267 second=264
McNemar PA A1 hit@9, S vs c (population M, n=390): first-only=4 second-only=0 p=0.125; hit first=319 second=315
McNemar P P3 hit@9, S vs V3 (population M, n=390): first-only=43 second-only=0 p=2.274e-13; hit first=267 second=224
query time (ms, 5572 position queries, candidates+score+tiers; varies 20-30% between runs): p50=5.3 p95=65.0 max=283.2

samples 600

### wikitail  chat
rows total=600; dropped: none | samples kept=600; populations: M=426, S0=0, S1=0, U=174

#### M  mode P  (n=426)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 426 | 5.9% | 20.4% | 27.9% | 4.0% | 15.5% | 20.9% | 27.9% |
| P2 | 426 | 20.7% | 43.2% | 53.1% | 17.4% | 37.3% | 47.2% | 50.2% |
| P3 | 426 | 26.5% | 58.5% | 65.5% | 23.7% | 52.3% | 60.6% | 59.4% |
| P4 | 426 | 67.1% | 87.3% | 92.7% | 65.3% | 87.1% | 92.7% | 66.9% |

#### M  mode PA  (n=426)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 426 | 5.9% | 20.4% | 27.9% | 4.0% | 15.5% | 20.9% | 27.9% |
| P2 | 426 | 20.7% | 43.2% | 53.1% | 17.4% | 37.3% | 47.2% | 50.2% |
| P3 | 426 | 26.5% | 58.5% | 65.5% | 23.7% | 52.3% | 60.6% | 59.4% |
| P4 | 426 | 67.1% | 87.3% | 92.7% | 65.3% | 87.1% | 92.7% | 66.9% |
| A1 | 426 | 51.6% | 72.3% | 78.9% | 49.1% | 70.2% | 78.2% | 64.3% |
| A2 | 426 | 75.4% | 92.5% | 96.0% | 74.4% | 92.5% | 95.8% | 67.1% |
M S (mode P): KS(1) 26.0% / 203.3 | KS(3) 36.6% / 284.5 | KS(9) 45.4% / 347.7 (mean KS / net keys saved per 100 words); top-1 flip rate 971/2698 = 36.0%
M c (mode P): KS(1) 24.4% / 191.1 | KS(3) 34.2% / 266.4 | KS(9) 42.9% / 329.3 (mean KS / net keys saved per 100 words); top-1 flip rate 1018/2698 = 37.7%

#### U  mode P  (n=174)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 174 | 43.1% | 67.8% | 76.4% | 39.1% | 64.9% | 73.0% | 71.3% |
| P2 | 174 | 69.5% | 87.4% | 90.8% | 67.8% | 86.8% | 89.1% | 81.0% |
| P3 | 174 | 82.2% | 94.3% | 97.7% | 82.8% | 94.3% | 97.7% | 85.1% |

#### U  mode PA  (n=174)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 174 | 43.1% | 67.8% | 76.4% | 39.1% | 64.9% | 73.0% | 71.3% |
| P2 | 174 | 69.5% | 87.4% | 90.8% | 67.8% | 86.8% | 89.1% | 81.0% |
| P3 | 174 | 82.2% | 94.3% | 97.7% | 82.8% | 94.3% | 97.7% | 85.1% |
| A1 | 174 | 43.1% | 67.8% | 76.4% | 39.1% | 64.9% | 73.0% | 71.3% |
| A2 | 174 | 62.1% | 84.5% | 86.8% | 59.2% | 82.8% | 85.1% | 79.3% |
McNemar P P1 hit@9, S vs c (population M, n=426): first-only=30 second-only=0 p=1.863e-09; hit first=119 second=89
McNemar P P3 hit@9, S vs c (population M, n=426): first-only=21 second-only=0 p=9.537e-07; hit first=279 second=258
McNemar PA A1 hit@9, S vs c (population M, n=426): first-only=3 second-only=0 p=0.25; hit first=336 second=333
McNemar P P3 hit@9, S vs V3 (population M, n=426): first-only=26 second-only=0 p=2.98e-08; hit first=279 second=253
query time (ms, 5652 position queries, candidates+score+tiers; varies 20-30% between runs): p50=7.2 p95=86.5 max=404.4

samples 600

### wikitail  formal
rows total=600; dropped: none | samples kept=600; populations: M=426, S0=0, S1=0, U=174

#### M  mode P  (n=426)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 426 | 9.6% | 23.9% | 32.6% | 9.2% | 23.2% | 31.0% | 32.6% |
| P2 | 426 | 23.9% | 45.5% | 54.9% | 23.0% | 44.4% | 52.3% | 52.1% |
| P3 | 426 | 30.0% | 59.6% | 66.4% | 28.9% | 58.0% | 65.3% | 60.3% |
| P4 | 426 | 67.6% | 87.6% | 93.0% | 67.4% | 87.6% | 93.0% | 67.1% |

#### M  mode PA  (n=426)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 426 | 9.6% | 23.9% | 32.6% | 9.2% | 23.2% | 31.0% | 32.6% |
| P2 | 426 | 23.9% | 45.5% | 54.9% | 23.0% | 44.4% | 52.3% | 52.1% |
| P3 | 426 | 30.0% | 59.6% | 66.4% | 28.9% | 58.0% | 65.3% | 60.3% |
| P4 | 426 | 67.6% | 87.6% | 93.0% | 67.4% | 87.6% | 93.0% | 67.1% |
| A1 | 426 | 52.6% | 72.8% | 78.6% | 51.9% | 72.5% | 78.4% | 64.1% |
| A2 | 426 | 75.1% | 92.5% | 96.0% | 74.9% | 92.5% | 95.8% | 67.1% |
M S (mode P): KS(1) 27.7% / 215.3 | KS(3) 38.6% / 298.6 | KS(9) 46.5% / 355.4 (mean KS / net keys saved per 100 words); top-1 flip rate 933/2698 = 34.6%
M c (mode P): KS(1) 27.3% / 212.0 | KS(3) 38.0% / 294.1 | KS(9) 45.8% / 350.5 (mean KS / net keys saved per 100 words); top-1 flip rate 940/2698 = 34.8%

#### U  mode P  (n=174)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 174 | 43.7% | 70.1% | 77.6% | 42.0% | 69.0% | 77.6% | 72.4% |
| P2 | 174 | 70.7% | 87.9% | 92.5% | 69.5% | 89.7% | 92.5% | 82.8% |
| P3 | 174 | 82.8% | 95.4% | 97.7% | 83.9% | 96.0% | 97.7% | 85.1% |

#### U  mode PA  (n=174)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 174 | 43.7% | 70.1% | 77.6% | 42.0% | 69.0% | 77.6% | 72.4% |
| P2 | 174 | 70.7% | 87.9% | 92.5% | 69.5% | 89.7% | 92.5% | 82.8% |
| P3 | 174 | 82.8% | 95.4% | 97.7% | 83.9% | 96.0% | 97.7% | 85.1% |
| A1 | 174 | 43.7% | 70.1% | 77.6% | 42.0% | 69.0% | 77.6% | 72.4% |
| A2 | 174 | 66.1% | 85.1% | 89.1% | 63.8% | 86.2% | 89.7% | 81.6% |
McNemar P P1 hit@9, S vs c (population M, n=426): first-only=7 second-only=0 p=0.01562; hit first=139 second=132
McNemar P P3 hit@9, S vs c (population M, n=426): first-only=5 second-only=0 p=0.0625; hit first=283 second=278
McNemar PA A1 hit@9, S vs c (population M, n=426): first-only=1 second-only=0 p=1; hit first=335 second=334
McNemar P P3 hit@9, S vs V3 (population M, n=426): first-only=26 second-only=0 p=2.98e-08; hit first=283 second=257
query time (ms, 5652 position queries, candidates+score+tiers; varies 20-30% between runs): p50=7.0 p95=82.9 max=778.4

samples 438; consistency: 0 mismatches

### typing76  chat
rows total=76; dropped: samples: W* has no lexicon entry for that reading=2 | samples kept=438; populations: M=260, S0=12, S1=0, U=166

#### M  mode P  (n=260)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 260 | 3.5% | 20.0% | 26.2% | 2.3% | 17.3% | 23.1% | 24.2% |
| P2 | 260 | 13.5% | 41.9% | 54.6% | 12.7% | 39.2% | 52.3% | 43.1% |
| P3 | 260 | 21.9% | 60.4% | 68.5% | 20.8% | 58.5% | 67.7% | 50.0% |
| P4 | 260 | 71.2% | 93.8% | 96.5% | 71.5% | 94.2% | 96.5% | 53.1% |

#### M  mode PA  (n=260)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 260 | 3.5% | 20.0% | 26.2% | 2.3% | 17.3% | 23.1% | 24.2% |
| P2 | 260 | 13.5% | 41.9% | 54.6% | 12.7% | 39.2% | 52.3% | 43.1% |
| P3 | 260 | 21.9% | 60.4% | 68.5% | 20.8% | 58.5% | 67.7% | 50.0% |
| P4 | 260 | 71.2% | 93.8% | 96.5% | 71.5% | 94.2% | 96.5% | 53.1% |
| A1 | 260 | 46.9% | 75.4% | 80.8% | 48.1% | 75.4% | 81.2% | 52.7% |
| A2 | 260 | 80.4% | 95.0% | 97.7% | 80.8% | 95.0% | 97.7% | 53.5% |
M S (mode P): KS(1) 22.6% / 158.8 | KS(3) 36.1% / 254.6 | KS(9) 45.8% / 320.8 (mean KS / net keys saved per 100 words); top-1 flip rate 561/1520 = 36.9%
M c (mode P): KS(1) 22.3% / 156.9 | KS(3) 35.4% / 250.4 | KS(9) 44.8% / 314.2 (mean KS / net keys saved per 100 words); top-1 flip rate 563/1520 = 37.0%

#### S0  mode P  (n=12)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 12 | 0.0% | 16.7% | 16.7% | 0.0% | 16.7% | 16.7% | 16.7% |
| P2 | 12 | 0.0% | 50.0% | 66.7% | 0.0% | 50.0% | 66.7% | 66.7% |
| P3 | 12 | 0.0% | 50.0% | 66.7% | 0.0% | 50.0% | 66.7% | 66.7% |
| P4 | 12 | 66.7% | 91.7% | 91.7% | 66.7% | 91.7% | 91.7% | 91.7% |

#### S0  mode PA  (n=12)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 12 | 0.0% | 16.7% | 16.7% | 0.0% | 16.7% | 16.7% | 16.7% |
| P2 | 12 | 0.0% | 50.0% | 66.7% | 0.0% | 50.0% | 66.7% | 66.7% |
| P3 | 12 | 0.0% | 50.0% | 66.7% | 0.0% | 50.0% | 66.7% | 66.7% |
| P4 | 12 | 66.7% | 91.7% | 91.7% | 66.7% | 91.7% | 91.7% | 91.7% |
| A1 | 12 | 33.3% | 75.0% | 91.7% | 33.3% | 75.0% | 91.7% | 91.7% |
| A2 | 12 | 91.7% | 91.7% | 100.0% | 91.7% | 91.7% | 100.0% | 100.0% |
S0 S (mode P): KS(1) 15.2% / 108.3 | KS(3) 34.6% / 241.7 | KS(9) 45.0% / 308.3 (mean KS / net keys saved per 100 words); top-1 flip rate 24/69 = 34.8%
S0 c (mode P): KS(1) 15.2% / 108.3 | KS(3) 34.6% / 241.7 | KS(9) 45.0% / 308.3 (mean KS / net keys saved per 100 words); top-1 flip rate 24/69 = 34.8%

#### U  mode P  (n=166)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 166 | 40.4% | 69.3% | 72.9% | 41.0% | 68.1% | 72.9% | 70.5% |
| P2 | 166 | 73.5% | 93.4% | 94.0% | 73.5% | 93.4% | 94.0% | 81.9% |
| P3 | 166 | 85.5% | 98.2% | 99.4% | 89.2% | 98.2% | 99.4% | 81.9% |

#### U  mode PA  (n=166)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 166 | 40.4% | 69.3% | 72.9% | 41.0% | 68.1% | 72.9% | 70.5% |
| P2 | 166 | 73.5% | 93.4% | 94.0% | 73.5% | 93.4% | 94.0% | 81.9% |
| P3 | 166 | 85.5% | 98.2% | 99.4% | 89.2% | 98.2% | 99.4% | 81.9% |
| A1 | 166 | 40.4% | 69.3% | 72.9% | 41.0% | 68.1% | 72.9% | 70.5% |
| A2 | 166 | 69.3% | 88.0% | 91.0% | 69.3% | 88.0% | 90.4% | 80.7% |
McNemar P P1 hit@9, S vs c (population M, n=260): first-only=8 second-only=0 p=0.007812; hit first=68 second=60
McNemar P P3 hit@9, S vs c (population M, n=260): first-only=3 second-only=1 p=0.625; hit first=178 second=176
McNemar PA A1 hit@9, S vs c (population M, n=260): first-only=0 second-only=1 p=1; hit first=210 second=211
McNemar P P3 hit@9, S vs V3 (population M, n=260): first-only=48 second-only=0 p=7.105e-15; hit first=178 second=130
query time (ms, 4048 position queries, candidates+score+tiers; varies 20-30% between runs): p50=5.8 p95=71.5 max=262.8

samples 438; consistency: 0 mismatches

### typing76  formal
rows total=76; dropped: samples: W* has no lexicon entry for that reading=2 | samples kept=438; populations: M=260, S0=12, S1=0, U=166

#### M  mode P  (n=260)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 260 | 6.2% | 21.2% | 29.2% | 5.8% | 20.4% | 28.5% | 27.3% |
| P2 | 260 | 16.9% | 41.2% | 55.8% | 16.5% | 40.8% | 55.0% | 44.2% |
| P3 | 260 | 23.8% | 60.0% | 68.5% | 23.8% | 60.4% | 68.8% | 50.0% |
| P4 | 260 | 70.8% | 93.8% | 96.5% | 71.2% | 94.2% | 96.5% | 53.1% |

#### M  mode PA  (n=260)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 260 | 6.2% | 21.2% | 29.2% | 5.8% | 20.4% | 28.5% | 27.3% |
| P2 | 260 | 16.9% | 41.2% | 55.8% | 16.5% | 40.8% | 55.0% | 44.2% |
| P3 | 260 | 23.8% | 60.0% | 68.5% | 23.8% | 60.4% | 68.8% | 50.0% |
| P4 | 260 | 70.8% | 93.8% | 96.5% | 71.2% | 94.2% | 96.5% | 53.1% |
| A1 | 260 | 49.6% | 75.0% | 80.8% | 51.2% | 75.0% | 80.8% | 52.7% |
| A2 | 260 | 80.4% | 95.0% | 97.7% | 80.4% | 95.0% | 97.7% | 53.5% |
M S (mode P): KS(1) 23.9% / 167.3 | KS(3) 36.7% / 258.1 | KS(9) 46.5% / 325.0 (mean KS / net keys saved per 100 words); top-1 flip rate 545/1520 = 35.9%
M c (mode P): KS(1) 23.9% / 167.3 | KS(3) 36.4% / 256.2 | KS(9) 46.4% / 324.6 (mean KS / net keys saved per 100 words); top-1 flip rate 542/1520 = 35.7%

#### S0  mode P  (n=12)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 12 | 0.0% | 16.7% | 16.7% | 0.0% | 16.7% | 16.7% | 16.7% |
| P2 | 12 | 0.0% | 58.3% | 66.7% | 0.0% | 58.3% | 66.7% | 66.7% |
| P3 | 12 | 0.0% | 58.3% | 66.7% | 0.0% | 58.3% | 66.7% | 66.7% |
| P4 | 12 | 66.7% | 91.7% | 91.7% | 66.7% | 91.7% | 91.7% | 91.7% |

#### S0  mode PA  (n=12)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 12 | 0.0% | 16.7% | 16.7% | 0.0% | 16.7% | 16.7% | 16.7% |
| P2 | 12 | 0.0% | 58.3% | 66.7% | 0.0% | 58.3% | 66.7% | 66.7% |
| P3 | 12 | 0.0% | 58.3% | 66.7% | 0.0% | 58.3% | 66.7% | 66.7% |
| P4 | 12 | 66.7% | 91.7% | 91.7% | 66.7% | 91.7% | 91.7% | 91.7% |
| A1 | 12 | 58.3% | 75.0% | 91.7% | 58.3% | 75.0% | 91.7% | 91.7% |
| A2 | 12 | 91.7% | 91.7% | 100.0% | 91.7% | 91.7% | 100.0% | 100.0% |
S0 S (mode P): KS(1) 15.2% / 108.3 | KS(3) 32.5% / 225.0 | KS(9) 45.0% / 308.3 (mean KS / net keys saved per 100 words); top-1 flip rate 24/69 = 34.8%
S0 c (mode P): KS(1) 15.2% / 108.3 | KS(3) 32.5% / 225.0 | KS(9) 45.0% / 308.3 (mean KS / net keys saved per 100 words); top-1 flip rate 24/69 = 34.8%

#### U  mode P  (n=166)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 166 | 45.2% | 69.9% | 74.1% | 45.2% | 69.9% | 74.7% | 71.7% |
| P2 | 166 | 74.7% | 94.0% | 94.0% | 75.3% | 94.0% | 94.0% | 81.9% |
| P3 | 166 | 84.9% | 98.2% | 99.4% | 86.1% | 98.2% | 99.4% | 81.9% |

#### U  mode PA  (n=166)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 166 | 45.2% | 69.9% | 74.1% | 45.2% | 69.9% | 74.7% | 71.7% |
| P2 | 166 | 74.7% | 94.0% | 94.0% | 75.3% | 94.0% | 94.0% | 81.9% |
| P3 | 166 | 84.9% | 98.2% | 99.4% | 86.1% | 98.2% | 99.4% | 81.9% |
| A1 | 166 | 45.2% | 69.9% | 74.1% | 45.2% | 69.9% | 74.7% | 71.7% |
| A2 | 166 | 70.5% | 88.6% | 91.0% | 70.5% | 88.6% | 90.4% | 80.7% |
McNemar P P1 hit@9, S vs c (population M, n=260): first-only=2 second-only=0 p=0.5; hit first=76 second=74
McNemar P P3 hit@9, S vs c (population M, n=260): first-only=0 second-only=1 p=1; hit first=178 second=179
McNemar PA A1 hit@9, S vs c (population M, n=260): first-only=0 second-only=0 p=1; hit first=210 second=210
McNemar P P3 hit@9, S vs V3 (population M, n=260): first-only=48 second-only=0 p=7.105e-15; hit first=178 second=130
query time (ms, 4048 position queries, candidates+score+tiers; varies 20-30% between runs): p50=7.7 p95=95.5 max=516.7

samples 155

### user-reported  chat
rows total=34; dropped: samples: W* has no lexicon entry for that reading=3 | samples kept=155; populations: M=75, S0=16, S1=0, U=64

#### M  mode P  (n=75)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 75 | 6.7% | 17.3% | 21.3% | 6.7% | 14.7% | 18.7% | 21.3% |
| P2 | 75 | 14.7% | 36.0% | 41.3% | 14.7% | 34.7% | 44.0% | 37.3% |
| P3 | 75 | 26.7% | 50.7% | 64.0% | 25.3% | 52.0% | 64.0% | 48.0% |
| P4 | 75 | 69.3% | 93.3% | 94.7% | 69.3% | 93.3% | 94.7% | 54.7% |

#### M  mode PA  (n=75)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 75 | 6.7% | 17.3% | 21.3% | 6.7% | 14.7% | 18.7% | 21.3% |
| P2 | 75 | 14.7% | 36.0% | 41.3% | 14.7% | 34.7% | 44.0% | 37.3% |
| P3 | 75 | 26.7% | 50.7% | 64.0% | 25.3% | 52.0% | 64.0% | 48.0% |
| P4 | 75 | 69.3% | 93.3% | 94.7% | 69.3% | 93.3% | 94.7% | 54.7% |
| A1 | 75 | 52.0% | 72.0% | 82.7% | 50.7% | 70.7% | 84.0% | 53.3% |
| A2 | 75 | 69.3% | 96.0% | 96.0% | 69.3% | 96.0% | 96.0% | 54.7% |
M S (mode P): KS(1) 21.9% / 149.3 | KS(3) 33.2% / 229.3 | KS(9) 41.1% / 282.7 (mean KS / net keys saved per 100 words); top-1 flip rate 167/437 = 38.2%
M c (mode P): KS(1) 21.7% / 148.0 | KS(3) 32.6% / 225.3 | KS(9) 41.2% / 282.7 (mean KS / net keys saved per 100 words); top-1 flip rate 170/437 = 38.9%

#### S0  mode P  (n=16)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 16 | 6.2% | 12.5% | 18.8% | 6.2% | 12.5% | 18.8% | 18.8% |
| P2 | 16 | 12.5% | 37.5% | 43.8% | 12.5% | 37.5% | 43.8% | 43.8% |
| P3 | 16 | 18.8% | 43.8% | 56.2% | 18.8% | 43.8% | 56.2% | 56.2% |
| P4 | 16 | 75.0% | 81.2% | 87.5% | 81.2% | 87.5% | 93.8% | 87.5% |

#### S0  mode PA  (n=16)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 16 | 6.2% | 12.5% | 18.8% | 6.2% | 12.5% | 18.8% | 18.8% |
| P2 | 16 | 12.5% | 37.5% | 43.8% | 12.5% | 37.5% | 43.8% | 43.8% |
| P3 | 16 | 18.8% | 43.8% | 56.2% | 18.8% | 43.8% | 56.2% | 56.2% |
| P4 | 16 | 75.0% | 81.2% | 87.5% | 81.2% | 87.5% | 93.8% | 87.5% |
| A1 | 16 | 62.5% | 87.5% | 87.5% | 62.5% | 87.5% | 93.8% | 87.5% |
| A2 | 16 | 81.2% | 100.0% | 100.0% | 87.5% | 100.0% | 100.0% | 93.8% |
S0 S (mode P): KS(1) 25.8% / 187.5 | KS(3) 34.0% / 243.8 | KS(9) 42.7% / 306.2 (mean KS / net keys saved per 100 words); top-1 flip rate 30/96 = 31.2%
S0 c (mode P): KS(1) 26.9% / 193.8 | KS(3) 35.0% / 250.0 | KS(9) 43.8% / 312.5 (mean KS / net keys saved per 100 words); top-1 flip rate 29/96 = 30.2%

#### U  mode P  (n=64)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 64 | 25.0% | 45.3% | 56.2% | 25.0% | 45.3% | 54.7% | 53.1% |
| P2 | 64 | 45.3% | 75.0% | 82.8% | 46.9% | 78.1% | 85.9% | 64.1% |
| P3 | 64 | 62.5% | 89.1% | 95.3% | 62.5% | 92.2% | 95.3% | 68.8% |

#### U  mode PA  (n=64)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 64 | 25.0% | 45.3% | 56.2% | 25.0% | 45.3% | 54.7% | 53.1% |
| P2 | 64 | 45.3% | 75.0% | 82.8% | 46.9% | 78.1% | 85.9% | 64.1% |
| P3 | 64 | 62.5% | 89.1% | 95.3% | 62.5% | 92.2% | 95.3% | 68.8% |
| A1 | 64 | 25.0% | 45.3% | 56.2% | 25.0% | 45.3% | 54.7% | 53.1% |
| A2 | 64 | 39.1% | 67.2% | 76.6% | 39.1% | 70.3% | 79.7% | 62.5% |
McNemar P P1 hit@9, S vs c (population M, n=75): first-only=2 second-only=0 p=0.5; hit first=16 second=14
McNemar P P3 hit@9, S vs c (population M, n=75): first-only=0 second-only=0 p=1; hit first=48 second=48
McNemar PA A1 hit@9, S vs c (population M, n=75): first-only=0 second-only=1 p=1; hit first=62 second=63
McNemar P P3 hit@9, S vs V3 (population M, n=75): first-only=12 second-only=0 p=0.0004883; hit first=48 second=36
query time (ms, 1422 position queries, candidates+score+tiers; varies 20-30% between runs): p50=8.9 p95=105.0 max=407.1

samples 155

### user-reported  formal
rows total=34; dropped: samples: W* has no lexicon entry for that reading=3 | samples kept=155; populations: M=75, S0=16, S1=0, U=64

#### M  mode P  (n=75)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 75 | 5.3% | 20.0% | 24.0% | 5.3% | 18.7% | 24.0% | 24.0% |
| P2 | 75 | 17.3% | 36.0% | 42.7% | 17.3% | 36.0% | 45.3% | 38.7% |
| P3 | 75 | 26.7% | 50.7% | 65.3% | 26.7% | 50.7% | 64.0% | 49.3% |
| P4 | 75 | 69.3% | 93.3% | 94.7% | 69.3% | 93.3% | 94.7% | 54.7% |

#### M  mode PA  (n=75)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 75 | 5.3% | 20.0% | 24.0% | 5.3% | 18.7% | 24.0% | 24.0% |
| P2 | 75 | 17.3% | 36.0% | 42.7% | 17.3% | 36.0% | 45.3% | 38.7% |
| P3 | 75 | 26.7% | 50.7% | 65.3% | 26.7% | 50.7% | 64.0% | 49.3% |
| P4 | 75 | 69.3% | 93.3% | 94.7% | 69.3% | 93.3% | 94.7% | 54.7% |
| A1 | 75 | 50.7% | 72.0% | 82.7% | 50.7% | 72.0% | 82.7% | 53.3% |
| A2 | 75 | 69.3% | 96.0% | 96.0% | 69.3% | 96.0% | 96.0% | 54.7% |
M S (mode P): KS(1) 22.2% / 152.0 | KS(3) 34.1% / 234.7 | KS(9) 41.9% / 288.0 (mean KS / net keys saved per 100 words); top-1 flip rate 163/437 = 37.3%
M c (mode P): KS(1) 22.0% / 150.7 | KS(3) 33.7% / 232.0 | KS(9) 42.2% / 289.3 (mean KS / net keys saved per 100 words); top-1 flip rate 164/437 = 37.5%

#### S0  mode P  (n=16)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 16 | 6.2% | 18.8% | 18.8% | 6.2% | 18.8% | 18.8% | 18.8% |
| P2 | 16 | 12.5% | 37.5% | 43.8% | 12.5% | 37.5% | 43.8% | 43.8% |
| P3 | 16 | 18.8% | 43.8% | 56.2% | 18.8% | 43.8% | 56.2% | 56.2% |
| P4 | 16 | 75.0% | 81.2% | 87.5% | 75.0% | 87.5% | 93.8% | 87.5% |

#### S0  mode PA  (n=16)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 16 | 6.2% | 18.8% | 18.8% | 6.2% | 18.8% | 18.8% | 18.8% |
| P2 | 16 | 12.5% | 37.5% | 43.8% | 12.5% | 37.5% | 43.8% | 43.8% |
| P3 | 16 | 18.8% | 43.8% | 56.2% | 18.8% | 43.8% | 56.2% | 56.2% |
| P4 | 16 | 75.0% | 81.2% | 87.5% | 75.0% | 87.5% | 93.8% | 87.5% |
| A1 | 16 | 62.5% | 87.5% | 87.5% | 62.5% | 87.5% | 87.5% | 87.5% |
| A2 | 16 | 81.2% | 100.0% | 100.0% | 81.2% | 100.0% | 100.0% | 93.8% |
S0 S (mode P): KS(1) 25.8% / 187.5 | KS(3) 34.0% / 243.8 | KS(9) 42.7% / 306.2 (mean KS / net keys saved per 100 words); top-1 flip rate 30/96 = 31.2%
S0 c (mode P): KS(1) 25.8% / 187.5 | KS(3) 35.0% / 250.0 | KS(9) 43.8% / 312.5 (mean KS / net keys saved per 100 words); top-1 flip rate 30/96 = 31.2%

#### U  mode P  (n=64)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 64 | 25.0% | 45.3% | 54.7% | 25.0% | 45.3% | 54.7% | 51.6% |
| P2 | 64 | 42.2% | 75.0% | 82.8% | 43.8% | 76.6% | 84.4% | 64.1% |
| P3 | 64 | 60.9% | 89.1% | 95.3% | 60.9% | 90.6% | 95.3% | 68.8% |

#### U  mode PA  (n=64)
| pos | n | S@1 | S@5 | S@9 | c@1 | c@5 | c@9 | V3@9 |
|---|---|---|---|---|---|---|---|---|
| P1 | 64 | 25.0% | 45.3% | 54.7% | 25.0% | 45.3% | 54.7% | 51.6% |
| P2 | 64 | 42.2% | 75.0% | 82.8% | 43.8% | 76.6% | 84.4% | 64.1% |
| P3 | 64 | 60.9% | 89.1% | 95.3% | 60.9% | 90.6% | 95.3% | 68.8% |
| A1 | 64 | 25.0% | 45.3% | 54.7% | 25.0% | 45.3% | 54.7% | 51.6% |
| A2 | 64 | 37.5% | 67.2% | 75.0% | 37.5% | 67.2% | 76.6% | 60.9% |
McNemar P P1 hit@9, S vs c (population M, n=75): first-only=0 second-only=0 p=1; hit first=18 second=18
McNemar P P3 hit@9, S vs c (population M, n=75): first-only=1 second-only=0 p=1; hit first=49 second=48
McNemar PA A1 hit@9, S vs c (population M, n=75): first-only=0 second-only=0 p=1; hit first=62 second=62
McNemar P P3 hit@9, S vs V3 (population M, n=75): first-only=12 second-only=0 p=0.0004883; hit first=49 second=37
query time (ms, 1422 position queries, candidates+score+tiers; varies 20-30% between runs): p50=6.1 p95=64.3 max=154.1
