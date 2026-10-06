# S2w：維基語料改用 MediaWiki 自己的台灣正體轉換

使用者 2026-10-05 把 S2w 選為下一片語料；2026-10-06 決定 S2n 合併後馬上做，詞類與 KN 平滑的重跑等 S2w 的計數。同日選定建置工具 zhconv-rs 0.4.2（PyPI）。

## 1. 為什麼

- **現況**（model-v2，模型 E）：`experiments/s2/build_counts.py` 先刪掉模板與標記（包括 `{{NoteTA}}` 與 `-{…}-`），再用 OpenCC 對照表逐句轉換（S2n：先判斷簡繁句）。維基編者在條目裡寫的轉換規則，在轉換前就被刪了。
- **一對多的字**（后／後、里／裡、干／乾／幹、台／臺…）現在靠 OpenCC 詞組與 S2n 的規則猜。維基自己的 zh-tw 轉換用三層資訊決定：全域轉換表、條目內的 `-{…}-` 規則、`{{NoteTA}}` 引用的公共轉換組（例如 `G1=IT` 把「内存」轉成「記憶體」）。
- **目標**：維基計數改用和 zh.wikipedia.org 的 zh-tw 頁面相同的轉換，讓語料的用字與用詞由維基編者的標記決定。

## 2. 工具與資料（固定）

- **zhconv-rs 0.4.2**（PyPI `zhconv-rs`，`import zhconv_rs`），只當建置工具。程式碼 MIT／Apache-2.0，轉換表 GPL-2.0-or-later；轉換表不放進 repo 或模型檔。是否影響模型檔的授權是法律判斷，這裡不構成法律意見（同 PLAN 的說明）。
  - 安裝：Mac 建 `~/.cache/shanjie/venv-s2w`（`python3 -m venv`），188 裝進既有的 `%USERPROFILE%\ime-research\proto\.venv`；兩邊都用 `pip install zhconv-rs==0.4.2 --require-hashes`，wheel 的 SHA-256 取自 PyPI 的 JSON，寫進 `experiments/s2w/requirements.txt`。
  - 內建表是 MediaWiki master `ecf4342132`（2026-02-05）；之後三次官方表更新不在裡面。研究紀錄寫明這一點；0.5.0 發佈後是否重跑另外決定。
  - 呼叫方式只用 `zhconv_rs.zhconv(text, "zh-tw", True)`（參數只能依位置傳；第三個參數一律明確傳 `True`）。
- **dump**：和 model-v2 相同的 `zhwiki-20261001-pages-articles.xml.bz2`（SHA-256 見 PLAN 的來源表）。公共轉換組與站上轉換表從**同一份 dump** 讀，不連網抓。
- **口語計數**：沿用模型 E 的 `counts-colloquial3.pkl`（`--expected`，`~/.cache/shanjie/work/s2n-e/`），不重算。口語語料不是 wikitext，S2w 不改它的轉換。
- **疊加層、詞庫、cvtune**：不變。

## 3. 做法

### 3.1 從 dump 抽轉換資料（`experiments/s2w/mwdata.py`，跑一次，結果快取在 `~/.cache/shanjie/work/s2w/mwdata.json`）

- **公共轉換組**：照 `Module:NoteTA` 的查找順序，群組名 `X` 先找 `Module:CGroup/X`，沒有再找 `Template:CGroup/X`。
  - Module：抓每個 `type = 'item'` 的 `rule`（含 `Item('原文', '規則')` 的簡寫）。`return require('Module:CGroup/Y')` 的別名頁指到 Y。
  - Template：抓每個 `{{CItem|規則|…}}` 的規則。
  - 重新導向（`#REDIRECT`／`#重定向`）照目標解開，最多 3 層。
- **NoteTA 的別名**：所有重新導向到 `Template:NoteTA` 的模板名（大小寫照 MediaWiki：只有第一個字母不分大小寫）。
- **站上轉換表**：`MediaWiki:Conversiontable/zh-tw` 與 `MediaWiki:Conversiontable/zh-hant` 的每一條 `來源 => 目標`。合併方式照 MediaWiki 的 `ZhConverter`：zh-tw 的條目優先，再補上 zh-hant 的條目。
- 報：群組數、規則總數、解不開的群組名（文章引用了但 dump 裡沒有）與它們被引用的篇數、兩份站上轉換表的條目數。

