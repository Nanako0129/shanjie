# 0c 實驗開關（第一片：Rust 推論試做）

狀態：修訂三（2026-10-19，維護者選擇題：A4 的延遲與記憶體沒過門檻之後，加前綴共用再量），plan-verifier READY。A1–A3 完成；A4 的三項量測在修訂三的程式（`ecd98540`）上重跑，三項都過（§6 最後一列）。修訂二的數字在研究紀錄 2026-10-19（PR #221）。

## 0. 起因與範圍

- 研究紀錄 2026-10-18 的 P1、P2：
  - 0c 的公開模型（`Funmula/0c-sequence-d512`，MIT）在現行候選、調參上界 0.3% 的規則下，discordtune 對 M 出貨值淨值 +75、上界 0.29%。
  - 自己的蒸餾學生（P2）是 −2。
- 維護者 2026-10-19 的決定：
  - 先否決直接出貨 0c：「代價太大了，既失去我們原有的研究成果，又無法宣稱是自己的東西」。
  - 之後改成「0c 可以出貨一版實驗」，並用三題選擇題定下：
    1. 做成設定裡的實驗開關，預設關。
    2. 在 Rust 裡自己寫 int8 推論，核心維持零外部套件。（修訂二：int8 沒過門檻，維護者同日選「改出 f32、用三元表那條唯讀映射路」；「Rust 自己寫、零外部套件」不變。）
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
  - 寫成自己的容器 `SJOC0001`：檔頭有 magic、版本、維度、字表大小，以及來源 `model.onnx` 與 `vocab.json` 的 SHA-256；字表（字、注音基底、可輕聲的基底、輸出欄的 `out_ids`）一起放進去；權重只存 f32（修訂二）。每個張量的起點都對齊 4 位元組（UTF-8 區塊後面補 0），Rust 才能把映射的位元組直接當 `&[f32]` 用。
  - 需要 `onnx` 套件，在 Colab 或 GCP 上跑，不在 Mac 上裝。
  - 轉出的檔不進 repo（MIT 的衍生物，之後由 B 決定怎麼發佈）；repo 只放程式與雜湊。
- **A2 numpy 參考實作**（`tools/oc_ref.py`）：
  - 照 §1 寫前向，讀 `SJOC0001`（f32）。
  - 在 dev302（公開）上和 ONNX Runtime 的分數比：log10 的最大絕對差 ≤ 1×10⁻⁴（f32 檔）。2026-10-19 已量：最大 3.15×10⁻⁵，300/300 列分數最高的候選相同（研究紀錄同日）。修訂二加了 4 位元組對齊，轉檔要重跑一次，這個比對也重跑一次。
  - 這一步確認轉檔與結構理解都對；不過就停（§4）。
  - 修訂二（main 寫）：
    - `oc_ref.py --export ITEMS.tsv --dump D --dev 302` 從 dev302 列與候選 dump 寫出 `ITEMS.tsv`（`列號<TAB>前文<TAB>以空白分隔的讀音<TAB>以 | 分隔的候選`，只含 n ≥ 2 的列）。
    - 加 `--pad-context 64` 寫出 `ITEMS-64.tsv`：每列的前文都剛好 64 字，等於固定的公開文字（`eval/dev/existing.txt` 句子欄依檔案順序接起來的前 64 字）接上這一列自己的前文，再取最後 64 字。dev302 自己的前文幾乎都是空的或只有幾個字，延遲門檻的「給 64 字前文」靠這個檔。
    - `oc_ref.py --compare MODEL_DIR SJOC --dump D --dev 302 --rust SCORES.jsonl` 讀 Rust 量測程式的分數檔，報 Rust 對 numpy、Rust 對 ORT 的 log10 差（最大、p99）與「分數最高的候選」相同的列數比例。ORT 現場跑，所以這一步在 GCP 上跑。
