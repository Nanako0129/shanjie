# 資料與模型授權清單

每個檔一列：來源類型與規模／授權／署名。程式碼是 Apache-2.0。「隨 App 內附」指檔案或其衍生檔放進 App 的 `Resources/`；沒有寫的檔是建置輸入或評測資料，不隨 App 內附。

通用的署名：Wikipedia 與 Wiktionary 貢獻者（CC BY-SA 4.0，https://creativecommons.org/licenses/by-sa/4.0/ ）。

## 詞庫與詞包

| 路徑 | 來源類型與規模 | 授權 | 署名 |
|---|---|---|---|
| `data/lexicon/mcbpmf-data.txt` | 小麥注音 McBopomofo 3.1.1 的 `data.txt`（片語源自 libtabe `tsi.src`） | MIT（全文 `LICENSES/McBopomofo-MIT.txt`）；libtabe 為 BSD | 小麥注音、libtabe |
| `data/lexicon/overlay-add.tsv` | 由 `tools/build_overlay.py` 從 Wikimedia 2026-10-01 的標題 dump 產生：中文維基、英文維基詞典、中文維基詞典（`all-titles-in-ns0`）。OpenCC `STCharacters.txt`、`JPShinjitaiCharacters.txt`（Apache-2.0）只用來過濾，不在檔案內。讀音的依據之一是教育部《國語辭典簡編本》（只取詞名、注音一式、變體類型與變體注音來決定讀音，原資料不散布、不在檔案內）。含「一」「不」的詞另有依 `tools/build_sandhi.py` 規則推出的變調讀音列；檔尾依序接 `overlay-manual.tsv`（手動加的詞）與 `slang-add.tsv`（網路用語）的列。隨 App 內附 | **CC BY-SA 4.0**（整個檔 share-alike）；《國語辭典簡編本》為 CC BY-ND 3.0 臺灣（教育部，只用於決定讀音，資料本身不散布） | Wikipedia 與 Wiktionary 貢獻者；網路用語列另見 `slang-add.tsv` 一列 |
| `data/lexicon/overlay-manual.tsv` | 手動加的詞（詞、讀音、備註），建置輸入，接在 `overlay-add.tsv` 檔尾 | CC0；併入 `overlay-add.tsv` 後隨該檔以 CC BY-SA 4.0 散布 | — |
| `data/lexicon/slang-add.tsv` | 網路用語詞表（詞、讀音、來源，部分列有第四欄加分）。詞取自中文維基的網路用語與流行語列表、中英文維基詞典的分類（MediaWiki API）、Fandom 上 PTT 鄉民百科與迷因 Wiki 的分類標題、台灣公開論壇的用量（只取詞，不含原文）、Muse（Meta 平台的流行語建議，收錄前都用論壇用量確認，只取詞）、Discord 提供的詞；建置輸入，由 `tools/build_overlay.py` 接進 `overlay-add.tsv` | **CC BY-SA 4.0**；PTT 鄉民百科與迷因 Wiki 的文字是 CC BY-SA 3.0，依 3.0 的「後續版本」條款以 4.0 散布；Discord 提供的詞 CC0 | Wikipedia 與 Wiktionary 貢獻者、PTT 鄉民百科 contributors（Fandom）、Fandom 迷因 Wiki 貢獻者 |
| `data/lexicon/sandhi-add.tsv` | 由 `tools/build_sandhi.py` 從 `mcbpmf-data.txt` 的多字詞產生：「一」「不」的另一種合規讀音（本調↔變調）、「法」的 ㄈㄚˋ 改成 ㄈㄚˇ、「紮」的口語讀音 ㄓㄚ、基底輕聲詞的本調讀音列（標籤 `neutral`）。規則依據教育部《國語辭典簡編本》〈單一音讀〉與 88 年《國語一字多音審訂表》；規則是事實，檔案不含教育部資料。隨 App 內附 | MIT（小麥基底的衍生；全文 `LICENSES/McBopomofo-MIT.txt`） | 小麥注音 |
| `data/lexicon/variant-order.tsv` | 本專案自寫的異體寫法排序表（溼／濕、姐／姊等 35 組），依台灣公開論壇與 g0v 打字文字的次數排序；不含任何第三方資料，收錄規則寫在檔頭 | **CC0 1.0** | — |
| `data/lexicon/demote.tsv` | 本專案自寫的降權表（目前一行），來源是使用者回報與探針 `eval/sw/sensitive-reading.txt`；不含第三方資料，收錄條件寫在檔頭 | **CC BY-SA 4.0**（與專案其他資料一致） | — |
| `data/lexicon/names-english.txt` | 本專案自寫的名單層英文寫法（名字的英文大小寫）；不含第三方資料，格式寫在檔頭。編進核心，和 `data/english-extra.txt` 合成一張表（`docs/spec/english-mixed.md` §4.4） | **CC0 1.0** | — |
| `data/english-extra.txt` | 本專案自寫的英文正式寫法表（ChatGPT、GitHub、macOS 等，兩百多行）；不含第三方資料，收錄規則寫在檔頭。編進核心（`docs/spec/english-mixed.md` §4.4） | **CC0 1.0** | — |
| `data/lexicon/rerank-protect.tsv` | 本專案自寫的重排保護詞表（語意校正不得換掉的常用詞，目前 5 個）；不含第三方資料，收錄規則寫在檔頭。隨 App 內附（`Resources/rerank-protect.tsv`），核心載入詞庫時讀它 | **CC0 1.0** | — |
| `data/lexicon/std-exempt.tsv` | 解碼器讀音項不扣分的 12 對（讀音，字）：詞庫 `mcbpmf-data.txt` 裡該字有兩個以上讀音、教育部《國語辭典簡編本》的第一讀音在詞庫被排在後面的那 12 對，由 `tools/std_exempt.py` 產生（只取簡編本單字條目的注音一式，原資料不散布、不在檔案內）；檔頭記兩份來源的 SHA-256。隨 App 內附（`Resources/std-exempt.tsv`），核心載入模型時讀它 | **CC BY-SA 4.0**；《國語辭典簡編本》為 CC BY-ND 3.0 臺灣（教育部，只用於決定這 12 對讀音，資料本身不散布） | — |
| `data/lexicon/mixed-words.tsv`、`mixed-words-sources.tsv` | 本專案自寫的中英混合詞表（CP值、SIM卡等英文接中文的詞，各列是英文、中文部分的讀音、寫法與次數）與每列寫法的依據。詞取自台灣公開論壇裡英文詞後面常接的中文詞（只取詞與次數，不含原文），寫法對照中文維基百科的條目標題。`mixed-words.tsv` 隨 App 內附 | **CC0 1.0** | — |
| `data/lexicon/digits.tsv` | 數字語境的計數表：由 `tools/build_digits.py` 從中文維基百科 2026-10-01 dump 的前 200,000 篇條目（zh-tw 轉換）與口語語料（Mozilla Common Voice、Tatoeba 的句子）數出詞與數字串前後相接的次數；不含合成句、不含私人對話資料。隨 App 內附 | **CC BY-SA 4.0** | Wikipedia contributors、Tatoeba contributors |
| `data/lexicon/names-add.tsv` | 名單層，由 `tools/build_names.py` 產生：name-suggestion-index 的台灣品牌名（咖啡、餐廳、速食、銀行、加油站、藥局、超商、超市、百貨、電子、服飾、美妝、烘焙、茶飲等 17 類）、這些品牌在中文維基的 zh-tw 條目標題（經 Wikidata sitelink）、中文維基分類（台灣手搖茶飲品牌、台灣餐飲公司、台灣公司）的條目標題、教育部各級學校名錄（最大學年度）與中文維基有重定向的學校簡稱，另加 `names-manual.tsv`。檔尾標籤 `creator`、`store` 的列來自 `names-creators.tsv`、`names-stores.tsv`。隨 App 內附（`Resources/names-add.tsv`），常開 | **CC BY-SA 4.0**，三方標示：(1) name-suggestion-index（https://github.com/osmlab/name-suggestion-index ），BSD-3-Clause，版權聲明與條款全文在 `LICENSES/name-suggestion-index-BSD-3-Clause.txt`，不以作者名義背書；(2) Wikipedia 貢獻者（CC BY-SA 4.0；Wikidata 的結構化資料為 CC0）；(3) 教育部統計處〈大專校院名錄〉（資料集 6091）與〈一般高級中等學校名錄〉（資料集 6089），2026 年 10 月取得（學年度見 `names.json` 的 `moe`）；此開放資料依政府資料開放授權條款 (Open Government Data License) 進行公眾釋出，使用者於遵守本條款各項規定之前提下，得利用之（https://data.gov.tw/license ） | 三方標示寫進 App 的 `Resources/LICENSES/CC-BY-SA-4.0-attribution.txt`，NSI 的版權聲明全文另附 `Resources/LICENSES/name-suggestion-index-BSD-3-Clause.txt` |
| `data/lexicon/names-sources.tsv`、`data/lexicon/names.json` | `names-add.tsv` 的出處紀錄：每個詞的來源（NSI 檔與 commit、維基頁面與 revision ID、名錄與學年度、Wikidata QID）；`names.json` 記版號、各來源數量、各過濾丟掉的數量與檔案的 SHA-256 | CC BY-SA 4.0，同 `names-add.tsv` | 同 `names-add.tsv` |
| `data/lexicon/names-collisions.tsv` | 名單內同音衝突的處置：讀音、保留與排除的詞（取自 `names-add.tsv` 的來源）與理由，人工維護的建置輸入 | CC BY-SA 4.0，三方標示同 `names-add.tsv` | 同 `names-add.tsv` |
| `data/lexicon/names-creators.tsv` | 實況主、YouTuber、VTuber 的名字與節目、頻道名，以及台灣以外的中文圈名人與店家的人名（詞、讀音、備註）。名字字串來自 Muse（Meta 平台的建議，只取名稱）、Discord 提的名字；收錄依台灣公開論壇的用量（看板 ≥ 3、文章 ≥ 10，只取詞，不含原文）與同讀音規則。建置輸入，由 `tools/build_names.py --append-creators` 追加到 `names-add.tsv` 檔尾（標籤 `creator`） | CC0（名稱清單與用量判定）；追加進 `names-add.tsv` 後隨該檔以 CC BY-SA 4.0 散布 | — |
| `data/lexicon/names-stores.tsv` | 店家、餐廳、飯店、企業的名稱（詞、讀音、備註）。名稱來自 Muse（Meta 平台的建議，只取名稱）；收錄依台灣公開論壇的用量（看板 ≥ 3、文章 ≥ 10，只取詞，不含原文）、出貨解碼器現在打錯、用量多於原本第一名。建置輸入，追加到 `names-add.tsv` 檔尾（標籤 `store`） | CC0（名稱清單與用量判定）；追加後隨 `names-add.tsv` 以 CC BY-SA 4.0 散布 | — |
| `data/lexicon/names-manual.tsv` | 手動補的常見專有名詞（詞、備註、讀音），建置輸入 | CC0；併入 `names-add.tsv` 後隨該檔以 CC BY-SA 4.0 散布 | — |
| `data/lexicon/names-readings.tsv` | 人工核對過的整名讀音表（名字、讀音），建置輸入 | CC0 | — |
| `data/packs/acg-add.tsv`、`data/packs/acg-sources.tsv`、`data/packs/acg.json` | 由 `tools/build_acg_pack.py` 從中文維基百科產生：公共轉換組 `Module:CGroup/*`／`Template:CGroup/*`（ACG 相關的組，取 zh-tw 值）、動畫作品條目的顯示標題、條目與角色列表裡的登場人物。每個詞的出處（頁面與抓取時的 revision ID）在 `acg-sources.tsv`；`acg.json` 記版號、各來源數量與檔案的 SHA-256。萌娘百科不用（CC BY-NC-SA 4.0，與 BY-SA 不相容）。`acg-add.tsv` 隨 App 內附，設定裡可開關 | **CC BY-SA 4.0** | Wikipedia 貢獻者 |
| `data/packs/acg-manual.tsv` | 手動加的 ACG 詞，四欄：詞、作品、備註、另外的讀音（選填）；由建置工具當成一個來源（標籤 `manual`）併入詞包，建置輸入。另有 34 個 BanG Dream! 主要樂團成員的名字，取自中文維基百科「BanG Dream!角色列表」（revid 94761519，各樂團小節） | 手動加的條目 CC0；取自維基百科的部分 **CC BY-SA 4.0**；整份檔案和詞包一樣以 CC BY-SA 4.0 散布 | Wikipedia 貢獻者 |
| `data/packs/acg-exclude.tsv` | 從條目抽出、但不是名字的字串（句子片段、轉換錯誤）與排除理由，逐一對照中文維基百科後定出；字串取自維基百科的條目文字。建置輸入 | **CC BY-SA 4.0** | Wikipedia 貢獻者 |
| `data/packs/acg-collisions.tsv` | 同音衝突的處置：讀音、保留與排除的詞（取自維基百科轉換組與條目的詞）與理由。建置輸入 | **CC BY-SA 4.0** | Wikipedia 貢獻者 |
| `data/packs/acg-groups.tsv` | 本專案自寫：把 `Template:CGroup/list` 的 ACG 相關組分成收／不收；不含第三方資料。建置輸入 | CC BY-SA 4.0（與專案其他資料一致） | — |
| `data/packs/acg-v040-words.txt` | v0.4.0 出貨詞包的詞（v0.4.0 tag 的 `acg-add.tsv` 第二欄、位元組排序去重），建置工具用它區分「已出貨的詞」與新詞。建置輸入 | **CC BY-SA 4.0** | Wikipedia 貢獻者 |
| `data/packs/acg-kept-c.tsv`、`data/packs/acg-keep-both.tsv` | 「讓詞包名字排第一」的詞包名字（178 個），與「兩種寫法都留」的詞和讀音；詞取自詞包。建置輸入與驗收清單 | **CC BY-SA 4.0** | Wikipedia 貢獻者 |
| `data/packs/acg-removed.tsv`、`data/packs/acg-counts.tsv`、`data/packs/acg-yield.tsv` | 從詞包拿掉的已出貨詞與理由、詞包詞在模型裡的次數、詞包詞在某讀音上讓出第一名的清單；詞取自詞包。建置輸入與驗收清單 | **CC BY-SA 4.0** | Wikipedia 貢獻者 |
| `data/packs/it-add.tsv`、`data/packs/it-sources.tsv`、`data/packs/it.json` | 由 `tools/build_it_pack.py` 產生，只收詞（字串）與讀音，不收定義或說明。來源：(1) 國家教育研究院「電子計算機學術名詞」（data.gov.tw 資料集 15416）的中文名稱（過維基詞頻門檻）與「兩岸對照名詞—計算機學術名詞」（資料集 15275）的中文名稱；(2) 中文維基百科 `Module:CGroup/IT`、`Module:CGroup/Electronics` 的 zh-tw 值，以及 AI 相關分類（人工智能、机器学习、大型语言模型、自然語言處理、深度学习、人工神经网络、生成式人工智能、计算机视觉，往下兩層）的條目標題（過詞頻門檻與 Wikidata 專有名詞過濾）；(3) `it-manual.tsv`。每個詞的來源在 `it-sources.tsv`；`it.json` 記版號、各來源與計數檔的雜湊、各條規則擋下的數量。原始下載不進 repo。`it-add.tsv` 隨 App 內附，設定裡可開關 | **CC BY-SA 4.0**；國家教育研究院的資料依政府資料開放授權條款 (Open Government Data License) 釋出，提供機關：國家教育研究院（https://data.gov.tw/license ） | Wikipedia 貢獻者；國家教育研究院。標示寫進 App 的 `Resources/LICENSES/CC-BY-SA-4.0-attribution.txt` |
| `data/packs/it-manual.tsv` | 手寫的資訊與 AI 詞（詞、類別、備註、另外的讀音選填），建置輸入 | CC0（專案自己的條目） | — |
| `data/packs/it-collisions.tsv` | 同音衝突的處置（讀音、保留的詞、排除的詞、理由）；詞取自詞包，建置輸入 | CC BY-SA 4.0（併入詞包後和詞包一致） | Wikipedia 貢獻者 |
| `data/packs/it-readings.tsv` | 人工確認過的讀音（詞、讀音），取代 `tools/readings.py` 的結果。建置輸入 | CC0 | — |
| `data/packs/quotes-manual.tsv` | 經典電影台詞清單：台詞原文、出處、年份、核對來源、收錄人、選填的讀音。每列的核對來源記在檔裡，核對來源空白的列建置工具不收；只收公開影視作品的台詞。建置輸入 | CC0（清單檔本身；台詞原文的權利屬原作者） | — |
| `data/packs/quotes-add.tsv`、`data/packs/quotes.json` | 由 `tools/build_quote_pack.py` 從 `quotes-manual.tsv` 已核對的列（208 句）切成分句（2–10 個漢字，278 個分句、311 列讀音含變調列）產生的疊加層與清單檔（版號、各項個數、檔案的 SHA-256）；讀音由 `tools/readings.py` 產生或取自清單第 6 欄。隨 App 內附，設定裡可開關（預設關） | CC0（同清單） | — |
| `data/packs/meme-add.tsv`、`data/packs/meme-sources.tsv`、`data/packs/meme.json` | 由 `tools/build_meme_pack.py` 產生（`docs/spec/lexicon-and-packs.md` §9）：只收詞（字串）與讀音，不收定義、說明或網頁原文。候選的標題來自 Fandom 上的迷因 Wiki 與 PTT 鄉民百科（CC BY-SA 3.0，依「或更新版本」改用 4.0）與中文維基百科；另有模型建議與一個公開網站的詞條字串，只當候選、只取字串；收錄由審定清單 `data/packs/meme-allow.tsv`（建置輸入）決定，並依台灣公開論壇（論壇 A）的用量。來源欄記類別代號，另有類別欄（`meme`、`offensive`）。隨 App 內附，設定裡可開關（預設開）；署名寫進 App 的 `Resources/LICENSES/CC-BY-SA-4.0-attribution.txt` | **CC BY-SA 4.0** | 迷因 Wiki 貢獻者、PTT 鄉民百科貢獻者（Fandom）、Wikipedia 貢獻者 |
| `data/packs/meme-allow.tsv`、`data/packs/meme-collisions.tsv`、`data/packs/meme-extra-readings.tsv` | 迷因詞包的審定清單（詞、類別）、同音衝突的處置（讀音、保留的詞、排除的詞、理由）與另外的讀音（詞、讀音）。建置輸入 | CC BY-SA 4.0（併入詞包後和詞包一致） | 同詞包 |
| `data/packs/beauty-add.tsv`、`data/packs/beauty-sources.tsv`、`data/packs/beauty.json` | 由 `tools/build_meme_pack.py --pack beauty` 產生（`docs/spec/lexicon-and-packs.md` §8）：只收詞（字串）與讀音，不收定義、說明或網頁原文。候選來自公開網站與台灣公開論壇（論壇 A）的挖詞（只取字串），收錄依論壇 A 的用量；來源欄只記類別代號。隨 App 內附，設定裡可開關（預設開）；沒有 CC BY-SA 的來源，所以署名檔不列它 | CC0（名稱清單與用量判定，沒有第三方著作權內容） | — |
| `data/packs/beauty-collisions.tsv`、`data/packs/beauty-readings.tsv` | 美妝詞包同音衝突的處置與人工確認過的讀音。建置輸入 | CC0 | — |
| `data/packs/beauty-exclude.tsv` | 人工審後剔除的字串與剔除類別。建置輸入 | CC0 | — |

