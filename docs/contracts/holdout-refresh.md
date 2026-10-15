# 契約：保留集換新（公開語料的新聞與口語，一次抽三份）

狀態：修訂二（2026-10-15）。plan-verifier 第一次 REVISE 的 7 項、第二次的 2 項（切換驗收的兩個輸入要同時存在；main 的回填設定寫死）都照改；待收尾審查。

## 0. 起因

- 保留集 `eval/holdout/holdout.txt`（227 列）是 2026-10-03 由不參與實作的 fresh agent 盲寫的，授權 CC0。
- 2026-10-10 起它至少用過 7 次：model-v5 補量、S7h、解碼器合併、語意校正強度、model-v6 §3.6 與 §4.5、zhen-inline。
  - 用多了就等於間接調參。
  - 「7 次」是事後估的，因為沒有計數器。
- n = 227 時，精確 McNemar 要 6:0 或 9:1 才顯著，只抓得到嚴重的退步。
- 舊集的同音組是開發集同一批同音組的偶數行（`eval/README.md`），所以兩者一直不是完全獨立。
- 維護者 2026-10-14 的選擇題：從公開語料找聊天與新聞的句子換新。授權先核對、不用私人訊息，換新前後兩版用同一份量。
- 來源與授權的調查，以及 Fable 的設計，記在研究紀錄 2026-10-15「保留集換新」一節。
- 維護者 2026-10-15 的選擇題：
  - 口語用 vTaiwan、維基討論頁、提點子三種；新聞用機關新聞稿、維基新聞、PeoPo 三種。
  - 一次抽 3 份，每份 300 列。
  - 每份用 8 次就換，或任何決定因它而做就立刻換。
  - 提點子接受資料集的開放授權標示，限量使用。
  - 舊集回填後退役，改當可讀的挑戰集。

## 1. 要做到什麼、怎麼看到

- 要做出三份互斥的新保留集（shard-1、2、3），每份 300 列。
  - 新聞 150 列，放在 `news.txt`。
  - 口語 150 列，放在 `talk.txt`。口語這半邊是公開的口語書寫，**不是** Discord 那種聊天；真正的聊天關卡仍是 E2 的私人判斷期，所以檔名用 `talk`，不叫 chat。
- 換新的順序：先由 shard-1 接替舊集，shard-2、3 先放著，之後依序換上。三份在同一個 commit 各量一次，份與份之間的差就是雜訊底線。
- 跨版本的比較不斷線：shard-1 回填 v0.4.0–v0.4.3 與當時 main 的數字，記在研究紀錄與 `eval/README.md`（§6）。
- 怎麼看到它有效：
  - `eval/README.md` 有新集的雜湊、回填表與使用紀錄。
  - 切換之後，`--set holdout` 量的是 shard-1。在切換的那個 commit 上，`--set holdout` 和 `--rows <shard-1 串接檔>` 用同一組旗標量出的 rowstats 逐位元組相同（§6 第三步）。
  - 舊集搬到 `eval/dev/holdout-2026-10-03/`，dev302 的前 302 列不變。

## 2. 來源與份量（每份）

| 半邊 | 來源 | 每份上限 | 授權 |
|---|---|---|---|
| 新聞 | 行政院以外機關的新聞稿（gov.tw `govnews.json`、數位發展部、食藥署等 data.gov.tw 資料集），只取 2026-10-05 起發布的 | 至少 105 列（70%），不設上限 | 著作權法第 9 條；資料集另標政府資料開放授權第 1 版或 CC0，照標示附顯名聲明 |
| 新聞 | 中文維基新聞：主題在台灣、原本就用繁體寫的條目（dump `zhwikinews` 最新一份） | ≤ 30 列 | CC BY 4.0，署名「維基新聞」加條目網址 |
| 新聞 | PeoPo 公民新聞：只收逐篇標 CC BY 或 CC BY-SA 的文章（讀文章頁的授權列） | ≤ 15 列 | 逐篇標示的授權，署名作者加網址 |
| 口語 | vTaiwan UberX 的 Polis 留言（compdemocracy/openData `vtaiwan.uberx/comments.csv`） | ≤ 33 列（三份合計約 100 列可用，推論） | CC BY 4.0，署名 The Computational Democracy Project |
| 口語 | 公共政策網路參與平臺「提點子」（data.gov.tw 58036） | ≤ 50 列 | 照資料集的政府資料開放授權標示（維護者決定接受），附顯名聲明；拿掉提議者欄位；整段貼新聞的提案不收 |
| 口語 | 中文維基討論頁與互助客棧：2026-10-01 dump **完成時間加一天**之後的修訂（API 取，不刮網頁） | 補足其餘 | CC BY-SA 4.0（＋GFDL），署名 Wikipedia contributors，附頁名與 revid |

