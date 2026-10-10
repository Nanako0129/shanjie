# 常見專有名詞進一般詞庫（N1：名單、出處與授權）

使用者 2026-10-10：「常見廠商名和產品名需要納入詞庫」（起因：「酷澎」打成「酷朋」，酷澎不在詞庫）、「常見公司名、學校名、飲料店、餐廳也應該放進一般詞庫」。PLAN「常見廠商名與產品名」：放一般詞庫（使用者指定），寫契約前先查來源與授權。調查結果與依據在研究紀錄 2026-10-10「常見專有名詞的來源與授權」（這個分支新增）。

## 0. 要做到什麼、怎麼看到

- 這一片（N1）產生一份**一般詞庫層** `data/lexicon/names-add.tsv`（常開，不是選用詞包；使用者指定「一般詞庫」）與它的出處、處置檔、授權、評測集合，並量好品質。**這一片不載入它**：
  - 理由（量過的）：詞彙外的詞只拿到「詞條分數第 25 百分位 − 1.0」的分數，A2 詞包的兩字詞 98.7% 輸給常用字組合（研究紀錄 2026-10-10 A2 審計）。名單不進語料計數就上線，多半打不出來。
  - 上線放在 **model-v6**：A3 詞包、這份名單、KN 一起重算計數（名單進斷詞詞彙，和 model-v5 的 `--extra-lexicon` 同一套），golden 只重產一次。這一片的所有量測都只用評測選項 `--extra-overlay`，App 與核心的預設載入不變。
- 看到它有效的方式（model-v6 那一片）：「酷澎」與名單評測集合打得出來，一般集合不變壞。這一片先量「名單本身對不對、加進詞庫後會不會擠掉既有詞」。

## 1. 來源與授權

依據與網址都在研究紀錄 2026-10-10「常見專有名詞的來源與授權」；這裡只寫用法。

- **name-suggestion-index（NSI）**，BSD-3-Clause（資料同授權）。釘一個 commit（第 0 步記下）。類別固定為：`amenity/cafe`、`amenity/restaurant`、`amenity/fast_food`、`amenity/bank`、`amenity/fuel`、`amenity/pharmacy`、`shop/convenience`、`shop/supermarket`、`shop/department_store`、`shop/mall`、`shop/electronics`、`shop/mobile_phone`、`shop/clothes`、`shop/cosmetics`、`shop/bakery`、`shop/tea`、`shop/beverages`。
  - **哪些品牌算台灣**：`locationSet.include` 明列 `tw`（`001` 全球不算）；`locationSet.exclude` 含 `tw` 的不收。
  - **取名字的順序**（研究紀錄的樣本：台灣品牌多半只有 `name:zh`）：`name:zh-Hant`、`name:zh-TW`、`name:zh`、`brand:zh-Hant`、`brand:zh`、`name`、`brand`，取第一個「全部是漢字、而且沒有簡體專用字」的值（簡體專用字的判斷用 `experiments/s2/build_counts.py` 的 `is_simplified`）；`name:zh-Hans`、`name:ja`、`matchNames` 不用。
- **Wikidata（CC0）＋中文維基（CC BY-SA 4.0）**：
  - NSI 的 `brand:wikidata` 有中文維基 sitelink 時，**也**收該條目在 `variant=zh-tw` 下的顯示標題（去掉消歧義括號）；NSI 的名字與維基標題兩個都收，去重。
  - 中文維基分類（固定清單，各取分類內條目與一層子分類）：`Category:台灣手搖茶飲品牌`、`Category:台灣餐飲公司`、`Category:台灣公司`、`Category:台灣電子商務網站`、`Category:臺灣的大學`（第 0 步核對分類存在；不存在的寫進報告、不自動換名）。
  - **所有維基標題**（NSI 對應的條目、分類成員、學校簡稱的重定向）一律取 `variant=zh-tw` 的顯示標題；不用 Wikidata 的 `zh` 標籤。
  - 手動補：`data/lexicon/names-manual.tsv`（維護者 CC0），第一筆「酷澎」。
