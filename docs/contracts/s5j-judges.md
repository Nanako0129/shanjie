# S5j 契約：判斷器前置實驗（Apple Foundation Models 與 Jev）

使用者 2026-10-04 核准「三件一起」：S2r-2、S4 契約，以及這個實驗。目的是在善解**現有的候選**上，量兩種判斷器能帶來多少：本機的 Apple Foundation Models、雲端的 Jev（使用者同意，只用公開集合）。順便量外部專案借鏡清單裡的三件事：選項順序偏差、前文的效果、只在不確定時介入。結果是 S5（端上重排）的設計依據；這片**不改** `core/`、`macos/`、`data/`、`eval/`。

起點是 2026-10-04 的探測（`docs/research-log.md` 同日條目）。在 dev302 上給前 8 名、沒給前文、照排名順序排列，Apple 模型的整句正確率是 262／302；善解第一名是 238／302。修好 38 句、弄壞 14 句，p = 0.0012，延遲 p50 454 ms、p95 787 ms。模型有 77% 的時候挑第一個。

## 1. 候選與基準

- 候選用 main 的評測 CLI 產生（這個 worktree 的 `target/release/shanjie-eval`，含 S2r）：`shanjie-eval --lm data/lm/bigram.sjlm --profile chat --rows <檔> --dump <檔>`。
- 每列取 dump 裡前 8 個**不同的**字串，依名次排列。基準是第 1 名。
- **只取 8 名是這片刻意縮小的範圍**：S5 的 A1b 是在 64 名候選上量，但 8 選 1 的延遲與提示長度都比較小，也是 J1 的設定。這片的數字是 S5 的參考，不是 A1b 的量測。
- **分差** = 第 1 與第 2 個不同字串的分數差。
- 讀音用各集合原本的讀音，不用探針。

## 2. 集合與抽樣

| 集合 | 列 | 角色 | 判斷器 |
|---|---|---|---|
| discordtune | 隨機 1,000 列（`random.Random(20261004).sample`，從 `iter2.all_sets()`），再用同一個種子分成 A、B 兩半各 500 列 | 主要調整集：門檻只在 A 半選，在 B 半檢定 | 只用 Apple（本機）；**不送 Jev** |
| cvtune | 隨機 1,000 列（同一個種子） | 守門：用 discordtune 選好的門檻驗證 | Apple、Jev |
| dev302、typing76 | 全部 | 挑戰集，只記錄 | Apple、Jev |

保留集不碰。wikitune 不用。

## 3. 條件（提示與規則在跑之前寫死，看結果後不改）

**Apple**：
- 模型與輸出：`SystemLanguageModel(guardrails: .permissiveContentTransformations)`，純文字輸出、`sampling: .greedy`、`maximumResponseTokens: 8`；每列一個新的 session。
- 指示（instructions）照探測：「你是台灣繁體中文注音輸入法的選字助手。使用者用注音打了一句話，下面列出讀音相同的候選句子。選出使用者最可能想打、最符合台灣日常用法的那一句，只回答編號。」
- 提示：編號清單（`1. 句子`），最後加「只回答一個數字（1 到 k）。」
- 解析：取回答裡第一個 1 到 k 的數字。解析不出或被安全過濾擋下的列，用基準，並且各自計數。
- 條件：
  - **A-fwd**：照名次順序，不給前文。
  - **A-rev**：順序反過來；挑到的編號換回原本的候選。
  - **A-both**：A-fwd 和 A-rev 挑到同一個候選才採用，否則用基準。
  - **A-ctx**：只用 discordtune 抽樣裡有前文的列。照名次順序，提示最前面加一行「前文：…」，和同一批列的 A-fwd 配對比較。

