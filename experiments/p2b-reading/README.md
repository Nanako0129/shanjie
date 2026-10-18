# 蒸餾 B：帶讀音輸入的學生（探索片，不進 main）

計畫：維護者 2026-10-19 凌晨的選擇題。P2 沒過（學生對 M 出貨值 −2）；0c 不出貨（維護者：代價太大、無法宣稱是自己的東西），只當教師。這一片在 P2 上只改一個變項：讓學生看得到讀音。之後從 P2 學生和這一片的學生裡挑一個最好的，另外先登記規則，再用 chatjudge-2 確認。研究紀錄 2026-10-18 有 L0、P1、P2 三節。

## 檔案

這個資料夾是 `exp/p2b-reading` 分支上 executor 寫的程式與單元測試；資料準備、教師打分與評測由 main 在 GCP 執行，訓練在 Colab（Mac 不跑重運算）。S7a 與 P2 的程式一律不改，只 import（`train_b.py` 補好 `sys.path`）；P2 的資料串接、補齊、候選分數、KL、排程、發散規則都是直接呼叫 `train_p2.py`，B 只加讀音這一段。下面「登記」一節是跑之前凍結的規格，這一段與「命令」「決定」是實作說明。

| 檔 | 做什麼 | 需要 torch |
|---|---|---|
| `p2bana.py` | 讀數（main 寫的，不是這一批的內容） | 否 |
| `train_b.py` | 音節表、`join_rows_b`（P2 的過濾加 `len_mismatch`）、`PackedB`／`collate_b`（P2 的批次加 `rd` 讀音張量）、`ReadingGPT`（S7a GPT 加 R）、訓練；寫 `tinylm-B.npz` 與 `train-B.json`；結束碼 2＝發散 | 串接、音節表與 `rd` 不要；模型與訓練要 |
| `score_b.py` | 用 B 打分，輸出和 `s7a-tinylm/score.py` 同格式的 JSONL（`fusegrid.load_scores` 讀得進去） | 要（CPU 即可） |
| `test_b.py` | 單元測試（合成小資料）；8 個 torch 測試沒有 torch 時跳過 | 部分 |

## 命令

記號同 P2：`M`＝`tinylm-M.npz`；`wiki.txt`、`pool.txt`＝L0 的訓練列；`wiki.dump8`、`pool.dump8`＝出貨設定的 CLI 對這些列的 `--dump`；`X0C.*.jsonl`＝P1 打的教師分數。

```bash
# 冒煙（CPU，幾秒；Mac 沒有 torch，torch 的 8 個測試要 main 在 Colab 先跑）：
python3 -m unittest discover -s experiments/p2b-reading -p 'test_*.py' -v
# 訓練（Colab G4，1 個種子，登記的預算 <= 2 單元）
python experiments/p2b-reading/train_b.py --npz M.npz \
    --data wiki.txt wiki.dump8 X0C.wiki.jsonl pool.txt pool.dump8 X0C.pool.jsonl \
    --out OUT --seed 20261018 --epochs 3 --batch 64 --lr 1e-4 --warmup 100 --val-frac 0.02
#   → OUT/tinylm-B.npz、OUT/train-B.json；結束碼 2＝發散（不寫檔）
# 打分（GCP c4d，和 M 同一種 CPU；每個集合一次，N＝該集合 CLI 摘要行的 top1，和 score.py 同一個核對）
python experiments/p2b-reading/score_b.py --npz OUT/tinylm-B.npz --backend torch \
    --dump SET.chat.dump --rows SET.txt --name SET --expect-top1 N --out scores/B.chat.SET.jsonl
```

`train-B.json` 同 `train-S.json`（計數、步數、訓練曲線、留出曲線、秒數、輸入檔 SHA-256、種子、`train_order_sha256`），`counts.unusable` 多一項 `len_mismatch`，另有 `n_syl`。`tinylm-B.npz` 是 S7a 的所有陣列加 `rd_emb`（n_syl × d，第 0 列恆為 0）與 `rd_syl`（n_syl 個名字，第 0、1 列是 `<none>`、`<unk>`）。

## 登記沒寫死、我做的決定

