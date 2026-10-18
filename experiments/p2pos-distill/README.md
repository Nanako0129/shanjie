# 位置級蒸餾（探索片，不進 main）

計畫：維護者 2026-10-19 的選擇題。學生路線（P2、B、C、L）都沒過，研究顧問的分析在 repo 外（`fable-2026-10-19-after-l.md`）。這是學生路線的最後一片，附暫停條款。研究紀錄 2026-10-18 有 P2 一節，2026-10-19 有蒸餾 B、C、L 三節。

## 登記（2026-10-19，跑之前 commit）

探索片：只用調參層與開發層，不出貨，結論只寫方向；私人集合只記數字。不碰判讀集、保留集、chatjudge-2。

- **假設**：8 個候選上的 KL 每列只給學生很少的監督（80% 的列教師尖在第一名，等於只教「維持第一」）。四個學生的留出 KL 都停在 0.36–0.38、和教師一致率都停在約 80%，像是監督密度的天花板。改用教師在**每個位置、同音字集合上**的分佈當標籤，同一批列的監督多很多，學生在可改列上和教師的一致率應該明顯上升。
- **臂 T（主臂）**：
  - 學生：出貨 M（`tinylm-M.npz` `32308122…`）起始，架構不變；分數照舊是候選每個字 log 機率加總，前文 `context_key`。融合方式與 Rust 推論都不用改。
  - 教師標籤：0c（`score_0c.py` 釘住的三個檔）對每列的**正解句**算一次，前文截成學生看得到的 `context_key`（≤ 2 個漢字）。第 j 個位置取 log-softmax，限制在 H(r_j)：該列第 j 個讀音在 `data/lexicon/mcbpmf-data.txt` 的單字集合，和 0c 字表的交集，重新正規化後整個存下。讀音照列檔給的（列檔的讀音就是正解的讀音），不做輕聲另試。
  - 損失：Σ_j KL(q_j ‖ p_j)，p_j 是學生在同一個 H(r_j) 上重新正規化的 softmax，各列平均；不放 n-gram、不放讀音輸入。同音字之間的比值就是候選排序用到的量，所以這個損失直接塑造推論時的分數〔推論〕。
- **臂 R（對照，只報）**：P2 的 8 候選 KL，但教師第一名和 n-gram 第一名不同的列，損失加權 ×4（選定值，不搜尋）。用來分開兩種解釋：R ≈ P2 而 T > P2 → 監督密度；R ≈ T > P2 → 只要強調困難列就夠。
- **資料、訓練**：全照 P2。資料是 L0 的 287,914 列（P2 可用 287,000 列）；Colab G4，3 個 epoch、batch 64、學習率 1e-4、warmup 100、留出 2%、種子 20261018，發散規則照 P2。
- **打分**：同一型 GCP C4D。M、P2 學生、0c 的分數與四個聊天集合的 dump，沿用蒸餾 C 那次的封存檔（`9bd3eca1…`）；T、R 用 `score.py` 打同一份 dump。判讀用 `p2bana.py`（學生前綴 T、R）；一致率用 `p2cdiag.py`（學生前綴可指定）。
- **門檻（事先寫死）**：
  - (a) chatclause 可改列上，學生單獨挑的候選和 0c 相同的比例 ≥ 72%（P2 是 63.6%）。
  - (b) discordtune 對 M 出貨值（α 0.3、τ 2、δ 0.25）淨值 ≥ +40，且對 n-gram 第一名的改壞數 95% 上界 ÷ n ≤ 0.5%（α／τ／δ 在 chatclause 用 `select_weighted(cap_ub=0.003)` 選）。
  - (b) 過 → 補到 3 個種子，用它自己的不一致列數算 chatjudge-2 的 MDE，估值 ≥ MDE 才登記確認片。
  - (a) 過、(b) 不過但淨值 ≥ +20 → 學生受列數限制，下一片是資料（HPLT 3.0）。
  - (a) 不過 → 學生路線結案，研究線暫停，等 App 計數器的數字。
  - 只判 T；R 只報。
- **只報、不判**：
  - 留出 KL（改成位置級，和 P2 不能直接比；另印 8 候選 KL 的留出值）、留住率、前沿表、error ratio。
  - 書面設定在 wikitune-clean（`~/.cache/shanjie/clean/wikitune-clean.txt`，2,181 列，公開）上，T 對 M 的非劣性：同一份書面 dump，M 與 T 都用 M 的書面出貨值。
  - 教師分佈落在 H(r_j) 外的質量（中位數與 p90）。
- **停止條件**：教師分佈落在 H(r_j) 外的質量中位數 > 20%（集合建錯，先修再跑）；訓練發散；GCP 超過 4.5 小時。
- **平台與預算**：GCP ≤ 3 小時（教師標籤與打分）、Colab ≤ 2 單元（T、R 各一）、main ≤ 1 天、executor ≤ 0.5 天；超過 1.5 倍停下問。教師的位置級標籤是 0c（MIT）的衍生物，和 `p1teacher.tar.zst` 一樣放 repo 外、記雜湊。私人集合的分數檔帶回維護者另一台機器的私人目錄。

