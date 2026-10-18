# 0c 實驗開關（第一片：Rust 推論試做）

狀態：草稿（2026-10-19），等 plan-verifier。

## 0. 起因與範圍

- 研究紀錄 2026-10-18 的 P1、P2：
  - 0c 的公開模型（`Funmula/0c-sequence-d512`，MIT）在現行候選、調參上界 0.3% 的規則下，discordtune 對 M 出貨值淨值 +75、上界 0.29%。
  - 自己的蒸餾學生（P2）是 −2。
- 維護者 2026-10-19 的決定：
  - 先否決直接出貨 0c：「代價太大了，既失去我們原有的研究成果，又無法宣稱是自己的東西」。
  - 之後改成「0c 可以出貨一版實驗」，並用三題選擇題定下：
    1. 做成設定裡的實驗開關，預設關。
    2. 在 Rust 裡自己寫 int8 推論，核心維持零外部套件。
    3. 順便做前文回傳：殼給同一則訊息、跨標點、最多 64 字的前文。
- 預設行為不變（開關預設關），所以這件事不是確認片，不用掉 chatjudge-2；chatjudge-2 留給自己的學生（蒸餾 B 之後）。
- 分兩片：
  - **這一片（A）**：權重轉檔、numpy 參考實作、Rust 推論、一致性與延遲量測。先確認自己寫的推論可行，才開 B。
  - **下一片（B）**：接進引擎與殼、前文回傳、設定開關、融合值、署名與發佈。另寫契約；A 的量測結果會寫進 B。
- 0c 在這個專案的定位照維護者 2026-10-13 的決定與 2026-10-19 的補充：是外部模型，對外不稱 SJ-SR 系列。

## 1. 模型結構（2026-10-19 在 Colab 用 onnx 套件拆解 `model.onnx`，commit `66eb4a81`，兩個檔的 SHA-256 和 `score_0c.py` 釘住的值相同）

拆解印出了輸入輸出、全部運算的次數、所有大於 1,000 個值的初始值形狀，以及前 60 個節點，也就是到第 0 層前饋層的 `Split` 為止。下面標〔推論〕的，是從運算次數與初始值形狀推出來的，圖上沒有直接看到，由 A1 從圖讀出確認。

- **輸入**（每條序列）：
  - `ids`、`bases`、`tones`、`types`、`positions`。
  - 遮罩：下三角。
  - `proj_pos`：要投影的位置。
  - `target_node`、`target_ids`：每個要打分的字在序列中的位置與字的編號。
- **輸出**：`scores`，每個候選一個 log P（自然對數）。
- **嵌入**：
  - 讀音位置（`types == 1`）用 `base_embedding[bases] + tone_embedding[tones]`，取代字嵌入。
  - 其他位置用 `decoder.weight[ids]`；這張表也是輸出層。
  - 再加 `type_embedding[types]`。
  - 加完做 LayerNorm（只有 weight、沒有 bias，eps 1e-5）。
- **8 層**：
  - 第 0 層的注意力前面沒有 LayerNorm（圖上看到）；1–7 層有〔推論：18 個 LayerNorm＝嵌入 1＋注意力前 7＋前饋前 8＋最後 1＋輸出頭 1〕。
  - 注意力：q、k、v 各一個 512×512 線性層，沒有 bias；8 個 head，head 寬 64。
  - q、k 先套 RoPE（cos／sin 由 `positions` 算，rotate-half 寫法），再做縮放點積加因果遮罩，接輸出線性層，殘差相加。
  - 前饋層：LayerNorm → 512×1536 線性層 → 拆成兩半（圖上看到到這裡）→〔推論〕其中一半過 GELU（erf 版，9 個 Erf＝8 層＋輸出頭）乘另一半 → 768×512 線性層，殘差相加。
