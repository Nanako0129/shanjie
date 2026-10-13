# X0c：外部重排模型 `0c-sequence-d512` 的離線對照（研究，不出貨）

## 1. 目標

- **起因**：維護者 2026-10-13 要研究靈犀注音 0c（https://0c.biggo.com/technology/ ）。它和善解的架構幾乎相同：詞庫加 Viterbi 產生候選，Transformer 重排前 8 名，最多看 64 字前文。它的重排模型 `Funmula/0c-sequence-d512` 以 MIT 授權公開權重與推論範例（https://huggingface.co/Funmula/0c-sequence-d512 ），和我們的 `tinylm-v3` 有三點不同：
  - 模型輸入有**注音**；
  - 參數約 3,260 萬（8 層、寬 512），我們的是 1,440 萬；
  - 字表 27,857 字，我們的是 9,714 字。
- **維護者決定（選擇題）**：做離線對照實驗。只研究、不進產品。
- **要回答的問題**：
  1. 用善解的 n-gram 前 8 名當候選，換成 0c 的模型打分，用和 `tinylm-v3` 當初**相同的選值方法**選出融合參數之後，在沒看過的集合上，0c 比 `tinylm-v3` 多改對或少改壞多少？
  2. 0c 自己建議的融合（引擎分數 + 2 × 模型分數，差距 > 0 就換）放到我們的分數尺度上，結果如何？
  3. 每列的耗時多少？0c 的選擇有多少是因為較長的前文、多少是因為聲調而改變的？0c 改對而 `tinylm-v3` 沒改對的列裡，這兩類各占多少？（§2.5 的逐列規則；只看數字，不寫出私人句子）
- **怎麼看到**：研究紀錄一節。內容有：
  - 每個集合、兩種設定的 `evalstats compare --net` 表；
  - 不能打分的列數；
  - 第 3 個問題的計數與耗時；
  - 結論與建議的下一步：例如讓我們自己的模型也看得到注音、加長前文，或不值得做。

## 2. 做法

### 2.1 取得模型與環境（main 做，維護者已同意；安全處置見 §8）

- **Python 環境**：用本機已有的 uv Python 3.13（`~/.local/share/uv/python/cpython-3.13.15-macos-aarch64-none/bin/python3.13`，不另外安裝軟體）在 `~/.cache/shanjie/x0c/venv` 建虛擬環境。
- **套件**：`pip install --isolated --only-binary=:all: --require-hashes -r experiments/x0c/requirements.txt`。
  - 只裝 `onnxruntime` 1.31.0 與它的遞移依賴（`flatbuffers`、`numpy`、`packaging`、`protobuf`；以實際解出的為準），每個套件寫明版本與 PyPI 的 sha256。
  - 不裝 `huggingface_hub`、`onnx`；不從網路升級 venv 自帶的 pip。
- **模型檔**：
  1. 先用 Hugging Face 的 API 把版本名 `d512-pf512-20260926` 解析成 commit SHA，記進研究紀錄。
  2. 用 `curl --fail -L --proto =https -o <我們取的檔名> https://huggingface.co/Funmula/0c-sequence-d512/resolve/<commit SHA>/<檔名>` 只下載五個檔到 `~/.cache/shanjie/x0c/model/`：`model.onnx`、`vocab.json`、`manifest.json`、`LICENSE`、`README.md`（內含推論範例，只讀、不執行）。
  3. 核對：`model.onnx` 的 SHA-256 等於 `manifest.json` 寫的值（`71af1f0e…`），大小 130,723,134 bytes（不是 LFS 指標檔）；`vocab.json` 等於 `556dffaf…`；另外三個記我們算的值。
- 權重與 README 都不進 repo。研究紀錄寫明來源、commit SHA、MIT 授權與著作權人。

### 2.2 固定的候選與 tinylm-v3 分數（同一次 CLI 執行）

- **CLI**：v0.4.1 tag（`5c4415a`）的 `shanjie-eval`。
- **模型**：
  - `--lm data/lm/bigram.sjlm`（`f81a021e…`），旁邊只有 `classes.sjc`（`75a5efb3…`），沒有 `kn.sjkn`；
  - `--tinylm data/lm/tinylm.sjtl`（`7e73ef67…`）。
