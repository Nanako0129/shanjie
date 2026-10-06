# S5c 契約：Cloudflare Clef-flash 對照 Jev（雲端、只用公開集合）

使用者 2026-10-05 要看 Cloudflare 的 Clef 能不能取代 Jev，選了「雲端對照 Jev（公開集合）」。Clef-flash 是 Cloudflare 2026-10 發佈的決策模型：9B 參數，從 Qwen3.5-9B 後訓練，Apache-2.0，API 格式和 Jev 相容（state 加 typed questions，回傳每個選項的機率）。在 Workers AI 上執行，價格是每百萬輸入 token 0.09 美元。

這片只回答一件事：**在 S5j 跑過 Jev 的同一批公開題目上，Clef-flash 比 Jev 好還是差。** 結果用於 H（一鍵校正）與 S6（雲端校正）選擇供應商。這片**不改** `core/`、`macos/`、`data/`、`eval/`，也不接進輸入法。

## 1. 端點、模型與金鑰

- 端點：`POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/cloudflare/clef-flash`，body 帶 `"model": "clef-flash"`。
- Workers AI 沒有提供版本釘選。報告記下執行日期與回應裡的 `model` 欄位；同一次完整執行不換模型。
- API token 與帳號 ID 從環境變數 `CF_AI_TOKEN`、`CF_ACCOUNT_ID` 讀。不印出、不寫檔、不放進指令列。
- 只用 `clef-flash`，不用完整版 Clef（參數量未公開，延遲 209 ms）。

## 2. 題目與集合（和 S5j 的 Jev 完全相同，可逐列配對）

- 題目、候選、抽樣都用 S5j 的 prep 檔，配對用 S5j 的 Jev 逐列結果。**全部唯讀使用，開啟前先核對雜湊**（main 2026-10-05 記下）；檔案不存在或雜湊不符就以非 0 結束並印固定字串，不重新產生。不呼叫 `s5.py` 的 `prep()`、`read_rows()`、`set_dir()`（`read_rows()` 會在檔案不見時用當下的模型悄悄重產）。路徑中的 repo 檔是相對於 repo 根目錄的 `experiments/s5-judges/results/`。

  | 檔案 | SHA-256 |
  |---|---|
  | `~/.cache/shanjie/work/s5-judges/cvtune/rows.jsonl` | `98eec57ff74ca1cc423804c3cfa1afb250314f3eb32c869f728e60a1ce4e170d` |
  | `~/.cache/shanjie/work/s5-judges/cvtune/jev-sent-fwd.jsonl` | `ea8b5637606e0589758b7d51356ce1a29d21af343fbd02e1e6bcf3d64b925594` |
  | `~/.cache/shanjie/work/s5-judges/cvtune/jev-sent-rev.jsonl` | `9cb4e99dcd1b18707b0c8cf1e64c4dcde8646a102a3b24cd55349ce36b735462` |
  | `~/.cache/shanjie/work/s5-judges/cvtune/jev-pos.jsonl` | `cfea93c3db4154a1f391c4c5d4e81fa7a7a8073256ad4929906e12eadcdd70a0` |
  | `experiments/s5-judges/results/dev302/rows.jsonl` | `70aaee641fa78b757683c47595197745075d69b92ccb0b9d5846cd39e7f65c39` |
  | `experiments/s5-judges/results/dev302/jev-sent-fwd.jsonl` | `5a909cf09c3539609306614ebf3b0a658df18e460935380615f4dc3ac3b5fd5f` |
  | `experiments/s5-judges/results/dev302/jev-sent-rev.jsonl` | `a0a2d6c3a832b7319bbf3853fdefc48406e3ff1b433b206248d9567e768cab53` |
  | `experiments/s5-judges/results/dev302/jev-pos.jsonl` | `844f3a43033df8a6b7abb7703d9b42ac636c3a88567138a4f4fb29aa411f9195` |
  | `experiments/s5-judges/results/typing76/rows.jsonl` | `ed0bb91342ad9bc64cefe4189e809725e0fca5b3acbe7eb8ef2919640c7bd34a` |
  | `experiments/s5-judges/results/typing76/jev-sent-fwd.jsonl` | `0577a26da9a7a77b6e6ca23c45a4cf9ace25e31f60c9174246548e17ca534e61` |
  | `experiments/s5-judges/results/typing76/jev-sent-rev.jsonl` | `32bb17c4c20eca5382439a6a2a7c7e55cef2abc383b49b6e5511e334fbd75fca` |
  | `experiments/s5-judges/results/typing76/jev-pos.jsonl` | `37f22a6a86589dbf3c554bc60bc23eb79e7d9cf6eebd8be445c46ab45aab04a0` |
  | `~/.cache/shanjie/work/s5-judges/cvtune/score.json` | `e6420bd25f37bbf9fa43079fcb3a797b1641ecb6e4ca471cae1e72e69cf3cef0` |
  | `experiments/s5-judges/results/dev302/score.json` | `2a2ebeb3f5b03ed447021cf7ff7c7deba7f98977bb47d543a79ac57a7f711373` |
  | `experiments/s5-judges/results/typing76/score.json` | `599873911d9d3f2447c51ab1b2a0077506c9e8d6eed3a27015bd1d3ae77ee5fb` |

