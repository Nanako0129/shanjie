# 唯音辭典產生工具（`tools/vanguard_textmap.py`）

這份寫的是現在的行為。

範圍：把善解的詞庫與語言模型轉成唯音（vChewing）Vanguard 打字引擎讀的辭典 `VanguardFactoryDict4Typing.txtMap`（TextMap 2.0）的工具：輸入、輸出格式要點、簡體欄與作者標記、計分、旗標、`--base-txtmap` 模式、`build_stats.json`、重現已發佈的檔案、授權。工具不被 App、核心或 CI 的建置步驟呼叫；它的測試是 `tools/test_vanguard_textmap.py`（見 §9）。

## 1. 輸入

工具讀 `--shanjie-root`（預設是這支檔案所在的 repo 根）底下的檔案：

| 檔 | 用途 |
|---|---|
| `data/lexicon/mcbpmf-data.txt` | 基底詞庫，每列 `讀音 詞 分數`（空白分隔；詞的字數要等於音節數，注音符號單字除外） |
| `data/lexicon/overlay-add.tsv`、`sandhi-add.tsv`、`names-add.tsv` | 疊加層與名單層，每列四欄（讀音、詞、分數、標籤）；`names-add.tsv` 可用 `--no-names` 略過；標籤 `colloq` 的列只在 `--include-colloq-readings` 時收 |
| `data/packs/<包>-add.tsv` | `--packs` 指定的詞包（`acg`、`it`、`meme`、`beauty`、`quotes`；`none` 為不用） |
| `data/lexicon/variant-order.tsv` | 異體字的先後（四欄，前三欄是讀音、次要、主要，第四欄不參與計分；次要的分數被壓到主要的 0.01 以下） |
| `data/lexicon/demote.tsv` | 選用；有就套用（`讀音 詞 扣分 …`，讀音為 `*` 表示所有讀音） |
| `data/lm/bigram.sjlm` | 通語二元模型：字詞表、詞頻與二元計數 |
| `data/lm/kn.sjkn` | `--kn-beta` > 0 時讀（`--kn-path` 可換）；必須是為該 `bigram.sjlm` 建的（雜湊綁定），否則報錯 |
| `data/lm/colloquial.sjlm` | `--colloq-uni-w` 或 `--colloq-bi-w` > 0 時讀（`--colloq-path` 可換） |
| `--trigram-path` | 選用，`SJT30001` 三元表，必須是為該 `bigram.sjlm` 建的 |
| `--extra-ngrams` | 選用，覆寫表，見 §6 |
| `--opencc-dir` 裡的 `TSPhrases.txt`、`TSCharacters.txt` | 只在 `--simplified` 時讀，OpenCC（commit `3ac34aa`，Apache-2.0）的繁轉簡表，見 §2.1；表本身不在 repo 內 |

路徑旗標（`--kn-path`、`--colloq-path`、`--trigram-path`、`--extra-ngrams`、`--base-txtmap`、輸出與 `--companion-dir`）的相對路徑以目前的工作目錄解析，不是以 `--shanjie-root`；在 repo 外執行時給絕對路徑或相對工作目錄的路徑。

依賴 numpy（`tools/build_classes.py`、`tools/build_trigram.py` 已依賴）。

## 2. 輸出

TextMap 2.0 檔：標頭（`VERSION 2.0`、`TYPE TYPING`、`READING_SEPARATOR -`、`ENTRY_COUNT`、`KEY_COUNT`）、`VALUES` 區、`KEY_LINE_MAP` 區（每個讀音鍵一列：`鍵 起始行 行數`；鍵照 UTF-8 位元組排序）。每個讀音鍵底下：

- **unigram**：型 B 行 `@分數<TAB>簡體格<TAB>繁體詞1|詞2…`，同分的詞併在一行，分數由高到低；預設簡體格是 `\a`（零個詞），開 `--simplified` 時見 §2.1；
- **contextual**：一個值對一個前文時型 C 行 `值<TAB>分數<TAB>6<TAB>前文`；同一個值有兩個以上前文時型 D 行 `!值<TAB>6<TAB>分數1<TAB>前文1<TAB>…`，一行 2 到 16 組（剛好剩 17 組時切成 15 加 2）。前文是 1 到 2 個詞，詞之間用 `|`。typeID 6 是繁體；開 `--simplified` 時另有 typeID 5（簡體）的行，見 §2.1；
- **meta**：給 `--vendor` 時多一個讀音鍵 `_VENDOR`，見 §2.2。

