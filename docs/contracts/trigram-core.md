# 契約：trigram 進核心，只用在書面設定（trigram-core）

狀態：修訂二（2026-10-16；修訂一處理 plan-verifier 第一次 REVISE 的兩項，修訂二處理第二次的一項，見 §5）。

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

### 第 B 片：引擎量測與語意校正的配合

- 書面重播：基準工具的速度表現在只重播聊天（`docs/benchmark.md` 的速度一節），加一列書面重播；量每鍵 p95、最大、載入時間、RSS。超過上限（RSS +150 MB 或 p95 +1 ms）→ 先試已知的修法（例如依 u 分段的索引縮小二分搜尋），還超過就停，回報維護者。
- **選字記憶**（在第 C 片之前，main 在 Windows 機器的 WSL 跑）：`engine_learn` 用書面設定、模型旁有重對應的 `trigram.sjt3`：鏡像改選 ≥ 80%、A2 ≥ 80% 且非衝突改壞 0、ㄗㄞˋ 污染 0（門檻同 model-v6 §2.8）；`manual_words_come_first_in_the_shipping_setting` 書面不比 v0.4.6 少。不到門檻 → 停，問維護者（選項包含調整學習加分的例外範圍）。
- 語意校正（tinylm-v5）：書面的 (α, τ, δ) 是在 bigram＋KN 的分數上選的；trigram 讓 n-gram 分數差距變大。驗收：書面、開 tinylm-v5、標準強度，新對舊在判讀五集、調參集、discordtune 每集都不顯著為負（守門）。不過 → 在 Rust CLI 的候選傾印上重選書面的三個值（照 S7h 的流程），出 tinylm 的新版本；選值與守門用不同的集合，另寫在這一片的修訂。
- `tinylm.sjtl` 的配對檢查（現在核對模型與 `kn.sjkn` 雜湊）要知道書面不用 KN；同一個 PR 改。

### 第 C 片：資料 Release、打包、發版

- 資料 Release `trigram-v1`（`trigram.sjt3` 與說明檔，授權和 model-v6 相同），`data/trigram.sjt3.sha256`、`scripts/build-app.sh`、CI、`release.yml` 的下載與核對；`tools/bench.py` 的 `VERSIONS2` 加列（書面帶 `+tri:`）。
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
