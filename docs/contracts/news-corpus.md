# 契約：新聞語料——能訓練的拿去訓練，不能訓練的拿來驗證

狀態：**過審**（2026-10-26）。修訂三（同日，實作時）：兩個抓取來源改成實際的抓法（§2.1、§3.1、§3.6、§6），依據是維護者當天的決定，見 §2.1。收尾審查 REVISE 三項都是少寫的規格（防護漏了聊天保留桶、私人命中拿掉後計數讀哪個檔、N01／N02 的連文表要用該格的 bigram 重建），照維護者對同類情況的決定（derisk-1015：收尾只差規格就直接進實作）補上，進實作。修訂二：第二次 REVISE 的兩項（防護的比對對象照 sj-lm-7 §2.4 全列、連文三元組的計數與建置指令）已改。修訂一：處理 plan-verifier 第一次 REVISE 的七項（整數權重、書面來源 B 補進比對與防護、程式改動的範圍、步驟順序與同一份 dump、`sources.tsv` 不動、`--rows` 的旗標禁令、newstune 當判讀集的依據）。

## 0. 依據（維護者的決定與查過的事）

- 維護者 2026-10-26：「書面的補充我認為還有新聞也可以用」，接著「那應該反過來不能訓練的用來驗證，能用的拿去訓練吧」。選擇題選「退役，用報社新聞重抽」。
- **訓練側**：
  - 政府新聞稿全部可以進訓練。依據是著作權法第 9 條第 1 項第 1 款與第 2 項（公文包括新聞稿），範圍包括行政院「本院新聞」。
- **條款查核**（2026-10-26，逐站讀官方條款原文；摘要記在研究紀錄同日一節）：
  - 中文維基新聞是 CC BY 4.0，2025-01-30 以前的內容可以選 CC BY 2.5。它是查過的新聞來源裡，唯一明文允許再利用、包括改作的。
  - 報社大多明文禁止重製，或禁止拿去訓練 AI。
  - 智慧局的生成式 AI 著作權指引，官方文字目前停在公聽會紀錄（正式全文還沒找到）。報導轉述的建議是：政府開放資料、單純的新聞報導、過期著作與 CC0 以外的內容都先取得授權。官方文字沒有把統計模型另外處理。
- 維護者看過查核結果後的選擇題：
  - 驗證用的新聞改從**一家報社（下稱報社 A）＋中文維基新聞**抽。報社 A 的風險維護者接受：只放在本機與評測機器，不進 repo、不訓練、不散布。
  - 維護者同日決定：沒有授權、也就不必標出處的來源，公開的地方（repo、commit、PR、Release、App、Discord）一律不寫名字。報社 A 與 §3.1 的書面來源 B 的名稱、網址與抓取細節只記在維護者本機的 `.agent-local/news-sources.md`。
  - 中文維基新聞也**加進書面訓練**。
  - 書面來源 B（一個中文網站的自產文章）也收進訓練：維護者決定收、承擔風險。
  - `newstune` **當書面判讀集之一**（選擇題）。研究規則「書面判讀只用維基判讀集與保留集新聞半邊」改成也含 newstune，維護者本機的規則已經跟著改。
  - main 定的做法：維基新聞依文章切成兩邊，驗證用的那份文章不進訓練（§3.2）。
- 現況：
  - 保留集的新聞半邊（現役 shard-1，以及 `next/` 裡的 shard-2、3）約八成是機關新聞稿，其餘是維基新聞與 PeoPo（`docs/contracts/holdout-refresh.md` §2）。新聞稿一旦進訓練，這些就不再是沒看過的文字。
  - 行政院 294 句的新聞稿驗證集也一樣。

## 1. 要做到什麼、怎麼看到

- **驗證側（§2）**：
  - 做出新的私人新聞驗證集：開發用的 `newstune`（2,000 列），以及保留集新聞半邊的三份（各 150 列）。
  - 舊的新聞半邊與 294 句退役。
  - 看得到的地方：`eval/README.md` 有新集的雜湊與使用紀錄（只有雜湊與列數，沒有文字）；`--set holdout` 只讀口語半邊（n 150）；新聞半邊由 fresh verifier 用 `--rows` 量（§2.4）。
