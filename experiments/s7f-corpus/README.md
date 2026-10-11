# S7f：核心的口語語料（清理、混合、生成句）

契約：`docs/contracts/s7f-colloquial.md`。這個資料夾是 executor 寫的程式與單元測試；下載、清理真資料、呼叫 API、Colab 訓練與打分都由 main 執行。清理後的文字、生成句、`ids-*.npy` 都不進 repo。

## 檔案

| 檔 | 做什麼 |
|---|---|
| `clean.py` | 共用：去掉網址／email／電話（換成斷句）、切句（`。！？；!?…` 與換行）、`Filter`（簡體專用字 → 沒有漢字 → 長度 4–60 → 去重 → 和評測集重疊）、評測句載入（dev302 用 `lm_eval.rows_of` 照 `--dev 302` 的讀法）、`sample_to_cap`（固定種子抽到剛好的字數）、`water_fill`（合計上限的分配） |
| `archive_tw.py` | archive.tw 逐字稿 → `archive_tw.txt`＋manifest（commit） |
| `cofacts.py` | Cofacts 的 `articles.csv.zip`（`text`）與 `reply_requests.csv.zip`（`reason`）→ `cofacts.txt`＋manifest（HF revision、兩個 zip 的 SHA-256）。只在 Mac 上跑 |
| `wikitalk.py` | 中文維基 `pages-meta-current` dump（串流）→ `wikitalk.txt`＋manifest（dump 的 SHA-256） |
| `build_chat.py` | 三個來源 → `chat-real.txt`＋manifest（每個來源的列數、字數、SHA-256）；合計少於 500 萬字結束碼 3、不寫檔 |
| `invade.py` | invade 大陸用語詞表（`word:` 欄位）；印出詞數、詞表 SHA-256、commit，生成前凍結 |
| `generate.py` | 種子詞 → 提示 → Cerebras／OpenRouter → 把關 → 剛好 `--chars` 字；花費帳本、`--dry-run`、`--resume` |
| `discord_overlap.py` | 評測列（`前文|句子|讀音`）整句出現在各語料檔的列數；只印數字，`--rows-out` 寫重疊的列號 |
| `test_s7f.py` | 單元測試（`make test` 會跑）；`experiments/s7a-tinylm/test_extra.py` 測 `--extra` 與 `prep.py text` |

`clean.load_simp_only()` 讀 `~/.cache/shanjie/sources/opencc`（`build_counts.load_conv`，有雜湊核對），所以清理與生成都要在有這些檔的機器上跑；測試用假的簡體字集，不讀這些檔。

## 契約沒寫死、我做的決定

| 項目 | 做法 |
|---|---|
| archive.tw「只收中文檔」 | 根目錄 `YYYY-MM-DD-*.md`、漢字數 > 拉丁字母數的檔 |
| archive.tw「用 `.alternates` 去掉英文演講的譯稿」 | `.alternates` 只記成對、不記哪邊是原文，所以**列在裡面的中文檔全部不收**（也會去掉少數中文原稿） |
| archive.tw 的標記 | `#` 開頭的行（標題、`### 唐鳳：`）與 `>` 開頭的行（舞台說明、段落標記）整行丟掉；行首的時間（`11:30`）與短標籤（`答：`、`sli.do：`）去掉；Markdown 連結留文字 |
| 沒有漢字的句子 | 丟掉（`no_han`）：英文句不算進字數上限 |
| Cofacts | `articles` 只收 `articleType = TEXT` 且 `status = NORMAL`；`reply_requests` 只收 `status = NORMAL`。其他欄位（含使用者雜湊）不讀不寫；檔名不是那兩個 zip 就停 |
| 維基「段落」 | wikitext 的一行。連到名字以 bot／機器人結尾的使用者頁（或 MediaWiki message delivery）的段落整段丟掉；簽名（使用者頁、討論頁、貢獻頁連結與 `2026年10月1日 (三) 12:34 (UTC)`）去掉後才判斷簡體 |
| 電話 | `+` 或 `0`（或括號）開頭、至少 8 個數字（可夾 `-`、空白、括號、`.`）；日期與金額不算 |
| 合計上限 | 三個來源各自封頂 800 萬字後若超過 2,000 萬字：比共同水位少的來源整個收，其餘切到同一個水位（三個都夠就各 6,666,667／6,666,667／6,666,666 字）；不拿別的來源補 |
| 跨來源重複 | `build_chat.py` 照給的順序，後面來源裡已出現過的句子拿掉，列數記在 manifest |
| 抽到剛好的字數 | 固定種子打散，取前段到上限前 60 字，剩下的 60–120 字從接下來 1,000 句裡用子集合湊剛好；輸出照原順序 |
| invade 詞表 | 標了 `deprecation:` 的詞（網站上畫刪除線，2 個）不算。**單字詞（宏、寄、潤）不用**（main 2026-10-12 決定：它們在台灣是別的意思的日常字，例如「寄信」）；manifest 的 `invade.single_char_excluded` 列出被排除的單字詞；兩字以上的詞照字面比對 |
| 和評測集重疊的最短長度 | **只用 4 字以上的評測句**拿掉語料句（main 2026-10-12 決定，和語料句的下限相同）：一、兩字的「句子」（錯字回報第 29 列「吧」、「對齊」、「試了」）出現在語料句裡不算那一句重疊，照字面拿掉會讓聊天文字少掉所有含「吧」的句子。manifest 的 `eval.below_min_rows` 記短於 4 字的評測列數，`eval_below_min_hits` 記每一列（`集合:列號`，不寫句子）被多少留下的語料句包含 |
| 生成句的回應 | 去掉頭尾引號；空的（`empty`）或有換行的（`multi_line`）丟掉，其餘照契約 §2.2 的規則 |
| 生成到多少才停 | 收滿 `--chars`＋1,000 字才停，再抽到剛好 `--chars`（池子太薄會湊不出剛好） |
| 花費 | 每一批（`--workers` 個呼叫）結束後把這批的花費加進帳本；帳本 ≥ `--budget-usd` 就不再開新的一批（最多超出一批的花費）。供應商沒回報 `usage.cost` 又沒給 `--price-in/--price-out` 就停：否則帳本不會增加、上限形同虛設 |