| 項目 | 做法與理由 |
|---|---|
| 重用 P2 | `join_rows`（三方核對與 P2 的三個原因）、`Packed`、`collate`、`cand_scores`（用一個把 `rd` 帶進去的 lambda 當模型）、`kl_rows`、`plan`、`diverged`、`to_tensors` 都是 import；只有 `run`（資料與模型不同）、`evaluate`、`prefetch`（要用 `collate_b`）是照 P2 複製後改，改的地方只有讀音 |
| 不可用的原因順序 | P2 的 `n<2`、`skip`、`too_long`，最後才是 `len_mismatch`；任一候選的字數不等於該列的音節數就算。打分時的原因順序是 `len_mismatch` 先於 `too_long`（打分不看 `skip`，教師不在這一步） |
| 音節表 | 只用可用列（過完全部原因之後）的讀音欄建，含留出列；排序後從第 2 列開始。不可用列才有的音節不進表，打分時走「未知」。「未知」那一列訓練時從不出現，所以永遠是 0，等於「無」；登記只要求它存在 |
| R 的第 0 列 | `nn.Embedding(padding_idx=0)`：梯度恆為 0，起始為 0，AdamW 的衰減與更新對 0 仍是 0，所以保持精確 0（測試跑 3 步後檢查）。其他列照 P2 的規則歸在 `ndim ≥ 2`，權重衰減 0.1 |
| 讀音在哪個位置 | `collate_b` 在被打分的位置（預測第 j 個候選字的那一欄）放第 j 個音節的 id，其餘為 0；前文被左截斷時 `st` 跟著變，位置仍是 `st-1+j`。空前文時 c_1 的音節放在 BOS 那一欄 |
| 打分程式重用訓練的函式 | `score_b.py` 用同一個 `PackedB`／`collate_b`／`cand_scores_b` 算 ln 分數再除以 ln 10，所以訓練與打分是同一個函式；`ll0` 是空前文（prefix 給空字串），`ll16` **直接複製 `ll`**（E-B 也是這樣；B 沒有 16 字前文的欄位意義，融合只讀 `ll`） |
| 不能打分的列 | `n >= 2` 但字數與音節數不合（`len_mismatch`）或有候選超過 63 字（`too_long`）：`ll`、`ll0`、`ll16` 全是 0.0，`noll: 1`，`fusegrid.pick` 保留 n-gram 第一名；`score_b` 印各原因的列數與表外音節數。沒有另寫 `skip` 欄，`p2bana.alone` 的「單獨挑對率」會把這些列算進去（全 0 時挑第 1 名），要排除的話請看 `noll` |
| 打分批次與精度 | CPU、float32、關 TF32；`--bs` 是每批的列數（預設 16，每列最多 8 條序列），不是 `score.py` 的序列數 |
| `rd_syl` 是字串陣列 | `tinylm.NumpyLM` 把所有陣列轉成數字，讀這個檔會報錯。這是刻意的：numpy 後端若悄悄忽略 R，打出來的就是 M 的分數 |
| 沒量過的 | 吞吐量；R 的加入多一次 embedding 查表，預期可忽略。**這份程式在 Mac 上沒有 torch，ReadingGPT、儲存／載入、打分程式與訓練只在 `test_b.py` 的 8 個 torch 測試裡，要由 main 在有 torch 的機器上先跑過再開訓練** |

## 登記（2026-10-19，main 寫，跑之前 commit）

探索片：只用調參層與開發層，不出貨，結論只寫方向；私人集合只記數字。不碰判讀集、保留集、chatjudge-2。

- **假設**：P2 的學生只看候選字和前 2 個字，0c 另外看整串讀音。讓學生在預測每個候選字之前先知道那個字的讀音，它就能專心分辨同音字，從 0c 學到更多。
- **和 P2 唯一的差別：讀音輸入**。其他全部照 P2 的登記：出貨 M 起始、KL 對 0c、前文 `context_key`、資料、3 個 epoch、學習率與排程、種子 20261018、調參上界 0.3%、過關條件。
  - 學生多一張讀音嵌入表 R（每個帶聲調的音節一列，再加「無」與「未知」兩列），初始全為 0，所以訓練開始時和 M 完全相同。
  - 預測候選的第 j 個字的那個位置，輸入向量加上 R(第 j 個音節)：c_1 是前文最後一個字（或 BOS）那個位置，c_j（j ≥ 2）是 c_{j−1} 那個位置。其他位置加「無」那一列，固定為 0。
  - 分數照 P2：s = Σ ln p(c_j | …)，完整字表的 softmax。
  - 音節表從訓練列的讀音欄建（帶聲調，例如 `ㄓㄥˋ`）；評測列遇到表外的音節用「未知」。
  - 候選字數和音節數不同的列：訓練時不用，記數。打分時這一列所有候選的分數都是 0，融合會保留 n-gram 第一名，記數。
- **打分**：同一台 GCP C4D。出貨設定的 CLI dump 四個聊天集合；M 用 `score.py`；B 學生用這一片的打分程式（輸出和 `score.py` 同格式）；P2 學生（`tinylm-S.npz` `06a25d07…`）用 `score.py`；0c 用 `score_0c.py`，放在沒有網路的 namespace 裡。
- **選值與主讀法**：照 P2，在 chatclause 調參期上用 `select_weighted(cap_ub=0.003)` 選 α／τ／δ。B 學生在 discordtune 上同時符合以下兩條才算過：
  - 對 M 出貨值（α 0.3、τ 2、δ 0.25）的淨值 ≥ +40；
  - 對 n-gram 第一名的改壞數 95% 上界 ÷ n ≤ 0.5%。
- **只報、不判**：
  - P2 學生、0c、M 同規則在同一台機器上的同一張表。
  - B 學生的前沿表與單獨挑對率、留住率。
  - 讀不到讀音的列數。
  - chatjudge-2 的 MDE（B 學生對 M 出貨值的不一致列數換算）。
- **之後怎麼走**：不論這一片過不過，都把 B 與 P2 學生的數字交給維護者。照同日的決定，從兩者挑一個，另外登記確認片的規則，再用 chatjudge-2 一發。B 要出貨的話，Rust 推論與 SJTL 格式要多支援讀音嵌入表，是之後的工程片。
- **平台與預算**：Colab G4 一個種子 ≤ 2 單元；GCP c4d-highcpu-32 Spot，us-central1，`--max-run-duration` 3 小時加 DELETE，約 1 小時，做完當場刪掉。私人集合由 Mac 放上去並核對雜湊，只回數字，VM 刪除時一起清掉。
