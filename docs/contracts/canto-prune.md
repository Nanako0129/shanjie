# 疊加層拿掉粵語詞（canto-prune）

狀態：READY（2026-10-14，plan-verifier 第 1 次就 READY）；修訂一（同日，`/code-review` 之後，§7）。

## 0. 起因

- 加朋友名字（PR #151）時，縮寫預測的 golden 有一個查詢的前 9 名原本是「冇陰公、冇陰功」。維護者：「冇陰公、冇陰功這也太罕見了」。選擇題選「另開一片拿掉」。
- 量過：`data/lexicon/overlay-add.tsv` 有 338,253 個詞，其中 721 個含下面 §1 的粵語用字（不算「俾」「咩」）；8 個是教育部《重編國語辭典修訂本》的詞目，其他 713 個不是（710 個來自維基詞典的標題、3 個來自中文維基）。清單維護者已看過。

## 1. 規則

- **粵語用字**（`tools/build_overlay.py` 的具名常數 `CANTO_CHARS`）：冇 嘅 啲 喺 佢 嘢 唔 咁 嚟 搵 睇 畀 乜 啱 攰 嗰 哋 嚿 諗 噉 嗮 噏 揸 攞 餸 嚫 撳 搣 嗌 掟 喐 睩 嗱。「俾」（俾斯麥）、「咩」（羊咩咩、「真的咩」）、「喎」（喎斜、口眼喎斜）、「嘞」（好嘞）國語也用，不列入。
- **保留**：教育部《重編國語辭典修訂本》的詞目，用 `build()` 本來就讀的 `moe_titles()`（萌典 `dict-revised`；只用來決定去留，不散布內容）。
- `build()` 在**候選階段**拿掉：`cand` 只收不符合 `cantonese(w, moe)` 的標題。所以 S2n、S2f 的判斷與落點都看不到這些詞，紀錄檔（`experiments/s2n/overlay-removed.tsv`、`experiments/s2f/overlay-variant-removed.tsv`）裡來源詞含粵語字的列跟著消失，`experiments/s2f/check_overlay.py` 仍然是 0／0／0。手動詞不套用。
- `data/lexicon/overlay-add.tsv` 與兩個紀錄檔由 `python3 tools/build_overlay.py` 重產（有釘住的 dump 的機器）；`--check` 和檔案一致。
- 基底詞庫 `mcbpmf-data.txt` 與 ACG 詞包不動。

## 2. 怎麼看到它有效

- 拿掉之後，疊加層裡含 `CANTO_CHARS` 的非手動詞只剩萌典詞目；測試把這個結果（目前 9 個詞）固定下來（CI 沒有萌典，所以斷言已提交的檔案）。`experiments/s2f/check_overlay.py` 0／0／0。
- 縮寫預測 golden 重產後，原本「冇陰公、冇陰功」那個查詢不再出現這兩個詞。
- 評測（出貨設定 `--context --packs acg --tinylm`，聊天與書面）：dev302、typing76、錯字回報、probe、fw 的 rowstats 前後比較，用 `tools/evalstats.py compare` 報同一張表；cvtune、wikitune 在 Mac 以外的機器跑同一張表。改壞數記進研究紀錄，改壞的列逐一看原因。

## 3. 驗收

1. `tools/test_build_overlay.py` 加：`cantonese()` 的真值表（含粵語字的詞拿掉、萌典詞目留下、「俾」「咩」「喎」「嘞」不算）；已提交的疊加層裡含粵語字的非手動詞正好是固定的那一組。變異「不檢查詞目」讓真值表失敗。`build()` 裡呼叫的那一行沒有 dump 就跑不到，由 `--check`（本機）與上面的固定結果把關。
2. `make test-python`、`make test-rust` 通過（golden 依規則重產：`experiments/sp/golden_predict.py`，其他依賴詞庫的 golden 照它們的產生程式重產，變動寫進研究紀錄）。
3. §2 的評測表：任一集合改壞 > 改對就停（§5）。
4. 保留集：fresh verifier 照規則跑一次。

- 同一個 PR 另加維護者要的手動詞「喵菜子」「哈那口」（「菜菜子」本來就在基底詞庫，只加回歸句）（`data/lexicon/overlay-manual.tsv`，維護者 2026-10-14「兩則都可以做」），回歸句子併進 `eval/golden/manual-words-rows.txt`。

## 4. 範圍外

- 其他方言用字（客語、閩南語用字）與其他罕見詞：另議。
- 基底詞庫裡含這些字的 39 個詞。
- 粵語詞的降分（維護者選了拿掉）。

## 5. 停止條件

- 任一評測集合在任一設定下改壞 > 改對，或改壞的列裡有台灣常用的寫法被拿掉：停下來回報，調整 `CANTO_CHARS` 後再量。

## 6. 負責人與順序

- main 實作（改動小：常數、`build()` 一個判斷、測試、資料檔、golden），接在 PR #151 之後（同樣動 `tools/build_overlay.py` 與 `overlay-add.tsv`，從 `feat/friend-names` 開分支，#151 合併後 rebase 到 main）。
- 審查：/code-review＋fresh verifier（含保留集）＋CI，merge commit。

## 7. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
| `/code-review`（修訂一之前） | 過濾放在 S2n／S2f 之後，17 個替代寫法指向被拿掉的詞，`check_overlay.py` 失敗 | 改在候選階段過濾，兩個紀錄檔重產，檢查 0／0／0 |
| 同上 | 漏掉嗮 噏 揸 攞 餸 嚫 撳 搣 嗌 掟 喐 睩 嗱 | 加進 `CANTO_CHARS` |
| 同上 | 「喎」「嘞」國語也用（口眼喎斜、好嘞） | 從 `CANTO_CHARS` 拿掉 |
| 同上 | 寫死的保留詞和「建置時不讀萌典」的說法不對：`build()` 本來就讀萌典 | 改用 `moe_titles()`，刪掉常數與錯的說法 |
| 同上 | cvtune、wikitune 沒量 | 在 Mac 以外的機器量，寫進研究紀錄 |
| 同上 | 喵菜子的回歸句不加詞也對 | 研究紀錄與測試註解照實寫（只固定行為） |
