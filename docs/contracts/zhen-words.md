# 中英混打：擴充英文單字表與正式寫法（zhen-words）

狀態：READY（2026-10-14 收尾審查；plan-verifier 第 1、2 次 REVISE 逐項處置在 §9）。接在 `docs/contracts/zhen-inline.md` 合併之後實作（同一個英文窗與同一個顯示量測）；資料的部分（§2.1 補充表、§2.2 維基計數）可以先做。

## 0. 起因

- 維護者 2026-10-14：「中英混打的英文詞庫還是有點太少了」「常見的AI廠商名稱都沒有」。選擇題：補詞來源「兩者都要」（自己整理的補充表＋大型詞表），大小寫「補充表記正式寫法」；接著問「你看也能用wiki擴增嗎？」——大型詞表改用中文維基裡夾在中文之間的英文字（比一般英文詞頻表更貼近中文裡會混打的字，也帶著最常見的大小寫）。
- 量過（出貨的單字表 `3352d050…`，82,730 詞，只有 SymSpell 的 `^[a-z]{3,32}$`）：常用的技術、品牌與 App 名稱（`eval/english/probe-common.txt`，208 個，三個字母以上）缺 89 個（例如 github、json、api、macos、xcode、pdf、html、ios、macbook、instagram、spotify、shopee）；AI 廠商與產品（`eval/english/probe-ai.txt`，56 個）缺 27 個（openai、chatgpt、gpt、deepseek、qwen、kimi、nvidia、huggingface、ollama、midjourney、elevenlabs、groq、langchain、notebooklm、suno 等）。本來就是英文單字的名字有（anthropic、claude、gemini、mistral、grok、perplexity、copilot、cursor）。
- 補進去也只能得到小寫、首字大寫、全大寫三種，OpenAI、ChatGPT、iPhone、GitHub、macOS 打不出來。

## 1. 要做到什麼、怎麼看到它有效

- 句首或中文後面打 `chatgpt`、空白：英文窗第一項是 `ChatGPT`，選了留在組字區（照 zhen-inline），Enter 送出「…ChatGPT」。`openai` → `OpenAI`、`github` → `GitHub`、`iphone` → `iPhone`、`macos` → `macOS`、`nvidia` → `NVIDIA` 同理。
- 一般英文字照舊：`rust` 的英文窗仍是 `rust`、`Rust`、`RUST`。
- **誰、怎麼看到**：維護者實機（§5.6）打上面那句；量測（§4）證明打中文時的誤觸與組字區閃動沒有超過門檻。

## 2. 資料

### 2.1 補充表 `data/english-extra.txt`（進 git，CC0，main 手寫）

- 一行一個**正式寫法**（`^[A-Za-z]{3,32}$`），`#` 開頭的行是分類註解，空行略過；鍵＝小寫。同一個鍵出現兩次就是錯（建置失敗）。
- 內容（約數百到一千多詞，之後可以隨時加）：AI 廠商、產品與模型名（OpenAI、ChatGPT、DeepSeek、Qwen、Kimi、NVIDIA、HuggingFace、Ollama、Midjourney、Claude、Gemini、Anthropic…）、開發工具與格式（GitHub、JSON、API、macOS、iOS、Xcode、PDF、HTML、YAML、npm…）、常見品牌、裝置與 App（iPhone、MacBook、AirPods、Instagram、Spotify、Shopee、Uber…）。
- 正式寫法以官方網站或官方文件的寫法為準，main 逐項寫；不確定的不收。這份表是我們自己寫的，授權 CC0（`LICENSES/data.md` 註明）。

### 2.2 維基表 `data/english-wiki.txt`（進 git，CC BY-SA 4.0）

