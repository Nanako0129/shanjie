# S-bench 契約：跨版本基準測試

使用者 2026-10-05：「我們能建立 benchmark 的基準測試嗎？才會知道輸入法有沒有進步」。選定先做自動部分（AskUserQuestion）。

**目標**：用一個指令量任何一個版本（tag 或 commit），結果累積成跨版本的表，看輸入法有沒有進步。到目前為止每一片都是各自做 A/B（例如 S2r-2 對 main），沒有一張表能看出 v0.1.0 → v0.1.1 → v0.1.2 的整體變化。

**怎麼算做到**：`docs/benchmark.md` 有 v0.1.0、v0.1.1、v0.1.2 三列基準線，全部由同一個指令產生；同一版重跑，準確率逐位元組相同；v0.1.2 的數字和研究紀錄裡同條件的數字一致（§3.2）。看得到的人：使用者讀 `docs/benchmark.md`。

## 1. 量什麼（套件 v1，固定）

### 1.0 版本與集合分開

- **版本**只決定程式與詞庫：每個 ref 用 `git archive <ref> Cargo.toml Cargo.lock core cli data eval/variants.tsv` 匯出（不含 `eval/` 的其他部分，所以保留集不會被複製出來），各自 build。
- **集合**一律來自套件，所有版本讀同一份：
  - 公開的 dev302、typing76、probe 凍結成 `eval/bench/suite-v1/` 裡的 rows 檔（`|句子|讀音`），由執行工具的 checkout 提供，SHA-256 寫死在工具裡。dev302 照 `iter2.all_sets` 的規則產生一次（`eval/dev/*.txt` 排序、排除 `user-typing.txt`、取前 302 列），之後 `eval/dev` 再加句子也不影響套件 v1。
  - cvtune、wikitune 不進 repo（授權見 §2），工具讀快取裡的檔，SHA-256 寫死在工具裡，不符就失敗退出。
  - 私有集合從 `--private-root <目錄>` 讀（預設 `~/side-project/shanjie-private`），SHA-256 記在結果裡。**所有**私有路徑（集合檔、逐列輸出、上一版的逐列快取）都由 `--private-root` 推出，工具裡不出現其他寫死的私有路徑；私有集合缺檔時，工具不碰任何私有的快取或輸出路徑（不建立、不 stat）。
- 每個版本都只用 `--rows` 餵集合，**不用** `--dev`（`--dev` 會讀該版本自己的 `eval/dev`，版本和集合就混在一起了）。
- 工具自己定義套件的集合，不 import `experiments/s2/iter2.all_sets`（它會讀到所有存在的私有檔）。

### 1.1 準確率

- 用**該版本自己**的評測 CLI 與詞庫：`shanjie-eval --lm <main checkout 的 data/lm/bigram.sjlm 絕對路徑> --profile <p> --rows <檔> --dump <暫存>`。v0.1.0 起 CLI 就有這些旗標與 `SHANJIE_VARIANTS`（2026-10-05 用 `git show v0.1.0:cli/src/main.rs` 核對）。
- 模型一律是 `data/bigram.sjlm.sha256` 對得上的 model-v1；結果記下模型雜湊，模型換了就是新的套件版本。
- **計分規則只有一套**：每次呼叫 CLI 都設 `SHANJIE_VARIANTS=<執行工具的 checkout 的絕對路徑>/eval/bench/suite-v1/variants.tsv`（套件凍結的副本；CLI 的工作目錄可能是匯出目錄，所以要絕對路徑），top1 與 oracle@64 由工具從 dump 用同一套寬鬆對照算出（`reference/proto/eval.py` 的 `lenient`，讀同一份 variants）。CLI 摘要行只當交叉檢查：和工具算的不同就失敗退出。
- 集合（列數 2026-10-05 實數）：