- **訓練側（§3）**：
  - 政府新聞稿、維基新聞（訓練那一份）與書面來源 B，當成通語 SJ-LM-7（規模待定）的書面語料。
  - 量法：在 SJ-LM-7 的階段 3 之後加一段「階段 3b」，格點事先固定（§3.3）。
  - 看得到的地方：研究紀錄有格點每一格對 B6 與 main 的數字，選定的那一格進 SJ-LM-7 定案。
- **順序**（每一步的輸入都在前一步產生）：
  1. 程式改動（§3.6）合併。
  2. 抓取並凍結 §3.1 的三個訓練來源，以及一份 `zhwikinews` dump（記日期與 SHA-256；驗證側與訓練側都用這一份）。
  3. 驗證側 §2.1–§2.3：抓報社 A、產生池、對訓練文字比對、抽樣。
  4. §2.4 換手。
  5. 訓練側 §3.3–§3.5 的計數、建置與量測（計數時開 `--holdout-guard`，比對對象包含步驟 3 的全部私人新聞列）。

## 2. 驗證側

### 2.1 來源與份量

| 集合 | 報社 A | 維基新聞（驗證那一份文章） | 合計 |
|---|---|---|---|
| `newstune`（開發層，可以用來調參與選臂） | 1,800 | 200 | 2,000 |
| 保留集新聞半邊 shard-1、2、3（每份） | 120 | 30 | 150 |

- **報社 A**（抓取細節在 `.agent-local/news-sources.md`）：
  - 時間窗：2026-09-01～2026-10-25 發布的中文新聞。
  - 抓法（修訂三）：robots.txt 列的 sitemap 只回溯約 4 天，所以改成依文章編號逐日列舉文章頁。
  - 速率與 User-Agent 照維護者 2026-10-26 的決定：每秒最多 10 個請求、不換 IP。遇到 429 照 Retry-After 等候，再把速率減半；遇到 403，或連續 20 個錯誤，整批停下。
  - 只讀文章頁，不碰 robots.txt 禁止的路徑。列舉腳本本身不讀 robots.txt；列舉的文章路徑在開抓前人工核對過，不在禁止之列。
  - 列舉腳本是一次性的，不進 repo（細節在 `.agent-local/news-sources.md`）。
  - 不收評論、專欄、轉載外電，也不收該社記者以外署名的文章（例如讀者投書）。
- **維基新聞**：
  - 用 §1 步驟 2 凍結的那一份 `zhwikinews` dump。
  - 驗證那一份文章：`wikinews.py` 的 `is_validation`（§3.2），由 `wikinews.py export --split val` 匯出。
  - 只收主題在台灣、原本就用繁體寫的條目（同 holdout-refresh §2）。
- **每單位上限**：一篇文章 ≤ 2 個子句。
- **互斥**：同一篇文章只會進一個集合。三份保留集與 `newstune` 互斥，固定種子 20261026。

### 2.2 切句、過濾、讀音（照 holdout-refresh §3，數字不改）

- 切句照 holdout-refresh §3：4–25 字的子句，前文是同段落的前一個子句，格式 `前文|句子|讀音`。
- 過濾照同一節：
  - 簡體專用字、入侵詞表、`CANTO_CHARS`；
  - 拉丁字母與數字；
  - 句子欄的非漢字；
  - 樣板句；
  - 電頭（「（○○社記者…電）」）與記者署名、日期行。
- 讀音用 `tools/readings.py`。UNREADABLE 與 GAP 的列不收；CHECK 列由抽樣的 agent 決定，定不了就丟掉並計數。
- 讀音抽查：隨機 40 列，錯誤率超過 5% 就先修規則。
- 份量檢查：池小於需求的 3 倍就停下來回報。補救是把時間窗往前延，不是放寬過濾。

### 2.3 抽樣程序（照 holdout-refresh §4）

- **fresh mech-executor**：
  - 負責抓取、清理、過濾與產生讀音。
  - 過濾後的池寫成列檔，放在它自己的暫存資料夾，只回報每一步的計數。
