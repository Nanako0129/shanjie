# E2：讀音條件小模型（tinylm-v6 的去風險）

契約：`docs/contracts/derisk-1015.md` §2、§3 E2、§4（E2 的單元測試性質）、§5（停止條件）。這個資料夾是 executor 寫的全部程式、單元測試與命令；訓練與打分由 main 執行（Mac 不跑重運算）。S7a 的程式一律不改，只 import：`experiments/s7a-tinylm/`、`experiments/e2-chateval/judge.py`、`tools/readings.py`、`tools/evalstats.py`。

## 檔案

| 檔 | 做什麼 | 需要 torch |
|---|---|---|
| `label_readings.py` | 訓練文字的讀音標注：`tools/readings.py` 規則 0、1、3、4（**沒有規則 2**）；`--work` 對 S7a 的 `runs-*.txt` 產生對齊 `ids-*.npy` 的 `rd-*.npy`、`rdvocab.txt`、`rd-report.json`（含「無讀音」比例）；`--sample` 只印比例 | 否 |
| `tinylm_r.py` | numpy 核心：`RVocab`、`target_aligned`、`mask_context`／`sample_splits`（訓練樣本）、`build_item`／`row_readings`／`ll_many_r`（打分）、`NumpyLMR`（含讀音表的前向）、`.npz` 格式 | 否 |
| `torch_r.py`、`train_r.py` | `GPTR`（S7a 的 `GPT` 加讀音表）、匯出、torch 打分後端；Colab G4 的訓練腳本 | 是 |
| `score_r.py` | `score.py` 的延伸：同樣的候選（`lm_eval.py --dump` 前 8 名）、同樣的記錄與檔案格式；`--arm control`（v5 權重、不開讀音）或 `--arm new`；`one`（單一集合）、`sets`（`run_sets.py` 那組集合 × 兩種設定） | 否（`--backend torch` 才要） |
| `anchor.py` | E2 錨點：控制臂設定的延伸版對 `score.py`，用 `parity.py`，max \|ΔLL\| < 1e-4，不符結束碼 1 | 否 |
| `judge_e2.py` | 選值（兩次 `judge.py` 呼叫，一種設定一次）與判讀（串接 rowstats、`evalstats compare --net`） | 否 |
| `split_dist.py` | 評測列的前文長度分布（給 `--split-probs`） | 否 |
| `test_e2.py`、`test_torch_r.py` | 單元測試（合成小資料）；後者沒有 torch 整檔跳過 | 後者 |

## 設計（契約沒寫死、我做的決定）

