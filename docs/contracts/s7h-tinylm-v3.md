# S7h：`tinylm-v3`（聊天設定改用 E2 選出的值）與語意校正預設開

狀態：PR 1（#129）已合併、Release `tinylm-v3` 已建、PR 2（#130）fresh verifier CONFIRMED、實機通過（2026-10-13，研究紀錄 S7h PR 2 一節）。契約 READY（2026-10-13：plan-verifier REVISE 兩次、逐項處置後最後一次收尾審查 READY，§8）；〔修訂，2026-10-13，PR #129 的 /code-review〕保留集移到 PR 1、建 Release 之前；加 CLAUDE.md 的評測表與預設開的實機檢查（§8 最後兩列），plan-verifier READY。

## 0. 起因與依據

- **使用者決定（2026-10-12 晚上，選擇題；E2 契約 §0 第 1 項、PLAN 同日的決定）**：計分改成「改對 − 改壞」（1:1）；**語意校正要預設開**，另外要求改壞率 ≤ 0.5%。
- **使用者（2026-10-12）**：#118 合併後實際打字覺得語意校正「改得太少」；v0.4.1 等 E2 選出新的值（`tinylm-v3`）再一起出（PLAN 的 v0.4.1 一段）。
- **E2 的結果**（PR #126，研究紀錄 2026-10-13「E2 判斷期（2026-08）的表與結論」）：判斷期 2026-08（6,559 列），對 n-gram 第一名：
  - 聊天 R3′（α 0.3、τ 2、δ 0.25，在調參期上選、先凍結）：改對 125／改壞 16，淨值 +109，改壞率 0.24%（改壞數 95% 上界換成比例 0.37%）；
  - 聊天 R2（`tinylm-v2` 的值 0.2／1／0.25）：82／7，+75，0.11%；
  - 書面 R2（`tinylm-v2` 的值 0.7／1／2）：30／1，+29，0.02%。
- **E2 契約 §3.3 給這一片的門檻**：判斷期淨值 > 0、改壞率點估計 ≤ 0.5%、公開與守門集合沒有「改對 < 改壞而且 p < 0.05」的格。R3′ 三項都符合（研究紀錄同節；守門集合最差的是 `wikitypo` 0／2、p 0.50）。書面沒有選新的值：書面的 R2 在判斷期 30／1、改壞率 0.02%，已在 0.5% 以內；在公開與守門集合每一格都是改對 ≥ 改壞（研究紀錄 2026-10-12「E2 公開集合與守門集合的表」：`lytune` 36／3、`wikitypo` 0／0，其餘集合改壞 0–1），三項門檻都成立，照舊。
- **現況**：出貨的是 `tinylm-v2`（`710249c2…`，SJTL0002）；殼的「語意校正」沒設過＝關（`Shell.swift` 的 `rerankStore.rerank ?? false`，s7c2b 2026-10-12 的修訂）；核心的 `rerank_on` 預設關（S7c-2c §2.3）。

## 1. 要做到什麼、怎麼看到