- **A3 Rust 推論**（`core/src/oc.rs`，新模組，照 `tinylm.rs` 的寫法）：
  - 修訂二：用 `trigram.rs` 的 `Mapped`（唯讀整檔映射，三元表已在用，契約 `trigram-mmap.md`）載入 `SJOC0001`，權重直接從映射讀成 `&[f32]`、不複製進堆積；不受 `tinylm::MAX_FILE` 限制（真模型 f32 約 131 MB）。載入時檢查檔頭、版面算出的總長度和檔案大小完全相同、每個張量對齊 4 位元組，任一不符就拒絕。`Mapped::open` 回傳的錯誤型別要能給 0c 用（executor 決定怎麼泛化）。
  - `score(context, syllables, candidates) -> Vec<Option<f64>>`：每個候選一個 log10 P，打不了的是 `None`；規則和 §1 最後一點相同。
  - 矩陣乘法用 `tinylm` 既有的 `gemm`（macOS 是 Accelerate，其他平台是純 Rust）；GELU 用 erf，要自己實作或用既有的函式，精度要過 A2 的比對。
  - **前綴共用**（修訂三）：同一列的所有候選共用 `[BOS] 前文 [SEP] 讀音×n [OUT]` 這一段（輸入完全相同，因果遮罩下也不看後面的字），每一層的 K、V 只算一次並快取；每個候選只再算自己的 n−1 個字，注意力讀快取的前綴加上自己前面的字。輕聲另試時讀音不同，前綴另算一次。
    - 結果必須和逐候選算整條序列相同：golden 照舊用 numpy 的逐候選分數（`packed=False`），容差 1×10⁻⁵ 不變。
    - 不跨列快取（每列重算前綴）；跨列、跨按鍵的快取是 B 片的事。
    - 修訂二的逐候選算法（8 條整序列補齊成一批）2026-10-19 已量過：64 字前文時 p95 74.5 ms、RSS 峰值增量 +273.2 MiB，兩條都沒過；這是修訂三的起因。
  - **量測程式**（修訂二）`core/examples/oc_score.rs`，`cargo run --release -p core --example oc_score -- …`；不改 CLI、不改引擎：
    - `--sjoc PATH --items ITEMS.tsv --out SCORES.jsonl`：每列一行 `{"i": 列號, "ll": [每個候選的 log10] 或 null}`（整列打不了是 null），規則和 `score()` 相同。給 A4 的一致性用。
    - `--sjoc PATH --items ITEMS.tsv --latency [--no-context] [--tinylm M.sjtl]`（`--no-context` 把每列的前文換成空字串）：單執行緒，每列計時一次 `score()`（給 `--tinylm` 時同一批列也計時 M 的打分），跑 3 輪，報每輪的 p50／p95 與 3 輪的中位數；p95 取排序後第 int(n × 0.95) 個，和 CLI 相同。
    - `--sjoc PATH --items ITEMS.tsv --mem --tinylm M.sjtl`（只在 macOS）：用 `task_info` 讀 `MACH_TASK_BASIC_INFO` 的 `resident_size`（RSS）與 `TASK_VM_INFO` 的 `phys_footprint`（libc 的 extern 宣告，零套件）。量三個點：程式開始、載入 0c 並打完所有列之後、再載入 M 並打完所有列之後；每個點報當下的 RSS 與 phys_footprint，另報 `MACH_TASK_BASIC_INFO` 的 `resident_size_max`（RSS 峰值，含打分時的暫時緩衝；`/code-review` 指出只量打完一列後的值會漏掉長列的峰值）。報 0c 一個的增量與兩個合計的增量，峰值另列。