只有簡繁兩種條目：CNS（7）、非漢字（8）、符號詞（9）、注音文（10）、GBEX（11）、字母標點（4）等其他類型不產生。

格子裡的 `\`、`|`、空白與 U+0007 以 `\\`、`\|`、`\s`、`\a` 跳脫；空格子寫 U+0007。分數是 log10 機率，最高 −0.001（`SCORE_CEIL`），預設四捨五入到小數 3 位（`--decimals`），輸出時去掉尾端的 0。

工具寫完後用內建的嚴格檢查器（`--validate`，預設開）讀回整個檔：UTF-8、只有 LF、標頭與計數、每一行的格式（型 C、D 接受 typeID 5 與 6；typeID 2 的 meta 行只要求分數欄是有限數字）、跳脫、分數有限且不超過上限、`KEY_LINE_MAP` 連續涵蓋所有 `VALUES` 行、鍵照位元組排序（`--base-txtmap` 的輸出不查排序）；開了 `--simplified` 時另查 §2.1 的鏡像規則。檢查器是照唯音 `VanguardTrie.TrieIO` 的規則重寫的，沒有複製唯音的程式；不合格就以結束碼 2 結束。

### 2.1 簡體欄（`--simplified`，預設關）

唯音讀型 B 行的第 2 欄為簡體（EntryType 5）、第 3 欄為繁體（6），兩欄各自以 `|` 切開、不必一一對應（唯音原始碼 `VanguardTrieIO.swift`）；contextual 行的 typeID 5 只在簡體模式被查、6 只在繁體模式（`TrieProtocol.swift`，完全相等比對）；同一個讀音鍵底下行的順序不影響解析。唯音的辭典載入路徑不做繁轉簡（依其原始碼），所以簡體欄與 typeID 5 要由辭典自己帶；不開 `--simplified` 時簡體格是零個詞、也沒有 typeID 5 的行，簡體模式打不出字。開 `--simplified` 時：

- **轉換（逐詞）**：對一個詞由左到右，每個位置先用 `TSPhrases.txt` 做最長比對（命中就換成它的第一個值，接著從比對的尾端繼續），沒有命中的字用 `TSCharacters.txt` 的第一個值，沒有列的字原樣保留；只看詞本身，不看前後文，也不做兩岸用語轉換（例如不把「軟體」換成「软件」）。函式是 `make_t2s`。
- **B 行**：簡體格是同一格繁體詞逐一轉換、依原順序去重後以 `|` 併列；分數不變。兩個不同分數的繁體詞轉成同一個簡體詞時，各自留在自己那一行。
- **contextual**：每一條 typeID 6 的條目（讀音、值、前文、分數）另寫一條 typeID 5：值與前文的每個詞都轉換，讀音與分數不變；轉換後（讀音、值、前文）重複的取最高分。`--bi-budget`、`--tri-budget` 只算繁體的選取，簡體是同一批條目的鏡像、不另佔預算。
- **行序**：同一個讀音鍵底下依序是 B 行、typeID 5 的 C／D 行（值依 UTF-8 位元組排序）、typeID 6 的 C／D 行；`KEY_LINE_MAP` 照樣連續涵蓋。
- **OpenCC 表**：從 `--opencc-dir`（預設 `$SHANJIE_CACHE/sources/opencc`，`SHANJIE_CACHE` 預設 `~/.cache/shanjie`）讀兩個檔，讀之前核對 SHA-256（常數 `OPENCC_SHA256`：`TSPhrases.txt` 為 `9a23666e…ef102`，`TSCharacters.txt` 為 `9ff46a7d…1642d`）；缺檔或不符以結束碼 2 結束，訊息印出期望值與檔案位置。沒有任何命令列旗標或環境變數可以跳過或改寫這個核對。
- **檢查器的鏡像規則**：每條 B 行的簡體格非空、且等於繁體格轉換去重的結果；每個讀音鍵底下，typeID 6 條目的（值、前文）逐一轉換後的集合等於 typeID 5 條目（值、前文）的集合。
- `build_stats.json` 另記 `chs_contextual_entries`（去重後的 typeID 5 條目數）、`chs_contextual_lines`（typeID 5 的行數），`inputs.files` 多兩個 OpenCC 檔（檔名與 SHA-256）。`contextual_total_entries`、`contextual_lines` 仍只算繁體。

### 2.2 作者標記（`--vendor TEXT`，預設不寫）

給了就寫一個 meta unigram：`KEY_LINE_MAP` 多一個鍵 `_VENDOR`（依位元組排序，`_` 排在所有注音鍵之前），它的 `VALUES` 行是 `TEXT<TAB>0<TAB>2`（型 C 三欄：值是 TEXT、分數欄是 0、typeID 2 是 meta；格式比照唯音原廠檔的 `_BUILD_TIMESTAMP<TAB>…<TAB>2`）。TEXT 要是合法的值（不得含 TAB、換行等控制字元，也不得空），否則以結束碼 2 結束；meta 行不鏡像成簡體。`build_stats.json` 的 `flags.vendor` 記下這個值。

## 3. unigram 的計分

對每個（讀音 r、詞 w），依序：

1. 起點是詞庫分數（疊加層、名單層、詞包的列一併進來）。
2. **壓上限（cap_overlay，`--no-cap-overlay` 關）**：判斷以詞（字串）為單位，不以列為單位。一個詞只要出現在 `overlay-add.tsv`、`names-add.tsv` 或非 `acg-oov` 詞包的任一列，它在每個讀音、每個來源（含 `mcbpmf-data.txt` 與 `sandhi-add.tsv`）的列都受這一步影響：通語裡有詞頻 c 時分數取 `min(分數, log10(c/N))`；通語裡沒有時扣 1.0，但這個詞也出現在非 `acg-oov` 詞包的列裡時不扣。只出現在 `mcbpmf-data.txt` 或 `sandhi-add.tsv` 的詞不壓、不扣。一個詞只要出現在 `acg-oov` 詞包的任一列，它所有的列都不壓、不扣，也不參與下面的混合。
3. 套異體順序。
4. **混合通語詞頻（`--uni-lam` λ）**：若詞在通語裡有詞頻，`p1 = c/N`；有 `kn.sjkn` 時 `p1 = β·(n₁₊(w)+1)/T + (1−β)·p1`（β 是 `--kn-beta`，n₁₊ 是接續類型數，T 是側檔總數，字詞表前兩個保留詞不混）；有 `--colloq-uni-w` 時再與常談詞頻混合。分數成為 `λ·log10(p1) + (1−λ)·原分數`。通語沒有而常談有的詞，把原分數換成機率後與常談混合（細節在程式 `build_textmap` 的第 3 步）。
5. `--len-bonus`：每多一個字加這個值（預設 0）。
6. 再套一次異體順序，扣 `demote.tsv`，夾到上限並四捨五入。

每個讀音的 B(r) 是該讀音最高的 unigram 分數。unigram 行在詞的每個讀音上都輸出，不受 §4 的讀音差距限制。

## 4. contextual 的計分

contextual 只掛在 w 的部分讀音上：w 在該讀音的分數不低於 w 最佳讀音的分數減 `--max-reading-gap`（預設 1.5）；`acg-oov` 詞包的詞不產生 contextual。

候選是通語二元表裡的（前文 v、詞 w）：計數 ≥ `--bi-min-count`（預設 3）、v 的總數 ≥ `--bi-min-ctx`（預設 5）、v 在詞庫裡且長度 ≤ `--max-ctx-len`（預設 4）、w 在詞庫裡且長度 ≤ `--max-val-len`（預設 4）。對 w 掛的每個讀音 r：

- 條件機率 `p2 = (k − D)/tot(v) + back(v)·pb`，k 是二元計數，D 是模型的折扣，`back(v) = 1 − Σ(k − D)/tot(v)`，`pb` 是 w 在 r 的基礎機率（`10^(未混合分數)`，有 `kn.sjkn` 時同 §3 第 4 步混入接續類型數）。有 `--colloq-bi-w` 時 `p2` 與常談的條件機率線性混合，常談才有的二元組也併入候選。
- `--bi-mode blend`（預設）：`s = bi_lam·log10(p2) + (1−bi_lam)·未混合分數 + len_adj + bi_offset − 扣分`；`lift`：`s = unigram 分數 + bi_gamma·(log10(p2) − log10(pb))`。夾到上限並四捨五入。
- 只收 `s > B(r) + --bi-margin`（預設 0.02）的；`--bi-non-top1-only` 略過 w 已是 r 唯一第一名的。
- 排名（`--bi-rank`：`impact` = 計數 × (s − B(r))，預設；`lift`；`count`）後取前 `--bi-budget`（0 = 不限）個，每個讀音鍵最多 `--bi-max-per-key`（0 = 不限）個。同（讀音、值、前文）取最高分。

`--no-bigrams` 不產生 contextual。

## 5. 三元（選用）

`--trigram-path` 給了就從三元表再選：（u, v, w）計數 ≥ `--tri-min-count`（預設 3）、（u, v）總數 ≥ `--tri-min-ctx`（預設 5）、u 與 v 都是長度 ≤ `--max-ctx-len` 的詞庫詞、w 同 §4。`p3 = max((k − D)/tot + back·p2, 0.5·p2)`；用 `p3` 與 `p2` 各算 §4 的分數 `s_eff` 與 `s_bi`，要求 `s_eff > B(r) + --tri-margin`（預設 0.02）且 `s_eff > s_bi + 0.05`；存的分數是 `B(r) + 0.5·(s_eff − B(r))`（夾到上限並四捨五入），前文是（u, v）兩個詞。排名同 §4，取前 `--tri-budget`（0 = 不限）個；與二元共用同一張 contextual 表（同鍵取高分）。

## 6. `--extra-ngrams`（覆寫表）

TSV，每列 `讀音<TAB>值<TAB>前文<TAB>機率[<TAB>來源]`，第一列若是 `reading value context prob` 表頭則略過；`#` 開頭與空列略過；同一個（讀音、值、前文）取較高的機率。前文用 `|` 分隔 1 到 2 個詞；

