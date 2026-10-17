# 契約：書面語意校正強度在三元解碼器上重選（rerank-formal，只改書面融合參數）

狀態：READY（2026-10-17；修訂一：主判讀換成沒看過的 wikijudge、指令與檔案路徑寫死）。語意校正三片的第一片（維護者 2026-10-17 選擇題：「先重選書面強度，再依序接 L 重判、listwise」）。後兩片另寫契約。標〔推論〕的沒有量過。

## 1. 起因與依據

- 出貨的 `tinylm-v5` 書面三檔（標準 α 1、τ 1、δ 3；保守 1、1、4；積極＝標準）是 2026-10-15 在 v0.4.4 的 n-gram 上選的：那時書面還沒有三元表，選值規則是「改壞 1 句 ≈ 改對 10 句」（`--weight 10`）。之後兩件事變了：v0.4.7 起書面解碼用三元表（`sj-cx-1-7.7m`，書面有表時 KN 關），候選與分數都不同；方法論 2026-10-12 起語意校正一律 1:1 計分、要預設開另外要求改壞率 ≤ 0.5%。
- E2 修訂七在現在的書面解碼器上只報過一行（研究紀錄 2026-10-17）：同一份分數，1:1＋上限選出 0.5、2、0.25，對出貨 1、1、3 在判讀五集串接是改對 169／改壞 134、淨 +35、p 0.051；那時沒有接 discordtune，也不是判讀。
- Fable 的分析（研究紀錄同日）把這一片排第一：最便宜（Windows 機器半天、不用 Colab），而且產出的「現行解碼器的候選與 tinylm-v5 分數檔」是後兩片的基準。
- 聊天的值不動：聊天在 tinylm-v5 時已經是 1:1＋上限選的（chatclause 調參期），解碼器的聊天設定之後沒有變。

## 2. 要做到什麼、怎麼看到

- 交付：書面三檔重選的值；判讀過才匯出新檔 `tinylm-v6.sjtl`（模型權重、聊天參數與 tinylm-v5 逐位元組相同，只有書面那幾格不同），Release `tinylm-v6` 要維護者同意；`data/tinylm.sjtl.sha256` 換成新檔；研究紀錄、`docs/data-files.md`、`LICENSES/data.md`。〔名稱：E2 把讀音條件模型暫稱 v6，那是去風險的標籤，沒有發佈也結案了，這裡用掉 v6；研究紀錄註明。〕
- 判讀沒過：不匯出、不發佈，出貨的值不變，結果寫進研究紀錄（這也是一個結論）。
- 誰看、怎麼看：main 在 Windows 機器跑選值與判讀；fresh verifier 跑保留集一次並重跑抽查；維護者看表與實機（§4.6）。

## 3. 做法（沿用 `docs/contracts/rerank-strength.md` §4 與 `docs/contracts/model-v6.md` §2.7 的同一套工具，只改下面幾點）

1. **候選與分數**：用目前出貨的設定（`data/*.sha256` 釘的 model-v6、classes-v5、kn-v1、trigram `sj-cx-1-7.7m`，名單層常開，不開詞包，`--context`），書面設定，以 Rust CLI（`shanjie-eval … --dump … --tinylm data/lm/tinylm.sjtl --tinylm-scores …`，tinylm-v5 的權重）產 dump 與分數檔。集合：選值 cvtune、wikitune；**新判讀集 wikijudge**（下一段）；判讀五集 dev302、typing76、user-reported（當下全部列）、lytune、wikitypo；私人守門 discordtune（只由 main 跑、只帶回數字）。各檔的 SHA-256 記進研究紀錄。聊天設定不重產。
   - **wikijudge**（修訂一）：`experiments/s2/build_tune.py` 加 `--wiki-range LO HI` 與 `--wiki-name NAME`（預設 300000 305000 wikitune，預設輸出逐位元組不變，單元測試斷言），用 `--part wiki --wiki-range 310000 315000 --wiki-name wikijudge` 建（同規則、同種子、同 3,000 列上限、同 `tools/readings.py`）。第 310,000–314,999 篇沒有任何集合用過（計數前 200,000 篇、wikitune 300,000–304,999、數字語境的判讀集 305,000–309,999）。檔案不進 repo，雜湊記進研究紀錄。Windows 機器或 Colab 建。
