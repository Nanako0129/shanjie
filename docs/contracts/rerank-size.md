# 契約：語意校正換大一號的模型（rerank-size，L 與 XL 在現在的解碼器上重判）

狀態：**結案，沒有候選過關，不匯出**（2026-10-17：Holm m＝5 全部不過，L188 書面沒有標準值、XL 兩種設定的改壞率都超過 0.5%、LCOLAB 兩種設定都沒過第 1 條；出貨照舊 `sj-sr-6-14m`；數字在研究紀錄同日一節）。READY（2026-10-17，plan-verifier 第 2 次，以 §9 的推薦選項為前提；修訂一：選值與判讀的指令、路徑寫死，多重比較改用 Holm）。語意校正三片的第二片（維護者 2026-10-17 選擇題：「先重選書面強度，再依序接 L 重判、listwise」；同日「L 和 XL 都判」）。第一片是 `docs/contracts/rerank-formal.md`（已出貨成 `sj-sr-6-14m`）。§9 的三個問題要維護者先決定，決定前只做 §3.1–§3.3 的訓練、匯出與公開集合打分。標〔推論〕的沒有量過。

## 1. 起因與依據

- 出貨的會意（`sj-sr-6-14m`）是 S7a 的 M（1,440 萬參數）。L、XL 上一次比是 S7b／S7e（研究紀錄 2026-10-11）：那時用「改對 − 10 × 改壞」選值、書面還沒有三元表、候選來自 model-v5，三個模型在維護者的聊天上都過不了 10:1，結論是「模型變大沒有幫助」。之後計分改成 1:1＋改壞率上限（方法論 2026-10-12），解碼器換了兩次（model-v6＋KN、書面三元表），所以舊結論不能直接沿用。
- Fable 的設計分析（研究紀錄 2026-10-17）是這份契約的底稿；決定點列在 §9。

## 2. 要做到什麼、怎麼看到

- 交付：L、XL 各自在現在的解碼器上照同一套規則選值、在沒看過的資料上判讀，寫成一張對 M（`sj-sr-6-14m`）的表；過關的那一個匯出成 int8 `.sjtl`，Release 照模型命名規則叫 `sj-sr-7-<規模>`（L 約 3,000 萬參數 → `sj-sr-7-30m`，XL → `sj-sr-7-65m`），建 Release 要維護者同意。
- 都沒過：不匯出、出貨不變，結論寫進研究紀錄（這也是結論）。
- 誰看、怎麼看：main 在 Colab 訓練、在 Windows 機器與 Colab 打分；fresh verifier 跑保留集一次；維護者看表、延遲與記憶體數字、實機。

## 3. 做法

### 3.1 模型（Colab G4）

- 三個候選：**L188**（S7b 在 Windows 機器上訓練的 `tinylm-L.npz`，研究紀錄記的權重 SHA-256 前 8 碼 `0c7c7c48`，用之前核對）、**L-colab**、**XL**（`experiments/s7a-tinylm/train.py --size L`、`--size XL`，預設種子與排程 8,836 步，訓練資料和出貨的 M 同一批：`ids-wiki.npy` `80ad1949…`、`ids-coll.npy` `996b9ffc…`、`vocab.txt` `85cd05b0…`）。
- L 訓兩份是為了量平台雜訊：同一個設定只換平台，兩個 L 對 M 的淨值差就是平台雜訊的一個量（只報，§3.5；決定靠 Holm 修正，不靠它）〔推論：S7f 的經驗是只換平台與種子，淨值也會差到幾十列〕。
- 最終 loss 對照 S7e 記的 L 3.886、XL 3.803，差超過 0.02 就停下來查〔推論的門檻〕。

### 3.2 匯出與打分（出貨的位元組就是打分的位元組）

