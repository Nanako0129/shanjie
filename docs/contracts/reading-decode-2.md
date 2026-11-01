# 契約：讀音項進核心解碼器（R1 產品片，修訂二；reading-decode-2）

狀態：修訂二（2026-11-02：第二次 REVISE 兩項 P1 與非阻擋項照改，見 §8），待收尾審查。取代 `docs/contracts/reading-decode.md`（2026-10-23 的「重排前一步」設計；維護者之後選了「讀音項放進 n-gram 解碼器本身的路徑分數」，見 `docs/iteration-plan.md` 與 CLAUDE.md「目前的順序」）。

## 0. 起因與證據

- 維護者 2026-10-22：「變快」被選成「便會」是 n-gram 只看字、不看讀音，要從根本修；之後（10-23）決定讀音項放進解碼器本身，每一鍵同步算、語意校正關也生效，不是重排前的一步。
- 探索與確認（`experiments/r1-reading-decode/README.md`，分支 `exp/r1-reading-decode`）：
  - R1e、R1f：解碼器帶讀音項（B-f：λ 2.0、c 4、變調免扣、底分用 mc）＋融合拿掉讀音通道項。R1f（v0.6.1 底層）只差「搞完這波」：詞庫把「波」的標準讀音 ㄅㄛ 排在 ㄆㄛ 後面。
  - R1g：R1f＋簡編本第一讀音在詞庫被排在後面的 12 對（`std-exempt.tsv`）不扣分。照登記不過（條件 3 關的那組 0／0），維護者例外。
  - **R1g 確認片確認成立**（2026-11-01，`f6e28941`；指令與判讀工具 `4261d8cb`，CLI 是 `b90adc96` 的）：書面標準 wikijudge5 +15／−0（p 6.1×10⁻⁵）；聊天標準 cjudge-1 3／4，90% bootstrap 下界 −0.089 個百分點（不變差）；188 第二份來源逐位元組相同。維護者對 R1g 條件 3 的例外記在 decisions（2026-11-01）。
- **公開列的效果（2026-11-02，Mac 單列，R1g 的 CLI）**：「否則｜變快」（ㄅㄧㄢˋ ㄎㄨㄞˋ）與「搞完這波」在現在的出貨四種設定（聊天／書面 × 語意校正開／關）都已經對，有沒有讀音項都一樣，所以不當效果的證據。公開集合裡 R1g 由錯改對的列是聊天標準在錯字回報的 6 列（第 81、159、166、168、170、173 列，多是「長」讀 ㄓㄤˇ），書面與語意校正關在公開集合是 0／0；這幾列在 v0.6.1 錯是讀音通道項（ν）造成的：main 加 `--no-reading-channel` 時本來就對（研究紀錄 2026-10-28 前後的量測、`cli/tests/colloquial.rs`），所以第 81 列「長歪了」只當「ν 項拿掉」的回歸檢查，不當讀音項的效果證據。
- **讀音項本身改第一名的公開句子（2026-11-02，Mac 單列，`b90adc96` 的 CLI；關閉臂在聊天語意校正開時加 `--no-reading-channel`，所以兩臂都沒有 ν 項）**：main 自己寫的句子，照 wikitune 裡讀音項改對的列的用字造的：
  | 句子 | 設定 | 讀音項關 | 讀音項開 |
  |---|---|---|---|
  | 以敵偽人員的名字命名 | 書面標準、書面關 | 以狄為人員的名字命名 | 以敵偽人員的名字命名 |
  | 以敵偽人員的名字命名 | 聊天標準、聊天關 | 對 | 對（不變） |
  | 這個屬叫散白蟻屬 | 四種設定 | 這個數較散白蟻屬 | 這個屬較散白蟻屬 |
  效果的斷言用這兩句：書面兩種設定「以敵偽…」由錯變對；四種設定「這個屬…」的第一名由「數」變「屬」（整句仍不全對，斷言的是第一名不同、而且是「屬」）。

## 1. 要做到什麼