2. **錨點**：`OUT_SHIP/rowstats/SHIP.formal.dev302.new.txt`（§3.3 第一個指令）要和 CLI `shanjie-eval --lm data/lm/bigram.sjlm --tinylm data/lm/tinylm.sjtl --profile formal --dev 302 --context --rowstats F`（tinylm-v5 的書面標準）逐位元組相同（`cmp F …` 的結束碼）；不同就停（§5）。
3. **選值與基準**（`experiments/e2-chateval/judge.py`，只讀分數檔；每個指令各用自己的 `--out`，檔名不會互蓋；選完凍結）。`SETS`＝`cvtune wikitune wikijudge dev302 typing76 user-reported lytune wikitypo discordtune`：
   - 出貨基準：`judge.py --scores <書面分數檔> --sets SETS --setting formal --model RF --fixed SHIP 1 1 3 --out OUT_SHIP`。
   - 標準：`judge.py --scores <書面分數檔> --sets SETS --setting formal --model RF --select STD --tuning cvtune wikitune --weight 1 --cap 0.005 --out OUT_STD`。
   - 保守：同上，`--select CONS --cap 0.001 --out OUT_CONS`；積極：`--select AGGR --cap 0.02 --out OUT_AGGR`。
   - 某一檔在上限內選不出任何格（`judge.py` 丟出 ValueError）：那一檔記為「沒有可選的值」，標準選不出就結案、不匯出。
   - 格點用 `judge.py` 現行的格點；選到格點邊界就照記，不擴大格點（擴大要另寫修訂）。
4. **判讀**（只用沒參與選值的集合；照 `docs/contracts/derisk-1015.md` §2 的定義，判讀五集與 discordtune 依 §2 的順序串接、列號重編）：
   - **事先看過的資料**（修訂一）：E2 修訂七已經在同一個解碼器、同一組判讀五集上量過「1:1＋上限選出的 0.5、2、0.25 對出貨 1、1、3」：改對 169／改壞 134、淨 +35、p 0.051（研究紀錄 2026-10-17）。所以判讀五集不再當決定性的證據，只當守門；決定性的判讀換成沒有人看過的 wikijudge。
   - **主判讀**：wikijudge，BASE＝`OUT_SHIP/rowstats/SHIP.formal.wikijudge.new.txt`、CAND＝`OUT_STD/rowstats/STD.formal.wikijudge.new.txt`，`tools/evalstats.py compare BASE CAND --net`：**顯著為正（a＝0.05）**。
   - **守門**（都要過）：(1) 判讀五集＋discordtune 依 `derisk-1015.md` §2 的順序（dev302、typing76、user-reported、lytune、wikitypo，最後 discordtune）用 `experiments/derisk-1015/e2/judge_e2.py` 的 `concat_rowstats` 各自串成 BASE（`OUT_SHIP/…/SHIP.formal.<集合>.new.txt`）與 CAND（`OUT_STD/…/STD.formal.<集合>.new.txt`），不顯著為負；(2) lytune 單獨（同樣的 BASE、CAND 路徑）不顯著為負；(3) 改壞率：CAND 那次的 `OUT_STD/rowstats/STD.formal.<集合>.base.txt`（只用 n-gram 第一名）對 `.new.txt`，wikijudge＋判讀五集＋discordtune 依上面的順序串接，改壞 ÷ 列數 ≤ 0.5%。
   - 只報：判讀五集不含 discordtune 的串接（看過的資料）、cvtune、wikitune（選值集）。
   - 新的標準和出貨相同（選值選回 1、1、3）就直接結案，不匯出。
   - **保守、積極**照 rerank-strength §4.3 的門檻（積極：任一判讀集合顯著為負或淨值低於標準就寫成標準值；保守：每一格的改壞數都 ≤ 標準才留，否則寫成標準值）。
   - cvtune、wikitune 是選值集，只報不判。只報：D2c 拆解的書面三類列數（`d2c_split.py`）、每種集合的改對／改壞。
