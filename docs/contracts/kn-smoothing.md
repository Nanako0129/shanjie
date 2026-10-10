# 契約：Kneser–Ney 回退分布（離線量測）

狀態：修訂一 READY（2026-10-10，plan-verifier 第 2 次審查，§6）；正式量測完成（2026-10-10，§7）。`docs/PLAN.md`「貝氏 n-gram 平滑（Pitman–Yor）」一項的第一片；issue #44 討論串的機率方向之一。這一片只做離線量測，不改核心、不改模型檔、不出貨。

## 0. 現況（讀程式確認過）

- 執行時的機率（`core/src/lm.rs` `prob_c`、`reference/proto/lm.py` `prob`，兩邊逐位元相同）有三個分支，都用到回退分布 `pb`：
  1. **有保留條目**：`(c(v,w) − D) / t(v) + back(v) · pb`；
  2. **詞類項**（v、w 都有詞類、沒有保留條目）：`back · ((1−μ)·pb + μ·Pc·emit)`；
  3. **一般回退**：`back · pb`。
  - `D = 0.75`（absolute discounting），剪枝 `PRUNE = 2`（`tools/build_lm.py`）。
- **`pb` 是詞庫分數 `10^lp`，不是語料的 unigram**（`lm.py` `word`）。疊加層與詞包的詞先過 `cap_overlay`：語料見過的取 `min(lp, log10(c/N))`，沒見過的扣 `UNSEEN_OVERLAY_PENALTY`；基底詞庫的詞直接用小麥的分數。同一個 `lp` 又以 `(1−λ)·lp` 進總分。句尾的 `pb` 是 `p_eos`。
- 計數進模型前（`build_lm.py` `build`）：維基 ×1、口語 ×5 的期望次數加總後**四捨五入成整數、去掉 0**，再做異體合併（`merge_variants`：後一個詞只放在類的代表成員上，前文寫給類裡每個成員；`N`、`eos_total` 每類只算一次，`once`）。所以進到這一步的每個詞對至少 1 次（加權期望次數 ≥ 0.5）。
- Kneser–Ney 的想法：回退時不看「這個詞出現幾次」，看「它接在幾種不同的詞後面」（接續次數）。只出現在固定搭配裡的字（例如只接在某個姓後面的名字用字），回退機率會變小。這對準「常用單字組合贏過整詞」這一類錯（A2 根因：輸掉的詞包詞 88.7% 輸給單字組合，研究紀錄 2026-10-10）。

## 1. 要做到什麼、怎麼看到

- **要回答的問題**：把模型詞彙裡的詞的 `pb` 換成含 Kneser–Ney 接續分布的版本，選字有沒有變好？
- **怎麼看到**：同一個模型檔、同一份詞類表，只換 `pb`（§2.4 寫明每一次用哪個檔）。設定只用 cvtune、wikitune 選；dev302、打字測驗、錯字回報不參與選，但參與否決（§2.3）。照 repo `CLAUDE.md` 的同一張表報。保留集只在收尾由 fresh verifier 跑一次。
- **結論的用途**：有效，就另寫一片做進核心（模型格式、golden、模型版本）；沒效，就把負面結果寫進研究紀錄，Pitman–Yor 不再往下做。

## 2. 做法

### 2.1 接續次數表（側檔，不動模型檔）

- 新工具 `tools/kn_cont.py --lm MODEL --theta θ --out FILE`（`S2_WORK` 指定計數目錄，和 `build_lm.py` 相同）：
  1. 用 `build_lm.py` 同一套程式（import，不複製）讀 `CORPORA`、加權、四捨五入、去掉 0、`variant_classes`、`merge_variants`，得到整數的 `bi`。
  2. 對每個詞 w（合併之後，後一個詞只會是代表成員或不在任何類裡的詞）算 `N(w) = |{ v : once(v), v ≠ "<s>", w ≠ "</s>", bi[(v, w)] ≥ θ }|`。`once(v)` 讓一個 k 個成員的類只算一種前文。
  3. 類的非代表成員取代表成員的 `N`（和 unigram 寫回每個成員的做法一致）。
  4. 讀 `MODEL` 的詞彙，依字串對到每個詞彙 id；模型詞彙裡有、計數裡沒有的詞（不問原因），`N = 0`。
  5. 寫側檔：magic `SJKN0001`、V（u32）、`MODEL` 檔的 SHA-256（32 bytes）、θ（u32）、`N(w)`（u32[V]，id 0、1 寫 0）。
- **θ 格點 {1, 2, 3}**：計數在這一步已是整數（§0），θ = 1 是所有留下的詞對（加權期望次數 ≥ 0.5），θ = 2 和模型的剪枝門檻相同，θ = 3 再排除只靠零星出現的前文。
- 分數次數上的 Kneser–Ney 有現成做法（Zhang & Chiang 2014，ACL，https://aclanthology.org/P14-1072/ ），但它作用在每個詞對的次數分布上；我們的計數檔只存期望值的加總，做不到，所以用「四捨五入後的整數＋門檻」近似，報告寫明。
- 報告每個 θ：`N = 0` 的詞占模型詞彙的比例、ΣN′（下一節）。