- **教育部統計處各級學校名錄**（政府資料開放授權條款第 1 版）：大專校院 `https://stats.moe.gov.tw/files/opendata/u1_new.csv`（資料集 6091，含 103 學年起各年）與一般高級中等學校 `https://stats.moe.gov.tw/files/school/115/high.csv`（資料集 6089，115 學年）；UTF-8 帶 BOM，取「學校名稱」欄，只取「學年度」是數字而且等於該檔最大學年度的列（第 0 步記學年度與雜湊；格式樣本在研究紀錄）。簡稱（政大、清大）只收中文維基有重定向到該校條目的。
- **不用**：OpenStreetMap POI、財政部稅籍與商工登記、商標公報、萌娘百科、部落格與網頁抓取（理由在研究紀錄）。
- **授權**：`names-add.tsv`、`names-sources.tsv`、`names.json`、`names-collisions.tsv` 以 CC BY-SA 4.0 散布（和 `overlay-add.tsv` 相同）；`names-manual.tsv` 與 `names-readings.tsv` 是維護者的 CC0。`LICENSES/data.md` 為這六個檔各列授權，CC BY-SA 的四個附上同樣的三方標示：分列 NSI 的 BSD-3 版權聲明全文、Wikipedia 貢獻者（CC BY-SA 4.0）、教育部統計處的標示（條款附件的標示文字＋機關、年份、資料集名稱與學年、https://data.gov.tw/license ），並寫明條款不含商標權（第 2.4 條）、詞庫只散布名稱字串。評測檢查集另加一列（§3 第 3 項）。

## 2. 建置（`tools/build_names.py`）

- **抓取**（main 執行一次，在 executor 的離線建置之前）：沿用 `build_acg_pack.Api` 的快取、限速（每秒 ≤ 1 個請求）、User-Agent 與 `maxlag=5`。只連：`zh.wikipedia.org/w/api.php`、`www.wikidata.org/w/api.php`、`raw.githubusercontent.com`（只限 NSI 那個 commit 的路徑）、`data.gov.tw` 與 `stats.moe.gov.tw`（名錄 CSV）。其他網址一律拒絕。上限：請求 3,000 個、快取 500 MB，超過就中止。快取放 `~/.cache/shanjie/sources/names/`，記錄 commit、revision ID、學年度。`--offline` 只讀快取，缺檔就中止。
- **過濾**（事先寫死）：
  - 只收全部是漢字、2–10 字的名字；含英文字母、數字、符號的名字整個不收（不截取漢字部分）。
  - 去掉公司後綴（股份有限公司、有限公司、公司、集團）只適用於分類來源；學校全名、NSI 名字不去字。
  - **去重**：字串只要出現在基底詞庫、`overlay-add.tsv`、`sandhi-add.tsv`、`data/packs/acg-add.tsv` 的**任何讀音**下就不收（用 `build_acg_pack.lexicon_words()` 加上 acg-add 的詞）。理由：核心依疊加層的字串封頂該詞所有讀音的詞條（`CappedLexicon::new`），收了會改到既有詞的分數；同一個（讀音, 詞）重複還會讓核心載入失敗（`OverlayDuplicate`）。建置最後檢查輸出和這四份檔的詞集合交集為空，不為空就中止；另外 `make test` 加一條測試讀已提交的檔，斷言同一件事，之後任何一邊改了檔，CI 都會擋。