- **main**：
  - 用 `tools/holdout_guard.py report --rows <池>` 比對下列文字：
    - 所有公開評測集；
    - 現役與 `next/` 的保留集口語半邊；
    - SJ-LM-7 的全部訓練文字（維基、口語池、`synth.txt`、PTT、g0v、字幕）；
    - §3.1 的三個訓練來源：新聞稿、`wikinews.py export --split train` 匯出的維基新聞訓練那一份、書面來源 B。三份都是純文字，走 `--text`；維基新聞**不走** `--wiki`（整份 dump 含驗證那一份，會讓池裡每一列維基新聞都命中）。
  - 比對在 Mac 以外的機器上做，main 只看命中的列號與計數。
- **mech-executor**：
  - 丟掉命中的列，回報「重疊丟掉 N 句」，這一行出現在抽樣之前。
  - 用固定種子抽樣，把各集合寫到 `~/.cache/shanjie/news-eval/`：`newstune.txt`、`holdout-news-{1,2,3}.txt`、`sources.tsv`（每列的網址或條目 revid）。
- **fresh agent 盲後處理**，只回數字：
  - 拼不出 0 列（CLI `--rows` 跑完，n 等於列數）；
  - 讀音和詞庫第一名不同的列數，只報；
  - 比對自檢：命中數等於列數。
- main 不讀這些檔的內容。commit 只記 SHA-256 與列數。

### 2.4 保留集的換手（只動 `eval/`、`docs/`、`LICENSES/`、`site/`、`tools/kpi.py`、`tools/bench.py` 與它們的測試）

- 刪掉的檔（`git rm`，main 不打開檔案內容）：
  - `eval/holdout/news.txt`；
  - `eval/holdout/next/shard-{2,3}/news.txt`。
- `eval/holdout/next/sources.tsv` 原封不動：新聞那幾列留著當退役紀錄，雜湊不變。`eval/README.md` 的出處說明加一句「新聞的列已退役」。
- 退役：行政院 294 句的新聞稿驗證集。檔案在 repo 外，研究紀錄寫明它從這天起是訓練語料。
- `--set holdout` 從此只讀 `talk.txt`（n 150）。`load_set` 本來就讀 `eval/holdout/` 頂層所有的 `.txt`，不必改程式。
- **新聞半邊**：
  - 照保留集規則，只在推 `v*` tag 前量一次。
  - 由 fresh verifier 在評測機器上用 `--rows ~/.cache/shanjie/news-eval/holdout-news-1.txt --profile formal --rowstats` 量基準與新版。檔案用前核對雜湊，量完刪掉 rowstats 與那台機器上的副本。
  - **旗標禁令（流程規則，不是技術防護）**：`--rows` 不像 `--set holdout` 會拒絕逐列輸出。量新聞半邊的 verifier，brief 明寫禁止 `--dump`、`--dump-words`、`--tinylm-scores`，只准 `--rowstats`（檔內只有 `[0-9\t\n]`），照 holdout-refresh §6 的做法。`docs/verification.md` 與 `eval/README.md` 的新聞半邊段落寫同一句。
- **改的文件**：
  - `docs/contracts/kpi.md` §3：口語半邊＝`--set holdout` 全集（聊天設定）；新聞半邊＝私人檔（書面設定）。全集合那兩行不再存在；v2 JSON 的 `holdout.chat`＝口語半邊、`holdout.formal`＝新聞半邊（私人檔），各自帶 `rows_sha256`，同 kpi.md §3。
  - `tools/kpi.py` 的表：`rows_sha256` 換了就分欄（現有行為）。測試：`tools/test_kpi.py` 現有的分欄測試涵蓋兩份新聞半邊換檔；補一條「口語半邊的雜湊不變、新聞半邊的雜湊換了」的案例。`tools/bench.py` 的保留集表原本只分 chat／formal 兩欄，也改成依 `rows_sha256`（沒記就依 n）分欄，欄位依第一次出現的順序，並補一條畫表層級的測試。
  - `docs/verification.md` 與 `eval/README.md` 的保留集段落。
  - `LICENSES/data.md` 的新聞列拿掉。
  - 維護者本機 `CLAUDE.md` 的「評測與資料」與「研究」的書面判讀規則，由 main 改（後者已改）。
