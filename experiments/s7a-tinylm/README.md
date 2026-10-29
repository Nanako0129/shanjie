# S7a：小型字元語言模型重排（離線）

契約：`docs/contracts/s7a-tinylm.md`（§5 的表是誰在哪台機器跑什麼）。這個資料夾是 executor 寫的全部程式、單元測試與命令；訓練與量測由 main 執行。不連網、不進 repo 的檔案（權重、中間檔、分數檔）放 `work\s7a\`。

## 檔案

| 檔 | 做什麼 | 需要 torch |
|---|---|---|
| `tinylm.py` | numpy 核心：字表（PAD 0、UNK 1、BOS 2）、`row_key`（`context_key`；16 字版）、`.npz` 格式（`save_params`）、numpy 前向（`NumpyLM`）、**唯一的 `LL`**（`ll_many`，序列 `[BOS] + k + c`） | 否 |
| `prep.py` | `runs`：核對輸入雜湊，產生訓練文字（維基＋口語，與 n-gram 同一組函式）；`encode`：字表（≥ 5 次）與 `ids-*.npy`；`text`（S7f）：另一個文字檔照口語的切法、用**現有**字表轉成 `ids-<name>.npy`；SJ-SR-7 加 `text --half`（只留 SHA-256(UTF-8) 第一個位元組 < 128 的句子；句子＝輸入檔的一行，去掉結尾換行、轉換之前，`.tsv` 只取最後一欄；整行的漢字段一起留或一起丟）、`text --runs-only`、`encode --extra NAME…`（字表把各檔 `runs-<NAME>.txt` 各算一次，不乘權重；不給 `--extra` 時 `vocab.txt`、`ids-wiki/coll.npy`、`encode-report.json` 與以前逐位元相同；另寫 `ids-vocab.json` 記字表與 ids 雜湊、extra 集合，`train.py` 開訓前核對；字表來自不同 extra 集合的目錄 `encode` 拒絕，除非 `--force`） | 否 |
| `buildcheck.py` | `construction`：對原始來源的建構檢查；`substr`：調參集正解句出現在訓練文字裡的比例（只記錄）；`--flags` 另寫逐列 0／1（S7d） | 否 |
| `batching.py` | 訓練批次（一段一個序列、同長度成批、超過 65 個 token 的切片重疊 1 個）；`build_epoch`／`plan`：`train.py` 的 epoch 與批次順序（S7f 起放這裡，沒有 torch 也能測；`--extra PATH[:WEIGHT]` 可重複，每段重複 WEIGHT 次、不寫權重＝1、依序接在最後；訓練一個 epoch、一批一步；每種長度的最後一批不滿，所以步數隨加權後總 token 增加但不嚴格成比例，接近程度沒量過） | 否 |
| `torch_model.py`、`train.py` | 模型、`export_npz`、torch 打分後端、訓練 | 是 |
| `score.py` | 候選（`--dump` 前 8 名）× `LL` → 只有數字的分數檔；`--ppl-out` 正解句困惑度 | 否（`--backend torch` 才要） |
| `run_sets.py` | 每個集合各跑一次 `lm_eval.py --dump` 與 `score.py`（一行命令跑完全部集合） | 否 |
| `parity.py` | 一致性關卡：兩份分數檔的最大 \|ΔLL\| | 否 |
| `fusegrid.py` | 格點、凍結選擇、rowstats、`evalstats` 表格、§3／§4 報告；`--tune-keep` 只用旗標 0 的調參列選（S7d）；`--break-weight W` 照「改對 − W × 改壞」在 (α, τ, δ) 上選、照 S7e §3 判斷，δ 是融合的門檻（S7e） | 否 |
| `latency.py` | numpy 延遲（8 候選 × 17 字，200 次）與 int8 大小 | 否 |
| `int8check.py` | S7b §2.3：`dequant`（權重 int8 還原成 float32 的複本）、`compare`（int8 對 n-gram 的 §4 與 int8 對 fp32 的表，結論「int8 夠用／需要 FP32／不適用」） | 否 |
| `export_sjtl.py` | S7c-1：`.npz` → `SJTL0001`，S7c-2c 起給 δ 時寫 `SJTL0002`，model-v6 起給 `--levels` 時寫 `SJTL0004`（可加 `--kn-side`）（Rust 讀的模型檔；檔案格式寫在檔頭的 docstring）；`read_sjtl` 供測試與 golden 產生器用 | 否 |
| `test_*.py` | 單元測試；`test_torch.py` 沒有 torch 時整檔跳過 | 只有 `test_torch.py` |

## 契約沒寫死、我做的決定

| 項目 | 做法 |
|---|---|
| 字表 | 訓練文字（維基＋口語各算一次，不乘 5）出現 ≥ 5 次的字，次數多的在前、同次數依碼位；其餘 UNK。BOS 只出現在輸入，不是目標 |
| 序列 | 每段（一個 `HAN` 連續漢字段，≥ 2 字）一個序列：`BOS` ＋ 漢字。**不把多段接在一起**（否則模型會把前一段當前文，打分時沒有）。長度 > 65 個 token 的段切成重疊 1 個 token 的片，每個字恰好是一次目標，只有第一片有 BOS（這個長度上限是決定，沒有量測過有多少段超過） |
| 批次 | 同一批只放同長度的序列（沒有 padding），每批約 32768 個 token（`--tokens-per-batch`）；批次順序隨機，種子固定 20261010 |
| 權重 | 維基各片 ×1、口語（`colloquial-train.txt` 加 `synth.txt`）各片 ×5，同一個 epoch 內打散 |
| 架構 | pre-LN decoder，tied embedding，可學習位置（64），LayerNorm eps 1e-5，GELU **tanh 形式**（numpy 沒有 erf），MLP 4d，沒有 dropout |
| 訓練 | AdamW（β 0.9／0.95，權重衰減 0.1 只給矩陣）、峰值 1e-3、**前 200 步線性暖機**再 cosine 到 0（契約只寫 AdamW＋cosine，暖機是偏離契約的決定；沒有跑過不暖機的對照，是否需要未量測）、梯度裁切 1.0、CUDA 上 bf16 autocast（損失用 float32）。`nan` 或暖機後損失高於起點 +1 就結束碼 2（發散） |
| 前文過長 | `[BOS] + k + c` 超過 64 個 token 時只丟掉 k 最舊的字；c 本身 > 63 字會報錯（調參集沒有這麼長的句子，遇到會停而不是默默截斷） |
| numpy 精度 | 檔案是 float32；`NumpyLM` 預設 float64 計算（`score.py` 用）以免 numpy 這邊的捨入進入一致性關卡；`latency.py` 兩種都量（float32 是之後 Rust 的合理參考） |
| 分數檔 | 每列一行 JSON，只有數字（候選分數、對錯、與正解的編輯距離、`LL`、不加前文的 `LL`、16 字前文的 `LL`），沒有任何句子，discordtune 也一樣 |
| 16 字前文那組 | 契約寫「另外一組只記錄」。我讓它**用同一個選法、自己在 cvtune＋wikitune 上選一組 (α, τ)**（主組的凍結值不動、也不影響 §4 判斷），表格與報告另列「(16-char context)」。若 main 想改成沿用主組的值，改 `fusegrid.run_setting` 的 `frozen["k16"]` 一行 |
| §4 停止條件「最佳格點都少於 +0.5」 | 解讀為：聊天設定、兩個大小的 cvtune＋wikitune 合計 top1 增益都 < +0.5 個百分點（`fusegrid.py` 印 `STOP CONDITION`，結束碼 3）。書面設定的增益照樣印在每個 `##` 標題行 |
| 「LL 有變」 | 前文 key 非哨兵的列裡，任一候選的 \|LL(k) − LL(無 k)\| > 1e-6。同時印「小模型單獨挑的結果有變的列數」。錯字回報檔或 discordtune 的「LL 有變」是 0 → `fusegrid.py` 印 `STOP: context not wired`、結束碼 3 |
| 小模型單獨挑 | 分母是該集合全部列，候選數 < 2 的列照第一名計 |
| 建構檢查的維基部分 | 維基的切分是條目序號（訓練 `articles(200000)`、wikitune 序號 ≥ 300,000，`build_tune.py` 把序號寫死在迴圈裡，不能 import），所以核對的是 `prep.py` 記在 `charcount.json` 的 `articles`（用 `--charcount` 給）不超過 300,000；沒給就在報告寫 `NOT CHECKED`，不宣稱檢查過。沒有再讀 30 萬篇。口語部分是真的逐行對原始來源算 `is_tune(raw)` |
| `score.py` 的基準核對 | `--expect-top1` 必填（CLI／`lm_eval.py` 摘要行的 `top1`），用 dump 第一名與 `lenient` 重算，不等就停 |