- 某個來源填不滿上限時，用同一半邊最大的來源補：新聞補新聞稿，口語補討論頁。
- 每一個來源單位的上限：
  - 一篇新聞稿 ≤ 2 子句；一篇維基新聞 ≤ 2；一篇 PeoPo ≤ 2；
  - 一個討論頁 ≤ 2；
  - 一則留言或提案 1。
- 不收 ND（禁止改作）授權的內容。

## 3. 切句與過濾（抽樣前寫死，不在看到資料後改）

- **切句**：
  - 在 。！？；， 切子句，取 4–25 字的子句。
  - 前文是同段落、同留言或同提案的前一個子句，含結尾的標點。第一個子句的前文是空的。
  - 列的格式同 `eval/dev/user-typing.txt`：`前文|句子|讀音`。
- **過濾掉的子句**：
  - 含簡體專用字的。字集用 `experiments/s7f-corpus/` 在 2026-10-12 修正之後的那一份，**不是** `build_counts.SIMP_ONLY`（它含群、床、秘）。
  - 含入侵詞表（commit `cbc7bdc`）任一詞的。
  - 含 `tools/build_overlay.py` 的 `CANTO_CHARS` 的。
  - 含拉丁字母或數字的（CLI 打不出來）。
  - 在整個來源池裡出現 ≥ 2 次的（樣板句）。
  - 新聞稿的聯絡人、發布日期這類欄位行。
- **去重**：
  - 新聞稿逐子句和行政院「本院新聞」那 294 句去重。
  - 討論頁照 `experiments/s7f-corpus/wikitalk.py` 清掉簽名、bot、問候句。
- **讀音**：
  - 用 `tools/readings.py` 產生。UNREADABLE 與 GAP 的列不收。
  - CHECK 列由抽樣的 agent 讀句子決定讀音，定不了就丟掉並計數。
- **讀音抽查**：
  - 先隨機抽 40 列，由抽樣的 agent 人工核對，回報錯誤率。
  - 超過 5% 就先修規則再抽其餘，修的內容記進研究紀錄。
- **份量檢查**：過濾與重疊比對（§4 第 3 步）之後，池小於需求的 3 倍就停下來回報。補救方法是把時間窗往後延，不是放寬過濾。

## 4. 抽樣程序（誰、什麼時候、讀什麼）

1. main 寫這份契約，READY 後 commit。種子是 20261015。
2. **executor** 實作 `tools/holdout_guard.py` 與它的單元測試（§8）。它不碰 `eval/holdout/`。合併之後才做第 3 步。
3. **fresh mech-executor**（之後不做任何實作）負責抓取、清理、過濾（§3）與產生讀音，然後**停下來**交出兩樣東西：
   - 過濾後的池，放在它自己暫存資料夾裡，不進 repo；
   - 池裡每個句子的雜湊檔，格式照 §8。

   它只回報每一步的計數。
4. **main** 把雜湊檔帶到 Mac 以外的機器，用 `tools/holdout_guard.py --hashes <池的雜湊> --report-matches`，對下列文字比對子句雜湊：
   - 所有公開評測集；
   - 訓練文字（維基前 20 萬篇、口語訓練檔、`synth.txt`、補次數檔）；
   - S7f 清理後的語料（`986626adaefa`）；
   - 行政院 294 句；
   - 舊保留集。

   得到「要丟的雜湊」清單，交回給 mech-executor。整個過程 main 只碰雜湊，不碰句子。
