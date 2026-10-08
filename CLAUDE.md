# 善解開發須知（給在這個 repo 工作的 Claude Code session）

維護者的工作方式與偏好。外部貢獻者看 `CONTRIBUTING.md`；計畫與驗收看 `docs/PLAN.md`；每一片的規格在 `docs/contracts/`。這份檔案記的是「怎麼做」，不重複那些文件的內容。新的偏好一律寫進這裡（或對應的 `docs/` 文件），不要只留在個人記憶裡。

## 一片工作的流程

1. **先寫契約**：`docs/contracts/` 一片一份，寫清楚要做到什麼、怎麼看到它有效、驗收、停止條件、範圍外。
2. **契約送 fresh `pilotfish:plan-verifier`**，READY 才實作；REVISE 照最小修改處理，第二次 REVISE 後逐項處置，再一次收尾審查。
3. **實作**交給 executor 或自己做；改到核心行為時同一個 PR 更新 `docs/contracts/s3a.md` 的按鍵規則表與 `core/include/shanjie.h`。
4. **合併關卡**（repo 未滿 100 顆星）：本地 `/code-review` 沒有未處置的問題 ＋ fresh `pilotfish:verifier` CONFIRMED ＋ CI 全綠。用 **merge commit**，不 squash。
5. **合併後當場清理**：GitHub 已開「merge 後自動刪分支」；本機 `git worktree remove` 那片的 worktree、`git branch -d` 本機分支、`git fetch --prune`。有未提交改動的先處理，不硬刪。實驗分支結論寫進文件後再刪，刪前先問。

細節：
- 契約過審就可以自行推進（AUTO）。遇到停止條件、驗收不過、要裝軟體、要花錢、要做對外或不可逆的動作，才停下來問。
- 需要使用者決定的事，用選擇題問，推薦的選項放第一個。
- 使用者明說的需求照字面做；要偏離時先做到，再附上有依據的說明。
- verifier 或其他會改檔的 agent 在某個 worktree 跑的時候，不碰那個 worktree；要急修就先停掉它。
- commit 一律接在測試成功之後（`make test && git commit …`），commit 前看 `git diff --cached --stat` 有沒有預期外的檔案。測試或 merge 的輸出不要經過 `| tail`、`| grep` 再接 `&&`：管線的結束碼是最後一個指令的，失敗會被吃掉（2026-10-05 因此 commit 了一次沒過的測試）。要篩選輸出就先導到檔案、看 `$?`，再 commit。

## 文件要跟著更新

- 每次改動都檢查 README、`docs/PLAN.md`、相關契約、`CONTRIBUTING.md` 是否需要跟著改。
- 實驗、決定、更正當下寫進 `docs/research-log.md`（時間序）；方法上的教訓寫進 `docs/methodology.md`。
- 文件裡的數字要量過才寫；推論要標明是推論；寫錯的結論要更正，不要只改數字。

## 評測與資料

- **保留集** `eval/holdout/`：只在一片收尾時由 fresh verifier 跑一次、只回數字：基準與新版各跑一次 `--set holdout --rowstats`（寫到它自己的暫存目錄、檔內只能有 `[0-9\t\n]`），再跑 `tools/evalstats.py compare`，只回那一行表格，然後刪掉兩個 rowstats。其他時候不讀、不用來調參數。
- **私有資料**（使用者的 Discord 調參集、學習檔、聊天紀錄）放在 repo 外，只報統計數字。讀個人資料前先問；agent 一律不讀。
- **使用者回報的錯字**：加到 `eval/dev/user-reported.txt` 的**最後面**（使用者 2026-10-03 同意以 CC0 釋出），格式 `前文|句子|讀音`。加在最後，`--dev 302` 的前 302 列才不會變。
- 改到選字結果的改動（使用者 2026-10-08 決定，從下一片開始），dev302、打字測驗、錯字回報檔在聊天與書面兩種設定各報一行同一張表：n、top1 基準／新、改對／改壞、精確 McNemar p、CER（字元錯誤率）基準／新、Δtop1 與 ΔCER 的配對 bootstrap 95% 區間。做法：基準與新版各跑 `lm … --rowstats FILE`（CLI 或 `reference/proto/lm_eval.py`），再 `python3 tools/evalstats.py compare BASE CAND --label 名稱`；契約 `docs/contracts/eval-stats.md`。Discord 調參集由 main 在本機跑、只貼表格數字。調參數用 cvtune、wikitune，不用 dev302；調參的前後比較也用同一張表。

