# 解碼器依「字串＋最後一個詞」合併（v0.4.3）

狀態：已合併（PR #147），隨 v0.4.3 發佈（2026-10-14）；READY（2026-10-14 收尾審查）；plan-verifier 第一次 REVISE（6 點）、第二次 REVISE（2 點：§3.1 改在 Mac 以外的機器跑、第 226 列的 `Selftest.expected` 與相等即停）都照改（FIX）。

## 0. 起因

- 錯字回報第 80 列（`夏來分析`，正解 `下來分析`，書面、句首）。研究紀錄 2026-10-13「『夏來分析』與解碼器的合併方式」拆過分數：`下／來／分析` 是三條路徑裡最高的（−9.76），但解碼器在位置 2 只為字串 `下來` 留一條路徑，留下分數較高的詞 `下來`（−5.06），丟掉 `下｜來`（−6.42），接 `分析` 時 `來→分析` 遠比 `下來→分析` 好。
- 這是 bigram 解碼的合併錯誤：下一個詞的分數取決於最後一個詞，合併時卻只看字串。2026-10-10（奧爾加）已記成缺陷，延到 model-v5 時漏掉（同一節的〔更正〕）。
- scratch 試驗（只改 Python 參考實作，beam 不變）：dev302、typing76、錯字回報（77 列）、cvtune、wikitune 兩種設定，每一格淨值 ≥ 0（cvtune 聊天 +5、書面 +3；wikitune 聊天 +7、書面 +7）。沒量 Rust、速度、記憶體。
- 維護者 2026-10-13 選擇題：v0.4.1 之後的第一片，跟 v0.4.2 出。〔2026-10-14〕學習前文的修正（`docs/contracts/learn-single-ctx.md`）維護者選擇單獨先發 v0.4.2，這一片改跟 v0.4.3 出。

## 1. 規則（核心與參考實作各一處）

1. **合併的鍵**：`reference/proto/lm.py` 的 `decode` 與 `core/src/lm.rs` 的 `decode_segment_learned`，每個位置的候選從「每個字串留分數最高的一條」改成「每個（字串, 最後一個詞）留分數最高的一條」。最後一個詞用**詞的字串**當鍵（不用 LM 的詞 id：字表外的詞沒有 id；Python 本來就用字串算下一個詞的分數）。
2. **同分**：沿用現在的規則，先放進去的留下（Rust `!(sc > cand[q].score)`、Python `sc > cand[...][0]`）；候選放進去的順序不變。
3. **beam**：每個位置照舊留分數最高的 `beam` 條（`BEAM_S1`；Python 預設 64），數的是路徑，不是相異字串。beam 的值不改。
4. **輸出去重**：解碼結束（加完句尾或右邊固定詞的分數、排序後），同一個字串只留分數最高的一條，順序照分數。這樣呼叫端拿到的 n-best 仍然是相異字串：語意校正的前 8 名（`engine.rs` 的 `TOPK`）、評測 CLI 的 oracle 與 dump 都不會出現重複的句子。
5. **不變的部分**：學習加分（`Learn`，依字串查前文）、降權表、右邊固定詞的收尾（`End::Next`）、`decode_beam`（沒有 LM 的 unigram 解碼）都不改。

## 2. golden 與一致性

- **預期輸出一律由 Python 參考實作重產**，Rust 逐位元組相同；PR 說明逐一列出改過的檔或值與它的產生指令：
  - `eval/golden/` 依 LM 的檔：`s2-lm.txt`、`s2-lm-dev302-top1.tsv`、`s2-lm-dev302-{chat,formal}.rowstats`、`s2h-lm-context.txt`、`s2r-probe-top1.tsv`、`sw-probe.txt`、`sp-predict.txt`（各檔的產生指令照 `docs/data-files.md`「怎麼產生」）；
  - `eval/golden/kn-tiny/decode.tsv`：`python3 -B tools/gen_kn_tiny.py`；
  - `cli/tests/golden.rs` 寫死的 `--no-classes` 摘要行：`reference/proto/lm_eval.py --no-classes --context --dev 302`（兩種設定）；
  - dev302 第 226 列的兩種設定第一名：`core/tests/c/abi_smoke.c` 的 `CHAT`／`FORMAL` 與 `macos/Sources/ShanjieKit/Selftest.swift` 的 `expected`，都照重產後 `s2-lm-dev302-top1.tsv` 的第 226 列改；
  - 其他 `make test` 拿來和 LM 解碼結果比的值：executor 先列清單、main 核對，同樣由 Python 重產。
- 不依 LM 的（`unigram.txt`、`s1-*`、`s2r-probe-unigram.txt`、`sbench-unigram-typing76.txt`）必須不變。
- **門檻類斷言一律不改**（學習率 ≥ 80%、每鍵 p95 < 16 ms、筆數下限等）；不過就是 §4 的停止條件。`git diff` 裡不能有任何門檻常數改變。
- `docs/data-files.md` 的 golden 說明跟著改（有寫產生方式的地方）。
- **自我測試的句子**：`macos/Sources/ShanjieKit/Selftest.swift` 的 `ngramText`（`我等一下在過去`）與 `rerankedText`（`我等一下再過去`）在新解碼器下用 CLI 各解一次寫進 PR 說明；只要任一個改變，或兩個變得相同，就是停止條件（重挑句子照 `docs/data-files.md` 的程序，由 main 做）。

## 3. 驗收