### 2.2 回退分布

- `reference/proto/lm.py` 的 `BigramLM` 加可選的側檔（`kn=FILE, kn_beta=β`）；載入時核對側檔的 V 與模型雜湊，不符就丟 `ValueError`。`lm_eval.py` 加 `--kn FILE --kn-beta β`。
- 模型詞彙裡的詞（id ≥ 2）：
  - `N′(w) = N(w) + 1`（加一，讓 `N = 0` 的詞不會是 0）；
  - `ΣN′ = Σ_{w: id ≥ 2, once(w)} N′(w)`，每類只算一次（類的資訊由 `kn_cont.py` 一併寫進側檔，或由 `lm.py` 依同一套 `variant_classes` 算，實作時二選一並寫進測試）；
  - `pb = β · N′(w)/ΣN′ + (1−β) · 10^lp`。
- 不在模型詞彙裡的詞（沒見過的疊加詞、詞包詞）與句尾：`pb` 照舊（`10^lp`、`p_eos`）。
- **三個分支（§0）全部用新的 `pb`**：有保留條目、詞類項、一般回退。`D`、`back(v)`、`μ`、`Pc`、`emit`、`(1−λ)·lp`、`PRUNE` 都不動。
- 因為 `N′ ≥ 1`，`pb > 0`，不會有 `log10(0)`；同一個 `lp` 下，`N` 越大 `pb` 越大（β > 0）。
- **單元測試**（玩具模型，手算）：
  - β = 0 時每個分數和不加側檔逐位元相同（真實模型 dev302 也比一次 `--dump`）；
  - β = 1 時三個分支各一個手算值；
  - 一個兩個成員的類：前文只算一次、兩個成員的 `N` 相同、ΣN′ 等於手算值；
  - 同一個 `lp`：`pb(N=0) ≤ pb(N=1)`，格點裡每個 β 都檢查；
  - 側檔雜湊或 V 不符就報錯；
  - `kn_cont.py` 每個 θ 的側檔雜湊兩兩不同（真實計數上，§3 第 1 項）。

### 2.3 設定與選法（事先寫死）

- 格點：θ ∈ {1, 2, 3} × β ∈ {0.5, 1}，共 6 組；基準＝同一個模型、不加側檔。
- **每一組與基準都跑全部五個集合、兩種設定**：cvtune、wikitune、dev302、打字測驗、錯字回報 × 聊天、書面。
- 指令（固定，每一次都一樣，只換 `--rows`／`--dev`、`--profile` 與側檔）：

  ```
  python3 reference/proto/lm_eval.py --lm MODEL --profile chat|formal --rows SET --context --rowstats OUT [--kn SIDE --kn-beta β]
  ```

  - 詞類表照預設（`MODEL` 同目錄的 `classes.sjc`），降權照預設；dev302 用 `--dev 302` 取代 `--rows`。
  - 集合檔：cvtune＝188 `work/s2f4/tune/cvtune.txt`（SHA-256 `31de456d…`）、wikitune＝188 `work/s2f4/tune/wikitune.txt`（`8dcfe40c…`，和 S2k 相同）；打字測驗＝`eval/dev/user-typing.txt`；錯字回報＝評測當時 main 上的 `eval/dev/user-reported.txt`。
  - 比較：`python3 tools/evalstats.py compare BASE CAND --label 名稱`。
- **統計量**：cvtune 與 wikitune、聊天與書面四格的 top1 合計。
- **否決**：五個集合 × 兩種設定任何一格「配對檢定 p < 0.05 而且淨值為負」。
- **候選**：沒被否決的設定裡統計量最高的；統計量不高於基準（差 ≤ 0）就是負面結果。
- **同分**：先取 θ 較大的，再取 β 較小的（字典序，離現狀較近）。

### 2.4 用哪個模型檔、哪份計數

| 一次 | 模型檔（`--lm`，側檔綁它的雜湊） | 詞類表 | 計數（`kn_cont.py` 的 `S2_WORK`） | `UNSEEN_OVERLAY_PENALTY` |
|---|---|---|---|---|
| 前導（可選，只記錄、不用來選） | 現在出貨的 model-v4（`data/lm/bigram.sjlm`，`data/bigram.sjlm.sha256` 釘的那個） | classes-v2（`data/lm/classes.sjc`，`data/classes.sjc.sha256` 釘的那個） | 188 `work/s2f4`（model-v4 的模型是同一批計數建的） | 1.0（main 現值） |
| 正式（選法只用這一次） | model-v5（合併後 main 釘的那個） | classes-v3 | 188 `work/modelv5` | model-v5 選定的值（合併後的 `lm.py`） |

- **前提**：正式那次只在 model-v5 與 classes-v3 合併進 main、這個分支 rebase 到它之後才跑；沒有就只做前導，正式那次等待，不改用別的檔。
- 側檔一律對表中那個出貨的模型檔算；模型檔裡有、計數裡沒有的詞照 §2.1 第 4 步給 `N = 0`。
- 計數與側檔在 192.168.123.188 上算；評測也在 188 跑（綁 P-core、絕對路徑，照 `experiments/s2f/README.md` 的批次慣例），同一組比較用同一台。