5. **匯出**（判讀過才做）：`export_sjtl.py --type int8 --ngram-model <model-v6> --kn-side <kn-v1> --chat 0.3 2 0.25 --formal <新標準> --levels 1 2 3 0.3 2 0.25 <新保守> <新積極>`，`tinylm-M.npz` 用 tinylm-v5 那一份（`32308122…`）。核對：新檔和 tinylm-v5 只在書面那幾格不同（照 model-v6 §2.7 的切片核對，位移與欄位寫進研究紀錄）。
6. **一致性**（照 rerank-strength §4.4）：新檔在 dev302 三檔 × 書面，CLI 的 rowstats 和 `judge.py` 最後一次的 rowstats `cmp` 都是 0；聊天三檔對 tinylm-v5 的 CLI rowstats 也 `cmp` 為 0（證明聊天沒變）。

## 4. 驗收

1. 錨點（§3.2）`cmp` 為 0。
2. 選值與判讀的表進研究紀錄：三檔的值、選值集改對／改壞、主判讀那一行 `evalstats compare --net`、守門各行、保守／積極的處置。
3. 判讀過時：§3.5 的切片核對、§3.6 的一致性都過；`data/tinylm.sjtl.sha256` 換新、`docs/data-files.md` 的表、`LICENSES/data.md`（同 tinylm-v5 那一列的來源與授權）、PLAN 一行；CI 下載新檔（Release 建好之後）全綠。
4. **保留集**（fresh verifier，只跑一次，照 `CLAUDE.md`）：書面三檔，新檔對只用 n-gram、對 tinylm-v5 各一行；聊天不跑（沒變）。記進 `eval/README.md` 的使用紀錄（shard-1 第 2 次）。
5. `make test-rust`（Windows 機器的 WSL）、`make test-golden`、`make test-python` 全過；/code-review＋fresh verifier。
6. **實機**（維護者，`imeshot` ≤ 15 秒）：書面設定（截圖探測視窗就是書面），打一句主判讀裡「新值改對、出貨值錯」的公開列（從 dev302、打字測驗、錯字回報裡挑，研究紀錄寫是哪一列），裝新檔前後各拍一次。

## 5. 停止條件

- 錨點不符；切片核對發現權重或聊天參數有任何位元組不同；§3.6 任一 `cmp` 不是 0。
- 主判讀沒過、或守門有一條不過 → 不匯出、照記結論（不是錯誤，是結果）。
- 保留集任一書面檔對 tinylm-v5 顯著為負 → 不發佈，回報維護者。
- 預算：Windows 機器 ≤ 1 天（含建 wikijudge）、Colab ≤ 1 單元（只在 Windows 機器沒有維基 dump 時用來建 wikijudge）；discordtune 這一片讀一次；chatclause 不用。

## 6. 範圍外

聊天的值；模型權重（L、XL 是第二片，listwise 是第三片）；格點本身的改變；重排的候選數；讀音或前文長度（E2 已結案）。

## 7. 限制

- Mac 不跑打分與大量評測（Windows 機器的 WSL）；私人集合只帶回數字、逐列檔用完刪；保留集只由 fresh verifier 跑。
- Release 建立、推 tag、網站部署都要維護者當下同意。
- executor（若有）不可以：push、開 PR、讀保留集或 repo 外的私人資料、啟動 App、碰 `~/Library` 與鑰匙圈、連網。

## 8. 修訂二（2026-10-17，判讀之後；維護者選擇題「傾向 1」）