| 集合 | 來源 | 列數 | profile | 備註 |
|---|---|---|---|---|
| dev302 | `eval/dev` 前 302 列（`experiments/s2/iter2.all_sets`） | 302 | chat、formal | 挑戰集；S5j 證明它會誤導，只當參考 |
| typing76 | `eval/dev/user-typing.txt` | 76 | chat、formal | 可和 `docs/typing-test.md` 的三套輸入法實打結果並列 |
| probe | `eval/probe/s2r-probe.txt` | 83 | chat | 讀音變體（一／不／法）的探針 |
| cvtune | `~/.cache/shanjie/work/s2/tune/cvtune.txt`（`experiments/s2/build_tune.py` 產生） | 3,677 | chat | 調參數時看過 |
| wikitune | 同上 `wikitune.txt` | 2,733 | chat | 調參數時看過 |
| discordtune（私有） | `<private-root>/discord-tune-rows.txt` | 4,958 | chat | 使用者真實聊天，調參數時看過；repo 只放統計 |

- 每格的指標：n、top1、oracle@64、`top1_sha256`（第一名字串串起來的雜湊）。
- **和上一列逐列比**：修好、弄壞、McNemar 雙尾精確 p。工具檢查兩版逐列結果的列數都等於集合列數，而且上一版逐列算出的 top1 等於它紀錄裡的 top1；不符就停下。（原本寫的「top1(新) − top1(舊) = 修好 − 弄壞」在兩個等長的 0／1 序列上恆成立，檢查不到東西，`/code-review` 指出後改掉。）

### 1.2 速度與記憶體

- 按鍵重播程式 `tools/bench/replay/`：獨立的 crate，執行時產生 Cargo.toml，把 `core` 指到該版本匯出的原始碼。只用 v0.1.0 起就有的 API：`Engine::new`、`load_lm`、`key`、`Key::ch`、`Key::new`、`KeyKind`、`Layout::key_of_symbol`、`key_of_tone`（同上核對）。
- 內容：套件的 dev302 讀音轉成按鍵（標準排列；輕聲以外沒有聲調鍵的音節補 Space，和 `core/tests/engine_replay.rs` 的 `keys_of` 相同；每列最後按 Enter），每鍵計時；報 p95 與 max、載入時間（`Engine::new` 加 `load_lm`）、峰值 RSS（`getrusage`）。引擎預設 chat profile。
- **重播必須真的在打字**：記下每列 Enter 送出的字串與總按鍵數，和同一版 CLI 在 dev302 chat 的第一名逐列比。全部相同才算數（現行 `core/tests/engine_lm.rs` 的 `replay_standard_chat_production_path` 已要求引擎和 dev302 chat 的 golden 逐列相同）；有不同就列出列號並失敗退出，速度欄不填。
- **同場量測**：速度只在同一次執行、同一台機器裡比較才有意義。`run` 一次給多個版本時，各版本輪流跑 3 輪，每版取三輪 p95 的中位數。結果記下機型、macOS 版本、開始與結束的 load average。表上速度欄註明「同場」的日期。
- 速度只報、不設門檻（門檻在各片契約裡）。

### 1.3 保留集

- 工具**不跑**保留集（`eval/README.md` 的規則：main 不看內容）。
- 基準線與之後每次發版前，由 fresh verifier 在該版 tag 的 worktree build 出 CLI 跑 `--set holdout`（§1.0 的匯出不含 `eval/holdout`，不能用工具的匯出）、兩個 profile，只回報摘要行（n、top1、oracle@64、`top1_sha256`）；main 把數字填進表的保留集欄。
- v0.1.0、v0.1.1、v0.1.2 各量一次，作為基準線。

## 2. 產出

