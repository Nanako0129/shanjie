# L0：既有 M 權重上換成 listwise 目標（探索片，不進 main）

計畫：`docs/iteration-plan.md` §4.1（維護者 2026-10-18 晚上選擇題：下一個研究片做 L0；過門檻才接 Phase 2 全套，沒過就把預算改投資料與前文回傳）。這個資料夾是 `exp/l0-listwise` 分支上的程式與單元測試；資料準備與打分由 main 在 GCP 執行，訓練在 Colab（Mac 不跑重運算）。S7a（`experiments/s7a-tinylm/`）的程式一律不改，只 import。

## 檔案

這個資料夾是 `exp/l0-listwise` 分支上 executor 寫的全部程式與單元測試；資料準備與打分由 main 在 GCP 執行，訓練在 Colab（Mac 不跑重運算）。S7a 與 tools 的程式一律不改，只 import（`sys.path` 由 `l0core.py` 補好）。下面「登記」一節是跑之前凍結的規格，這一段與後面幾段是實作說明。

| 檔 | 做什麼 | 需要 torch |
|---|---|---|
| `rows.py`、`test_rows.py` | 訓練列的建法（main 寫的，不是這一批的內容） | 否 |
| `l0core.py` | `k_cross`、`context_for`（兩種前文）、選項與正解（`ok_flags`、`target_of`、`untrainable_reason`）、特徵 `features`、`pack_row`／`collate`（預先編成 token、補齊成陣列）、決策規則 `pick`、`select_t`（上界規則選 t）、`alone_retain`（單獨挑對率與留住率）、`m_view`（把 `score.py` 紀錄當成分數表） | 否 |
| `torch_l0.py` | `L0Model`（M 的 GPT 到最後的 LayerNorm，加兩個線性頭）、`list_loss`、`save_l0`／`load_l0`、`score_packs` | 是 |
| `train_l0.py` | 訓練，每格 w 一個 epoch；寫 `l0-w<w>.pt`、`train-w<w>.json` | 是 |
| `score_l0.py` | `l0`（L0 打分，`--context 64\|2`）、`m64`（M 的似然、前文取到 64 個漢字）；分數檔只有數字 | `l0` 要；`m64` 用 numpy 後端就不要 |
| `gate.py` | 讀分數檔，印登記裡的讀數與 (A)(B) 的 PASS／FAIL | 否 |
| `test_l0.py` | 單元測試（合成小資料）；6 個 torch 測試沒有 torch 時跳過 | 部分 |

## 命令順序

記號：`M`＝`work/s7a/tinylm-M.npz`；`R_<集>`＝各集合的列檔；`D`＝dump 資料夾；`SCORES`＝分數檔資料夾；訓練列用 `rows.py` 建好、去重後的 `wiki.txt`、`pool.txt`（做法見 `rows.py` 開頭的說明）。同一次比較的所有臂放在同一種 CPU 上：第 1–3 與第 5 步在同一種 VM（C4D Spot）上跑。VM 先裝 `libopenblas0-pthread`，用 `readlink -f /etc/alternatives/libblas.so.3-x86_64-linux-gnu` 確認，啟動長工作前先量 100 列估時間。