## 實機測試（`tools/imeshot.swift`）

- 安裝輸入法（`scripts/install-ime.sh`）由 main 執行（使用者已授權）；subagent 一律不安裝、不啟動 App、不呼叫 TIS 或 lsregister、不碰 `~/Library` 與鑰匙圈。
- `imeshot` 會開一個「善解截圖探測」小視窗。使用者在工作時，macOS 不讓它搶焦點，要先請使用者點一下視窗。
- **佔用使用者鍵盤的時間要短**：每次目標 15 秒以內，超過先問；快速連打用 `N*a,b,c`（每鍵約 10 ms）；能合併的情境合併成一次。
- 要錄影時用 `IMESHOT_VIDEO`：周圍變暗是刻意保留的（使用者要知道正在錄），長度依步驟估算、多留幾秒。
- 剛重裝的輸入法第一次可能吃掉前幾個鍵，先暖機。
- 截圖只截測試視窗附近，蘋果的參考截圖不進 repo。善解自己的實機截圖與錄影也不進 repo：背景常拍到使用者的終端機、聊天等私人畫面，只放在 session 的暫存區；要分享的 demo 用 `IMESHOT_DEMO=1`（只錄測試視窗），檔案交給使用者自己決定。

## 外觀與行為

- 外觀和按鍵行為以**實測的蘋果注音**為準：用 `imeshot` 拍蘋果與善解的同比例截圖（必要時錄影逐格看），量數字再調；不靠猜。
- 視覺參數放具名常數，註解寫出處（哪張截圖量的）。
- 每一輪只改使用者指出的地方；外觀的收斂輪數由使用者的實機回饋決定。
- 動畫用系統預設的時間曲線，不自己調彈性。

## 發版

- 推 `v*` tag 觸發簽章、公證與發佈，**每次都要使用者當下同意**。
- 網站（shanjie.nyanako.com）手動部署，部署前先問；網站不放下載連結，直到使用者決定公開。

## 已知的系統問題

- **安全輸入讓第三方輸入法反灰**（2026-10-05）：只要有任何程式開著安全輸入（密碼欄、Terminal 的 Secure Keyboard Entry；使用者說用過 Chrome 相關工具之後會出現），macOS 就把善解、小麥在選單裡反灰。
  - `ioreg -l -w 0 | grep kCGSSessionSecureInputPID` 的 PID **不一定是持有者**：持有者是沒有視窗的背景程式時，報的是前景 App（實測）。
  - 判斷法：切到別的 App 再查。PID 跟著前景變，就是背景程式；PID 不變，才是那個 App。
  - 請使用者讓那個程式離開密碼欄或重開它；不要替使用者結束程式。重現用 `tools/secure-input.swift`。

- **Caps Lock 切換輸入法偶爾失效**（任何輸入法都切不動、Ctrl+Space 正常）：是 macOS 的問題，不是善解。安裝後不需要做任何步驟。使用者回報時，請使用者在「系統設定 → 輔助使用 → 鍵盤」打開慢速按鍵、按一次 Caps Lock、再關掉。用指令自動化試過都無效（`hidutil` 的 `SlowKeysDelay` 設不進去；`defaults write` 寫得進去但系統不會即時套用；程式送的 Caps Lock 不會觸發切換），詳見 `docs/research-log.md`。