### 3.2 每篇文章的轉換（`build_counts.py --mw`、`build_tune.py --mw`）

1. HTML 實體還原（和現行相同的四個）。
2. 找出條目裡所有 NoteTA（含別名）的參數：`G1`–`G30` 的群組名，與數字參數 `1`–`30` 的規則。
3. 在條目最前面加上規則：站上轉換表的每一條寫成 `-{H|來源=>zh-tw:目標}-`；群組與數字參數的每一條規則寫成 `-{H|規則}-`。順序：站上轉換表、群組（照 G 的編號）、數字參數。
4. `zhconv_rs.zhconv(加上規則的全文, "zh-tw", True)`。
5. 之後照現行流程：刪模板（3 次）、刪標記、切句、取連續漢字；**不再呼叫 `convert()`**，也不套 `VARIANTS`、臺→台。
- **理由**：zhconv-rs 的 `H` 規則不分位置套用全文，所以加在開頭和線上 NoteTA 的效果相同（研究紀錄 2026-10-06 的 zhconv-rs 調查）。
- **優先順序**（main 2026-10-06 用 0.4.2 實測）：`-{H|來源=>zh-tw:目標}-` 可以用；兩條規則的來源相同時，**文中較後面的那條勝出**，和它在文中的位置無關（例如站上表的「通用电气=>奇異」寫在前、條目的「zh-cn:通用电气; zh-tw:通用電氣;」寫在後，結果是「通用電氣」；順序反過來就是「奇異」）。所以第 3 步的順序（站上轉換表、群組、數字參數，接著是條目本文裡的 `-{H|…}-`）讓條目自己的規則蓋過站上轉換表，和 MediaWiki 中條目規則優先於站上轉換表的行為相同。
- 沒有 `--mw` 時，兩支程式的輸出逐位元組不變。`--mw` 可以和 `--expected` 一起用（S2w 的正式計數一律加 `--expected`）。
- `build_tune.py --mw` 只影響 wikitune 的參考句（`--part wiki`），cvtune 與 `colloquial-train.txt` 不變。輸出寫到 `S2_WORK` 指定的目錄，不蓋掉模型 E 的 tune 檔。

### 3.3 單元檢查（`experiments/s2w/test_mw.py`，不需要 dump，用手寫的小 wikitext；需要裝好 zhconv-rs 的 venv）

- 條目內規則：`-{zh-cn:内存; zh-tw:記憶體;}-` 轉成「記憶體」；`-{H|zh-cn:内存; zh-tw:記憶體;}-` 之後全文的「内存」都轉成「記憶體」；`-{R|王后}-` 原樣。
- NoteTA：一個測試用的小 `mwdata`（群組 `T` 含規則 `zh-cn:内存; zh-tw:記憶體;`），條目 `{{NoteTA|G1=T}}…内存…` 的輸出有「記憶體」；**突變**：不加群組規則（第 3 步跳過群組），這條必須失敗。
- 別名：用別名模板名引用同一個群組，結果相同。
- 數字參數：`{{NoteTA|1=zh-cn:鼠标; zh-tw:滑鼠;}}…鼠标…` 輸出有「滑鼠」。
- 站上轉換表：小 `mwdata` 裡一條 `通用电气 => 奇異`，條目裡的「通用电气」轉成「奇異」；**突變**：不加站上轉換表，這條必須失敗。
- 優先順序：同一個小 `mwdata` 再加群組 `G` 含 `zh-cn:通用电气; zh-tw:通用電氣;`，條目 `{{NoteTA|G1=G}}…通用电气…` 得到「通用電氣」；條目本文另寫 `-{H|zh-cn:内存; zh-tw:內存;}-` 而群組 `T` 寫 `zh-cn:内存; zh-tw:記憶體;` 時，得到「內存」（本文規則蓋過群組）。
- 這兩條（單向規則、優先順序）任一條不過就停下回報，不改用別的寫法。
- 沒有 `--mw` 時的逐位元組不變：用 `build_counts.py` 跑 600 篇（`--procs 1`，加與不加 `--expected` 各一次），和 main 上同一指令的 pkl `cmp` 相同（S2n 的 verifier 用過同樣的方法）。