**Jev**（`experiments/jev/jev.py`，模型釘死 `jev-1.13.0`，只送 cvtune 抽樣、dev302、typing76）：
- **J-sent**：題目和 J1 一樣（`experiments/jev/j1_choice.py` 的 `INSTR`），8 句整句選一，每次請求 20 題。分成照名次順序（J-sent-fwd）與反過來（J-sent-rev）。
- **J-pos**：Jevboard 的題型。
  - 出題：在第 1 名句子上，候選之間字不同的每個位置各出一題。選項是「只把那個位置換成某個候選的字」的整句，含不換（第 1 名本身）。題目狀態只放前文，不放目前的句子。
  - 題目文字（英文，照 J1 的風格）：「A user in Taiwan typed a Zhuyin (Bopomofo) phonetic input. Every option below is the same sentence with one character position varied, and all options are pronounced the same. Choose the option the user most likely meant: correct Traditional Chinese characters as used in Taiwan, grammatical, and sensible.」
  - 採用規則：回應有各選項機率時，照 Jevboard 的規則：機率 ≥ 0.5 而且 ≥ 2 倍的「不換」才採用。沒有機率就直接採用選中的選項。實際用了哪一種要記錄下來。
  - 組合：所有採用的位置一起換成一句，只做一輪。

**門檻（gating）**：
- 只有分差 < τ 時才採用判斷器挑的句子。
- τ 的候選值：discordtune 抽樣分差的 10%、20%、……、90% 分位數，再加上「永遠採用」。
- **只在 discordtune 的 A 半**，為 A-fwd 與 A-both 各選一個淨修正最多的 τ；再原封不動用到 discordtune 的 B 半與 cvtune。

## 4. 指標

- 每個集合、每個條件都報：
  - 寬鬆對照（`reference/proto/eval.py` 的 `lenient`）的整句正確率；
  - **oracle@8**（正解在前 8 名的比例，寬鬆對照），以及 **A1b@8**（正解在前 8 名的列裡，判斷器挑對的比例）；
  - 和基準配對的修好列數、弄壞列數、McNemar 精確檢定（雙尾）、配對標準誤；
  - 判斷器挑第 1 個選項的比例；
  - 被擋與解析失敗的列數。
- **順序翻轉率**：A-fwd 與 A-rev（J-sent-fwd 與 J-sent-rev）挑到不同候選的比例。
- **延遲**：Apple 是每列的 p50、p95 和最大值；Jev 是每次請求的。Apple 的延遲只有在和使用者約好、使用者沒在用電腦的時段量到的才算有效（§8）；其他時段量到的照樣記錄，但標成「負載不明」，不拿來做 S5 的決定。
- Jev 另報輸入 token 數。

## 5. 預先寫死的判斷規則（依 `docs/methodology.md` §2；偏離處明寫）

discordtune 是主要調整集，要 p < 0.05 且淨值為正；cvtune 是守門集，採 §2 的否決條件：p < 0.05 且淨值為負就否決。驗證集的「不劣於一個標準誤」不適用，因為這片不碰驗證集。

| 設定 | 進步要在哪裡成立 | 守門 | 說明 |
|---|---|---|---|
| A-fwd、A-rev、A-both（不設門檻） | discordtune 全部 1,000 列：p < 0.05 且淨值為正 | cvtune 不被否決 | 符合方法論 |
| 設門檻的 A-fwd、A-both | discordtune **B 半**：p < 0.05 且淨值為正 | cvtune 不被否決 | τ 只在 A 半選，B 半是樣本外 |
| J-sent-fwd、J-sent-rev、J-pos | cvtune：p < 0.05 且淨值為正 | 無 | **偏離方法論**：discordtune 不能送到雲端（隱私），cvtune 是 Common Voice 與 Tatoeba 的句子，不是使用者平常的聊天，證據比 Apple 弱，報告要標明 |
| A-ctx | 不參加候選判定 | — | 只回答「前文有沒有幫助」：和同一批列的 A-fwd 配對比較，報 p 值與淨值 |
| 順序翻轉率、挑第 1 個的比例 | 不參加候選判定 | — | 描述性數字 |