1. **核心解碼器帶讀音項**（所有設定：聊天、書面，三檔，語意校正開或關，有沒有小模型都一樣）：把現在只給評測用的 `CappedLexicon::with_reading_decode`＋`with_reading_exempt`（`core/src/reading.rs`、`core/src/lm.rs`，R1g 工具 `b90adc96`）變成引擎的出貨行為。解碼器對每個單字詞的路徑加 `λ·max(t, −c)`，λ、c、變調免扣、底分 mc 照 `reading.rs` 現在的常數，算法不改。
   - **同一個建表函式**：核心加一個函式（參數是合併後的 `Lexicon`，也就是 `ReadingDecode::new` 現在吃的那一份，和 `data_dir`），讀 `data_dir` 裡的 `mcbpmf-data.txt` 原文與 `std-exempt.tsv`，建出讀音表；引擎的 `load_lm` 與評測 CLI、replay 都只呼叫它，不各寫一份（現在 CLI 自己讀檔、自己呼叫 `with_reading_decode`，要改成走這個函式）。
   - **學習過的字不被讀音項推翻**：路徑上的詞若是學習加分抬起來的（解碼器的 `was_boosted`），那個詞不加讀音項（讀音項最多扣 λ·c＝8，學習加分最多 6，否則使用者教過的罕用讀音會被蓋掉）。評測 CLI 沒有學習，所以 §2 的比對不受影響。
2. **12 對豁免是出貨資料**：`std-exempt.tsv` 逐位元組原樣搬到 `data/lexicon/std-exempt.tsv`（雜湊仍是 `a821a206…`，標記照舊 `+rx:a821a206`；檔頭寫的舊路徑不改，搬過的事寫在 `LICENSES/data.md` 與 `data/lexicon` 的說明）。產生程式 `std_exempt.py` 搬到 `tools/`（它寫的檔頭照舊）。加 `tools/test_std_exempt.py`：只比資料列（非 `#` 開頭的列）等於這個檔；簡編本的 zip 不在 repo，CI 上沒有就跳過（印出跳過的原因），本機有就一定跑；列進 `make test-python`。
   - **誰讀、缺檔怎樣**：`shanjie_load_lm`（`Engine::load_lm`）讀它；缺檔或格式錯時 `load_lm` 回錯（沿用現有「模型檔缺」的錯誤碼，`shanjie.h` 的必要檔清單與錯誤說明一起改），不是靜默沒有豁免。
   - **App 與殼**：`scripts/build-app.sh` 複製這個檔進 Resources、資料來源與授權說明加一行（《國語辭典簡編本》衍生的 12 對讀音，與 `LICENSES/data.md` 一致）；`scripts/check-app.sh` 檢查它在；Swift 測試的檔案清單（`macos/Tests/ShanjieKitTests/`：`RerankTests.swift`、`SelftestTests.swift`、`Support.swift`）加上它。
3. **融合拿掉讀音通道項**（ν 項）：聊天語意校正開時現在的 `ReadingChannel` 項整個拿掉（R1e／R1g 的 `--no-reading-channel` 就是這個行為）；`Engine::set_reading_channel`、`--no-reading-channel`（CLI 與 replay）一起拿掉。常談照舊。
4. **開關**：讀音項沒有使用者開關（不進設定）。評測用的關閉旗標 `--no-reading-decode`（CLI、replay）與 `Engine::set_reading_decode(false)`，不進 `shanjie.h`。關掉時：聊天語意校正開的三檔等於 main 加 `--no-reading-channel`（解碼器沒有讀音項、融合也沒有 ν 項，因為 ν 項這一片拿掉了）；其他五種設定等於 main 的出貨。
5. **摘要行標記**：CLI 與 replay 出貨（預設）時，**每一種設定**（聊天、書面、語意校正開或關）的標記最後都加 `+rd+rx:<std-exempt.tsv 的 SHA-256 前 8 碼>`；`+rc` 不再出現（讀音通道項拿掉了）。驗標記的工具一起改：`tools/triage.py`、`tools/kpi.py`、`tools/bench.py`（`colloq_tag` 與其他格子的標記）依版本決定：舊版照舊（聊天常談有 `+rc`）、這一版起每一格尾端加 `+rd+rx`，連同它們的測試（`tools/test_triage.py`、`tools/test_kpi.py`、`tools/test_bench.py`）；`VERSIONS2` 新版那一列等發版再加。
   - **golden 測試**（`cli/tests/golden.rs` 與 Python 參考 `lm_eval.py` 逐位元組一致）：Python 不實作讀音項；golden 的比對改帶 `--no-reading-decode`（維持和 Python 一致），另外加一份只有 Rust 的出貨預設釘值（dev302、打字測驗、錯字回報，聊天與書面：摘要行＋`top1_sha256`）。錯字回報列的流程（`make test-golden`、補 golden 行）改成兩份都要更新，寫進 `CONTRIBUTING.md` 與維護者本機規則（main 改）。