## 4. 執行順序與關卡

### 4.1 第一關：轉換本身（Mac，維基前 2,000 篇，不建模型）

- 程式：`experiments/s2w/gate1.py`（executor 寫，算在第 5 節的預算；main 執行）。兩種轉換都跑維基前 2,000 篇（`articles(2000)` 的順序），每篇各自得到一串「連續漢字段」（和計數用的相同：切句、取連續漢字、長度 ≥ 2）。
- **對齊**（每篇各自做）：S2n 那串是 A、MW 那串是 B，用 `difflib.SequenceMatcher(None, A, B, autojunk=False).get_opcodes()`。`equal` 的段相同；`replace` 且兩邊段數相等時，逐一配成「不同的段」；其餘（`insert`、`delete`、兩邊段數不相等的 `replace`）都算「對不上」。
- **差異處**：每對「不同的段」(a, b) 用字元層級的 `SequenceMatcher(None, a, b, autojunk=False).get_opcodes()`，每個非 `equal` 的 opcode 是一處差異，「詞對」是 `(a[i1:i2], b[j1:j2])`。
- 報：相同、不同、對不上的段數（兩邊分開）；差異處總數；依詞對的次數排序（同次數照詞對字串排），前 100 個寫進 `experiments/s2w/diff-top100.tsv`（只有詞對與次數，不放整句）。
- **人工判讀（預先寫死）**：所有差異處依（篇序、段序、opcode 序）排好，用 `random.Random(20261006).sample(差異處, 100)` 抽 100 處（每處的詞對與兩邊的整段，整段只放在 `~/.cache/shanjie/work/s2w/`）。main 逐處判為「MW 對」「S2n 對」「兩者都可」「都不對」。依據依序是：教育部《重編國語辭典修訂本》的字形與詞條、台灣通行寫法、條目主題的地區（例如中國大陸的機構名保留原名）。每處的判讀與理由寫進 `experiments/s2w/review-100.tsv`（詞對、判讀、理由，不放整句）。
- **關卡**：「MW 對」多於「S2n 對」，而且兩者的雙尾精確符號檢定（二項分布，p = 0.5）p < 0.05（「兩者都可」「都不對」不計）。沒過就停，回報判讀結果，不上 188。
- **真實資料上的作用**（同一批 2,000 篇，`gate1.py` 一併報）：
  - `mwdata.json` 抽查：群組 `IT` 存在、規則裡有 `zh-cn:账号; zh-tw:帳號;`；站上轉換表有「通用电气 => 奇異」（兩條都是 main 2026-10-06 在線上版本看到的，`Module:CGroup/IT` 用 `Item('原文', '規則')` 的寫法，`MediaWiki:Conversiontable/zh-tw` 一行一條 `*來源=>目標;`）。任一條不成立是停止條件（代表 `mwdata.py` 解析錯了）。
  - 同樣 2,000 篇另跑一次「不加站上轉換表與群組規則」的 `--mw`（只靠 zhconv-rs 內建表與條目本文規則），報和完整 `--mw` 結果不同的段數。0 是停止條件。
  - 引用到群組的次數（篇數與 G 參數個數），以及其中解不開的比例；解不開的 G 參數超過 5% 是停止條件。
- **五個字**：「喫」「着」「爲」「説」「裏」在兩種轉換的出現次數，以及 MW 輸出裡每百萬漢字的次數。任一個字在 MW 輸出裡超過每百萬漢字 5 次是停止條件（模型 E 的維基計數裡這幾個字多半是 0，所以不用倍數）。
- **七個殘留詞**（之后、由于、最后、然后、以后、此后、哪里）：兩種轉換的合計次數。
- **同時量**：
  - **速度**：2,000 篇的牆鐘與使用者 CPU 時間，`--mw` 對不加；推估 20 萬篇在 188（10 個 worker）的時間。
  - **規則外洩**：轉換後刪完標記的句子裡，含 `-{`、`}-`、`zh-cn:`、`zh-tw:`、`zh-hans`、`zh-hant` 的句數（zhconv-rs 遇到不認得的旗標會把整段規則原樣輸出）。超過總句數的 0.1% 是停止條件。
  - **多行規則**：條目裡跨行的 `-{…}-` 個數（zhconv-rs 抽全文規則的 regex 不跨行）。只報告。

