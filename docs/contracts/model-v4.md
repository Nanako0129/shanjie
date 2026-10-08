# 契約：model-v4 與 classes-v2（含來源指紋的模型）

狀態：草稿（2026-10-08）。使用者 2026-10-08 決定：發佈的模型加入用來辨識來源的指紋條目，只公開「含有指紋條目」，不公開是哪些、怎麼放；趕在 v0.3.0 之前。

## 0. 要做到什麼

- 模型被重新散布時，能從檔案本身辨識出它來自善解的發佈版。
- 打字的人看不出差別：在第 2 節量測的範圍內，選字解碼與即時預測的結果不變。
- 授權與署名不變：模型與詞類表仍是 CC BY-SA 4.0、同一批語料。

## 1. 產物

| 檔案 | Release | 內容 |
|---|---|---|
| `bigram.sjlm` | `model-v4` | 語料與建置流程同 model-v3（`tools/build_lm.py`），另含少量指紋條目。指紋由不在 repo 的工具加入。 |
| `classes.sjc` | `classes-v2` | `tools/build_classes.py` 對 model-v4 重建，輸入同 classes-v1；和 classes-v1 只差檔頭記的模型 SHA-256。 |

- 本機建好的檔案：`bigram.sjlm` 81,373,933 bytes，SHA-256 `06768f2949cf8b135d1f591056ffb16f3ae3f6d70aef5911ffd55de134250322`；`classes.sjc` 5,443,546 bytes，SHA-256 `9e343d3e3ce83f1008e371f62de5d8e62721df97e7b02562503ef2cc57226f3f`。Release 下載回來核對相同後，才寫進 `data/bigram.sjlm.sha256`、`data/classes.sjc.sha256`。
- 用 `tools/build_lm.py` 自己重建的模型不含指紋條目，雜湊和 model-v4 不同，釘選測試會失敗；開發一律下載 Release。

## 2. 驗收

本機（main 量；PR 1 的 fresh verifier 重跑 1–3 並跑 5，都在建 Release 之前）：

1. 解碼不變：`shanjie-eval --lm` 對 dev302（`--dev 302`）、打字測驗（`eval/dev/user-typing.txt`）、錯字回報（`eval/dev/user-reported.txt`），聊天與書面、有無 `--context`，共 12 組，model-v3 與 model-v4 的 `--rowstats` 逐位元組相同、摘要行相同。
2. 預測不變：`cli/tests/golden.rs` 的 `predict_matches_python_golden`（2,938 個查詢，分數比對 f64 位元）在 model-v4 上通過，golden 不重產。
3. 其餘 golden 都不重產；在 model-v4＋classes-v2 上，`cargo test --release --no-fail-fast` 只有兩個雜湊釘選失敗，`swift test --package-path macos` 全過；PR 2 改完釘選後 `make test` 全過。
4. 指紋條目會影響的打字情境（私有，main 跑，只報數字；清單與情境的產生方式不在 repo，verifier 無法重跑，這一點寫進 PR）：對每一條指紋條目，在它可能影響解碼與即時預測的打字情境下，比較 model-v3 與 model-v4 的解碼第一名（聊天、書面）與預測輸出，全部相同。只報「N 條；解碼 n 列、相同 n；預測 q 筆、相同 q」。
5. 保留集（PR 1 的 fresh verifier，建 Release 之前；照 `CLAUDE.md`「保留集」的程序）：model-v3 與 model-v4 各跑一次 `--set holdout --context --rowstats`（聊天、書面），寫到 verifier 自己的暫存目錄，檔內只能有 `[0-9\t\n]`；再跑 `tools/evalstats.py compare`，只回那一行表格（改對、改壞都要是 0），然後刪掉 rowstats。
6. Release 建好後，用 `gh release download` 下載回來，SHA-256 和本機檔案相同；CI（下載 Release）在 PR 上全綠。

## 3. 改動

