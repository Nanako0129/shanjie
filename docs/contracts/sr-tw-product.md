# 產品片：會意 SJ-SR-8-15M、三檔新融合格、常談句首溫和版（SR-TW 產品）

狀態：**修訂三**（2026-11-06）：收尾審查 REVISE 的兩項照最小修改（測試範圍改成規則、摘要行只比設定標記欄），維護者選「改完就開始實作」。修訂二：plan-verifier 第二次 REVISE 的三項逐項處置，三項都改（出貨標記不加尾碼、只在關掉溫和版時加；釘著選字或格子的既有測試逐一重量並保留前提；下載與檔案說明的文件補進清單、grep 不分大小寫）。修訂一：plan-verifier 第一次 REVISE 的四項照最小修改（溫和版的摘要行標記、引擎路徑的測試、參數數待量與 npz 雜湊出處、預算與超支停止條件）。來源：`docs/contracts/sr-no-twphrases.md` §8（產品步驟）、研究紀錄的 SR-TW-M（溫和版）、SR-TW-V／SR-TW-R（V4-s3 確認）、FUSE-1（標準格確認，wikijudge8）、FUSE-2（保守格確認，wikijudge9）。維護者的選擇：SR-TW-R 後「先在 VM 實機打『權限』再決定」、FUSE-1 前「先重選融合門檻」、FUSE-2 前「先重選保守與積極兩檔」、聊天積極「積極＝新標準」、LEXGAP 結案時「結案，回產品片」（decisions 2026-11-05、11-06）。

## 1. 要做到什麼

出貨版的語意校正換成三件一起：

1. **會意換成 V4-s3**（研究紀錄 SR-TW-V／SR-TW-R 量測用的匯出檔 `2c69a761…`），代號會意 SJ-SR-8-15M。參數數**待量**：照 `docs/releases/sj-sr-7-15m.md` 的算法從新檔的檔頭與張量清單算（〔推論〕V4-s3 從出貨權重接續訓練、字表與架構相同，應和 SJ-SR-7-15M 一樣是 14,646,912；`.sjtl` 的 14,886,880 是位元組數，不是參數數）。訓練文字的繁體句不套 OpenCC `TWPhrases`，「權限」不再被抹成「許可權」。
2. **三檔融合格**（α, τ, δ）：

   | 檔 | 聊天（`core/src/colloquial.rs` 的 `cell`） | 書面（`.sjtl` 檔頭） |
   |---|---|---|
   | 保守 | (1.3, 2, 1.0) | (0.8, 2, 1.5) |
   | 標準 | (1.1, 2, 0) | (0.8, 2, 0) |
   | 積極 | (1.1, 2, 0) | (0.8, 2, 0) |

   `.sjtl` 檔頭的聊天格不改（出貨的聊天三檔都走常談的 `cell`，檔頭的聊天格只在常談不生效時當退路）。
3. **常談句首溫和版**（SR-TW-M 的 `unseen_start_unigram`）成為出貨行為：句首詞在常談表的句首上下文裡沒出現過時，第一個詞的常談項用單字估計，不用句首退回值。凡是常談生效的地方（App 與評測 CLI 的預設）都開；評測 CLI 留一個關掉它的研究旗標。

**怎麼看到它有效**：評測 CLI 在聊天三檔的摘要行有 `+tl:<新 .sjtl 前 8 碼>` 與 `+colloq:81c2470d`（溫和版開，標記和現行出貨相同）；引擎（App 走的 `Engine::load_colloquial`）的測試斷言溫和版開著；錯字回報第 179、180 列（「權限」）兩種設定的標準檔都打對；wikijudge7 的「上級」「士卒」「醉酒」三列書面標準檔打對；VM 實機照 2026-11-05 那 10 句「權限」打 ≥ 9 句對，三列句子照打對。

## 2. 要改的地方

- **模型**：從 V4-s3 的 npz 用 `export_sjtl.py --type int8` 匯出 SJTL0004，`--ngram-model`、`--kn-side` 綁現行出貨的 bigram（`e3fb6056…`）與 kn；書面三檔照 §1 的表，聊天格與其他檔頭欄位照抄現行出貨 `data/lm/tinylm.sjtl`。
  - **方法核對**：同一份 npz、照抄現行檔頭全部格子重新匯出，要和 SR-TW 量測用的 `2c69a761…` 逐位元組相同（證明匯出方法、綁定與 npz 都對），不同就停。新檔只差書面三檔的格子。