- **和 A3 的順序**：A3（`feat/acg-a3`）會重建 `acg-add.tsv`。A3 先合併；這一片 rebase 到含 A3 的 main 之後，用新的 `acg-add.tsv` 離線重建、重跑 §3 的量測再合併。
- **讀音**：`tools/readings.py`（和評測集合同一套規則）；標 CHECK 的與多音字（樂、行、長、重、藏、都、會、種、朝）由 main 人工核對，結果寫進這一片自己的整名讀音表 `data/lexicon/names-readings.tsv`（名字、讀音；維護者 CC0），`build_names.py` 在 `readings.py` 之後套用、取代該名字的讀音，並檢查音節數等於字數。**這一片不改 `tools/reading_overrides.tsv`**（那張表會改到評測集合與 ACG 詞包的讀音，而且名字已去重、不會整個切成一個詞，整名覆寫在那裡不生效）。拼不出讀音的不收，列在報告裡。
- **分數**：用 `build_acg_pack.scores()`（各字數 2–10 的第 25 百分位，和詞包相同）。
- **同音衝突**：`build_names.py` 自己提供 `decode`（CLI 加 `--extra-overlay` 名單層）傳給 `build_acg_pack.detect_collisions`，**不修改** `tools/build_acg_pack.py`（那個檔歸 A3 片）。模型用 model-v5＋classes-v3、扣分 1.0。
  - 類型 (c)（名字把既有詞庫的詞擠下第一名）：不收（和 ACG 詞包的使用者規則相同）。
  - 類型 (a)（名單內同音）：處置檔 `data/lexicon/names-collisions.tsv` 是**人工維護的輸入**（格式與規則照 `acg-collisions.tsv`：讀音、保留的詞、排除的詞、理由），建置只讀它、不改它；沒處置的列寫進報告 `build/names/collisions.txt`。main 依「來源數多的優先」提建議，用選擇題請使用者確認後由 main 寫入。**全部處置完才合併**。
- **輸出**：`data/lexicon/names-add.tsv`（疊加層格式）、`names-sources.tsv`（每詞的來源、QID、revision 或學年度）、`names.json`（版本、來源統計、各過濾丟掉的數量、未處置的 (a) 數量），以及不進版控的報告 `build/names/collisions.txt`。

## 3. 驗收

1. **第 0 步**：各來源的筆數（NSI 各類別台灣品牌數、分類條目數、名錄筆數）、NSI commit、各頁 revision、名錄學年度、分類名稱核對。
2. **名單品質（main 抽樣）**：固定種子從名單抽 100 個，逐一看出處判斷是不是常見的公司、品牌、學校或產品名；至少 90 個是才算通過。依抽樣加過濾最多一輪，加了就換種子從新名單重抽 100 個（排除第一次抽到的），用第二次判斷。
3. **檢查集** `eval/dev/names/names.txt`（子資料夾，不放 `eval/dev/` 頂層，`--dev 302` 才不會變；和 `eval/dev/acg/` 同一種做法）：依來源分層抽 60 個名字（固定種子）加「酷澎」，讀音同 §2，`LICENSES/data.md` 另加一列（不是 CC0）。用 `--rows` 指定這個檔，model-v5 下加不加 `--extra-overlay` 的 top1，聊天與書面各一行（預期在 model-v6 之前改善有限，這一片只記錄）。
4. **一般集合**：dev302、打字測驗、錯字回報，兩種設定，加 `--extra-overlay` 名單層與不加，`tools/evalstats.py compare`：改壞必須是 0。cvtune、wikitune 也報。加檔前後各跑一次 `--dev 302`，兩種設定的 `top1_sha256` 都不變；`cli/tests/golden.rs` 不必重產就通過。
5. **`--extra-overlay FILE`**（只加在評測 CLI；`lm_eval.py` 不在這一片）：檔案的列併進詞庫，**也**併進封頂用的疊加層文字（和 `--packs` 同一條路），所以詞彙外的詞會扣 `UNSEEN_OVERLAY_PENALTY`；可以和 `--packs` 併用；缺檔時結束碼非 0 並印出路徑；沒給這個選項時輸出逐位元組不變。測試：
   - 用 CLI 同一條載入路徑（`load_with_packs` 加這個選項、再建 `CappedLexicon`）載入夾具列「酷澎」，斷言它在夾具讀音下的封頂分數等於「夾具分數 − 1.0」；把選項改成只併進詞庫、不併進封頂文字時，這條測試要以斷言失敗。
   - `--dump`：加了選項「酷澎」是第一名，不加時不是。〔更正，2026-10-11，PR #110 /code-review：原本寫「不加就沒有」，但兩個單字也拼得出「酷澎」，不加時它仍在候選裡，所以測試比名次（`cli/tests/golden.rs`）。〕
   - 給了選項時摘要行加 `+xo:<檔案 sha8>`（PR #110 /code-review 後加，沒給時輸出不變），基準與加名單層的兩行分得出來。
   - 缺檔時結束碼不是 0。