5. **mech-executor** 丟掉命中的句子，回報「重疊丟掉 N 句」。這個計數出現在抽樣**之前**。接著照 §3 做份量檢查，用固定種子抽樣、切成三份，寫入：
   - `eval/holdout/next/shard-{1,2,3}/{news,talk}.txt`
   - `eval/holdout/next/sources.tsv`
   - `eval/holdout/hashes.sha256`（§8）
6. **盲後處理**：另一個 fresh agent 做下列檢查，只回數字。
   - 拼不出：把每份的 `news.txt`、`talk.txt` 依這個順序串成一個暫存檔，用 CLI 的 `--rows <暫存檔> --profile chat` 跑一次。摘要行的 n 必須等於 300，也就是 `usable` 沒有丟列。這一步不用改 `cli/`。
   - 讀音和詞庫第一名不同的列數：在暫存的 worktree 裡把三份複製到 `eval/holdout/` 頂層，跑 `--set holdout --check-readings`。這個數字**只回報**，不拿來丟列：多音字與變調的列本來就會不同。
   - 和開發集零重疊。
   - 把三份的句子欄一行一句餵給 `tools/holdout_guard.py --hashes eval/holdout/hashes.sha256`，回報丟掉的行數。必須等於 900。
   - 抽完之後如果還驗到和 §4 第 4 步所列文字的重疊：停下來回報，不自己補抽。
7. main **不讀** `eval/holdout/` 底下的任何內容，包括 `next/`、`sources.tsv` 和雜湊檔裡的句子對應。commit 時只看 `git diff --cached --stat` 與 SHA-256。

## 5. 授權與出處

- **換新 PR** 同時改 `LICENSES/data.md`：
  - 第 27 列的 `eval/holdout/*` 收窄成 `eval/holdout/holdout.txt`、`eval/holdout/oov_words.list`，授權 CC0。
  - `eval/holdout/hashes.sha256` 是本專案產生的，CC0。
  - `eval/holdout/next/**/news.txt` 一列：寫明著作權法第 9 條、政府資料開放授權的顯名聲明、CC BY 4.0（維基新聞），以及 PeoPo 的逐篇授權。
  - `eval/holdout/next/**/talk.txt` 一列：整檔 CC BY-SA 4.0，署名 Wikipedia contributors、The Computational Democracy Project，加上提點子的顯名聲明。
  - `eval/holdout/next/sources.tsv`：出處清單，CC0。
- **切換 PR** 再改一次：
  - 舊集那一列改寫成 `eval/dev/holdout-2026-10-03/*`，CC0。
  - 新集的兩列改寫成切換後的確切路徑：shard-1 的 `eval/holdout/news.txt`、`talk.txt`，以及仍在 `next/` 的 shard-2、3。
  - 每個檔案剛好被一列涵蓋。
- 逐列出處寫在 `sources.tsv`。欄位是：份、檔、列號、來源、頁名加 revid 或網址、授權。先例是 `acg-sources.tsv`、`names-sources.tsv`。
- 條款頁快照照 S7f 的寫法記：網址、取得時間、內容 SHA-256 的前 12 碼。

## 6. 新舊並存與換手（不改 `cli/`、`core/`）

- 事實：`cli/src/main.rs` 的 `load_set` 會讀 `eval/holdout/` 這一層所有的 `.txt`，不讀子資料夾，所以 `next/` 底下的檔案不會被 `--set holdout` 讀到。雜湊檔用 `.sha256` 副檔名，也不會被讀到。
- **第一步，換新 PR**：只動 `eval/`、`docs/`、`LICENSES/`。
  - 內容：三份資料、`sources.tsv`、`hashes.sha256`、`eval/README.md` 的雜湊與使用紀錄、`LICENSES/data.md`。
  - 這時舊集仍是現役。
  - **審查只用 fresh verifier**。它的 brief 禁止在報告裡引用任何列的文字。這個 PR 不跑 `/code-review`。
  - verifier 要確認，`--set holdout --rowstats` 在這個 PR 上仍然成功，而且 n = 227。
