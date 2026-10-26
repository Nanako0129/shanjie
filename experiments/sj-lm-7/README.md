# SJ-LM-7 工具與指令（契約 `docs/contracts/sj-lm-7.md`）

這個資料夾放通語 SJ-LM-7 的新口語語料處理腳本與測試。模型建置的配方（計數、`build_lm.py`、分類、承接、連文）照 `experiments/model-v6/README.md`，這裡不重寫。裡面沒有任何數字：每一步量到的數字記進 `docs/research-log.md`。

## 腳本

| 檔案 | 做什麼 | 契約 |
|---|---|---|
| `ptt_clean.py` | PTT 網頁 → `body.tsv`、`push.tsv`（單位 `看板/文章`，一個單位最多 50 句）。同一篇、同一帳號、相鄰、時間差 ≤ 3 分鐘的推文先接起來再切句 | §2.4 |
| `p2clean.py` | 切句與過濾規則（4–40 漢字、漢字比例、網址與帳號、數字、程式碼、簡體專用字）、去重鍵、`Guard`。從 `exp/p2-data` 複製，`ptt_clean.py` 與 `clean_subs.py` 共用 | §2.4 |
| `clean_subs.py` | OPUS OpenSubtitles `zh_tw` → `subs.tsv`、`subs-half.tsv`（`SHA-256(句子)` 第一個位元組 < 128 的一半）。含假名、亂碼（詞庫外字）、中英並列三道過濾，另外擋繁簡共用字的大陸詞組（什么、這里、最后、對于、愿意⋯，`MAINLAND`）與殘留記號（括號、「︰」標籤、粵語專用字與「唔＋漢字」、OCR 的「黑占」，`RESIDUE`）；輸入是 OPUS `raw/zh_tw.zip`（逐片原文，片＝片號資料夾，每部片後面插一個空行），只數片數、不設每片上限 | §2.7 |
| `review_subs.py` | 字幕逐句審查（§2.7）：`prefilter` 把含繁簡共用字（大陸用法不同）、英文字母或記號的句子切成每份 1,000 句（只放句子，不放編號）給 Sonnet subagent 審；審查者回報「行數、類別、原句」，`apply` 用原句（清理時的去重鍵）比對拿掉，重寫 `subs.tsv`、`subs-half.tsv` 並寫 `manifest.json` | §2.7 |
| `dedupe_eval.py` | 用 `tools/holdout_guard.py` 的比對器，把新來源句子對各評測集去重；只印每個來源、每個集合的命中數 | §2.4 |
| `moore_lewis.py` | `score`：字元 4-gram Kneser–Ney，打分集合一個、候選池兩折交叉（池依句子的 SHA-256 分兩半，各用另一半同字數的固定種子樣本訓練去打分，沒有句子被看過它的模型打分），每個來源留交叉熵差最好的 50%；`--jobs N` 訓練一次後用 N 個 fork 的程序打分，輸出、計數、門檻與雜湊和 `--jobs 1` 逐位元組相同。`inset`：打分集合（Moore–Lewis 的 in-domain）：訊息檔去掉含有評測句子（列檔第二欄、`--min-len` 字以上，比對器同 `dedupe_eval.py`）的訊息，只輸出內容、保持順序，只印計數與雜湊。`split`：訊息 ID 的 SHA-256 第一個位元組 < 32 的 1/8 進保留桶 | §2.4 |
| `test_*.py` | 合成輸入的單元測試，`make test-python` 會跑 | |

未驗證（要在真檔上看一眼，測試沒有真檔）：

- `ptt_clean.py` 的 `body()` 逐行拿掉 `article-metaline` 與 `article-metaline-right`（作者、看板、標題、時間）。

## 指令（之後由 main 執行；`<…>` 是路徑）

聊天保留桶（先 commit 切法再建任何打分集合；私人訊息只在 188 與 Mac，不送 GCP）：

```sh
python3 experiments/sj-lm-7/moore_lewis.py split --messages <訊息 id<TAB>內容> --reserve <reserve.tsv> --rest <rest.tsv>
```

PTT 清理（輸入是抓好的 `<看板>/<文章>.html`）：

```sh
python3 experiments/sj-lm-7/ptt_clean.py <PTT_DIR> <OUT_DIR> --jobs <N>   # 寫 body.tsv、push.tsv，印每個來源的計數；--jobs 用滿核心，輸出和單程序逐位元組相同
python3 experiments/sj-lm-7/g0v_clean.py <封存目錄，<YYYY-MM>.html> <OUT.tsv> --exclude <cc0-sentences> --exclude <口語池各檔>…   # 規則同 ptt_clean（單位＝一則訊息、每則最多 50 句、去重）；跳過 (no CC0) 與引用行，只留不在 --exclude 裡的句子；印計數
```

字幕清理（`--invade` 是 invade 清單的 clone，`--invade-sha256` 是凍結的雜湊；`--extra-rows LABEL=FILE` 把私人評測列加進守門，必須涵蓋 `REQUIRED_LABELS`）：