## 命令

記號同 P2／B：`M`＝`tinylm-M.npz`；`wiki.txt`、`pool.txt`＝L0 的訓練列；`wiki.dump8`、`pool.dump8`＝出貨設定的 CLI 對這些列的 `--dump`；`X0C.*.jsonl`＝P1 打的 P2 教師分數；`*.pos.jsonl.gz`＝下面第 1 步的位置級教師。

| 檔案 | 做什麼 | 在哪跑 |
|---|---|---|
| `teacher_pos.py` | 0c 對每列正解句算每個位置、H(r_j) 上的分佈 | GCP（onnxruntime、onnx、numpy） |
| `train_pos.py` | 臂 T 訓練；`fit()` 是和臂 R 共用的訓練迴圈 | Colab G4 |
| `train_r.py` | 臂 R 訓練（P2 加權 ×4） | Colab G4 |
| `p2posana.py` | T、R 的判讀（p2bana、p2cdiag）與書面非劣性 | 任何機器（只讀分數檔） |
| `test_pos.py` | 單元測試；torch 的 4 個在沒有 torch 時略過 | Mac（其餘）、Colab（torch 的） |

```bash
# 冒煙：Mac 跑非 torch 的部分；Colab 在訓練前再跑一次（torch 的 4 個測試）
python3 -m unittest discover -s experiments/p2pos-distill -p 'test_*.py' -v

# 1. 位置級教師（GCP C4D，沒有網路的 namespace；model-dir 的三個檔核對釘住的雜湊，改過的圖只在記憶體裡）
#    先量時間與 outside-H 質量：前 1,000 列
python experiments/p2pos-distill/teacher_pos.py --model-dir MODELDIR --rows wiki.txt --out /tmp/probe.pos.jsonl --limit 1000
#    全量（每個列檔一次；--workers 每個程序一個執行緒、連續切片，輸出順序同列檔）
python experiments/p2pos-distill/teacher_pos.py --model-dir MODELDIR --rows wiki.txt --out wiki.pos.jsonl.gz --workers 8
python experiments/p2pos-distill/teacher_pos.py --model-dir MODELDIR --rows pool.txt --out pool.pos.jsonl.gz --workers 8
#   → 每列一行（跳過的列也有一行，帶 why）；印出各跳過原因的列數與 outside-H 質量的中位數、p90。
#     結束碼 3＝中位數 > 20%（登記的停止條件）。

# 2. 臂 T（Colab G4，1 個種子，登記的預算 <= 1 單元）
python experiments/p2pos-distill/train_pos.py --npz M.npz \
    --data wiki.txt wiki.dump8 X0C.wiki.jsonl wiki.pos.jsonl.gz pool.txt pool.dump8 X0C.pool.jsonl pool.pos.jsonl.gz \
    --out OUT_T --seed 20261018 --epochs 3 --batch 64 --lr 1e-4 --warmup 100 --val-frac 0.02
#   → OUT_T/tinylm-T.npz、OUT_T/train-T.json；結束碼 2＝發散

# 3. 臂 R（Colab G4，1 個種子，登記的預算 <= 1 單元）
python experiments/p2pos-distill/train_r.py --npz M.npz \
    --data wiki.txt wiki.dump8 X0C.wiki.jsonl pool.txt pool.dump8 X0C.pool.jsonl \
    --out OUT_R --seed 20261018 --epochs 3 --batch 64 --lr 1e-4 --warmup 100 --val-frac 0.02 --boost 4
#   → OUT_R/tinylm-R.npz、OUT_R/train-R.json；結束碼 2＝發散

# 4. 打分（GCP C4D，和 M 同一種 CPU；每個集合一次，N＝該集合 CLI 摘要行的 top1）；T、R 各一輪，集合名照蒸餾 C 的封存檔
python experiments/s7a-tinylm/score.py --npz OUT_T/tinylm-T.npz --backend torch \
    --dump SET.chat.dump --rows SET.txt --name SET --expect-top1 N --out scores/T.SET.jsonl
#    書面 dump（wikitune-clean，--profile formal）：M 與 T 各一份 → scores/M.wikitune-clean.formal.jsonl、scores/T.wikitune-clean.formal.jsonl

# 5. 判讀（scores/ 放 M.、S.、X0C.、T.、R. 的 SET.jsonl；dumps/ 放 SET.dump）
python experiments/p2pos-distill/p2posana.py --scores scores --dumps dumps --train wiki.dump8 pool.dump8 --students T R \
    --formal-m scores/M.wikitune-clean.formal.jsonl --formal-t scores/T.wikitune-clean.formal.jsonl
```