- 每個候選匯出 int8：`export_sjtl.py --type int8 --ngram-model <model-v6> --kn-side <kn-v1>`，α、τ、δ 先填 `sj-sr-6-14m` 的值當佔位（打分不讀它們）。不做 f32 臂（S7b 量過 M、L 的 int8 對 fp32 都不顯著）。
- 檔案大小上限是核心的 `MAX_FILE`（`core/src/tinylm.rs`，64 MiB）。照長度公式 L 約 30.6 MB、XL 約 66.0 MB〔推論，匯出後量〕，XL 只剩約 1.1 MB；超過就是停止條件，不改 `MAX_FILE`。
- 候選與分數：和第一片 §3.1 同一套（`data/*.sha256` 釘的 model-v6、classes-v5、kn-v1、`sj-cx-1-7.7m`，名單層常開，不開詞包，`--context`），Rust CLI `shanjie-eval … --dump … --tinylm <檔> --tinylm-scores …`，**聊天與書面兩種設定都產**；M 用同一個 CLI commit 重打，不沿用第一片的檔。
- 錨點：M 的書面 dev302 rowstats（`sj-sr-6-14m`，書面標準）要和第一片 `OUT_R4/rowstats/S20.formal.dev302.new.txt` 逐位元組相同；聊天的要和 CLI 對 `sj-sr-6-14m` 的 rowstats 相同。不符就停。
- 集合：選值 cvtune、wikitune（書面）、chatclause（聊天，私人，只在 Windows 機器或 Colab 跑、只帶回數字）；判讀五集 dev302、typing76、user-reported（當下全部列）、lytune、wikitypo；守門 discordtune（只讀一次）；沒看過的判讀集見 §3.4。

### 3.3 選值（`experiments/e2-chateval/judge.py`，只讀分數檔；選完凍結）

- **分數檔**：每個模型 X ∈ {M, L188, LCOLAB, XL}、每種設定 s ∈ {formal, chat}、每個集合，CLI 的 `--tinylm-scores` 輸出改名成 `<分數>/X.s.<集合>.jsonl`（`judge.py` 一次只讀一個 `--model`）。
- **選值階段的集合**（不含沒看過的集合與 discordtune，這兩種只在 §3.5 的判讀那一次讀）：書面 `SEL_F`＝`cvtune wikitune wikijudge wikijudge2 dev302 typing76 user-reported lytune wikitypo`；聊天 `SEL_C`＝`chatclause dev302 typing76 user-reported lytune wikitypo`。
- **M 的基準**（篩選 (b)(c) 用，出貨值）：`judge.py --scores <分數> --sets SEL_F --setting formal --model M --fixed M 0.7 2 1 --out OUT_M_formal_SEL`；`judge.py --scores <分數> --sets SEL_C --setting chat --model M --fixed M 0.3 2 0.25 --out OUT_M_chat_SEL`。
- **候選**：X ∈ {L188, LCOLAB, XL}，上限 c ∈ {0.005, 0.004, 0.003, 0.002, 0.001}，k＝c × 10000（50、40、30、20、10）：
  - 書面：`judge.py --scores <分數> --sets SEL_F --setting formal --model X --select X_S<k> --tuning cvtune wikitune --weight 1 --cap c --out OUT_X_formal_S<k>`。
  - 聊天：`judge.py --scores <分數> --sets SEL_C --setting chat --model X --select X_S<k> --tuning chatclause --weight 1 --cap c --out OUT_X_chat_S<k>`。
- **篩選**（每個 X × s × k，三條都過才留；串接用 `experiments/derisk-1015/e2/judge_e2.py` 的 `concat_rowstats`，照下面的順序、列號重編）：
  - (a) 改壞率：A 集合依序串接 `OUT_X_s_S<k>/rowstats/X_S<k>.s.<集合>.base.txt` 與 `.new.txt`，改壞 ÷ 列數 ≤ 0.5%。A 集合：書面 `wikijudge wikijudge2 dev302 typing76 user-reported lytune wikitypo`（含 wikijudge2）；聊天 `chatclause dev302 typing76 user-reported lytune wikitypo`。
  - (b) 判讀五集（`dev302 typing76 user-reported lytune wikitypo`）串接：BASE＝`OUT_M_s_SEL/rowstats/M.s.<集合>.new.txt`，CAND＝`OUT_X_s_S<k>/rowstats/X_S<k>.s.<集合>.new.txt`，`tools/evalstats.py compare BASE CAND --net` 不是顯著為負（不是「改壞 > 改對而且 p < 0.05」）。
  - (c) lytune 單獨，同 (b) 的 BASE、CAND 路徑，同樣不是顯著為負。
