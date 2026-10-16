# 契約：trigram 進核心，只用在書面設定（trigram-core）

狀態：修訂三（2026-10-16；修訂一、二處理 plan-verifier 的兩次 REVISE，見 §5；修訂三把第 B 片寫細）。

## 0. 依據

- derisk-1015 的 E1g、E1h（研究紀錄 2026-10-16 兩節；契約 `docs/contracts/derisk-1015.md` §3 E1g、E1h）：書面設定**關掉 KN、加一張 trigram 表**，在調參集上顯著勝過現在的書面設定（bigram＋詞類 μ 0.2＋KN）。通過的檔裡最小的是 **H3**：相對熵剪枝、7,700,964 條、trigram 表 143.6 MB；調參集 +64（205/141，p 0.0007），對「只關 KN」+117，判讀五集 +36、discordtune +35（都不顯著為負）。單獨關 KN 在調參集顯著變差（−53）。聊天設定沒有訊號（E1f：KN 關的 trigram +11、p 0.46），所以聊天不動。
- 增益集中在維基語域（wikitune、lytune）；口語的 cvtune 各檔 −11 到 +13，不顯著。受益的是書面設定（自動模式下聊天 App 以外都是書面）打百科、正式文的人；發版說明照實寫。
- 那些數字都是 Python 參考實作（`experiments/derisk-1015/e1/`）在 **B0** 上量的：B0 是照 model-v6 配方重算的 bigram，和 model-v6 步驟 2 的無指紋版逐位元組相同（研究紀錄「derisk-1015 的結果」的重現一節）；詞類表是用 model-v6 的分群檔為 B0 重建的（μ 0.2）。出貨的是 model-v6（多了指紋條目）＋classes-v5。trigram 表的 c、t3、back3 只依 trigram 計數、不依低階的 P2（`trimodel.py` 檔頭），所以表可以重新對應到 model-v6 的詞 id 直接用；但「增益在出貨模型上還在」要先量（§2 第 0 片）。
- 維護者 2026-10-16 的選擇題：核心契約從 H3 開始；上限 **RSS +150 MB、每鍵 p95 +1 ms**（現在 RSS 約 293 MB、dev302 重播 p95 約 2.3 ms）。
- 設計分析：Fable（2026-10-16），配對、切片與風險排序照它的建議，數字由 main 核對過。

## 1. 要做到什麼、怎麼看到

- 書面設定的解碼用「model-v6＋classes-v5＋trigram 表、KN 關」；聊天設定與預測列（V3）不變。學到的詞在書面不經過 trigram（第 A 片），選字記憶在書面有表時照 model-v6 的門檻量過（第 B 片）。
- 怎麼看到：
  - 開發者：Rust CLI `--profile formal` 的摘要行多 `+tri:<表 sha8>`、少 `+kn:`；和 Python 參考實作逐列相同（第 A 片）。
  - 發版：跨版本基準在書面設定的 lytune、wikitune 看到增益，使用者端的集合（保留集、錯字回報、discordtune）沒有顯著變差；每鍵延遲與 RSS 在上限內（第 B、C 片）。
  - 使用者：書面設定打百科類的句子時，第一名較常是對的；聊天設定不變；改選過的詞在書面照樣學得起來。

## 2. 切片

每片各自 PR，照「改動分級」的程式關卡（`/code-review`＋fresh verifier＋CI）。第 0 片是實驗，不改核心。

### 第 0 片：出貨模型上的前置檢查（Windows 機器，Python，不改核心）

- 程式（`experiments/derisk-1015/e1/` 旁加，或新資料夾 `experiments/trigram-core/`）：
  - `remap_tri.py`：把 `.sjt3` 從 B0 的詞 id 換成 model-v6 的詞 id（照詞串對應），重算 `ctx_key`、重排序，檔頭的模型 SHA-256 改綁 model-v6。B0 有、model-v6 沒有的詞 → 停（B0 是 model-v6 去掉指紋條目，不應該發生）。
  - 單元測試：每個 (u, v, w) 詞串三元組的 c、每個 (u, v) 的 t3 前後相同；條目數、前文數相同。
