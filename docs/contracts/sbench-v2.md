# S-bench 套件 v2：每一版用自己出貨的設定量（先做 v0.4.0、v0.4.1）

狀態：READY（2026-10-13：plan-verifier REVISE 兩次，逐項處置後最後一次收尾審查 READY，§6）

## 0. 起因與使用者的決定

- `docs/benchmark.md` 只有 v0.1.0–v0.1.2。套件 v1 固定用 model-v1 比所有版本（`docs/contracts/sbench.md` §1.1），§7.4 寫「model-v2 出貨時另寫套件 v2」，沒做；`docs/verification.md` 規定「發版前」跑 `tools/bench.py`，v0.2.0 起都沒照做（研究紀錄 2026-10-13）。
- **使用者決定（2026-10-13，選擇題）**：推 v0.4.1 tag 之前，先做套件 v2，量 **v0.4.0 與 v0.4.1** 兩列與各自的保留集；v0.2.0–v0.3.0 之後再補（另一片）。

## 1. 要做到什麼、怎麼看到

- `tools/bench.py run --suite v2 v0.4.0 <v0.4.1 的 commit> --as v0.4.1` 用每一版**出貨時的設定**（§2.1）量套件 v2 的集合（§2.2），寫 `eval/bench/results/v2-<label>.json`；`tools/bench.py table` 在 `docs/benchmark.md` 加「套件 v2」一節（準確率主表、和上一版的逐列比較、保留集、速度），套件 v1 的表與參考列逐位元組不變。
- **怎麼看到**：維護者讀 `docs/benchmark.md` 的套件 v2 一節，看到 v0.4.0 → v0.4.1 每個集合、兩種設定的第一名、修好／弄壞與保留集。

## 2. 套件 v2

### 2.1 每一版的出貨設定

工具裡一張寫死的表（鍵是版本標籤；`--as` 把一個還沒打 tag 的 commit 對到表上的版本）：

| 版本 | 模型（雜湊取自該版的 `data/*.sha256`） | CLI 旗標 |
|---|---|---|
| v0.4.0 | `bigram.sjlm`（model-v5）、`classes.sjc`（classes-v3） | `--context --packs acg` |
| v0.4.1 | 同上，加 `tinylm.sjtl`（tinylm-v3） | `--context --packs acg --tinylm <檔>` |

- 依據：App 預設開動漫詞包（`Shell.swift` 的 `acgPack ?? true`）、組字用前文（S2h）；v0.4.1 起語意校正預設開（`rerank ?? true`），v0.4.0 沒有語意校正；降權表兩邊預設都開（CLI `demote=true`、`Shell` 的 `demote ?? true`），一致。聊天與書面兩種設定都量。
- **CLI 沒有模擬的 App 行為**（表的說明要寫出來，不說成「和 App 完全相同」）：本機學習（CLI 不學）；殼是每個鍵之後非同步重排、CLI 每列只排一次；殼的前文來自輸入框、CLI 用列檔的前文欄；聊天／書面由 App 決定，這裡兩種都量。〔/code-review 2026-10-13 更正：原本寫「這些不影響 v0.4.0 → v0.4.1 的比較」，沒量過。推論：App 在重排結果回來前送出的列保留 n-gram 的第一名，所以開著語意校正的版本在 App 裡的改善可能比表上小。〕
- **旗標守門**：每一格的 CLI 摘要行的設定標記要等於這張表推出來的字串（例如 v0.4.1 聊天是 `lm-chat+ctx+tl:7e73ef67+acg`），而且沒有 KN 標記；不符就那一格失敗。這同時守住「旗標漏帶」與「模型資料夾裡多了 `kn.sjkn` 被自動載入」。
- 模型檔：工具讀匯出版本的 `data/bigram.sjlm.sha256`、`data/classes.sjc.sha256`、`data/tinylm.sjtl.sha256`（有的話），到 `~/.cache/shanjie/bench/models/<sha256>/<檔名>` 找；找不到就失敗退出並寫出要下載哪個 Release。**main 事先放好檔案**（從 Release 下載並核對雜湊），executor 不連網。CLI 從 `--lm` 同一個資料夾讀 `classes.sjc`、看到 `kn.sjkn` 會自動開 KN（`cli/src/main.rs` 235–237 行），所以工具為每一版組一個暫時的模型資料夾，只放 `bigram.sjlm` 與 `classes.sjc`（不放 `kn.sjkn`），`--lm` 指向它。
- **計分改用 CLI 選出的那一句**：開 `--tinylm` 時 dump 保留 n-gram 的順序（`cli/src/main.rs` 305 行的註解），從 dump 取第一名會忽略語意校正。套件 v2 的逐列對錯取自 CLI 的 `--rowstats`（`SHANJIE_VARIANTS` 設成套件 v2 的 `variants.tsv`，和 v1 同一套寬鬆對照）；CLI 摘要行的 `top1_sha256` 算的就是選出的那一句（main.rs 343、369 行），直接記進結果。獨立的交叉檢查：**沒有 `--tinylm` 的格**，工具從 dump 用自己的寬鬆對照重算 top1，要和 rowstats 相等；有 `--tinylm` 的格靠 §3 第 1 項的錨點守。