### 4.2 重建（188，第一關過了才做）

- 照 S2n 的批次檔方式（P-core 親和性、絕對路徑、記憶體每 5 分鐘取樣）：`mwdata.py`；`build_counts.py --articles 200000 --mw --expected`；`build_tune.py --part wiki --mw`（wikitune-mw）；`tools/build_lm.py`（維基用新計數，口語用模型 E 的 `counts-colloquial3.pkl`，參數不變）→ 候選模型 W。
- 全部用 `S2_WORK=<S2w 的工作目錄>`（`build_counts.py`、`build_tune.py`、`tools/build_lm.py` 都讀這個變數），不蓋掉模型 E 的計數。模型 E 的 `counts-colloquial3.pkl` 先複製進這個目錄，複製前後比對 SHA-256。`build_lm.py` 不改。

### 4.3 驗收（模型 W 對模型 E，在整合當時 main 的樹上）

- **使用者的句子（合併條件）**：「大概十分鐘後到」聊天與書面都對；報分數差。「好」之後打 ㄅㄚ˙ 的第一名只報告（S2h 是否合併另外決定）。
- **守門（否決）**：dev302、打字測驗、**cvtune-native**，聊天與書面兩種設定，配對比較（修好／弄壞／McNemar 精確 p）。任何一格淨值為負且 p < 0.05 就否決。
  - dev302 與打字測驗是人工句子，和兩種轉換都無關。
  - **完整的 cvtune 偏向模型 E**：它的參考句是 `build_tune.py` 用 S2n 的 `convert()` 轉出來的（Common Voice 與混有簡體的 Tatoeba），兩種轉換不同的地方，參考答案站在 S2n 那邊。所以完整的 cvtune 只報告。
  - **cvtune-native**：只留原句經 `convert()` 前後完全相同的 cvtune 列（這些列的參考句不依賴任何轉換）。`experiments/s2w/cv_native.py`（executor 寫）照 `build_tune.py` 的讀檔與 `is_tune` 重算：一個子句只要出現在任何「轉換後有改變」的原句裡，就不算 native。報 native 的列數；少於 1,000 列是停止條件。
- **wikitune（只報告，有方向偏誤）**：
  - 舊的 wikitune（模型 E 的參考句，S2n 轉換）偏向模型 E；wikitune-mw（第 300,000–305,000 篇，MW 轉換，同樣的取樣方法與種子）偏向模型 W。兩組都報兩個模型的配對結果。
  - 同一種設定在兩組都淨值為負且 p < 0.05，就否決。
- **錯字回報檔**：報兩種設定的結果，不當關卡。
- **次數報告**（維基計數，模型 E 對 W）：
  - S2n 的七個殘留詞合計；超過 6,282（S2n 的 5% 關卡：改前 125,635 的 5%）是停止條件。模型 E 是 0，所以不用倍數。
  - 正確用法：皇后、王后、太后、公里、里長、鄰里、台灣、臺灣、干涉、只有、系統。
  - 「喫」「着」「爲」「説」「裏」：每百萬漢字的次數，任一個超過 5 是停止條件（和第一關同一個門檻；MW 的 zh-tw 不一定把這些換成台灣寫法，S2w 照字面不另加轉換，超過時交給使用者決定是否加一遍台灣用字）。
  - 「臺」「台」的次數。
- **沒有丟資料**：維基計數的段落數與總詞數，對模型 E 下降超過 1% 是停止條件。
- **模型大小**：位元組數、V、有二元組的前詞數、二元組條目數；超過 100 MB 是停止條件。
- **golden 與測試**：依賴模型的 golden 照它自己的指令重產（`s2-lm.txt`、`s2-lm-dev302-top1.tsv`、`s2r-probe-top1.tsv`，以及那時已合併的 S2h golden），每一列變動要能追到模型的改變；`unigram.txt`、`s1-*` 逐位元組不變。`cargo test`（debug、release）、`swift test`、C 冒煙測試在本機的模型 W 上全綠。
- **保留集**：片收尾時由 fresh verifier 跑一次，只回數字。

### 4.4 出貨