- **前文是空的**＝unigram 覆寫：該讀音底下已有這個詞就把分數換掉，沒有就加進去（計數寫在 `extra_unigram_overridden`、`extra_unigram_added`）；
- 前文不空＝contextual：直接寫進 contextual 表，蓋過先前算出的同一個（讀音、值、前文）（`extra_ngram_entries`）。

## 7. `--base-txtmap` 模式

不讀善解的資料、不重算分數：讀一個現成的 TextMap 2.0 檔，只套 `--extra-ngrams` 的覆寫，再寫回。沒有被覆寫碰到的行逐位元組保留；新讀音鍵接在原有鍵之後（它們之間照位元組排序，原檔的鍵序不一定是位元組序，所以不重排）；`ENTRY_COUNT` 與 `KEY_COUNT` 照實際更新。沒給 `--extra-ngrams` 時輸出與輸入逐位元組相同。`--validate` 時輸入與輸出各檢查一次（輸入、輸出都不查鍵序）。base 模式不支援 `--simplified` 與 `--vendor`（它只改現成檔案的行，沒有轉換與 meta 這兩步），同時給時以結束碼 2 拒絕；其他建置旗標在這個模式下無作用。

## 8. 命令列

`python3 tools/vanguard_textmap.py (--out-txtmap <檔> | --out-dict <資料夾> --companion-dir <資料夾>) [旗標]`