6. **不變**：守門、常談、會意、預測列、詞包的規則；`reading.rs` 的算法與常數；評測集合。學習只多一條（第 1 點：學習抬起的詞不加讀音項）。模糊音開時讀音項也套在容錯讀音的字上（`lm.rs` 的容錯來源）：沒量過，§2 的 GCP 工作在公開集合帶 `--fuzzy` 只報。`Engine::total_score` 不含讀音項（`engine.rs` 說它等於單段最高解碼分數的註解與相關測試改成只在 `--no-reading-decode` 時成立）。

## 2. 怎麼看到它有效

- **評測 CLI 等於確認過的工具**：同一次 GCP 工作、同一種 CPU，重跑 `b90adc96` 的 CLI（R1g 的 60 條 `e` 指令與 `base` 指令）和新的 CLI：
  - 出貨方向：新 CLI 預設（不給讀音旗標）的 rowstats 等於 `b90adc96` 的 `e` 臂，8 種設定 × 各自的集合（R1g 的 60 跑同一組）全部逐位元組相同；
  - 關閉方向：新 CLI `--no-reading-decode` 等於 `b90adc96` 的 `base` 臂，聊天語意校正開的三檔另外對 `base` 加 `--no-reading-channel`（每種設定的參照指令在腳本裡逐一寫出）；
  - 只報：公開集合（dev302、打字測驗、錯字回報）帶 `--fuzzy` 的出貨對關閉。
- **引擎等於評測 CLI**（在同一次 GCP 工作）：replay 的出貨預設與新 CLI 的出貨預設，在下列每一組（集合，設定）的 `top1_sha256` 相同；每一組先斷言前提：新 CLI 預設的 `top1_sha256` 不等於 `--no-reading-decode` 的（讀音項在這組真的改了列），前提不成立就停：
  | 集合 | 設定 |
  |---|---|
  | wikitune-clean | 書面標準（有小模型）、書面關 |
  | cvtune-clean | 聊天標準（含常談）、聊天關 |
  另外照 `colloquial_replay_agrees_with_the_cli` 的寫法加一個 `cli/tests` 測試：走 `load_lm` 與真的資料資料夾（replay 就是這樣），用 §0 的兩句，`assert_ne!` 開與關的第一名不同、再斷言 replay 與 CLI 相同。
- **單元測試**：`reading.rs`、`lm.rs` 現有的讀音項測試改成走共用的建表函式；新增：
  - 出貨模型（走 `load_lm`）：§0 的「以敵偽人員的名字命名」書面標準與書面關，`set_reading_decode(false)` 時是「以狄為…」、預設時全對；「這個屬叫散白蟻屬」四種設定，關時第一名是「數」、開時是「屬」；錯字回報第 81 列「長歪了」聊天標準預設時對（ν 項拿掉的回歸檢查，前提改成和 main 的釘值比：main 是錯的）；「搞完這波」四種設定都對（不退步）；
  - 小詞庫：學習抬起的單字（罕用讀音）在讀音項下照樣勝出；拿掉第 1 點的條件時這個測試要失敗；
  - 缺 `std-exempt.tsv` 時 `load_lm` 回錯；`set_reading_decode(false)` 時和沒有讀音項的解碼相同。
