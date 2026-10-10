# P1a 同音功能字分類器（離線）

契約：`docs/contracts/p1a-confusion.md`（規格以它為準）。這個資料夾是實作：資料準備、訓練、候選、打分、融合與報告。單元測試 `test_p1a.py`（`make test` 會跑；單獨跑：`/opt/homebrew/bin/python3 -W ignore -m unittest experiments/p1a-confusion/test_p1a.py`，要有 numpy）。

## 檔案

| 檔 | 做什麼 | 機器 |
|---|---|---|
| `p1a.py` | 純函式：資料段落（匯入 `build_counts`／`build_counts_text` 的函式，不複製）、字位讀音、遮掉字位的特徵、numpy 訓練、`f(c)`、融合、選格點、分階段 | 都可 |
| `prep.py` | `hashes`（輸入雜湊）、`construction`（建構檢查）、`runs`（漢字段檔）、`classes`（類別表 `classes.json`） | 188 |
| `train.py` | `extract`（抽樣本與特徵）、`fit`（訓練兩次、核對 SHA-256、量大小） | 188 |
| `candidates.py` | 前 8 名候選（Python `decode`，保留詞切分）；`--equalize` 是詞庫分數拉平對照組 | 188；discordtune 在 Mac |
| `score.py` | 在候選檔加上每個讀音的 `f(c)`（有前文、沒前文各一份）與金標準字位的分類器預測 | 188；discordtune 在 Mac |
| `fuse.py` | `fable`、`accuracy`、`stage N`、`report`（表格、各項報告、§4 判斷） | Mac |

## 約定

- 188 是 Windows，在 cmd 裡；`REPO` 是 repo 在 188 上的絕對路徑，`TRAIN`、`SYNTH` 是 model-v5 用的 `colloquial-train.txt`、`synth.txt`，`CVTUNE`、`WIKITUNE` 是釘住的調參集，`LM` 是 model-v5（`classes.sjc` 要在同一個資料夾）。這幾個由 main 填。
  ```
  set PYTHONUTF8=1
  set PY=%USERPROFILE%\ime-research\proto\.venv\Scripts\python.exe
  set P1A=%REPO%\experiments\p1a-confusion
  set W=%USERPROFILE%\.cache\shanjie\work\p1a
  mkdir %W%\cands %W%\scored %W%\eq %W%\weights
  ```
  venv 要有 numpy（`%PY% -c "import numpy"`）。程式都離線。
- Mac 用 `/opt/homebrew/bin/python3`（有 numpy），`W=~/.cache/shanjie/work/p1a`（不進 repo）。
- 工作目錄 `W` 的結構：`runs-*.txt.gz`、`classes.json`、`samples-<讀音>.npz`、`weights/<讀音>.sjw`、`cands/<集合>.<設定>.jsonl`、`scored/…`、`eq/…`。集合名固定：`cvtune wikitune dev302 typing reported discordtune`（設定：`chat`、`formal`）。
- `dev302` = `--dev 302`（`eval/dev/*.txt` 依檔名排序的前 302 列）、`typing` = `eval/dev/user-typing.txt`、`reported` = `eval/dev/user-reported.txt`。

## 第 0 步（188，只做一次；任何一步失敗就停）

```
%PY% %P1A%\prep.py hashes --train %TRAIN% --synth %SYNTH% --model %LM% --cvtune %CVTUNE% --wikitune %WIKITUNE%
%PY% %P1A%\prep.py construction --train %TRAIN%
%PY% %P1A%\prep.py runs --train %TRAIN% --synth %SYNTH% --out %W%
%PY% %P1A%\prep.py classes --runs %W% --out %W%\classes.json
```

- `hashes`：11 個輸入對契約 §2.2 的前綴，不符結束碼 1。`construction`：原始 `cv-*`、`cmn_sentences.tsv.bz2` 的雜湊與逐行 `is_tune(raw)` 計數對 `colloquial-train.txt`，另印維基的條目序號界線（計數只用前 200,000 篇，wikitune 從第 300,000 篇起，見 `build_tune.py`）；輸出記進報告。
- `runs`：維基用 `build_counts.articles(200000)`（`imap` 保持順序）、口語用 `build_counts_text.lines`，轉換與切分全用 `build_counts` 的函式；結果是兩個 `runs-*.txt.gz`（一行一段）。這一步最久（約等於一次計數批次）。
- `classes`：每個讀音的字位字數（未加權），印類別表與每個字的樣本數；`classes.json` 就是之後不改的類別表。沒有至少 2 個類別的讀音標 SKIPPED。