- 發 `tinylm-v3`：權重與 `tinylm-v1`、`v2` 相同（M 的 int8），格式 SJTL0002，綁 model-v5（`f81a021e…`）；**聊天 α 0.3、τ 2、δ 0.25**，書面 α 0.7、τ 1、δ 2（不變）。
- 輸入法改用 `tinylm-v3`；「語意校正」**沒設過時＝開**。存過的值照舊（存了 false 的使用者仍是關）。
- **怎麼看到**：
  - **實機（維護者）**：裝這一片的 App，聊天設定、語意校正開著，打 `ㄓˊ ㄒㄧㄥˊ ㄓㄜˋ ㄍㄜ˙ ㄔㄥˊ ㄕˋ`（dev302 第 25 列，正解 `執行這個程式`）：組字是 `執行這個程式`；關掉語意校正再打一次是 n-gram 的句子（`執行這個城市`）。
    - 已量（CLI，2026-10-13）：殼的條件（`--profile chat --packs acg`、**沒有** `--context`，`--dev 302`）下第 25 列 n-gram 錯、`tinylm-v2` 錯、v3 候選檔對（整體 top1：n-gram 242、v2 250、v3 259）；有前文的條件（`--context`、不開詞包）下也是 v2 錯、v3 對。
    - CLI 每列只重排一次，殼是每個鍵後重排；殼的條件以 PR 2 新增的 XCTest 為準（§2.3、§3 第 3 項）。〔已量，2026-10-13，PR 2 的工作區〕這個測試在 v2 下只有「開時是 `執行這個程式`」那一個斷言失敗（組字仍是 `執行這個城市`），在 v3 下通過；所以實機句就是第 25 列，下面的退路沒有用到。那個測試在 v3 下不過時，照 §2.3 的做法：測試不留，實機改打**當時 `Selftest.rerankRowStandard` 用的那一列**（現在是第 65 列；§2.3 若重挑就是重挑後的那一列），開時是 `Selftest.rerankedText`、關時是 `Selftest.ngramText`，PR 說明照實寫。
  - **實機（預設開）**：維護者的機器上存著 `rerank`（2026-10-13 `defaults read com.nyanako.inputmethod.shanjie rerank` 是 1，#118 實機檢查時打開的），存著的值會蓋過預設，看不到「沒設過」的情況。PR 2 的 App 裝好之前，main 用選擇題問維護者能不能刪掉這個 key（`defaults delete com.nyanako.inputmethod.shanjie rerank`）；同意才刪，裝好之後選單與設定視窗的「語意校正」是勾著的（imeshot 或維護者看）。不同意就只靠 XCTest（§3 第 3 項），PR 說明照實寫。
  - **離線**：Rust（CLI `--tinylm`）用 v3 的檔，在公開與守門集合上逐字重現 E2 研究紀錄裡 R3′ 的表（§3 第 1 項）；CLAUDE.md 規定的評測表（§3 第 8 項）；保留集（§3 第 5 項，建 Release 之前）。

## 2. 改動

分兩個 PR，因為 `docs/releases/README.md` 規定資料類 Release 的說明檔先進 main，而換檔的雜湊與下載要在同一個切換的 PR 裡改。

### 2.1 PR 1：候選檔、重現與說明檔（不換檔）

- **匯出**（main 在本機）：先核對 `~/.cache/shanjie/work/s7a/tinylm-M.npz` 的 SHA-256 是 `32308122…`，再
  `python3 experiments/s7a-tinylm/export_sjtl.py --npz ~/.cache/shanjie/work/s7a/tinylm-M.npz --out ~/.cache/shanjie/s7h-public/tinylm-M8-v3.sjtl --type int8 --ngram-model data/lm/bigram.sjlm --chat 0.3 2 0.25 --formal 0.7 1 2`。
  已試跑（2026-10-13）：`7e73ef677cd232099132b542c3f3e92aa6a1d7d17725fb0fbda25873509f12ce`，14,638,224 bytes，和 `tinylm-v2` 的檔只差 9 個 byte（`cmp -l`）。實作時照同一個指令重跑，雜湊要相同。