- 使用次數：新的新聞半邊從 0 開始，口語半邊照舊累計。換份前後的數字不跨份比，表依 `rows_sha256` 分欄。
- `newstune` 是開發層，也是書面判讀集之一（§0）：可以用來調參與選臂；只報數字，不讀列的內容。

## 3. 訓練側（通語 SJ-LM-7 的階段 3b）

### 3.1 語料

| 來源 | 內容 | 單位 |
|---|---|---|
| 政府新聞稿 | 行政院「本院新聞」，加上 holdout-refresh §2 列的 23 個 data.gov.tw 機關新聞稿資料集，抓當下資料集裡的全部 | 一篇新聞稿 |
| 維基新聞 | `zhwikinews` dump 裡驗證那一份（§2.1）以外的全部條目，照 `build_counts.py` 的條目處理（`TEMPLATE`、`MARKUP`、`SENT`、轉換、漢字段） | 一個條目 |
| 書面來源 B | 一個中文網站的自產文章（維護者 2026-10-26 決定收、承擔風險）；不收標「轉載自」、業配、標「由 AI 協助生成」的文章；正文拿掉廣告分隔行 | 一篇文章 |

- 清理：
  - 新聞稿拿掉聯絡人、發布日期與附件這類欄位行；
  - 樣板句（整個新聞稿池裡出現 ≥ 5 次的子句）只算一次。
- 書面來源 B（修訂三）：從網站公開的 WordPress REST API 讀文章（`news_fetch.py wp`），不刮網頁。抓取遵守 robots.txt，只用一條連線、每 2 秒最多一個請求（robots.txt 要求更慢就照它），User-Agent 照維護者同日的決定。業配作者的文章整位排除，標記規則放在本機設定檔。
- 快照：三個來源各凍結成一份，記 SHA-256，之後不再抓。

### 3.2 維基新聞的切法

- 用條目名 UTF-8 的 SHA-256 第一個位元組切：< 32 是驗證那一份，其餘是訓練那一份。
- 這個切法在抓任何資料之前 commit，§2 與 §3 共用同一個函式（§3.6）。

### 3.3 格點（事先固定，6 格）

| 臂 | 書面連文（trigram）表 | bigram（通語）、辨類、承接 |
|---|---|---|
| N00 | WC（現行選定） | B6 |
| N10 | WC＋新聞 ×1 | B6 |
| N01 | WC | B6＋新聞 ×1 |
| N11 | WC＋新聞 ×1 | B6＋新聞 ×1 |
| N02 | WC | B6＋新聞 ×2 |
| N12 | WC＋新聞 ×1 | B6＋新聞 ×2 |

- 「新聞」＝§3.1 的三個來源（新聞稿、維基新聞訓練那一份、書面來源 B），每一格三個都在、權重相同。每個來源各一個計數檔，`build_lm.py --corpora` 各給一個整數權重（例如 N01：`counts-200000.pkl:1 counts-colloquial3.pkl:5 counts-g0v.pkl:5 counts-ptt-push.pkl:5 counts-ptt-body.pkl:1 counts-press.pkl:1 counts-wikinews.pkl:1 counts-srcb.pkl:1 --prune 6`）。
- 權重是乘在期望計數上，剪枝門檻 6 也是對加權後的計數判：×1 的新聞詞對要在新聞裡出現 ≥ 6 次才留，×2 的要 ≥ 3 次。折扣 D 0.75 不變。

- **每一格的連文表都用該格自己的 bigram 建**（連文表綁 bigram 的詞 id 與雜湊）：
  - N00：沿用現行的 WC 表（建在 B6 上）。
  - N01、N02：WC 的配方（`--corpora counts-200000.pkl:1 counts-colloquial3.pkl:5`）用 `--lm <該格的 bigram> --thresholds 1` 重建，再 `entropy_prune.py --keep 7700964`。
  - N10、N11、N12：WC＋新聞的配方（§3.6）用 `--lm <該格的 bigram>` 建，同樣剪到 7,700,964 條。
  - 驗收：每一格 `.sjt3` 檔頭記的 bigram SHA-256 等於該格的 `bigram.sjlm`。
