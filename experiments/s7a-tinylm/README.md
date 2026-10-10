# S7a：小型字元語言模型重排（離線）

契約：`docs/contracts/s7a-tinylm.md`（§5 的表是誰在哪台機器跑什麼）。這個資料夾是 executor 寫的全部程式、單元測試與命令；訓練與量測由 main 執行。不連網、不進 repo 的檔案（權重、中間檔、分數檔）放 `work\s7a\`。

## 檔案

| 檔 | 做什麼 | 需要 torch |
|---|---|---|
| `tinylm.py` | numpy 核心：字表（PAD 0、UNK 1、BOS 2）、`row_key`（`context_key`；16 字版）、`.npz` 格式（`save_params`）、numpy 前向（`NumpyLM`）、**唯一的 `LL`**（`ll_many`，序列 `[BOS] + k + c`） | 否 |
| `prep.py` | `runs`：核對輸入雜湊，產生訓練文字（維基＋口語，與 n-gram 同一組函式）；`encode`：字表（≥ 5 次）與 `ids-*.npy` | 否 |
| `buildcheck.py` | `construction`：對原始來源的建構檢查；`substr`：調參集正解句出現在訓練文字裡的比例（只記錄） | 否 |
| `batching.py` | 訓練批次（一段一個序列、同長度成批、超過 65 個 token 的切片重疊 1 個） | 否 |
| `torch_model.py`、`train.py` | 模型、`export_npz`、torch 打分後端、訓練 | 是 |
| `score.py` | 候選（`--dump` 前 8 名）× `LL` → 只有數字的分數檔；`--ppl-out` 正解句困惑度 | 否（`--backend torch` 才要） |
| `run_sets.py` | 每個集合各跑一次 `lm_eval.py --dump` 與 `score.py`（一行命令跑完全部集合） | 否 |
| `parity.py` | 一致性關卡：兩份分數檔的最大 \|ΔLL\| | 否 |
| `fusegrid.py` | 格點、凍結選擇、rowstats、`evalstats` 表格、§3／§4 報告 | 否 |
| `latency.py` | numpy 延遲（8 候選 × 17 字，200 次）與 int8 大小 | 否 |
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

沒有 torch 就無法在這台 Mac 驗證的部分：`torch_model.py`、`train.py` 只做過語法檢查（`py_compile`）與逐行對照，`test_torch.py` 的 6 項要在 188 上第一次跑；其中 numpy／torch 的 `LL` 差 < 1e-4（經過 `export_npz`）是契約的單元測試項目。
