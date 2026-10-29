# X0C-6：0c 在 v0.6.0 的候選上重量（探索片，不進 main）

計畫：維護者的問題是「外部重排模型 0c 的 App 實驗開關要不要退場」，要先知道 0c 在現行出貨版本（v0.6.0）的候選上，對現行出貨值還剩多少。上一次同樣的量測是 P1（研究紀錄 2026-10-18「P1：0c 在現行候選上重量」，分支 `exp/p1-0c`）：當時對 M 出貨值淨值 +105，但改壞數上界 ÷ n 超過 0.5%。之後通語換成 SJ-LM-7-9.7M、會意換成 SJ-SR-7-15M，聊天又加了常談與讀音通道項，所以重量。程式只在這個資料夾與 `experiments/x0c/`（只用，不改）；0c 打分在 GCP 評測 VM，Mac 不跑重運算。

## 登記（跑之前 commit）

探索片：只用調參層與開發層，不出貨，結論只寫方向；私人集合只記數字。不碰判讀集、保留集。沒有通過／不通過的門檻，結果由維護者讀。

- **問題**：v0.6.0 的候選（通語 SJ-LM-7-9.7M 的 n-gram 解碼，前 8 名）上，0c（`Funmula/0c-sequence-d512`，MIT）加 n-gram 分數的融合，對 v0.6.0 出貨值（會意 SJ-SR-7-15M 加常談與讀音通道項）多出多少、壞掉多少？
- **三個臂**（同一批列、同一批候選）：
  - (a) v0.6.0 出貨：出貨設定 CLI 自己的 rowstats，已含常談與讀音通道項。
  - (b) n-gram 第一名：dump 的第 1 名；另跑一次不帶 `--tinylm` 的 CLI，它的 rowstats 必須和 dump 第 1 名逐列相同，不同就停。
  - (c) 0c 融合：同一批候選的前 8 名，`pick(ng + α·ll, τ, δ)`，`ll` 是 0c 在前文取最後 64 字加讀音的對數機率（`score_0c.py` 的 `ll` 欄）。
- **出貨設定的 CLI**（`tools/bench.py` VERSIONS2「v0.6.0」）：main 的 `0c1abf62`（v0.6.0 的程式；之後只有文件改動）；聊天、標準強度、`--context --packs acg,it --tinylm`（出貨的會意）`--colloquial-core`（出貨的常談），模型檔核對 `data/*.sha256`；摘要行的標記要含常談與讀音通道項（`+colloq:81c2470d+rc`）。
- **集合**（全部聊天設定）：
  - 調參層：chatclause，濾過的 5,631 列，前文用 64 字欄（App 的前文，CTX64 的欄）。
  - 判讀層：discordtune，濾過的 4,919 列，同一個 64 字欄。
  - 只當圖像：dev302、typing76、user-reported（公開，聊天設定）。
- **選值**：只在 chatclause 上用 `fusegrid.select_weighted(weight=1, col="ll", cap_ub=0.004)` 選 α／τ／δ。上界 0.4% 是 P1 的教訓：P1 在調參集守住 0.5% 的格子，到 discordtune 變 0.61%，所以這次留 0.1 pt 的餘裕。格點是 `fusegrid` 的整張（8×6×8）；沒有格子在上界內時，0c 維持 n-gram 第一名。選定後不再動。
- **0c 讀不了的列**（候選有字表外的字、長度不等於音節數、音節數不在 2–32、音節不在表內）：`score_0c.py` 標 `skip`，這些列 (c) 維持 n-gram 第一名；每個集合都數出來報。
- **判讀集 discordtune 上報**：
  - (c) 對 (a)：改對、改壞、淨值、精確 McNemar p、error ratio（改壞 ÷ 改對）。
  - (c) 對 (b)：改對、改壞、淨值、改壞數 95% 上界 ÷ n、error ratio。
  - (a) 對 (b) 作參照；三個臂的第一名正確數；0c 讀不了的列數。
- **調參集另報**：(c) 選到的格子與改對／改壞、0c 單獨挑對率與留住率（定義同 L0 的 (A)）、上界 0.1%–0.5% 的前沿表、(a) 在 chatclause 上對 n-gram 的改對／改壞（出貨值不是在這個集合上調的，只報）。
- **只報、不判**：沒有門檻。方向的讀法由維護者決定；「0c 對 (a) 的淨值」和「(c) 對 n-gram 的改壞數上界」是兩個最先看的數。不做出貨主張；之後要不要把 0c 的 App 開關退場，由維護者在看過數字後決定。
- **隔離與核對**（同 P1）：
  - 0c 打分的程序放在沒有網路的 namespace（`unshare -n`），跑之前先確認連不出去；模型三個檔照 `score_0c.py` 的 `PINNED` 核對雜湊，不符就停；onnxruntime 1.31.0（`experiments/x0c/requirements.txt` 的版本）。
  - 私人列、CLI 的 dump、私人集合的 `ng`／`ll` 分數檔都只在 VM 上，每種結束狀態都刪。選值與融合也在 VM 上跑（`x0c6ana.py`）。
  - 回到 Mac 的：私人集合（chatclause、discordtune）只有每個臂的「列號 TAB 0/1」檔（`arm.<集合>.<臂>.txt`，臂為 ship、ng、fused；`prep.py digits` 驗證只有數字、tab、換行）加 `x0c6ana` 印出的純統計表（驗證為 ASCII）；公開集合另帶分數檔（JSON 數字欄，`prep.py check` 驗證）與 CLI rowstats。
  - 不在 VM 上安裝任何東西：`~/venv` 不能 `import onnxruntime` 就停，訊息寫明缺什麼，由維護者同意後才裝。
- **平台**：GCP 評測 VM，全部步驟用滿核心（CLI 以 `--threads` 同時跑；0c 以 `--workers` 等於核心數，五個集合合成一批打分）。
- **工具**：
  - `prep.py`：`build` 把五個集合的列、dump、n-gram-only rowstats 合成一份輸入（並做上面的逐列核對），`split` 把打分結果切回各集合，`check` 驗證公開集合的分數檔只有數字、`digits` 驗證只有數字與 tab／換行。
  - `x0c6ana.py DIR [EMIT_DIR]`：在 VM 上讀分數檔與 rowstats，印出上面登記的數字，並寫各臂的「列號 TAB 0/1」檔。
  - `test_x0c6.py`：合成檔案的 smoke test（`python3 -m unittest experiments/x0c6/test_x0c6.py`）。
- **之後怎麼走**：結果寫進研究紀錄，只寫方向；不開子片補救同一次量測。