| 項目 | 做法與理由 |
|---|---|
| 讀音輸入的位置 | 讀音 embedding 加在**輸入 embedding** 上，但接的是「這個位置要預測的那個字」的讀音（目標對齊），不是同位置那個字的讀音。理由：因果語言模型在位置 t 看到的是第 t 個字、預測第 t+1 個字；若把第 t 個字自己的讀音加在位置 t，模型在預測第 t+1 個字時完全看不到使用者打的讀音，讀音輸入就沒有作用。資料以「字對字」存（`rd[t]` ＝ token t 的讀音，長度與 token 序列相同），餵模型前位移一格（`tinylm_r.target_aligned`），位置 0（預測第一個字）拿到第一個字的讀音。這是契約允許的「等價做法並寫明」；因果性有測試（改第 j 個字的讀音，只影響預測第 j 個字的位置與其後） |
| 「無讀音」記號 | id 0。讀音表第 0 列固定為 0 向量、不訓練（`padding_idx=0`）。所以讀音全為 0 時，模型與 S7a 的字元模型逐位元相同（測試：`NumpyLMR` 全無讀音的 LL 與 `NumpyLM` 相等，`np.array_equal`） |
| 讀音表 | 詞庫（`data/lexicon/mcbpmf-data.txt`）讀音裡出現過的全部注音音節，排序後 id 從 1 起，存在 `rdvocab.txt` 與 `.npz`（`rd_syl`），模型檔自己就定義了表 |
| 參數量 | M 的參數加 R × d（R ＝ 音節數 ＋ 1 ＝ 1,419，量過現在的詞庫是 1,418 個音節；× 384 ≈ 0.54 M）。其餘（層數、d、頭數、步數、批次、學習率、暖機、權重 ×1 維基／×5 口語、字表）與 M 完全相同 |
| 訓練步數與批次 | 直接呼叫 `batching.plan`，同種子（20261010）、同 `--tokens-per-batch`，所以批次內容與順序和 M 相同；`train-Mr.json` 的 `steps` 必須等於 `train-M.json` 的。不加 `--max-steps` |
| 切點與前文長度分布 | 評分時序列是 `[BOS] + k + c`，k ＝ `context_key`（尾端連續漢字、**最多 2 字**），所以評測的前文長度只有 0、1、2。訓練樣本（epoch 裡的每一片）隨機抽 s ∈ {0, 1, 2}：BOS 與前 s 個字是前文、填「無讀音」，其後帶讀音（`mask_context`）。機率 P(s) ＝ 評測列的 `key` 欄分布：`split_dist.py` 對 v5 的分數檔（聊天設定、各集合）算出，傳給 `train_r.py --split-probs`。公開的 `eval/dev/*.txt` 489 列的分布是 s＝0：351、s＝1：0、s＝2：138（0.718／0／0.282），只作為沒有分數檔時的粗略值。超過 65 個 token 的段切成的後續片沒有 BOS：它的第一個字是上一片的最後一個字，同樣套用規則，所以那些片的有效前文多 1 字（只影響長段，未量測有多少） |
| 標注規則 | 與 `tools/readings.py` 同一份資料（詞庫、`reading_overrides.tsv`、`heterophony1.list`）與同一個斷詞（`ime.segment_words`），同一條規則順序 0、1、3、4；規則內容是 `main()` 裡分支的複本（它們在函式內，無法 import），`test_e2.py::TestReadingsParity` 在公開句子上跑兩邊、斷言有讀音的位置完全相同（用空的萌典跑 `readings.main`，等於關掉規則 2）。規則 5（CHECK）的詞、人工表讀音不在詞庫（GAP）的詞、詞庫沒有的字：該詞每個字都是無讀音。詞庫沒有的字把句子切開，每一段分別斷詞 |
| 無讀音比例 | 公開的 `eval/dev/*.txt` 489 句（4,075 字）：3.2%（CHECK 132 字；GAP、詞庫外 0）。訓練文字（維基＋口語）的比例由 `rd-report.json` 記錄；維基的人名、外文音譯會比這個高〔推論，未量測〕 |
| 評分的讀音 | 候選位置用評測列第三欄（使用者打的讀音），同一列每個候選共用。前文（BOS 與 k）一律無讀音。某列有任何候選的字數 ≠ 讀音音節數，或有音節不在讀音表：該列**所有候選都用無讀音**，同一列的候選不會一部分有讀音一部分沒有；數量印在每個集合的輸出（「rows scored without readings」），要記進研究紀錄 |
| 分數檔 | 與 `score.py` 同欄位（`ll`、`ll0`、`ll16`、`key`…），所以 `judge.py`、`fusegrid.py`、`parity.py` 直接讀。`ll0`、`ll16` 的讀音規則相同（k 空／16 字，都無讀音）。E2 的選值與判讀只用 `ll` |
| 控制臂 | `tinylm-M.npz`（tinylm-v5 匯出用的權重）走 S7a 原本的 `NumpyLM`／`TorchLM`，沒有讀音通道；`ll_many_r` 對它退化成 `tinylm.ll_many`（測試：逐位元相同） |
| 候選來源 | `run_sets.py dumps` 產生的 `dumps/<集合>.<設定>.dump`（`lm_eval.py --dump`，有候選字串）與 `.top1`。Rust 的 `--tinylm-scores` 檔只有數字、沒有字串，不能當候選來源 |
| 串接順序 | 契約說「依 §2 串接」：判讀五集依 §2 列出的順序 dev302、typing76、user-reported、lytune、wikitypo，聊天主判讀最後接 discordtune。兩臂同一個順序。串接用 `judge_e2.concat_rowstats`（列號重編），這是 E2 自己的小函式；`experiments/derisk-1015/` 共用的串接工具若已存在，兩者輸出應逐位元組相同，main 擇一即可 |

## 測試

```sh
cd <repo>   # Mac：/Users/nanako/side-project/shanjie-dr2 或合併後的 checkout
/opt/homebrew/bin/python3 -W ignore -m unittest discover -s experiments/derisk-1015/e2 -p 'test_e2.py' -v
```

numpy only，約 3 秒。`TestReadingsParity` 需要真的詞庫與 McBopomofo 的 `heterophony1.list`（`SHANJIE_HETERO` 或預設位置），沒有就跳過。涵蓋契約 §4 的 E2 四項：讀音與字對齊（長度相同）、前文位置在訓練與評分都是無讀音、CHECK／詞庫外的字是無讀音、讀音輸入關掉時延伸版的打分與 `score.py` 相同（`test_control_records_equal_score_py`、錨點的單元測試版 `test_anchor_*`）。`test_torch_r.py` 在 Colab／Windows 的**訓練之前**先跑一次（它在沒有 torch 的 Mac 上寫的，從未執行過）：

```sh
python -W ignore -m unittest discover -s experiments/derisk-1015/e2 -p 'test_torch_r.py' -v
```

## main 的輸入與雜湊