- **旗標**：`--context`，不開詞包，`--profile chat` 或 `formal`。和 E2 選 R3′ 時的候選條件相同（S7h 契約 §3 第 1 項：`--context`、不開詞包、不載 KN），所以 0c 的選值和 R3′ 是在同一種候選上做的。
- **每個（設定, 集合）只跑一次 CLI**，同時輸出：
  - `--dump D`：n-gram 順序的前 64 名與分數。`--tinylm` 開著時，dump 仍是 n-gram 順序。
  - `--tinylm-scores T`：出貨的 Rust int8 路徑算的 `tinylm-v3` 分數檔，格式同 `score.py`，`ll` 是 log10。
- **列檔**（和 S7h 契約 §3 第 1 項相同）：
  - cvtune `~/.cache/shanjie/s7c2c-public/tune/cvtune.txt`、wikitune `~/.cache/shanjie/s7c2c-public/tune/wikitune.txt`；
  - dev302 `--dev 302`、typing76 `eval/dev/user-typing.txt`、錯字回報 `eval/dev/user-reported.txt`（84 列）、fw `eval/rerank/s7e-function-words.txt`；
  - lytune `~/.cache/shanjie/e2-public/lytune.txt`、wikitypo `~/.cache/shanjie/e2-public/wikitypo.txt`；
  - 私有：discordtune、E2 的聊天調參期與判斷期（E2 凍結的檔，路徑只在 main 的本機）。
- **D 與 T 放在 repo 外**（dump 有語料或私人訊息的候選文字，照 `CLAUDE.md`「語料不進版控」）：
  - 公開集合：`~/.cache/shanjie/x0c/run/<設定>.<集合>.{dump,v3.jsonl}`；
  - 私有集合：`~/side-project/shanjie-private/x0c/run/`，調參期與判斷期分開放在 `tune/`、`judge/` 兩個子資料夾（兩者的集合名稱都是 `chatclause`，同一個資料夾放不下）。
- D 的 SHA-256 記一次。0c 的分數檔從**同一個 D** 產生。
- 不沿用 E2 的舊分數檔：那些來自別的 dump，錯字回報也只有 72 列。

### 2.3 0c 的分數 `experiments/x0c/score_0c.py`

- **輸入**：§2.2 的 D 與列檔（`前文|句子|讀音`）。
- **輸出**：和 T 同格式的分數檔（JSON lines，只有數字）。
  - 每列的 `n`、`ng`、`ok`、`ed`、`glen` 和 T **完全相同**：同一個 D 的前 8 名，同樣的寬鬆對照。
  - `ll` = 0c 的 `log P(候選 | 前文, 讀音)` **除以 ln 10**（換成 log10，和 tinylm 的 `ll`、n-gram 的分數同尺度）。前文取該列前文欄的最後 64 字。
  - 另外兩欄：
    - `ll0`：前文空白；
    - `llr`：讀音拿掉聲調符號，每個音節都當一聲。
  - 還有一個欄位 `skip`（0／1）。
- **0c 的規則照它的模型卡與 README 的範例**（只讀不執行；`score_0c.py` 自己用 onnxruntime 與 numpy 重寫輸入格式與 `score()`）：最後一個音節另外試輕聲，取較高分。
- **載入**：先把 `model.onnx` 讀進記憶體、算 SHA-256、核對，再把同一份 bytes 交給 `InferenceSession`，`providers` 寫死 `["CPUExecutionProvider"]`；不設 `optimized_model_filepath`、不開 profiling、不註冊 custom op。`vocab.json`、`manifest.json` 用標準函式庫的 `json.load` 讀；不用 pickle。
- **不能打分的列**：前 8 名裡任一候選有下列情形時，這一列 `skip = 1`，`n` 不變，`ll`、`ll0`、`llr` 全部填 0：
  - 字表外的字（OOV）；
  - 字數不等於音節數（例如英數或標點）；
  - 音節數不在 2–32 之內。
  - 填 0 之後 `fusegrid.pick` 一定選第 0 名，也就是 n-gram 的結果。
  - 每個集合記三類各幾列。