- **輸入**：188 上的 `zhwiki-20261001-pages-articles.xml.bz2`（model-v5 用的同一份，SHA-256 `5db9052e…7e2f`，`experiments/model-v5/README.md`）。文章文字用 `experiments/s2/build_counts.py` 的 `articles()`（和計數模型同一套去標記）。
- **計數**（`tools/build_english_wiki.py`，在 188 跑）：每篇文章裡的英文字＝前後都不是 `[A-Za-z0-9]` 的最長 `[A-Za-z]+`（`GPT4`、`x86` 這種黏著數字的整個丟掉），長度 3–32。對每個小寫鍵記：出現的文章數（df）與每種大小寫的次數。
- **正式寫法**：次數最多的大小寫；同次數取位元組序最小的。
- **輸出**：df ≥ `WIKI_MIN_DF` 的鍵，每行一個正式寫法，依小寫鍵排序；檔頭幾行註解寫輸入雜湊與 `WIKI_MIN_DF`。`WIKI_MIN_DF` 由 §4 決定。
- 計數本身（每個鍵的 df 與次數）是中間檔，放 repo 外（188 與 `~/.cache/shanjie/`），不進 git。

### 2.3 合併成出貨的單字表

- `tools/build_english_words.py` 多兩個輸入 `--extra FILE`、`--wiki FILE`（預設 `data/english-extra.txt`、`data/english-wiki.txt`）：三份的鍵取聯集；**顯示寫法的優先序**：補充表 > SymSpell（小寫）> 維基的正式寫法。也就是說 SymSpell 裡有的字（例如 `song`、`the`）就算在維基多半大寫，仍顯示小寫；要大寫的品牌放進補充表。
- 輸出格式：一行一個顯示寫法，依小寫鍵的位元組序排序；`data/english-words.txt.sha256` 換成新的雜湊（建置照舊核對）。SymSpell 的下載與輸入雜湊不變。
- 鍵仍然只有 `[a-z]{3,32}`（顯示寫法小寫後就是鍵），所以 zhen-mixed §2.2「純字母、≥ 3 由查表保證」不變。

## 3. 引擎與英文窗

- **載入規則**（取代 zhen-mixed §2.6 與 `load_english` 文件註解的「`[a-z]{3,32}`」）：每一行符合 `^[A-Za-z]{3,32}$`（LF、不含 BOM、沒有空行，照舊）；鍵＝那一行的 ASCII 小寫；**同一個鍵出現兩次（例如 `rust` 和 `Rust`）整檔拒絕**；引擎存鍵，另存顯示寫法（和鍵相同的不另存）。上限放寬成 **1,000,000 行、32 MiB**（`ENGLISH_MAX_LINES`、`ENGLISH_MAX_BYTES`；一行最多 33 位元組，所以位元組上限不會比行數先到）。§4 裡超過上限的格不量、視為不可行。任何一項不符照舊整檔失敗、先前的表不變。
- **英文窗**（zhen-mixed §2.3、zhen-inline 的 token 後空白）：清單＝［顯示寫法、小寫、首字大寫、全大寫］依序去重（保留第一次出現的）；殘餘音節在詞庫時的中文項照舊接在最後。一般字（顯示寫法＝小寫）因此和現在逐項相同。
- 其他規則（觸發、覆寫次數、zhen-inline 的條件 D、Enter、token）不變；D 的「影子是單字表某個字的前綴」照小寫鍵算。
- `docs/contracts/s3a.md` 中英混打的規則表、`docs/contracts/zhen-mixed.md` §2.3 與 §2.6、`docs/data-files.md`、`LICENSES/data.md`（補充表 CC0、維基表 CC BY-SA 4.0：署名照 ACG 詞包用維基資料的那一列）、`scripts/build-app.sh` 的署名檔，同一個 PR 更新。

## 4. 量測（main，188；數字進研究紀錄）

- 工具與集合照 zhen-mixed §4.1–§4.3 與 zhen-inline §3：`english-sim`，cvtune、wikitune 選門檻；dev302、typing76 驗證。
- **格點**：`WIKI_MIN_DF ∈ {不收維基, 1000, 300}`，每格都含 SymSpell 與補充表（〔修訂，2026-10-14〕原本還有 100、30、10；§7 的雜訊停止條件觸發，改了四輪計數規則後抽樣 df100 仍過半是雜訊、df300 以上通過，照「加大 df」拿掉這三格，研究紀錄同日）。每格量：
  - 誤觸：`--p 0.02 --repair overwrite --repeat 5` 的 per_1000_mean，與 `--p 0`；
  - 組字區顯示（zhen-inline）：`--inline` 的 `inline_per_1000_mean`；
  - 召回（`--recall`，分母＝那一格的全部詞條，分子＝英文窗開著且第一項等於顯示寫法）。