`train-T.json` 同 `train-S.json`（計數、步數、訓練曲線、留出曲線、秒數、輸入檔 SHA-256、種子、`train_order_sha256`），差別：`counts` 多 `pos_usable` 與 `pos_unusable`（`pos_skip`、`gold_too_long`、`empty_h_student`）；`val_rows_p2` 是 P2 的留出列數、`val_rows` 是其中有位置級教師的列數；`val_curve` 的每一項是 `[步, {pos_kl, pos_top1, cand8_kl, cand8_top1}]`（步 0、每 500 步、最後一步）。`pos_kl`＝留出列的 Σ_j KL 平均（位置級，和 P2 的 KL 不能直接比）、`pos_top1`＝學生在 H 上的第一名等於教師第一名的位置比例、`cand8_*` 是 P2 的 8 候選 KL 與第一名相同比例，用 P2 的 `cand_scores`／`kl_rows` 在同一批留出列上算。`train-R.json` 只有 `cand8_*`，`counts.boosted` 是加權列數。

## 登記沒寫死、我做的決定

| 項目 | 做法與理由 |
|---|---|
| 教師檔格式 | 每列一行（含跳過的列，`skip: 1` 與 `why`），讓訓練端能逐列核對 `i` 與列數，不靠推測對齊。每個位置存 `[字串, [log q …]]`（log q 取 5 位小數，已在 H 上重新正規化），另存 `out`＝重新正規化前落在 H 外的質量。`--out` 以 `.gz` 結尾就是 gzip 文字（全量約數 GB 的文字，壓縮後便於搬到 Colab） |
| H(r) 與 0c 圖的修改 | H 取 lexicon 裡單字（第二欄長度 1）、重複的字去重；與 0c 字表的交集用 `score_0c` 的字表。`log_softmax` 張量在原圖的 `value_info`（[u, 27859]、float）裡，記憶體中搬到圖輸出後再開 session；`score_0c.load_model` 先核對原始位元組的雜湊，我只在它呼叫 `make_session` 時換成加輸出的版本（不寫檔、不改 `score_0c`）。**Mac 沒有 onnx／onnxruntime，這一段沒跑過**：靠 `--limit 1000` 先確認輸出列數等於字數（不符就停）與 outside-H 質量 |
| 跳過原因 | `score_0c.check_row` 對正解句的四種（oov、length、syllables、base），再加 `empty_h`（某個位置的 H 為空）。單音節列（< 2 音節）依 0c 的規則跳過 |
| outside-H 質量的統計 | 所有位置混在一起的中位數與 p90（不是每列平均）。中位數 > 20% 時檔案照寫、結束碼 3 |
| 臂 T 的列集合與留出集 | 訓練列＝P2 可用列 ∩ 有位置級教師的列。為了讓留出集和 P2 完全相同：先對**全部** P2 可用列做 P2 的 `plan`（同種子），再把沒有位置級教師的列從批次與留出集拿掉，所以批次可能略小於 64。留出 8 候選 KL 用留出列中有位置級教師的那些，用 P2 的 `Packed`／`evaluate` 算 |
| 學生字表 | 教師字（已限定在 H ∩ 0c 字表）再丟掉學生字表沒有的字，兩邊在剩下的字上重新正規化；某個位置一個字都不剩的列不訓練（`empty_h_student`）。正解句超過 63 字的列不訓練（`gold_too_long`，P2 對候選的同一條限制）。正解句裡學生字表沒有的字照 `ll_many` 當 UNK 輸入 |
| 損失的層次 | 列的損失＝Σ_j KL(q_j‖p_j)，批次損失＝各列平均（登記字面）；沒有除以字數，所以長句佔比較大 |
| 精度 | 同 P2：前向在 bf16 autocast，H 上的 log-softmax 與 KL 用 float32；教師權重取 float32 |
| 臂 R | 加權＝`--boost`（4）倍，條件是 P2 教師機率的第一個最大值不在第 0 格（平手算在第 0 格）；批次損失是加權列損失的平均，不除以權重和。驗證數字不加權 |
| 共用迴圈 | P2 的 `run` 把損失寫死，不能改 P2；所以 `train_pos.fit()` 是照 P2 複製的一個迴圈（AdamW、`lr_at`、梯度裁剪、發散規則、驗證曲線、輸出 npz 都同 P2），T 和 R 共用，R 的資料／KL／加權之外的部分都是 P2 的函式 |
| 書面非劣性的值 | M 與 T 都用 M 的書面出貨值（α 0.7、τ 1、δ 2），預設在 `--formal-values`，**不從檔案讀**（來源：`docs/contracts/rerank-strength.md` 的匯出指令 `--formal 0.7 1 2`；要從 header 讀用 `export_sjtl.read_sjtl(path)[0]["formal"]` 與 `["delta"][1]`）。報 M、T 各自對 n-gram 第一名的改對／改壞、淨值、改壞數上界 ÷ n，以及 T 對 M 的 McNemar |
| `p2cdiag.py` | 加 `--student`（預設 S，行為不變）；輸出行開頭多印學生名 |