- `tools/bench.py`
  - `run <ref>... [--label <名稱>]`：每個 ref 用 `git archive` 匯出到 `~/.cache/shanjie/bench/src/<sha>/`（不動 repo 的 worktree 清單），各自 build CLI 與重播程式，跑 §1.1、§1.2。
  - `--private-root <目錄>`：私有集合的根目錄。executor 的每一次執行與測試都必須指向一個空的暫存目錄。
  - 公開與私有集合的**統計數字**寫進 `eval/bench/results/<label>.json`（含集合、`variants.tsv`、模型與詞庫的指紋、環境、重播與 CLI 的一致列數、總按鍵數）。逐列輸出寫快取：公開集合在 `~/.cache/shanjie/bench/rows/`，私有集合在 `<private-root>/bench/`。逐列輸出不進 repo（wikitune 句子是 CC BY-SA，cvtune 句子來自 Common Voice／Tatoeba）。
  - 逐列比較需要上一列的輸出：從快取讀，快取不在或雜湊和 results JSON 的 `top1_sha256` 不符就重跑上一版。
  - `table`：從所有 results JSON 重產 `docs/benchmark.md`，不手改。
- **指紋**：每個集合檔的 SHA-256、套件 `variants.tsv` 的雜湊、模型雜湊、該版 `data/lexicon` 的雜湊（只記錄）。快取以（commit、集合 SHA-256、variants、模型）為鍵；上一版紀錄的指紋和目前的套件不同時，工具在目前的套件下重跑上一版再比，不拿舊結果硬比，並在 stderr 說明原因。工具從不讀取或雜湊 `eval/holdout/` 底下的檔案。
- **缺檔**：私有集合缺檔時，該欄寫「—（沒有私有資料）」並在 stderr 說明；公開集合缺檔就失敗退出。不默默略過。
- `docs/benchmark.md`：
  - 主表：版本 × 集合的 top1%，附和上一版的修好／弄壞／p；
  - 保留集表；
  - 速度表（同場）；
  - 量測規則：哪些集合被調參數看過、dev302 只當參考、保留集是唯一沒被看過的集合、怎麼重跑。
- `docs/verification.md` 加一列：發版前跑 `tools/bench.py run <新版 commit>`，再請 verifier 量保留集，兩者的結果隨發版前的 PR 進 repo。

## 3. 驗收

1. **決定性**：同一版跑兩次，results JSON 的準確率欄與 `top1_sha256` 完全相同；`table` 重產的 `docs/benchmark.md` 逐位元組相同。
2. **對得上研究紀錄**（main）：v0.1.2（ef20c40；研究紀錄的 B 是分支 2f4f684，詞庫與 CLI 和 ef20c40 相同，不同就照停止條件處理）chat 的 cvtune 3,121／3,677、wikitune 1,860／2,733、dev302＋typing76 合計 303／378（研究紀錄 S2r-2 一節）；discordtune 4,172／4,958。對不上就停下來回報。
3. **逐列比較正確**：
   - executor：工具的列數與上一版 top1 檢查在每一格都通過；另外用套件的 rows 檔自己跑兩版 CLI 的 dump、獨立重算 v0.1.1 → v0.1.2 在 cvtune、wikitune 的修好／弄壞，和工具一致（不經過 `s2r_eval.py`，它會讀私有檔）。
   - main：同一組數字和 `experiments/s2/s2r_eval.py ab <v0.1.1 CLI> <v0.1.2 CLI> --which orig --all --sets cvtune,wikitune` 一致。
4. **舊版本**：v0.1.0、v0.1.1 都能 build 並產出完整的一列，**包括 probe**（集合來自套件，不是該版本的匯出）；同一次執行裡三個版本的集合指紋相同。任何一版 build 或執行失敗，表上寫原因，不留空白。
5. **突變**：
   - 把一個公開集合檔改一個字，工具因 SHA-256 不符失敗退出；把套件 `variants.tsv` 刪一行，同樣失敗（套件改了要升版）；
   - 把快取裡上一版的逐列輸出改一列，工具發現雜湊不符並重跑上一版；
   - 把公開集合檔移走，工具以非 0 結束並說明是哪個檔；
   - `--private-root` 指向空目錄時，私有欄寫「—（沒有私有資料）」；
   - 重播程式拿掉一聲補的 Space，執行失敗（重播與 CLI 不一致）。