### 為什麼評測句要設最短長度

「和評測集重疊」照字面是「評測句整句出現在新句子裡就丟掉新句子」。錯字回報檔裡有一字句「吧」與「對齊」「試了」「繳了」這類兩字句，照字面做，所有含「吧」的新句子都會被丟掉。第一版照字面實作時2026-10-12 在 archive.tw（commit 3bc642e；評測句不含 cvtune／wikitune；executor 的機器沒有 OpenCC 檔，簡體判斷用三個字的替代集合，不是 `SIMP_ONLY`）實測：195,808 句裡被這條規則丟掉 1,072 句，其中含「吧」773 句、「對齊」150 句。聊天語料裡「吧」更常見，丟掉的比例會更高。所以改成只用 4 字以上的評測句（上表）；manifest 的 `eval.shortest` 仍記最短評測句的長度。

## 命令（main 執行）

```sh
cd experiments/s7f-corpus
PY=/opt/homebrew/bin/python3
W=<工作資料夾>               # 不進 repo
TUNE=<有 cvtune.txt、wikitune.txt 的資料夾>

# 三個來源（各寫 $W/clean/<來源>.txt 與 .manifest.json）
$PY archive_tw.py --repo <transcript clone> --tune $TUNE --out $W/clean
$PY cofacts.py --articles <dir>/articles.csv.zip --requests <dir>/reply_requests.csv.zip --revision <HF revision> --tune $TUNE --out $W/clean   # 只在 Mac
$PY wikitalk.py --dump <dir>/zhwiki-20261001-pages-meta-current.xml.bz2 --tune $TUNE --out $W/clean
$PY build_chat.py --out $W/clean archive_tw=$W/clean/archive_tw.txt cofacts=$W/clean/cofacts.txt wikitalk=$W/clean/wikitalk.txt

# 生成句（A2）：先凍結詞表，再看提示長什麼樣
$PY invade.py --repo <invade clone>                      # 記下 sha256 與 commit
$PY generate.py --provider cerebras --model gpt-oss-120b --seeds seeds/*.txt --invade <invade clone> --invade-sha256 <sha256> \
    --tune $TUNE --chars 2500000 --out $W/gen --ledger $W/gen/spend.json --budget-usd 20 --dry-run 5
# 金鑰只放在環境變數 CEREBRAS_API_KEY／OPENROUTER_API_KEY（單一程序），不印出、不寫檔
$PY generate.py --provider cerebras --model gpt-oss-120b --price-in <USD/1M> --price-out <USD/1M> --seeds seeds/*.txt \
    --invade <invade clone> --invade-sha256 <sha256> --tune $TUNE --chars 2500000 --out $W/gen --ledger $W/gen/spend.json --budget-usd 20 \
    --dedupe-against $W/clean/chat-real.txt
$PY generate.py --provider openrouter --model <Gemma 4 的模型 id> --or-provider <供應商> --seeds seeds/*.txt \
    --invade <invade clone> --invade-sha256 <sha256> --tune $TUNE --chars 2500000 --out $W/gen --ledger $W/gen/spend.json --budget-usd 20 \
    --dedupe-against $W/clean/chat-real.txt $W/gen/cerebras-gpt-oss-120b.txt
# 中斷（預算、HTTP 錯誤）結束碼 3；原始回應在 $W/gen/<tag>.jsonl，同樣的參數加 --resume 接著跑

# 轉成 ids（用 S7a 的字表，不重建）；A1 只有真人口語，A2 再加兩家的生成句
cat $W/clean/chat-real.txt >| $W/chat-a1.txt
cat $W/clean/chat-real.txt $W/gen/cerebras-gpt-oss-120b.txt $W/gen/<openrouter tag>.txt >| $W/chat-a2.txt
$PY ../s7a-tinylm/prep.py text --work <S7a 的 work（有 vocab.txt）> --in $W/chat-a1.txt --name chat-a1
$PY ../s7a-tinylm/prep.py text --work <同上> --in $W/chat-a2.txt --name chat-a2
# 訓練（Colab）：A0' 不給 --extra；A1、A2 給各自的 ids
python train.py --size M --work <work> --out <out> --seed 20261012 --max-steps 8836 [--extra <work>/ids-chat-a1.npy]

# discordtune 的重疊（Mac，只印數字）
$PY discord_overlap.py --rows <discordtune 的列> --corpus <A0 的 runs-wiki.txt> <A0 的 runs-coll.txt> $W/clean/chat-real.txt \
    $W/gen/cerebras-gpt-oss-120b.txt $W/gen/<openrouter tag>.txt --rows-out <只有列號的檔>
```

- `prep.py text` 和口語文字同一條路：`convert`、`HAN` 連續段、≥ 2 字，每段前面一個 BOS；`text-<name>.json` 記輸入、字表、runs、ids 的 SHA-256，VM 與 Mac 的結果用它核對。
- `discord_overlap.py` 的 A0 訓練文字是漢字連續段（沒有標點），含標點或英數的 discordtune 句子不會出現在裡面。

## 測試

```sh
python3 -W ignore -m unittest experiments/s7f-corpus/test_s7f.py experiments/s7a-tinylm/test_extra.py -v   # repo 根目錄
```