| 輸入 | 從哪來 | 雜湊記在哪 |
|---|---|---|
| 訓練文字 `runs-wiki.txt`、`runs-coll.txt`、`ids-*.npy`、`vocab.txt` | S7a 的 `prep.py runs/encode`（`experiments/s7a-tinylm/README.md` 的步驟 2；M 當時的同一份，不重做） | `prep.py` 的 `charcount.json`（輸入雜湊）、`encode-report.json`；核對與 M 訓練時相同 |
| 讀音標注結果 `rd-*.npy`、`rdvocab.txt`、`rd-report.json` | 本資料夾 `label_readings.py` | `rd-report.json` 內（`rd_sha256`、`rdvocab_sha256`、詞庫、`reading_overrides.tsv`、`heterophony1.list` 的 SHA-256） |
| `heterophony1.list` | McBopomofo `Source/Data/heterophony1.list`；機器上不在預設位置就設 `SHANJIE_HETERO` | 同上 |
| tinylm-v5 匯出用的權重 `tinylm-M.npz` | S7a 的 M 檢查點（匯出 `tinylm-M8.sjtl` 用的那個） | **main 填**：`tinylm-M.npz` sha256 ＝ `________` |
| 候選：`dumps/<集合>.<chat|formal>.dump`、`.top1` | tinylm-v5 打分時用的那批（`run_sets.py dumps`），不重產 | `score_r.py` 每個集合印 dump 的 sha256；記進研究紀錄 |
| 評測列檔 | `cvtune.txt`、`wikitune.txt`（`$TUNE`）、dev302／打字測驗／錯字回報（repo）、`lytune`、`wikitypo`、`chatclause`、`discordtune`（私人，只在 main 放私人資料的機器） | 列檔雜湊照 `docs/contracts/rerank-strength.md` 與 e2-chat-eval 的紀錄 |
| 錯字回報 | 與 v5 打分時相同（若當時用 `--reported-rows 66` 就同樣給） | `run_sets.py` 核對前 66 列的雜湊 |

## 命令順序

記號同 `experiments/s7a-tinylm/README.md`：`REPO`＝這個分支的 checkout，`W`＝S7a 的工作資料夾（有 `ids-*.npy` 等），`TUNE`＝放 `cvtune.txt`、`wikitune.txt` 的資料夾，`PY`＝該機器的 python。`E2`＝`$REPO/experiments/derisk-1015/e2`。

### Colab G4：標注、訓練

```sh
cd $E2
# 0. 兩份單元測試（含 torch 的）；任何一項失敗就停
python -W ignore -m unittest discover -s . -p 'test_*.py' -v
# 1. 標注（CPU，48 執行緒；SHANJIE_HETERO 指向 heterophony1.list）。印出並寫 $W/rd-report.json：記下 noread_fraction（wiki、coll 各一）
SHANJIE_HETERO=/content/heterophony1.list python label_readings.py --work $W --procs 46
# 2. 前文長度分布：用 v5 的分數檔（聊天設定、各集合；私人集合的 key 欄只是 0／1／2 的數字）
python split_dist.py $SCORES_V5/M.chat.cvtune.jsonl $SCORES_V5/M.chat.wikitune.jsonl $SCORES_V5/M.chat.dev302.jsonl ...   # 印 P0 P1 P2
# 3. 訓練（M 的大小，一次，不調超參數）。結束碼 2＝發散，停下來回報。G4 一次不得超過 4 小時（契約 §5）
python -u train_r.py --size M --work $W --out $W --split-probs P0 P1 P2
# 4. 核對：train-Mr.json 的 steps、pieces 等於 train-M.json 的；copy 回 tinylm-Mr.npz、train-Mr.json、rd-report.json
```

### Windows 機器（188，torch）：打分

先 `set PYTHONUTF8=1`，`PY` 同 S7a README。兩臂都用同一個 `score_r.py`、同一批候選。`$S`＝分數資料夾。公開集合與私人集合（chatclause、discordtune）都在這台跑，私人的列檔放 `%USERPROFILE%\shanjie-private\`。

```bat
cd /d %REPO%\experiments\derisk-1015\e2
rem 1. 錨點（停止條件）：控制臂設定對 score.py，cvtune 聊天全部候選。第一種：現跑 score.py 當參照；第二種：--reference 給 S7a 當時的 M.chat.cvtune.jsonl
%PY% -u anchor.py --npz %W%\tinylm-M.npz --backend torch --dump %W%\dumps\cvtune.chat.dump --rows %TUNE%\cvtune.txt --name cvtune ^
    --expect-top1 <cvtune.chat.top1 的內容> --out %W%\e2\anchor
rem 結束碼 1 或沒印 "anchor: OK"＝停止，不往下做。