- `--shanjie-root`：資料所在的 repo 根，預設是這支檔案所在的 repo。
- `--out-txtmap X`：只寫 `X` 與旁檔 `X.build_stats.json`（完整檔名後面接，例如 `VanguardFactoryDict4Typing.txtMap.build_stats.json`）。
- `--out-dict D`：建 `D`，寫 `D/VanguardFactoryDict4Typing.txtMap` 與 `D/build_stats.json`，並把 `--companion-dir` 裡的配套檔複製進去（`phonic_table_Z.txt`、`data-punctuations.txt`、`data-paired-punctuations.txt` 與所有 `template-*.txt`）。`--companion-dir` 沒有預設，`--out-dict` 沒給就報錯。配套檔是唯音的檔案，不屬於善解的資料。
- 輸出路徑（`--out-txtmap` 或 `--out-dict`，以解析符號連結後的真實路徑判斷）不可以在 `--shanjie-root` 的工作樹內，免得 33 MB 的產物被 commit；已存在時要 `--force` 才覆寫。
- 其餘旗標與預設值：`--include-colloq-readings`、`--no-names`、`--packs`（預設 `none`）、`--no-cap-overlay`、`--kn-path`、`--kn-beta`（預設 0，範圍 0–1）、`--colloq-path`、`--colloq-uni-w`、`--colloq-bi-w`（預設 0，範圍 0–1）、`--trigram-path`、`--extra-ngrams`、`--base-txtmap`、`--uni-lam`（預設 0，範圍 0–1）、`--len-bonus`（0）、`--decimals`（3）、`--no-bigrams`、`--bi-mode`（`blend`）、`--bi-lam`（0.7）、`--bi-offset`（0）、`--bi-gamma`（1）、`--bi-min-count`（3）、`--bi-min-ctx`（5）、`--max-ctx-len`（4）、`--max-val-len`（4）、`--max-reading-gap`（1.5）、`--bi-margin`（0.02）、`--bi-non-top1-only`、`--bi-budget`（0）、`--bi-rank`（`impact`）、`--bi-max-per-key`（0）、`--simplified`、`--opencc-dir`、`--vendor`（見 §2.1、§2.2）、`--tri-budget`（0）、`--tri-min-count`（3）、`--tri-min-ctx`（5）、`--tri-margin`（0.02）、`--validate` / `--no-validate`（預設開）。