6. **速度**：同場量 v0.1.0、v0.1.1、v0.1.2，報 p95 中位數與 max、載入時間、RSS；三版的重播與 CLI 都是 302／302 一致，結果記下總按鍵數。
7. **私有資料沒被碰到**（不需要 root；2026-10-05 main 在本機實測過這個做法）：executor 的完整執行（`run v0.1.0 v0.1.1 v0.1.2 --private-root <空的暫存目錄>`）包在
   `sandbox-exec -p '(version 1)(allow default)(deny file* (subpath "/Users/nanako/side-project/shanjie-private") (with send-signal SIGKILL))'` 裡跑。任何對真實私有目錄的存取（含 stat、`os.path.exists`）都會讓程序當場被殺（結束碼 137），所以通過條件是「整個執行以 0 結束」。
   - 突變**由 main 執行**（只做一次，先告知使用者，因為會跳出 macOS 的當機報告；executor 不做這一步）：在工具裡暫時加一行讀 `/Users/nanako/side-project/shanjie-private/discord-tune-rows.txt`，同一個指令必須以 137 結束；之後還原。
   - 子程序（cargo、CLI、重播程式）也在同一個沙盒裡，一樣受限。

## 4. 範圍外

- 實打對照（`tools/imetype.swift`，要使用者放開鍵盤）：使用者這次沒選。
- S4 學習的效果：靜態集合量不到。S4 合併後另加套件 v2（例如用使用者聊天的前半段模擬改選、量後半段）。
- 保留集自動化、CI 每次跑（太慢，私有資料也不在 CI）。

## 5. 負責人、預算、停止條件

| 檔案或工作 | 負責人 |
|---|---|
| `tools/bench.py`、`tools/bench/replay/`、`eval/bench/suite-v1/`、`docs/benchmark.md` 的產生器（executor 的試跑結果不 commit） | `pilotfish:executor` |
| 用私有資料重跑 `run v0.1.0 v0.1.1 v0.1.2`，commit 基準線的 `eval/bench/results/*.json` 與 `docs/benchmark.md`；驗收 2、驗收 3 的 main 部分、驗收 7 的突變；保留集（請 verifier 量）；`docs/PLAN.md`、`README.md`（指向 benchmark.md）、`docs/research-log.md`、`docs/methodology.md`（量測規則）、`docs/verification.md` | main |

- **預算**：executor 1 回合加 1 次修正。
- **executor 不可以做的事**：
  - 不得讀 `~/side-project/shanjie-private/` 或 `eval/holdout/`；每一次執行工具與測試都要加 `--private-root <空的暫存目錄>`；不得執行 `experiments/s2/s2r_eval.py` 或任何會呼叫 `iter2.all_sets` 的程式；
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

## 6. 審查紀錄

**plan-verifier（2026-10-05）**：REVISE，4 項 P2，全部 FIX。
| 問題 | 處置 |
|---|---|
| P2 沒有關掉私有集合的開關，executor 的驗收 3 會讀私有檔；main 與 executor 對基準線檔的分工沒寫 | FIX：§2 `--private-root`；工具不 import `all_sets`；§3.3 分成 executor 與 main；§5 由 main commit 基準線；§3.7 開檔紀錄 |
| P2 集合來源沒寫，舊版本的匯出沒有 probe，`--dev` 會讓版本和集合混在一起 | FIX：§1.0 集合來自套件、凍結並寫死雜湊，一律 `--rows` |
| P2 兩套寬鬆對照規則（各版 `variants.tsv` 對今天的 `lenient`） | FIX：§1.1 只用套件的 `variants.tsv`，top1 由工具從 dump 算，CLI 摘要行交叉檢查；Δtop1 = 修好 − 弄壞 的檢查 |
| P2 速度檢查不會失敗 | FIX：§1.2 重播與 CLI 逐列一致才算數；§3.5、§3.6 |
| 非阻擋：匯出會複製保留集 | FIX：§1.0 只匯出需要的路徑 |