### 2.2 集合（凍結；公開的放 `eval/bench/suite-v2/`，SHA-256 寫死在工具裡）

| 集合 | 來源 | 列數 | 備註 |
|---|---|---|---|
| dev302、typing76、probe | 套件 v1 的檔（同雜湊） | 302、76、83 | dev302 只當參考 |
| user-reported | `eval/dev/user-reported.txt` 前 80 列（前置條件 PR #131 已合併，3503fd8；凍結時寫死實際的 SHA-256；之後的第 81 列起不進這版套件） | 80 | 使用者回報的錯字；之後新增的列不進這版套件 |
| fw | `eval/rerank/s7e-function-words.txt` | 186 | 功能字（在／再、的／得／地） |
| cvtune、wikitune | `~/.cache/shanjie/s7c2c-public/tune/`（`31de456d…`、`8dcfe40c…`） | 3,791、2,827 | 不進 repo（授權）；調參時看過 |
| lytune、wikitypo | `~/.cache/shanjie/e2-public/`（雜湊在 E2 研究紀錄） | 4,811、155 | 口語守門；不進 repo |
| discordtune（私有） | `--private-root` 下的 `discord-tune-rows.txt` | 4,958 | 同 v1；只記統計 |

全部集合聊天與書面兩種設定都量。`variants.tsv` 沿用套件 v1 的檔。

### 2.3 逐列比較、速度、保留集

- 逐列比較：v0.4.1 對 v0.4.0（同套件 v2），修好／弄壞／McNemar 精確 p，規則同 v1 §1.1（列數、上一版 top1 的檢查）。
- 速度：沿用 v1 的重播程式，用該版的 `bigram.sjlm`＋`classes.sjc`，**不載詞包、沒有前文、沒有語意校正**（重播程式是同步打鍵，語意校正是非同步、不在按鍵路徑上），同場三輪取 p95 中位數。重播的一致性檢查**另跑一格和重播設定完全相同的 CLI**（`--profile chat`、不帶 `--context`、`--packs`、`--tinylm`，dev302），拿那一格 dump 的第一名逐列比，不和正式結果格比。速度表註明這個設定。重播的 `--lm` 也指向工具組的模型資料夾（`Lm::load` 會讀同資料夾的 `classes.sjc` 與 `kn.sjkn`）。另報 CLI 印在 stderr 的重排時間（`tinylm: rerank … p50 … p95 …` 那一行）。v0.4.x 的核心 API 若和重播程式不合，executor 改重播程式的匯出端，不改核心。
- 保留集（照 CLAUDE.md，一片只跑一次）：**等要打 v0.4.1 tag 的 commit 定下來之後**才跑（v0.4.1 要收的 PR 都合併、main 不再動），fresh verifier 在 `v0.4.0` tag 與那個 commit 的 worktree 各 build CLI，用 §2.1 的旗標（`--lm` 指向工具組的模型資料夾、`SHANJIE_VARIANTS` 指向套件 v2 的 `variants.tsv`）跑 `--set holdout --rowstats <verifier 自己的暫存檔>`，聊天與書面各一次；每個 rowstats 先檢查只含數字、tab、換行；再以 v0.4.0 為基準、v0.4.1 為新版跑 `python3 tools/evalstats.py compare BASE CAND --net --label "bench-v2 holdout P"`，聊天與書面各只回那一行，然後刪掉四個 rowstats。main 從那一行取 n 與兩版的 top1 填進結果 JSON。保留集出現「改對 < 改壞而且 p < 0.05」就停，不推 tag，交給使用者決定。

