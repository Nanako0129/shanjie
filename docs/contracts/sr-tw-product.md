# 產品片：會意 SJ-SR-8-15M、三檔新融合格、常談句首溫和版（SR-TW 產品）

狀態：**草稿**（2026-11-06）。來源：`docs/contracts/sr-no-twphrases.md` §8（產品步驟）、研究紀錄的 SR-TW-M（溫和版）、SR-TW-V／SR-TW-R（V4-s3 確認）、FUSE-1（標準格確認，wikijudge8）、FUSE-2（保守格確認，wikijudge9）。維護者的選擇：SR-TW-R 後「先在 VM 實機打『權限』再決定」、FUSE-1 前「先重選融合門檻」、FUSE-2 前「先重選保守與積極兩檔」、聊天積極「積極＝新標準」、LEXGAP 結案時「結案，回產品片」（decisions 2026-11-05、11-06）。

## 1. 要做到什麼

出貨版的語意校正換成三件一起：

1. **會意換成 V4-s3**，代號會意 SJ-SR-8-15M（14,886,880 個參數；權重 npz SHA-256 `99c7f44c…`，研究紀錄 SR-TW-V）。訓練文字的繁體句不套 OpenCC `TWPhrases`，「權限」不再被抹成「許可權」。
2. **三檔融合格**（α, τ, δ）：

   | 檔 | 聊天（`core/src/colloquial.rs` 的 `cell`） | 書面（`.sjtl` 檔頭） |
   |---|---|---|
   | 保守 | (1.3, 2, 1.0) | (0.8, 2, 1.5) |
   | 標準 | (1.1, 2, 0) | (0.8, 2, 0) |
   | 積極 | (1.1, 2, 0) | (0.8, 2, 0) |

   `.sjtl` 檔頭的聊天格不改（出貨的聊天三檔都走常談的 `cell`，檔頭的聊天格只在常談不生效時當退路）。
3. **常談句首溫和版**（SR-TW-M 的 `unseen_start_unigram`）成為出貨行為：句首詞在常談表的句首上下文裡沒出現過時，第一個詞的常談項用單字估計，不用句首退回值。凡是常談生效的地方（App 與評測 CLI 的預設）都開；評測 CLI 留一個關掉它的研究旗標。

**怎麼看到它有效**：評測 CLI 在聊天三檔的摘要行有 `+tl:<新 .sjtl 前 8 碼>` 與 `+colloq:81c2470d`；錯字回報第 179、180 列（「權限」）兩種設定的標準檔都打對；wikijudge7 的「上級」「士卒」「醉酒」三列書面標準檔打對；VM 實機照 2026-11-05 那 10 句「權限」打 ≥ 9 句對，三列句子照打對。

## 2. 要改的地方

- **模型**：從 V4-s3 的 npz 用 `export_sjtl.py --type int8` 匯出 SJTL0004，`--ngram-model`、`--kn-side` 綁現行出貨的 bigram（`e3fb6056…`）與 kn；書面三檔照 §1 的表，聊天格與其他檔頭欄位照抄現行出貨 `data/lm/tinylm.sjtl`。
  - **方法核對**：同一份 npz、照抄現行檔頭全部格子重新匯出，要和 SR-TW 量測用的 `2c69a761…` 逐位元組相同（證明匯出方法、綁定與 npz 都對），不同就停。新檔只差書面三檔的格子。
- **核心**：
  - `core/src/colloquial.rs`：`PAIRED_TINYLM_SHA256` 換成新 `.sjtl` 的完整雜湊（註解寫出處）；`cell()` 三檔照 §1；溫和版在出貨載入路徑預設開。
  - 改到核心行為：同一個 PR 更新 `docs/contracts/s3a.md` 的按鍵規則表（若有融合格或常談的段落）與 `core/include/shanjie.h`（若有相關說明或常數）。
- **評測 CLI**：預設跟出貨一樣（溫和版開）；`--colloquial-unseen-start-unigram` 改成反向的研究旗標（關掉溫和版），名稱與說明一起改。
- **資料與發佈**：`data/tinylm.sjtl.sha256`、`data/models.tsv`、`scripts/fetch-models.sh` 與 CI 的下載名稱、會意的模型 Release（**建立前問維護者**）、`docs/releases/` 加會意 SJ-SR-8-15M 的模型說明、App 關於頁的模型列表、README 與網站（`site/`）的模型代號（grep `SJ-SR-7-15M` 全部改）。
- **測試與 golden**：釘著 `tl:f7d0ac63` 的 golden 檔與測試（`eval/golden/*`、`cli/tests/*`、Swift selftest 的模型雜湊，grep 全部）重產；`experiments/colloq-levels/expect_core.py`、`experiments/loss0/loss0.py` 裡的雜湊一起改。golden 重產的差異要能用「選字變了」解釋，不能有格式或標記的意外變化。

## 3. 驗收

1. **出貨標準四條**（研究方向「出貨標準」，對現行出貨 main），聊天與書面、三檔都量；集合照現行規則（第 1 條 discordtune；第 3 條 cvtune-clean-tw0；回歸層 dev302、打字測驗；第 4 條錯字回報兩種設定合計）。逐檔逐條報；任一條不過就停（§5）。
2. **檢查列**：錯字回報第 179、180 列兩種設定標準檔打對；wikijudge7「上級」「士卒」「醉酒」三列書面標準檔打對（只看這三列的選字，不當作判讀集再判）。
3. **摘要行**：每一次執行的摘要行 `+tl:` 是新檔的前 8 碼；聊天三檔有 `+colloq:81c2470d`，其餘標記與 `eval/golden/reading-decode-shipping.txt` 的出貨標記相同。不符就停（配對沒換好時常談與讀音通道項會靜靜關掉）。
4. **第二份來源**：第 1、3 條用到的執行在另一台機器重跑，rowstats 逐位元組相同才判讀。
5. **測試**：`make test-rust`（完整的在第二台機器）、`make test-python`、`make test-swift`、CI 全綠。
6. **VM 實機**（tart，開發版）：2026-11-05 那 10 句「權限」≥ 9／10；三列句子照打打對；聊天三檔各打一句，設定頁切換強度後選字照預期改變。
7. 合併關卡照程式改動：兩個 fresh verifier（程式審查、結果驗收）、CI 全綠、merge commit。

## 4. 量測

- GCP（`lib/gcp-mac.sh`、`lib/gcp-remote.sh`），所有臂同一種 CPU；逐列結果帶回 `shanjie-private`。遠端腳本照規則存檔、fresh verifier 審過、Monitor 監看。
- 兩臂：main（現行出貨）與本 PR 的建置。私人集合只在 `/dev/shm`，不寫 `--dump-words`／`--tinylm-scores`。
- 報結果先講兩邊看得到的輸入：底層（通語、辨類、承接、連文、讀音項）相同、前文最多 64 字、詞包照 App 預設；差的是會意權重、三檔格子與常談句首的估計。

## 5. 停止條件

- 方法核對不等於 `2c69a761…`。
- 出貨標準任一條任一檔不過：停下報數字、出選擇題，不在同一片裡調格子救。
- 檢查列沒全對、摘要行不符、兩台機器不一致。
- golden 重產出現不能用選字解釋的差異。

## 6. 範圍外

- 重訓會意、重建通語或連文；LEXGAP 的 691 詞（下一次重建通語時放進計數詞表）。
- `.sjtl` 檔頭的聊天格。
- 發版（推 tag 另外問）與網站部署（另外問）。
