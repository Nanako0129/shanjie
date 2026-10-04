# S2r-2 契約：修正疊加層的讀音，並補上標準讀音變體

S2r（`docs/contracts/s2r-sandhi-variants.md`，PR #13）只處理小麥基底。疊加層 `data/lexicon/overlay-add.tsv`（維基標題，34 萬列，CC BY-SA 4.0）的讀音，是 `tools/build_overlay.py` 用 `ime.Lexicon.to_syllables` 推出來的：把詞切成基底詞，每段取那個詞分數最高的讀音。單字段遇到破音字時只能猜分數最高的讀音，因此有系統性的錯誤。使用者 2026-10-04 核准三件平行；這片照 S2r 的規則 AUTO 進行。

## 0. 現況（main 2026-10-04 在 main `c6abd58` 上量得）

| 問題 | 列數 |
|---|---|
| 「不」讀 ㄅㄨˊ 但下一字不是去聲（含詞尾） | 813 |
| 「一」讀變調，但在排除範圍內或變調方向錯 | 2 |
| 「法」讀 ㄈㄚˋ（審訂表沒有這個音） | 27 |
| 「和」讀 ㄏㄢˋ（連詞音；疊加層是詞條標題，多半是人名地名，應讀 ㄏㄜˊ） | 576 |
| 疊加層完全沒有「一」「不」的另一種標準讀音 | 一／不共 5,336 字次 |
| 讀音靠「單字破音字取最高分」猜出來的列（不含一、不） | 124,579（36.3%）；最多的是 兒、子、仔、大、縣、頭、水、三 |

最後一項大多是本調和輕聲的差別（子、兒、頭），猜的通常就是常用讀音。照 zaoseq 那樣「猜的就不收」會丟掉三分之一的疊加層，所以這片**只量測、分類、提出建議，不改資料**。

## 1. 產生規則（只改 `tools/build_overlay.py` 的讀音後處理；`ime.to_syllables` 不動）

`to_syllables` 給出讀音之後，對每一列：

1. **正規化**：
   - 「一」的變調改回 ㄧ；「不」的 ㄅㄨˊ 改回 ㄅㄨˋ；
   - 「法」的 ㄈㄚˋ 改成 ㄈㄚˇ；
   - 「和」的 ㄏㄢˋ：前一字是「唱、倡、附、應、酬、賡」時改成 ㄏㄜˋ（唱和、附和，審訂表「和」的去聲），其他改成 ㄏㄜˊ。例：「一唱百和」「一倡一和」讀 ㄏㄜˋ，「上和下睦」讀 ㄏㄜˊ。
   - 正規化後的讀音是這個詞的**主要列**。
2. **變調列**：主要列裡每個「一」「不」照 `tools/build_sandhi.py` 的 `other_reading` 換成變調（含 S2r 的全部排除條件）；有任何位置換了，就多輸出一列。和 S2r 相同，一個詞最多兩列：全本調、全變調。
3. **分數與來源**（round 3 修訂）：主要列沿用原本的分數；變調列的分數＝主要列 − `VARIANT_PENALTY`（0.5，log10，約 1/3；`tools/build_overlay.py` 的具名常數）。原因：變調列常和別的詞共用讀音（同一成語的異體寫法、罕見詞），原本同分時由檔案順序決定誰排第一；降分後「已經有這個讀音的詞」贏，變調列只在沒有競爭者時才排第一。來源欄沿用 `wikt`／`zhwiki`。主要列在前。
   - **使用者決定（2026-10-05，覆蓋 S2r 對專名的保守政策）**：疊加層的「一」變調列全部保留，包括 main 抽查指出的人名、序詞、品牌（胡一山、一等伯、一匙靈…）；它們實務上無害。曾實測的「基底要有 一＋下一字 的變調證據才輸出」不能分出這些詞（基底本身有 一等、一山、一聯、一匙），沒有採用。
   - 「和」補一條：後面接 麵、麪、泥 時讀 ㄏㄨㄛˊ（審訂表 和麵、和泥）；「和」前面任何位置有 唱、倡 也讀 ㄏㄜˋ（契約的例子「一唱百和」「一倡一和」）。