- **`english-sim --recall` 的改法**（executor）：每一行先轉小寫再逐字母打（`Key::ch` 只收鍵盤上的小寫字母），成功＝英文窗開著且 `candidates[0]` 等於**表裡那個鍵的顯示寫法**；重算覆寫次數也用小寫。新選項 `--probe FILE`（和 `--recall`、`--words 表` 一起用）：打 FILE 裡的每個字（一行一個，大小寫不拘、轉小寫打），鍵不在表裡的算失敗、另計第五類「不在表裡」；引擎與重算一致的檢查只對在表裡的字做。輸出多 FILE 的 SHA-256 與第五類；不加 `--probe` 時欄位不變（數值因為改用顯示寫法比對，對全小寫的表也不變）。
- **兩份測試清單**：`eval/english/probe-common.txt`（208 個，SHA-256 `82fa863b…`）、`eval/english/probe-ai.txt`（56 個，`8c21e941…`）：§0 用的字，三個字母以上、去重、排序，main 選的（CC0）。
- **可行格**：兩個集合的誤觸 per_1000_mean ≤ 0.5、`--p 0` 是 0、`inline_per_1000_mean` ≤ 2（zhen-inline 的門檻）、召回 ≥ 95%。取可行格裡**詞數最多**的。
- 選定後 dev302、typing76 各跑一次（同 zhen-mixed §4.4 的最後一步），任一不過就是停止條件。
- **分開報的召回**（不是門檻；選定的表）：補充表的全部詞條、`eval/english/probe-common.txt`、`eval/english/probe-ai.txt` 三份各報「英文窗開著且第一項等於顯示寫法」的比例與 zhen-mixed §4.5 的四類計數（覆寫 0 次、覆寫 1 次且殘餘在詞庫、覆寫 ≥ 2 次且殘餘在詞庫、殘餘不在詞庫）；打不出來的字逐一列進研究紀錄（照現行觸發規則，例如 `npm` 沒有覆寫、`api`、`json` 覆寫 1 次而殘餘在詞庫，都不會開英文窗〔推算，未量〕）。

## 5. 驗收

1. FFI（`core/src/ffi.rs` 的拒絕清單）：`upper case` 那一條改成「同鍵不同寫法（`rust`／`Rust`）拒絕」；兩條上限改成新的值（超過 32 MiB、超過 1,000,000 行，仍然 `rust` 放第一行）；另加「含正式寫法的表（`ChatGPT`、`rust`）可以載入，而且 `chatgpt` 會開英文窗」。
2. `tools/test_build_english_words.py`：合成輸入測三份合併、優先序（補充表蓋過 SymSpell、SymSpell 蓋過維基）、排序、重複鍵失敗、壞行失敗；`tools/test_build_english_wiki.py`：合成文章測切字規則（黏著數字的丟掉、長度界線）、df 與正式寫法（含同次數）。突變（優先序反過來、df 改成次數）各讓一個測試失敗。
3. 引擎測試（`core/tests/engine_english.rs`，`eval/golden/english-tiny.txt` 加幾個有正式寫法的行）：`chatgpt ␣` 的清單是 `["ChatGPT","chatgpt","Chatgpt","CHATGPT"]`；`rust ␣` 仍是 `["rust","Rust","RUST"]`；顯示寫法是首字大寫的字（例如 `Claude`）清單是 `["Claude","claude","CLAUDE"]`；選了正式寫法之後組字區與送出的是正式寫法。突變「不去重」與「顯示寫法不放第一」各讓一個斷言失敗。
4. `cli/tests/english_sim.rs`：合成的小表（含 `ChatGPT`、`rust` 與一個只會覆寫 0 次的字）跑 `--recall` 與 `--recall --probe`（probe 含一個不在表裡的字），預期的開窗數、第一項相符數、五類計數寫死在測試裡；突變「比對小寫而不是顯示寫法」讓它失敗。
5. 既有的中英混打測試全過（一般字的清單逐項不變）。
6. §4 的可行格存在、選定值寫回腳本（`WIKI_MIN_DF`），`data/english-words.txt.sha256` 是選定那份的雜湊；CI 全綠。
7. 保留集：fresh verifier 照規則跑一次（單字表只影響中英混打，中英混打預設關，出貨設定的結果應和 main 逐位元組相同）。
8. 實機（維護者）：開中英混打，打 `我在用` ＋ `chatgpt` ＋ 空白 → 第一項 `ChatGPT` → 1 → Enter，送出「我在用ChatGPT」；再打 `rust` ＋ 空白，第一項仍是 `rust`。