```bash
# 1. 候選 dump（GCP，出貨設定的 Rust CLI；四個聊天集合加兩份訓練列。每份 summary line 的 top1 記下來，當下面的 N_<集>）
cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile chat --context --packs acg --rows R_chatclause.txt --dump D/chatclause.dump --name chatclause --threads 32
#   同樣跑 discordtune、chatjudge、lytune（聊天）、wiki.txt（→ D/wiki.dump）、pool.txt（→ D/pool.dump）

# 2. M 出貨前文的 LL（S7a 的 score.py，不改；--out 的檔名照 gate.py 的命名）
python experiments/s7a-tinylm/score.py --npz M --backend numpy --dump D/chatclause.dump --rows R_chatclause.txt --name chatclause --expect-top1 N_chatclause --out SCORES/M.chatclause.jsonl
#   discordtune、chatjudge、lytune 一樣 → SCORES/M.<集>.jsonl

# 3. M 的似然、前文取到 64（只要調參層）
python experiments/l0/score_l0.py m64 --npz M --backend numpy --rows R_chatclause.txt --dump D/chatclause.dump --name chatclause --expect-top1 N_chatclause --out SCORES/M64.chatclause.jsonl

# 4. 訓練（Colab G4，兩格合計 ≤ 3 單元；先在 CPU 上跑過 test_l0.py 的 torch 測試）
python experiments/l0/train_l0.py --npz M --data wiki.txt D/wiki.dump pool.txt D/pool.dump --w 1 3 --out OUT
#   → OUT/l0-w1.pt、l0-w3.pt、train-w1.json、train-w3.json；結束碼 2＝發散

# 5. L0 打分（GCP，兩格 w × 兩種前文 × 四個集合）
python experiments/l0/score_l0.py l0 --model OUT/l0-w1.pt --rows R_chatclause.txt --dump D/chatclause.dump --name chatclause --expect-top1 N_chatclause --context 64 --device cpu --out SCORES/L0w1.c64.chatclause.jsonl
#   --context 2 → SCORES/L0w1.c2.<集>.jsonl；w3 同；discordtune、chatjudge、lytune 同

# 6. 讀數
python experiments/l0/gate.py --tune chatclause --dev discordtune chatjudge lytune --dir SCORES
```

`gate.py` 的檔名約定（聊天設定；調參集與每個開發集都要有）：

| 檔名 | 來源 |
|---|---|
| `L0w<w>.c64.<集>.jsonl`、`L0w<w>.c2.<集>.jsonl` | `score_l0.py l0 --context 64`／`2`（模型 `l0-w<w>.pt`） |
| `M.<集>.jsonl` | S7a 的 `score.py`（欄位 `n`、`ok`、`ng`、`ll`），M 出貨值用 `fusegrid.pick(r, 0.3, 2, col="ll", delta=0.25)` |
| `M64.<調參集>.jsonl` | `score_l0.py m64`（只有調參集） |

每個集合的所有檔列數與 `ok` 必須相同，不同就停。輸出全是數字表（除了集合名沒有別的文字）：(A) 的單獨挑對率與留住率（L0 的四格、M、M@64）、每格選到的 t 與 16 格的完整表、每個開發集的 L0 對 n-gram（改對、改壞、淨值、改壞數上界、上界 ÷ n、error ratio）與對 M 出貨值（兩邊各自的對錯、淨值、精確 McNemar p）、最後的 (A)(B) 判定。判定只看前文 64；前文 2 的列照報、不判。

## 契約沒寫死、我做的決定