- **挑選**：留下的格裡取 (a) 串接上改對最多的那格（平手取上限較小的），記為 X 在 s 的標準。五格都不過：X 在 s **沒有標準值**，X 不能出貨（§3.5 要兩種設定都過第 2、3 條）。
- **三檔**：標準＝挑到的那格；保守＝k＝10 那格，前提是它過了篩選，而且在 A 集合的每一個集合，改壞數（對只用 n-gram）都 ≤ 標準的，否則保守＝標準；積極＝標準。
- 只報：M 照同一套規則重選（`--model M --select M_S<k>`，`OUT_M_s_S<k>`），看同規則下的 M；出貨決定的基準仍是出貨值。

### 3.4 沒看過的判讀集

- 書面：**wikijudge3**＝`build_tune.py --part wiki --wiki-range 320000 325000 --wiki-name wikijudge3`（第 320,000–324,999 篇沒有任何集合用過：計數前 200,000 篇、wikitune 300,000–304,999、數字語境的判讀集 305,000–309,999、wikijudge 310,000–314,999、wikijudge2 315,000–319,999）。
- 聊天（§9 第 1 題選 A）：從 E2 調參池（2015-07～2026-07）用新的種子抽不在 chatclause 裡的約 3,000 列當 **chatjudge**，排除、讀音、校對、凍結規則照 `docs/contracts/e2-chat-eval.md`。校對要 main 讀維護者的私人句，**讀之前要維護者同意**；列檔只放 Windows 機器，用完退役。沒有 chatjudge 時，聊天沒有第 1 條可判（§3.5），聊天只做守門，結論標「探索性」。
- 沒看過的集合記為 U：書面 U＝wikijudge3，聊天 U＝chatjudge。

### 3.5 判讀與出貨決定（基準＝出貨的 `sj-sr-6-14m`：聊天 0.3、2、0.25，書面 0.7、2、1；同一批候選）

- **判讀那一次**（所有選值凍結之後才跑；discordtune 與 U 只在這裡讀）：`J_s`＝U 加上 `dev302 typing76 user-reported lytune wikitypo discordtune`。
  - M：`judge.py --scores <分數> --sets J_s --setting s --model M --fixed M <出貨值> --out OUT_M_s_J`。
  - 每個有標準值的 X：`judge.py --scores <分數> --sets J_s --setting s --model X --fixed X_STD <標準> --fixed X_CONS <保守> --out OUT_X_s_J`。
- 每個候選、每種設定三條：
  1. **主判讀**：BASE＝`OUT_M_s_J/rowstats/M.s.U.new.txt`，CAND＝`OUT_X_s_J/rowstats/X_STD.s.U.new.txt`，`evalstats compare --net` 的精確 McNemar p，**用 Holm 修正後**顯著為正（下一段）。
  2. **改壞率**：`OUT_X_s_J/rowstats/X_STD.s.<集合>.{base,new}.txt` 依 `J_s` 的順序串接，改壞 ÷ 列數 ≤ 0.5%。
  3. **不變差**：判讀五集＋discordtune 依 `J_s` 的順序（不含 U）串接，BASE 取 `OUT_M_s_J/…/M.s.<集合>.new.txt`、CAND 取 `OUT_X_s_J/…/X_STD.s.<集合>.new.txt`；lytune 單獨同樣的路徑；兩者都不是顯著為負。