## 模型檔（不在 git，GitHub Release 附件）

下列模型檔的訓練文字只寫類型與規模，語料來源身分與原始文字不公開；公開部分的授權與署名如下。所有模型檔都不含私人對話資料。

| 路徑 | 來源類型與規模 | 授權 | 署名 |
|---|---|---|---|
| `data/lm/bigram.sjlm`（Release `sj-lm-7-9.7m`，通語 SJ-LM-7-9.7M） | 由 `tools/build_lm.py` 從語料詞頻計數產生：中文維基百科 2026-10-01 dump 的 20 萬篇條目、Mozilla Common Voice 繁中（台灣）句子、Tatoeba 中文句子、gpt-oss-120b 產生的台灣口語合成句、g0v Slack `#rand0m` 頻道的公開訊息、台灣公開論壇的文章與推文（只用於詞頻計數，不含原文）。斷詞用的詞彙只用常開詞庫（小麥注音基底、萌典補詞、疊加層、變調列、名單層）與詞包的詞，來源與授權見上面各列。發佈的檔案另含少量用來辨識來源的指紋條目 | **CC BY-SA 4.0**（維基 CC BY-SA 4.0；Common Voice CC0；g0v `#rand0m` CC0；Tatoeba CC BY 2.0 FR；合成句是 gpt-oss-120b 的模型輸出，模型權重本身為 Apache-2.0；資訊與 AI 詞包的名詞依政府資料開放授權條款，提供機關：國家教育研究院） | Wikipedia contributors、Tatoeba contributors |
| `data/lm/classes.sjc`（Release `sj-wc-6-512c`，辨類 SJ-WC-6-512C） | 由 `tools/build_classes.py` 從和 `bigram.sjlm` 同一批計數的詞類分群產生，只配 `sj-lm-7-9.7m` | CC BY-SA 4.0（來源同 `bigram.sjlm`） | Wikipedia contributors、Tatoeba contributors |
| `data/lm/kn.sjkn`（Release `sj-kn-2-397k`，承接 SJ-KN-2-397K） | 由 `tools/kn_cont.py` 從和 `bigram.sjlm` 同一批計數算出的 Kneser–Ney 接續次數，只配 `sj-lm-7-9.7m` | CC BY-SA 4.0（來源同 `bigram.sjlm`） | Wikipedia contributors、Tatoeba contributors |
| `data/lm/trigram.sjt3`（Release `sj-cx-2-7.7m`，連文 SJ-CX-2-7.7M） | 書面設定用的 trigram 表：由維基百科與口語池（Mozilla Common Voice、Tatoeba、合成句；不含 g0v 與論壇）的計數，相對熵剪枝後重新對應到 `sj-lm-7-9.7m` 的詞 id（`tools/build_trigram.py`） | CC BY-SA 4.0（來源同 `bigram.sjlm`） | Wikipedia contributors、Tatoeba contributors |
| `data/lm/trigram-chat.sjt3`（Release `sj-cx-2-950k`，連文 SJ-CX-2-950K） | 聊天設定用的 trigram 表：由和 `bigram.sjlm` 同一批語料（維基百科、口語語料，含 g0v `#rand0m` 與台灣公開論壇的文章與推文，只用於詞頻計數，不含原文）的計數，相對熵剪枝成 950,000 條，重新對應到 `sj-lm-7-9.7m` 的詞 id | CC BY-SA 4.0（來源同 `bigram.sjlm`） | Wikipedia contributors、Tatoeba contributors |
| `data/lm/tinylm.sjtl`（Release `sj-sr-8-15m`，會意 SJ-SR-8-15M） | 「語意校正」用的小型字元語言模型，格式 SJTL0004，權重 int8。訓練文字和 `bigram.sjlm` 同類：中文維基百科 2026-10-01 dump 的前 20 萬篇條目、Mozilla Common Voice 繁中（台灣）句子、Tatoeba 中文句子、gpt-oss-120b 產生的台灣口語合成句、g0v Slack `#rand0m` 頻道的公開訊息、台灣公開論壇的文章與推文，以及 OPUS OpenSubtitles v2018 繁體字幕（清理後 4,432,866 句）；都只用於訓練，不含原文，繁體句不套 OpenCC `TWPhrases` | **CC BY-SA 4.0**（來源同 `bigram.sjlm`）；OpenSubtitles 只用於訓練，字幕文字不在檔案內，照 OPUS 的要求引用 P. Lison and J. Tiedemann, 2016, OpenSubtitles2016: Extracting Large Parallel Corpora from Movie and TV Subtitles, LREC 2016 | Wikipedia contributors、Tatoeba contributors；OpenSubtitles（Lison and Tiedemann 2016，http://www.opensubtitles.org/ ） |
| `data/lm/colloquial.sjlm`（Release `sj-cl-2-69k`，常談 SJ-CL-2-69K） | 聊天設定的語意校正多參考的口語詞頻表，只有詞與詞對的次數、沒有句子文字，由 `tools/build_lm.py --corpora` 產生：(1) `sj-lm-7-9.7m` 計數詞表下的口語計數（Mozilla Common Voice 繁中（台灣）句子、Tatoeba 中文句子、gpt-oss-120b 產生的台灣口語合成句；不含 g0v 與論壇）；(2) 一份 143,895 句的公開口語池（約 300 萬漢字，已對公開評測集去重）：WildChat-1M 台灣子集 3,491 句（allenai/WildChat-1M，Allen Institute for AI）、YODAS zh000 繁體字幕 90,080 句（espnet/yodas，1,138 支 CC BY YouTube 影片的字幕）、Tatoeba 繁體中文句 36,839 句（208 位作者）、MASSIVE zh-TW 13,485 句（AmazonScience/massive，Amazon.com Inc.） | **CC BY-SA 4.0**（來源同 `bigram.sjlm`）。口語池四個來源：WildChat-1M ODC-BY 1.0（https://opendatacommons.org/licenses/by/1-0/ ）、YODAS CC BY 3.0（https://creativecommons.org/licenses/by/3.0/ ）、Tatoeba CC BY 2.0 FR（https://creativecommons.org/licenses/by/2.0/fr/ ）、MASSIVE CC BY 4.0（https://creativecommons.org/licenses/by/4.0/ ）；四個都只要求署名，可以併進 BY-SA | Tatoeba contributors 與上面口語池的四個來源；逐支影片與逐句的署名在 `colloquial-attribution.tsv`（下一列），App 的 `Resources/LICENSES/CC-BY-SA-4.0-attribution.txt` 與資料 Release 的 NOTICE 列出全部。YODAS 只給影片 id，照資料集提供的內容署名：資料集名稱、授權、影片 id、網址、句數 |
| `data/lm/colloquial-attribution.tsv`（`sj-cl-2-69k` 的另一個附件，雜湊在 `data/colloquial-attribution.tsv.sha256`） | 由 `tools/build_attribution.py` 從口語池的來源欄位產生：每個來源單位一列（YODAS 影片 id 與影片網址、Tatoeba 句子 id 與作者、WildChat 與 MASSIVE 的資料集出處與授權）與涵蓋的句數，不含句子文字。隨 App 打包（`Resources/LICENSES/colloquial-attribution.tsv`），也是資料 Release 的附件 | 整份清單跟著 `colloquial.sjlm` 以 **CC BY-SA 4.0** 散布 | 作者與影片 id 是 CC BY 系列授權要求的署名資訊，原樣沿用資料集提供的內容 |