| 項目 | 做法與理由 |
|---|---|
| 選項與索引 | 選項個數＝候選個數 n（≤ 8），選項 i 就是候選 i（0 起算，0＝維持第一＝第 1 名）。模型為每個候選各編一次碼（第 1 名也編，給 `keep_head`），一列最多 8 條序列 |
| 特徵 | 8 維：`[ng_i − ng_0]` 加名次 2..8 的 one-hot（名次 i+1 對應第 i 個 one-hot 欄）。`ng` 直接用 dump 的分數，沒有重新換算（登記寫 log10，dump 的單位沒有在這裡驗，見下方） |
| 序列 | `[BOS] + enc(k) + [BOS] + enc(c)`，字表外的字走 UNK；總長 ≤ 64。k 的長度由候選長度決定（`62 − len(c)`），所以每個候選的 k 不同。前文 2：`tinylm.context_key`（≤ 2 個漢字，遇標點就停），再從左邊截到放得下 |
| 向量取法 | 最後一個真實位置的最終 LayerNorm 輸出；右邊補 PAD，因果注意力讓補的位置碰不到前面（測試對照一次一列的結果，atol 1e-5） |
| 兩個頭的初始化 | 權重 N(0, 0.02)、偏差 0（和 GPT 其餘線性層同一種）。兩格 w 在建模型前都 `torch.manual_seed(seed)`，所以頭的初始值與 M 的權重完全相同 |
| 學習率 | 全部參數（M 與兩個頭）同一個峰值 1e-4、同一個排程（`train.lr_at`，暖機 100）；登記沒有分，所以沒有給頭另外的學習率 |
| 衰減 | 權重衰減 0.1 加在 `ndim ≥ 2` 的參數（含兩個頭的權重），偏差與 LayerNorm 不加 |
| 損失的正規化 | 批內加權平均：`Σ(權重 × 交叉熵) / Σ權重`，權重＝w（目標是維持第一）或 1。分母用權重和而不是列數，w = 3 才不會順便把整體學習率放大；登記只寫「乘 w」，沒有說分母 |
| 可訓練列 | 依序檢查：候選 < 2（`n<2`）、有候選超過 62 個字（`too_long`）、前 n 名沒有寬鬆比對（`no_match`）；三種都只計數。正解取第一個寬鬆相符的候選（`eval.lenient`；兩個都對時取名次前的） |
| 超長候選（> 62 字） | 不訓練；打分時整列 `noscore: 1`，決策維持第一，單獨挑對率與留住率把它當「選了第 1 名」算進分母。`m64` 的上限是 63（`ll_many` 放得下的長度） |
| 只有一個候選的列 | 不訓練；分數檔寫 `s: [0.0]`（不編碼），決策恆為維持第一 |
| 留出列 | 在可訓練列中用 `default_rng(seed)` 的一個排列，前 `round(0.02 × N)` 個（至少 1 個）當留出列，其餘依同一個排列的順序成批（最後一批可以不滿）。留出的損失是不加權的交叉熵（不同 w 的格才可比），另報列表正確率；只畫曲線。`--max-steps` 只取前 N 批，排程在那裡結束（冒煙測試用） |
| 兩格同批 | 批次由 `split` 一次決定，兩格共用；`train-w<w>.json` 記 `train_order_sha256`，兩格相同；測試另外攔截 `prefetch`，驗證兩格收到的批次逐項相同 |
| 發散 | 記錄點（每 100 步的平均損失）不是有限值，或暖機之後的記錄點高於第一個記錄點，就印 `DIVERGED`、結束碼 2（不寫 `.pt`，整個程序停，不繼續下一格） |
| 預取 | `collate` 在一條背景執行緒上領先 4 批，GPU 不等補齊；每步的工作只有搬進裝置與前向、反向 |
| 精度 | 訓練：CUDA 上 bf16 autocast（損失用 float32），CPU 用 fp32。打分：fp32、關 TF32、不 autocast（和 `TorchLM` 一樣） |
| 存檔 | 一個 `.pt`：`state`（整個 `L0Model` 的權重）、`cfg`（GPT 的 V、層數、寬度、頭數、ctx）、`chars`、來源 npz 的 SHA-256、`extra`（w、種子、步數）；`torch.load(weights_only=True)` 可讀 |
| 決策規則的邊界 | 邊際 `m > t` 嚴格大於；`t = 0` 時 `m = 0`（打平）維持第一。第 2..n 名並列最高時取名次前的 |
| 無門檻（沒有任何 t 符合上限） | 登記只說「記無門檻」。實作：gate 印「無門檻」，開發集上該格視為 t = ∞（不換，等於 n-gram），(B) 判 FAIL。t = 8 幾乎不會換，這一條實務上幾乎碰不到 |
| 單獨挑對率 | 沒有門檻時的選項 argmax（並列取名次前）；M 與 M@64 用 `ll` 的 argmax，同一個函式，「維持第一」＝索引 0。`noscore` 的列算選第 1 名 |
| (A) 的條件 | 單獨挑對率 ≥ 55% **且**留住率 ≥ 92%（登記的兩條都要），整數比較（`n × 100 ≥ 55 × d`）。在調參集、前文 64、每格 w 各判一次 |
| (B) 的條件 | 前文 64 的 discordtune：L0（選到的 t）對 M 出貨值的淨值（L0 對 M 錯 − L0 錯 M 對）≥ +40，**且**L0 對 n-gram 的改壞數上界 ÷ n ≤ 0.5%。M 出貨值＝`fusegrid.pick(r, 0.3, 2, col="ll", delta=0.25)` |
| 總判定 | 任一格 w 通過 (A) 或 (B) 就印 PASS（登記「任一格 w 符合任一條」）。(A)、(B) 的 overall 也各印一行，看得出是哪一邊過 |
| 分數檔 | `i, n, ok, s` 或 `noscore`；`ok` 是每個候選的寬鬆比對，所以 gate 不需要列檔、dump 或任何文字 |
| `--expect-top1` | 必填，用 `score.top1_from_dump` 從 dump 第一名重算，不同就在載入模型之前 `sys.exit`（同 `score.py`） |