- **`docs/releases/tinylm-v3.md`**：說明（檔案、SHA-256、大小、內容：權重同 v1／v2、只有聊天的融合參數不同、怎麼選的、判斷期與公開集合的數字、綁 model-v5、授權）。照 `docs/releases/README.md` 的規則：建 Release 前拿掉草稿標記。
- **研究紀錄**：新的一節，§3 第 1、2 項的結果與候選檔雜湊；另加 E2 一節的〔更正〕（下面）。
- **〔更正〕E2 研究紀錄 2026-10-13「/code-review 與 fresh verifier」一節**：「舊的在改壞數約 705 以上會卡在 745」寫得不準。實作時用 8b83a1d 之前的 `evalstats` 上界函式重新量「從哪個 k 起和 log 空間的結果不同」「從哪個 k 起固定在 745」，照量到的數字寫〔更正〕。main 已先量（2026-10-13，`git show 8b83a1d^:tools/evalstats.py` 對照現在的 `poisson_upper95`，k 0–1,200）：k ≤ 485 差 < 4e-12；k 679 起差 > 1e-9，686 起 > 1e-6，**694 起 > 0.001**；**k ≥ 700 時舊函式固定是 745.1332**（新的：700 是 745.1109、705 是 750.2659）。〔更正〕照這些數字寫。原句保留、後面加更正，不改數字了事。
- **CLAUDE.md 的評測表**（§3 第 8 項）：dev302、打字測驗、錯字回報（整個檔，當時 77 列〔修訂，2026-10-13：#131 加了第 78 列，重現這張表用 `--limit 77`〕）在聊天與書面各報「n-gram → v3」與「v2 → v3」兩行，寫進研究紀錄；說明檔摘要。
- **保留集**（§3 第 5 項）：在 PR 1 由 fresh verifier 對候選檔跑，**建 Release 之前**；這是這一片唯一的一次。verifier 回報的兩行由 main 寫進研究紀錄與說明檔，再 commit。
- PLAN（v0.4.1 一段、S7 的狀態清單加 S7h 一條；#126 已合併）、README（S7 的狀態：第 35 行與路線圖表的 S7 一列）、網站 `site/index.html`（v0.4.1 路線圖與語意校正一項，和 PLAN 第 116 行說的同步；只改檔，部署要使用者同意）：寫 E2 結果之後的狀態（候選檔、等 Release、預設開是 PR 2 才做）。

### 2.2 Release `tinylm-v3`（PR 1 合併後，保留集已在 PR 1 跑過而且沒有觸發停止條件；要使用者當下同意）

- 照 `docs/releases/README.md`「換檔時建 Release」：暫存資料夾、上傳前後都用說明檔裡的雜湊核對、`--latest=false`。

### 2.3 PR 2：換檔與預設開（Release 建好之後）

- **換檔**：`docs/data-files.md` 第 37 行列的項目裡和 `tinylm.sjtl` 有關的每一處（例如 `data/tinylm.sjtl.sha256`、`ci.yml` 兩個 job 與 `release.yml` 的下載、`macos/Tests/ShanjieKitTests/Support.swift` 與 `scripts/build-app.sh` 的提示、README、CONTRIBUTING、`docs/verification.md`、`LICENSES/data.md`、`docs/PLAN.md` 的模型檔那一條、`docs/data-files.md`）。第 37 行列的其他項目（`cli/tests/golden.rs`、`core/tests/engine_*.rs`、依模型的 golden）是換 n-gram 模型時才動的，2026-10-13 grep 沒有 `tinylm-v2` 字樣；以 grep 為準。實作時 `grep -rn 'tinylm-v2'` 全 repo，除了歷史敘述（研究紀錄、舊契約、`docs/releases/tinylm-v2.md`）以外都改，PR 說明列出留下的每一處與理由。
- **實機句的測試**（`RerankTests`）：新增一個測試，用 `RerankTests` 的 `rig()`（聊天 App 的 Controller、`MemoryAcgPackStore()` 沒設過＝內附詞包開、測試 Resources 裡真的 `tinylm.sjtl`、`HeldRerankRunner` 每個鍵後取工作、最後 `drain()`；沒有前文），打第 25 列的注音（鍵序列由測試從注音獨立推出，同現有測試的做法）：重排開時最後的組字是 `執行這個程式`，關時等於 n-gram 的句子而且不是 `執行這個程式`。這個測試讓 §1 的實機句有殼條件下的量測。
  - 換檔前先在 v2 下跑一次，結果（過或不過）照實寫進 PR 說明與研究紀錄；預期不過，過了也只記下，不影響後面的步驟。
  - **在 v3 下不過時**（唯一的處理）：這個測試不留（不 commit），實機句改成當時 `Selftest.rerankRowStandard` 的那一列（它由現有的 selftest 測試與 `testSentenceIsTheSelftests` 涵蓋），v3 下第 25 列的組字（只有開與關兩個結果）寫進 PR 說明與研究紀錄。這不是停止條件。