候選與 CLI 核對（每個集合 × 兩種設定；`.sum` 是摘要行）：

```
for %P in (chat formal) do (
  %PY% %P1A%\candidates.py --lm %LM% --profile %P --rows %CVTUNE%   --name cvtune   --out %W%\cands\cvtune.%P.jsonl   > %W%\cands\cvtune.%P.sum
  %PY% %P1A%\candidates.py --lm %LM% --profile %P --rows %WIKITUNE% --name wikitune --out %W%\cands\wikitune.%P.jsonl > %W%\cands\wikitune.%P.sum
  %PY% %P1A%\candidates.py --lm %LM% --profile %P --dev 302         --name dev302   --out %W%\cands\dev302.%P.jsonl   > %W%\cands\dev302.%P.sum
  %PY% %P1A%\candidates.py --lm %LM% --profile %P --rows %REPO%\eval\dev\user-typing.txt   --name typing   --out %W%\cands\typing.%P.jsonl   > %W%\cands\typing.%P.sum
  %PY% %P1A%\candidates.py --lm %LM% --profile %P --rows %REPO%\eval\dev\user-reported.txt --name reported --out %W%\cands\reported.%P.jsonl > %W%\cands\reported.%P.sum
)
```

（在批次檔裡 `%P` 要寫 `%%P`。）CLI 核對在 Mac：同一個模型，`cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile chat --context --rows <檔>`（dev302 用 `--dev 302`），摘要行的 `top1_sha256` 要和 `.sum` 的相同（它是逐列第一名串接的雜湊）。不同就停。

Fable 的數字（釘住的 cvtune，Mac 或 188 都行，只要有 `cands/`）：`python3 fuse.py fable --work %W%`，印 §0 的三個數字（只錯功能字的列數、正解在前 8 名的列數、錯在多字詞裡的列數）；cvtune 與錯字回報各一行。

## 第 1 階段（只訓 ㄗㄞˋ）

188：

```
%PY% %P1A%\train.py extract --runs %W% --classes %W%\classes.json --model %LM% --out %W% --readings ㄗㄞˋ
%PY% %P1A%\train.py fit --samples %W% --classes %W%\classes.json --out %W%\weights --readings ㄗㄞˋ
for %S in (cvtune wikitune dev302 typing reported) do for %P in (chat formal) do ^
  %PY% %P1A%\score.py --lm %LM% --weights %W%\weights --in %W%\cands\%S.%P.jsonl --out %W%\scored\%S.%P.jsonl
```