## 要知道的後果（不是 bug）

- **torch 的程式沒有在 Mac 上跑過**（Mac 沒有 torch）：`torch_l0.py`、`train_l0.py`、`score_l0.py l0` 與 `test_l0.py` 的 `TestTorch` 在 main 第一次 CPU 試跑之前沒有被執行過。非 torch 的部分（`l0core`、`gate`、`score_l0.py m64`）有跑；`train_l0.py` 的資料建構與分批（`build_data`、`split`、`prefetch`、`diverged`）用臨時腳本抽出函式驗過。先在 GCP 或 Colab 上跑 `python -m unittest discover -s experiments/l0 -p 'test_*.py' -v`，6 個 torch 測試全過再開訓練。
- dump 的 n-gram 分數是不是 log10（登記這樣寫）沒有在這裡驗證。特徵 `ng_i − ng_0` 的尺度由 dump 決定，頭只能線性使用它。
- k 取到 62 − len(c) 個字，所以長候選的 k 比短候選短；同一列裡各候選看到的前文量不同，這是登記的設計。
- 前文 2 用的是**同一個**以前文 64 訓練的 `.pt`，只在打分時換 k（登記的「App 現況」不重訓）。
- 調參集上的 M 只報單獨挑對率與留住率；M 出貨值只在開發集上當比較對象。

## 測試

```bash
python3 -m unittest discover -s experiments/l0 -p 'test_*.py' -v
```

沒有 torch 時（Mac 的 `python3`）跑 `test_l0.py` 的非 torch 測試與 `test_rows.py`，6 個 torch 測試跳過。非 torch 的測試：`k_cross`；選項與正解（寬鬆變體 她／他、兩個都對取前面的、三種不可訓練的原因）；特徵；token 序列與補齊（兩種前文、長度邊界 62／63／64）；決策規則的邊界；選 t（上限、並列取大、無門檻、小集合）；單獨挑對率與留住率（含 `noscore`、M 的 `ll`）；`gate.py` 在手造的 1000 列集合上的精確數字與 PASS／FAIL；檔案不對齊就停；`--expect-top1` 不符就停（`m64` 與 `l0`）；`m64` 端到端（對 `ll_many` 的暴力算法、一列超長候選、一個候選的列）。torch 測試：補齊不改分數、損失等於手算的遮罩加權交叉熵、存檔再讀分數相同、`train_l0.py` 在 T 尺寸隨機模型上兩格端到端（兩格批次相同、計數、留出曲線、同種子重跑權重逐位相同、兩格權重不同）、發散規則、`score_l0.py l0` 端到端（逐列用一次一個候選、手寫序列的前向對照；前文 64 與 2 的分數不同）。都不需要 GPU。

## 登記（2026-10-18，main 寫，跑之前 commit）

探索片：只用調參層與開發層，不出貨，結論只寫方向；私人集合只記數字。不碰判讀集、保留集、chatjudge-2。