- S5j 的這兩個位置（`~/.cache/shanjie/work/s5-judges/`、`experiments/s5-judges/results/`）在執行前後不得多出檔案，修改時間也不得改變。
- 集合只有 cvtune（S5j 的 1,000 列抽樣）、dev302、typing76。
- 條件逐字沿用 S5j 的 Jev 程式行為（`experiments/s5-judges/s5.py` 的 `jev()`）：
  - **C-sent-fwd、C-sent-rev**：`INSTR`，8 句選一，狀態 `{"rows": [{"context": 前文}, …]}`，每次請求 20 題。
  - **C-pos**：`POS_INSTR`，逐位置題型，狀態裡的前文是空字串（和 S5j 的 J-pos 實際送出的一致），採用規則同 J-pos（機率 ≥ 0.5 且 ≥ 2 倍「不換」）。
- **公開集合的前文都是空字串**（S5j 的 `s5.py` 只替 discordtune 載入前文），所以 C-sent 和 C-pos 送出的前文都是空的，和 J-sent、J-pos 實際送出的一致。
- 回應的解析：
  - C-sent：S5j 的 J-sent 用的是 Jev 回應的 `choice` 欄位。為了配對一致，Clef 回應有 `choice` 就用它；沒有就取機率最高的選項，同分取編號最小的。兩者都有時，另報 `choice` 和機率最高者一致的比例。
  - C-pos：照 S5j 的 `pos_decision()`，用逐選項機率套 J-pos 的規則。`pos_decision()` 在缺機率時會悄悄退回用 `choice`，所以呼叫前先檢查每個選項都有機率，缺了就算解析失敗。
  - 每一題的原始逐選項機率都和決定一起存下來。
  - 回應格式和假設不同（例如沒有逐選項機率），停下回報，不自行詮釋。
- 重用 S5j 的常數與純函式（`INSTR`、`POS_INSTR`、`positions()`、`pos_decision()`、`mcnemar()`、`jev_conds()` 的計算方式、`reference/proto/eval.py` 的 `lenient`）時，只 import 或照抄，不呼叫會寫檔的 `set_dir()`、`read_rows()`、`score_set()`。

## 3. 指標與判斷

- 和 S5j §4 相同：寬鬆對照的整句正確率、oracle@8、A1b@8、修好／弄壞／McNemar（對第 1 名），挑第 1 個的比例、順序翻轉率（C-sent-fwd vs C-sent-rev）、每次請求的延遲 p50／p95，與輸入 token 數。
- **和 Jev 逐列配對**：C-sent-fwd 對 J-sent-fwd、C-sent-rev 對 J-sent-rev、C-pos 對 J-pos，報修好／弄壞／McNemar。Jev 的逐列結果用 §2 表中的檔案。
- **預先寫死的判斷**（照 S5j §5 的雲端偏離：discordtune 不能上雲端，證據比本機實驗弱，報告要標明）。三組條件各自判斷，都只看 cvtune：
  - **對第 1 名**：C-sent-fwd、C-sent-rev、C-pos 各自 p < 0.05 且淨值為正，那個條件才算 H／S6 的候選。
  - **對 Jev**：三組配對（C-sent-fwd／J-sent-fwd、C-sent-rev／J-sent-rev、C-pos／J-pos）各自做 McNemar。彙總規則：
    - 至少一組 Clef 顯著較好（p < 0.05 且 Clef 淨值為正），而且沒有一組顯著較差，才寫「Clef 比 Jev 好」；
    - 至少一組顯著較差，而且沒有一組顯著較好，寫「Clef 比 Jev 差」；
    - 都不顯著，寫「沒有顯著差別」，可以依速度與價格選；
    - 有好有壞，寫「混合」，逐組列出。
  - dev302、typing76 只記錄。

## 4. 隱私

- **程式層級的白名單**：驅動程式只接受 cvtune、dev302、typing76；其他集合（包括 discordtune）在讀取金鑰與開任何網路連線之前，就以非 0 結束。
- stdout、stderr 與錯誤訊息不得含句子內容與金鑰，只能有列號、數字、HTTP 狀態碼與固定字串。
- cvtune 的逐列結果寫進 `~/.cache/shanjie/work/s5-clef/`；dev302、typing76 的逐列結果可以進 `experiments/s5-clef/results/`。