- **選字的變化表**：`tools/evalstats.py compare --net`（dev302、打字測驗、錯字回報，聊天與書面），用 GCP 工作的 rowstats 產生，附在 PR。
- **真引擎重播與速度、記憶體**：`shanjie-replay`（dev302、打字測驗、錯字回報，兩種設定）對 main 報送出文字與改選次數的逐列變化（在 GCP 工作裡）；每鍵 p95 與常駐記憶體在 Mac 的閒置窗量（照「每版 KPI」），先量 main 的數字再量這一版，每鍵 p95 超過 16 ms，或常駐記憶體比 main 多 10 MiB 以上就停（§5）；`load_lm` 的時間（多讀一次 `mcbpmf-data.txt` 建表）也量，比 main 多 1 秒以上就停。
- **實機**（佔鍵盤 ≤ 15 秒）：繁中虛擬機（`tart run --no-audio`，標準檔）：書面設定（`defaults write` 把 `profileMode` 設成 formal）句首打「以敵偽人員的名字命名」的注音要整句對（v0.6.1 是「以狄為」）；聊天設定打「長歪了」要對（v0.6.1 錯，ν 項拿掉的回歸）；語意校正關（`rerank` 設成 false）書面再打「以敵偽…」一次照樣對。三次都對才算過。

## 3. 驗收

- §2 的逐位元組比對全部相同（出貨＝`b90adc96` 的 `e` 臂；關閉＝`base` 臂，聊天語意校正開另加 `--no-reading-channel`）；引擎與 CLI 的 `top1_sha256` 兩兩相同。
- 單元測試與改到的測試檔在 Mac 通過，完整 `make test-rust` 在 188；`make test-python`、`make test-golden`、`make test-swift`；`make bundle` 之後 `check-app.sh` 通過、Resources 裡有 `std-exempt.tsv`；用新 CLI 在一個公開集合跑 `triage.py` 的聊天、書面、語意校正關三種設定都被接受。
- 重播沒有一列由對變錯，或有的話逐列列出、交維護者看；速度與記憶體在 §5 的門檻內。
- 實機看過（§2 的三次都對）。
- 同一件事寫在好幾處，同一個 PR 一起改：`docs/contracts/reading-decode.md`（標「被修訂二取代」）、`reading-channel.md`（ν 項拿掉）、`docs/contracts/s3a.md`（按鍵規則表與描述 ν 項的段落）、`LICENSES/data.md`、`CONTRIBUTING.md`（錯字回報列的 golden 流程）、`docs/PLAN.md`、`docs/iteration-plan.md`、`CLAUDE.md` 指到的「目前的順序」（維護者本機那份，由 main 改）、README、網站（`site/`）的模型與功能段、`core/include/shanjie.h`（只改註解，若有提到讀音通道）。合併前 grep「讀音通道」「reading channel」「reading_channel」「+rc」，沒有一句仍說它在出貨。

## 4. 範圍外

- 讀音項的參數（λ、c）或豁免清單的擴大：另開探索。
- 讀音計數 B（把 P(讀音｜字) 估準）：研究線的下一片。
- 殼與設定介面：不加開關。

## 5. 停止條件

- 逐位元組比對有任何一列不同，或引擎與 CLI 的 `top1_sha256` 不同：停下報告。
- §0 兩句在出貨引擎的前提或效果不成立、第 81 列預設時錯，或 §2 的比對組有任何一組「開關兩邊 `top1_sha256` 相同」：停下報告。
- 每鍵 p95 超過 16 ms 或記憶體多 10 MiB 以上：停下報數字。
- 重播有改壞的列：列出交維護者決定。
- 為了通過需要改 λ、c、豁免清單或算法：停下。

## 6. 檔案

- **分支**：`feat/reading-decode` 從 main 開，先合進 `exp/r1-reading-decode`（`f6e28941`：`reading.rs`、`--reading-decode`／`--reading-exempt`、R1 的工具與研究紀錄一起進 main；merge commit），再在上面做 §1 的改動。GCP 工作之前先在 Mac 量 dev302 兩種設定：合併後、改動前不給讀音旗標的 CLI 輸出，和 `b90adc96` 的 `base` 逐位元組相同（main 在 R1 分支之後若改到選字，這裡就會不同，不同就停下）。
- **會變的既有釘值**：`cli/tests/colloquial.rs`（`+rc` 與第 81 列的 [2, 0, 0]）、`core/tests/engine_colloquial.rs`、`core/tests/engine_english.rs`（`set_reading_channel`）、`cli/tests/replay.rs` 的讀音通道測試、`core/src/ffi.rs` 測試自己建的資料資料夾（要放 `std-exempt.tsv`）。規則：每一個改掉的釘值，PR 說明都寫出是讀音項還是 ν 項拿掉造成的，說不出原因就停。