- 引用從 `model-v3`／`classes-v1` 改成 `model-v4`／`classes-v2`：`.github/workflows/ci.yml`、`.github/workflows/release.yml`、`cli/tests/golden.rs`（下載訊息常數、兩個釘選）、`core/tests/engine_lm.rs`、`engine_predict.rs`、`engine_learn.rs`、`engine_demote.rs` 的缺檔訊息、`macos/Tests/ShanjieKitTests/Support.swift`、`scripts/build-app.sh`、`CONTRIBUTING.md`、`docs/data-files.md`、`docs/verification.md`、`LICENSES/data.md`。
- `docs/PLAN.md`「模型檔不進 repo」那句：Release 改 model-v4、SHA-256 改新值，拿掉「或用 `tools/build_lm.py` 從本機計數重建」（重建的模型不含指紋條目）。
- 缺檔訊息裡的「(or rebuild with tools/build_lm.py…)」拿掉：`cli/tests/golden.rs` 的 `LM_MISSING`、`core/tests/engine_lm.rs`。
- `golden.rs` 的 `lm_file_is_the_documented_build` 改名 `lm_file_is_the_released_model`，訊息改成「下載 model-v4；`tools/build_lm.py` 重建的模型不含指紋條目，不會相符」。
- 收尾檢查（`git grep`，只看追蹤中的檔案）：`git grep -n 'model-v3\|classes-v1\|5c7d5a94\|80dbaa08\|81,373,869\|rebuild with tools/build_lm.py'` 只剩歷史紀錄與本片自己的說明：`docs/research-log.md`、`experiments/**`（README 與實驗腳本）、`docs/contracts/` 裡的契約（含這一份）、`docs/releases/model-v3.md`、`classes-v1.md`、`model-v4.md`、`classes-v2.md`（新說明檔寫「和 model-v3／classes-v1 的差別」）、`docs/releases/README.md` 與 `docs/contracts/release-notes.md` 的檔名舉例、`docs/PLAN.md` 裡標了日期的狀態與歷史句（第 536 行那句除外，它照上面改）。
- `LICENSES/data.md` 與 `docs/data-files.md`：寫明發佈的模型含有用來辨識來源的指紋條目（不寫是哪些）。`docs/data-files.md` 的版本表改成以 model-v4／classes-v2 為現行版（下一次重建是 model-v5／classes-v3），「舊的 Release 一直保留」改成「model-v3、classes-v1 在 model-v4 發佈後移除，其餘保留」。
- `core/tests/engine_learn.rs` 的註解「3 with model-v3 + classes」改成不寫模型版本（「3 with the released model and classes」）。
- `docs/releases/model-v4.md`、`docs/releases/classes-v2.md`：手寫的 Release 說明（授權、署名、SHA-256、大小、和 classes 的綁定、含有指紋條目）。
- `docs/contracts/s2w-mediawiki-zhtw.md` §8.3 加註更正：W2 沒有出貨，`model-v4` 這個名稱改用在本片（model-v3 加指紋條目），不是 W2；「`model-v1`～`model-v3` 永遠不刪」改成：使用者 2026-10-08 決定 model-v4 發佈後移除 model-v3 與 classes-v1，model-v1、model-v2 保留。
- 研究紀錄記這一片的量測（只寫第 2 節的結果，不寫指紋的內容與選法）。

## 4. 對外動作與順序（使用者 2026-10-08 已同意建立新 Release、在核對後刪除舊的兩個）

說明檔要先進 main（`docs/releases/README.md`），而改引用的 PR 的 CI 要下載新 Release，所以分兩個 PR：

1. **PR 1（說明與契約）**：這份契約、`docs/releases/model-v4.md`、`docs/releases/classes-v2.md`。本機驗收第 2 節 1–4 通過後開 PR；`/code-review`、fresh verifier（核對兩份說明檔的大小、雜湊、綁定關係與第 1 節和本機檔案相符）、CI 全綠後 merge。
2. **建 Release**：用 main 上的說明檔建 `gh release create model-v4 data/lm/bigram.sjlm -R Nanako0129/shanjie --title "語言模型 model-v4（bigram.sjlm）" --notes-file docs/releases/model-v4.md --latest=false`，`classes-v2` 同樣（標題「詞類表 classes-v2（classes.sjc，配 model-v4）」），下載回來核對雜湊（第 2 節 6）。
3. **PR 2（改引用）**：第 3 節其餘的改動、研究紀錄。CI 全綠、`/code-review`、fresh verifier（含第 2 節 1–3、5）後 merge。
4. **刪除 model-v3 與 classes-v1 兩個 Release**，下列全部成立才做：
   - PR 2 已 merge，記下它的 merge commit `M`。
   - `gh pr list --state open` 列出的每個 PR 的 head，以及 `git fetch --prune` 之後 `git branch -r --no-merged origin/main` 列出的每個遠端分支，都滿足 `git merge-base --is-ancestor M <分支>`（已併入含 `M` 的 main），或使用者對那個分支有記錄下來的決定（關閉、放棄）。本機還沒推的分支同樣先併入。
   - 第 2 節 6 已核對。
   - 代價：刪除後，從採用 model-v3 到 PR 2 之間的 commit 下載不到模型，CI、golden 釘選與 `build-app.sh` 都無法在那段歷史重跑（`git bisect` 要用本機副本）。維護者在本機留一份 model-v3 與 classes-v1，不公開。
   刪除後在 `docs/releases/model-v3.md`、`classes-v1.md` 開頭加一行「已在 model-v4 發佈後移除（日期）」，連同 PLAN 狀態一起用一個小 PR 更新。

## 5. 停止條件

- 第 2 節 1、2、4、5 有任何差異。
- 下載回來的雜湊不符（不刪任何舊 Release）。
- 需要重產任何 golden。

## 6. 範圍外

- 重新訓練模型、改 `tools/build_lm.py`。
- 指紋的工具、清單與選法：不進 repo、不寫進任何公開文件、commit、PR 或 Release 說明。