## main 的命令順序

記號：`REPO`＝這個分支的 checkout（188 與 Mac 各一份）；`W`＝`%USERPROFILE%\.cache\shanjie\work\s7a`（188）／`~/.cache/shanjie/work/s7a`（Mac）；`TUNE`＝放 `cvtune.txt`、`wikitune.txt` 的資料夾（188：`%M3%\tune`；Mac：把兩個檔拷到 `$W/tune`）；`LM`＝出貨的 model-v5（`f81a021e…`）所在的 `bigram.sjlm`，同資料夾要有 classes-v3 的 `classes.sjc`（`run_sets.py dumps` 不給 `--lm` 時用 `REPO/data/lm/bigram.sjlm`）。188 的 python：`%USERPROFILE%\ime-research\proto\.venv\Scripts\python.exe`（下面寫 `%PY%`），先 `set PYTHONUTF8=1`；Mac 的 python：`/opt/homebrew/bin/python3`。長時間的步驟照慣例 `start "" /b /wait /affinity FFF %PY% -u …`。

### 188（torch）

```bat
cd /d %REPO%\experiments\s7a-tinylm
set W=%USERPROFILE%\.cache\shanjie\work\s7a
set M3=%USERPROFILE%\.cache\shanjie\work\s2f4
set TUNE=%M3%\tune
set SYNTH=<synth.txt 的絕對路徑>

rem 1. 訓練前先跑單元測試（含 test_torch.py：numpy／torch 一致、匯出、訓練小型試跑），CPU 即可
%PY% -m unittest discover -s . -p "test_*.py"

rem 2. 第 0 步：訓練文字（會核對輸入雜湊，不符就停）→ 建構檢查（失敗就停）→ 字表與 ids → 記錄用的子字串比例
%PY% -u prep.py runs   --work %W% --coll %TUNE%\colloquial-train.txt --synth %SYNTH% --procs 10
%PY% -u buildcheck.py construction --coll %TUNE%\colloquial-train.txt --out %W%\construction-check.json
%PY% -u prep.py encode --work %W%
%PY% -u buildcheck.py substr --work %W% --rows %TUNE%\cvtune.txt %TUNE%\wikitune.txt

rem 3. 候選：兩種設定各一份 dump（出貨 model-v5＋classes-v3、--context、不開詞包）。--sets 內的 discordtune 不在 188 跑
%PY% -u run_sets.py dumps --work %W% --tune %TUNE% --lm %LM% --sets cvtune wikitune dev302 typing76 user-reported

rem 4. 訓練（各一次，不調超參數）。結束碼 2＝發散，停下來回報
%PY% -u train.py --size S --work %W% --out %W%
%PY% -u train.py --size M --work %W% --out %W%

rem 5. 公開集合的 LL 打分（torch）與正解句困惑度（cvtune、wikitune）
%PY% -u run_sets.py score --work %W% --tune %TUNE% --backend torch --models S M --out %W%\scores188 --ppl --sets cvtune wikitune dev302 typing76 user-reported
```