### 8.1 `build_stats.json`

建置紀錄，內容是各步驟的筆數與秒數（開 `--simplified` 時另有 `chs_contextual_entries`、`chs_contextual_lines`）、`validated`、輸出的 `txtmap_sha256`、`entry_count`、`key_count`、所有旗標（`flags`）與 `inputs`。整份不出現絕對路徑：路徑類的值（`txtmap_path`、`base_txtmap`、`flags` 裡的路徑欄位，含 `opencc_dir`）記相對 `--shanjie-root` 的路徑，在它之外的只記檔名（`--shanjie-root` 本身記 `.`）。`inputs` 有 `git_commit`（`--shanjie-root` 的 `git rev-parse HEAD`；它不是 git 工作樹的頂層就是 `null`）、`git_dirty`（`git status --porcelain --untracked-files=no` 非空，不是 git 工作樹時 `null`）與 `files`：這次讀到的每個資料檔的相對路徑與 SHA-256（開 `--simplified` 時含兩個 OpenCC 檔）。標準輸出印同一份（不含 `flags`）。

## 9. 測試

`tools/test_vanguard_textmap.py`（`make test-python` 與 CI 的 numpy venv 步驟都跑）：

- 單元：格子的跳脫與嚴格解碼、`parse_extra_ngrams`（含空前文）、`override_unigram_lines`、`override_contextual_lines`、檢查器接受合法檔並拒絕各種壞檔。
- 端對端：在暫存資料夾建一個合成的小 repo 根（`eval/golden/kn-tiny/lexicon.txt` 當 `mcbpmf-data.txt`、kn-tiny 的 `bigram.sjlm` 與 `kn.sjkn`、空的疊加層、變調、名單與異體表，`--packs none --bi-min-count 1 --bi-min-ctx 1`），以子程序跑三種模式（`--out-txtmap`、`--out-dict`、`--base-txtmap`）並通過自我驗證；`build_stats.json` 遞迴檢查沒有絕對路徑、`inputs` 的雜湊與 git 欄位正確；寫進工作樹內（含經符號連結）被拒絕；`--out-dict` 沒給 `--companion-dir` 被拒絕。
- 簡體欄與作者標記：用合成的小 OpenCC 表（測試時把 `OPENCC_SHA256` 暫時換成合成表的雜湊，不讀真表，沒有因真表缺席而跳過的測試）在同一個程序裡建置：轉換函式（最長比對優先、字表後備、無列的字原樣、多值取第一個）、B 行簡體格與去重、同簡體詞的兩個繁體分數各留一行、typeID 5 的鏡像與同鍵取高分、行序、檢查器接受 typeID 5 並拒絕破壞鏡像的檔、不開 `--simplified` 時整份輸出等於開了之後拿掉簡體格、typeID 5 行與 meta 行、`--vendor` 的鍵與 `VALUES` 行、`build_stats.json` 無絕對路徑；以子程序用真常數跑命令列：OpenCC 雜湊不符、缺檔、`--vendor` 含 TAB 或空字串、`--base-txtmap` 加 `--simplified` 或 `--vendor`，結束碼都是 2。