**起因**：§3.4 的判讀（研究紀錄同日）標準檔 0.5、2、0.25 主判讀過、守門 3 不過（改壞率 1.72%），依 §5 不匯出。保守檔 0.7、1、1.5（照 §3.3 的保守規則選出）四項全過：wikijudge 對出貨 +20／−0、五集＋私人集 +56／−13、lytune +27／−4、改壞率 0.14%。維護者選：保守那格當新的書面標準，用沒看過的資料再確認一次。**這是看過判讀數字之後改的規則**，所以下面的確認用全新的資料，門檻在跑之前寫死。

**新的書面三檔（候選）**：標準＝0.7、1、1.5（固定，不重選）；積極＝標準（同 tinylm-v5 的做法；這次選出的積極檔守門不過）；保守＝出貨的 1、1、4，前提是它在 wikijudge、wikijudge2、dev302、typing76、user-reported、lytune、wikitypo 每一個集合的改壞數（對只用 n-gram）都 ≤ 新標準，否則保守＝新標準。私人集不在這個比較裡（§5：這一片只讀一次，已讀過、逐列檔已刪）。

**確認資料 wikijudge2**：`build_tune.py --part wiki --wiki-range 315000 320000 --wiki-name wikijudge2`（同一套做法；第 315,000–319,999 篇沒有任何集合用過），用 §3.1 的同一個 CLI 與模型產分數檔 `RF.formal.wikijudge2.jsonl`（和其他集合的分數檔放同一個資料夾 `<分數>`）。

**這次唯一的 judge 指令**（自己的輸出資料夾 `OUT_R2`）：`judge.py --scores <分數> --sets wikijudge wikijudge2 dev302 typing76 user-reported lytune wikitypo --setting formal --model RF --fixed SHIP 1 1 3 --fixed NEW 0.7 1 1.5 --fixed C114 1 1 4 --out OUT_R2`。下面所有數字都只從 `OUT_R2/rowstats/<SHIP|NEW|C114>.formal.<集合>.{base,new}.txt` 算（`evalstats compare --net` 的改對、改壞欄）。

**確認門檻（都要過，跑之前寫死）**：
1. wikijudge2 上 NEW 對 SHIP（`OUT_R2/rowstats/SHIP.formal.wikijudge2.new.txt` 對 `OUT_R2/rowstats/NEW.formal.wikijudge2.new.txt`，`evalstats compare --net`）顯著為正（a＝0.05）。
2. wikijudge2 上 NEW 的改壞率（`OUT_R2/rowstats/NEW.formal.wikijudge2.base.txt` 對 `.new.txt` 的改壞 ÷ 列數）≤ 0.5%。
3. 已量過的守門照 §3.4 的數字（不重跑）。
4. 保守檔：每個集合 C114 的改壞數（`C114.formal.<集合>.base.txt` 對 `.new.txt`）和 NEW 的（同樣算法）並列成表進研究紀錄，照上面的規則決定保守＝1、1、4 或＝標準。

**之後**：確認過才照 §3.5 匯出（`--formal 0.7 1 1.5`，`--levels` 的書面三格照上面的三檔）、§3.6 一致性（這次的比對對象：標準與積極 → `OUT_R2/rowstats/NEW.formal.dev302.new.txt`；保守留 1、1、4 → `OUT_R2/rowstats/C114.formal.dev302.new.txt`，寫成標準 → 同 NEW 那一份；聊天三檔照 §3.6 對 tinylm-v5）、§4.4 保留集（fresh verifier，書面三檔對只用 n-gram 與對 tinylm-v5 各一行，shard-1 第 2 次）、§4.6 實機；Release 要維護者同意。確認沒過 → 結案、不匯出，研究紀錄照記。

**預算**：Windows 機器 1 小時內（建 wikijudge2 約 2 分鐘、單一集合評分、判讀）；不用 Colab。

## 9. 修訂三（2026-10-17，維護者選擇題「守 0.5% 上限，找改對最多的那格」；取代 §8 的候選）