拷回 Mac 的 `$W/`：`tinylm-S.npz`、`tinylm-M.npz`、`train-S.json`、`train-M.json`、`scores188/`、`ppl/`、`dumps/`（cvtune 的就夠做一致性關卡；公開集合的 dump 不是必須）、`construction-check.json`、`encode-report.json`、`charcount.json`，還有 `cvtune.txt`、`wikitune.txt`（放 `$W/tune/`）。

### Mac（numpy，不需要安裝任何東西）

```sh
cd $REPO/experiments/s7a-tinylm
W=~/.cache/shanjie/work/s7a
PY=/opt/homebrew/bin/python3

# 6. 一致性關卡：cvtune 聊天全部候選，numpy 重算，對 188 的 torch；任一 |ΔLL| ≥ 1e-4 就停（結束碼 1）
$PY run_sets.py score --work $W --tune $W/tune --backend numpy --models S M --out $W/scores-mac --sets cvtune
$PY parity.py $W/scores188/S.chat.cvtune.jsonl $W/scores-mac/S.chat.cvtune.jsonl
$PY parity.py $W/scores188/M.chat.cvtune.jsonl $W/scores-mac/M.chat.cvtune.jsonl

# 7. discordtune（一致性關卡通過之後）：自己的候選與 numpy 分數，檔案放在和 188 的分數同一個資料夾；只回數字，用完刪掉
mkdir -p $W/scores && cp $W/scores188/*.jsonl $W/scores/
$PY run_sets.py dumps --work $W --discord <discordtune 的檔> --sets discordtune --lm <LM>
$PY run_sets.py score --work $W --discord <discordtune 的檔> --backend numpy --models S M --out $W/scores --sets discordtune

# 8. 融合格點與表格（公開集合的分數用 188 的、discordtune 用 Mac 的）
$PY fusegrid.py --scores $W/scores --out $W/fusion --models S M --settings chat formal --sets cvtune wikitune dev302 typing76 user-reported discordtune

# 9. 延遲與 int8 大小（S、M 各一）
$PY latency.py --npz $W/tinylm-S.npz
$PY latency.py --npz $W/tinylm-M.npz

# 10. 用完：刪掉 discordtune 的分數檔、dump 與 rowstats（rowstats 只含 [0-9\t\n]）
rm $W/scores/*.discordtune.jsonl $W/dumps/discordtune.* $W/fusion/rowstats/*.discordtune.*
```