- **多重比較**：第 1 條的檢定數 m＝實際跑了主判讀的（候選, 設定）組數（最多 3 × 2＝6；沒有 chatjudge 時最多 3）。Holm：把 m 個 p 由小到大排，第 i 個（從 1 起）要 < 0.05 ÷ (m − i + 1) 才算過，遇到第一個不過就停，後面的都算不過。研究紀錄寫出 m 與每個 p。只有「改對 > 改壞」的才可能算過。
- **平台雜訊**（只報）：L188 與 LCOLAB 在每種設定 U 上對 M 的淨值（改對 − 改壞）之差。
- **合併規則**（§9 第 3 題選 A）：一個檔同時給兩種設定用，所以兩種設定都要過第 2、3 條，至少一種過第 1 條；兩邊都只是「不差」就不換（換大模型有大小、延遲與記憶體的代價）。
- 候選之間：先過 §4 的硬門檻，再取兩種設定在 U 上對 M 的淨值合計最高的。L188 與 LCOLAB 一個過、一個不過：寫成「L 的效果在平台雜訊內」，不出貨 L。
- **保留集**：只對最後選中的那一個跑一次（fresh verifier，shard-1 下一次使用），兩種設定對 `sj-sr-6-14m` 各一行；它只防「在新資料上顯著變差」，顯著為負就不發佈。它擋不住單純運氣好的候選，那由上面的 Holm 處理。
- 事先寫下的期望（只當讀結果時的參考）：S7b 的 L 對 M 在 wikitune 多改對約 35–39 列、discordtune 聊天 −97；S7e 的 L、XL 在維護者的聊天上都沒比 M 好。結果若又是「公開集合好、聊天不好」，照方法論 2026-10-12 那一條讀。

## 4. 延遲、記憶體、大小（Mac 空閒、release 版，同一天同一場量 M 當基準，不引用舊數字）

- 工具：`shanjie-eval … --tinylm X.sjtl --dev 302 --context` 印的 `tinylm: rerank n= p50= p95= … rss … around the .sjtl load`；`core/tests/engine_rerank.rs` 的 `replay_dev302_parity_and_measurements`（`SHANJIE_TINYLM=…`）印按鍵路徑與重排的 p50、p95。只量這兩個，屬於 Mac 允許的少量量測。
- 硬門檻：按鍵路徑 p95 < 16 ms、而且比沒有模型時多 ≤ 1 ms（S7c-2a 的門檻；重排在主執行緒外）；重排 p95 ≤ 50 ms〔推論：打字每鍵約 150 ms，超過三分之一，「先出現 n-gram 的字再被換掉」會常見〕。
- 記憶體（§9 第 2 題選「不設，看數字再定」）：載入 `.sjtl` 前後的 RSS 增量只報，量完交維護者決定；原本推薦的 ≤ 150 MB 不當門檻。核心載入時把 int8 還原成 f32，增量大約和參數數成正比；Fable 的估計是 M 約 +71、L 約 +130、XL 約 +270 MB〔推論，這一片同場重量〕；照推薦值，實際上只有 L 有機會出貨，XL 只當研究數字。
- 大小：L 的 `.sjtl` 比 M 多約 16 MB、XL 多約 51 MB〔推論，匯出後量〕，Sparkle 更新是整包下載。只報、不設門檻。

## 5. 驗收

1. §3.1 雜湊與 loss、§3.2 錨點都對；每個候選的 `.sjtl` 大小與 SHA-256 進研究紀錄。
2. 研究紀錄有一張表：每個候選 × 每種設定的三檔值、選值集改對／改壞、主判讀那一行、守門各行、§4 的延遲與記憶體。
3. 有候選過關時：照第一片 §3.5 的做法匯出正式檔（張量和打分用的檔逐位元組相同，只有檔頭參數格不同）；一致性（dev302 × 兩設定 × 三檔，CLI rowstats 和 `judge.py` 的 `cmp` 為 0）；保留集（§3.5）；`make test-rust`（Windows 機器的 WSL）、`make test-golden`、`make test-python`、`make test-swift` 全過；/code-review＋fresh verifier；Release 要維護者同意；切換 PR 照 `docs/data-files.md` 的換版清單。
4. 實機（維護者，`imeshot` ≤ 15 秒）：兩種設定各打一句主判讀裡「候選改對、M 錯」的公開列，換檔前後各拍一次；另外在實機看一次記憶體（活動監視器的善解程序）。

