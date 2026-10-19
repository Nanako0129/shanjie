# P2：把 0c 蒸餾進 M 規模的學生（探索片，不進 main）

計畫：維護者 2026-10-18 晚上的選擇題。P1 照登記沒過：0c 對 M 出貨值在 discordtune 淨值 +105，但開發層的改壞率上界是 0.61%。所以 P2 當成新的探索片重新登記，調參集的上界事先訂得比 0.5% 緊。研究紀錄 2026-10-18 有 L0 與 P1 兩節。打分在 GCP、訓練在 Colab，Mac 不跑重運算。

## 檔案

這個資料夾是 `exp/p2-distill` 分支上 executor 寫的程式與單元測試；資料準備、教師打分與評測由 main 在 GCP 執行，訓練在 Colab（Mac 不跑重運算）。S7a（`experiments/s7a-tinylm/`）的程式一律不改，只 import（`sys.path` 由 `train_p2.py` 補好）。下面「登記」一節是跑之前凍結的規格，這一段與「命令」「決定」是實作說明。

| 檔 | 做什麼 | 需要 torch |
|---|---|---|
| `p2ana.py` | 讀數（main 寫的，不是這一批的內容） | 否 |
| `train_p2.py` | 資料串接（列檔、dump、教師 JSONL 三方核對與過濾）、預先編成 token、每批 64 列的候選補齊成一個張量、候選分數（可微，自然對數）、KL 損失、訓練；寫 `tinylm-S.npz` 與 `train-S.json`；結束碼 2＝發散 | 串接與編碼不要；訓練要 |
| `test_p2.py` | 單元測試（合成小資料）；5 個 torch 測試沒有 torch 時跳過 | 部分 |

## 命令

記號：`M`＝`tinylm-M.npz`；`wiki.txt`、`pool.txt`＝L0 的訓練列；`*.dump`＝出貨設定的 CLI 對這些列的 `--dump`；`*.0c.jsonl`＝P1 打的 `score_0c.py` 教師分數（每份與同名列檔逐列對應）。

```bash
# 冒煙（CPU，幾秒）：python3 -m unittest discover -s experiments/p2-distill -p 'test_*.py' -v
# 訓練（Colab G4，1 個種子，登記的預算 <= 2 單元）
python experiments/p2-distill/train_p2.py --npz M.npz \
    --data wiki.txt wiki.dump wiki.0c.jsonl pool.txt pool.dump pool.0c.jsonl \
    --out OUT --seed 20261018 --epochs 3 --batch 64 --lr 1e-4 --warmup 100 --val-frac 0.02
#   → OUT/tinylm-S.npz、OUT/train-S.json；結束碼 2＝發散（不寫檔）
# P3：前文 64 字（跨標點）只加 --context 64，其餘不變；train-S.json 記 context
# 打分（GCP，和 M 同一種 CPU）：experiments/s7a-tinylm/score.py --npz OUT/tinylm-S.npz --backend torch … 其餘照登記
```

`train-S.json` 有計數（讀入列數、可用列數、各不可用原因、每個檔案的小計）、步數、訓練曲線（每 100 步的平均 KL）、留出曲線（步 0、每 500 步、最後一步的 KL 與第一名相同比例）、秒數、輸入檔的 SHA-256、種子、`train_order_sha256`。

## 登記沒寫死、我做的決定

| 項目 | 做法與理由 |
|---|---|
| 三方核對 | 列數（列檔、dump、教師）不同，或某列教師的 `i` 不是列號、`n` 不等於 dump 的候選數、`ll` 長度不等於 `n`／有非有限值，就印 `STOP` 並結束（不繼續）。教師 `n < 2` 的列不要求 `ll` |
| 不可用的列 | 依序檢查並只計數：`n<2`、`skip`（0c 讀不了）、`too_long`（有候選超過 63 個字，`ll_many` 會拒絕；登記沒提這一項，照 L0 的做法排除） |
| 序列 | `[BOS] + k + c`，`k = tinylm.row_key(prefix, 2)`（就是 `context_key`），候選字數放不下時從 k 左邊截，和 `ll_many` 同一行邏輯；字表外的字走 UNK。第 1 名也一起編，所以一列最多 8 條序列 |
| 候選分數 | 對 logits 的目標位置取 cross entropy 再負號加總（`index_add`），未用到的格填 `-1e9`（有限值，免得 softmax 與乘法碰到 inf − inf）；測試對照 `ll_many × ln 10`，容差 1e-4 |
| 損失 | `KL(q ‖ p)` 逐列算 `Σ q (log q − log p)`，教師 q 在 float64 算好再轉 float32，未用到的格 q = 0；批內各列平均（除以列數，不是候選數） |
| 留出列 | 可用列上 `default_rng(seed)` 的一個排列，前 `round(0.02 × N)` 個（至少 1 個）留出，其餘每個 epoch 用 `default_rng([seed, epoch])` 重洗；批次不滿的最後一批保留。`--max-steps` 只截批次清單，排程在那裡結束（冒煙用） |
| 留出曲線 | 多畫一個步 0（還沒訓練的 M）當起點，登記沒要求；只是畫曲線，不拿來選任何值 |
| 發散 | 照 L0：每 100 步的平均損失不是有限值，或暖機（步 > `--warmup`）之後的記錄點高於第一個記錄點，就印 `DIVERGED` 並結束碼 2，不寫 `.npz`。第一個記錄點是暖機那 100 步的平均，所以第 200 步的平均只要比它高就停；這是登記字面的讀法 |
| 學習率與衰減 | `s7a-tinylm/train.py` 的 `lr_at`（線性暖機、cosine 到 0，涵蓋 3 個 epoch 的全部步數）；權重衰減 0.1 加在 `ndim ≥ 2` 的參數（含詞嵌入與位置嵌入），偏差與 LayerNorm 不加 |
| 預取與精度 | `collate` 在背景執行緒領先 4 批；CUDA 上 bf16 autocast（候選分數與損失在 float32 算），CPU 用 fp32 |
| 沒量過的 | 吞吐量（L0 同類訓練器在 Colab G4 約 3,200 列/秒）；這份程式在 Mac 上沒有 torch，torch 的部分只在 `test_p2.py` 的 5 個測試裡，要由 main 在有 torch 的機器上先跑過再開訓練 |