- **selftest 的句子**：照 `docs/data-files.md` 第 37 行，融合參數變了要重新量 `Selftest.rerankRowStandard`（dev302 第 65 列）在 v3 下還會被改。CLI 已量（2026-10-13，`--profile chat --context --dev 302`）：第 65 列在 v2 與 v3 都對；以 selftest 與 `RerankTests` 自己的條件為準（`make test`），不改就從 dev302 裡 v3 會改的列重挑。`Selftest.swift` 與 `Support.swift` 註解裡寫 v2 的地方改成 v3，量測的條件照實寫。
- **預設開**：`Shell.swift` 的 `rerankStore.rerank ?? false` 改成 `?? true`，`rerankOn` 的起始值與註解一起改；`Settings.swift` 的 `SettingsModel.rerank` 起始值（`false`，`init` 會用 `refresh()` 蓋掉）也改成 `true`，不留和預設相反的值。核心的預設（`engine.rs` 的 `rerank_on = false`）**不改**：殼每次建引擎都明確呼叫 `setRerank`（s7c2b 的測試核對過），CLI 與其他呼叫者照舊。
  - 測試（改 s7c2b 2026-10-12 那一組）：沒設過 → 開，殼每次建引擎傳 `setRerank(true)`（`Shell.rerankSent` 逐次核對，初次建置與兩次重建）、產生重排工作；存 false → 傳 false、沒有工作被取；存 true → 開。
  - 突變：`?? true` 改回 `?? false`，測試以斷言失敗。
- **文件**：`docs/contracts/s7c2b-shell-rerank.md` 加一列修訂（預設改開，依據本契約）；README、網站 `site/index.html`（第 2 層的說明、路線圖的語意校正一項）、PLAN 寫「預設開」；設定視窗或選單有說明文字寫到預設的，一起改；程式註解（例如 `macos/Sources/Shanjie/main.swift` `DefaultsRerankStore` 的「absent means off」）一起改（實作時 grep `預設關`、`預設關閉`、`default off`、`means off`、`rerank ?? `、`default is off`、`shipped default`、`goes back to`、`never chosen`，例如 `RerankTests.swift` 開頭與預設那個測試的註解）。網站部署要使用者同意，不在這一片。

## 3. 驗收

1. **重現 E2 的 R3′ 表（PR 1，main 跑）**。候選與 E2 相同（出貨的 model-v5＋classes-v3、`--context`、不開詞包、不載 KN）。先 `test ! -e data/lm/kn.sjkn`（CLI 看到這個檔就自動開 KN；2026-10-13 三個 checkout 都沒有），有就停。每個集合跑兩次，基準不給 `--tinylm`、候選給 `--tinylm ~/.cache/shanjie/s7h-public/tinylm-M8-v3.sjtl`：`cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile chat --context --rowstats OUT` 加上集合的列：
   - cvtune `--rows ~/.cache/shanjie/s7c2c-public/tune/cvtune.txt`、wikitune `--rows ~/.cache/shanjie/s7c2c-public/tune/wikitune.txt`、dev302 `--dev 302`、打字測驗 `--rows eval/dev/user-typing.txt`（76 列）、錯字回報 `--rows eval/dev/user-reported.txt --limit 72`（E2 用的是 72 列；檔案當時 77 列）、功能字測試句 `--rows eval/rerank/s7e-function-words.txt`、`lytune` `--rows ~/.cache/shanjie/e2-public/lytune.txt`、`wikitypo` `--rows ~/.cache/shanjie/e2-public/wikitypo.txt`；
   - 每格 `python3 tools/evalstats.py compare BASE CAND --net --label "R3′ chat <集合>"`（`<集合>` 逐字用表上的名稱：`cvtune`、`wikitune`、`dev302`、`typing76`、`user-reported`、`fw`、`lytune`、`wikitypo`），輸出的表格行要和研究紀錄 2026-10-13「E2 的新調參期與 R3′」第二張表同名的那一行**逐字元相同**。不同就停，回報不同的列數與 `s[j] − s[0]` 和 δ 的距離（只有數字），分得出是 Rust／numpy 的邊界誤差還是程式錯誤。
   - 已試跑（2026-10-13）：dev302 聊天 top1 259，和表上相同。
