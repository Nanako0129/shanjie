# S-bench 契約：跨版本基準測試

使用者 2026-10-05：「我們能建立 benchmark 的基準測試嗎？才會知道輸入法有沒有進步」。選定先做自動部分（AskUserQuestion）。

**目標**：用一個指令量任何一個版本（tag 或 commit），結果累積成跨版本的表，看輸入法有沒有進步。到目前為止每一片都是各自做 A/B（例如 S2r-2 對 main），沒有一張表能看出 v0.1.0 → v0.1.1 → v0.1.2 的整體變化。

**怎麼算做到**：`docs/benchmark.md` 有 v0.1.0、v0.1.1、v0.1.2 三列基準線，全部由同一個指令產生；同一版重跑，準確率逐位元組相同；v0.1.2 的數字和研究紀錄裡同條件的數字一致（§3.2）。看得到的人：使用者讀 `docs/benchmark.md`。

## 1. 量什麼（套件 v1，固定）

### 1.1 準確率

- 用**該版本自己**的評測 CLI 與詞庫：`shanjie-eval --lm data/lm/bigram.sjlm --profile <p> --rows <檔> --dump <暫存>`。v0.1.0 起 CLI 就有這些旗標（2026-10-05 用 `git show v0.1.0:cli/src/main.rs` 核對）。
- 模型一律是 `data/bigram.sjlm.sha256` 對得上的 model-v1；結果記下模型雜湊，模型換了就是新的套件版本。
- 集合（列數 2026-10-05 實數）：

| 集合 | 來源 | 列數 | profile | 備註 |
|---|---|---|---|---|
| dev302 | `eval/dev` 前 302 列（`experiments/s2/iter2.all_sets`） | 302 | chat、formal | 挑戰集；S5j 證明它會誤導，只當參考 |
| typing76 | `eval/dev/user-typing.txt` | 76 | chat、formal | 可和 `docs/typing-test.md` 的三套輸入法實打結果並列 |
| probe | `eval/probe/s2r-probe.txt` | 83 | chat | 讀音變體（一／不／法）的探針 |
| cvtune | `~/.cache/shanjie/work/s2/tune/cvtune.txt`（`experiments/s2/build_tune.py` 產生） | 3,677 | chat | 調參數時看過 |
| wikitune | 同上 `wikitune.txt` | 2,733 | chat | 調參數時看過 |
| discordtune（私有） | `~/side-project/shanjie-private/discord-tune-rows.txt` | 4,958 | chat | 使用者真實聊天，調參數時看過；repo 只放統計 |

- 每格的指標：n、top1（CLI 摘要行，寬鬆對照）、oracle@64、`top1_sha256`。
- **和上一列逐列比**：修好、弄壞、McNemar 雙尾精確 p。用兩版的 dump 第一名，以 `reference/proto/eval.py` 的 `lenient` 比對（S2v 已驗證和 Rust 一致）。

### 1.2 速度與記憶體

- 按鍵重播程式 `tools/bench/replay/`：獨立的 crate，執行時產生 Cargo.toml，把 `core` 指到該版本匯出的原始碼。只用 v0.1.0 起就有的 API：`Engine::new`、`load_lm`、`key`、`Layout::key_of_symbol`、`key_of_tone`（同上核對）。
- 內容：dev302 的讀音轉成按鍵（標準排列，每列最後按 Enter），每鍵計時；報 p95 與 max、載入時間（`Engine::new` 加 `load_lm`）、峰值 RSS（`getrusage`）。
- **同場量測**：速度只在同一次執行、同一台機器裡比較才有意義。`run` 一次給多個版本時，各版本輪流跑 3 輪，每版取三輪 p95 的中位數。結果記下機型、macOS 版本、開始與結束的 load average。表上速度欄註明「同場」的日期。
- 速度只報、不設門檻（門檻在各片契約裡）。

### 1.3 保留集

- 工具**不跑**保留集（`eval/README.md` 的規則：main 不看內容）。
- 基準線與之後每次發版前，由 fresh verifier 用該版 CLI 跑 `--set holdout`、兩個 profile，只回報摘要行（n、top1、oracle@64、`top1_sha256`）；main 把數字填進表的保留集欄。
- v0.1.0、v0.1.1、v0.1.2 各量一次，作為基準線。

## 2. 產出

