# SJ-LM-7 工具與指令（契約 `docs/contracts/sj-lm-7.md`）

這個資料夾放通語 SJ-LM-7 的新口語語料處理腳本與測試。模型建置的配方（計數、`build_lm.py`、分類、承接、連文）照 `experiments/model-v6/README.md`，這裡不重寫。裡面沒有任何數字：每一步量到的數字記進 `docs/research-log.md`。

## 腳本

| 檔案 | 做什麼 | 契約 |
|---|---|---|
| `ptt_clean.py` | PTT 網頁 → `body.tsv`、`push.tsv`（單位 `看板/文章`，一個單位最多 50 句）。同一篇、同一帳號、相鄰、時間差 ≤ 3 分鐘的推文先接起來再切句 | §2.4 |
| `p2clean.py` | 切句與過濾規則（4–40 漢字、漢字比例、網址與帳號、數字、程式碼、簡體專用字）、去重鍵、`Guard`。從 `exp/p2-data` 複製，`ptt_clean.py` 與 `clean_subs.py` 共用 | §2.4 |
| `clean_subs.py` | OPUS OpenSubtitles `zh_tw` → `subs.tsv`、`subs-half.tsv`（`SHA-256(句子)` 第一個位元組 < 128 的一半）。含假名、亂碼（詞庫外字）、中英並列三道過濾；每部片最多 50 句（片與片之間用空行分，見下面「未驗證」） | §2.7 |
| `dedupe_eval.py` | 用 `tools/holdout_guard.py` 的比對器，把新來源句子對各評測集去重；只印每個來源、每個集合的命中數 | §2.4 |
| `moore_lewis.py` | `score`：字元 4-gram Kneser–Ney，打分集合一個、候選池兩折交叉（池依句子的 SHA-256 分兩半，各用另一半同字數的固定種子樣本訓練去打分，沒有句子被看過它的模型打分），每個來源留交叉熵差最好的 50%。`split`：訊息 ID 的 SHA-256 第一個位元組 < 32 的 1/8 進保留桶 | §2.4 |
| `test_*.py` | 合成輸入的單元測試，`make test-python` 會跑 | |

未驗證（要在真檔上看一眼，測試沒有真檔）：

- OPUS `mono/zh_tw.txt.gz` 是否用空行分隔字幕檔。`clean_subs.py` 假設是；整個輸入只看到一部片、而且 50 句上限丟了句子時，腳本以 `STOP` 結束（結束碼 1）。
- `ptt_clean.py` 的 `body()` 逐行拿掉 `article-metaline` 與 `article-metaline-right`（作者、看板、標題、時間）。

## 指令（之後由 main 執行；`<…>` 是路徑）

聊天保留桶（先 commit 切法再建任何打分集合；私人訊息只在 188 與 Mac，不送 GCP）：

```sh
python3 experiments/sj-lm-7/moore_lewis.py split --messages <訊息 id<TAB>內容> --reserve <reserve.tsv> --rest <rest.tsv>
```

PTT 清理（輸入是抓好的 `<看板>/<文章>.html`）：

```sh
python3 experiments/sj-lm-7/ptt_clean.py <PTT_DIR> <OUT_DIR>      # 寫 body.tsv、push.tsv，印每個來源的計數
```

字幕清理（`--invade` 是 invade 清單的 clone，`--invade-sha256` 是凍結的雜湊；`--extra-rows LABEL=FILE` 把私人評測列加進守門，必須涵蓋 `REQUIRED_LABELS`）：

```sh
python3 experiments/sj-lm-7/clean_subs.py --raw <zh_tw.txt.gz> --out <OUT_DIR> --invade <INVADE_CLONE> --invade-sha256 <HEX> \
    [--extra-rows holdout=<f> ...] [--sample 300 --seed <S>]   # --sample 只印公開字幕的句子，供校對 300 句
```

和評測集去重（每個 `--set` 是 `前文|句子|讀音` 三欄的列檔；同一個 LABEL 可以給多個檔；`--jobs` 用滿核心）：

```sh
python3 experiments/sj-lm-7/dedupe_eval.py --set holdout=<eval/holdout/…> --set dev302=<…> --set <LABEL>=<…> \
    --source ptt-body=<body.tsv> --source ptt-push=<push.tsv> --source g0v=<…> --source subs=<subs.tsv> --out <OUT_DIR> --jobs <N>
```

