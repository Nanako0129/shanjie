# S5p 契約：雲端 System One 判斷器的提示詞改進與可行性比較（Jev、Clef-flash）

使用者 2026-10-05 要求「先改進看看 Jev 的輸入提示」，接著說「Clef 也是 System One，比較雲端這兩者可行性」。

- S5j 的 Jev 題目（`experiments/s5-judges/s5.py` 的 `INSTR`、`POS_INSTR`）是英文清單題，state 只有前文，跑之前就寫死，之後沒改過。
- 這一片照 TypeSafe 官方文件的建議改寫題目，讓 Jev 和 Clef-flash 在同一批公開題目上各跑每個變體，在調整半挑最好的變體，在另一半驗證。最後給出兩者的雲端可行性比較。
- 結果用於 H（一鍵校正）與 S6（雲端校正）選擇供應商與題型。這片**不改** `core/`、`macos/`、`data/`、`eval/`，也不接進輸入法。

## 1. 依據（TypeSafe 文件，2026-10-05 讀取）

- state 用具名欄位的巢狀 JSON；題目用加反引號的路徑指向特定值（`how-to-build-with-system-one`）。
- 一題只問一個判斷；同一個 state 的多題一起送。
- 相近的選項，用含 `what`、`not_for`、`examples` 的物件寫對照說明（`primitives/choice`）。
- 重排序的官方做法是每個候選一題 noul，依 P(true) 排序，noul 的 criteria 寫出 true／false 的定義（`cookbooks/rerank_typesafe`）。
- 文件沒有提到選項順序偏差，也沒有提到非英文的題目。

## 2. 模型、端點與金鑰

| 供應商 | 模型 | 端點 | 金鑰 |
|---|---|---|---|
| TypeSafe | `jev-1.13.0`（釘死，同 S5j） | `experiments/jev/jev.py` 的端點 | `~/.config/typesafe/api_key`（`jev.py` 的規則，不印出） |
| Cloudflare | `clef-flash`（Workers AI 沒有版本釘選；記錄執行日期與回應的 `model`） | `…/ai/run/@cf/cloudflare/clef-flash` | 環境變數 `CF_AI_TOKEN`、`CF_ACCOUNT_ID`，付費呼叫只由 main 執行 |

- 兩者共用 S5c 的驅動程式與 `Client`（`experiments/s5-clef/`，S5c 契約 `fa4471e`、工具 `8605fe7`），只換端點與金鑰；**不用** `experiments/jev/jev.py` 的 `Client`，它的錯誤訊息會帶出回應本文的前 200 字。S5c 的工具合併前不開工。
- 金鑰在白名單與雜湊檢查之後才讀：Jev 讀環境變數 `TYPESAFE_API_KEY`，沒有就讀 `~/.config/typesafe/api_key`；Clef 讀 `CF_AI_TOKEN`、`CF_ACCOUNT_ID`。
- **所有網路呼叫都由 main 執行。** executor 一律離線，用假的 HTTP 層測試。

## 3. 集合與切分（只用公開集合）

- cvtune：S5j 的 1,000 列抽樣（prep 檔唯讀、先核對雜湊，表同 S5c 契約 §2）。用 prep 檔每列的 `half` 欄位：`A` 是**調整半**，`B` 是**驗證半**（和檔案順序的前 500／後 500 列一致）。
- 只有一個候選的列不送判斷器，計分時用第 1 名（同 S5j），所以各變體在 A、B 半的列完全配對。
- dev302、typing76 只跑每個供應商挑中的變體（V0 已有結果）。
- dev302、typing76：只記錄，不參加判斷。
- discordtune 不送任何雲端服務。驅動程式在讀金鑰與連網之前，對白名單以外的集合以非 0 結束。
- 每列的讀音用 prep 檔的 `syls` 欄位，以空白連接成注音字串。

## 4. 題目變體（文字逐字寫死，看結果後不改）

共同的 state（V1–V4）：

```json
{"context": "<前文>", "reading": "<注音，空白分隔>", "candidates": {"c1": "<候選1>", "...": "...", "ck": "<候選k>"}}
```

公開集合的前文都是空字串（同 S5j）。