- 量法（照 E1g 的設定：書面 λ 0.7、β 1、`--context`；集合是 E1g 的凍結檔，逐一核對雜湊與列數）：
  - BASE：**出貨的書面形狀**：model-v6＋classes-v5＋kn-v1（KN 開）。
  - CAND：model-v6＋classes-v5＋重對應的 H3，KN 關。
  - 判讀（重現，不是新假設）：調參集 CAND 對 BASE 顯著為正（a = 0.05），判讀五集串接與 discordtune 不顯著為負。
  - 只報：和 E1h 的 FH3k 逐列比 top1，不同的列數（指紋條目與詞類表的差異都會造成）。
- 不通過 → 停，回報維護者；通過 → 第 A 片。CAND 的 rowstats 與摘要行就是第 A 片 Rust 錨點的目標。
- 預算：Windows 機器一小時。

### 第 A 片：Rust 讀表、解碼器、書面關 KN、CLI 錨點（App 不開）

- **檔案**：沿用 `SJT30001`（`experiments/derisk-1015/e1/sjlm_io.py` 檔頭）。放在模型旁、檔名 `trigram.sjt3`。產生程式從 `experiments/` 移到 `tools/build_trigram.py`（重對應＋寫檔），測試進 `tools/test_*.py`。
- **Rust `core/src/lm.rs`**：
  - `Lm::load`：模型旁有 `trigram.sjt3` 就讀（核對 magic、V、綁的模型 SHA-256、長度、`ctx_off` 單調、`next_id` 在前文內遞增），不合就新的 `LmError` 變體（不靜默略過，訊息點名這個檔）；沒有就關閉。整檔 `std::fs::read` 進記憶體（和 bigram 相同；core 不加依賴）。
  - 打分：照 `trimodel.py` 的式子，`P3 = max(c − D, 0)/t3 + back3·P2`，D = 0.75（寫在檔頭或常數，和 Python 同一個值；檔頭的 D 不是 0.75 一律拒絕，Rust 與 Python 讀檔都是），back3 在查到前文時現算（H3 平均每前文不到 2 條）。P2 是現在的 `prob_c` 系列（含詞類項；KN 照當次設定）。沒查到前文、u 未知 → P2，逐位元相同。
  - 解碼器：`Hyp` 加前一個詞；有 trigram 時重組鍵從（表面字串, 最後一詞）改成（表面字串, 前一詞, 最後一詞），光束寬度不變。起始狀態照 `harness.py` 的 `start_state`：沒有前文 → (`<s>`, `<s>`)；前文給一個詞 → (未知, 那個詞)，第一個詞只用 bigram。
  - **每個設定各自的打分設定**：把解碼與重算總分時只傳 λ 的地方換成由 `Profile` 導出的設定（λ、KN 開關、trigram 開關）。書面：KN 關、trigram 開（有表時）；聊天：照舊（KN 照側檔、trigram 關，有表也不用）。
  - **引擎的詞段交界**（`refresh_lm` 把組字區切成一段一段解；Python 參考實作只有整句，這幾條是本契約定的）：
    - (a) 每段第一個詞的歷史 (u, v)：左邊是固定的字（非標點）→ (未知, 那個字)；左邊是標點或英文段 → (`<s>`, `<s>`)，和計數時一段漢字從句首開始相同；沒有固定的字、從輸入框前文開始 → (未知, `history(前文)`)；`history` 給的是 `<s>` 時是 (`<s>`, `<s>`)，和 `harness.py` 的 `start_state` 相同。u 未知時第一個詞只用 bigram。
    - (b) 收尾項：`Eos` 用 trigram（(倒數第二詞, 最後一詞) → `</s>`，和 `harness.py` 的 `eos` 相同）；`End::Next`（右邊是固定的字）也用 trigram（(倒數第二詞, 最後一詞) → 那個字）。段內只有一個詞時，收尾項的 u 是起始狀態的 v（`harness.py` 的 `hist`）。
    - (c) `Engine::total_score` 照 (a)、(b) 逐詞算（帶兩個歷史詞），s3a 的「`total_score` 等於解碼第一名的分數」在書面有表時照舊成立；`docs/contracts/s3a.md` 記載。
    - (d) 預測列（V3）與它的重算完全不變：兩種設定都照 v0.4.6（KN 照側檔、不用 trigram）。契約與 `docs/contracts/s3a.md` 寫明。
  - **學習加分的詞條**（比照 model-v6 §2.8 規則 1）：詞條被學習加分（`boosted > lp`）時，這個詞項**不經過 trigram**，用沒有表時的 P2（加分後的 `pb`；書面本來就關 KN）。理由：trigram 開著時加分只透過 `back3·P2` 進分數，會被 back3 縮小，和 KN 吃掉加分是同一類問題。沒被加分的詞條照常用 trigram。`total_score` 套同一個例外。Python 參考實作沒有學習，不受影響。