```sh
python3 experiments/sj-lm-7/clean_subs.py --raw <raw/zh_tw.zip> --out <OUT_DIR> --invade <INVADE_CLONE> --invade-sha256 <HEX> \
    [--extra-rows holdout=<f> ...] [--sample 300 --seed <S>]   # --sample 只印公開字幕的句子，供校對 300 句
```

和評測集去重（每個 `--set` 是 `前文|句子|讀音` 三欄的列檔；同一個 LABEL 可以給多個檔；`--jobs` 用滿核心）：

```sh
python3 experiments/sj-lm-7/dedupe_eval.py --set holdout=<eval/holdout/…> --set dev302=<…> --set <LABEL>=<…> \
    --source ptt-body=<body.tsv> --source ptt-push=<push.tsv> --source g0v=<…> --source subs=<subs.tsv> --out <OUT_DIR> --jobs <N> \
    --min-len discordtune=6 --min-len reserve=6     # 契約 §2.4：私人兩份只拿 6 個字以上的列
```

打分集合（私人訊息只在 188 與 Mac；`--exclude` 的 LABEL 同 `dedupe_eval.py`，列檔是 `前文|句子|讀音`；輸入是切完的 7/8 `rest.tsv`；只印計數與輸出雜湊）：

```sh
python3 experiments/sj-lm-7/moore_lewis.py inset --messages <rest.tsv> --exclude discordtune=<…> --exclude reserve=<…> --exclude <LABEL>=<…> \
    --min-len 6 --out <打分集合.txt>                              # 少於 --min-len 個字的評測句不當比對樣板（holdout_guard 本身的下限是 4）
```

Moore–Lewis（只在 188 算，同一份輸入、同一個種子獨立再算一次，兩次的輸出雜湊要相同）：

```sh
python3 experiments/sj-lm-7/moore_lewis.py score --in-domain <打分集合.txt> --source ptt-body=<…> --source ptt-push=<…> --source g0v=<…> \
    --out <OUT_DIR> --seed <N> --jobs <N>                        # 每個來源留 50%（預設 --keep 0.5）；--jobs 預設 1，用滿核心，輸出和 --jobs 1 逐位元組相同
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
python3 tools/readings.py --apply-inverted data/lexicon/mcbpmf-data.txt --unihan <Unihan_Readings.txt>   # 就地套用；要在重建疊加層、變調列之前（它們讀這份基底）
# readings.py 的規則 3b 預設關（其他建置工具的輸出不變）；要用就加 --rule-3b（--unihan 才有作用），build_overlay 的 --moedict-readings 路徑自己開
python3 tools/build_overlay.py --moedict-readings --reading-report <report.tsv>   # 讀音：萌典第一讀音優先，其次 readings.py；列出讀音改變的詞
python3 experiments/sj-lm-7/wiki_text.py <維基文字> --articles 200000 --procs <N> --no-trad-tw-phrases   # <維基文字>：和計數同一批篇、同一條管線的漢字段，一行一段
python3 tools/build_overlay.py --moedict-readings --moedict-supplement <維基文字> <口語檔>… --jobs <N>   # 加「萌典補詞」來源
```

詞包處置（兩臂各自凍結次數，`--expected-model` 是該臂模型的 SHA-256）：

```sh
python3 tools/build_acg_pack.py freeze-counts --expected-model <SHA-256>
```

維基文字檔（萌典補詞要數子字串的語料）要自己先備好：一行一段漢字，用 `build_counts.article_runs(raw, conv, trad_tw_phrases=False)` 從 dump 產生；腳本沒有包這一步。

## 連文表重數與剪成四個大小（探索 `trigram-explore.md`；之後由 main 在 GCP 執行）

連文表由 `experiments/derisk-1015/e1/` 的管線出：`count.py`（uni／bi／tri 期望次數，和 bigram 的 `build_counts.py` 同一套詞庫、轉換、切句）→ `build_models.py tri`（加權、四捨五入、異體合併、建不剪的表）→ `entropy_prune.py`（相對熵剪枝）。這條管線的 uni／bi 不拿去建 bigram（bigram 照上面 `build_counts.py` 與 `tools/build_lm.py`）；它的 uni／bi 只是順便算出的同一份次數。`<C>` 是計數輸出資料夾，`<詞表>` 是 bigram 計數用的同一份計數詞表。