6. **衝突與重建**：這個分支對 `tools/reading_overrides.tsv` 的 diff 為空；建置報告列出未處置的 (a) 數量，必須是 0；同一份快取離線建兩次，`names-add.tsv`、`names-sources.tsv`、`names.json` 三個檔逐位元組相同；建置前後 `names-collisions.tsv` 逐位元組不變（單元測試）；這個分支對 `tools/build_acg_pack.py` 的 diff 為空；交集測試（§2）在 `acg-add.tsv` 加一列和名單相同的詞時以斷言失敗。
7. **大小**：`names-add.tsv` 的詞數與檔案大小；加進詞庫後的記憶體增加量（量法同 ACG A 片）。
8. **單元測試**（夾具由研究紀錄的真實樣本裁剪，不連網）：`locationSet` 的判定（`tw`、`001`、`exclude`）、NSI 取名字的順序（只有 `name:zh` 的台灣品牌照收、含簡體專用字的不收、日文 `name` 不收）、名錄只取最大學年度而且學年度是數字的列、`readings.py` 讀錯的名字（例如「朝陽科技大學」）照 `names-readings.tsv` 輸出、音節數不等於字數時中止、維基標題一律 zh-tw（簡體的分類成員標題輸出為 zh-tw）、後綴去除只用在分類來源、含英文的名字整個不收、四份檔任何讀音下的詞都不收、6 字詞的分數等於 `scores()[6]`、假的 Api 超過請求上限時中止、不在允許清單的網址被拒絕、`--offline` 缺快取時中止。
9. `make test`、CI 全綠；fresh verifier 確認第 2–6 項的數字（保留集不在這一片：這一片不改出貨行為）。

## 4. 前置條件、範圍外、停止條件、預算、限制、回滾

- **前置條件與順序**（main 在派工前做，派工 brief 附上輸出）：
  1. 把 model-v5 的 `bigram.sjlm` 與 classes-v3 的 `classes.sjc` 放進這個 worktree 的 `data/lm/`，`shasum -a 256 -c data/bigram.sjlm.sha256 data/classes.sjc.sha256` 兩個都要通過。
  2. 執行順序：main 寫研究紀錄那一節 → executor 寫 `build_names.py`、`--extra-overlay` 與單元測試（含 `Api` 的允許清單與上限、全用假資料）→ main 用 `build_names.py` 連網抓取一次（寫進快取）→ main 跑 `--offline` 建置、抽樣與評測。executor 不連網，也不等快取。
- **範圍外**：上線（model-v6）；語料計數；英文與中英混寫的名字；國中小與幼兒園名錄；稅籍與商標資料；店家是否仍在營業的核對；選單開關（一般詞庫，常開）。
- **停止條件**：第 2 項低於 90 個；一般集合有改壞，或 `--dev 302` 的 `top1_sha256` 變了；名單內同音衝突超過 50 組；記憶體增加超過 20 MiB；請求數或快取大小超過上限；某個來源的授權條款和研究紀錄那一節不同（例如 NSI 的 LICENSE 改了）。
- **預算**：executor 實作 1 次＋修正 1 次；抓取一次（main）。
- **限制**：executor 不連網（只用 `--offline` 與假資料）、不改 `tools/build_acg_pack.py`；不讀 `eval/holdout`、`~/side-project/shanjie-private`、學習檔、`~/Library`、鑰匙圈；不安裝、不啟動 App；不 push、不開 PR、不碰其他 worktree。
- **回滾**：revert；這一片不載入名單，使用者端不受影響。

## 5. 審查紀錄