2. **書面不變（PR 1）**：先 `shasum -a 256 -c data/tinylm.sjtl.sha256`（`data/lm/tinylm.sjtl` 是 v2，`710249c2…`），再 dev302 與 cvtune 各跑兩次 `cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile formal --context --dev 302 --tinylm <檔> --rowstats OUT`（cvtune 把 `--dev 302` 換成 `--rows ~/.cache/shanjie/s7c2c-public/tune/cvtune.txt`），`<檔>` 分別是 `data/lm/tinylm.sjtl` 與 `~/.cache/shanjie/s7h-public/tinylm-M8-v3.sjtl`；兩個 rowstats 用 `cmp` 比，逐位元組相同（書面的值沒變）。
3. **殼（PR 2）**：§2.3 的測試（預設開的三種情況；第 25 列的實機句測試，除非照 §2.3 在 v3 下不過而不留）；selftest 在 v3 下通過；`make test` 全綠（只跑一次完整的）。
4. **App（PR 2，main）**：`scripts/build-app.sh` → `scripts/check-app.sh`（bundle 裡的 `tinylm.sjtl` 雜湊等於 `data/tinylm.sjtl.sha256` 記的 v3）→ selftest-bundled 通過。
5. **保留集（PR 1，建 Release 之前；fresh verifier 照 CLAUDE.md 的規則，只回數字；這一片只跑這一次）**：
   - 先 `test ! -e data/lm/kn.sjkn`，再 `echo "7e73ef677cd232099132b542c3f3e92aa6a1d7d17725fb0fbda25873509f12ce  $HOME/.cache/shanjie/s7h-public/tinylm-M8-v3.sjtl" | shasum -a 256 -c`，不符就停。
   - 聊天與書面各跑兩次（`P` 是 `chat` 或 `formal`，`T` 是 verifier 自己的暫存資料夾）：
     - 基準：`cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile P --context --set holdout --rowstats T/P.base`
     - 候選：`cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile P --context --set holdout --tinylm ~/.cache/shanjie/s7h-public/tinylm-M8-v3.sjtl --rowstats T/P.cand`
   - PR 2 只換檔與殼，CLI 的結果和這個檔綁在一起（雜湊相同），所以 PR 2 不再跑保留集。
   - 每個 rowstats 先檢查只含數字、tab、換行：`LC_ALL=C tr -d '0-9\t\n' < F | wc -c` 要是 0，不是就停；再 `python3 tools/evalstats.py compare T/P.base T/P.cand --net --label "S7h holdout P"`，聊天與書面各只回表格那一行，然後刪掉四個 rowstats。
   - 保留集出現「改對 < 改壞而且 p < 0.05」就停，交給使用者決定；這時還沒有建 Release。
