# 調參集的正解不套 TWPhrases（TW0 版）

狀態：已建好（2026-11-05，研究紀錄同日）；以下是修訂三的原文，「區域性」「分割槽」後來由維護者更正為台灣也用的詞、不算錯轉。修訂三（2026-11-04，任何新版的分數之前）。修訂三改方法：收尾審查（修訂二）指出 TWPhrases 有 56 條鍵或值不是全漢字（例如「U盤→隨身碟」「乙肝→B肝」），「保留舊列、只換正解」的逐段配對前提不成立；維護者選「用現成工具重新抽（推薦）」與「維基那半放 188」（decisions 2026-11-04）。修訂一、二的逐列對應方法整個拿掉。修訂三的審查（plan-verifier）REVISE 五項照最小修法修完，維護者選「五項修完就實作」（不再送契約審查）：新選項另起名、寫出兩邊的指令並核對 188 的來源雜湊；去重比對全部七個訓練來源並鎖雜湊；文字來源平行；cv 那半用另一個工作目錄；「區域性」這類詞追到原句。

## 0. 起因

- 會意 SJ-SR-7-15M 的訓練文字與調參集的正解，都是 `build_counts.convert` 預設 `trad_tw_phrases=True` 轉出來的：繁體句也套 OpenCC `TWPhrases.txt`，例如「權限 → 許可權」「局部 → 區域性」「分區 → 分割槽」（研究紀錄 2026-11-04「TWPhrases 盤點」）。
- 調參集的正解裡因此留著錯誤轉換的寫法：wikitune-clean 有「區域性」1、「分割槽」3、「型別」1、「釋出」1 處，cvtune-clean 有「型別」2、「釋出」1、「檢視」1 處。修好的會意在這些列反而會被算成改壞，量尺偏向舊模型。
- 維護者 2026-11-04 選「重建不套的新版」：之後的出貨標準與選值改用新版，舊版留著，只拿來對照過去的數字。

## 1. 要做到什麼

1. **建法**：用現成的 `experiments/s2/build_tune.py`，同一份來源、同一個亂數種子（20261003）、同樣的列數上限（cvtune 4,000、wikitune 3,000）重新抽，只差「繁體句不套 TWPhrases」：
   - 新選項 `--cv-no-trad-tw-phrases`（另起名，不改現有的 `--tune-no-trad-tw-phrases`）：`split_colloquial` 的 tune 側參考句與讀音的繁體句不套 TWPhrases；切分仍用原句的雜湊 `is_tune`，哪些原句進 tune 不變。另加 `--cv-name`（預設 `cvtune`），帶新選項時 colloquial-train 不寫出。
   - 不帶新選項時，所有既有輸出逐位元組不變；特別是 wikijudge6 的指令（`--part wiki --wiki-range 335000 340000 --wiki-name wikijudge6 --tune-no-trad-tw-phrases`）照舊得到 `9d792e5a…`（`test_build_tune.py` 用合成來源斷言「只帶 `--tune-no-trad-tw-phrases`」的 wiki 輸出和改動前相同）。
   - 維基那半的取樣接在 cv 那半的亂數狀態之後，所以兩邊都要帶 `--cv-no-trad-tw-phrases`，而且 188 上的 CV／Tatoeba 來源檔與讀音資料要和 Mac 的逐檔雜湊相同。
   - 指令（事先固定）：
     - Mac：`S2_WORK=~/.cache/shanjie/work/tw0 python3 experiments/s2/build_tune.py --part cv --cv-no-trad-tw-phrases --cv-name cvtune-tw0`
     - 188：`S2_WORK=<188 的工作目錄> python3 experiments/s2/build_tune.py --part wiki --cv-no-trad-tw-phrases --tune-no-trad-tw-phrases --wiki-name wikitune-tw0`
     - `S2_WORK` 另開目錄，不碰 `~/.cache/shanjie/work/s2/tune/` 裡既有的 `colloquial-train.txt` 等檔；跑前跑後核對口語池 `eb905936…` 的雜湊不變。
2. **去重**（同 2026-10-18 clean 版的做法，範圍改成這一輪會意真正的訓練文字）：`tools/holdout_guard.py report --no-trad-tw-phrases` 把兩份新檔比對 SR-TW 的七個訓練來源，報告裡 `rows` 列出的列從檔案拿掉，其餘照原順序寫成 `cvtune-clean-tw0`、`wikitune-clean-tw0`。七個來源（研究紀錄「SR-TW 訓練開跑與 D1」節、SJ-LM-7 凍結的 `dedup-final`）：維基 dump 前 200,000 篇（`--wiki … --articles 200000`）、口語池 `eb905936…`、`synth.txt` `bec7a6a2…`、g0v `cedad812…`、PTT 內文 `f42d5c8b…`、PTT 推文 `6568bc66…`、字幕（`dedup-final/subs` 那一份，雜湊在跑之前從 Mac 的檔算出、寫進腳本與報告）。
   - 平行：維基用 `--procs`；每個文字來源各開一個 `holdout_guard report` 程序，和維基的池同時跑，程序數加起來等於核心數；最後合併各報告的 `rows`（聯集）。合併的程式與腳本一起送審。