- **假設**：重排的鑑別力卡在「生成式似然」這個目標上（M 單獨挑對 44.6%，XL 只多 8 列；口語語料 +2–7 pt 換不到淨值；E-B 的讀音前綴也沒有幫助）。同一組 M 權重改用「從 n-gram 前 8 名裡挑對的那一個」的 listwise 目標微調，單獨挑對率與留住率都會明顯上升。只換目標，不換架構，不建新語料。
- **模型**：
  - encoder 是出貨 M（`work/s7a/tinylm-M.npz`，SHA-256 `32308122…`，就是 `tinylm-v5.sjtl` `d07de71d…` 量化前的權重）的 transformer，不用 LM 輸出層。
  - 每個候選 c 的輸入是 `[BOS] k [BOS] c`：第二個 BOS 當分隔符（不加新符號，字表與 SJTL 格式不變）。k 是前文去掉所有非漢字之後的最後 62 − |c| 個字（跨標點），所以總長最多 64。取最後一個位置（c 的最後一字）的最終 LayerNorm 輸出當向量 h。
  - 〔更正，2026-10-18，跑之前〕第一版寫成「`tinylm.han_tail`，跨標點」。但 `han_tail` 遇到非漢字就停，不會跨標點。這裡照研究顧問的原意改成跨標點，下面的「前文對照」也用同一個 k。
  - 選項：「維持第一」與第 2..n 名（n ≤ 8）。維持第一的分數是 `w_keep·h_1 + b_keep`（h_1 是第 1 名的向量）；第 i 名（i ≥ 2）的分數是 `w·h_i + u·f_i + b`，f_i 是 [第 i 名對第 1 名的 n-gram 分差（log10）, 名次 2..8 的 one-hot]。
  - 第 1 名不另外再算一個候選分數：維持第一就是選第 1 名，兩個都放進 softmax 會變成兩個正解。研究顧問的「8 個候選＋維持第一」照這樣實作。
- **損失**：對這 n 個選項做 softmax 交叉熵。目標是正解所在的選項：正解是第 1 名時目標是「維持第一」。正解用評測的寬鬆比對（`eval/variants.tsv`）判斷，有多個候選都算對時取名次最前的。正解不在前 n 名的列，以及 n < 2 的列不用來訓練。正解是第 1 名的列損失乘 w。w ∈ {1, 3} 兩格，事先固定。
- **訓練資料**：只用不在 n-gram 計數、也不在 M 訓練文字裡的文字。
  - 維基第 200,000–299,999 篇：計數與 M 用前 200,000 篇，wikitune 與各判讀集從第 300,000 篇起。dump 和計數用的是同一份（`zhwiki-20261001`，`5db9052e…`）。清理、轉換與子句切法照 `experiments/s2/build_tune.py`（4–30 個漢字的子句），去重後用種子 20261018 抽 120,000 個子句。前文是同一條目裡這個子句之前的文字。
  - Phase 2 句子池 `pool.tsv`（143,895 句，SHA-256 `19fdc474…`，`exp/p2-data`，已對評測集合去洩漏）全部用上，一樣切成 4–30 個漢字的子句。前文是同一句裡這個子句之前的文字；同一支 YODAS 影片或同一則 WildChat 訊息的前一句也接上去。不做 Moore–Lewis 篩選。
  - 讀音用 `tools/readings.py`，含 CHECK 或讀不出的列丟掉。和四個評測集合的句子完全相同的訓練列也丟掉（在 GCP 上比對，只回列數）。
  - 不用 `colloquial-train.txt`、`synth.txt`（都在計數裡）。
