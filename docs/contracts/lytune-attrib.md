# lytune 退步歸因（v0.4.3 → v0.4.4）

修訂 2，2026-10-16（執行後的偏離與版本號，見 §8）。研究片：只量、不改核心；結果決定下一版（原寫 v0.4.5；v0.4.5 在結果出來前已發佈，改成 v0.4.6）要不要換 `classes.sjc` 的詞類權重 μ。

## 0. 起因

- v0.4.3 → v0.4.4 出貨設定，lytune（4,811 列）聊天 4,055 → 4,010（改對 40／改壞 85，p 7×10⁻⁵）、書面 4,021 → 3,979（54／96，p 0.0008），顯著變差（`docs/benchmark.md`，研究紀錄 2026-10-15）。使用者端三集沒有顯著變差，維護者決定先出 v0.4.4、另開這一片歸因。
- Fable 的推論（研究紀錄同日「v0.4.4 的 lytune 退步」）：書面幾乎都在 n-gram 階段，指向「詞類項 × model-v6」；聊天另有約 −37 在重排階段。沒量過的：model-v5 上詞類開／關、model-v6 上 KN 開／關；μ＝0.8 是 2026-10-08 在 model-v3、dev302 上選的格點下緣（`docs/contracts/s2k-word-classes.md` §2 的候選 {0.8, 0.9, 0.95, 0.98}），從沒往下量過。
- μ 是 `classes.sjc` 檔頭的一個 f64（`tools/build_classes.py` 格式說明、`core/src/lm.rs` 載入時只檢查 0 ≤ μ ≤ 1），檔案只綁 `bigram.sjlm` 的雜湊；`tinylm-v5` 只綁 n-gram 模型與 `kn.sjkn` 的雜湊、不綁 `classes.sjc`（`core/src/tinylm.rs` `load_paired`）。所以 μ 的每個值只要改那 8 個位元組，不必重建模型、詞類或小模型，App 與 CLI 都直接讀得到。μ＝0 時詞類項的式子 `back·((1−μ)·pb + μ·…)` 退回 `back·pb`，等同 `--no-classes`，但 `--tinylm` 不擋它（`--no-classes` 會被 `--tinylm` 拒絕）。

## 1. 要做到什麼、怎麼看到

- 把 lytune 的退步拆到改動上：n-gram 階段的模型（v5 → v6）、詞類項、KN，和重排階段，各佔多少列。
- 用出貨設定掃 μ，看降低 μ 能不能把 lytune 拿回來、又不讓其他集合變差。
- **怎麼看到**：研究紀錄一節，內容是 §3 的兩張表（每格是 `tools/evalstats.py compare` 的一行），加上 §4 的判讀結果；維護者看表，再用選擇題決定 v0.4.6 怎麼做。

## 2. 共同設定