- 加進 bigram 的臂要重建辨類與承接，並把會意的 `.sjtl` 重新匯出、綁定新的 bigram（照 sj-lm-7 §2.6 的量測做法）。
- 書面 KN 的設定照階段 3 選定的那一臂。

### 3.4 洩漏防護

- 三個訓練來源（新聞稿、維基新聞訓練那一份、書面來源 B）在**每一次計數**時都打開 `--holdout-guard`：bigram 的期望計數（`build_counts_text.py`）與連文的三元組計數（`count.py text`，§3.6）都要。
- 比對對象和 sj-lm-7 §2.4 相同，再加上本片的私人新聞列，每一份都在指令裡明列，不靠預設：
  - 保留集口語半邊：現役 `eval/holdout/talk.txt` 與 `next/shard-{2,3}/talk.txt`；
  - 公開評測與調參集：dev302、打字測驗（聊天、書面）、錯字回報、cvtune-clean、wikitune-clean、動漫五集、名單檢查集、資訊詞包檢查集、網路用語三集、輕聲檢查集；
  - 私人新聞列：`newstune` 與 `holdout-news-{1,2,3}`；
  - 私人的 discordtune 與聊天保留桶：照 sj-lm-7 §2.4，在評測機器上用同一個比對器（`dedupe_eval.py --min-len 6`，只比句子 6 字以上的列）**拿掉命中的句子**，只回命中數。
- **計數讀的檔**：每個訓練來源先在評測機器上跑 `dedupe_eval.py --min-len 6`（對 discordtune 與聊天保留桶），輸出 `<來源>.dedup.tsv`；之後所有 `build_counts_text.py` 與 `count.py text` 都只讀 `.dedup.tsv`，再加上面公開集合與私人新聞列的 `--holdout-guard`。記下的命中數＝`.dedup.tsv` 比原檔少的行數。
- 每個來源、每個集合的命中數（跳過的段數）在階段 3b 的任何分數之前記進研究紀錄。

### 3.5 量法與選法

- 每一臂對 N00、對 main 各量一次（同一台 VM、同一種 CPU）：
  - 出貨標準四條；
  - wikitune-clean 與 `newstune`（書面）；
  - 網路用語、名單、動漫各檢查集。
- **能選的臂**：
  - 對 main 過「兩臂共同關卡」（sj-lm-7 §3）；
  - 對 N00：`newstune` 書面淨值 > 0；
  - 對 N00：wikitune-clean 不顯著變差（精確 McNemar p ≥ 0.05 或淨值 ≥ 0）；
  - 有改到 bigram 的臂，對 N00 的 discordtune 也不顯著變差。
- 選法：能選的臂裡推薦 `newstune` 淨值最大的那一臂，數字給維護者選。沒有能選的臂，就是照登記不過：沿用 N00，報給維護者。

### 3.6 程式改動（executor；走程式改動的關卡：`/code-review` ＋ fresh verifier；`experiments/` 的測試只在本機跑）

- `experiments/sj-lm-7/wikinews.py`：
  - `is_validation(title)`：條目名 UTF-8 的 SHA-256 第一個位元組 < 32。
  - `export --dump D --split train|val --out F`：條目處理呼叫 `build_counts` 既有的那一份（`TEMPLATE`、`MARKUP`、`SENT`、轉換、漢字段），不另抄；一段一行輸出給 `build_counts_text.py` 與 `holdout_guard.py --text`。
  - 單元測試（合成的小 dump）：同一個條目名兩次判定相同；train 與 val 互斥、聯集是全部條目；固定門檻（一個已知雜湊第一個位元組 < 32 與一個 ≥ 32 的條目名）；`--split train` 的輸出不含任何 val 條目的文字。