- **候選**：出貨設定的 Rust CLI（main `02c1a8cf`；model-v6、classes-v5、kn-v1、三元表 sj-cx-1-7.7m），參數 `--profile chat --context --packs acg --dump`，訓練列與評測集合用同一個版本。GCP C4D 上跑。
- **訓練**：在 Colab G4 上跑，每格 w 跑一個 epoch，兩格的批次與順序完全相同。
  - 每批 64 列，AdamW（β 0.9／0.95，權重衰減 0.1 只加在矩陣上），峰值學習率 1e-4，暖機 100 步後 cosine 降到 0，梯度裁切 1.0，CUDA 上用 bf16 autocast，種子 20261018。
  - 另外留 2% 的訓練列，只用來畫曲線、看有沒有發散，不拿來選任何值。損失出現 nan，或暖機後高於起始值，就停下。
  - 不做超參數搜尋。
- **評估集合**（都用聊天設定，都已有分數檔的前例）：調參層是 chatclause 調參期（5,761 列）；開發層是 discordtune（4,958）、chatjudge（2,972，已用過）、lytune 聊天（4,811，只當警示）。
- **決策規則**：邊際 m＝第 2..n 名的最高分 − 維持第一的分數；m > t 才換成那一名。
  - t 的格點事先固定：{0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 3.5, 4, 5, 6, 8}，共 16 格，每格 w 各一組。
  - 在調參層選 t：條件是改壞數的 95% 精確 Poisson 上界 ÷ n ≤ 0.5%（`evalstats.poisson_upper95`），符合的格裡取淨值（改對 − 改壞，都對 n-gram 第一名）最大的；同分取較大的 t。沒有任何一格符合，就記「無門檻」。
- **主讀法**（寫死；任一格 w 符合任一條就算過）：
  - (A) chatclause 調參期上，L0 不設門檻（選項 argmax）時：
    - 單獨挑對率 ≥ 55%：分母是可改的列（n-gram 前 8 名有正解、第一名不對；E-A 量的是 738 列，M 為 44.6%）。
    - 留住率 ≥ 92%：分母是 n-gram 第一名就對的列（5,540 列，M 為 86.9%）。
  - (B) discordtune 上，L0（用選到的 t）對「M 出貨值」（α 0.3、τ 2、δ 0.25，`fusegrid.pick`）的淨值 ≥ +40，而且 L0 對 n-gram 的改壞數上界 ÷ n ≤ 0.5%。
  - 如果這次 dump 算出的可改列數與留住列數和 738／5,540 不同，就用新數字，在研究紀錄寫明。
- **只報、不判**：
  - 開發層各集合的改對／改壞、精確 McNemar p、改壞率上界、error ratio。
  - **前文對照**：用 M 的似然、前文同樣取到 64 的單獨挑對率與留住率（`ll_many`，k＝前文去掉非漢字後的最後 63 個字，和 L0 的 k 同一個函式；超長的部分由 `ll_many` 截掉）。L0 過了而 M@64 也過 (A) 時，研究紀錄要寫明增益有多少來自前文、多少來自目標。
  - **App 現況**：L0 只給 ≤ 2 個漢字的前文（`context_key`，App 目前 `set_left_context` 給的範圍），重新選 t，報 (A) 的兩個比例和 discordtune 對 M 出貨值。
- **之後怎麼走**：過了就照 §4.1 接 Phase 2 全套（建 30 萬列的 Moore–Lewis 資料、3 個種子、前文回傳另開產品片）。沒過就結案、記方向：目標不是瓶頸，Phase 2 的預算改投資料與前文回傳。不開子片補救，變體回到探索。
- **平台與預算**：
  - GCP：c4d-highcpu-32 Spot，us-central1，`--max-run-duration` 加 DELETE。建列與 dump 約 2 小時，打分約 1 小時，做完當場刪 VM。
  - Colab：G4，兩格合計 ≤ 3 單元。超過 4 單元就停掉第二格，照實報告。
  - 私人集合由 Mac 放到 VM 上，核對雜湊；只回數字，VM 刪除時一起清掉。
- **已知限制**：
  - 離線用 64 字前文，增益到實機會縮水，要等前文回傳才拿得到。上面的「App 現況」就是在量這一塊。
  - 只有 1 個種子：這是探索，結論只寫方向。