- **最後**〔整段推論，依運算次數與初始值形狀〕：
  - LayerNorm。
  - 在 `proj_pos` 的位置取向量 → `lm_head.dense` 512×512（Gemm，有沒有 bias 由轉檔時確認）→ GELU → LayerNorm。
  - 乘 `decoder.weight` 的轉置，加偏差（27,865 維）→ LogSoftmax。
  - 取 `target_ids` 的值，對每個候選加總。
  - `out_ids`（27,859 個）是輸出的字表範圍，`target_ids` 是字的編號減 6。
- 總參數 32,638,433。
- 由 A1 轉檔時從圖讀出並寫進轉檔程式註解的項目（不用猜）：RoPE 的基數；GELU 套在 `Split` 的前半還是後半；第 1–7 層注意力前的 LayerNorm 與最後兩個 LayerNorm 的位置；輸出頭有沒有 bias、GELU 與 LayerNorm 的順序；LogSoftmax 是在 27,865 維上算，還是在 `out_ids` 篩過的 27,859 維上算；`proj_pos`、`target_node`、`target_ids` 的確切用法與 ReduceSum 的範圍。
- 序列格式和打分規則照 `experiments/x0c/score_0c.py`：`[BOS] 前文 [SEP] 讀音×n [OUT] 候選前 n−1 字`，最後一個音節在 `neutral_tolerant_last` 時另試輕聲取大，音節數 2–32，字表外的字整列不打分。

## 2. 這一片要做的（A）

- **A1 轉檔**（`tools/convert_oc.py`，Python）：
  - 讀 `model.onnx` 的權重，依圖的節點順序對應到 §1 的角色。初始值的名稱是 `val_38` 這類流水號，要靠圖找出每個是哪一層的 q、k、v、o 等。
  - 寫成自己的容器 `SJOC0001`：檔頭有 magic、版本、維度、字表大小，以及來源 `model.onnx` 與 `vocab.json` 的 SHA-256；字表（字、注音基底）一起放進去；權重有 f32 與 int8 兩種（int8 用逐列對稱量化，和 `export_sjtl.py` 同一種）。
  - 需要 `onnx` 套件，在 Colab 或 GCP 上跑，不在 Mac 上裝。
  - 轉出的檔不進 repo（MIT 的衍生物，之後由 B 決定怎麼發佈）；repo 只放程式與雜湊。
- **A2 numpy 參考實作**（`tools/oc_ref.py`）：
  - 照 §1 寫前向，讀 `SJOC0001`（f32 與還原後的 int8）。
  - 在 dev302（公開）上和 ONNX Runtime 的分數比：log10 的最大絕對差 ≤ 1×10⁻⁴（f32 檔，在 Python 裡讀，不受 Rust 的 64 MiB 上限影響）。
  - 這一步確認轉檔與結構理解都對；不過就停（§4）。
- **A3 Rust 推論**（`core/src/oc.rs`，新模組，照 `tinylm.rs` 的寫法）：
  - 載入 `SJOC0001`（int8 在載入時還原成 f32），檔案上限照 `tinylm::MAX_FILE`（64 MiB）。真模型只有 int8 檔（約 33 MB）放得進去；f32 檔（130 MB）Rust 不載入，只在 golden 的極小模型與 Python 裡用。
  - `score(context, syllables, candidates) -> Vec<Option<f64>>`：每個候選一個 log10 P，打不了的是 `None`；規則和 §1 最後一點相同。
  - 矩陣乘法用 `tinylm` 既有的 `gemm`（macOS 是 Accelerate，其他平台是純 Rust）；GELU 用 erf，要自己實作或用既有的函式，精度要過 A2 的比對。
  - 先不做前綴共用與 KV 快取：8 個候選補齊成一批（和 tinylm 一樣）。
- **A4 量測**（main 做）：
  - **一致性**：dev302 聊天 300 列。(1) Rust int8 對 numpy int8（同一份 int8 檔、還原後算）：log10 最大差；(2) Rust int8 對 ONNX Runtime（f32）：log10 差的 p99 與最大值，以及「8 個候選裡分數最高的那個」相同的列數比例。
  - **延遲**：Mac 空閒、單執行緒，每列 8 個候選，不給前文與給 64 字前文各一次，報 p50／p95。同一場也量 M（`--tinylm`）當基準。這是少量的單句量測，允許在 Mac 上做。
  - **大小**：檔案大小，以及載入前後的 RSS。

