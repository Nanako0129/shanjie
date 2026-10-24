# youtube-events：事件索引的候選詞工具（契約 `docs/contracts/youtube-events.md` A）

只用 Python 標準函式庫。網路只在 `common.http_get` 一處（測試把它換掉）；測試不連網、不讀真實資料：
`python3 -W ignore -m unittest experiments/youtube-events/test_youtube_events.py`（已在 `make test-python`）。

資料流：影片中繼資料與維基大事記 → 候選字串 → 新聞驗證（事件詞）→ PTT 長期在用 → 分流表（main 人工審）。
原始回應、PTT 頁面、候選與中間檔都放 repo 外（`~/.cache/shanjie/` 或 `shanjie-private/`）；下面 `$W` 是工作資料夾。

| 步驟 | 指令 | 輸出 |
|---|---|---|
| 1 影片 | `YT_KEY=… python3 experiments/youtube-events/fetch_videos.py UCeypx1ck3i7Q-GqUUceWv2A $W/videos.json` | JSON；印影片數與配額單位（金鑰不印） |
| 2 大事記 | `python3 experiments/youtube-events/fetch_wiki_years.py 2021 2026 $W/wiki.json` | JSON：`date`（日、月或年）、`text`、`links`；每秒 1 個請求 |
| 3 候選 | `python3 experiments/youtube-events/extract_candidates.py $W/videos.json $W/wiki.json $W/candidates.tsv` | TSV：`string event date source`；任一輸入給 `-` 就略過 |
| 4 新聞 | `python3 experiments/youtube-events/verify_news.py $W/candidates.tsv $W/news.tsv $W/news-cache` | TSV：`string date headlines outlets pass`；每秒 1 個請求，原始 RSS 快取在第三個參數的資料夾，不存標題 |
| 5 PTT | `python3 experiments/youtube-events/ptt_longterm.py <PTT_DIR> $W/candidates.tsv $W/ptt.tsv --jobs <核心數>` | TSV：`string articles boards span_days long_term`；`<PTT_DIR>/<看板>/M.<unix>.A.<hash>.html`，只算頁面本身跨 ≥ 180 天的看板 |
| 6 分流 | `python3 experiments/youtube-events/route.py $W/candidates.tsv $W/news.tsv $W/ptt.tsv $W/review.tsv [--names-hint 名單.txt] [--readings 讀音.tsv --counts 文章數.tsv]` | 審查表 TSV：`string route events dates news_… ptt_… same_reading` |

- `YT_KEY` 只從環境變數讀，錯誤訊息也不帶網址。
- 步驟 4 的查詢帶 `after:`／`before:`（日期窗），讓舊事件在自己的時間窗內搜；不要這個運算子時加 `--no-date-operators`。日期只有月或年的，窗就是整個月或整年再加前後 7 天。
- 步驟 5 要在 188 或 GCP 跑時用 `--jobs`（一個看板一個工作）；它只讀 PTT 頁面，輸出只有計數。
- 步驟 6：`--names-hint` 是每行一個字串的名單；`--readings` 是 `字串<TAB>讀音（音節以空白分隔）`、`--counts` 是 `詞<TAB>PTT 文章數`，兩個都給才會在 `same_reading` 列出詞庫裡同讀音的詞與文章數（net-slang 的同讀音規則由 main 審查時套用）。已在詞庫各層（基底、`overlay-add.tsv`、`names-add.tsv`、`slang-add.tsv`、兩個詞包）的字串不輸出。
- 分流優先序：`names`（在名單提示裡）＞ `net-slang`（長期在用）＞ `hot-word`（新聞過關、不長期）＞ `drop`。
- 常數都在檔案開頭，可調：`extract_candidates.py` 的 `STOP_PHRASES`（頻道名與固定系列後綴）、`GENERIC`（章節套話）、`SPLIT_WORDS`；`verify_news.py` 與 `ptt_longterm.py` 的門檻。