- **V0（基準）**：S5j 的 J-sent **正序**原樣（`INSTR`，8 句選一）。Jev 的 V0 = S5j 的 `jev-sent-fwd.jsonl`（雜湊見 S5c 契約 §2），不重跑；Clef 的 V0 = S5c 的 `clef-sent-fwd.jsonl`（雜湊在 S5c 完整執行後由 main 記進本契約 §12，驅動程式讀之前核對）。
- **V1（逐候選 noul，英文）**：每個候選一題，題目 id `q_c1`…`q_ck`，同一列的 k 題放在同一個請求：
  - instructions：「A user in Taiwan typed the Zhuyin (Bopomofo) reading `reading`. The text already written before it is `context` (it may be empty). Is `candidates.cN` exactly the sentence the user meant to type?」（`cN` 換成該題的候選 id）
  - criteria：
    - `true`：`{"what": "correct Traditional Chinese characters as used in Taiwan, grammatical and sensible given the context, and matching the reading", "examples": ["我等一下再打給你", "明天的會議改到下午三點"]}`
    - `false`：`{"what": "a same-sounding sentence with a wrong character, a simplified or mainland form, or a phrase that does not make sense", "not_for": "sentences that are merely informal but correct", "examples": ["我等一下在打給你", "明天的會意改到下午三點", "這間店的拉面很好吃"]}`
  - 挑 P(true) 最高的候選，同分取名次較前者。不跑反序。「k 題放在同一個請求時和順序無關」是推論，沒量過，報告要標明。
- **V2（結構化 choice，英文）**：**每列一個請求**，state 是上面的單列 state，一題 choice，選項 `c1`…`ck` 的說明是該候選句。instructions：「A user in Taiwan typed the Zhuyin (Bopomofo) reading `reading`. The text already written before it is `context` (it may be empty). Every option is a sentence with that same reading. Choose the one the user meant: correct Traditional Chinese as used in Taiwan, grammatical, and sensible in context.」正序、反序各跑一次。
- **V3**：V1 的 instructions 與 criteria 改成繁體中文。
  - instructions：「台灣的使用者用注音輸入法打了讀音 `reading`，之前已經寫的文字是 `context`（可能是空的）。`candidates.cN` 是不是使用者真正要打的那一句？」
  - true：「台灣使用的正確繁體字、合乎文法，也符合前文與讀音」，例子同 V1。
  - false：「讀音相同但有錯字、簡體或大陸用語，或意思不通的句子」，`not_for`「只是口語、但用字正確的句子」，例子同 V1。
- **V4**：每列一個請求，state 與 V2 相同，instructions 改成繁體中文：「台灣的使用者用注音輸入法打了讀音 `reading`，之前已經寫的文字是 `context`（可能是空的）。每個選項都是同一個讀音的句子。選出使用者真正要打的那一句：台灣使用的正確繁體字、合乎文法、在前文下說得通。」正序、反序各跑一次。
- V1–V4 的例句是這片自寫的，不得取自任何評測集。main 2026-10-05 已比對：上面 5 句在 `eval/dev`、`eval/sets` 與 cvtune、dev302、typing76 的 prep 檔裡都查不到（`eval/holdout` 不比對）。

解析規則同 S5c：noul 取 P(true)；choice 有 `choice` 欄位就用、否則取機率最高者（同分取編號最小）；缺機率算解析失敗。每題的原始機率都存下來。

## 5. 判斷規則（預先寫死）

1. **在 A 半挑變體**：每個供應商各自在 V1–V4 裡，挑 A 半整句正確率最高者（V2／V4 只用正序的結果）；同分取編號較小的變體。
2. **在 B 半驗證**：每個供應商跑挑中的變體（choice 變體正序、反序都跑，但只用正序計分與檢定，反序只用來算翻轉率），和該供應商 V0 在 B 半同一批列配對 McNemar：
   - p < 0.05 且挑中的變體淨值為正：「提示詞改進成立」；
   - p ≥ 0.05：「沒有顯著差別」；
   - p < 0.05 且淨值為負：「改寫比原題差」。
3. **對第 1 名**：挑中的變體在 B 半對第 1 名 p < 0.05 且淨值為正，才算 H／S6 的候選。
4. **Jev 對 Clef**：兩者各自挑中的變體，在 B 半逐列配對 McNemar，彙總規則同 S5c §3。
5. 這片在 A 半比了 8 組（2 個供應商 × 4 個變體），只在 B 半做 5 個預先指定的檢定：每個供應商的「挑中變體 vs V0」與「挑中變體 vs 第 1 名」，加上「Jev 挑中 vs Clef 挑中」。不做其他事後比較。

## 6. 可行性比較表（報告必附）

兩個供應商各一欄，數字取 B 半挑中變體的實測：

- 整句正確率、對第 1 名的修好／弄壞／p；
- 每次請求與每列的延遲 p50／p95；
- 每 1,000 列的費用：以回應的 token 用量換算。Jev 的價格以 TypeSafe 帳單頁或 API 回應為準；查不到就標「未證實」，不估。
- 能不能釘版本；請求上限（題數、選項數、context）；
- 權重是否開放、能不能自架；
- 資料保留與訓練條款：只寫有出處的，沒有就寫「未證實」。S6 前要核對（PLAN）。

## 7. 隱私