- **核心**：
  - `core/src/colloquial.rs`：`PAIRED_TINYLM_SHA256` 換成新 `.sjtl` 的完整雜湊（註解寫出處）；`cell()` 三檔照 §1；溫和版在出貨載入路徑預設開。
  - 改到核心行為：同一個 PR 更新 `docs/contracts/s3a.md` 的按鍵規則表（若有融合格或常談的段落）與 `core/include/shanjie.h`（若有相關說明或常數）。
- **評測 CLI**：預設跟出貨一樣（溫和版開）；`--colloquial-unseen-start-unigram` 改成反向的研究旗標（關掉溫和版），名稱與說明一起改。摘要行：溫和版開時（出貨預設）聊天標記照舊是 `+colloq:81c2470d`，不加尾碼；用研究旗標關掉時加 `+us0`（`+colloq:81c2470d+us0`）。兩種狀態在摘要行上分得開；`tools/triage.py` 的 `expected_tag` 與 `tools/bench.py` 的 `colloq_tag` 照現行寫法就對得上出貨標記，不用改（它們不帶關掉的旗標）。溫和版在 App 走的引擎路徑有沒有開，由 §3.5 的引擎測試看，不靠標記。
- **引擎**：`core/src/engine.rs` 的 `load_colloquial` 載入後開溫和版（`set_unseen_start_unigram(true)`），`core/src/colloquial.rs` 的註解（「Off by default and in the engine」）一起改。
- **資料與發佈**：`data/tinylm.sjtl.sha256`、`data/models.tsv`、`scripts/fetch-models.sh` 與 CI 的下載名稱、會意的模型 Release（**建立前問維護者**）、`docs/releases/` 加會意 SJ-SR-8-15M 的模型說明、App 關於頁的模型列表、README 與網站（`site/`）的模型代號（grep 不分大小寫 `SJ-SR-7-15M|sj-sr-7-15m`，現況句全部改；研究紀錄、過去的 Release 說明與 appcast、其他模型的說明、`docs/releases/sj-sr-7-15m.md` 這些歷史紀錄不改）。同一份清單另加：`scripts/build-app.sh`（缺檔與雜湊不符的訊息要寫 `sj-sr-8-15m`）、`CONTRIBUTING.md`、`docs/data-files.md`、`LICENSES/data.md`（出貨 `tinylm.sjtl` 的 Release 與訓練文字）、`.github/workflows/release.yml`。
- **釘著選字或格子的既有測試**：規則是**凡是載入真的 `data/lm/tinylm.sjtl` 或讀 `colloquial::cell` 的測試**（`core/tests/`、`cli/tests/`、`macos/Tests/`、`experiments/*/test_*.py`），都照這一條處理；下面列的是已知的，不是全部。已知：`core/tests/engine_colloquial.rs` 的 `SHIPPING`／`WITH_TERM`（dev302 第 25 列）、`CHANNEL_ROWS`、`LEVEL_ROWS` 與保守檔的斷言；`cli/tests/colloquial.rs` 的 `the_term_applies_at_every_strength`（註解寫著 2026-10-27 的格子）與 `nothing_changes_elsewhere`；`core/src/colloquial.rs` 的單元測試 `fused_pick_uses_the_registered_cell`（新保守格 (1.3, 2, 1.0) 的增益 1.26 ≥ 1，原本「保留第 0 名」的斷言會變）；`experiments/colloq-levels/expect_core.py` 的 `CELL`；`core/tests/engine_rerank.rs` 的 `a_manual_word_survives_the_formal_rerank_on_the_final_files`（前提：書面標準／積極下沒有 ManualSet 時重排會把「米雅歌」改掉）；`core/tests/reading_decode.rs` 的 `the_term_makes_the_written_sentence_right`、`row_81_is_right_without_the_reading_channel`（聊天標準釘 `vec![0]`）；`experiments/r1-reading-decode/test_r1.py` 的三格互不相同的斷言（只在本機，改成積極＝標準）；另外 `engine_fuzzy.rs`、`engine_learn.rs`、`engine_cap.rs`、`engine_predict.rs`、`engine_fuzzy_rerank.rs` 也載入真模型，跑過看有沒有變。每一個都在新 `.sjtl`、新格子下用 CLI 的 `--tinylm-scores` 的 `c` 重新量，註解寫量的指令與日期；**保留每個測試原本要證明的事**（例如每一檔各找一列公開句子，在那一列該檔的格子和標準格或檔頭的格子選得不同）。積極＝標準之後，「積極格和標準格選得不同」這個前提不再成立，改成斷言積極的格子等於標準的格子（維護者的決定）。找不到符合前提的公開列就停（§5）。把 `cell()` 改回舊值時，至少一個引擎測試要失敗。
- **測試與 golden**：釘著 `tl:f7d0ac63` 的 golden 檔與測試（`eval/golden/*`、`cli/tests/*`、Swift selftest 的模型雜湊，grep 全部）重產；`experiments/colloq-levels/expect_core.py`、`experiments/loss0/loss0.py` 裡的雜湊一起改。golden 重產的差異要能用「選字變了」解釋，不能有格式或標記的意外變化。