## 6. 停止條件

- 雜湊不符、loss 偏離超過 0.02、錨點不符、XL 檔 > `MAX_FILE`、§4 硬門檻不過、chatjudge 的校正率 > 1%（校對改了超過 1% 的列，代表抽樣或過濾有問題）。
- 主判讀或守門沒過 → 不匯出，照記結論。
- 預算：Colab G4 ≤ 3 單元（訓練、匯出、必要時的公開集合打分）；Windows 機器 ≤ 2 天（4 個模型 × 2 種設定 × 約 11 個集合，XL 打分約慢 4.5 倍〔推論〕），超過就把公開集合搬到 Colab。Mac 只跑 §4 的量測與該級的測試。

## 7. 範圍外

改格點、改 `MAX_FILE`、改融合規則（listwise 是第三片）、f32 出貨、載入後保持 int8（另開核心片）、重訓 M、用 discordtune 選值、讀 2026-09 的訊息、一片出兩個檔。

## 8. 限制

- 這一片不碰核心與 App 的程式；有候選過關才有切換 PR（資料檔、雜湊、CI 下載）。
- 私人資料（chatclause、discordtune、chatjudge）只在 Windows 機器或 Colab 跑、只帶回數字；列檔不進 repo、不交給 subagent 讀。

## 9. 請維護者先決定

**決定（維護者 2026-10-17 17:40 選擇題）**：第 1 題選 A（抽 chatjudge，同意 main 讀並校對）；第 2 題選 B（不設記憶體門檻，量完把數字給維護者再定；§4 的記憶體改成只報）；第 3 題選 A（兩種設定都不差＋至少一種顯著好）。


1. **聊天的沒看過判讀集**：(A，推薦) 抽 chatjudge，main 讀並校對約 3,000 句維護者的私人句；(B) 不抽，聊天只做守門、結論標探索性。沒有 A，聊天這一側沒有決定性證據，而一個檔兩種設定共用。
2. **記憶體門檻**：(A，推薦) RSS 增量 ≤ 150 MB，實際上只有 L 有機會出貨；(B) 不設，看數字再定；(C) 另開核心片讓載入後保持 int8，XL 才有機會。
3. **合併規則**：(A，推薦) 兩種設定都不差＋至少一種顯著好就換；(B) 兩種都要顯著好。

## 10. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
| plan-verifier 第 1 次（2026-10-17，REVISE） | P2：篩選 (b)(c) 照第一片 §9 字面會變成「候選對自己在舊出貨值」，書面與聊天的基準不一致；(a) 含不含 wikijudge2 不清楚；五格都篩不過時沒有規則；「對 M」的 BASE 路徑沒寫。P2：用保留集當多重比較的防護和 §3.5 矛盾，保留集只擋顯著為負；檢定數算成 3，實際最多 6；平台雜訊沒有進決定規則 | FIX：§3.3 寫死 M 的基準指令、每個候選 × 設定 × 上限的指令與 `--out`、(a) 的集合清單（含 wikijudge2）、五格都不過＝沒有標準值、不能出貨；選值階段不讀 U 與 discordtune。§3.5 寫死判讀那一次的指令與 BASE／CAND 路徑；第 1 條改用 Holm（m＝實際跑的組數）；保留集只防顯著為負；平台雜訊只報 |
| plan-verifier 第 2 次（2026-10-17） | READY | — |