- 只送公開集合（cvtune 的 CC BY 句子、dev302 與 typing76 的 CC0 句子）與自寫例句。
- stdout、stderr 與錯誤訊息不得含句子內容、金鑰、帳號 ID 或請求網址。
- cvtune 的逐列結果寫進 `~/.cache/shanjie/work/s5-prompts/`；dev302、typing76 可以進 `experiments/s5-prompts/results/`；冒煙測試的輸出一律放在 `~/.cache/shanjie/work/s5-prompts/` 底下，不進 repo；跑完後 `git status --porcelain` 不得出現含 `-n20` 的路徑。

## 8. 負責人、執行與預算

- 前置：
  - Jev 的部分：S5c 的工具合併。
  - Clef 的部分：再加上 `CF_AI_TOKEN`、`CF_ACCOUNT_ID` 可用、S5c 在 cvtune 的完整執行完成，而且 main 把 `clef-sent-fwd.jsonl` 的雜湊記進 §12。
- `pilotfish:executor`（離線）：在 S5c 的驅動程式上加供應商切換與 V1–V4，回報：
  - (a) 白名單在讀金鑰與連網之前生效；
  - (b) 假 HTTP 層的錯誤路徑輸出不含句子、金鑰、帳號 ID；
  - (c) 解析單元測試：V1／V3 的逐候選 noul（P(true) 取最大、同分取名次前者），V2／V4 的正序與反序換回原編號；
  - (d) 用 dev302 第 0 列印出 V1–V4 的請求 JSON 給 main 檢查格式，只用 dev302，那是 CC0，可以印句子；
  - (e) `git diff` 沒有 discordtune 的路徑或內容；
  - (f) 假 HTTP 層測試：每個供應商的請求數到上限就停；V1／V3 的假回應 k 個 P(true) 全部相同時，標記為同分，不悄悄算成選對 c1；
  - (g) 所有例句（含 false 例句）對 `eval/dev`、`eval/sets` 與三個 prep 檔的 `truth`、`cands` 做字串比對，命中 0 筆；
  - (h) Clef V0 檔雜湊不符時以非 0 結束。
- main 執行全部網路呼叫：先用 dev302 前 20 列冒煙（每個供應商、每個變體），再跑 A 半，選變體，再跑 B 半與 dev302、typing76。
- 請求數（每列一個請求，只有一個候選的列不送）：每個供應商 A 半 V1、V2 正、V2 反、V3、V4 正、V4 反各約 500 次，共約 3,000 次；B 半挑中的變體約 500–1,000 次；dev302、typing76 約 380–760 次。每個供應商最多約 4,800 次，兩個合計最多約 9,600 次。
- **使用者同意（2026-10-05）**：使用者選了「上限 2 美元」，只送公開集合。
- **停止門檻**（兩個都要在執行中能算）：
  - Clef：以回應的 token 用量 × 每百萬輸入 token 0.09 美元換算；沒有用量時，以請求 JSON 的字元數當上限估計。
  - Jev：TypeSafe 文件沒有公布單價，改用**硬性請求數上限 6,000 次**。另外以 S5j 的實際帳單（J1 300 題約 0.004 美元）按題數推估費用，只用來報告。
  - 兩個供應商合計的估計費用超過 2 美元，就停。

## 9. 停止條件

- 401／403、模型不存在、回應沒有逐選項機率：停下回報。
- 任一變體解析失敗超過 1%：停下回報。
- 冒煙測試裡任一 choice 變體正序、反序挑第 1 個的比例都 ≥ 0.95：停下回報。
- 冒煙測試裡 V1 或 V3 挑 c1 的比例 ≥ 0.95，或 k 個 P(true) 全部相同的列 ≥ 0.95：停下回報（像是反引號路徑沒被解析，或 k 題被當成同一題）。冒煙報告要印出這兩個比例。
- Jev 的請求數到 6,000 次：停下回報。
- 429、5xx、逾時：同一個請求最多重試 3 次（間隔 2、4、8 秒），仍失敗就停。
- 費用超過 2 美元：停下回報。
- 中斷後同一個指令重跑，已有答案的請求不重送。

## 10. executor 不可以做的事

- 不得讀金鑰檔或鑰匙圈、不得連網、不得用真的金鑰。
- 不得讀或送出 discordtune；不得讀 `eval/holdout/`。
- 不得改 `core/`、`macos/`、`data/`、`eval/`、`site/`、`.github/`、`.gitignore`；除了 commit 不做其他 git 動作。
- 不得安裝軟體、開 GUI App。

## 11. 回滾

只新增 `experiments/s5-prompts/` 與 `~/.cache/shanjie/work/s5-prompts/`；刪掉即可。

## 12. 執行前要補記的雜湊

- Clef V0：`~/.cache/shanjie/work/s5-clef/cvtune/clef-sent-fwd.jsonl` 的 SHA-256。S5c 的 cvtune 完整執行之後由 main 記在這裡，Clef 的 A 半開跑前必須已經寫入。