`fit` 同一份樣本訓練兩次，兩個權重檔的 SHA-256 不同就結束碼 1（停止條件）；它也印每個讀音的非零特徵數與 int8 稀疏檔大小（§3 第 4 項）。詞庫分數拉平對照組（階段 1 只改 ㄗㄞˋ，每個集合 × 設定，指令同候選、多一個 `--equalize ㄗㄞˋ` 並輸出到 `eq\`）：

```
%PY% %P1A%\candidates.py --lm %LM% --profile %P --rows %CVTUNE% --name cvtune --out %W%\eq\cvtune.%P.jsonl --equalize ㄗㄞˋ
```

其餘四個集合照 `cands` 那五行改 `--out` 與 `--equalize`。把 `weights\`、`scored\`、`eq\`、`cands\`、`classes.json` 拷到 Mac 的 `W`。

Mac：

```
python3 experiments/p1a-confusion/fuse.py accuracy --work $W
python3 experiments/p1a-confusion/fuse.py stage 1 --work $W --out $W/stage1.json
```

`accuracy` 印 §3 第 2 項的表（分類器、最常見字、n-gram 第一名）與停用清單。`stage 1` 印啟用的讀音、兩種設定凍結的 (μ, τ)、cvtune 聊天的「再＞在」「在＞再」前後與停止規則的 PASS／STOP，以及合計 top1 對 n-gram 的增減。**STOP 就停在這裡寫結論**（契約 §2.4）。

## 第 2、3 階段

PASS 才繼續。階段 2：`--readings ㄗㄨㄛˋ` 重跑 `extract`、`fit`（權重資料夾累積）、對所有集合重跑 `score.py`（分類器會一起打分，`fuse.py` 依階段決定加總哪些讀音），`--equalize ㄗㄞˋ,ㄗㄨㄛˋ` 重做對照組，`fuse.py stage 2 …`。階段 3：`--readings ㄕˋ,ㄚ,ㄒㄧㄤˋ,ㄉㄠˋ`，`stage 3`，之後才做下面的總報告。每個階段的 (μ, τ) 都是重新選的。

## discordtune（私有，Mac，只回數字）

候選、對照組與打分的指令和上面相同，`--rows` 換成 discordtune 檔，`--lm data/lm/bigram.sjlm`，輸出到 Mac 的 `W` 的 `cands/`、`eq/`、`scored/`（集合名 `discordtune`）；同一個模型雜湊、同樣核對 `top1_sha256` 對 CLI。用完把這三個資料夾裡 discordtune 的檔刪掉。

## 總報告（Mac，最後一個階段之後）

```
python3 experiments/p1a-confusion/fuse.py report --work $W --stage $W/stageN.json --sets cvtune,wikitune,dev302,typing,reported,discordtune
```

印：每個集合 × 兩種設定的 `evalstats` 表格（分類器、詞庫分數拉平、μ = 0 三組，基準是 n-gram 第一名）、凍結的 (μ, τ)、門檻觸發比例、前 8 名 oracle、各讀音字位正確率（基準→新）、錯字只在功能字的列數及其中錯在多字詞裡與修好的列數、第一名換成或換掉不在類別表的字的列數、加不加前文 `f(c)` 有變的列數（錯字回報與 discordtune 都必須 ≥ 1）、非零特徵數與大小，最後是 §4 的逐項 PASS／FAIL 與一行結論。報告裡沒有任何句子。

## 實作時決定的細節（契約沒寫死的地方）

- **特徵**（`p1a.FEATURES`，14 個）：句首旗標、句尾旗標、左段最後 1、2 個字與兩字組合、右段前 1、2 個字與兩字組合、左段最後一字×右段第一字、左段最後一個詞、右段第一個詞、兩者的詞類與詞類組合。字串雜湊用 `zlib.crc32`（不用 Python 的 `hash`，每次執行才穩定）。切不開的左段或右段退成單字（切成一段完整漢字時用到的詞被切掉一半的情況）。
- **特徵的 < 3 次**：數雜湊後 ID 在入選樣本裡出現幾次（各特徵欄位分開，同一個 ID 在同一個樣本只可能來自碰撞），丟掉的特徵指向一列零權重。
- **權重檔 `.sjw`**：自訂二進位（魔數、meta JSON 排序鍵、int32 特徵清單、float32 權重），不含任何時間戳，所以相同訓練給相同 SHA-256。訓練是 float64 的 numpy（`np.add.at`、無 BLAS 歸約），存檔轉 float32，打分用存檔的數字。權重零初始、`RandomState(20261010)` 逐 epoch 洗牌，L2 同時加在權重與偏差上（等同 `torch.optim.Adam(weight_decay=1e-6)`）。
- **樣本上限**：每個（讀音，類別，來源）至多 150,000 個，依串流順序（維基先、口語後）；每個來源的類別都滿了就不再往下讀。口語權重 5 只進損失函式。
- **分類器正確率**（§3 第 2 項）：用「分類器選的字」＝在 `P_char` 下最可能的字（類別字，或「其他」類裡最常見的字，若 `P(其他)·q` 勝過所有類別字）；對的條件是這個字等於金標準字（寬鬆對照）。字位用該列使用者打的音節（`syls[i] == 讀音`），分類器吃該列的前文。三個數字（分類器、最常見字、n-gram 第一名）都是同樣的字位與同樣的對照。
- **n-gram 的字位正確率與 f(c)**：n-gram 第一名與候選都是逐字對應音節（詞庫的詞長等於讀音長）。
- **「其他」類 q 的 n(c)** 來自入選的（受上限）樣本；`V` 是詞庫裡該讀音的單字詞中不在類別表的字數。
- **停止規則的計數**（`fuse.py stage`）：「再＞在」是金標準字為再、第一名同一位置是在的字位數（長度相同的列），和 §0 盤點同一個算法。
- **觀察（沒有改設計）**：每個類別各取到 300,000 個樣本，所以分類器訓練時各類別大致等量，機率沒有帶到真實的先驗（真實語料裡「在」遠多於「再」）。`f(c)` 是同一組特徵下各字的相對值，先驗不同會讓格點的 μ 去補；若階段 1 沒過，先看這點。