### 輸出怎麼對應契約 §3

| §3 | 在哪裡 |
|---|---|
| 1 輸入雜湊、列數 | `prep.py runs` 印出的 `input_sha256`（存 `charcount.json`）；`construction-check.json`（含來源檔雜湊、行數）；`buildcheck.py substr` 的三行；`score.py` 每個集合印一行「rows、dump sha256、dump candidates、n-gram top1 from the dump N、summary line N」（候選檔雜湊與列數；兩個 top1 不等就停） |
| 2 訓練 | `train-S.json`／`train-M.json`（參數量、步數、最終 loss＝最後兩個 100 步區間的平均，nats／token）；`prep.py runs`／`encode` 的字數與字表大小；`ppl/<model>.<set>.json`（每字困惑度） |
| 3 表格與報告 | `fusegrid.py` 的輸出：每個大小 × 設定的凍結 (α, τ)、每個集合一行 `evalstats` 表、觸發列數、前 8 名 oracle、小模型單獨挑、前文 key 非哨兵的列數、LL 有變的列數；16 字前文另列 |
| 4 一致性 | `parity.py` 的 `ll` 行 |
| 5 延遲與大小 | `latency.py` |
| 6 判斷 | `fusegrid.py` 的 `section 4` 逐項 PASS／FAIL（只涵蓋 discordtune 與護欄格；M 要使用者同意多約 30 MB、M 的 p95 > 150 ms 只考慮 S，這兩項看 `latency.py` 與使用者） |

## S7b：L、XL 與 int8

契約 `docs/contracts/s7b-larger-lm.md`；結果在研究紀錄同日 S7b 一節。188 用 S7a 同一份訓練文字與 ids；Mac 的 `OUT`＝`$W/s7b`。

```sh
# 188：訓練與公開集合的 torch 打分（錯字回報固定 S7a 的前 66 列）
%PY% -u train.py --size L --work %W% --out %W%
%PY% -u run_sets.py score --work %W% --tune %TUNE% --backend torch --models L --out %W%\scores188 --ppl --reported-rows 66 --sets cvtune wikitune dev302 typing76 user-reported

# Mac：一致性關卡（cvtune 兩種設定）、discordtune（只回數字、用完刪除）、格點、對 M、int8
$PY run_sets.py score --work $W --tune $W/tune --backend numpy --models L --out $W/scores-mac-L --sets cvtune
$PY parity.py <188 的 L.chat.cvtune.jsonl> $W/scores-mac-L/L.chat.cvtune.jsonl      # formal 同樣一次
cp <188 的 scores188/L.*.jsonl> $OUT/scores/
$PY run_sets.py score --work $W --discord <discordtune 的檔> --backend numpy --models L --out $OUT/scores --sets discordtune
$PY fusegrid.py --scores $OUT/scores --out $OUT/fusion --models L --settings chat formal --sets cvtune wikitune dev302 typing76 user-reported discordtune
# L 對 M：M 的 rowstats 來自 M 的 int8 比對（S7a 凍結的 0.7／2 重排 M 的分數，檔名 M8.<設定>.<集合>.fp32.txt），L 的來自 fusegrid
python3 ../../tools/evalstats.py compare $OUT/int8/rowstats/M8.chat.discordtune.fp32.txt $OUT/fusion/rowstats/L.chat.discordtune.new.txt --label "L vs M chat discordtune"
# int8：還原複本、所有集合打分、比對（α、τ 必須是 fp32 自己選出的值，工具會核對）
$PY int8check.py dequant --npz $W/tinylm-L.npz --out $W/tinylm-L8.npz
$PY run_sets.py score --work $W --tune $W/tune --discord <discordtune 的檔> --backend numpy --models L8 --out $OUT/scores --reported-rows 66 --sets cvtune wikitune dev302 typing76 user-reported discordtune
$PY int8check.py compare --scores $OUT/scores --fp32 L --int8 L8 --chat 2 2 --formal 1 2 --sets cvtune wikitune dev302 typing76 user-reported discordtune --out $OUT/int8-L
```

M 的 int8 比對同樣用 `--fp32 M --int8 M8 --chat 0.7 2 --formal 0.7 2`，輸出在 `$OUT/int8`。

## S7d：只用和訓練文字沒有整句重疊的調參列選 α、τ

契約 `docs/contracts/s7d-tune-overlap.md`。