- **第二步，回填**：由 fresh verifier 做，量完就刪 rowstats。
  - 對象與設定（照下表，不推論）：
    - v0.4.0、v0.4.1、v0.4.2、v0.4.3：各自 tag 的 worktree，用 `tools/bench.py` `VERSIONS2` 裡該版的出貨旗標與模型（模型放在 bench 快取裡，以雜湊核對），摘要行的設定標記必須等於 `VERSIONS2` 推出的那一個。
    - main：回填當下的 main commit（記下 commit），模型是那個 commit 的 `data/*.sha256` 所記的四個檔（model-v6 起是 `bigram.sjlm` `fc2bddd1…`、`classes.sjc` `efac3184…`、`kn.sjkn` `cbefbced…`、`tinylm.sjtl` `d07de71d…`；以 commit 裡的雜湊檔為準），放在該 worktree 的 `data/lm/`；旗標是 `--context --packs acg --tinylm data/lm/tinylm.sjtl`；摘要行的設定標記必須是 `lm-<設定>+ctx+kn:<kn.sjkn 前 8 碼>:θ1:β1+tl:<tinylm.sjtl 前 8 碼>+acg`（model-v6 時是 `lm-chat+ctx+kn:cbefbced:θ1:β1+tl:d07de71d+acg`），不符就停。
  - 做法：把 shard-1 的 `news.txt`、`talk.txt` 依這個順序串成暫存檔（和 `--set holdout` 讀的順序相同），用 `--rows <暫存檔> --rowstats` 量，聊天、書面兩種設定都量。n 是 300。
  - 在同一個 main commit 量 shard-2、3，記錄雜訊底線，也就是三份兩兩的差。
  - **記錄位置**：回填與雜訊底線的數字寫進研究紀錄，以及 `eval/README.md` 的回填表。不寫進 `docs/benchmark.md`：那份由 `tools/bench.py table` 產生，結果 JSON 加新集的欄位屬範圍外（§11）。
  - 這些量測不計入使用次數。
  - brief 明寫禁止 `--dump`、`--dump-words`、`--tinylm-scores`：`--rows` 沒有 `--set holdout` 那種拒絕，這是流程規則，不是技術防護。
- **第三步，切換 PR**：
  - shard-1 的兩個檔 `git mv` 到 `eval/holdout/` 頂層。
  - 舊集的 `holdout.txt` 與 `oov_words.list` `git mv` 到 `eval/dev/holdout-2026-10-03/`。放子資料夾，是因為放在 `eval/dev/` 頂層會排在 `homophones.txt` 前面，把 dev302 弄歪。
  - 同一個 PR 改 `LICENSES/data.md`（§5），並把 `docs/contracts/eval-stats.md` 契約 D 裡「盲寫」的字句改掉。
  - 驗收（fresh verifier，在切換 PR 的 commit 上）：用第二步 main 那一列的同一組旗標與模型（雜湊照那個 commit 的 `data/*.sha256`），各跑一次 `--set holdout --rowstats A` 與 `--rows <把 eval/holdout/news.txt、talk.txt 依序串成的暫存檔> --rowstats B`，比對 A 與 B 是否逐位元組相同，只回報「相同／不同」與兩邊的 n，然後刪掉 A、B 與暫存檔。第二步刪掉的 rowstats 不參與這個比對。
- 兩版的 n 不同時（例如詞庫改了，讓某一列變成拼不出），`evalstats.py compare` 會拒絕比較。這時停下來回報，不自己挑列。

## 7. 換新規則

- **一次「使用」**：現役保留集的任何一個數字回到 main 或維護者手上。回填凍結的 tag、量雜訊底線不算。
- `eval/README.md` 的雜湊表旁邊加使用紀錄：日期、片、PR。這是規則能執行的機制。
- **換下一份**的條件，符合任一個就換：
  - 使用滿 8 次；
  - 任何決定是因它而做：觸發停止條件，或看過數字之後改門檻、改值。
- 三份都用完後：把時間窗往後延，用同樣的規則重抽。
- 這套規則寫進 `eval/README.md`，以及維護者本機的 `CLAUDE.md`「評測與資料」（由 main 改，不進 repo）。

## 8. 雜湊格式與保存期限（不讓新集被下次建模型吸進訓練文字）