- `tools/bench.py`
  - `run <ref>... [--label <名稱>]`：每個 ref 用 `git archive` 匯出到 `~/.cache/shanjie/bench/src/<sha>/`（不動 repo 的 worktree 清單），各自 build CLI 與重播程式，跑 §1.1、§1.2。
  - 公開與私有集合的**統計數字**寫進 `eval/bench/results/<label>.json`（含集合、模型與詞庫的指紋、環境）。逐列輸出寫快取：公開集合在 `~/.cache/shanjie/bench/rows/`，私有集合在 `~/side-project/shanjie-private/bench/`。逐列輸出不進 repo（wikitune 句子是 CC BY-SA，cvtune 句子來自 Common Voice／Tatoeba）。
  - 逐列比較需要上一列的輸出：從快取讀，快取不在或雜湊和 results JSON 的 `top1_sha256` 不符就重跑上一版。
  - `table`：從所有 results JSON 重產 `docs/benchmark.md`，不手改。
- **指紋**：每個集合檔的 SHA-256、模型雜湊、該版 `data/lexicon` 的雜湊。集合指紋和上一列不同時，該格標「集合變動」，不算 Δ。
- **缺檔**：私有集合缺檔時，該欄寫「—（沒有私有資料）」並在 stderr 說明；公開集合缺檔就失敗退出。不默默略過。
- `docs/benchmark.md`：
  - 主表：版本 × 集合的 top1%，附和上一版的修好／弄壞／p；
  - 保留集表；
  - 速度表（同場）；
  - 量測規則：哪些集合被調參數看過、dev302 只當參考、保留集是唯一沒被看過的集合、怎麼重跑。
- `docs/verification.md` 加一列：發版前跑 `tools/bench.py run <新版 commit>`，再請 verifier 量保留集，兩者的結果隨發版前的 PR 進 repo。

## 3. 驗收

1. **決定性**：同一版跑兩次，results JSON 的準確率欄與 `top1_sha256` 完全相同；`table` 重產的 `docs/benchmark.md` 逐位元組相同。
2. **對得上研究紀錄**：v0.1.2（ef20c40）chat 的 cvtune 3,121／3,677、wikitune 1,860／2,733、dev302＋typing76 合計 303／378（研究紀錄 S2r-2 一節，B 欄）；discordtune 4,172／4,958（main 跑）。對不上就停下來回報。
3. **逐列比較正確**：v0.1.1 → v0.1.2 在 cvtune、wikitune（chat）的修好／弄壞，和 `experiments/s2/s2r_eval.py ab <v0.1.1 CLI> <v0.1.2 CLI> --which orig --all --sets cvtune,wikitune` 的結果相同。
4. **舊版本**：v0.1.0、v0.1.1 都能 build 並產出完整的一列；任何一版 build 或執行失敗，表上寫原因，不留空白。
5. **突變**：
   - 把一個公開集合檔改一個字，該格標「集合變動」；
   - 把快取裡上一版的逐列輸出改一列，工具發現雜湊不符並重跑上一版；
   - 把公開集合檔移走，工具以非 0 結束並說明是哪個檔。
6. **速度**：同場量 v0.1.0、v0.1.1、v0.1.2，報 p95 中位數與 max、載入時間、RSS。

## 4. 範圍外

- 實打對照（`tools/imetype.swift`，要使用者放開鍵盤）：使用者這次沒選。
- S4 學習的效果：靜態集合量不到。S4 合併後另加套件 v2（例如用使用者聊天的前半段模擬改選、量後半段）。
- 保留集自動化、CI 每次跑（太慢，私有資料也不在 CI）。

## 5. 負責人、預算、停止條件

| 檔案或工作 | 負責人 |
|---|---|
| `tools/bench.py`、`tools/bench/replay/`、`eval/bench/`、`docs/benchmark.md` 的產生器 | `pilotfish:executor` |
| discordtune 的部分、驗收 2 與 3 的私有部分、保留集（請 verifier 量）、`docs/PLAN.md`、`README.md`（指向 benchmark.md）、`docs/research-log.md`、`docs/methodology.md`（量測規則）、`docs/verification.md` | main |

- **預算**：executor 1 回合加 1 次修正。
- **executor 不可以做的事**：
  - 不得讀 `~/side-project/shanjie-private/` 或 `eval/holdout/`；
  - 不得 push、開 PR；
  - 不得跑 `scripts/install-ime.sh`、`shanjie install`；
  - 不得開 GUI App、執行 `.app` 內的二進位；
  - 不得改 `core/`、`cli/`、`macos/`（工具只讀它們的匯出副本）。
- **停止條件**：
  - 驗收 2 對不上；
  - 同一版兩次結果不同；
  - 舊版 build 不起來而且原因不明；
  - 超過預算。
- **回滾**：只新增檔案，revert 合併 commit 即可。
