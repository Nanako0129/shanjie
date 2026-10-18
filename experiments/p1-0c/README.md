# P1：0c 在現行候選上重量（探索片，不進 main）

計畫：維護者 2026-10-18 晚上的選擇題（L0 結案後，「資料與前文回傳」底下先做這一片；前文回傳先不開片，等這一片的數字）。研究顧問的分析在維護者本機的 advice 資料夾（`fable-2026-10-18-after-l0.md`）。程式只在這個資料夾與 `experiments/x0c/`（只 import，不改）；打分在 GCP，Mac 不跑重運算。

## 登記（2026-10-18，main 寫，跑之前 commit）

探索片：只用調參層與開發層，不出貨，結論只寫方向；私人集合只記數字。不碰判讀集、保留集、chatjudge-2。

- **問題**：0c（`Funmula/0c-sequence-d512`，MIT，研究紀錄 2026-10-13 X0c）是唯一在私人聊天上量到大效果的模型：v0.4.1 的候選加舊規則，discordtune 對 R3′ +127。換成現行出貨候選（main `9633b496`：model-v6、classes-v5、kn-v1、三元表 sj-cx-1-7.7m），在上界規則下它還剩多少？這個數字就是之後蒸餾（P2）的天花板。
- **打分**：
  - 四個聊天集合用出貨設定的 CLI dump：`--profile chat --context --packs acg`，GCP C4D。
  - M 用 `experiments/s7a-tinylm/score.py`（torch CPU），它的輸出也是 0c 打分的 `--v3` 檔。
  - 0c 用 `experiments/x0c/score_0c.py`，欄位 `ll`（前文取最後 64 字）、`ll0`（不給前文）、`llr`（全部當一聲，只記錄）。
  - 全部在同一台 VM 上跑。
- **隔離**：X0c 在 Mac 上用 `sandbox-exec`。這次在一次性的 GCP VM 上跑，VM 上沒有任何金鑰，做完當場刪掉。0c 打分的程序用 `unshare -n` 放進沒有網路的 namespace。
  - onnxruntime 1.31.0 與 numpy 2.5.4 的版本照 `experiments/x0c/requirements.txt`；那份的雜湊是 macOS arm64 的 wheel，Linux 上用不了，所以只釘版本、不釘雜湊。
  - 模型三個檔照 `score_0c.py` 的 `PINNED` 核對雜湊，不符就停。
- **集合**：調參層 chatclause 調參期（5,761）；開發層 discordtune（4,958）、chatjudge（2,972，已用過）、lytune 聊天（4,811，只當警示）。
- **選值**：在 chatclause 調參期上用 `fusegrid.select_weighted(cap_ub=0.005)` 選 α／τ／δ，權重 1，0c 用 `ll` 欄。另外印 `fusegrid.frontier(ub=True)` 的前沿表（上界 0.1%–0.5%）。M 也照同一規則選一次（「M 同規則」），只報。
- **主讀法（寫死）**：0c 用選到的值，在 discordtune 上同時符合兩條才算過，過了才開 P2：
  - 對 M 出貨值（α 0.3、τ 2、δ 0.25，`fusegrid.pick`）的淨值 ≥ +40；
  - 0c 對 n-gram 第一名的改壞數 95% 上界 ÷ n ≤ 0.5%。
- **只報、不判**：
  - 開發層各集合：0c 對 n-gram、0c 對 M 出貨值（改對／改壞、淨值、精確 McNemar p）、改壞率上界、error ratio。
  - **前文**：`ll0` 另外照同一規則選值，報同樣的表。「`ll` 對 `ll0` 選擇不同的列數、其中各自對的列數」就是前文的貢獻。
    - 聊天集合的前文只有一段：同一則訊息的前一段，或 10 分鐘內同頻道的上一則（`build_chatclause.py`）。列檔沒有標是哪一種，所以這次量不出「只用 App 看得到的前文」（研究顧問提的 `llk`）。
    - `ll` 對 `ll0` 的差是前文回傳能拿到的上限。上限低於下面的 MDE，前文回傳就不必開片；高於 MDE，再補建列檔的標記去量 `llk`。
  - **MDE**：用 chatjudge 上「0c 對 M 出貨值」的不一致列數，等比放大到 chatjudge-2 的 5,783 列，算 `evalstats.mde`（α 0.05 與 0.01，檢定力 80%）。
  - 0c 單獨挑對率與留住率，定義同 L0 的 (A)。
- **教師分數**（P2 的資料，不論這一片過不過都產出）：L0 的訓練列（維基 112,850 列＋句子池 175,064 列，前 8 名 dump 已有，`l0train8.tar.zst` `2c58e673…`）用 0c 打 `ll`、`ll0`。`n`、`ng`、`ok` 等欄位由 `v3stub.py` 從 dump 與列檔算，不跑 M。只存在 repo 外；這是 0c（MIT）的衍生物，之後出貨要署名。
- **之後怎麼走**：過了照維護者的決定開 P2（蒸餾學生，另外登記）。沒過就記方向：蒸餾的天花板不夠，預算轉向研究顧問列的 P3（本機自學的時間序重播）或 P4（聊天計數表），再出選擇題。不開子片補救。
- **平台與預算**：GCP c4d-highcpu-32 Spot，us-central1，`--max-run-duration` 4 小時加 DELETE，約 3–4 小時；Colab 0。私人集合由 Mac 放上去並核對雜湊，只回數字，VM 刪除時一起清掉。