rem 2. 兩臂打分（兩種設定 × 全部集合）。v5 的分數檔也要有的集合照 S7a／e2-chat-eval 同一組
set SETS=chatclause cvtune wikitune dev302 typing76 user-reported lytune wikitypo discordtune
%PY% -u score_r.py sets --arm control --npz %W%\tinylm-M.npz  --backend torch --work %W% --tune %TUNE% --model-name E2C --out %S%\ctl ^
    --sets %SETS% --chatclause <F> --lytune <F> --wikitypo <F> --discord <F> [--reported-rows 66]
%PY% -u score_r.py sets --arm new     --npz %W%\tinylm-Mr.npz --backend torch --work %W% --tune %TUNE% --model-name E2N --out %S%\new ^
    --sets %SETS% --chatclause <F> --lytune <F> --wikitypo <F> --discord <F> [--reported-rows 66]
```

每個集合的輸出行記進研究紀錄：`rows scored without readings`（新臂）、dump 的 sha256、`n-gram top1 from the dump`。

### 選值與判讀（只讀分數檔，量很小）

選值就是契約的兩條 `judge.py`，每種設定一次，選完凍結；`judge_e2.py select` 一次跑完兩條（兩條的完整引數見 `judge_e2.py` 的檔頭，`test_exact_judge_invocations` 鎖住）：

```sh
python judge_e2.py select --scores $S/ctl --model E2C --out $J/ctl
python judge_e2.py select --scores $S/new --model E2N --out $J/new
# 等於（每臂各兩次）：
#   judge.py --scores DIR --sets chatclause cvtune wikitune dev302 typing76 user-reported lytune wikitypo discordtune --setting chat   --model M --out OUT --select STD --tuning chatclause --weight 1 --cap 0.005
#   judge.py --scores DIR --sets cvtune wikitune dev302 typing76 user-reported lytune wikitypo                       --setting formal --model M --out OUT --select STD --tuning cvtune wikitune --weight 10
```

- 印出的 `STD: selected on ...: alpha, tau, delta` 是兩臂各自的選值，**只記錄**（不與出貨值比、不停）。保守、積極不跑。
- `judge.py` 把每個集合的 rowstats 寫到 `OUT/rowstats/STD.<設定>.<集合>.{base,new}.txt`；`new` 是選定點的挑選。
- 判讀（BASE＝控制臂、CAND＝新臂，兩邊同一個串接順序）：

```sh
python judge_e2.py compare --base $J/ctl --cand $J/new --work $J/tables
```

印出的第一列 `MAIN chat discordtune+five`：聊天設定、判讀五集加 discordtune 串接，`evalstats compare --net` 一行；**通過＝改對 > 改壞且精確 McNemar p < 0.05**。接著三列守門：`GUARD formal five`（書面判讀五集串接）、`GUARD lytune chat`、`GUARD lytune formal`，**通過＝不是（改壞 > 改對 且 p < 0.05）**。其後的 `report …` 列（聊天五集不含 discordtune、每個集合單獨、選值集 chatclause／cvtune／wikitune）只報不判。判讀不事後改門檻（契約 §4）。

對照臂對出貨的 tinylm-v5（Rust dump 的分數）另報一行，只報不判：把 Rust CLI 的 `--tinylm-scores` 輸出放成 `$S/v5/v5.<設定>.<集合>.jsonl`，用出貨值各跑一次 `judge.py --fixed`（值取自 `docs/contracts/rerank-strength.md`：聊天 0.3 2 0.25、書面 0.7 1 2；寫進研究紀錄前再核對一次），同一個 `--out`：

```sh
python ../../e2-chateval/judge.py --scores $S/v5 --sets <同上聊天集合> --setting chat   --model v5 --out $J/v5 --fixed STD 0.3 2 0.25
python ../../e2-chateval/judge.py --scores $S/v5 --sets <同上書面集合> --setting formal --model v5 --out $J/v5 --fixed STD 0.7 1 2
python judge_e2.py compare --base $J/v5 --cand $J/ctl --work $J/tables-v5      # 只看 MAIN 那列，差異反映後端（Rust 對 torch）與選值的不同
```

用完：刪掉私人集合（chatclause、discordtune）的分數檔、dump 與 rowstats（rowstats 只含 `[0-9\t\n]`，但仍照 S7a 的慣例刪）。

## 停止條件對照（契約 §5）

| 條件 | 這裡怎麼看到 |
|---|---|
| E2 錨點不符 | `anchor.py` 結束碼 1／沒有 `anchor: OK` |
| `score_r.py` 的 dump 第一名重算的 top1 ≠ 摘要行 | 結束碼非 0，訊息 `STOP` |
| `rd-*.npy` 與 `ids-*.npy` 不對齊 | `label_readings.py`、`train_r.py` 都會停 |
| 訓練發散 | `train_r.py` 結束碼 2 |
| Colab 一次超過 4 小時、計算超過 8 小時 | main 看 `train-Mr.json` 的 `seconds`；超過就停 |
