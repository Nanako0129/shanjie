# S2 實驗：n-gram 語言模型（Python 原型，2026-10-03）

在原型解碼上加 bigram，量各評測集的 top-1。語料只在本機 `~/.cache/shanjie/` 訓練用，不散布原文；計數檔也不進 repo。

## 語料

| 來源 | 授權 | 規模 | 程式 |
|---|---|---|---|
| 中文維基 2026-10-01 前 5 萬篇條目（sha1 和官方相符），用 OpenCC 轉台灣繁體 | CC BY-SA 4.0 | 5,697 萬詞次 | `build_counts.py` |
| Mozilla Common Voice zh-TW 句子（commit ff32a0e） | CC0 | 1.9 萬句 | `build_counts_text.py` |
| Tatoeba cmn 例句 | CC BY 2.0 FR | 8.9 萬句 | 同上 |
| Cerebras gpt-oss-120b（Apache-2.0 開放權重）合成的台灣口語聊天句；提示詞只寫主題與語氣，不放評測句 | 模型輸出 | 13.5 萬句（花費約 US$9，用偏高單價估算，上限是使用者核准的 US$10） | `synth_colloquial.py` |

口語三個來源合計 182 萬詞次。

## 方法

`eval_bigram.py`、`eval_mix.py`：透過原型的 learner 掛勾加分，加分＝λ·log10(P_bigram(w|v)/P_uni(w))。P_bigram 是 absolute discounting 插值（D=0.75），語料沒見過的詞不加分。口語計數乘上 k 之後再和維基相加。解碼用 `decode(beam=64)`，看第一名；Discord 與新聞稿的句子在本機私有檔，只印數字。

## 結果（λ=0.5，第一名寬鬆正確）

| 語料 | dev 302 | 打字測驗 76 | Discord 845 | 新聞稿 294 |
|---|---|---|---|---|
| 無 bigram | 163 | 44 | 648 | 213 |
| 維基 | 212 | 58 | 664 | 236 |
| 維基＋口語（Common Voice、Tatoeba），k=20 | 219 | 58 | 669 | 234 |
| **維基＋口語（加合成句），k=5** | **231** | **65** | **685** | 235 |
| 維基＋口語（加合成句），k=10 | 230 | 65 | 680 | 234 |
| 維基＋口語（加合成句），k=20 | 227 | 63 | 672 | 233 |

- λ 掃描（只用維基）：0.5 最佳；1.0 以上開始變差，2.0 甚至低於不加 bigram。
- 比較基準：macOS 內建在 Discord 錯 100 句、新聞稿錯 64 句。它學過使用者平常的習慣，也在測驗中學到測驗句，見 `docs/typing-test.md`。
- 「無 bigram」那一列的 Discord 和新聞稿數字，是用寬鬆標準重算的 top-1。

## 方法上的更正（2026-10-03，採納 Fable 5.1 的審查意見）

- 口語權重 k 是看著 Discord 和新聞稿的數字挑的，所以這兩組已經參與了選模，不能再當成獨立的驗證集。
- 「維基＋口語（加合成句）」沒有對照組（不含合成句、k=5），所以 k 的效果和合成句的效果混在一起。
- 新聞稿 235 對 macOS 內建 230（錯 59 對 64）的差距在雜訊內，只能說打平。
- `iterate.py` 的錯字統計以字計數，而且只算長度相同的輸出；「嚐→常 ×8」其實是 4 句。
- `order: 3` 目前會靜默退化成 bigram，因為計數檔沒有 trigram。
- 加分公式混用兩套 unigram，句尾的 `</s>` 有統計但解碼時沒有計分；這是下一步要修的地方。

## S2n：簡體句轉換與口語計數的輸入（2026-10-05）

- `counts-colloquial3.pkl` 的輸入是 `colloquial-train.txt`（`build_tune.py --part cv` 產生）加 `synth.txt`，各 ×1；用現行轉換重建後 uni／bi／tri／runs 與舊檔逐項相同（S1 版或現行疊加層都相同）。`synth-targeted.txt(.ok)` 不在內。舊的 `colloquial-train.txt` 是修掉 吃→喫 之前產生的。
- `build_counts.py` 的 `convert` 先判斷簡體句（契約 §2.1），簡體句走全部 STPhrases、第一個對照、台灣用字（`TAIWAN_KEEP` 例外），繁體句維持只轉簡體專用字。`test_convert.py` 是固定字串檢查；`experiments/s2n/` 放挑例外字（`pick_exceptions.py`）、突變（`mutations.py`）、語料量測（`measure_residue.py`）、評測（`evaluate.py`）與模型表面字串量測（`model_surfaces.py`）。
- 重建用 `S2_WORK=<目錄>` 避免蓋掉舊計數；維基 20 萬篇約 41 ms／篇（單程序），在多核機器上用 `--procs`。數字見 `docs/research-log.md` 2026-10-05 S2n。