4. **排序**：照現行（詞的 code point）；同一個詞的兩列相鄰，主要列在前。

| 項目 | 預估（§5 的停止條件用這些數字） |
|---|---|
| 主要讀音改變的列 | 2,195 |
| 新增的變調列 | 2,933 |
| 疊加層總列數 | 342,761 → 約 345,694 |

**授權**：變調列是疊加層的衍生，所以留在 `overlay-add.tsv`（CC BY-SA 4.0），不放進 MIT 的 `sandhi-add.tsv`。

**核心不用改**：
- `CappedLexicon` 用詞（第 2 欄）判斷封頂，變調列是同一個詞，照樣封頂。
- `Lexicon::parse_with` 允許同一個詞有多個讀音（`sandhi-add.tsv` 已經這樣用）。
- 實作時要用測試確認，不是只靠推論（§4 第 6 項）。

## 2. 小檢查（verifier 在 S2r 的建議）

`tools/build_sandhi.py --check` 先跑一個自我檢查，再比對檔案：

- `other_reading("真不一般", ["ㄓㄣ","ㄅㄨˋ","ㄧˋ","ㄅㄢ"], 1)` 必須是 `None`；
- `other_reading("一不做二不休", …)` 的第一個「一」必須是 ㄧˊ；
- 「不一會」的「不」不得變成 ㄅㄨˊ。

把 `next_tone` 的「一 → 1」拿掉時，這個檢查必須失敗（突變測試）。

## 3. 破音字審核（只量測與報告）

新增 `experiments/s2/overlay_polyphones.py`，把 §0 最後一項分類：

- 只差輕聲／本調；
- 聲調不同；
- 聲母或韻母不同。

每類報列數與最多的 20 個字。

- 「聲母或韻母不同」這類，抽 50 列（固定種子），由 main 人工判讀猜錯幾列。
- 結論寫進 `docs/research-log.md`：建議下一片處理哪些字、用什麼做法（例如只對高風險字輸出多個讀音，或降分）。
- 這片**不實作**這些建議。

## 4. 驗收

1. **重產與檢查**：
   - `python3 tools/build_overlay.py` 重產，`--check` 逐位元組一致（來源檔在 `~/.cache/shanjie/sources/`，SHA-256 不變）；
   - `python3 tools/build_sandhi.py --check` 通過，`sandhi-add.tsv` 不受影響：它只讀疊加層來去重，若因此改變要說明。
2. **列數**：每項變動的列數和 §1 的預估差距在 10% 以內。
3. **人工抽查**（main 做，依 S2r §0 的教育部規則）：
   - 50 列改變或新增的列：超過 2 列不合規則就修生成器，不縮小範圍；
   - 另抽 50 列「和」：如果超過 2 列其實是連詞用法（例：歌名「我和你」），就為連詞加例外規則。
4. **golden**：
   - `s1-dev302.txt`、`s1-overlay-sets.txt`、`s2-lm.txt`、`s2-lm-dev302-top1.tsv`、`s2r-probe-top1.tsv`、`s2r-probe-unigram.txt` 照原本的產生者重產；
   - 重產前寫下預期會變的列，重產後列出實際變動的列（公開集合可以列句子）；
   - `reference/proto/check_unigram_overlay.py` 一致；`unigram.txt`、`s1-dev302-nooverlay.txt` 逐位元組不變。
5. **第 0 步與正式路徑的實際斷言**：
   - 在 `core/tests/engine_lm.rs` 加至少三個字面斷言，走 `Engine::new` 加 `load_lm`，每個都先確認在 main 上會錯：
     - 一個疊加層詞，「不」在非去聲前，用 ㄅㄨˋ 打（候選：下不爲例、三不沾）；
     - 一個含「和」的疊加層人名或地名，用 ㄏㄜˊ 打；
     - 一個疊加層的「一」或「不」詞，用新的變調讀音打（候選：一丈紅 ㄧˊ ㄓㄤˋ ㄏㄨㄥˊ）。
   - 這些斷言在本分支通過；把 `data/lexicon/overlay-add.tsv` 換回 main 的版本時必須失敗（以斷言失敗，不是編譯錯誤）。