## 10. 重現已發佈的檔案

已發佈的檔案由 main 的 `1a9899ac` 的資料與下面的旗標產生，輸出 SHA-256 為 `7f5d0a9228835464eb30743e2e16c90013383674e529ae71931a32909a10d130`，620,219 條、478,784 個讀音鍵。工具與資料是兩個獨立的 commit，重現不受之後 `data/` 的變動影響：

1. 取含這支工具的 commit（合併後的 main 或之後任一版）的 `tools/vanguard_textmap.py`。
2. 另開一個資料 commit 的 worktree：`git worktree add --detach <路徑> 1a9899ac`。把 `data/lm/` 補齊成該 commit 的 `data/*.sha256` 列的檔（在那個 worktree 裡設 `GITHUB_REPOSITORY=Nanako0129/shanjie`、登入 `gh` 後跑 `scripts/fetch-models.sh`，或照 `data/*.sha256` 的檔名與該 commit 的 `scripts/fetch-models.sh` 對應的 Release 代號逐一 `gh release download`），再 `shasum -a 256 -c data/*.sha256` 全部通過。
3. 輸出路徑放在任何 repo 工作樹之外：

   ```
   python3 <工具> --shanjie-root <那個 worktree> --out-txtmap <輸出資料夾>/VanguardFactoryDict4Typing.txtMap \
     --packs acg,it,meme,beauty --kn-beta 0.75 --uni-lam 0.7 --bi-mode blend --bi-lam 0.7 \
     --bi-budget 400000 --bi-margin 0.05 --validate
   ```

4. 期望：輸出的 SHA-256 等於上面那個值，`entry_count` 620,219、`key_count` 478,784、`validated` 為 true；旁檔 `VanguardFactoryDict4Typing.txtMap.build_stats.json` 的 `inputs.git_commit` 是 `1a9899ac…`、`git_dirty` 為 false。

### 10.1 簡體版（vchewing-dict-2）

同一份資料與旗標，加上 `--simplified --vendor SHANJIE_善解輸入法`（OpenCC 兩個表放在 `--opencc-dir`，雜湊對得上才會建）：

```
python3 <工具> --shanjie-root <那個 worktree> --out-txtmap <輸出資料夾>/VanguardFactoryDict4Typing.txtMap \
  --packs acg,it,meme,beauty --kn-beta 0.75 --uni-lam 0.7 --bi-mode blend --bi-lam 0.7 \
  --bi-budget 400000 --bi-margin 0.05 --simplified --vendor SHANJIE_善解輸入法 --validate
```

期望：輸出的 SHA-256 為 `5fc41d6ef2269b21cabfc8fa4aa55e7b6416ac06bc121add6c04035888e1a412`，`entry_count` 673,934（620,219 加 1 行 `_VENDOR` 加 53,714 行 typeID 5）、`key_count` 478,785（多 `_VENDOR`）、`chs_contextual_entries` 396,506（繁體 400,000 條裡 3,494 條轉簡後與別條重複）、`chs_contextual_lines` 53,714、`validated` 為 true。檔裡的繁體格與 typeID 6 的行與 §10 的檔逐行相同。

## 11. 授權

輸出整份以 CC BY-SA 4.0 散布。開 `--simplified` 時簡體字形由 OpenCC 的 `TSPhrases.txt`、`TSCharacters.txt`（Apache-2.0）轉出，兩個表不在 repo 內、也不在輸出檔裡。裡面含：

- MIT 的成分：小麥注音的 `mcbpmf-data.txt` 與 `sandhi-add.tsv`，全文在 `LICENSES/McBopomofo-MIT.txt`；
- BSD 的成分：libtabe 的片語；
- 定版帶了名單層與資訊詞包，所以也含 name-suggestion-index 的成分（BSD-3-Clause，全文在 `LICENSES/name-suggestion-index-BSD-3-Clause.txt`，要保留版權聲明、不得以其作者名義背書），與政府資料開放授權條款第 1 版的成分（名單層、資訊詞包，要標示提供機關）。

這份列舉不是完整清單：要保留的聲明與署名一律以 `LICENSES/data.md` 各列為準。`--out-dict` 複製的配套檔是唯音的檔案（LGPLv3／MulanPSL-2.0），只從使用者給的資料夾複製，不屬於善解的資料。