- 每個設定都照實報告，不挑好看的。
- 這片只產生資料，接不接進輸入法由 S5 的契約決定。

## 6. 隱私與資料

- discordtune：
  - 只給 Apple 的本機模型處理；Foundation Models 在裝置上執行（Apple 的文件）。
  - **所有由 discordtune 衍生的檔案**，包括抽樣的列、`--rows` 輸入、`--dump` 候選、Swift 判斷程式的輸入輸出、模型原始回答、續跑用的檢查點，都只寫進 `~/side-project/shanjie-private/s5-judges/`。終端機、repo、報告只出現統計數字。
- **程式層級的防護**（不只靠文字規定）：
  - Jev 驅動程式只接受白名單上的集合（cvtune、dev302、typing76），其他集合（包括 `iter2.all_sets()` 標為私有的）在開網路連線之前就以非 0 結束。
  - 所有工具的 stdout、stderr 與錯誤訊息都不得含句子內容，只能有列號、數字與固定字串。
- cvtune（含 Tatoeba，CC BY 2.0 FR）：逐列結果寫進 `~/.cache/shanjie/work/s5-judges/`，不進 repo。
- dev302、typing76（CC0）：逐列結果可以進 `experiments/s5-judges/results/`。
- Jev 只送公開集合的句子與前文；key 照 `jev.py` 的規則讀，不印出。

## 7. 產出與檔案範圍

- 新增 `experiments/s5-judges/`：
  - `README.md`（設定、結果表、限制）；
  - Apple 判斷程式（Swift，`swiftc -parse-as-library` 編譯，執行檔不進 repo）；
  - 驅動與計分的 Python；
  - `results/`（只放 dev302、typing76）。
- `docs/research-log.md` 與 `docs/methodology.md` 由 main 寫。
- 其他檔案都不改。

## 8. 負責人、執行方式與預算

- `pilotfish:executor` 實作工具，並只用 dev302 的前 20 列做冒煙測試，每個條件都要測到；回報完整執行的指令。回報裡要附上：
  - (a) Jev 驅動程式用 `--sets discordtune` 執行，在任何網路呼叫之前就以非 0 結束；
  - (b) 冒煙測試的輸出，以及刻意製造的解析失敗與錯誤路徑的輸出，都不含句子內容；
  - (c) `git diff` 裡沒有任何 discordtune 的路徑或內容。
- 完整執行由 main 在背景跑：Apple 約 5,500 次呼叫，約 45–60 分鐘；Jev 上限 5,000 次請求（J1 的 300 題花約 US$0.004）。
- **Apple 的完整執行要先問使用者**（`docs/PLAN.md` 的 Mac 量測規則；之前的本機模型實驗曾讓使用者覺得電腦很卡）：main 先請使用者給一個不用電腦的時段並取得同意，才開始跑。Jev 的執行不吃本機資源，不受這條限制。
- 結果、解讀與研究紀錄由 main 負責。

## 9. 停止條件

- Apple 模型不可用；或任一條件被擋加上解析失敗超過 5%：停下回報，不調提示。
- `jev-1.13.0` 不可用或額度錯誤：停下回報，不改用 `jev-latest`。
- 冒煙測試任一條件的解析失敗超過 1 列。
- 完整執行中斷：從中斷處續跑，不重抽樣。

## 10. executor 不可以做的事

- 不得改 `core/`、`macos/`、`data/`、`eval/`、`site/`、`.github/`；不得 commit 以外的 git 動作（不 push、不開 PR）。
- 不得讀 `eval/holdout/`；不得把 discordtune 的任何內容印到終端機或寫進 repo；冒煙測試不用 discordtune。
- 不得把 discordtune 送到 Jev 或任何網路服務。
- 不得安裝軟體、開 GUI App、碰鑰匙圈、改系統設定，也不得執行 `.app` 內的二進位。
- 不得自己跑完整實驗（超過冒煙測試的量）；回報指令給 main。