6. **實機（PR 2，維護者）**：§1 的兩項：預設開（刪 key 要維護者當下同意）與那一句的開關。main 先用 imeshot 量一次（每次 ≤ 15 秒），再請維護者自己打一次；檢查時切掉的開關最後切回開。
7. 兩個 PR：本地 `/code-review` 沒有未處置的問題、fresh `pilotfish:verifier` CONFIRMED（PR 1：第 1、2、5、8 項；PR 2：第 3、4 項與換檔的完整性）、CI 全綠，merge commit。
8. **CLAUDE.md 的評測表（PR 1，main 跑，verifier 重現）**：先 `test ! -e data/lm/kn.sjkn`。`P` 是 `chat`、`formal`，集合是 dev302（`--dev 302`）、打字測驗（`--rows eval/dev/user-typing.txt`）、錯字回報（`--rows eval/dev/user-reported.txt`，整個檔；〔修訂，2026-10-13〕第 78 列加入之後重現要加 `--limit 77`）。每格跑三次 `cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile P --context <集合> [--tinylm F] --rowstats OUT`：不給 `--tinylm`（n-gram）、`F` 是 `~/.cache/shanjie/s7c2c-public/tinylm-M8-v2.sjtl`（v2）、`F` 是 v3 的候選檔；再 `python3 tools/evalstats.py compare` 各比「n-gram → v3」與「v2 → v3」（`--net`）。12 行寫進研究紀錄；verifier 重跑要逐字相同。

## 4. 範圍外

- 核心的預設、CLI 的行為。
- 重新訓練、換 n-gram 模型或詞類表、書面設定的新值。
- v0.4.1 的發版說明與推 tag（另外的 PR；推 tag 要使用者當下同意）。
- 網站部署（要使用者同意）。
- E2 延後的項目（跨期句子去重、程式碼區塊訊息當前文；SIGHAN、NTUML2021）。

## 5. 停止條件

- §3 第 1 項不逐字相同、第 2 項不逐位元組相同。
- 匯出的雜湊和 §2.1 記的不同。
- selftest 在 v3 下沒有任何 dev302 的列能用。
- 保留集出現「改對 < 改壞而且 p < 0.05」（PR 1，Release 之前）。
- 實機（imeshot 或維護者）上，§1 定下的實機句（第 25 列，或照 §2.3 改用的 selftest 那一列）語意校正開與關打出來的組字相同：停下來回報，不出貨。第 25 列的 XCTest 在 v3 下不過不是停止條件（§2.3）。
- 建 Release、推 tag、部署網站：等使用者同意。

## 6. 回滾

- revert PR 2 的 merge commit：換檔、預設開、selftest 與測試一起回到 v2、預設關（Release `tinylm-v2`、`tinylm-v3` 都留著）。PR 1 只有說明檔與研究紀錄，不用回滾。

## 7. 前置條件與 agent 的限制

- 前置：E2（PR #126）已合併（0a622a0，2026-10-13），`evalstats --net`、E2 的研究紀錄與 `lytune`／`wikitypo` 的建置程式都在 main。
- verifier 可以讀：worktree 本身、`~/.cache/shanjie/s7h-public/`（v3 候選檔、量測）、`~/.cache/shanjie/s7c2c-public/tune/`（cvtune、wikitune）、`~/.cache/shanjie/e2-public/`（`lytune.txt`、`wikitypo.txt`）、`~/.cache/shanjie/s7c2c-public/tinylm-M8-v2.sjtl`；`eval/holdout` 只在 §3 第 5 項（PR 1）照規則跑一次。
- agent 一律不讀 `~/side-project/shanjie-private/`、`~/.cache/shanjie/work/`（匯出由 main 做）、`~/Library`、鑰匙圈；不安裝、不啟動 App、不在 `.app` 裡執行程式、不呼叫 TIS 或 lsregister；不建 Release、不推 tag、不部署網站、不改 GitHub 上的任何東西（推分支與開 PR 由 main 做）。

## 8. 修訂與審查