3. **輸入核對**（跑之前，不符就停）：維基 dump `zhwiki-20261001-pages-articles.xml.bz2` `5db9052e…`、`TWPhrases.txt` `bcb435b7…`（`build_counts.py` 鎖的那一份）、上面七個訓練來源、CV／Tatoeba 來源檔與讀音資料（`tools/readings.py` 讀的萌典與 McBopomofo 檔；188 上的雜湊必須等於 Mac 上的，腳本先比對）。
4. **輸出位置**：列檔全部在 repo 外（Mac 的 `~/.cache/shanjie/work/tw0/` 與 `~/.cache/shanjie/clean/`，188 上在 `%USERPROFILE%\shanjie-private\`，用完刪）；研究紀錄與 `eval/README.md` 只寫列數與 SHA-256。
5. **機器**：
   - cvtune 那半在 Mac（格式轉換，單程序，幾分鐘）。
   - wikitune 那半與兩份的去重放 188（維護者 2026-11-04「那改188」）。讀 dump 前 300,000 篇是單一串流、只能單核心，這一步經維護者同意在 188 以單程序跑（負載檢查對它只記錄，記秒數）；去重照上面的平行方式用滿核心。腳本照 188 的規則（`fullspeed-wsl.ps1`、心跳回報產出、掃錯、私人檔用完刪、從複本執行），送出前 fresh verifier 審過。估 60 分鐘，超過 120 分鐘停。

## 2. 怎麼看到它有效（驗收）

- 單元測試（`make test-python`）：不帶新選項時 `build_tune` 的 cvtune 與 colloquial-train 輸出和現在逐位元組相同；帶選項時，一個含「權限」的繁體原句進 tune 側後參考句是「權限」（不帶時是「許可權」），一個簡體原句照舊轉成台灣用詞。
- 實際輸出：
  - 兩份新集合裡「區域性」「分割槽」「擴充套件」「指令碼」「許可權」的出現次數報出來；每一處都用同一段切分與篇數重跑一次、只對這幾列記下子句來自哪一個原句，用 `convert` 自己的簡體判斷分類：來自簡體句的照實報（簡體句照舊套 TWPhrases，不算失敗），來自繁體句的就停（§4）。
  - 兩份裡至少有一列含 TWPhrases 的鍵（例如「權限」「程序」「支持」），而且鍵的總數多於舊 clean 版（舊版 cvtune 8 處、wikitune 11 處）。
  - 列數：cvtune-clean-tw0、wikitune-clean-tw0 各自在 2,000 列以上（舊版 3,286、2,181）。
- 研究紀錄寫兩份的列數、去重拿掉的列數、SHA-256；`eval/README.md` 加兩列。

## 3. 之後怎麼用（過渡）

- SR-TW-V（研究紀錄 2026-11-04 登記）起，出貨標準第 3 條（cvtune-clean 非劣性）與選值改用 `cvtune-clean-tw0`，書面調參用 `wikitune-clean-tw0`。新舊兩版的列不一一對應，跨版本的數字不比，舊版的數字只當歷史。
- `sr-no-twphrases.md` 鎖定的 cvtune-clean `d0960ebe`、wikitune-clean `d576ea94` 屬於它到 SR-TW-Q 為止的量測，數字不改。它 §8 的產品步驟用候選當初被選出的那一版：候選來自 SR-TW-V 就用 tw0 版；若維護者改選 SR-TW-Q 確認過的 W1，就照舊版。同一個 PR 在 `sr-no-twphrases.md` §8 加這一句。其他進行中、還沒重新登記的片（R1 讀音解碼 `exp/r1-reading-decode`）照舊用舊版，下一次登記時寫明換版。
- 同一個 PR 改寫規則的文件：`docs/methodology.md`（cvtune-clean 守門那一段）、`docs/iteration-plan.md`（出貨標準）、`CONTRIBUTING.md`（有提到就改）、`eval/README.md` 加兩列；維護者本機的出貨標準由 main 改。
- 這兩份是調參層，不是判讀集。新版雖然沒看過，但抽自和舊調參集同一段來源、大量重疊，不能當判讀集用。

## 4. 停止條件

- 輸入雜湊不符（含 188 與 Mac 的來源或讀音資料不同、口語池在 cv 那半前後不同）；不帶選項的回歸斷言不過；任一份少於 2,000 列；「區域性」這類詞有任何一處追到繁體原句；188 那半超過 120 分鐘：停下回報，不調整方法再跑。

## 5. 範圍外

- wikijudge1～5、dev302、打字測驗、錯字回報（後三個是手寫的，沒有經過轉換）；chatclause、discordtune（私人聊天集；`experiments/e2-chateval/build_chatclause.py` 沒有呼叫任何 OpenCC 轉換，2026-11-04 讀程式確認）。
- 重新訓練任何模型（另一片）。
- 簡體句的 TWPhrases（SR-TW-V 的 V4 另處理）。
- 把 `build_counts.convert` 等的 `trad_tw_phrases` 預設改成 False：呼叫者多（`holdout_guard`、`build_digits`、名單、字幕清理、讀音計數…），改預設會悄悄改變它們的輸出，另開一片，逐一列出呼叫者並明寫值。