## 3. 驗收

1. **對得上 S7h 的量測**（錨點）：工具加 `--override-packs off` 跑 v0.4.0 與 v0.4.1 的聊天設定（錨點這一次**不設** `SHANJIE_VARIANTS`，用 CLI 預設的對照表，和 S7h 量的時候相同；正式的套件 v2 結果照 §2.1 用套件的 `variants.tsv`），cvtune、wikitune、dev302、typing76、fw、lytune、wikitypo 的 top1 要等於研究紀錄 2026-10-13 S7h 一節（基準 n-gram／R3′）：v0.4.0 3364、2067、241、66、154、3986、136；v0.4.1 3443、2170、259、69、157、4044、134。對不上就停。
2. **決定性**：同一版跑兩次，結果 JSON 的準確率欄相同；`table` 兩次逐位元組相同。
3. **套件 v1 不變**：`table` 重產後，`docs/benchmark.md` 原有的表、參考列、靜態節逐位元組不變，只多出套件 v2 一節。
4. **突變**（每項都要讓工具以非 0 結束或測試以斷言失敗）：改一個公開集合檔的一個字；v0.4.1 的 tinylm 雜湊對不上快取的檔；拿掉 v0.4.1 的 `--tinylm`（設定標記不符，錨點也不符）；沒有 tinylm 的格，dump 重算的 top1 和 rowstats 不一致（用 fixture 觸發）；模型檔缺。
5. **私有資料**：同 v1 §3.7 的 `sandbox-exec` 做法（拒絕清單加上 `eval/holdout` 與 `~/.cache/shanjie/work`），executor 的完整執行以 0 結束。
6. **速度的一致性**：兩版的重播都是 302／302 和重播設定的 CLI 格一致；突變（用 fixture，保證會不一致）：把比對格 dump 的某一列第一名改成別的字串，一致性檢查失敗、速度欄寫錯誤、工具以非 0 結束。
7. **旗標守門**：突變：v0.4.1 少帶 `--packs acg`，那一格因設定標記不符而失敗。
8. `make test` 通過；工具的既有測試全過；新增測試涵蓋 §2.1 的表、`--as`、rowstats 計分。

## 4. 範圍外

- v0.2.0、v0.2.1、v0.3.0 的列（下一片）。
- 實打對照、KN。
- 改 `core/`、`cli/`、`macos/`。

## 5. 負責人、限制、停止條件、回滾