- `experiments/sj-lm-7/news_fetch.py`：
  - 新聞稿：行政院「本院新聞」與 holdout-refresh §2 的 23 個 data.gov.tw 資料集，用官方下載點，不刮網頁。
  - 一般文章頁：網址清單或 sitemap 由參數給（報社 A 與書面來源 B 的網址只放 `.agent-local`），每 2 秒最多一個請求，抓之前讀 robots.txt、被禁止的路徑不抓。
  - `wp`（修訂三）：WordPress REST API 的文章列表，網址與排除規則由參數給。書面來源 B 用這個。報社 A 不用這個工具，用 §2.1 的一次性列舉。
  - 輸出 `單位\t段落` 的 TSV；排除規則（欄位行、樣板句 ≥ 5 次只算一次、文章層的排除標記）各有單元測試。
- **bigram 的計數**：用既有的 `build_counts_text.py --no-trad-tw-phrases --expected`（B 臂的三份計數詞表）加 `--holdout-guard`，不改程式。每個來源一個計數檔。
- **連文的三元組計數**（N10、N11、N12 用）：
  - 用 `experiments/derisk-1015/e1/count.py text --tag <來源> --no-trad-tw-phrases --tri-min 0.01`，計數詞表同上，和 WC 表的三元組計數同一套設定（研究紀錄 2026-10-26 階段 1 的 trigram 一節）。
  - executor 替 `count.py text` 加 `--holdout-guard [ROWS…]`，重用 `holdout_guard` 的比對器：預設關，關著時輸出逐位元組不變；開著時命中的漢字段不計數，回報跳過幾段。單元測試照 holdout-refresh §8 的計數開關那幾條。
- **連文表的建置**：
  - `build_models.py tri --counts <unmapped 計數> --lm <該格的 bigram> --thresholds 1 --corpora counts-200000.pkl:1 counts-colloquial3.pkl:5 counts-press.pkl:1 counts-wikinews.pkl:1 counts-srcb.pkl:1`；
  - 接著 `entropy_prune.py --keep 7700964`，和 WC 表一樣。
  - 兩支都是既有工具（`b6a3c8c8` 起有 `--corpora`），不改。

## 4. 授權

- 新聞稿：通語、連文的 `LICENSES/data.md` 列與 Release 說明，來源加「政府機關新聞稿（著作權法第 9 條；資料集另依政府資料開放授權條款第 1 版顯名）」。
- 維基新聞：同一列加「維基新聞（CC BY 4.0）」並署名「維基新聞」。
- 書面來源 B：不列名字（§0）。App 關於頁的模型資料來源跟著 SJ-LM-7 的 PR 2 一起改。
- 報社 A 不進 repo，`LICENSES/` 沒有它的列；研究紀錄只寫「報社新聞（只在本機驗證，不訓練、不散布）」。

## 5. 停止條件

- 份量檢查不過，或讀音抽查改了規則仍然超過 5%。
- 抽完之後還驗到重疊。
- 報社 A 或書面來源 B 的條款或 robots.txt 在抓取期間改成禁止一般爬蟲，或站方要求停止：停下來，已抓的刪掉，問維護者。
- 格點沒有能選的臂：報數字，不開子片救同一個格點。

## 6. 預算

- GCP ≤ NT$800：四次 bigram 重建，加上六格的量測。每一步用滿核心，開跑 5 分鐘內看 load。
- 抓取在 Mac 或評測機器上做，照 §2.1、§3.1 的速率。

## 7. 範圍外

- 其他查過的網站不收。書面來源 B 的影片字幕等語音辨識試點裝好工具再另案。公共媒體的一般內容與 TED 演講查過，條款不允許，不收（公共媒體標 CC BY 的少數影片另見 YouTube 契約 §3a；查核紀錄在研究紀錄 2026-10-26）。
- 其他報社：條款明文禁止，不收。
- 會意 SJ-SR-7 的語料不加新聞；要加另案量。
- 立法院發言（lytune）照舊只當警示。

## 8. agent 不可以做的事

- 不讀維護者的私人資料夾、學習檔、`~/Library`、鑰匙圈。
- 不登入任何網站、不建帳號、不發信或發文、不推 tag。
- 抓取遵守 robots.txt 與 §2.1、§3.1 的速率，不刮被禁止的路徑。
- main 以外的 agent 不把任何驗證列的文字貼進報告、對話、研究紀錄、commit 或 PR。