## 評測資料

評測資料只用於評測，不隨 App 內附。

| 路徑 | 來源類型與規模 | 授權 | 署名 |
|---|---|---|---|
| `eval/sets/trap.txt`、`eval/sets/daily.txt` | 本專案自寫 | CC0 | — |
| `eval/sets/moedict.txt` | 教育部《重編國語辭典修訂本》例句抽樣（經 g0v/moedict-data） | CC BY-ND 3.0 TW；**只用於評測，不得用來建詞庫或改作** | 教育部 |
| `eval/variants.tsv` | 教育部《重編國語辭典修訂本》釋義中的「也作／亦作」詞對（經 g0v/moedict-data，commit a6dc997）；每一欄都是辭典原有的詞條字串，詞對是辭典記載的事實，不含釋義文字；由 `tools/build_variants.py` 產生 | CC BY-ND 3.0 TW；**只用於評測** | 教育部 |
| `eval/forms.tsv` | 教育部《重編國語辭典修訂本》釋義中的「也作／亦作」詞對與單字異體（經 g0v/moedict-data，commit a6dc997）；每一欄都是辭典原有的詞條字串與讀音，字對是辭典記載的事實，不含釋義文字；由 `tools/build_forms.py` 產生 | CC BY-ND 3.0 TW；**只用於評測** | 教育部 |
| `eval/probe/s2r-probe.txt` | 由 `tools/build_probe.py` 從 `eval/dev/` 的句子產生（換「一」「不」的讀音） | CC0 | — |
| `eval/rerank/s7e-function-words.txt` | 本專案自寫 | CC0 | — |
| `eval/dev/*.txt`、`eval/dev/holdout-2026-10-03/*`、`eval/holdout/.gitkeep`、`eval/learn/cases.tsv` | 本專案自寫 | CC0 | — |
| `eval/dev/user-reported.txt` | 使用者回報的真實句子 | CC0（回報者授權） | — |
| `eval/learn/newword-probe-boundary.txt`、`eval/learn/newword-boundary-teach.txt` | 本專案自寫的新詞邊界探針與教學句；讀音由 `tools/readings.py` 產生 | CC0 | — |
| `eval/learn/newword-probe.txt` | 本專案自寫的新詞探針：詞庫沒有的詞出現在新前文；讀音由 `tools/readings.py` 產生 | CC0 | — |
| `eval/holdout/talk.txt`、`eval/holdout/next/**/talk.txt`、`eval/holdout/retired/**/talk.txt` | 從公開口語書寫切出的子句：中文維基百科討論頁與互助客棧（2026-10-01 dump 之後的修訂，API 取得）、vTaiwan UberX 的 Polis 留言、公共政策網路參與平臺「提點子」（data.gov.tw 58036，不含提議者欄位）；逐列出處在 `eval/holdout/next/sources.tsv`，不用來調參或訓練 | **CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）；vTaiwan 資料 CC BY 4.0；提點子依政府資料開放授權條款第 1 版 | Wikipedia contributors（頁名與 revid 見 `sources.tsv`）、The Computational Democracy Project；「本資料由國家發展委員會提供，依政府資料開放授權條款（https://data.gov.tw/license ）進行公眾釋出」 |
| `eval/holdout/next/sources.tsv` | 新保留集的逐列出處（份、檔、列號、來源、單位、授權） | CC0 | — |
| `eval/dev/it/words.txt` | 資訊與 AI 詞包本身的檢查集：從 `it-add.tsv` 依來源比例抽 60 個詞（固定種子 20261024），一列一詞、沒有前文，讀音取自詞包 | CC BY-SA 4.0，與 `it-add.tsv` 相同 | Wikipedia 貢獻者、國家教育研究院 |
| `eval/dev/meme/words.txt`、`words-all.txt`、`embedded.txt` | 迷因詞包本身的檢查集：`words.txt` 從 `meme-add.tsv` 抽詞（固定種子 20261025，不到 60 個就全收），`words-all.txt` 是詞包全部的詞，`embedded.txt` 是把每個詞嵌進固定前後文的句子，一列一詞或一句，讀音取自建置用的讀音 | CC BY-SA 4.0，與 `meme-add.tsv` 相同 | 迷因 Wiki 貢獻者、PTT 鄉民百科貢獻者（Fandom）、Wikipedia 貢獻者 |
| `eval/dev/beauty/words.txt` | 美妝詞包本身的檢查集：從 `beauty-add.tsv` 抽 60 個詞（固定種子 20261108），一列一詞、沒有前文，讀音取自建置用的讀音 | CC0 | — |
| `eval/dev/acg/*.txt` | Wikidata 的 zh-tw 標籤（作品名、角色、角色名片段，sitelink 數最多者），讀音由 `tools/readings.py` 產生 | CC0（Wikidata 的資料授權） | — |
| `eval/dev/acg/recent.txt` | 2026 年作品與角色名 23 個的中文維基百科條目，逐一對照條目確認；讀音由 `tools/readings.py` 產生 | **CC BY-SA 4.0** | Wikipedia 貢獻者 |
| `eval/dev/acg/interpunct.txt` | 動漫詞包的檢查集：從含間隔號、角色列表條目整頁的角色名依作品分層抽 40 個（固定種子 20261010；`tools/readings.py` 標 CHECK、讀音有疑義的詞不抽），加 5 個指定詞共 45 列；名字取自中文維基百科的角色列表條目（revision 記在 `acg-sources.tsv`），讀音由 `tools/readings.py` 產生 | **CC BY-SA 4.0** | Wikipedia 貢獻者 |
| `eval/dev/names/stores.txt` | 店家檢查集：`names-stores.tsv` 每個收錄的字串與讀音一列（前文空白） | CC BY-SA 4.0，同 `names-add.tsv` | 同 `names-add.tsv` |
| `eval/dev/names/creators.txt` | 創作者名字檢查集：`names-creators.tsv` 每個收錄的字串與讀音一列（以 \| 分隔前文、詞、讀音，前文空白）；由 `names-creators.tsv` 產生 | CC BY-SA 4.0，同 `names-add.tsv`（隨 `names-add.tsv` 散布的名稱，不是 CC0） | 同 `names-add.tsv` |
| `eval/dev/names/names.txt` | 專有名詞檢查集：依來源分層抽 60 個名字加「酷澎」，名字取自 `names-add.tsv` 的來源（name-suggestion-index、中文維基、教育部名錄），讀音由 `tools/readings.py` 與 `names-readings.tsv` 產生 | CC BY-SA 4.0，三方標示同 `names-add.tsv`（不是 CC0） | 同 `names-add.tsv` |
| `tools/testdata/names/*` | `tools/test_build_names.py` 的夾具：從真實的 name-suggestion-index 台灣品牌資料（`nsi-*.json`，逐筆原樣節錄）與教育部學校名錄（`moe-*.csv`，表頭與少數列原樣節錄）裁剪；測試裡的維基與 Wikidata 回應是手寫的合成資料。只供測試 | NSI 部分 BSD-3-Clause（`LICENSES/name-suggestion-index-BSD-3-Clause.txt`）；名錄部分依政府資料開放授權條款第 1 版（教育部統計處，https://data.gov.tw/license ） | name-suggestion-index、教育部統計處 |

## 其他

| 路徑 | 來源類型與規模 | 授權 | 署名 |
|---|---|---|---|
| `packaging/AppIcon.icon/`（App 與安裝程式的圖示） | 本專案的設計；圖層 `glyph.svg` 的「解」是 Noto Serif CJK TC Black（Adobe／Google）的字形外框，由 `scripts/make-app-icon.swift` 轉成路徑；字型檔不進 repo、不隨 App 內附 | 圖示：Apache-2.0（和專案程式一致）；字型：SIL Open Font License 1.1。OFL FAQ 1.1 允許把字型用在 logo 與設計，1.13 寫明用字型做出的圖不受 OFL 約束（https://openfontlicense.org/ofl-faq/ ） | Noto Serif CJK TC（Adobe／Google） |
| 執行期模型（S5 起） | Gemma 4 E2B 等；不隨安裝檔散布，第一次啟用時下載並顯示授權 | 各自授權（Gemma 4：Apache-2.0） | — |