```sh
# 1. 維基切 32 份平行（每份 1 個工作程序；i % 32 == K 的篇；輸出名帶 200000-shardKof32）。--no-trad-tw-phrases 要和 bigram 計數一致（契約 §2.2）
for K in $(seq 0 31); do
  python3 experiments/derisk-1015/e1/count.py wiki --out <C> --articles 200000 --procs 1 --shard $K/32 --no-trad-tw-phrases --extra-lexicon <詞表> --tri-min 0.01 &
done; wait
python3 experiments/derisk-1015/e1/count.py merge --out <C> --tag 200000 $(for K in $(seq 0 31); do printf '200000-shard%sof32 ' $K; done)

# 2. 口語各來源：一個 TSV 一個程序（路徑含 .tsv 取最後一欄，.bz2 自動解壓），再按來源合併。<來源> 是 bigram 用的那個計數檔名（counts-<來源>.pkl）
for F in <來源>_1.tsv <來源>_2.tsv …; do
  python3 experiments/derisk-1015/e1/count.py text --out <C> --tag <來源>-$(basename $F .tsv) --no-trad-tw-phrases --extra-lexicon <詞表> --tri-min 0.01 $F &
done; wait
python3 experiments/derisk-1015/e1/count.py merge --out <C> --tag <來源> <來源>-<檔1> <來源>-<檔2> …     # 每個來源一次：口語池、g0v、PTT 推文、PTT 內文

# 3. 建不剪的表（門檻 1）。--lm 是該臂的通語 bigram.sjlm（表綁它的雜湊）；--corpora 的檔名和權重照 bigram 的 --corpora，每個檔讀它的 tri-*.pkl
python3 experiments/derisk-1015/e1/build_models.py tri --counts <C>/unmapped --lm <臂>/bigram.sjlm --out <T> --thresholds 1 \
    --corpora counts-200000.pkl:1 counts-<口語池>.pkl:5 counts-<g0v>.pkl:5 counts-<ptt-push>.pkl:<W> counts-<ptt-body>.pkl:1      # 寫 <T>/tri-t1.sjt3、tri-sizes.json

# 4. 剪成四個大小（一次評分、四個 K 從同一個排名取；--kn 是該臂的 kn.sjkn，β 必須是 1）。7700964 ＝ 出貨 H3 的條目數
python3 experiments/derisk-1015/e1/entropy_prune.py --tri <T>/tri-t1.sjt3 --lm <臂>/bigram.sjlm --kn <臂>/kn.sjkn \
    --keep 7700964,3850000,1900000,950000 --labels K7.7M,K3.85M,K1.9M,K0.95M --out-dir <P>                                  # 寫 <P>/tri-<標籤>.sjt3 與 .json
```

量測：把 `<P>/tri-<標籤>.sjt3` 複製成一個資料夾裡的 `trigram.sjt3`，旁邊放該臂的 `bigram.sjlm`、`classes.sjc`、`kn.sjkn`，`shanjie-eval --lm <資料夾>/bigram.sjlm …`：

```sh
shanjie-eval --lm <資料夾>/bigram.sjlm --profile formal …                      # 書面：有表就用（KN 關）
shanjie-eval --lm <資料夾>/bigram.sjlm --profile chat …                        # 聊天：和以前一樣不讀表
shanjie-eval --lm <資料夾>/bigram.sjlm --profile chat --chat-tri on-kn …       # 實驗：聊天也用表，KN 開（摘要行有 +kn 與 +tri）
shanjie-eval --lm <資料夾>/bigram.sjlm --profile chat --chat-tri off-kn …      # 實驗：聊天也用表，KN 關
```

`--chat-tri` 只接受 `--profile chat`、不能和 `--no-tri` 並用、資料夾裡沒有 `trigram.sjt3` 時報錯（否則會靜默量到 bigram）。核心的開關是 `Lm::set_chat_tri(Some(kn))`，預設關，App 與 FFI 不呼叫。注意：維基切片的記憶體是 32 份各自的 tri 計數表，先用 `--articles 2000 --shard 0/32` 量一份的尖峰再決定同時跑幾份；`count.py` 沒有 `--holdout-guard`（`build_counts.py` 有），若 bigram 計數開了守門，連文要先確認輸入已經濾過。

## 輸入表（契約 §2.4；每一列的 SHA-256 在輸入凍結時填）

| 輸入 | SHA-256 |
|---|---|
| 維基 dump | |
| `colloquial-train.txt`（`--train-only-new-conv` 重產） | |
| g0v `#rand0m` 增量、PTT 內文、PTT 推文（和評測集去重後） | g0v `cedad812b4cd71964be00a13c3c64dad87d8295e44667ebac363feeb224389e4`（15,207 句）、PTT 內文 `f42d5c8b04dcbb8759b932975121e3a9745c0392bc804bdca734d0b781da1928`（5,343,729 句）、PTT 推文 `6568bc6696cd99b4bc0139f6cf9d8fa3608fa6ac02c41ee342d3355519cdd115`（8,665,059 句） |
| 字幕（§2.7，審查與去重後；會意用） | `e005cf44544909a0b6f0696e7b97a6a089b24755eadde142f00a3c0b4074cc1b`（4,432,866 句；`SHA-256(句子)` 第一個位元組 < 128 的一半 `9ee7cfb3296c66f23cbf57a68d34c9886de8c13d6ed67f98b80bc977d06afbf4`） |
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