- **A4 量測**（main 做；修訂三：三項都在修訂三的 commit 上重跑，§3 的門檻一律用重跑的數字判，修訂二的數字只留在研究紀錄）：
  - **一致性**（GCP，一台 VM 上做完）：重新轉檔、`oc_ref.py --export` 產 dev302 聊天的 `ITEMS.tsv`、`oc_score --out` 產 Rust 分數、`oc_ref.py --compare … --rust SCORES.jsonl` 比對。(1) Rust 對 numpy（同一份 f32 檔）：log10 最大差；(2) Rust 對 ORT：log10 差的 p99 與最大值，以及「8 個候選裡分數最高的那個」相同的列數比例。
  - **延遲**（Mac，空閒、單執行緒；用 `VECLIB_MAXIMUM_THREADS=1` 跑，Accelerate 才不會自己開多執行緒）：不給前文是 `oc_score --sjoc … --items ITEMS.tsv --latency --no-context --tinylm data/lm/tinylm.sjtl`；給 64 字前文是 `oc_score --sjoc … --items ITEMS-64.tsv --latency --tinylm data/lm/tinylm.sjtl`。M 在同一場當基準。這是少量的單句量測，允許在 Mac 上做。
  - **大小與記憶體**（Mac）：檔案大小；`oc_score --mem` 的 RSS 與 phys_footprint 增量（映射讓兩者分家：RSS 會算映射頁，footprint 不算）。

## 3. 驗收

- **跨語言 golden**（`tools/gen_oc_tiny.py`，照 `gen_tinylm_tiny.py`）：
  - 用固定種子做一個同結構的極小隨機模型，寫成 `SJOC0001`（f32），放在 `eval/golden/oc-tiny/`。
  - 用 numpy 參考實作算出分數表。`core/tests/oc_tiny.rs` 讀同一份檔，分數要和表一致：容差 1×10⁻⁵（log10），沿用 tinylm golden 的做法。
  - 項目要涵蓋：字表外的字、空前文、需要截掉的長前文、單音節（不打分）、輕聲另試、33 個音節（超過上限，不打分）。
- **真模型一致性**（不在 CI；main 跑，數字寫進研究紀錄）：A2 的 numpy 對 ORT ≤ 1×10⁻⁴；Rust 對 numpy（同一份 f32 檔）≤ 1×10⁻⁴；dev302 上 Rust「分數最高的候選」和 ORT 相同的列 ≥ 99%。（修訂二：int8 門檻刪掉，因為不出 int8。）
- **記憶體門檻**：`oc_score --mem` 量到的 RSS 峰值增量（程式開始的 RSS → M 和 0c 都載入並打完所有列之後的 `resident_size_max`）≤ 250 MiB（維護者 2026-10-18 定的重排模型上限；指標用 RSS，因為它把映射頁也算進去，是比較嚴的那個）；phys_footprint 只報。
- **延遲門檻**：照 `rerank-size` §4，每列 8 個候選的重排 p95 ≤ 50 ms（Mac 空閒、單執行緒，用 `ITEMS-64.tsv` 那一場：每列都是 64 字前文）。
- **突變**（每一項都要讓 golden 測試失敗，build error 不算）：
  - 拿掉 RoPE；
  - 拿掉因果遮罩；
  - 讀音位置改用字嵌入；
  - 拿掉 type 嵌入；
  - GELU 改成 tanh 近似；
  - 輸出頭少一個 LayerNorm；
  - 輕聲不另試。
  - 修訂三的前綴共用另加七項（2026-10-19 在 `31cff1f0` 上跑，全部被抓到）：
    - 後段的 RoPE 從 0 起算；
    - 快取存 RoPE 之前的 K；
    - 後段不讀快取；
    - 看得到的長度少 1；
    - 只拿掉前綴的因果遮罩；
    - 只拿掉後段的因果遮罩；
    - 同一批的候選互相看得到。
- `make test-rust`（完整的在另一台 Linux 機器上跑）、CI 全綠；Swift 與殼都沒改。

## 4. 停止條件、預算

- **停止條件**：
  - A2 對不上 ORT（結構理解有誤），查一輪還對不上就停。
  - 圖裡出現 §1 沒列到、又不是常見寫法的運算。
  - RSS 增量合計 > 250 MiB。
  - 延遲 p95 > 50 ms。
  - 任一條成立就停下來，把數字交給維護者，不自己改門檻。
- **預算**：
  - 研究顧問估 executor 約 3 天。
  - Colab 或 GCP 的 CPU 跑轉檔與 ORT 參考分數，各不到 1 小時。
  - Mac 只跑測試與 A4 的少量延遲量測。