- **CLI**：`--profile formal` 時摘要行加 `+tri:<sha8>`、不加 `+kn:`；`--no-tri` 明確關閉（研究用）。只有 `--profile formal` 且沒有 `--no-tri` 時才讀 `trigram.sjt3`：聊天與 `--predict`／`--predict-time` 不讀（表壞了也不影響）；`kn.sjkn` 不論哪個設定都照舊讀並核對（書面有表時只是計分關 KN）。Python 的 `lm_eval.py` 同規則，另外 `--tri FILE` 配 `--profile chat` 是錯誤。
- **App**：這一片**不放** `trigram.sjt3`（`build-app.sh`、CI、`release.yml` 不動），App 行為不變。
- 驗收：
  1. 沒有 `trigram.sjt3` 時逐位元組不變：現有 golden 全過、不改 golden 檔。
  2. Rust 單元測試（玩具模型＋玩具表）：分數和 Python 手算相同（保留條目、前文有但條目沒有、前文沒有、u 未知四種）；壞檔各回錯誤；突變：拿掉 back3、拿掉重組鍵的前一詞，測試要失敗。
  3. 小模型跨語言 golden（Python 產生、Rust 比對）：前 N 名與分數逐位元組相同。
  4. **真模型跨語言（決定性的一項，main 在 Windows 機器跑）**：model-v6＋classes-v5＋第 0 片重對應的 H3，`--profile formal --context`：dev302、打字測驗、錯字回報、cvtune、wikitune 的摘要行（含 `top1_sha256`）與 rowstats 和第 0 片 CAND 的 Python 輸出逐字相同；`--profile chat` 和 v0.4.6 的 CLI 逐字相同。
  5. 延遲：真模型開著表時 `perf_*` 測試照舊通過；另報每鍵 p95 與載入後 RSS（CLI 量，正式的在第 B 片）。
  6. **引擎測試**（玩具模型＋玩具表）：
     - 書面有表、組字區中間有一個固定的字、最佳路徑上有學到的詞：`total_score` 等於解碼第一名的分數；
     - 同一個情境逐條驗 (a)、(b)：固定的字之後的第一個詞只用 bigram、收尾項用 trigram（和手算相同）；
     - 學到的詞的詞項等於沒有表時的值，同一段同讀音沒被加分的詞條等於有表時的值；
     - 聊天：有表與沒有表時，解碼輸出、`total_score`、預測列逐位元組相同。
     - 書面：同一個組字區與前文，有表與沒有表時預測列逐位元組相同（詞、順序、分數；KN 照側檔開著）。
     - 突變各要讓測試以斷言失敗（不是編譯錯誤）：「`total_score` 不看 u」「聊天也開 trigram」「學到的詞照樣經過 trigram」「收尾項不用 trigram」「書面預測列關掉 KN 或用 trigram」。
- 停止條件：沒有表時任何 golden 改變；第 4 項任一行不同且原因不在本片；聊天設定任一行改變。
- 預算：executor 實作 1 次＋修正 1 次。

### 第 B 片：引擎量測與語意校正的配合（修訂三寫細）

第 A 片已經在 Mac 量過延遲與 RSS（研究紀錄 2026-10-16「trigram-core 第 A 片」：書面有表 p95 +0.01 ms、RSS +136 MB，在上限內），這一片不重做那個量測，只把它變成基準工具的固定一列。

