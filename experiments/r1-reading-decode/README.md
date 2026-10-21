# R1：解碼時用讀音算機率（探索；2026-10-22 登記）

起因（維護者 2026-10-22）：「變快這題我覺得不算是進步而是修正，這個應該是我們的n-gram有問題，不應該靠常談解決」「上次說到應該用讀音判斷機率或許是根本解」。選擇題：先做這片，字幕檢查（SUB-0，`exp/sub0-subtitles`）暫停。

問題：n-gram 只看字，不看讀音。「否則便會」的「會」讀ㄎㄨㄞˋ幾乎只出現在「會計」，n-gram 卻照「便會」這個字串的次數給分。v0.5.0 的讀音通道項（P5）在融合那一層修了這種錯，但只在常談生效時才加（聊天、標準強度、配對的會意、常談表已載入）；書面、保守、積極都沒有。

做法的方向：noisy channel。候選的分數 = 這串字的機率 × 這些字讀成打的音的機率，在每個設定、每個強度都算。這一頁在任何 R1 的數字之前 commit。

修訂一（2026-10-22，任何數字之前；plan-verifier 第一次 REVISE）：每個設定的 CLI 指令、雜湊與摘要標記；聊天標準拆成「基準＝出貨」與「λ 0＝P4d 那一格」兩個錨點；列出每一跑。

探索片：只用調參層（chatclause、cvtune-clean、wikitune-clean）、開發層（discordtune）與回歸層集合；不碰判讀集與保留集；不改核心與 App；不出貨。

## 讀音項

- 和 P5 的通道項同一個定義（`experiments/p5-reading-channel/p5.py` 的 `lex_logp`、`word_terms`、`ch_of`，估法 lex）：
  - 路徑上每個**單字詞** w 讀 r：t(r, w) = s(r, w) − max_r' s(r', w)，s 是引擎詞庫的分數。多字詞一律 0；詞庫找不到 (r, w) 照 0 算並計數。
  - ch_j = Σ max(t, −c)。
- **不同的地方是加在哪裡**：P5 加在融合之後；R1 加在 n-gram 分數上：s_j = ng_j + λ·ch_j。
  - 用 s 對 n-gram 的前 64 名重新排序，取新的前 8 名進語意校正。
  - 融合照各設定、各強度出貨的規則（α、τ、δ），n-gram 那一欄改用 s。
  - 聊天、標準強度：常談照出貨（P4d 那一格），P5 的通道項拿掉（ν 0）。R1 這一項就是要取代它。
- **離線近似**：用 CLI 的傾印（每列前 64 名候選）重算，不改解碼器。真的解碼器可能把第 64 名以外的路徑拉上來；只報「正解在前 64 名」的比例，不估它。

## 小模型分數（工具改動）

- 新的前 8 名可能有原本第 8 名以外的候選，CLI 現在只替前 8 名打 `ll`。
- `cli/` 加 `--tinylm-scores-topk N`（預設 8；不給時所有輸出逐位元組不變，單元測試斷言）：`--tinylm-scores` 的紀錄改成前 N 名的 `ll`，用核心已公開的 `TinyLm::ll_many`，和出貨的 `rerank` 用同一個函式與同一個前文 key。不改 `core/`。
- 錨點：N 64 時，前 8 名的 `ll` 和 N 8 那一跑逐位元組相同，不同就停。

## 設定、基準與每一次 CLI

「現行出貨」照 `tools/bench.py` VERSIONS2 的 v0.5.0 那一列：兩種設定都是 `--context --packs acg --tinylm`，聊天另加 `--colloquial-core`，書面靠模型旁的連文表。

**每一跑的指令**（`data/lm/` 放出貨的模型；跑之前 `shasum -a 256 -c data/bigram.sjlm.sha256 data/classes.sjc.sha256 data/kn.sjkn.sha256 data/trigram.sjt3.sha256 data/tinylm.sjtl.sha256 data/colloquial.sjlm.sha256`，有一個不符就停）：

```
shanjie-eval --lm data/lm/bigram.sjlm --profile <P> --context --packs acg --tinylm data/lm/tinylm.sjtl --rerank-level <L> \
  [--colloquial-core data/lm/colloquial.sjlm] --rows <集合> --dump D --dump-words W --rowstats R \
  --tinylm-scores S --tinylm-scores-topk 64
```

| 設定 | `--profile` | `--rerank-level` | `--colloquial-core` | 摘要行的設定標記要有 | 集合 |
|---|---|---|---|---|---|
| 聊天標準 | chat | standard | 有 | `+colloq:2984eeb5+rc` | chatclause、discordtune、cvtune-clean、打字測驗、錯字回報（前 154 列）、dev302 |
| 聊天保守 | chat | conservative | 有 | `+colloq:2984eeb5`，沒有 `+rc` | discordtune、打字測驗 |
| 聊天積極 | chat | aggressive | 有 | `+colloq:2984eeb5`，沒有 `+rc` | discordtune、打字測驗 |
| 書面標準 | formal | standard | 無 | `+tri:fdd3b05d`，沒有 `+colloq` | wikitune-clean、cvtune-clean、打字測驗、錯字回報（前 154 列）、dev302 |
| 書面保守 | formal | conservative | 無 | 同上 | wikitune-clean、打字測驗 |
| 書面積極 | formal | aggressive | 無 | 同上 | wikitune-clean、打字測驗 |

標記不符就停（例如書面沒有 `+tri`、聊天標準沒有 `+rc`）。上表共 19 跑。另外三種錨點用的跑：