**plan-verifier 收尾（ba439d0）**：REVISE，2 項 P2，全部 FIX。
| 問題 | 處置 |
|---|---|
| P2 私有的逐列輸出與快取路徑寫死，沒有跟著 `--private-root` | FIX：§1.0、§2 所有私有路徑由 `--private-root` 推出；缺檔時不碰私有路徑 |
| P2 §3.7 的 `fs_usage` 要 root，executor 跑不了；「同等方式」沒定義 | FIX：改成 `sandbox-exec` 的拒絕規則加 SIGKILL，通過條件是結束碼 0；突變必須得到 137（main 實測過）|
| P3 `SHANJIE_VARIANTS` 要絕對路徑 | FIX：§1.1 |
| P3 保留集要從 tag 的 worktree build | FIX：§1.3 |

**本地 `/code-review`（77e296e，medium）**：4 項，全部 FIX（9219aa1）。
| 問題 | 處置 |
|---|---|
| high：某格失敗時表上會顯示成「沒有私有資料」，多版本時還會整次不寫結果 | 每格記錄自己的結果或錯誤；失敗顯示「失敗：原因」；結果一律寫出，有失敗就以 1 結束 |
| medium：`git archive | tar` 沒有 pipefail，匯出失敗會留下壞快取 | 分兩步各自檢查結束碼，成功才寫標記 |
| medium：「集合變動」永遠不會觸發，快取只以 commit 為鍵 | 快取鍵含指紋；指紋不同就重跑上一版（§2） |
| low：Δtop1 = 修好 − 弄壞 恆成立 | 改成列數與上一版 top1 的檢查（§1.1） |

## 7. 修訂一（2026-10-06）：小麥資料基準列、實打對照表、落地

使用者 2026-10-06 要基準測試參考小麥注音。依 Fable 的分析，使用者選了「unigram 列＋靜態表＋里程碑實打」。善解的基底詞庫就是小麥的 `data.txt`，打字測驗裡小麥和善解 unigram 的錯法幾乎一樣（23 句 10 對 9、Discord 199 對 197、新聞 85 對 81），所以不移植小麥的解碼器。

### 7.1 小麥資料基準列

- **CLI 的改動（這片唯一允許改 `cli/` 的地方，由 executor 做）**：unigram 路徑（不帶 `--lm`）加 `--rows <檔>` 與 `--dump <檔>`，語意與 `--lm` 路徑相同：讀 `前文|句子|讀音` 列、每列寫前 64 名（`列號\t名次\tsurface\t分數`），摘要行的 profile 欄寫 `unigram`（例：`## typing76  unigram  {'n': 76, 'top1': …, 'oracle@64': …, 'top1_sha256': '…'}`）。可以和 `--no-overlay` 併用。不帶這兩個旗標時，既有的 `--set` 行為與所有 golden 逐位元組不變。
  - 一致性：新 golden `eval/golden/sbench-unigram-typing76.txt`（套件 v1 的 typing76、`--no-overlay`）由 Python 參考實作（`reference/proto/ime.py` 的 unigram 解碼、只用基底詞庫）產生，Rust CLI 的輸出要逐位元組相同，`cli/tests/golden.rs` 檢查。