```sh
# 188（訓練文字在那裡）：每個調參集一個 0／1 旗標檔，第 i 行對應 score.py 的列號 i
%PY% -u buildcheck.py substr --work %W% --rows %TUNE%\cvtune.txt %TUNE%\wikitune.txt --flags %W%\s7d-flags
# 拷回 Mac：%W%\s7d-flags\cvtune.flags、wikitune.flags → $W/s7d-flags/
# Mac：重現關卡（不加 --tune-keep，必須等於 S7b），再只用旗標 0 的列選
$PY fusegrid.py --scores $OUT/scores --out $W/s7d/repro --models M L --settings chat formal --sets cvtune wikitune dev302 typing76 user-reported discordtune
$PY fusegrid.py --scores $OUT/scores --out $W/s7d/keep --models M L --settings chat formal --sets cvtune wikitune dev302 typing76 user-reported discordtune --tune-keep $W/s7d-flags
# 用完：刪掉 discordtune 的候選、分數、rowstats（$W/dumps/discordtune.*、$OUT/scores/*.discordtune.jsonl、各 rowstats 資料夾的 *discordtune*）
```

## S7e：照「改對 − W × 改壞」選 (α, τ, δ)

契約 `docs/contracts/s7e-fewer-breaks.md`。公開集合沿用 S7b 的 M8 分數（cvtune、wikitune、dev302、打字測驗、錯字回報前 66 列）；新打分的是錯字回報 72 列、功能字測試句 `fw`、discordtune。

```sh
cd experiments/s7a-tinylm
W=~/.cache/shanjie/work/s7a; PY=/opt/homebrew/bin/python3
# 候選與分數（fw 的前 186 列會先核對 SHA-256）
$PY run_sets.py dumps --work $W/s7e-work --sets user-reported fw [discordtune --discord FILE]
$PY run_sets.py score --work $W/s7e-work --out $W/s7e-work/scores --models M8 --backend numpy --sets user-reported fw [discordtune --discord FILE]
# 分數資料夾：S7b 的 M8.*.{cvtune,wikitune,dev302,typing76}.jsonl 加上 s7e-work 的 user-reported、fw、discordtune（symlink 到 $W/s7e/scores）
# 重現關卡（不給 --break-weight，必須選回 0.7／2，契約 §2.2 的 10 格）用 S7b 的 66 列錯字回報，另一個 --out
$PY fusegrid.py --scores $W/s7b/scores --out $W/s7e/gate --models M8 --sets cvtune wikitune dev302 typing76 user-reported
# 選與判斷（兩種模式寫同名的 .base／.new，所以各用自己的 --out）
$PY fusegrid.py --scores $W/s7e/scores --out $W/s7e/w10 --models M8 --sets cvtune wikitune dev302 typing76 user-reported fw discordtune --break-weight 10
# 用完：刪掉 discordtune 的候選、分數與 rowstats（find $W/s7e-work $W/s7e -name '*discordtune*'）
```

- `score.py` 每個候選算 ll、ll0、ll16 三欄。這個模式用 ll 選與判斷，用 ll0 檢查前文有沒有接上（「LL changed by context」，錯字回報與 discordtune 是 0 就停），ll16 沒用到。
- L、XL 是在 Colab 上用 `train.py --size L|XL`、同一份 `ids-*.npy` 與 `vocab.txt` 訓練，公開集合也在 Colab 上打分（研究紀錄 2026-10-12 記了上傳的限制）；discordtune 只在 Mac 本機打分。

## Colab：資料怎麼傳（2026-10-12 實測，使用者定案）

VM 在美國（實測時在南卡羅來納州）。訓練只傳公開的程式與資料。私人評測集（discordtune、E2 的聊天列檔）從 2026-10-13 起可以上 Colab 算評測（維護者：「colab可以放」，`CLAUDE.md`「Mac 不跑重運算」），只用來評測、不進訓練；學習檔與原始聊天紀錄不上。

| 路徑 | 實測 | 用在 |
|---|---|---|
| VM 自己抓公開資料：維基 dump 走 your.org 鏡像、8 路平行分段 | 約 39.5 MiB/s（官方 dumps.wikimedia.org 單線 4.8 MiB/s，平行分段會被截短） | 維基 dump |
| VM 自己抓：Hugging Face／GitHub | 約 54.5／12.5 MiB/s | 資料集、程式 |
| Mac → S4（rclone）→ VM（有時效的下載網址） | 約 33 MiB/s ＋ 約 15.7 MiB/s | 只在 Mac 上的檔案（自己的衍生檔、模型） |
| `colab download`（VM → Mac） | 約 3.2 MiB/s | 結果（幾百 MB 以內） |
| `colab upload`（Mac → VM，16 MiB 分片） | 約 2.2 MiB/s | 不用 |
| Google Drive（`colab drivemount`） | Mac → Drive 16.8、Drive → VM 32.5、VM → Drive 13.1 MiB/s | 不用：每個 session 都要使用者授權一次 |