- **雜湊的輸入**：只取句子欄，不含前文、不含讀音。用 UTF-8 編碼，只去掉行尾換行，不做其他正規化。
- **檔案格式**：`eval/holdout/hashes.sha256` 每行一個小寫 hex 的 SHA-256，不放文字。三份共 900 行。
- **`tools/holdout_guard.py`** 讀一個文字檔，處理方式如下：
  - 每一行用和 §3 相同的規則切子句（在 。！？；， 切開），每個子句去掉前後空白後算雜湊。
  - 只要有一個子句的雜湊和 `--hashes` 檔相符，整行就丟掉。最後回報丟掉幾行。
  - `--report-matches` 另外輸出命中的雜湊（§4 第 4 步用）。
- **單元測試**（合成資料）要涵蓋：
  - 子句命中就丟整行；
  - 雜湊格式和輸入的定義與上面一致；
  - 沒命中的行逐位元組保留。
- `experiments/model-v6/README.md` 的建置步驟加一行：計數之前，所有訓練文字先過這個工具。下一次建模型時才實際執行。

## 9. 驗收

- 三份各 300 列（新聞 150、口語 150），來源比例符合 §2。湊不到 3 份時，照 §3 的份量檢查先停下來回報，不自己降份數。
- `sources.tsv` 每一列都有出處與授權。條款頁快照有記錄。`LICENSES/data.md` 在換新與切換之後，都讓每個檔案剛好被一列涵蓋（§5）。
- 盲後處理的結果（§4 第 6 步）：
  - 拼不出 0 列，每份 n = 300。
  - 讀音和詞庫第一名不同的列數有記錄，不設門檻。
  - 和開發集零重疊。
  - `holdout_guard.py` 丟掉 900 行。
  - mech-executor 的計數報告裡，「重疊丟掉 N 句」這一行出現在抽樣之前。
- 換新 PR 上，`--set holdout` 仍是 n = 227。
- 回填與雜訊底線的數字記在研究紀錄與 `eval/README.md`。
- 切換之後：
  - 在切換的 commit 上，`--set holdout` 與 `--rows <串接檔>` 用同一組旗標量出的 rowstats 逐位元組相同（§6 第三步），n = 300。
  - dev302 的前 302 列和切換前逐列相同（用 `cli --dev 302 --dump` 比較 dump）。
- `tools/holdout_guard.py` 的測試通過，而且包含在 `make test-python` 裡。

## 10. 停止條件

- 過濾與重疊比對之後，池小於需求的 3 倍（§3）。
- 讀音抽查錯誤率超過 5%，而且修了規則之後仍然超過。
- 任何一份的授權查不清：條款頁打不開，或逐篇的授權讀不到。
- 抽完之後還驗到重疊（§4 第 6 步）。
- 回填時兩版的 n 不同（§6）。
- 需要讀私人訊息，或需要改 `cli/`、`core/` 才能做。

## 11. 範圍外

- 改 `cli/` 或 `core/`，例如讓 `--rows` 也拒絕 `--dump`。
- 結果 JSON 與 `docs/benchmark.md` 的新集欄位：`tools/bench.py` 要改格式，另案處理。
- 網站上綁著舊集的數字：下次部署時改，部署前先問。
- 去函國發會確認提點子：維護者已選擇接受標示，不發信。日後若有人提出異議，照 `sources.tsv` 整批拿掉。

## 12. 負責人

| 步驟 | 負責人 |
|---|---|
| 契約、`LICENSES/data.md`、`eval/README.md` 的規則與使用紀錄、PR、§4 第 4 步的重疊比對（只碰雜湊） | main |
| §4 第 2 步：`tools/holdout_guard.py` 與測試（不碰 `eval/holdout/`；這個 PR 照程式改動的關卡走） | executor |
| §4 第 3、5 步：抓取到寫檔 | fresh mech-executor |
| §4 第 6 步：盲後處理 | fresh agent |
| §6 第一步的審查、第二步的回填與雜訊底線 | fresh verifier |

## 13. agent 不可以做的事

- 不讀 `~/side-project/shanjie-private`、學習檔、`~/Library`、鑰匙圈。
- 不安裝軟體、不啟動 App、不推 tag、不建 Release。
- 不登入任何網站、不建帳號、不發信或發文。
- 抓取只用官方 dump、API 或下載點，遵守 robots.txt 與 crawl-delay，不刮被禁止的路徑。
- main 以外的 agent 不把保留集的文字貼進報告、對話、研究紀錄、commit 訊息或 PR。