- 聊天標準加 `--no-reading-channel`（P4d 那一格），只跑 chatclause 與 discordtune：2 跑。
- `--tinylm-scores-topk` 不給（N 8）：聊天標準與書面標準的 cvtune-clean 各一跑，共 2 跑。

**錨點**（不符就停）：

1. **基準＝出貨**：每個設定、每個集合，離線重算出貨的那一格（λ 0；聊天標準是 P4d 那一格加 P5 的 lex／1.0／4，其他設定是各自那一檔的融合），每列的選字等於那一跑 `--tinylm-scores` 的 `c`，對錯等於 rowstats。
2. **λ 0 的聊天標準＝P4d 那一格**：離線的 P4d 那一格（ν 0）等於 `--no-reading-channel` 那兩跑的選字與 rowstats。
3. **小模型分數**：N 64 那一跑前 8 名的 `ll`，和 N 8 那一跑逐位元組相同。

R1 的每一格在聊天標準是「P4d 那一格＋λ·ch」（P5 那一項拿掉，ν 0）；其他設定是「各自那一檔＋λ·ch」。所有判讀都對錨點 1 的出貨比。

**第 9 名以後的候選**：沒有 `ll` 的列（`--tinylm-scores` 沒寫紀錄的列）由 s 的第一名決定，照 d23 的規則。新的前 8 名裡只要有一個候選沒有 `ll`（太長、模型打不了分），那一列也由 s 的第一名決定，和核心在前 8 名有候選太長時跳過重排一樣；這種列只報列數（修訂二，任何數字之前，`/code-review` 指出原本沒有規定）。第 9–64 名的對錯用和 rowstats 相同的寬鬆對照，CER 用 `evalstats.levenshtein`。

## 格點與選格（事先固定）

- 格點：λ ∈ {0.25, 0.5, 1.0, 2.0} × c ∈ {1, 2, 4}，12 格；估法只用 lex（P5 選的估法）。
- 一組值用在所有設定（產品只出一組）。
- 選格：每一格算兩個 U2（改對 − 2×改壞，對各自的基準）：chatclause（聊天、標準）與 wikitune-clean（書面、標準）。
  - 只看兩個 U2 都 > 0 的格，取兩者相加最大的一格。
  - 同分時依序取：較小的那個 U2 大的、λ 小的、c 大的。
- **沒有合格的格 → 照登記不過**，不跑後面的條件。

## 判讀（選中的那一格，對各設定的基準；10-20 第三次放寬的出貨標準）

| # | 條件 | 設定與集合 |
|---|---|---|
| 1 | 不顯著變差（精確 McNemar p ≥ 0.05 或淨值 ≥ 0），而且改壞 ≤ 26 或改壞÷改對 ≤ 0.6 | discordtune：聊天標準、聊天保守、聊天積極 |
| 2a | 回歸層：逐列相同，或改壞 ≤ 2 且淨值 ≥ 0 | 打字測驗：六個設定；dev302 書面標準（dev302 聊天只報、逐列標類別） |
| 2b | Δtop1 的 90% 配對 bootstrap 下界 ≥ −1.0 個百分點 | cvtune-clean：聊天標準、書面標準；wikitune-clean：書面保守、書面積極 |
| 3 | 淨值 > 0，而且改壞÷改對 ≤ 0.6（不帶學習檔） | 錯字回報（前 154 列）：聊天標準＋書面標準合計 |

- 四條全過：報給維護者。下一步是產品片：讀音項放進核心的解碼器（每個設定、每個強度），以這片的離線選字當錨點逐列核對，常談上 P5 那一項同時拿掉；出貨前照規則在沒看過的判讀集確認一發。
- 任一條不過：寫「照登記不過」，和維護者討論，不結案。
- **只報**：
  - 每個設定的 n-gram 層改對／改壞（只看 s 的第一名）。
  - 新的前 8 名換掉候選的列數，以及需要第 8 名以外 `ll` 的列數。
  - 錯字回報裡讀音項扣分的正解列（10-21 記的盲點：「長」ㄓㄤˇ、「嗎」ㄇㄚ˙、「波」ㄅㄛ）。調參層與開發層的讀音由 `tools/readings.py` 產生，看不到正解用次要讀音被扣分；只有錯字回報與打字測驗是真人讀音。
  - wikitune-clean 同時是書面標準的選格集合，和書面保守／積極的 2b 集合；那兩條只能當回歸看。
  - error ratio。
- 看過任何數字之後，不改格點、選格規則與門檻。

## 預算、停止條件、範圍外

- **預算**：
  - GCP c4d VM ≤ 6 小時：上面 23 跑 CLI，再離線算 12 格。
  - 同一個比較的所有設定在同一台、同一種 CPU 上跑。私人集合的傾印與分數檔跑完就刪（`trap … EXIT`）。
  - executor 1 回合：CLI 選項與離線重算的程式；兩者都過 `/code-review`（CLI 選項是程式改動，另照改動分級送 verifier）。
  - 超過 1.5 倍就停下來問。
- **停止條件**：
  - 錨點 1–3 任何一個對不上；模型檔的雜湊或摘要行的設定標記不符。
  - 前 8 名的 `ll` 兩跑不同。
  - 私人句子被帶回 Mac。
  - 用到判讀集或保留集。
- **範圍外**：
  - 改核心的解碼器（產品片）。
  - 讀音計數 B（按讀音重新計數語料；這片之後）。
  - mc 估法、多字詞的讀音項。
  - 改常談或會意的參數。
  - SUB-0。