## 登記（2026-10-18，main 寫，跑之前 commit）

探索片：只用調參層與開發層，不出貨，結論只寫方向；私人集合只記數字。不碰判讀集、保留集、chatjudge-2。

- **假設**：從出貨 M 起始，用 0c 在 L0 訓練列上的候選分佈當軟標籤微調，學生就能學到 0c 在聊天上的排序。分數照舊是 M 的似然（候選每個字的 log 機率加總），所以融合方式、SJTL 格式、Rust 推論都不用改。在更緊的調參上界下，學生仍然要贏過 M 出貨值。
- **學生**：
  - 出貨 M（`tinylm-M.npz` `32308122…`），架構不變，沒有新的頭。
  - 每個候選的分數 s = Σ ln p(c_i | [BOS] k c_<i)，k = `tinylm.context_key(prefix)`（≤ 2 個漢字，和出貨 M 的 `ll` 欄、App 現在給的前文相同）。前文回傳不開片（P1 的結論），所以學生只用 App 拿得到的前文。
- **教師與損失**：
  - 教師是 0c 的 `ll` 欄（log10，前文取最後 64 字，`experiments/x0c/score_0c.py`），q = softmax(ll_0c · ln 10)，溫度 1。
  - 學生 p = softmax(s)，只在同一列的候選之間做。
  - 損失 KL(q ‖ p)，各列平均。n-gram 分數不進模型。
  - 只用候選至少 2 個、而且 0c 讀得了（`skip = 0`）的列。
- **資料**：L0 的訓練列（維基第 200,000–299,999 篇 112,850 列＋Phase 2 句子池 175,064 列，前 8 名 dump，`l0train8.tar.zst` `2c58e673…`），加上 P1 打的教師分數（`p1teacher.tar.zst` `0a761ec3…`）。都不在 n-gram 計數裡，也不在 M 的訓練文字裡。
- **訓練**：Colab G4。
  - 3 個 epoch，每批 64 列，AdamW（β 0.9／0.95，權重衰減 0.1 只加在矩陣上），峰值學習率 1e-4，暖機 100 步後 cosine 降到 0，梯度裁切 1.0，CUDA 上 bf16 autocast，種子 20261018。
  - 留 2% 的列只用來畫曲線：留出列的 KL，以及學生和教師第一名相同的比例。曲線不拿來選任何值。
  - 損失出現 nan，或暖機後高於起始值就停下。不做超參數搜尋。
  - 輸出照 `torch_model.export_npz` 存成 S7a 的 `.npz`，打分直接用 `experiments/s7a-tinylm/score.py`。
- **打分**：同一台 GCP C4D 上做以下幾件事：
  - 用出貨設定的 CLI（`--profile chat --context --packs acg`）dump 四個聊天集合。
  - M 與學生都用 `score.py`（torch CPU）打分。
  - 0c 也用 `score_0c.py` 重新打分，放在沒有網路的 namespace 裡，只當參考。
- **集合**：調參層 chatclause 調參期；開發層 discordtune、chatjudge（已用過）、lytune 聊天（只當警示）。
- **選值**：chatclause 調參期上用 `fusegrid.select_weighted(cap_ub=0.003)` 選 α／τ／δ，權重 1，學生用 `ll` 欄。上界 0.3% 是這次事先訂的餘裕：P1 時調參集 0.46% 到開發層變 0.61%。
- **主讀法（寫死）**：學生用選到的值，在 discordtune 上同時符合兩條才算過：
  - 對 M 出貨值（α 0.3、τ 2、δ 0.25）的淨值 ≥ +40；
  - 學生對 n-gram 第一名的改壞數 95% 上界 ÷ n ≤ 0.5%。
- **只報、不判**：
  - 開發層各集合：學生對 n-gram、學生對 M 出貨值（改對／改壞、淨值、精確 McNemar p）、上界、error ratio。
  - 單獨挑對率與留住率。
  - 學生在調參集上的前沿表（上界 0.1%–0.5%）。
  - 0c 在同一個 0.3% 規則下的數字，作為天花板參考。
  - 用 chatjudge 上「學生對 M 出貨值」的不一致列數，換算 chatjudge-2 的 MDE。
- **之後怎麼走**：
  - 過了：把結果交給維護者，決定要不要開確認片（3 個種子、chatjudge-2 一發、出貨值當基準）。
  - 沒過：結案、記方向，不開子片補救。
- **平台與預算**：
  - Colab G4 1 個種子 ≤ 2 單元。
  - GCP c4d-highcpu-32 Spot，us-central1，`--max-run-duration` 3 小時加 DELETE，約 1 小時，做完當場刪掉。
  - 私人集合由 Mac 放上去並核對雜湊，只回數字，VM 刪除時一起清掉。