- **一致性**：用 README 範例裡的例子重現它的 `log_prob`（README 有印出數值時），差異 ≤ 1e-4；README 沒有數值時，改成用 `score_0c.py` 的輸入格式和範例逐行對照，研究紀錄寫明這一項是對照程式、不是數值重現。
- **只有數字落地**：分數檔放在 D 旁邊（repo 外），檔名 `x0c.<設定>.<集合>.jsonl`；T 改名成 `v3.<設定>.<集合>.jsonl`，兩個都給 `judge.py` 讀。

### 2.4 選值與評估（沿用 E2 的 `judge.py` 與 `fusegrid`，不改它們）

- **檔名**：`judge.py --scores DIR --model M` 讀 `DIR/<M>.<設定>.<集合>.jsonl`。0c 的檔用 `M = x0c`，T 用 `M = v3`，放在同一個資料夾。
- **聊天設定**，每格三行，都對 n-gram：
  - **X0C**：`--select X0C --tuning <E2 的聊天調參期> --weight 1 --cap 0.005`，和 R3′ 的選法相同，格點照 `fusegrid`。
  - **X0C-DEF**：α 2、τ inf、δ 0。推導：0c 的融合是「引擎分數 + 2 × 模型分數」，兩者同一個對數底；我們的 n-gram 與 `ll` 都是 log10，所以同樣是 α = 2，等價。δ 0 對應 0c「差距 > 0 才換」：同分時 `pick` 留第 0 名，結果相同。
  - **R3′**：`tinylm-v3` 的聊天值 0.3／2／0.25，用 T 算。
- **書面設定**，每格三行：
  - **X0C-F**：照 `tinylm-v3` 書面值當初的選法（S7e：`--tuning cvtune wikitune --weight 10`，不設上限）。
  - **X0C-DEF**：同上。
  - **tinylm-v3 書面**：0.7／1／2，用 T 算。
- **0c 對 tinylm 的逐列比較**：另一支小程式 `experiments/x0c/versus.py`，用 `fusegrid.pick` 產生兩邊的 rowstats，再交給 `evalstats compare --net`。每格兩行：
  - 全部列；
  - 只算 0c 能打分的列：`skip = 1` 的列兩邊都當成 n-gram 的結果。
- **評估集合**（兩種設定都報）：
  - 公開：lytune、wikitypo、dev302、typing76、fw、錯字回報（84 列）；
  - 私有：discordtune，以及 E2 的判斷期（2026-08，6,559 個子句；維護者決定再用一次，見 §5）。
  - 調參集（E2 調參期、cvtune、wikitune）只用來選值，不放進評估表。

### 2.5 第 3 個問題的逐列規則與耗時

所有計數都用選出來的參數（聊天用 X0C、書面用 X0C-F），從分數檔逐列算：

- **前文的影響**：
  - `ll ≠ ll0` 的列數（任一候選差 > 1e-6）；
  - `pick(ll) ≠ pick(ll0)` 的列數；
  - 後者當中，0c 的前文長度（前文欄最後 64 字）大於 tinylm 的前文長度（`experiments/s7a-tinylm/tinylm.py` 的 `row_key`）的列數。
- **聲調的影響**：`pick(ll) ≠ pick(llr)` 的列數。
- **0c 改對、tinylm-v3 沒改對的列**：其中前文改變選擇的有幾列、聲調改變選擇的有幾列。
- **耗時**：
  - 在這台 Mac 的 CPU 上，量 dev302 每列打分（8 個候選，含試輕聲）的 p50／p95，單執行緒，三輪取中位數。
  - 和 `tinylm-v3` 的 7.06／10.99 ms 並列，註明兩者實作不同：0c 是 onnxruntime fp32，我們是 Rust int8。

### 2.6 保留集

不用。這一片不出貨；而且保留集不能輸出候選（`--dump` 在保留集被拒），0c 的分數程式需要候選文字。

## 3. 驗收