- 程式：除非另註「tag」，都用 main `fc96deb` 的 `cli`（release build；main 和 v0.4.4 的 `core cli data/lexicon` 沒有差異）。標「tag」的臂照 `tools/bench.py` 的做法用 v0.4.3 自己的 CLI 與詞庫（`git archive v0.4.3` 的 `core cli data` 到獨立目錄、release build），也就是 bench 量 v0.4.3 時的設定。main 的 CLI 一律載入 v0.4.4 起的名單層（`names-add.tsv`）、手動詞加分 0.75 與 v0.4.4 的詞包，所以 main 配 v0.4.3 的模型和 tag 的 v0.4.3 不同，兩者的差另列一項（§3.1）。
- 計分：每次都設 `SHANJIE_VARIANTS=eval/bench/suite-v1/variants.tsv`（和 bench 相同；沒設時 CLI 用 repo 的 `eval/variants.tsv`）。μ 檔由 `experiments/lytune-attrib/set_mu.py` 從 `data/lm/classes.sjc` 複製一份、改檔頭的 μ、輸出新檔與 SHA-256；同一個腳本讀回 μ 核對。
- 模型檔：v0.4.3 用 model-v5＋classes-v3＋tinylm-v3（`data/tinylm.sjtl.sha256` 在 v0.4.3 是 `7e73ef67…`；v0.4.3 出貨的那一組，`tools/bench.py` `VERSIONS2` 的 v0.4.3 列）；v0.4.4 用 model-v6＋classes-v4＋kn-v1＋tinylm-v5。全部從各自的資料 Release 取得並核對 `data/*.sha256`（或 bench 工具記錄的雜湊）。
- 旗標：出貨的 `--context --packs acg`，`--profile chat`／`formal` 各跑一次；「n-gram 階段」不加 `--tinylm`，「出貨」加。每次都寫 `--rowstats`。
- 集合：lytune（4,811 列，`0e0fdbed…`）。μ 掃描另加 cvtune、wikitune、dev302、錯字回報（`eval/dev/user-reported.txt` 目前全部 116 列）與 discordtune（私人，維護者的 188 副本，只出數字）。
- 機器：188（WSL 或 Windows 原生都可以，16 程序分片），Colab 可以分攤；Mac 只跑單句核對。188 的記憶體曾經不穩，所以每台機器的第一個工作先重現一格已知數字（§4 第 1 點）。
- 統計：`tools/evalstats.py compare BASE CAND --net`，精確 McNemar；本片的比較都是探索性的，p 值照實報、不做多重比較校正，判讀用 §4 的規則。

## 3. 實驗

### 3.1 歸因（只跑 lytune，兩種設定）

| 臂 | 模型 | 詞類 | KN | 小模型 |
|---|---|---|---|---|
| N5 | v5（tag） | classes-v3（μ 0.8） | — | 無 |
| N5m | v5 | classes-v3（μ 0.8） | — | 無 |
| N5xm | v5 | 關（`--no-classes`） | — | 無 |
| N6 | v6 | classes-v4（μ 0.8） | kn-v1 | 無 |
| N6x | v6 | 關 | kn-v1 | 無 |
| N6k | v6 | classes-v4（μ 0.8） | 關（`--no-kn`） | 無 |
| N6xk | v6 | 關 | 關 | 無 |
| S3 | v0.4.3 出貨（tag） | | | tinylm-v3 |
| S3m | v0.4.3 的模型檔與旗標，main 的 CLI | | | tinylm-v3 |
| S4 | v0.4.4 出貨 | | | tinylm-v5 |

比較（每列一行表）：
- 總差：S3→S4（要重現 `docs/benchmark.md` 的 40／85、54／96）。
- 程式與詞庫（名單層、手動詞、詞包，不換模型）：S3→S3m（出貨）、N5→N5m（n-gram 階段）。
- n-gram 階段（main 的 CLI）：N5m→N6 的總差，再拆成：
  - 模型本身（v5 → v6，詞類與 KN 都關）：N5xm→N6xk；
  - 詞類項：v5 上 N5xm→N5m，v6 上 N6xk→N6k（不開 KN）與 N6x→N6（開 KN）；
  - KN：N6k→N6、N6xk→N6x。
- 重排階段：（S3m→S4 的淨差）減（N5m→N6 的淨差），用列數相減，標推論。

S3→S4 的淨差＝程式與詞庫（S3→S3m）＋S3m→S4；後者＝n-gram 階段＋重排階段。n-gram 階段的各項相加和 N5m→N6 的差寫成交互作用。

### 3.2 μ 掃描（出貨設定，加 tinylm-v5）

- μ ∈ {0, 0.2, 0.4, 0.6, 0.8}，0.8 就是 S4。五個值 × 六個集合 × 兩種設定（discordtune 兩種設定都跑，和 bench 相同）。
- 每個 μ 對 0.8 各出一行表。μ 只改一個常數、不改計算量，本片不量延遲；選定的 μ 進 v0.4.6 時由發版前的 bench 量。

## 4. 驗收與判讀