6. **「核心不用改」的測試**（新增到 `core/tests/`，Python 端用 assert）：用一個真的有變調列的疊加層詞，驗證三點：
   - (a) `Lexicon::parse_with` 載入真的 `overlay-add.tsv` 加 `sandhi-add.tsv` 成功，`word_info` 回傳主要列（本調）的讀音；
   - (b) 變調列的分數是主要列 − 0.5，`CappedLexicon::best_lp` 對變調讀音不高於主要讀音、差距不超過 0.5（兩者都被封頂時相同），而且低於原始分數；
   - (c) Python `ime.Lexicon.by_word` 給出同一個主要讀音。
   - 把變調列排到主要列前面，或只給 `CappedLexicon` 主要列時，這些測試必須以斷言失敗。
7. **評測**（`experiments/s2/s2r_eval.py`，A = main、B = 本分支）：
   - **採用規則（方法論 §3.4 的例外，研究紀錄要寫明）**：這片是依教育部規則修正錯誤讀音，不是調參數。所以採用條件是「抽查符合規則」（第 3 項）加上「gate 集合不顯著退步」，而不是在調整集上顯著進步。
   - 原讀音、全部列：cvtune、wikitune、discordtune 不得顯著退步；dev302、typing76 報數字。
   - 探針：cvtune 與 discordtune 不得顯著退步，預期會進步（疊加層詞也能打變調了）。
   - 另外量「含疊加層詞『和』」的列，只報數字。
8. **正式路徑**：`cargo test --release` 與 `swift test` 全綠；`engine_lm` 的重播 golden 照新的 Python 期望檔通過。
9. **效能**：每鍵 p95 < 16 ms；回報峰值 RSS 的變化。
10. **保留集**：片結束時由 fresh verifier 量一次，不退步；main 不讀。

## 5. 負責人、範圍、停止條件

- **負責人**：
  - `pilotfish:executor` 在 worktree `/Users/nanako/side-project/shanjie-s2r2`（分支 `feat/s2r2-overlay-readings`）實作與重產；
  - main 做人工抽查、整合、`/code-review`、fresh verifier、PR 與合併。
- **檔案範圍**：
  - `tools/build_overlay.py`、`tools/build_sandhi.py`（只加自我檢查）、`data/lexicon/overlay-add.tsv`、`experiments/s2/overlay_polyphones.py`；
  - `eval/golden/` 與 `eval/probe/`（重產）；
  - `core/tests/`（第 5、6 項的新測試，以及 golden 期望值的更新）、`cli/tests/`（只有 golden 期望值需要更新時）、`reference/proto/` 或 `experiments/s2/` 下的 Python 檢查（第 6 項 (c)）；
  - `docs/research-log.md`、`docs/PLAN.md`、`LICENSES/data.md`（疊加層的說明加一句變調列）。
- **預算**：實作加審查最多三輪；抽查後修生成器、為「和」加例外規則，都算一輪。
- **停止條件**：
  - 超過預算；
  - 列數和預估差超過 10%；
  - 任一 gate 集合顯著退步；
  - 抽查不合規則超過 2 列；
  - 跨語言檢查不一致；
  - 需要改 `core/src` 或 `macos/` 才能載入（表示 §1 的「核心不用改」不成立）。
- **executor 不可以做的事**：
  - 不得 push、開 PR；
  - 不得讀 `eval/holdout/`；不得把 discordtune 的內容印到終端機或寫進 repo；
  - 不得改契約範圍以外的檔案；
  - 不得安裝軟體、開 GUI App、碰鑰匙圈、執行 `.app` 內的二進位，或跑 `scripts/install-ime.sh`；
  - 不得自己跑超過 10 分鐘的評測，回報指令給 main。
- **回滾**：revert 這片的合併 commit。