- 有時效的網址用 `rclone link --expire 1h`，在 Mac 上產生、存成只有自己能讀的檔、不印出來；金鑰不離開 Mac。用完刪掉嵌著網址的腳本。
- CPU 的 session（不帶 GPU）在實測時沒有扣運算單元。

## S7c-1：Rust 推論（`SJTL0001`）

契約 `docs/contracts/s7c-core-tinylm.md`。`.sjtl` 不進 repo（只有 `eval/golden/tinylm-tiny/` 的隨機小模型例外），放 `~/.cache/shanjie/work/` 或 session 暫存區，**不放進任何 `data/lm/`**。

```sh
cd experiments/s7a-tinylm
W=~/.cache/shanjie/work/s7a
LM=<model-v5 的 bigram.sjlm，同資料夾要有 classes.sjc>      # 調 α、τ 時的 n-gram 模型；它的 SHA-256 寫進檔頭
# 匯出（α、τ 是 S7a 凍結的值，必填，沒有預設）。int8 與 f32 各一個檔
/opt/homebrew/bin/python3 export_sjtl.py --npz $W/tinylm-M.npz --out $W/tinylm-M8.sjtl  --type int8 --ngram-model $LM --chat 0.7 2 --formal 0.7 2
/opt/homebrew/bin/python3 export_sjtl.py --npz $W/tinylm-M.npz --out $W/tinylm-M32.sjtl --type f32  --ngram-model $LM --chat 0.7 2 --formal 0.7 2
# 評測 CLI：加 --tinylm 就用重排後的第一名算 top1、--rowstats、摘要行（摘要行多 +tl:<.sjtl 的 sha8>）
cd ../..
cargo run --release -q -p cli -- --lm $LM --profile chat --context --dev 302 --rowstats OUT --tinylm $W/tinylm-M8.sjtl     # 或 --rows FILE／--set holdout
# 每列的前 8 名與 LL（只有數字；score.py 的欄位去掉 key／key16／ll0／ll16），給 parity.py 與 fusegrid.pick／rowstats 讀
cargo run --release -q -p cli -- --lm $LM --profile chat --context --rows $W/tune/cvtune.txt --tinylm $W/tinylm-M8.sjtl --tinylm-scores OUT.jsonl
```

- `--tinylm` 要求載入的 n-gram 模型的 SHA-256 等於檔頭記的，而且 KN 側檔的狀態和檔頭記的相同（`docs/contracts/model-v6.md` §2.7）：`SJTL0004` 記了側檔的 SHA-256，就要開著**那一個** `kn.sjkn`；記的是全 0，或是 `SJTL0001`～`0003` 的檔，就不能開 KN。不符時結束碼 1、訊息點名 `.sjtl`，不輸出結果；KN 的三種不符各有自己的訊息與建議：KN 開著但檔是在沒有 KN 時調的（加 `--no-kn`，或改用 `--kn-side` 匯出的檔）、KN 開著但側檔不是調參時那一個（換回那個 `kn.sjkn`）、檔是在 KN 開著時調的但 KN 沒開（把那個 `kn.sjkn` 放回模型旁邊、拿掉 `--no-kn`）。
- `export_sjtl.py` 給了 `--levels` 就寫 `SJTL0004`：加 `--kn-side <kn.sjkn>` 時位移 212 寫進側檔的 SHA-256（寫檔前先核對側檔是 `SJKN0002`、記的模型雜湊等於 `--ngram-model`），沒給時寫 32 bytes 全 0；沒給 `--levels` 照舊寫 `SJTL0001`／`0002`；只給 `--kn-side` 是用法錯誤。
- `k` 是 `context_key(前文)`（`--context`）或空；`context_key` 回哨兵 `"^"` 時 k 是空字串，和 `tinylm.row_key` 一致。
- 沒有 `--tinylm` 時輸出逐位元組不變。有的時候 stderr 多一行 `tinylm: rerank …`（每列重排時間 p50／p95、載入時間、載入前後的 RSS），給契約第 6 項的紀錄用。
- 一致性：f32 檔對 `tinylm-M.npz`、int8 檔對 `tinylm-M8.npz`（`int8check.dequant` 的還原權重）的 numpy LL，用 `--tinylm-scores` 的輸出和 `score.py` 的輸出跑 `parity.py`（`ng` 容許 1e-9，`i`、`n` 要相同）。
- 小模型 golden 的產生：`python3 -B tools/gen_tinylm_tiny.py`（`eval/golden/tinylm-tiny/`；Rust 測試 `core/tests/tinylm_tiny.rs`）。