- 驗收全部通過後，模型 W 是 `model-v3`。先**問使用者**是否建立 GitHub Release `model-v3`（對外動作）；建好、下載回來比對雜湊相符，才改 `.sha256` 與所有 `model-v2` 的引用（清單照 S2n 第 2 節第 4 點，`grep -rn model-v2` 只剩歷史紀錄）、推分支、開 PR。`model-v1`、`model-v2` 永遠不刪、不覆蓋。
- **回滾**：revert 這片的 commit；`model-v2` 仍可下載。

## 5. 停止條件、預算、範圍外

- **停止條件**：第一關沒過；第 3.3 節的單向規則或優先順序檢查不過；第 4.1 節「真實資料上的作用」與「五個字」的任一條；規則外洩超過 0.1%；推估 188 整批超過 8 小時；實測或推估的峰值記憶體超過 48 GB；dump 裡找不到 `Module:CGroup/*` 或 `MediaWiki:Conversiontable/zh-tw`；守門否決；第 4.3 節列出的任何停止條件；「大概十分鐘後到」任一種設定不對。停下時報數字，不另外調參數或門檻。
- **預算**：executor 實作 1 回合加 1 次修正（第 3 節的程式與測試，加上 `gate1.py`、`cv_native.py`）；第一關的判讀由 main 做一次；188 跑一次完整重算。
- **範圍外**：口語語料的轉換；疊加層與詞庫增刪；zhconv-rs 0.5.0 或自己編譯；站上轉換表以外的線上設定（例如使用者偏好、`T=` 標題轉換）；網頁 n-gram（PLAN 另一項）；詞類與 KN 平滑（等這一片的計數）。

## 6. executor 不可以做的事

- 不連網（PyPI 安裝由 main 做；executor 用 main 建好的 venv）。不推分支、不開 PR、不建 Release。
- 不讀 `eval/holdout/`、`~/side-project/shanjie-private/`、使用者的 Discord 資料、學習檔或聊天紀錄。
- 不改 `core/`、`macos/`、`data/`、`eval/`；不安裝、不啟動 App，不呼叫 TIS 或 lsregister，不碰 `~/Library` 與鑰匙圈。
- 不改 `convert()`、`load_conv()` 與沒有 `--mw` 時的任何路徑。

## 7. 修訂一（2026-10-06）：MediaWiki 轉換之後補台灣字形

- **起因**：第一關（2,000 篇）停在「五個字」的停止條件：MW 輸出裡「爲」每百萬漢字 82.7、「裏」38.7、「説」23.7（門檻 5）；S2n 都是 0。MediaWiki 的 zh-tw 只做簡繁與用詞轉換，條目原文的大陸式繁體字形照留。其他項目都過：七個殘留詞 284 → 10；拿掉群組與站上轉換表後 29,705 段不同；兩條抽查都在；規則外洩 0.038%；不同處 89,464 處。
- **使用者決定（2026-10-06）**：補一層台灣字形。
- **做法**：`mwconv.convert()` 的輸出（zhconv-rs 轉完的整篇）再套一次 `experiments/s2/build_counts.py` 的 `VARIANTS`（爲→為、衆→眾、綫→線、麪→麵、僞→偽、裏→裡、峯→峰、羣→群、啓→啟、敎→教、着→著、説→說），直接引用同一個常數，不另抄一份。只換字形，不換詞；**不套** 臺→台（條目原文的「臺」保留）。三個呼叫者（`build_counts.py --mw`、`build_tune.py --mw`、`gate1.py`）都經過 `mwconv.convert()`，所以一起生效；`convert(..., groups=False, site=False)` 的對照組也套，兩組只差群組與站上轉換表。
- **檢查**：
  - 單元檢查：條目「这是爲了説明裏面」經 `mwconv.convert` 得到「這是為了說明裡面」；「臺北」保留「臺北」。突變：拿掉這一層，前一條必須失敗。
  - 第一關用同樣的 2,000 篇、同一個種子重跑，第 4.1 節全部項目重新報；「五個字」的停止條件照舊（每百萬 5）。100 處判讀用重跑後的抽樣。
- **範圍**：其他照第 1–6 節。預算：main 自己改（一行邏輯加測試），不另開 executor 回合。