| 時間 | 內容 | 結果 |
|---|---|---|
| 2026-10-13 | 初稿 | 送 plan-verifier |
| plan-verifier（第 1 次 REVISE） | 阻擋 2 點：(1) §3 第 5 項保留集沒有完整指令，漏 `--context`、`--profile`、`--lm`、雜湊核對與只含數字的檢查；(2) 實機句（第 25 列）只在 CLI 有前文的條件下量過，沒有殼條件的量測，也沒有量不到時的做法與停止條件。非阻擋 7 點：書面第三項門檻的依據、`--label` 的確切名稱、書面的完整指令、agent 限制一節、回滾寫成 revert PR 2、grep 加 `means off`、前置條件 | 全部 **FIX**：(1) §3 第 5 項寫成完整指令；(2) main 在殼的條件（`--packs acg`、沒有 `--context`）下量了第 25 列（n-gram、v2 錯，v3 對），§2.3 加殼條件的 XCTest，不過時改用 selftest 的句子，§5 加「開關組字相同就停」；非阻擋 7 點照改（§0、§3 第 1、2 項、§7、§6、§2.3） | 送收尾審查 |
| 收尾審查（第 2 次 REVISE） | 阻擋 1 點：第 25 列的 XCTest 在 v3 下不過時，§1 說改用 selftest 的句子，但 §2.3 的斷言、§3 第 3 項要求那個測試與 `make test` 全綠，§5 沒有對應的條件；退路又寫死第 65 列，而 §2.3 允許重挑 selftest 的句子。非阻擋：v2 下過了怎麼辦 | **FIX**（選 (a)）：§2.3 寫明唯一的處理（測試不留、實機句改成當時 `Selftest.rerankRowStandard` 那一列、結果寫進 PR 說明與研究紀錄、不是停止條件）；§1 的退路改成「當時 selftest 那一列」；§3 第 3 項加「除非照 §2.3 不留」；§5 的實機停止條件指向 §1 定下的句子，並寫明 XCTest 不過不是停止條件；v2 下的結果照實記、不影響後續。另外 §2.1 的〔更正〕換成 main 量到的數字 | 第 2 次 REVISE 後逐項處置完，送最後一次收尾審查 |
| 最後一次收尾審查（2026-10-13） | READY | 照契約做 PR 1 |
| PR #129 的 /code-review（2026-10-13，13 點） | 實質的：(1) Release 在保留集之前建，保留集沒過時已公開；(2) 說明檔寫「保留集之後量」卻沒有步驟把結果寫回；(3) 預設開沒有實機確認（維護者存著 `rerank` = 1）；(4) 第 25 列 XCTest 的退路分不出 v2、v3；(5) README S7 的兩處、(6) PLAN 的 S7 清單與 v0.4.1 一段、(7) 網站路線圖沒同步；(8) CLAUDE.md 規定的評測表沒報（只有聊天、錯字回報只 72 列、沒有書面）；(9) 換檔清單引用第 37 行不精確，`SettingsModel.rerank` 起始值；(10) grep 字詞漏了現有的寫法；(11) 沒有檢查 KN 側檔；(12) XCTest 的條件寫錯對象；(13) 說明檔的條列結構 | (1)(2) **FIX**：保留集移到 PR 1、建 Release 之前，結果由 main 寫進說明檔與研究紀錄（§2.1、§2.2、§3 第 5 項、§5）；(3) **FIX**：§1 加預設開的實機檢查，刪 key 要維護者同意（§3 第 6 項）；(4) **不改**：已量，XCTest 在 v2 下失敗、v3 下通過，退路沒有用到（§1 的〔已量〕）；(5)(6)(7)(13) **FIX**（PR 1 的文件）；(8) **FIX**：§3 第 8 項，12 行（main 已量）；(9)(10)(12) **FIX**（§2.3 的寫法）；(11) **FIX**：§3 第 1、5、8 項先 `test ! -e data/lm/kn.sjkn`（三個 checkout 都沒有，所以之前的重現量的就是出貨的設定） | 修訂送 plan-verifier |
| plan-verifier（2026-10-13，/code-review 之後的修訂） | READY | 照修訂做：PR 1 加評測表與保留集 |