1. **重現**：S3（tag）、S4 在 lytune 兩種設定的第一名數等於 `docs/benchmark.md` 的 4,055／4,021、4,010／3,979。不相等就停（§5）。
2. **歸因表**：§3.1 每個比較都有一行，研究紀錄寫出每一項（程式與詞庫、模型本身、詞類項、KN、重排階段）佔 S3→S4 淨差的多少列；相加不等於總差的部分照實寫成交互作用，不硬分。
3. **μ 的選擇**（給維護者的建議，不自動出貨）：在 μ < 0.8 的值裡，挑同時滿足下面三點、lytune 兩種設定淨值合計最大的那個；同分取較大的 μ（改動小）：
   - discordtune 與錯字回報，兩種設定都沒有「改壞 > 改對且 p < 0.05」（sbench-v2 §2.3 的擋法）；
   - cvtune＋wikitune＋dev302 兩種設定的淨值合計 ≥ 0；
   - lytune 至少一種設定是「改對 > 改壞且 p < 0.05」。
   沒有值滿足時，建議維持 0.8，研究紀錄寫明「降 μ 救不回 lytune 而不傷別的集合」，並列出接下來的候選（重調 tinylm-v5 的 α／τ、詞類表重分群）供選擇題用。
4. 保留集：本片不跑。選定的 μ 進 v0.4.6 時，照 sbench-v2 在發版前由 fresh verifier 跑一次。

## 5. 停止條件、預算

- 重現（§4 第 1 點）不相等：先查模型檔雜湊與旗標；查不出原因就停，回報差在哪一格。
- 同一格在兩台機器（或同台兩次）結果不同：停，當成 188 硬體問題處理，該台的結果作廢。
- 預算：188 約 16 程序、合計 ≤ 6 小時；超過先回報。不在 Mac 上跑整個集合。

## 6. 範圍外

- 改核心、改 μ 的載入方式、依設定（聊天／書面）分開的 μ：`classes.sjc` 只有一個 μ，要分開就是核心改動，另開一片。
- 重新分群、重建 model-v6、重訓或重調 tinylm-v5。
- 出貨：選定的 μ 要另開 PR（新的 `classes.sjc` 資料 Release、`data/classes.sjc.sha256`、CI 與 `release.yml` 的下載），由維護者用選擇題決定後才做。

## 7. 負責人

- main：契約、在 188／Colab 跑 §3、整理表與研究紀錄、私人集合（只出數字）。
- `experiments/lytune-attrib/set_mu.py` 與它的一個自我檢查（改完讀回 μ、其他位元組不變）：main 自己寫，很小。
- fresh `pilotfish:plan-verifier` 審這份契約；結果的 PR 是研究紀錄＋小腳本，照「改動分級」的程式級關卡（/code-review＋verifier＋CI）。

## 8. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
| plan-verifier 第 1 次 REVISE（2026-10-15） | 1. v0.4.3 的臂用 main 的 CLI 不是 bench 量的設定（bench 每版用自己的 CLI 與詞庫、`SHANJIE_VARIANTS` 用套件檔；main 一律載入名單層），重現會失敗，歸因也漏掉程式與詞庫的改動；2. 沒有單獨拆出模型本身（v5 → v6）的比較；非阻擋：CLI 在 LM 模式只印重排的 p95，不是每鍵延遲 | 全部 FIX：1 → §2 加 tag 臂的建置與 `SHANJIE_VARIANTS`，§3.1 加 S3m、N5m 與「程式與詞庫」一項，重排階段改用 S3m、N5m；2 → §3.1 加 N5xm→N6xk；非阻擋 → §3.2 不量延遲 |
| 執行後的偏離（2026-10-16，/code-review） | 1. 188 用 14 程序（§2、§5 寫 16）；2. tag 臂的 v0.4.3 在 Linux 編不過，套上 PR #156 的 `learn_store.rs` 常數修正（不動解碼）才建置，照本契約在 Linux 重跑也要套；3. 研究紀錄的表只列淨值、改對／改壞與 p，不是每格完整的 `compare` 行（§1）；4. v0.4.5 在結果出來前發佈 | 1、2 照實記在研究紀錄，重現的錨點 4 個數都相同，不影響結論；3 接受，rowstats 留在 188 可重跑；4 版本號改 v0.4.6（修訂 2） |