## 測試

```sh
cd experiments/s7a-tinylm
/opt/homebrew/bin/python3 -W ignore -m unittest discover -s . -p 'test_*.py'
```

沒有 torch 時 `test_torch.py` 整檔跳過（6 項）；其餘全部要過。`test_prep.py` 讀 `~/.cache/shanjie/sources/opencc` 的 OpenCC 檔（`build_counts` 本來就要），約 10 秒。

突變檢查（暫時改一份拷貝，不改 repo 的檔；每項都是斷言失敗，不是匯入錯誤）：

| 突變 | 失敗的測試 |
|---|---|
| `LL` 不放前文 k | `test_hand_values`、`test_context_changes_ll`、`test_exact_input_sequence`、`test_overlong_context_is_trimmed_from_the_left`（共 6 項） |
| 融合變成不做事（`pick` 回 0） | `test_alpha_threshold`、`test_fusion_actually_reranks`、`test_tau_gate_is_strict_less_than`、`test_end_to_end`（共 7 項） |
| 門檻 `>=` 改 `>` | `test_tau_gate_is_strict_less_than` |
| 選法同分取較大的 α | `test_tie_rule_smaller_tau_then_smaller_alpha`、`test_all_equal_picks_the_first_grid_cell` |
| 選法也讀 dev302 | `test_selection_reads_only_the_tuning_sets` 等 |
| 同分取較後的 n-gram 名次 | `test_ties_go_to_the_better_ngram_rank` |
| 建構檢查對轉換後的句子算 `is_tune` | `test_check_uses_the_raw_line` |
| `wiki_runs` 少做 TEMPLATE | `test_wiki_runs_equal_count_batch_runs` |
| numpy 前向沒有因果遮罩 | `test_causal`、`test_matches_loop_reference`、`test_pad_and_batch_invariance` |
| GELU 常數改錯 | `test_matches_loop_reference` |
| 字表外的字被丟掉而不是 UNK | `test_unk`、`test_ids` |

S7c-1 的 Rust 測試（`cargo test --release`）與突變檢查見契約的結果一節；Python 這一側的 `test_export_sjtl.py` 檢查檔頭、字表、張量與 int8 還原（`q.astype(float32) * scale`）。

沒有 torch 就無法在這台 Mac 驗證的部分：`torch_model.py`、`train.py` 只做過語法檢查（`py_compile`）與逐行對照，`test_torch.py` 的 6 項要在 188 上第一次跑；其中 numpy／torch 的 `LL` 差 < 1e-4（經過 `export_npz`）是契約的單元測試項目。

## CTX64 的訓練工具選項（探索片；全部預設關，不給時輸出與以前逐位元相同）

App 送給會意 SJ-SR-7-15M 的前文是最多 64 字的原文（含標點、英數；`core/src/tinylm.rs` 的 `key_for`、SJTL0005），所以訓練也要看過這樣的文字。以下四個選項只改工具，沒有跑任何訓練或語料處理。