- **參考列「小麥資料基準」**：`tools/bench.py reference` 子命令用目前 checkout 建出的 CLI 跑：`shanjie-eval --no-overlay --rows <套件集合> --dump <暫存>`（不帶 `--lm`、`--profile`），套件 v1 的每個集合都跑；unigram 沒有 profile 之分，chat、formal 兩欄填同一個數。計分照 §1.1（套件的 `variants.tsv`、同一套寬鬆對照，摘要行只當交叉檢查）。
- 結果存 `eval/bench/results/reference-mcbpmf.json`，內含產生它的 **commit SHA** 與 §2 的指紋（集合、variants、基底詞庫的雜湊）。`reference` 子命令在 commit SHA 或任一指紋和現有檔案不同時才重算，並在 stderr 說明原因；同一個 commit 重跑，每格的 `top1_sha256` 相同。
- `docs/benchmark.md` 主表的第一列是這一列，標題寫明 commit 與：「善解的 unigram 解碼器加小麥的基底詞庫，近似小麥的資料；不是小麥本身的解碼器（小麥現在有沒有用 bigram 沒有核對）」。它不是版本，不算修好／弄壞；版本列的數字與比較不變。量測規則一節加上 `tools/bench.py reference` 的重跑方法。

### 7.2 實打對照表（靜態）

- 新增 `eval/bench/static/typing-test.json`：從 `docs/typing-test.md` 逐格抄錄 macOS 內建注音、小麥注音、自然輸入法的**自動打字**結果（四組條件一致）：第一輪 23 句與第二輪 53 句（第 33–39 行）、Discord 845 句（第 174–180 行）、新聞 294 句（第 190–196 行）；錯的句子數、錯字數、日期 2026-10-03，每一格附來源行號。第一輪的手打結果（第 7–12 行）數字不同，不抄。Discord、新聞只抄統計數字。
- `tools/bench.py table` 在 `docs/benchmark.md` 加一節「實打對照（靜態，2026-10-03）」從這個 JSON 產生，註明：實打是當天的產品快照、蘋果注音在測驗中會學習（文件第 202 行）、和版本列的條件不同，不做統計比較。
- main 抄完後逐格核對，executor 不抄。

### 7.3 里程碑實打

- §4 的「實打對照」改成：在里程碑做（第一次是 model-v2 出貨時），由使用者排時間、放開鍵盤；做之前先備份蘋果注音的學習檔。結果用同一個 JSON 格式追加，附日期與各輸入法版本。不自動化。

### 7.4 落地

- 分支先 rebase 到 main，再照 CLAUDE.md 的合併關卡落地。套件 v1 仍釘 model-v1；model-v2 出貨時另寫套件 v2。
- **負責人**：`pilotfish:executor` 做 7.1 的 CLI 改動與 golden、`reference` 子命令、7.2 的程式部分（`table` 的新節、讀 JSON）。main 做：7.2 的抄錄與逐格核對；用真的私有資料（`--private-root` 指向 `~/side-project/shanjie-private`）跑 `tools/bench.py reference`，commit `reference-mcbpmf.json`（discordtune 欄填好）；commit 重產的 `docs/benchmark.md`；rebase；合併關卡。
- **驗收**：
  - 工具既有的測試全過；`cargo test`（含新的 unigram golden）全過；新增測試：參考列的指令確實帶 `--no-overlay`、不帶 `--lm`；參考列不出現修好／弄壞；commit SHA 改變時重算並在 stderr 說明；靜態 JSON 的每一格都有來源行號，而且該行只有一個對應的數字。
  - 合併後的分支含 `eval/bench/results/reference-mcbpmf.json`（discordtune 欄有值），`docs/benchmark.md` 主表第一列是參考列，而且和 `tools/bench.py table` 的輸出相同。
  - `table` 重產後，版本列與保留集表和現在的 `docs/benchmark.md` 逐字相同，只多出參考列與靜態節。
- **停止條件**：抄錄和 `docs/typing-test.md` 有任何一格不符；既有版本列的數字有任何變動；工具既有測試失敗。
- 預算：executor 1 回合加 1 次修正。executor 的限制照 §5（不讀保留集、不讀私有資料、私有路徑只用空的暫存目錄、不安裝、不連網、不推送）；§5 禁止改 `cli/` 的規定，只對 §7.1 寫明的 unigram 路徑 `--rows`／`--dump` 改動與它的 golden 例外。