## 3. 驗收

1. **出貨標準四條**（研究方向「出貨標準」，對現行出貨 main），聊天與書面、三檔都量；集合照現行規則（第 1 條 discordtune；第 3 條 cvtune-clean-tw0；回歸層 dev302、打字測驗；第 4 條錯字回報兩種設定合計）。逐檔逐條報；任一條不過就停（§5）。
2. **檢查列**：錯字回報第 179、180 列兩種設定標準檔打對；wikijudge7「上級」「士卒」「醉酒」三列書面標準檔打對（只看這三列的選字，不當作判讀集再判）。
3. **摘要行**：每一次執行的摘要行 `+tl:` 是新檔的前 8 碼；聊天三檔有 `+colloq:81c2470d`（沒有 `+us0`）；每一行的**設定標記欄**（`lm-…` 那一串）和 **main 上的** `eval/golden/reading-decode-shipping.txt`（不是這個 PR 重產的那份）比，只有 `+tl:<前 8 碼>` 不同。行尾的統計（`top1`、`top1_sha256`）會因選字改變，由 §2「golden 重產的差異要能用選字解釋」管。不符就停（配對沒換好時常談與讀音通道項會靜靜關掉）。
4. **第二份來源**：第 1、3 條用到的執行在另一台機器重跑，rowstats 逐位元組相同才判讀。
5. **測試**：`make test-rust`（完整的在第二台機器）、`make test-python`、`make test-swift`、CI 全綠。在既有的 `core/tests/engine_colloquial.rs` 加兩個斷言：(a) `Engine::load_colloquial` 之後 `colloquial().unwrap().unseen_start_unigram()` 為真；(b) 在一列溫和版會改選字的公開句子上（錯字回報第 179 列聊天標準，或實作時量到的另一列公開列，寫進測試註解），引擎的選字等於 CLI 預設的選字、不等於 CLI 關掉溫和版時的選字。把引擎的那一行拿掉時，這個測試要失敗。
6. **VM 實機**（tart，開發版）：2026-11-05 那 10 句「權限」≥ 9／10；三列句子照打打對；聊天三檔各打一句，設定頁切換強度後選字照預期改變。
7. 合併關卡照程式改動：兩個 fresh verifier（程式審查、結果驗收）、CI 全綠、merge commit。

## 4. 量測

- GCP（`lib/gcp-mac.sh`、`lib/gcp-remote.sh`），所有臂同一種 CPU；逐列結果帶回 `shanjie-private`。遠端腳本照規則存檔、fresh verifier 審過、Monitor 監看。
- 兩臂：main（現行出貨）與本 PR 的建置。私人集合只在 `/dev/shm`，不寫 `--dump-words`／`--tinylm-scores`。
- **預算**：GCP 一次，48 個執行（2 臂 × 3 檔 ×［聊天 discordtune、cvtune-clean-tw0、打字測驗、dev302、錯字回報 5 個＋書面 打字測驗、dev302、錯字回報 3 個］）；照 LEXGAP-0 量測的實測（c4d-32，20 個執行 324 秒，含 discordtune 與 cvtune），估約 15 分鐘（`EST_MIN` 15）。188 一次：第 1、3 條的 12 個執行（2 臂 × 3 檔 × discordtune、cvtune-clean-tw0），估約 30 分鐘。檢查列（第 179、180 列、wikijudge7 三列）在 Mac 上單句解。
- 報結果先講兩邊看得到的輸入：底層（通語、辨類、承接、連文、讀音項）相同、前文最多 64 字、詞包照 App 預設；差的是會意權重、三檔格子與常談句首的估計。

## 5. 停止條件

- 方法核對不等於 `2c69a761…`。
- GCP 或 188 的時間超過預算 1.5 倍：停下來問。
- 出貨標準任一條任一檔不過：停下報數字、出選擇題，不在同一片裡調格子救。
- 檢查列沒全對、摘要行不符、兩台機器不一致。
- golden 重產出現不能用選字解釋的差異。
- §2 規則涵蓋的任何測試，找不到符合原本前提的公開列。

## 6. 範圍外

- 重訓會意、重建通語或連文；LEXGAP 的 691 詞（下一次重建通語時放進計數詞表）。
- `.sjtl` 檔頭的聊天格。
- 發版（推 tag 另外問）與網站部署（另外問）。