1. **一致性**：§2 所有預期輸出，Rust 與 Python 逐位元組相同（executor，`make test-rust`）。cvtune、wikitune（不在 repo，雜湊照 `docs/contracts/s4-bayes.md` 記的 `31de456d…`、`8dcfe40c…`，先核對）兩種設定的 Rust 與 Python 摘要行（含 `top1_sha256`）相同：由 **main** 在 Mac 以外的機器上跑（Python 與同一個 commit 建的 Rust CLI；那台機器上的兩個檔先核對雜湊；照 `CLAUDE.md`「Mac 不跑重運算」）。
2. **同一張表**（`CLAUDE.md`「評測與資料」）：基準＝main（v0.4.1 等價），新＝這一片。dev302、打字測驗、錯字回報（當時的全部列），聊天與書面各一行，`--net`；**只用 n-gram** 一組、**開語意校正**（`--tinylm data/lm/tinylm.sjtl`，檔頭的 R3′ 與書面值，不重選）一組。
3. **守門**（公開）：cvtune、wikitune、lytune、wikitypo 兩種設定、只用 n-gram 與開語意校正各一組，沒有任何一格「改對 < 改壞 且精確 McNemar p < 0.05」；cvtune＋wikitune 每種設定的淨值合計 ≥ 0。
4. **私人調參集**（main 跑，只貼數字）：discordtune 兩種設定、只用 n-gram 與開語意校正各一行。E2 判斷期 2026-08 已用掉，這一片不用。
5. **速度**（走 LM 解碼的每鍵 p95，release，前後各量一次，數字記進研究紀錄，斷言照舊 < 16 ms）：`core/tests/engine_lm.rs` 的 `replay_standard_chat_production_path`、`replay_standard_formal`；`core/tests/engine_learn.rs` 的 `perf_full_store_per_key_and_enter_with_write`（有學習）；`core/tests/engine_predict.rs` 的 `timing_typing76_keys`。（`engine_replay.rs` 走沒有 LM 的 `decode_beam`，量不到這次的改動，不用。）**記憶體**：評測 CLI 跑 dev302 聊天的最大 RSS（`/usr/bin/time -l`）前後各一次。
6. **預測**：`core/tests/engine_predict.rs` 的 `measure_start_range`（`#[ignore]`，用 `--ignored` 跑）前後各一次，記出貨規則（w5m3）那一行在 user-typing 與 user-reported 的 hit@9：最佳路徑的斷詞變了，V3 預測取長詞起點的地方也會跟著變。
7. **案例**：書面、句首 `ㄒㄧㄚˋ ㄌㄞˊ ㄈㄣ ㄒㄧ` 的第一名是 `下來分析`（CLI，錯字回報第 80 列）。
8. **保留集**：收尾時由 fresh verifier 照 `CLAUDE.md` 跑一次（兩種設定、只用 n-gram 與開語意校正），只回表格。
9. **實機**（使用者，發 v0.4.3 前）：裝新版，書面設定打 `下來分析` 那串注音，畫面是 `下來分析`；平常打字沒有變慢的感覺。

## 4. 停止條件

- Rust 與 Python 任何一個 golden 不同而查不出原因。
- §3.3 任一格違反，或 §3.2 有任一行「改對 < 改壞 且 p < 0.05」。
- 每鍵 p95 ≥ 16 ms。
- 保留集任一設定淨值 < 0。
- §3.2、§3.3 任一行「開語意校正」那組的淨值（改對 − 改壞）< 0（這組直接吃解碼器的前 8 名；要重選 α、τ、δ 是另一片）。
- §3.6 的 hit@9 在任一集合比基準少超過 1 列。
- 門檻類斷言不過（§2）；自我測試的句子改變（§2）。
- 重產後 dev302 第 226 列聊天與書面的第一名相同（這一列是用來證明切換設定會改變輸出的；由 main 另挑一列）。
  - 〔2026-10-14，實作時觸發〕新解碼器下第 226 列聊天與書面都是 `幸好救護車及時趕到`。main 改用**第 284 列**（`廚房新裝了一個濾水壺`）：dev302 裡唯一一列 unigram（`廚房新莊了一個率水壺`）、聊天（`…綠水壺`）、書面（`…濾水壺`）兩兩不同；正解在書面那邊（原本第 226 列是聊天對），`acceptance6_sentence_has_three_different_top1` 改斷言 `formal == truth`。`Selftest.swift`、`abi_smoke.c`、`Support.swift`、`engine_lm.rs` 兩個測試、`docs/contracts/profile-choice.md` 跟著改。

碰到就停下來回報，不自己放寬。

## 5. 範圍外

beam 大小；trigram；學習規則（句首單字學習是下一片）；重選語意校正的 α、τ、δ；`decode_beam`。

## 6. 負責人、順序、預算

1. main：這份契約 → fresh `pilotfish:plan-verifier`。
2. `pilotfish:executor`（worktree `../shanjie-recombine`，分支 `feat/dec-recombine`）：§1 的兩處改動、輸出去重、§2 的重產、§3.1 的 repo 內部分、§3.5–§3.7、單元測試（至少一個測試：沒有合併修正時會留下錯的路徑、修正後留下 `下｜來`，用合成的小詞庫與 LM，不靠真模型）。本機測試照「改動分級」（Rust、Python、Swift 都會被影響）：`make test-rust`、`make test-python`、`make test-swift`、`python3 -m unittest reference/proto/test_demote.py`（直接呼叫 `decode`，不在 `test-python` 裡），回報各自的結束碼。
3. main：§3.1 的 cvtune／wikitune 一致性、§3.2–§3.4 的表與私人集合；/code-review＋fresh verifier（含保留集）＋CI；merge commit。
4. 預算：executor 一輪＋一次修正；超過就停。不花錢、不用 Colab。

## 7. executor 不可以做的事

安裝軟體；啟動 App、呼叫 TIS、lsregister；碰 `~/Library`、鑰匙圈；讀維護者的私人資料、`eval/holdout/`；push、開 PR、留言；改 beam 或其他常數；改這份契約。