## 3. 驗收

- **跨語言 golden**（`tools/gen_oc_tiny.py`，照 `gen_tinylm_tiny.py`）：
  - 用固定種子做一個同結構的極小隨機模型，寫成 `SJOC0001` 的 f32 與 int8 兩份，放在 `eval/golden/oc-tiny/`。
  - 用 numpy 參考實作算出分數表。`core/tests/oc_tiny.rs` 讀同一份檔，分數要和表一致：容差 1×10⁻⁵（log10），沿用 tinylm golden 的做法。
  - 項目要涵蓋：字表外的字、空前文、需要截掉的長前文、單音節（不打分）、輕聲另試、33 個音節（超過上限，不打分）。
- **真模型一致性**（不在 CI；main 跑，數字寫進研究紀錄）：A2 的 numpy f32 對 ORT ≤ 1×10⁻⁴；Rust int8 對 numpy int8（同一份 int8 檔）≤ 1×10⁻⁴。前者證明結構理解與轉檔正確，後者證明 Rust 算的就是 numpy 算的；int8 本身的量化誤差由下一條另外看。
- **int8 門檻**：dev302 上「分數最高的候選」和 ORT f32 相同的列 ≥ 99%，log10 差的 p99 ≤ 0.05。不過就照 §4 停下來。
- **延遲門檻**：照 `rerank-size` §4，每列 8 個候選的重排 p95 ≤ 50 ms（Mac 空閒、單執行緒、給 64 字前文）。
- **突變**（每一項都要讓 golden 測試失敗，build error 不算）：
  - 拿掉 RoPE；
  - 拿掉因果遮罩；
  - 讀音位置改用字嵌入；
  - 拿掉 type 嵌入；
  - GELU 改成 tanh 近似；
  - 輸出頭少一個 LayerNorm；
  - 輕聲不另試。
- `make test-rust`（在 188 的 WSL 跑完整的）、CI 全綠；Swift 與殼都沒改。

## 4. 停止條件、預算

- **停止條件**：
  - A2 對不上 ORT（結構理解有誤），查一輪還對不上就停。
  - 圖裡出現 §1 沒列到、又不是常見寫法的運算。
  - int8 沒過門檻（不改做 f16 或其他格式；那是另一片）。
  - 延遲 p95 > 50 ms。
  - 任一條成立就停下來，把數字交給維護者，不自己改門檻。
- **預算**：
  - 研究顧問估 executor 約 3 天。
  - Colab 或 GCP 的 CPU 跑轉檔與 ORT 參考分數，各不到 1 小時。
  - Mac 只跑測試與 A4 的少量延遲量測。

## 5. 範圍外（B 片）

- 引擎接線：`RerankJob` 帶音節、聊天設定才用 0c、學過的 stretch 不重排、單音節保留第一名。
- 前文回傳：殼的 `maxUTF16` 32 → 128；核心另存跨標點、同一則訊息的前文給 0c。
- 設定開關、融合值（α／τ／δ）、關於頁與 LICENSES 的 MIT 署名、模型檔怎麼發佈、CLI 與重播工具支援。
- 書面設定（照研究顧問的建議只接聊天，B 再確認）。
- int8 以外的格式（f16 等）：int8 沒過門檻時另開一片評估，不在這一片。

## 6. 審查紀錄

| 審查 | 結果 | 處置 |
|---|---|---|
| plan-verifier 第 1 次（2026-10-19） | REVISE：真模型的 Rust f32 比對超過 64 MiB 上限；int8 停止條件暗含 f16 備案；§1 有未觀察到的結構寫成事實 | 全部照最小修改：真模型改 Rust int8 對 numpy int8；int8 沒過就停、f16 移到範圍外；§1 標〔推論〕並把兩個待確認項加進 A1 清單 |