- **B1 書面重播列**：移到第 C 片。基準工具的模型資料夾來自 `data/*.sha256` 與 `VERSIONS2`，這兩個都是第 C 片才加；在那之前放表進去，不是量到「沒有表的書面」，就是書面格的設定標記核對失敗。延遲與 RSS 在第 A 片已經用同一個重播程式（加書面參數）量過、在上限內。
- **B2 選字記憶**（main 在 Windows 機器的 WSL 跑）：
  - `core/tests/engine_learn.rs` 讀環境變數 `SHANJIE_TEST_PROFILE`：所有建立引擎的地方（`engine()` 的 `Lm::load`＋`set_lm`，以及各測試裡的 `Engine::new`＋`load_lm`）都在 LM 就位之後 `set_profile(Formal)`；同時斷言載入的 `Lm` 有 trigram 表（`lm.scoring(Profile::Formal)` 的 tri 開著），沒有表就讓測試失敗，不會在「書面沒表」下默默通過。沒設環境變數時行為不變，不改任何斷言。
  - main 在 WSL 的 clone 裡把重對應的 H3 放成 `data/lm/trigram.sjt3`（只在那台機器、只為這次，量完刪掉），以 `SHANJIE_TEST_PROFILE=formal cargo test --release -p core --test engine_learn -- --include-ignored <名稱>` 逐一跑：`a2_mirror_teach_the_other_word`（鏡像改選 ≥ 80%）、`a2_candidate_pick_learning_on_cases_tsv`（A2 ≥ 80% 且非衝突改壞 0）、`pollution_zai_taught_under_two_contexts`（ㄗㄞˋ 污染 0）、`global_eps_table`（只記數字）。門檻同 model-v6 §2.8；判讀看測試印出的數字對門檻，不只看測試過不過。
  - 手動詞另外跑：`cargo test --release -p cli --test golden manual_words_come_first_in_the_shipping_setting`（同一個 clone、表在 `data/lm`）。這個測試現在釘死「書面只錯第 8 列」，有表時錯的列換了就會失敗；判讀看書面錯幾列（由測試的輸出或對應的 rowstats 算）：**≤ 1 列**就算通過，錯的是哪一列不限（v0.4.6 書面 14／15）。若通過但錯的列變了，第 C 片打包時同一個 PR 更新這個測試釘的例外。
  - 不到門檻 → 停，問維護者（選項包含調整學習加分的例外範圍）。
  - 〔結果與放寬，2026-10-16〕鏡像 8/10、ㄗㄞˋ 污染 0 過；A2 因為書面有表時多數詞組預設就對、可計數的列只剩 2 列（兩列都學會）而沒有判讀；手動詞書面錯 2／15（「米雅歌」單獨與在句中），不到門檻。維護者選擇題「記為已知問題、繼續」：手動詞這一條放寬（依「契約不要訂得過嚴」），第 C 片打包時把 `manual_words_come_first_in_the_shipping_setting` 釘的書面例外改成第 4、8 列，發版說明寫明書面打「米雅歌」要改選一次。
  - 驗收（executor 在 Mac）：沒設環境變數時 `engine_learn` 照舊通過；設成 `formal`、`data/lm` 沒有表時，上面四個測試都在建立引擎時失敗（斷言，不是編譯錯誤）。
- **B3 語意校正的守門**（main 在 Windows 機器跑 Rust CLI）：
  - BASE：v0.4.6 的 CLI（`58da6d2`），模型 model-v6＋classes-v5＋kn-v1，`--profile formal --context --packs acg --tinylm <tinylm-v5>`（出貨的書面形狀，標準強度）。
  - CAND：本分支的 CLI，同樣的模型資料夾再加重對應的 H3，同樣旗標。摘要行要有 `+tri:fdd3b05d` 與 `+tl:d07de71d`。
  - 集合：判讀五集（dev302、打字測驗、錯字回報 114 列凍結檔、lytune、wikitypo）、調參集（cvtune、wikitune）、discordtune（只回數字）。
  - 判讀：每個集合各自 `evalstats compare BASE CAND --net`；任一集合「改壞 > 改對而且 p < 0.05」→ 不通過。只報：判讀五集串接、調參集串接各一行。
  - 不通過 → 停，寫第 B 片的修訂四：在 Rust CLI 的候選傾印上重選書面的 (α, τ, δ)（照 S7h 的流程），選值與守門用不同的集合，出新的 tinylm 版本；這個修訂要另外送審。