## 5. 範圍外（B 片）

- 引擎接線：`RerankJob` 帶音節、聊天設定才用 0c、學過的 stretch 不重排、單音節保留第一名。
- 前文回傳：殼的 `maxUTF16` 32 → 256（`docs/contracts/context-return.md`）；核心另存的 `left_full` 只留宣告的前文長度或結尾的英文詞。0c 讀 `left_full` 要另過 security-reviewer（含 XPC 跨程序）。
- 設定開關、融合值（α／τ／δ）、關於頁與 LICENSES 的 MIT 署名、模型檔怎麼發佈、CLI 與重播工具支援。
- 書面設定（照研究顧問的建議只接聊天，B 再確認）。
- 閒置後第一鍵的延遲（映射頁被系統丟掉再讀回；三元表已有同樣性質，KPI 有這一欄）：B 片在實機量。
- **引擎要用 `try_score`**（`/code-review` 2026-10-19 第二輪）：`score()` 把非有限值與「打不了的列」都變成 `None`，損壞的模型檔會讓開關靜靜地什麼都不做。B 片接線時用 `try_score`，`Err` 當成模型損壞：關掉 0c、記一行靜態日誌、設定頁顯示模型不可用。
- 效能（`/code-review` 第二輪，延遲已在門檻內，先不做）：`[OUT]` 那一列的輸出頭每個候選重算一次、輕聲另試整段重算前綴、前綴最後一層對所有位置都算 FFN。B 片要跨按鍵快取時一起看。
- **App 沙盒之後的位置**（app-sandbox 修訂二，維護者 2026-10-19）：輸入法進沙盒後，下載的模型放在 container 裡；R8 的 XPC 輔助程式排在 B 片設計。〔2026-10-20〕沙盒外的獨立更新程式「善解更新」已在 v0.5.0 做掉（`standalone-updater.md`），B 片只剩 R8 的推論輔助程式與模型下載。
- **R8 的衝突（`/code-review` 2026-10-19 指出）**：`docs/PLAN.md` 開頭與 §7 R8 規定「下載的、第三方的或使用者指定的權重放在 XPC helper」，例外只涵蓋自己訓練、自寫格式、隨 App 打包的 S7 字元模型。0c 是第三方權重，若照研究顧問的建議在開關打開時才下載，就同時是下載的權重。這一片只寫離線的推論模組、不接進引擎，所以不違反 R8；B 片接線之前要先決定：照 R8 放進 helper，或放寬 R8（放寬要先過 security-reviewer，再由維護者決定，同一個 PR 改 R8 的文字）。
- 模型檔怎麼發佈：研究顧問建議開關打開時才下載（資料類 Release、SHA-256 釘在 `data/*.sha256`），不包進 App；B 片定。
- int8、f16 等其他格式：修訂二不做；要做另開一片。

## 6. 審查紀錄