## 6. 範圍外

- 兩個字母的字（AI、OK、UI、PR）：現在的觸發規則下兩個字母不會覆寫兩次，另外設計。
- 照現行觸發規則（zhen-mixed §2.2）打不出來的字（例如 `npm`、`api`、`json`）：這一片收進表、量出清單（§4），改觸發規則另外設計。
- 含數字的字（GPT4、PS5、K8s）、含符號的字（C++、Node.js）。
- zhen-inline 的組字區在打字時就顯示正式寫法（現在照打的字母顯示，按空白才給正式寫法）：之後另問。
- 使用者自己加英文字、學習英文字的選擇順序。
- 英文維基、wordfreq 等其他詞頻表：中文維基不夠時再評估。

## 7. 停止條件

- §4 沒有可行格（連「不收維基」都不過，表示補充表本身造成誤觸）：停下來回報是哪些字。
- 驗證集（dev302、typing76）不過。
- 維基計數的結果大部分是雜訊（main 抽 100 個看，過半不是會在中文裡混打的字）：停下來改規則（例如加大 df 或排除拼音），寫研究紀錄後再量。

## 8. 負責人、順序、預算、限制

1. main：寫補充表（§2.1）；寫 `tools/build_english_wiki.py` 與它的測試，在 188 跑維基計數（§2.2），產生各格的維基表。
2. executor（zhen-inline 合併之後，從 main 開分支）：§2.3 合併、§3 載入與英文窗、FFI、§4 的 `english-sim` 改法與 `cli/tests/`、測試、文件；預算 1 回合＋1 次修正。
3. main：§4 格點（188），寫回 `WIKI_MIN_DF` 與雜湊，研究紀錄；/code-review＋fresh verifier＋CI；合併；維護者實機。

- **executor 不可以**：同 zhen-mixed §8（連網；讀 `eval/holdout/` 或維護者的私人資料；安裝或啟動 App、呼叫 TIS 或 lsregister、碰 `~/Library` 與鑰匙圈；push、開 PR、留言、建 Release、發版；碰其他 worktree；改 `eval/bench/` 的檔或雜湊），另外：不連 188、不跑維基計數、不改 `data/english-extra.txt` 與 `data/english-wiki.txt` 的內容（測試用合成資料）。
- **回復**：中英混打預設關；要退回就 revert 這個 PR（`data/english-words.txt.sha256` 回到 `3352d050…`，`data/english-extra.txt`、`data/english-wiki.txt` 跟著移除）。

## 9. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
| plan-verifier 第 1 次（REVISE） | 1. 新的載入規則沒寫（行格式、同鍵不同寫法、200,000 行上限），和 zhen-mixed §2.6 與 FFI 測試矛盾 | 改：§3 寫出規則與上限（1,000,000 行、32 MiB），§5.1 寫 FFI 拒絕清單的改法 |
| 同上 | 2. 補充表的字打不打得出來沒有量（合併的召回被大表稀釋；npm、api、json 照現行規則不觸發） | 改：§4 三份清單分開報召回與四類計數、打不出來的列進研究紀錄；§6 寫明改觸發規則另外設計 |
| 同上 | 3. 缺預算、executor 禁止事項、回復方式 | 改：§8 |
| plan-verifier 第 2 次（REVISE；前三點確認已改） | 1. 召回量不出來：`english-sim --recall` 照原樣打每一行、和那一行比，正式寫法（`ChatGPT`）的大寫字母沒有注音鍵；不能拿測試清單對已載入的表量；兩份清單沒存 | FIX：§4 寫 `--recall` 改用小寫打、和顯示寫法比，新增 `--probe`（不在表裡另計），由 executor 做並加 `cli/tests/`（§5.4）；兩份清單存進 `eval/english/`、雜湊寫在 §4；§0 的數字照存下的清單重算（208 缺 89、56 缺 27） |