- 第一次 plan-verifier（2026-10-10）：REVISE 8 點，全部 **FIX**。
  1. 檢查集放 `eval/dev/` 頂層會改到 dev302：改放 `eval/dev/names/`，加 `--dev 302` 不變的檢查，授權另列。
  2. `--extra-overlay` 行為沒定義、空實作也會過：寫明併進詞庫與封頂文字、缺檔報錯、可和 `--packs` 併用、沒給時不變，加 CLI 測試。
  3. 去重只比同讀音，會改到既有詞的封頂、還可能讓核心載入失敗：改成四份檔任何讀音下的詞都不收，建置時檢查交集為空；刪掉停用詞規則。
  4. 來源取法沒寫死：NSI 標籤順序、`locationSet` 判定、NSI 名字與維基標題都收、所有維基標題一律 zh-tw、5–10 字分數用 `scores()`。
  5. 同音處置不能重現、沒有合併關卡、衝突偵測的檔案歸 A3：加 `names-collisions.tsv`、全部處置完才合併、離線重建逐位元組相同、自己的 `decode`、不改 `build_acg_pack.py`。
  6. 前置條件沒寫：模型檔放進 worktree 並核對雜湊、抓取與離線建置的順序。
  7. 抓取沒有上限：允許的主機清單、沿用 `Api` 的限速、請求 3,000 個與快取 500 MB 上限，超過就停。
  8. 授權依據不在 repo：研究紀錄新增那一節，§1 指向它。
- 第二次 plan-verifier（2026-10-10）：REVISE 6 點，逐項處置，全部 **FIX**（之後再一次收尾審查）。
  1. `names-collisions.tsv` 是輸入還是輸出不清楚：改成人工維護的輸入、建置只讀，未處置的寫進報告；「來源數多的優先」由 main 提建議、使用者確認；逐位元組比對的檔名寫清楚。
  2. 和 A3 的 `acg-add.tsv` 會互相影響：A3 先合併再 rebase 重建；`make test` 加交集測試。
  3. `--dump` 看不到詞條分數：改成走 CLI 載入路徑斷言封頂分數，`--dump` 只看有沒有出現。
  4. `lm_eval.py` 沒有 `--packs`：`lm_eval.py` 移出這一片（縮小範圍）。
  5. 名錄與 NSI 的格式沒寫：main 抓了樣本寫進研究紀錄（網址、表頭、範例列、雜湊、NSI 一筆真實資料），測試夾具由它裁剪；NSI 取名順序依樣本改成也收 `name:zh`（無簡體專用字時）。
  6. 進版控的五個檔都要有授權列：§1 寫明五個檔各自的授權與標示。
- 收尾審查（2026-10-10）：REVISE 1 點。人工核對的讀音寫進 `tools/reading_overrides.tsv` 不會對整個名字生效，能生效的單字覆寫又會改到評測集合與 A3 詞包：改成這一片自己的整名讀音表 `names-readings.tsv`，加測試，不改 `reading_overrides.tsv`。依規則收尾審查 REVISE 要暫停：**這一片等使用者決定要不要開工**，不派 executor。
- PR #110 的 /code-review（2026-10-11）：13 項，處置如下。
  - **FIX（程式）**：同音衝突偵測加開 ACG 詞包（App 預設開）。名單詞把詞包詞擠下第一名時，列成未處置的 (a)，由使用者決定，不自動丟。`names-collisions.tsv` 的一列可以點名詞包詞：詞包詞排第一、名單詞用 `+` 留著時，名單詞在那個讀音的分數最多是「詞包詞分數 − 1e-6」（`below_pack`）。另外修了摘要行標記、釘住 NSI commit、`tw_title` 的例外、去重測試改成斷言建置會停、過時的註解。
  - **FIX（資料，A3 合併後重建時一起做）**：`丸亀製麵`（NSI 的 `name:zh` 是日文寫法）用處置檔排除；基底詞庫已有台灣寫法 `丸龜製麵`。OpenCC 的日文新字體表不能整批轉換：403 個字裡有 329 個也在台灣詞庫的單字裡（例如「台」）。
  - **FIX（文件）**：PLAN 狀態行（書面第 8 名、計數救不了酷澎是推論）、§3 第 5 項 `--dump` 的說法、授權表與研究紀錄刪掉和來源授權無關的權利說明、授權表刪掉兩個不存在的分類。
  - **DEFER**：`tw_title` 每個標題一個請求（快取 1,382 個，離 3,000 的上限不遠，下次大改來源時改成批次查詢）；學校簡稱的規則會收進「市立大里」「國立宜中」這類少有人打的重定向（品質問題，不是錯字，留給之後的詞庫片）。
  - A3 先合併、這一片再重建與重量（§2）：照原訂順序。