| 審查 | 結果 | 處置 |
|---|---|---|
| plan-verifier 第 1 次（2026-10-19） | REVISE：真模型的 Rust f32 比對超過 64 MiB 上限；int8 停止條件暗含 f16 備案；§1 有未觀察到的結構寫成事實 | 全部照最小修改：真模型改 Rust int8 對 numpy int8；int8 沒過就停、f16 移到範圍外；§1 標〔推論〕並把兩個待確認項加進 A1 清單 |
| plan-verifier 第 2 次（2026-10-19） | READY | — |
| 修訂二（2026-10-19） | A4 量到逐列 int8 296/300、p99 0.437，照 §4 停下；診斷最好的 int8 變體 p99 0.109、約 76 MB | 維護者選擇題：改出 f32、用 `Mapped` 唯讀映射載入，int8 拿掉；改 §2 A1／A3／A4、§3、§4、§5。等 plan-verifier |
| plan-verifier（修訂二，2026-10-19） | REVISE：沒有任何交付物能讓 main 在真實的列上跑 Rust，A4 與 §3 的一致性、延遲、記憶體都量不到；RSS 怎麼讀沒寫 | 照最小修改：A3 加量測程式 `core/examples/oc_score.rs`（分數檔、延遲、記憶體三種模式，記憶體用 `task_info`）；A2 加 `oc_ref.py --export／--rust`；A4 與記憶體門檻指向它們 |
| plan-verifier 收尾（修訂二，2026-10-19） | REVISE：延遲門檻要「給 64 字前文」，但 dev302 的前文幾乎都很短，量測程式產生不出這個條件；P3：`--rust` 的指令寫法不完整 | 第二次 REVISE 逐項處置，兩項都 FIX：`oc_ref.py --export --pad-context 64` 產 `ITEMS-64.tsv`（固定公開文字補到 64 字），A4 與延遲門檻寫明用它；指令改成完整寫法 |
| plan-verifier 收尾（修訂二，第 2 次，2026-10-19） | READY | — |
| `/code-review`（2026-10-19，786fc165） | 14 項 | 修：test_oc_ref 接進 make test-python、Rust 對 numpy 改用逐候選的 numpy、非有限值與重複字表要停、延遲加 VECLIB_MAXIMUM_THREADS=1、記憶體門檻改量峰值、對齊檢查不靠 align_to、契約拿掉主機名、convert_oc 說明、oc.rs 註解與小重構、量測程式拒絕無關旗標。延後：R8 的衝突寫進 §5，B 片接線前決定 |
| 修訂三（2026-10-19） | A4：一致性過（Rust 對 numpy 2.33×10⁻⁵、對 ORT 4.07×10⁻⁵、300/300）；延遲 p95 74.5 ms（64 字前文）、RSS 峰值增量 +273.2 MiB，照 §4 停下 | 維護者選擇題：加前綴共用（每列的前綴只算一次、K／V 快取），門檻不動；改 §2 A3。等 plan-verifier |
| plan-verifier（修訂三，2026-10-19） | REVISE：狀態行仍寫 A3 完成、A4 一致性已過，但那是修訂二的程式；契約沒要求在前綴共用的程式上重量一致性；狀態行指向的研究紀錄還沒有 A4 的數字 | 照最小修改：狀態寫 A3 重開、A4 三項都在修訂三的 commit 上重跑並以重跑的數字判；修訂二的 A4 數字進研究紀錄（PR #221，合併後併進這個分支）；「修訂二之前」改成「修訂二的逐候選算法」 |
| plan-verifier（修訂三，第 2 次，2026-10-19） | READY | — |
| A4 重跑（修訂三，`ecd98540`，2026-10-19） | 一致性（GCP C4D）：Rust 對 numpy 2.33×10⁻⁵、對 ORT 4.07×10⁻⁵，第一名相同 300/300；檔案 `oc.sjoc` `fbdb21da…`、`ITEMS.tsv` `feca7850…`、`ITEMS-64.tsv` `59d9e3cb…`。記憶體（Mac）：RSS 峰值增量 +227.5 MiB（0c 單獨 +146.2）。延遲（Mac 閒置窗，開始時負載 2.86、單執行緒）：64 字前文 p95 33.94 ms（3 輪 34.83／33.84／33.94）、沒有前文 p95 27.21 ms | 三項都過門檻（≤ 250 MiB、≤ 50 ms），A 片完成 |
| `/code-review` 第二輪（2026-10-19，`786fc165..a2fc8b80`） | 13 項；K/V 快取與遮罩本身正確 | 修（`31cff1f0`）：超過 8 個候選時前綴每 8 個重算一次 → 每次嘗試只算一次；輕聲另試用 `max` 合併會吞掉 NaN → 合併前檢查；補非有限值與超過一批候選的測試；延遲模式印出 `VECLIB_MAXIMUM_THREADS`；`oc_ref.py` 兩處說法改正。前綴共用的七項突變全部被抓到（§3）。寫進 §5：B 片用 `try_score`、三項效能建議延後。不改：共用 `layer_norm` 的寫法（影響很小） |