- `core/src/reading.rs`、`core/src/lm.rs`、`core/src/engine.rs`（載入、解碼、拿掉 ν 項與它的開關）、`core/src/colloquial.rs`（ν 項）、`core/src/tinylm.rs`（若 ν 項在這裡）。
- `cli/src/main.rs`、`cli/src/replay.rs`、`cli/src/bin/replay.rs`（旗標與標記）；`cli/tests/golden.rs`（帶 `--no-reading-decode`＋出貨釘值）、`cli/tests/reading_decode.rs`、`cli/tests/replay.rs`。
- `tools/triage.py`、`tools/kpi.py`、`tools/bench.py`（標記），`tools/test_triage.py`、`tools/test_kpi.py`、`tools/test_bench.py`、`tools/test_std_exempt.py`（新）、`Makefile` 與 CI 的 Python 測試清單。
- `scripts/build-app.sh`、`scripts/check-app.sh`、`core/include/shanjie.h`（必要檔與錯誤說明）、`macos/Tests/ShanjieKitTests/` 的檔案清單。
- `data/lexicon/std-exempt.tsv`（原樣從實驗資料夾搬）、`tools/std_exempt.py`（從實驗資料夾搬）。
- 測試：`core/tests/`、`cli/tests/reading_decode.rs` 等改到的檔。
- 文件：§3 列的。

## 7. 預算與回滾

- executor 實作 1 次、修正 1 次；GCP 一次工作（舊 CLI 70 跑：60 跑加上聊天語意校正開三檔的 `base --no-reading-channel` 10 跑；新 CLI 出貨與關閉各 60 跑；公開集合的 `--fuzzy`；replay 的引擎比對與重播；≤ 1.5 小時，腳本先審）；188 完整測試一次；速度與記憶體在 Mac 的閒置窗量一次（main 與這一版各一次）。
- 回滾：整個 PR revert；資料檔是新增，舊版不讀。

## 8. 審查紀錄

- plan-verifier 第一次 REVISE（6 項，都照改）：(1) 只證明 CLI 等於工具、沒證明引擎 → 共用建表函式、引擎與 CLI 的 `top1_sha256` 比對、學習抬起的詞不加讀音項並加測試；(2) 關閉方向在聊天語意校正開時應對 main 加 `--no-reading-channel` → 改；(3) `triage.py`、`kpi.py`、`bench.py` 驗 `+rc` → 列進範圍；(4) golden 與 Python 一致 → golden 帶 `--no-reading-decode`、另加 Rust 出貨釘值；(5) App 打包、殼的測試、`shanjie.h` → 列進範圍，`load_lm` 讀、缺檔回錯；(6) 「變快」驗不出差別 → 量了（四種設定本來就對），改用第 81 列「長歪了」。非阻擋項也一起改（參照臂在同一次 GCP 工作重跑、檔案原樣搬、`test_std_exempt.py`、`s3a.md`、模糊音只報、`total_score` 註解、速度記憶體的機器與基準、`evalstats` 表）。
- plan-verifier 第二次 REVISE（2 項 P1，照改）：(1) 第 81 列在 v0.6.1 錯是 ν 項造成的，不是讀音項的效果 → 量了 main 自己寫的公開句子，「以敵偽人員的名字命名」（書面）與「這個屬叫散白蟻屬」（四種設定）由讀音項單獨改第一名，當效果斷言與實機；第 81 列改當 ν 項拿掉的回歸；(2) 引擎對 CLI 的比對可能是空的 → 加書面標準、每組先斷言開關兩邊不同、`cli/tests` 走 `load_lm`。非阻擋項照改：檔案逐位元組原樣搬、測試只比資料列且 CI 缺 zip 跳過、`+rd+rx` 每一格都加、建表函式的參數、GCP 前比對 `b90adc96` base、跑數照實、會變的釘值清單與說明規則、`load_lm` 時間。接著一次收尾審查。
