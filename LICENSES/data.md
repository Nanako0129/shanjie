# 資料與模型授權清單

| 路徑 | 來源 | 授權 | 備註 |
|---|---|---|---|
| `data/lexicon/mcbpmf-data.txt` | 小麥注音 McBopomofo 3.1.1 的 `data.txt`（片語源自 libtabe `tsi.src`） | MIT（全文 `LICENSES/McBopomofo-MIT.txt`）；libtabe 為 BSD | 可再散布 |
| `data/lexicon/overlay-add.tsv` | 由 `tools/build_overlay.py` 從 Wikimedia 2026-10-01 的標題 dump 產生：中文維基、英文維基詞典、中文維基詞典（`all-titles-in-ns0`）；OpenCC `STCharacters.txt`（Apache-2.0）只用來過濾，不在檔案內 | **CC BY-SA 4.0**，署名：Wikipedia 與 Wiktionary 貢獻者（https://creativecommons.org/licenses/by-sa/4.0/ ） | 只有這個檔是 share-alike；程式碼仍是 Apache-2.0。來源網址與 SHA-256 在 `tools/build_overlay.py`。S2r-2 起，含「一」「不」的詞另有一列變調讀音（依 `tools/build_sandhi.py` 的規則推出），是疊加層的衍生，所以同樣是 CC BY-SA 4.0，放在這個檔而不是 MIT 的 `sandhi-add.tsv` |
| `data/lexicon/sandhi-add.tsv` | 由 `tools/build_sandhi.py` 從 `mcbpmf-data.txt` 的多字詞產生：把「一」「不」改成另一種合規讀音（本調↔變調）、「法」的 ㄈㄚˋ 改成 ㄈㄚˇ | MIT（小麥基底的衍生；全文 `LICENSES/McBopomofo-MIT.txt`） | 規則依據教育部《國語辭典簡編本》〈單一音讀〉與 88 年《國語一字多音審訂表》；規則是事實，檔案不含任何教育部資料 |
| `data/lexicon/demote.tsv` | 本專案自寫（降權表，見 `docs/contracts/sw-sensitive-demote.md`；目前一行，來源是使用者回報與探針 `experiments/sw/sensitive-reading.txt`） | **CC BY-SA 4.0**（和專案其他資料一致，契約 §2 的決定） | 不含任何第三方資料；收錄條件寫在檔頭 |
| `data/packs/acg-add.tsv`、`data/packs/acg-sources.tsv`、`data/packs/acg.json` | 由 `tools/build_acg_pack.py` 從中文維基百科產生（契約 `docs/contracts/acg-pack.md`）：公共轉換組 `Module:CGroup/*`／`Template:CGroup/*`（ACG 相關的組，取 zh-tw 值）、動畫作品條目的顯示標題、條目與角色列表裡的登場人物。每個詞的出處（頁面與抓取時的 revision ID）在 `acg-sources.tsv`；`acg.json` 記版號、各來源數量與檔案的 SHA-256。萌娘百科不用（CC BY-NC-SA，與 BY-SA 不相容） | **CC BY-SA 4.0**，署名：Wikipedia 貢獻者（https://creativecommons.org/licenses/by-sa/4.0/ ） | 可選的詞包，隨 App 內附、選單可開關（預設開，使用者 2026-10-09 決定）。和 `overlay-add.tsv` 同樣是 share-alike；程式碼仍是 Apache-2.0 |
| `data/packs/acg-manual.tsv` | 維護者自己手動加的 ACG 詞（詞、作品、備註），由 `tools/build_acg_pack.py` 當成一個來源（標籤 `manual`）併入詞包 | CC0（維護者自己的清單） | 併入 `acg-add.tsv` 之後，隨詞包以 CC BY-SA 4.0 散布；本檔不含第三方資料；建置輸入，不隨 App 內附 |
| `data/packs/acg-exclude.tsv` | 從條目抽出、但不是名字的字串（句子片段、轉換錯誤）與排除理由，逐一對照中文維基百科後定出；字串本身取自維基百科的條目文字 | **CC BY-SA 4.0**，署名：Wikipedia 貢獻者（https://creativecommons.org/licenses/by-sa/4.0/ ），與其他詞包檔相同 | 建置輸入，不隨 App 內附 |
| `data/packs/acg-groups.tsv` | 本專案自寫：把 `Template:CGroup/list` 的 ACG 相關組分成收／不收 | **CC BY-SA 4.0**（和專案其他資料一致） | 建置輸入，不含第三方資料；不隨 App 內附 |
| `data/packs/acg-collisions.tsv` | 同音衝突的處置：讀音、保留與排除的詞（取自維基百科轉換組與條目的詞）與理由 | **CC BY-SA 4.0**，署名：Wikipedia 貢獻者（https://creativecommons.org/licenses/by-sa/4.0/ ），與 `acg-exclude.tsv` 相同 | 建置輸入，不隨 App 內附 |
| `eval/dev/acg/*.txt` | Wikidata 的 zh-tw 標籤（作品名、角色、角色名片段，sitelink 數最多者），讀音由 `tools/readings.py` 產生 | CC0（Wikidata 的資料授權） | 只用於評測，不用來調參 |
| `eval/sets/trap.txt`、`eval/sets/daily.txt` | 本專案自寫 | CC0 | |
| `eval/probe/s2r-probe.txt` | 由 `experiments/s2/build_probe.py` 從 `eval/dev/` 的句子產生（換「一」「不」的讀音） | CC0 | |
| `eval/dev/*.txt`、`eval/holdout/*`、`eval/learn/cases.tsv` | 本專案自寫 | CC0 | |
| `eval/dev/user-reported.txt` | 使用者回報的真實句子 | CC0（使用者 2026-10-03 同意） | |
| `eval/sets/moedict.txt` | 教育部《重編國語辭典修訂本》例句抽樣（經 g0v/moedict-data） | CC BY-ND 3.0 TW | **只用於評測，不得用來建詞庫或改作** |
| `eval/variants.tsv` | 教育部《重編國語辭典修訂本》釋義中的「也作／亦作」詞對（經 g0v/moedict-data，commit a6dc997） | CC BY-ND 3.0 TW | **只用於評測**。每一欄都是辭典原有的詞條字串，詞對是辭典記載的事實，不含釋義文字；由 `tools/build_variants.py` 產生 |
| `data/lm/bigram.sjlm`（不在 git；GitHub Release `model-v4` 附件） | 由 `tools/build_lm.py` 從語料詞頻計數產生：中文維基百科 2026-10-01 dump 的 20 萬篇條目、Mozilla Common Voice 繁中（台灣）句子、Tatoeba 中文句子、gpt-oss-120b 產生的台灣口語合成句。發佈的檔案另含少量用來辨識來源的指紋條目 | **CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ；維基為 CC BY-SA 4.0；Common Voice 為 CC0；Tatoeba 為 CC BY 2.0 FR；合成句是 gpt-oss-120b 的模型輸出，模型權重本身為 Apache-2.0） | 署名：Wikipedia contributors、Tatoeba contributors。不含任何私人對話資料 |
| `data/lm/classes.sjc`（不在 git；GitHub Release `classes-v2` 附件） | 由 `tools/build_classes.py` 從和 `bigram.sjlm` 同一批計數的詞類分群產生（S2k），只配 model-v4 | **CC BY-SA 4.0**（來源同上一列） | 署名：Wikipedia contributors、Tatoeba contributors。不含任何私人對話資料 |
| 執行期模型（S5 起） | Gemma 4 E2B 等 | 各自授權（Gemma 4：Apache-2.0） | 不隨安裝檔散布，第一次啟用時下載並顯示授權 |