## 3. 驗收

1. §2.2 的單元測試全過；真實計數上三個 θ 的側檔雜湊兩兩不同，報每個 θ 的 `N = 0` 比例與 ΣN′。
2. §2.3 的表：6 組 × 五個集合 × 兩種設定，每一格一行（n、top1 基準／新、改對／改壞、McNemar p、CER、兩個 bootstrap 區間），加上統計量與否決結果，以及依選法選出的設定（或負面結果）。
3. 固定探針（只報第一名，不當選法）：宿儺、零股（#74）、摩擦聲、月繳、規費要繳。
4. **收尾**：fresh verifier 照 repo `CLAUDE.md` 的保留集規則（寫到它自己的暫存目錄、檔內只能有 `[0-9\t\n]`、比完刪掉），基準與選出的設定各跑一次，只回那一行表格：

   ```
   python3 reference/proto/lm_eval.py --lm MODEL --profile chat|formal --rows eval/holdout/holdout.txt --context --rowstats OUT [--kn SIDE --kn-beta β]
   python3 tools/evalstats.py compare BASE CAND --label holdout-chat|holdout-formal
   ```

   負面結果時也跑一次（拿最高統計量的那組），只記錄。
5. 研究紀錄寫結果與「整數＋門檻」近似的限制；PLAN 的「貝氏 n-gram 平滑」一項更新狀態。

## 4. 停止條件、預算、限制

- **停止條件**：β = 0 和不加側檔不是逐位元相同；側檔和模型對不上；三個 θ 的側檔有兩個相同；188 一批超過 2 小時。
- **預算**：`kn_cont.py`、`lm.py`、`lm_eval.py` 的改動與測試交 executor 1 次＋修正 1 次；188 的計算與評測 main 跑。
- **限制**：`D` 不重新估（期望次數上的 count-of-counts 一樣要次數分布）；只換回退分布，不是完整的 modified Kneser–Ney。executor 不連網、不讀 `eval/holdout/`、`~/side-project/shanjie-private/`、學習檔與聊天紀錄，不 push、不開 PR、不碰其他 worktree。

## 5. 範圍外

- 改核心、模型格式、golden、Release（有效時另一片）。
- Pitman–Yor 的取樣（Teh 2006；Kneser–Ney 是它的近似，這片沒效就不做）。
- 重估 `D`、三個折扣的 modified Kneser–Ney、trigram。

## 6. 審查紀錄

| 審查 | 結論 | 處置 |
|---|---|---|
| plan-verifier 第 1 次 REVISE（2026-10-10） | 1. 側檔綁「同一次建出的模型」，但出貨的模型加過指紋、詞類表綁它的雜湊，兩者對不上；正式那次沒有以 model-v5 合併為前提；2. 計數在 `build` 已四捨五入成整數，θ 0.5 與 1 重複，理由寫錯；同分規則有交叉同分；3. 異體類的前文算了 k 次，非代表成員 `N` 永遠是 0，ΣN 也被扭曲；4. `N = 0` 的詞走舊分支，β = 1 時排序反過來，改成照公式會 `log10(0)`；5. 沒寫三個分支哪些換 `pb`；6. 否決用到「只記錄」的集合、只對選出的設定跑；評測指令與旗標沒寫死；保留集指令用 Rust CLI 載不到側檔 | 全部 FIX：1 → §2.1 側檔依字串對到出貨模型的詞彙、§2.4 表格逐次寫明模型、詞類表、計數、扣分，正式那次以 model-v5 合併並 rebase 為前提；2 → θ ∈ {1, 2, 3}、§0 與 §2.1 改正理由、同分改字典序；3 → §2.1 `once(v)`、非代表成員取代表的 `N`、ΣN′ 每類一次、兩成員類的測試；4 → 加一的 `N′`，不再有分支，單調性測試；5 → §2.2 三個分支全換、各一個手算值；6 → §1 改寫、§2.3 每組跑全部集合、固定指令、§3 第 4 項保留集用 `lm_eval.py` |
| plan-verifier 第 2 次（2026-10-10，修訂一） | READY | — |

## 7. 結果（2026-10-10）

- 選出 θ=1、β=1：調參集四格合計 +33 列（約 6,600 句，兩種設定合計 13,236 列），六組都沒有被否決，唯一顯著的一格是 wikitune 書面 +18（p = 0.025）；保留集聊天 180→182、書面 184→183，都不顯著；固定探針五個都沒變。完整表格與數字在研究紀錄 2026-10-10「Kneser–Ney 回退分布的離線量測」。
- **使用者 2026-10-10 決定**：併進下一次模型重建（model-v6，A3 的新名字與常見專有名詞要重算計數時）一起做進核心，只重產一次 golden；這片的量測 PR 先合併。另外兩個選項是「現在另開一片做進核心」「不做，記為幾乎無效」。