## 5. 產出與檔案範圍

- 新增 `experiments/s5-clef/`：`README.md`（設定、結果表、限制）、驅動與計分的 Python、`results/`（只放 dev302、typing76）。
- `docs/research-log.md` 由 main 寫。其他檔案都不改。

## 6. 負責人、執行與預算

- **只有 main 執行付費呼叫**。驅動程式從環境變數 `CF_AI_TOKEN`、`CF_ACCOUNT_ID` 取得 token 與帳號 ID；agent 的 brief 一律寫明不執行 `clef_run.py`、不用 token。變數不在時，main 停下回報。
- `pilotfish:executor` 實作，**不連網、不用真的 token**，回報：
  - (a) `--sets discordtune` 在讀環境變數與連網之前就以非 0 結束；
  - (b) 用注入的假 HTTP 層（不連網）測錯誤路徑：401、429、5xx、逾時、回應沒有逐選項機率。輸出都不含句子內容、token 與帳號 ID（請求網址含帳號 ID，錯誤訊息不得印出網址）；
  - (c) **解析的單元測試**，用假造的回應：正序裡 c3 機率最高時取第 3 個候選；反序裡同樣的回應要換回原本的編號；C-pos 規則；缺機率算解析失敗；
  - (d) **計分程式的重現檢查**：用 §2 表中 S5j 的 `jev-*.jsonl` 當輸入，新計分程式算出的 J-sent-fwd、J-sent-rev、J-pos 數字要和 S5j 的 `score.json` 完全相同（正確率、修好／弄壞、p、挑第 1 個比例、翻轉率）；不得寫進 S5j 的目錄；
  - (e) `git diff` 沒有 discordtune 的路徑或內容。
- **main 跑真的冒煙測試**：dev302 前 20 列，每個條件都跑到。冒煙測試的所有輸出一律寫進 `~/.cache/shanjie/work/s5-clef/dev302-n20/`，不進 repo；完整執行不沿用這些列。跑完後 worktree 的 `git status --porcelain` 不得出現含 `-n20` 的路徑。回報逐選項機率的鍵名、C-sent-fwd 與 C-sent-rev 各自挑第 1 個的比例和翻轉率；另用一個錯的 token 確認 401 路徑。
- 完整執行由 main 跑：約 400 次請求（和 S5j 的 Jev 相同），費用估計不到 0.1 美元，不吃本機資源。
- **使用者同意與預算**：使用者 2026-10-05 選了「雲端對照 Jev（公開集合）」，預算上限 1 美元。Cloudflare 的部落格寫「不讀、不存、不拿請求訓練」；Workers AI 文件頁沒有資料保留的說明，這點未核對。送出的只有 CC0 與 CC BY 的公開句子。
- 不另做 security-reviewer：只送公開資料；token 只經由環境變數提供，和 S5j 的 Jev 金鑰（`TYPESAFE_API_KEY`）相同，付費呼叫只由 main 執行，agent 的 brief 禁止使用。
- 預算：executor 1 回合加 1 次修正。

## 7. 停止條件

- 401／403（token 權限不對）、模型不存在、回應沒有逐選項機率：停下回報。
- 429、5xx、逾時：同一個請求最多重試 3 次（間隔 2、4、8 秒），仍失敗就停下回報。
- 冒煙測試裡 C-sent-fwd 與 C-sent-rev 挑第 1 個的比例都 ≥ 0.95：停下回報（像是解析壞掉，或模型照位置挑）。
- 任一條件解析失敗超過 1%：停下回報。
- 費用超過 1 美元：以回應的 `usage` 換算；回應沒有 `usage` 時，以請求 JSON 的字元數當作 token 數的上限估計，並在報告裡標明是估計。
- 完整執行中斷：同一個指令重跑時從中斷處續跑，已有答案的請求不重送。

## 8. executor 不可以做的事

- 不得改 `core/`、`macos/`、`data/`、`eval/`、`site/`、`.github/`；除了 commit 不做其他 git 動作。
- 不得讀 `eval/holdout/`；不得讀或送出 discordtune 的任何內容。
- **不得讀鑰匙圈**，不得改、lock 或 unlock 鑰匙圈；不得用真的 token，也不得連網。
- 不得安裝軟體、開 GUI App；不得跑任何網路呼叫或完整執行（真的冒煙測試由 main 跑）。

## 9. 回滾

只新增 `experiments/s5-clef/` 與 `~/.cache/shanjie/work/s5-clef/`；刪掉即可。
