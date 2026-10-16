# classes-v5：詞類權重 μ 0.8 → 0.2

修訂 0，2026-10-16。出貨片：只換資料檔 `classes.sjc`，不改核心程式。

## 0. 起因

- `docs/contracts/lytune-attrib.md` 的 μ 掃描（研究紀錄 2026-10-16）：出貨設定（model-v6＋kn-v1＋tinylm-v5，`--context --packs acg`）下，μ 0.2 對 0.8 在 lytune 聊天 +27（100／73，p 0.048）、書面 +65（130／65，p＜0.0001）；discordtune、錯字回報沒有顯著改壞；cvtune＋wikitune＋dev302 兩種設定淨值合計 +6。契約 §4 第 3 點的規則選出 0.2。
- 維護者 2026-10-16 選擇題：「換成 μ 0.2（推薦）」，進 v0.4.6。

## 1. 要做到什麼、怎麼看到

- 出貨的 `classes.sjc` 換成 classes-v5：和 classes-v4 逐位元組相同，只有檔頭位移 44 的 μ（f64）從 0.8 改成 0.2，由 `experiments/lytune-attrib/set_mu.py` 產生，SHA-256 `9e36fa1be4518adb34bcddc2c79761006b0b06c539aa64a4564ba00397cf0262`（Mac 產生的和 188 量測用的 `v6mu0.2/classes.sjc` 相同）。仍綁 model-v6，類別、發射與接續機率都不變。
- **怎麼看到**：CI 從 Release `classes-v5` 下載、核對 `data/classes.sjc.sha256` 後全綠；`cli/tests/golden.rs` 的整檔雜湊與重產的 golden 在 Rust 與 Python 參考實作逐位元組相同；發版前的 bench（v0.4.5 → 新版）在 lytune 上出現和 §0 同方向的改對，使用者端三集照 sbench-v2 §2.3 不擋。

## 2. 步驟

1. **Release `classes-v5`**（資料 Release，不標 Latest；建之前問維護者）：附件 `classes.sjc`，說明 `docs/releases/classes-v5.md`（照 classes-v4 的格式，寫明只改 μ、產生指令）。建好後下載回來核對雜湊與大小。PR 的 CI 要下載它，所以先建 Release 再推 PR。
2. **換版 PR**（`docs/data-files.md`「版本怎麼往前走」最後一段的清單）：
   - `data/classes.sjc.sha256`；`.github/workflows/ci.yml` 兩個 job 與 `release.yml` 的下載；`scripts/build-app.sh` 的提示；`cli/tests/golden.rs` 的雜湊與訊息；`core/tests/engine_*.rs` 的缺檔訊息；`macos/Tests/ShanjieKitTests/Support.swift`；README、CONTRIBUTING、`docs/verification.md`、`LICENSES/data.md`、`docs/PLAN.md` 的模型檔那一條、`docs/data-files.md`、`docs/training.md` 的現況句。已發佈 Release 的說明（`docs/releases/*-v*.md` 既有檔）與舊契約是紀錄，不改。
   - **golden**：依 μ 的全部用各自的產生者重產（`docs/research-log.md` 2026-10-14「model-v6 PR 2 的 golden」那一節列的同一組：`s2-lm.txt`、`s2-lm-dev302-top1.tsv`、`s2-lm-dev302-{chat,formal}.rowstats`、`s2h-lm-context.txt`、`s2r-probe-top1.tsv`、`sw-probe.txt`、`sp-predict.txt`），Python 參考實作在 Mac 產生（Linux 的 libm 末位不同），再確認 Rust 逐位元組相同。`--no-classes` 兩行、unigram 類、`kn-tiny`、`tinylm-tiny` 不依賴出貨的 μ，不動。研究紀錄寫每個 golden 變了幾行。
   - 寫死字串或分數的測試（`engine_lm`、`engine_learn`、`engine_predict`、`engine_demote`、`engine_pack`、`engine_rerank`、Swift 的 selftest 與 rerank 測試）在新檔上跑；失敗的逐項看：是 μ 造成的預期變化就更新期望值並在研究紀錄寫原因，不是就停。
3. **規則放寬**（「契約不要訂得過嚴」）：`docs/data-files.md` 與 `docs/training.md` 現在寫「只換詞類表（含 μ）就要重調 tinylm、發新的 tinylm-v…」。這條是怕融合參數配錯詞類表；本片的 μ 是在 tinylm-v5 開著的出貨設定上選的，量的就是要出貨的組合。同一個 PR 改成：只改 μ、而且是在出貨的小模型上量過才選的，可以沿用原本的 tinylm；重新分群（類別或機率表變了）仍要重調。tinylm-v5 只綁模型與 KN 側檔的雜湊，不綁詞類表，檔案不用動。

## 3. 驗收

1. Release `classes-v5` 下載回來的雜湊與大小和 §1 相同。
2. 本機：`make test-rust`（188 WSL）、`make test-python`、`make test-swift` 都過；golden 是重產的，Rust 與 Python 逐位元組相同。
3. CI 全綠（含 `changes`、`core`、`shell`）。
4. `/code-review` 沒有未處置的問題＋fresh verifier CONFIRMED。
5. 選字效果不在這個 PR 另量：§0 的數字就是這個檔案在出貨設定上的結果；發版前照 `docs/verification.md` 跑 bench 與保留集。

## 4. 停止條件

- Release 的檔和 §1 的雜湊不同。
- Rust 與 Python 的 golden 不一致。
- 寫死期望值的測試失敗而原因不是 μ（例如載入失敗、雜湊錯配）。
- 發版前的 bench 在 lytune 沒有出現 §0 的方向（兩種設定都沒有改對 > 改壞）：停下回報，不推 tag。

## 5. 範圍外

- 重新分群、重調或重訓 tinylm、依設定分開的 μ（核心改動）。
- 發版本身（推 tag 照舊每次問）。

## 6. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