- **B4 `tinylm.sjtl` 的配對檢查**：`tinylm::load_paired` 現在核對模型與 `kn.sjkn` 的雜湊。書面有表時 KN 關，但 tinylm-v5 的檔頭仍記著 kn-v1 的雜湊；這一片不改配對規則（側檔照樣在、照樣核對），只在 `docs/contracts/trigram-core.md` 與 model-v6 契約的配對規則寫明「書面有表時 KN 不參與打分，但 `kn.sjkn` 仍是配對的一部分」。B3 通過，不出新的 tinylm。〔結果，2026-10-16〕B3 八個集合都沒有顯著變差（數字在研究紀錄）。
- 停止條件：B2 不到門檻；B3 不通過（轉修訂四）。
- 預算：B2 的測試開關 executor 1 次＋修正 1 次；B2、B3 的量測 main 在 Windows 機器各半小時以內。

### 第 C 片：資料 Release、打包、發版

- 資料 Release `trigram-v1`（`trigram.sjt3` 與說明檔，授權和 model-v6 相同），`data/trigram.sjt3.sha256`、`scripts/build-app.sh`、CI、`release.yml` 的下載與核對；`tools/bench.py` 的 `VERSIONS2` 加列（書面帶 `+tri:`）。
- **書面重播列**（從第 B 片移來）：`tools/bench/replay` 加第五個參數 `chat|formal`（預設 chat）；`tools/bench.py` 套件 v2 的速度表（`measure_speed2`）在聊天重播之外多一列書面重播，模型資料夾照 `VERSIONS2`（有 `trigram.sjt3` 的版本放表）；書面的「重播＝CLI」核對對一格 `--profile formal` 的 CLI，預期標記在有表時含 `+tri:`；舊的結果 JSON 沒有書面列時表上顯示「—」；套件 v1 的文字逐位元組不變（`test_table_appends_v2_after_unchanged_v1_text` 照舊通過），`test_bench.py` 加書面列的測試。
- 跨版本基準（含書面重播列）與保留集照發版流程；保留集書面那一行顯著為負就不出貨。
- 安裝檔會變大（〔推論〕trigram 表壓縮後約 40 MB，沒量），發版說明寫實際大小。

## 3. 範圍外

- 聊天設定加 trigram、更小的剪枝檔（H3 與 H15 之間）、用出貨 P2 重新剪枝：之後另開實驗。
- 重算計數、改 trigram 的平滑式。
- mmap（第 B 片量到 RSS 超過上限才另議）。

## 4. 限制與回滾

- executor：不跑 Windows 機器、不連網；不讀 `eval/holdout`、repo 外的私人資料、學習檔、`~/Library`、鑰匙圈；不安裝、不啟動 App；不 push、不開 PR、不碰其他 worktree。私人集合（discordtune）只由 main 跑、只回數字。
- 回滾：第 A 片沒有表時本來就關閉；第 C 片之後 revert 打包那個 PR 即回到 v0.4.6 的書面設定。

## 5. 審查紀錄

| 輪次 | 結果 | 處置 |
|---|---|---|
| plan-verifier（第 2 次） | REVISE 1 項：(d) 書面預測列不變沒有驗收（KN 是共用 `Lm` 上的欄位，改成依設定關 KN 時容易連預測列一起關） | FIX：引擎測試加書面預測列有表／沒有表逐位元組相同與對應突變；(a)、(b) 兩處措辭照建議補清楚。第二次 REVISE 後逐項處置完，照維護者對同類情況的決定（收尾只差規格就進實作）不再送審 |
| plan-verifier（第 1 次） | REVISE 2 項：引擎詞段交界的歷史規則沒定（u、收尾項、`total_score`、預測列的 KN）；學到的詞照樣經過 trigram 時加分會被 back3 縮小，沒有驗收量書面有表時的選字記憶 | 兩項都 FIX：第 A 片加 (a)–(d) 與學到的詞不經過 trigram 的例外、引擎測試與四個突變；第 B 片加書面有表的 `engine_learn` 門檻與停止條件 |