Moore–Lewis（只在 188 算，同一份輸入、同一個種子獨立再算一次，兩次的輸出雜湊要相同）：

```sh
python3 experiments/sj-lm-7/moore_lewis.py score --in-domain <打分集合.txt> --source ptt-body=<…> --source ptt-push=<…> --source g0v=<…> \
    --out <OUT_DIR> --seed <N>                                   # 每個來源留 50%（預設 --keep 0.5）
```

計數（繁體句不套 `TWPhrases`）：

```sh
S2_WORK=<WORK> python3 experiments/s2/build_counts.py --no-trad-tw-phrases --articles 200000 --procs <N> --expected --extra-lexicon <計數詞表>   # 不帶旗標＝model-v6 的行為
S2_WORK=<WORK> python3 experiments/s2/build_counts_text.py --no-trad-tw-phrases --expected --extra-lexicon <計數詞表> <OUT.pkl> <口語檔>…
python3 experiments/s2/build_tune.py --train-only-new-conv       # 只重寫 colloquial-train.txt；cvtune 的抽樣與參考句不動；印出 SHA-256
```

詞庫（讀音修正與萌典補詞；`SHANJIE_MOEDICT`、`SHANJIE_HETERO`、`SHANJIE_UNIHAN` 指到本機檔，什麼都不下載）：

```sh
python3 tools/readings.py --report-inverted [--unihan <Unihan_Readings.txt>]      # 9 個倒掛字的現行與建議分數，不寫詞庫
# readings.py 的規則 3b 預設關（其他建置工具的輸出不變）；要用就加 --rule-3b（--unihan 才有作用），build_overlay 的 --moedict-readings 路徑自己開
python3 tools/build_overlay.py --moedict-readings --reading-report <report.tsv>   # 讀音：萌典第一讀音優先，其次 readings.py；列出讀音改變的詞
python3 tools/build_overlay.py --moedict-readings --moedict-supplement <維基文字> <口語檔>… --jobs <N>   # 加「萌典補詞」來源
```

詞包處置（兩臂各自凍結次數，`--expected-model` 是該臂模型的 SHA-256）：

```sh
python3 tools/build_acg_pack.py freeze-counts --expected-model <SHA-256>
```

維基文字檔（萌典補詞要數子字串的語料）要自己先備好：一行一段漢字，用 `build_counts.article_runs(raw, conv, trad_tw_phrases=False)` 從 dump 產生；腳本沒有包這一步。

## 輸入表（契約 §2.4；每一列的 SHA-256 在輸入凍結時填）

| 輸入 | SHA-256 |
|---|---|
| 維基 dump | |
| `colloquial-train.txt`（`--train-only-new-conv` 重產） | |
| g0v `#rand0m` 增量、PTT 內文、PTT 推文 | |
| `synth.txt` | |
| `mcbpmf-data.txt`、`overlay-add.tsv` | |
| 臂 A、臂 B 各自的計數詞表 | |
| OpenCC 表 | |

## 英文前文表（`docs/contracts/english-next.md` §1，修訂三）

建表（讀各來源的原始輸入，在清理丟掉英文之前計數；要用滿核心，`--jobs`）：

```sh
python3 tools/build_english_next.py --lexicon data/lexicon/mcbpmf-data.txt data/lexicon/overlay-add.tsv data/lexicon/sandhi-add.tsv data/lexicon/names-add.tsv \
    --wiki <維基 dump> --colloquial <colloquial-train.txt> --g0v <g0v 檔，一行一則訊息> --ptt <PTT 原始目錄> --subs <zh_tw.txt.gz> \
    --out data/lexicon/english-next.tsv --jobs <N>
```

| 輸入 | SHA-256（凍結時填；PTT 是「`相對路徑\0內容雜湊\n` 逐行排序串起來」的雜湊，工具也寫在表的檔頭） |
|---|---|
| 維基 dump（前 200,000 篇） | |
| `colloquial-train.txt` | |
| g0v `#rand0m` 來源檔 | |
| PTT 原始 `<看板>/<文章>.html` 目錄 | |
| OPUS `mono/zh_tw.txt.gz` | |
| 詞庫（`mcbpmf-data.txt`、`overlay-add.tsv`、`sandhi-add.tsv`、`names-add.tsv`） | |