1. **一致性**：照 §2.3 的「一致性」一項。
1a. **沙盒**：§8 的沙盒實測寫進研究紀錄——同一份規則下試讀金鑰檔、執行 `security`、連一次網路，三個都被拒（結束碼 137 或錯誤）；正式執行以 0 結束。
2. **同一批候選**：每個（設定, 集合）的 0c 分數檔與 T 列數相同，每列 `n`、`ng`、`ok`、`ed`、`glen` 完全相同；D 的 SHA-256 有記。不能打分的三類列數，每格都有記。
3. **表**：
   - 聊天：每個評估集合 X0C、X0C-DEF、R3′ 各一行（對 n-gram），外加 0c 對 R3′ 兩行（全部列、只算能打分的列）；
   - 書面：X0C-F、X0C-DEF、tinylm-v3 書面各一行，外加 0c 對 tinylm 兩行；
   - 私有集合只有數字。
4. **第 3 個問題**：研究紀錄有每個集合的 §2.5 計數與耗時 p50／p95。
5. **測試**：`experiments/x0c/test_x0c.py`，用假的模型，不需要真模型，CI 也能跑。
   - 傳給模型的前文是前文欄的最後 64 字；改成空前文時測試要失敗。
   - 傳給模型的讀音就是該列的讀音；換成另一列的讀音時輸入會變。
   - `ll` = 假模型給的自然對數 ÷ ln 10，差異 ≤ 1e-9。
   - 不能打分的列：`skip = 1`、`n` 不變、三欄全為 0，`fusegrid.pick` 選第 0 名。
   - `ll` 全部為 0 時，每個融合設定的改對、改壞都是 0。
   - `versus.py` 只算能打分的列時，`skip = 1` 的列兩邊都是 n-gram 的結果。
   - 測試加進 `make test` 與 CI。假模型不 import onnxruntime（CI 沒裝）。
6. **真模型的檢查**（main 在本機跑，寫進研究紀錄）：dev302 聊天 `ll ≠ ll0` 的列數大於 0（前文確實進了模型）。
7. **文件**：研究紀錄一節（數字、限制、結論、建議的下一步）與 PLAN 一項。契約、程式與測試進 repo；權重、dump、分數檔、rowstats 都不進，PR 的 diff 裡沒有任何候選文字或私人句子。

## 4. 停止條件

- Hugging Face 上的固定版本拿不到、雜湊對不上，或授權不是 MIT。
- 打分的核心（輸入格式與 `score()`）需要 `onnxruntime`、`numpy` 以外的套件，或需要連網。只負責下載的 `huggingface_hub` 不算，我們不用它。
- 範例的 `log_prob` 重現不了（差異 > 1e-4）。
- 任一公開集合不能打分的列超過 20%：比較就不公平，先回報。
- T 有 `n ≥ 2` 卻沒有 `ll` 的列（tinylm 的 `TooLong`）：`fusegrid.pick` 會出錯；有就停，回報列數。
- 超過時間預算。

## 5. 維護者的決定

- **判斷期再用一次**（2026-10-13，選擇題）：
  - E2 寫的是「判斷期只算這一次」（研究紀錄 2026-10-13）。這一片再用它來比兩個模型，只評估、不拿來選值。
  - 列檔用 E2 凍結的同一個檔（`aa87981a…`）；候選照 §2.2 用 v0.4.1 的 CLI 重產，所以 R3′ 的數字可能和 E2 表上的不同。研究紀錄要寫明這是判斷期第二次被讀。
  - 之後若有決定要依據判斷期的數字（例如語意校正強度設定、換模型）：判斷期已看過兩次，要另找未見的月份，或把這點寫進那一片的限制。

## 6. 範圍外

- 把 0c 的模型放進 App、改用 ONNX、重訓我們的模型、標點模型（`0c-punct-d512`）。
- 寫任何「0c 比較好或比較差」的對外說法；研究紀錄只寫在我們的集合上量到的數字。

## 7. 負責人、順序、預算