**起因**：維護者要的是改對的總量更高（保守檔在 wikijudge 對出貨只 +20），但預設檔仍守改壞率 ≤ 0.5%。標準檔失敗是因為選值集上的 0.5% 上限沒有轉移到其他來源；所以在選值時收緊上限，找一格介於標準與保守之間、在其他來源也守得住 0.5% 的值。§8 的確認資料 wikijudge2 照用（建好、還沒有人看過它的判讀）。

**候選**（只用選值集 cvtune＋wikitune，1:1）：`judge.py --scores <分數> --sets cvtune wikitune wikijudge dev302 typing76 user-reported lytune wikitypo --setting formal --model RF --fixed SHIP 1 1 3 --select S<k> --tuning cvtune wikitune --weight 1 --cap <c> --out OUT_S<k>`，c ∈ {0.005, 0.004, 0.003, 0.002, 0.001}，k＝50、40、30、20、10。私人集不在這裡（§5 只讀一次）。

**篩選與挑選**（用已看過的資料，事先寫死；都只用公開集合，私人集照 §5 不再讀，和 §3.4 守門 3 含私人集不同）：每個候選三條都過才留下——(a) wikijudge＋判讀五集（依 §3.4 的順序 wikijudge、dev302、typing76、user-reported、lytune、wikitypo 串接，`OUT_S<k>/rowstats/S<k>.formal.<集合>.{base,new}.txt`）對只用 n-gram 的改壞率 ≤ 0.5%；(b) 判讀五集串接（同順序、不含 wikijudge），BASE＝`OUT_S<k>/rowstats/SHIP.formal.<集合>.new.txt` 串接、CAND＝`S<k>.formal.<集合>.new.txt` 串接，`evalstats compare --net` 不是顯著為負（不是「改壞 > 改對且 p < 0.05」）；(c) lytune 單獨，BASE＝`SHIP.formal.lytune.new.txt`、CAND＝`S<k>.formal.lytune.new.txt`，同樣不是顯著為負。留下的裡面取 (a) 那個串接上改對最多的那格（平手取上限較小的）。全部不過就沿用 §8（保守檔 0.7、1、1.5）。挑到的那格記為 PICK。

**確認（wikijudge2，沒看過的資料）**：唯一的 judge 指令 `judge.py --scores <分數> --sets wikijudge wikijudge2 dev302 typing76 user-reported lytune wikitypo --setting formal --model RF --fixed SHIP 1 1 3 --fixed PICK <α τ δ> --fixed CONS 0.7 1 1.5 --fixed C114 1 1 4 --out OUT_R3`。門檻（都要過）：
1. wikijudge2 上 PICK 對 SHIP（`OUT_R3/rowstats/SHIP.formal.wikijudge2.new.txt` 對 `PICK.formal.wikijudge2.new.txt`，`evalstats compare --net`）顯著為正（a＝0.05）。
2. wikijudge2 上 PICK 的改壞率（`PICK.formal.wikijudge2.base.txt` 對 `.new.txt`）≤ 0.5%。

PICK 不過時：CONS 照 §8 的兩條門檻（換成 `CONS.formal.wikijudge2.*`）判，過了就用 CONS；也不過就結案、不匯出。PICK 就是 CONS 時只判一次。

**三檔**：標準＝通過的那格；積極＝標準；保守＝1、1、4，前提是在 wikijudge、wikijudge2、dev302、typing76、user-reported、lytune、wikitypo 每個集合的改壞數（對只用 n-gram，`C114.formal.<集合>.*`）都 ≤ 標準的，否則保守＝標準。§3.6 的比對對象：標準與積極 → `OUT_R3/rowstats/<標準的標籤>.formal.dev302.new.txt`；保守 → `C114.formal.dev302.new.txt`（寫成標準時同標準）。其餘照 §8「之後」（匯出、一致性、保留集、實機、Release 要維護者同意）。

**預算**：Windows 機器 1 小時內；不用 Colab。