| 選項 | 做什麼 |
|---|---|
| `prep.py text --raw` | **必須搭配 `--runs-only`**（否則直接報錯：不帶時會沿用 work dir 裡只有漢字的舊字表，標點悄悄變 UNK），字表與 ids 由 `encode --extra NAME` 算。每個輸入行變成一行訓練文字：OpenCC 轉換後的**原文**（標點、ASCII、數字、全形都留，只去頭尾空白，空行丟掉；`.tsv` 取最後一欄）。另外，行內沒有任何 2 字以上漢字段的行（只剩 ASCII 或單字碎片）不留，報告記在 `raw_no_han_lines`；空白行先跳過，不進 `--half` 的判斷。不再切成 2 字以上的漢字段。holdout guard 照舊：行內**任何**一個 2 字以上的漢字段中了守門，整行丟（沿用 `bc.guard_matcher` 與 `bc.fold`，沒有新守門）。`--half` 照整行判。報告多一個 `raw: true`，`holdout_guard_skipped_runs`／`half_dropped_runs` 在這個模式算「行」 |
| `prep.py runs --raw` | 三個來源都留整行原文：維基用 `bc.article_sentences`（照 。！？； 斷句、句內標點保留；只在 raw 路徑多一層：守門之後，含 `==`、`|`、`[[`、`]]`、`{{`、`}}` 或 `Category:`、`File:` 這類命名空間前綴的句子丟掉並記在 `wiki.markup_lines`，開頭的清單符號 `* # : ;` 與後面的空白去掉），口語池（`--coll`）與 `--synth` 的每一行用 `raw_line`（單位和預設路徑一樣，是 `build_counts_text.lines` 給的句子）。守門、「至少一個 2 字以上漢字段」、頭尾空白都和 `text --raw` 相同；報告裡 `wiki`、`colloquial-train.txt`、`synth.txt` 各自多 `guard_skipped_lines` 與 `no_han_lines`，總數改記在 `holdout_guard_skipped_lines`（不再是 `holdout_guard_skipped_runs`），另有 `raw: true`；這時 `runs` 的 `runs`／`chars` 是行與字元。口語池仍乘 5 |
| `encode --extra NAME` | 本來就把 `runs-NAME.txt` 的每個字元各算一次，所以 raw 檔裡出現 ≥ 5 次的標點與 ASCII 會進字表。例外：U+FFFF 以上的字元（emoji 等）不進字表（`make_lut` 只有 BMP 表），一律 UNK；漢字段沒有這種字，舊輸出不變 |
| `train.py --bos-every-piece`（`batching.rebos`／`plan(…, bos_every_piece=True)`） | 超過 65 個 token 的序列切段時，**每一段**都以 BOS 開頭（每段 = BOS + 64 字，每個字仍剛好當一次目標）；預設只有第一段有 BOS。核心推論永遠送 `[BOS] + 最後 L 字 + 候選`，BOS 就是截斷點。沒有長序列時回傳原陣列的相同位元 |
| `export_sjtl.py --left-len L` | ctx 64 時 L 最多 62。App 送最多 64 字，核心取最後 L 個字元（`core/src/tinylm.rs` `TinyLm::key_for`，`docs/contracts/context-return.md` 第 2 節）。需要 `--levels`（版本 4 的基底）。寫 SJTL0005：版本 4 的 244 位元組表頭（魔數 `SJTL0005`、版本 5）＋ 偏移 244 的 u32 L ＋ 其餘；`1 ≤ L ≤ ctx − 2`，否則在寫檔前報錯。沒給時輸出仍是版本 1 到 4。`read_sjtl` 讀得回 `left_len` |

測試：`test_extra.py` 的 `TestRawMode`（raw、守門、整行 half、字表、`rebos`、預設逐位元不變）與 `test_export_sjtl.py` 的 `--left-len` 兩個測試（位元組逐一對照 `core/tests/tinylm_tiny.rs` 的 `v5` 組法）。


## CTX64-B 的續訓選項（探索片；全部預設關，不給時輸出與以前逐位元相同）

| 選項 | 做什麼 |
|---|---|
| `prep.py encode --base-vocab FILE` | 字表 = FILE（一份 vocab.txt）的字、照 FILE 的順序（再罕見也留），後面接「一般規則會收（同樣輸入下次數 ≥ min-count、同樣排除 U+FFFF 以上）而 FILE 沒有」的字，順序照一般規則。`ids-vocab.json` 與 `encode-report.json` 多 `base_vocab`：`sha256`（FILE 的）、`size`、`appended`。NUL（U+0000）照樣保留 |
| `train.py --init NPZ` | 從 tinylm npz 開始訓練（`torch_model.from_npz` 載入）。npz 的 `chars` 必須是 work dir 的 vocab.txt 的前綴，且層數、寬度、ctx、頭數要和 `--size` 相同，否則訓練前就報錯。所有權重照抄；新增字的 `emb` 列 = npz 全部字（id ≥ 3）列的平均 + 高斯雜訊（std = 0.02 × 整個 `emb` 的 std，`default_rng(--seed)`）。`--max-steps` 本來就把餘弦排程縮到那一步結束（`total = len(batches)`），續訓不用另外處理 |
| `train.py --peak-lr X` | 峰值學習率，預設 1e-3；不是有限正數在解析參數時就拒絕。`train-<size>.json` 的 `peak_lr` 一律記實際值；用了 `--init` 才多 `init_npz_sha256`、`appended_rows` |

限制：新增字的 `emb` 列用「舊字列的平均加雜訊」當初始值，是設計選擇，沒有量過比別的初始化好。罕見、或沒出現在新語料裡的 base 字，它們的列拿不到梯度，只會受 weight decay 影響（逐步縮小）。`--base-vocab` 的檔案最後一行也要以換行結尾，否則拒絕（不然最後一個字會被悄悄丟掉）。`--init` 的檢查（前綴、設定）都在載入語料前做。

測試：`test_extra.py` 的 `TestBaseVocab`（合併順序、預設不變、列初始化、非前綴被拒，純 numpy）；`test_torch.py` 的 `test_init_from_npz_with_appended_vocab`（需要 torch，這台 Mac 沒有，會被跳過）。