- **executor**（先做；只碰公開集合）：
  - `score_0c.py`、`versus.py`、測試；
  - 公開集合的 CLI（§2.2）與 0c 打分；
  - 書面設定的全部表：X0C-F 用公開的 cvtune＋wikitune 選值，不需要私有資料；
  - 聊天設定裡不需要選值的行：X0C-DEF、R3′；
  - 公開集合書面設定的 §2.5 計數。
  - 不可以：
    - 讀私有資料、`eval/holdout`、`~/.cache/shanjie/work`；
    - 連網、安裝套件；
    - push、開 PR；
    - 改 `core/`、`cli/`、`macos/`。
- **main**：
  - §2.1 安裝與下載（維護者已同意），在 executor 之前；
  - executor 交回之後：私有集合的 CLI 與 0c 打分，在聊天調參期選出 X0C 的 (α, τ, δ)；
  - 用選出的值做聊天設定所有集合（公開與私有）的 X0C 行、0c 對 R3′ 的 `versus.py` 兩行、聊天的 §2.5 計數；
  - 私有集合的書面設定表；
  - 第 3 項驗收第 6 點的真模型檢查、耗時、研究紀錄。
- **順序**：契約過 plan-verifier → main 裝環境、下載、核對雜湊，寫好 `run_sandboxed.sh` 並做沙盒實測（驗收 1a）→ executor（載入真模型一律經過 `run_sandboxed.sh`）→ main 跑私有集合與聊天選值 → /code-review → fresh verifier → CI → merge。
- **預算（推論）**：
  - executor 1 回合加 1 次修正，整片約 3–4 小時。
  - 0c 打分的速度未知；公開集合合計約 1.6 萬列，每列 8 個候選，再乘上 `ll`、`ll0`、`llr` 三次。
  - 超過 5 小時先回報。

## 8. 安全審查（`pilotfish:security-reviewer`，2026-10-13）與處置

- **F1 推論範例在 README 裡，用 `huggingface_hub` 下載、沒固定版本，照字面會觸發 §4**：修。README 只讀不執行；`score_0c.py` 自己重寫；§4 改成只看打分核心；不裝 `huggingface_hub`。
- **F2 onnxruntime 的原生程式有記憶體錯誤類風險**（外部資料路徑穿越在 1.24.1 修掉；另有 heap overflow 的揭露）：修。鎖 1.31.0、只用 CPU EP、不寫最佳化模型檔、不開 profiling、不註冊 custom op；不裝 `onnx`；最後一道防線是 F4 的沙盒。
- **F3 下載方式**：修。用 commit SHA，不用分支或 tag 名稱；用 curl 下載並由我們取檔名，不用 `snapshot_download` 或 `git clone`，避免 symlink 與 LFS；核對雜湊與大小；從記憶體裡已核對的 bytes 載入。
- **F4 沙盒只寫給私有集合**：修。**所有載入真模型的執行**（main 與 executor、公開與私有）都經過 `experiments/x0c/run_sandboxed.sh` 呼叫 `sandbox-exec`，寫法照 `docs/contracts/sbench.md` §3.7：
  - 拒絕網路；
  - 拒絕讀取 `~/side-project/shanjie-private`（私有集合的輸入由外層 shell 開檔，以檔案描述子或 stdin 傳入；做不到就只對那幾個輸入檔開放讀取，實測後二選一，寫進研究紀錄）、`~/.ssh`、`~/.config`、`~/Library/Keychains`、`~/.codex`、`~/.grok`、`~/side-project/.hackmd_key`；
  - 拒絕寫入 `$HOME`，只開放輸出資料夾；
  - 只允許執行 venv 的 Python；以 `python -I -B` 執行，工作目錄不設在模型資料夾。
  - 規則的實際效果在第一次用之前實測（驗收 1a），測不過就停。
- **F5 pip 供應鏈**：修，見 §2.1。
- **F6 私有集合的 dump 位置**：修，見 §2.2。
- **F7 `json.load`、不用 pickle；假模型不 import onnxruntime**：修，見 §2.3 與驗收 5。
- 前提：假設發布者是誠實的，目前沒有惡意跡象；以上是降低最壞情況，不是指控。沙盒規則的效果以實測為準。