- `pilotfish:executor`：`tools/bench.py`、套件 v2 的凍結檔、測試、`table` 的新節。試跑結果不 commit。實作注意：套件 v2 的路徑不呼叫 v1 的 `preflight`（它會檢查 `~/.cache/shanjie/work/s2/tune`，§3 第 5 項的沙盒會殺掉）；上一版逐列結果的快取與核對改存 rowstats（有 `--tinylm` 的格，dump 的第一名不是選出的那一句）。
- **時間預算**（推論）：executor 1 回合加 1 次修正；main 跑兩版（CLI 約 1.6 萬列 × 兩種設定 × 兩版，開語意校正約十幾分鐘）、保留集、/code-review、verifier、CI，整片約 3–4 小時。超過 5 小時先回報。
- main：放好模型檔（Release 下載＋雜湊核對）；用真的私有資料跑 v0.4.0 與 v0.4.1、commit 結果 JSON 與 `docs/benchmark.md`；請 fresh verifier 量保留集並填入；`docs/verification.md` 那一列改成套件 v2 的指令，`docs/releases/README.md` 與 `CLAUDE.md`「發版」加一句指向它；研究紀錄、PLAN。
- executor 不可以：讀 `~/side-project/shanjie-private/`、`eval/holdout/`、`~/.cache/shanjie/work/`（每次執行 `--private-root` 指向空的暫存目錄；§3 第 5 項的沙盒拒絕規則把這三處都列進去）；連網、下載 Release；push、開 PR；安裝、開 App、在 `.app` 裡執行；改 `core/`、`cli/`、`macos/`。
- 停止條件：錨點對不上；同一版兩次結果不同；舊版 build 不起來而且原因不明；超過預算（executor 1 回合加 1 次修正）；保留集 v0.4.1 對 v0.4.0「改對 < 改壞而且 p < 0.05」。
- **被量的 commit 要等於出貨的那一版**：結果 JSON 記被量的 commit SHA。推 v0.4.1 tag 之前，`git diff <被量的 commit> <要打 tag 的 commit> -- Cargo.toml Cargo.lock core cli data eval/variants.tsv macos scripts` 必須是空的（`macos` 有殼的預設值，例如語意校正與詞包的 `?? true`；`scripts` 有 `build-app.sh` 包進去的模型；/code-review 2026-10-13 補上），輸出記進研究紀錄；不是空的就停，回報使用者（保留集不重跑第二次；重跑要使用者決定）。
- **錨點前先確認**：`git diff v0.4.0 677b08f -- core cli data/lexicon eval/variants.tsv` 與 `git diff 677b08f <v0.4.1 被量的 commit> -- core cli data/lexicon eval/variants.tsv`（677b08f 是 S7h 量錨點時的 main）沒有改到不開詞包時的解碼；有的話先回報，不直接拿錨點比。
- 回滾：只新增檔案與工具的新路徑，revert 合併 commit。

## 6. 修訂與審查

| 時間 | 內容 | 結果 |
|---|---|---|
| 2026-10-13 | 初稿 | 送 plan-verifier |
| plan-verifier（第 1 次 REVISE） | 阻擋 4 點：錯字回報 80 列在 main 還不存在；重播的一致性比對對象沒定義；保留集沒照 CLAUDE.md；被量的 commit 沒和 tag 綁住。非阻擋：`top1_sha256` 已是選出的那一句；rowstats 與摘要行是恆等式；旗標沒有守門；錨點的 v0.4.0 數字取自 677b08f；沒寫 CLI 沒模擬的 App 行為；模型資料夾要另組、不放 `kn.sjkn`；沙盒加兩處；保留集變差時要不要停；時間會超過 3 小時 | 全部 **FIX**（§2.1 加 App 行為說明、旗標守門、模型資料夾；計分改寫；§2.2 前置條件；§2.3 重播比對格、保留集照 CLAUDE.md；§3 第 4、5、7、8 項；§5 停止條件、commit 綁定、錨點前的 diff） | 送收尾審查 |
| 收尾審查（第 2 次 REVISE） | 阻擋 1 點：§3 第 7 項的突變（比對格換成開詞包的格）可能殺不死，dev302 開不開詞包第一名可能每列相同。非阻擋：重播的 `--lm` 指向模型資料夾；commit 綁定與「保留集只跑一次」衝突；錨點前少一段 diff；重排時間在 stderr；v2 不能呼叫 v1 的 preflight；快取要改存 rowstats；沒有時間預算、§3 編號亂；#131 已合併 | 第 2 次 REVISE 後逐項處置：阻擋點 **FIX**（突變改用 fixture 改掉比對格的一列）；非阻擋全部 **FIX**（§2.3、§5 的保留集時機與 commit 綁定改成「不符就停、回報使用者」、錨點前兩段 diff、實作注意、時間預算 3–4 小時、§3 重新編號、前置條件改成已滿足） | 送最後一次收尾審查 |
| 最後一次收尾審查（2026-10-13） | READY | 照契約做 |
