# S2k：詞類回退（非監督詞性）進 v0.3.0

使用者 2026-10-08 實機回報兩類錯誤：

- 整句結構不通，例如改選「做」之後變成「…在做的事什麼事情」；
- 罕見組合退回單字頻率，例如「詞態」打成「磁態」、「對齊」打成「對其」。

使用者問「是不是少了句構分析和詞態排列的分析」，並決定把詞類模型放進 v0.3.0。

現有模型只看相鄰兩個詞（bigram）。兩個詞的組合沒在語料出現時，分數退回單字頻率。

2026-10-05 的實驗（分支 `exp/word-classes`，在 model-v1 的計數上）做過一個改法：
- 從語料自動分出詞類（Brown 式 exchange 分群，K = 512）；
- 在回退時，把一部分機率改用「詞類 bigram × 詞在類裡的機率」。

結果：dev302 聊天 +12（15／3，p = 0.0075）、書面 +17（22／5，p = 0.0015），未調參。細節在 `experiments/s2-classes/README.md`。

這份契約分兩段：

- **第一段**：在 model-v3 的計數上重跑，事先寫死判定規則。
- **第二段**：判定過了才另寫契約，把它做進 Rust 核心。

## 1. 第一段：在 model-v3 上重跑（研究，不動產品）

### 1.1 資料與分群

- **計數**：model-v3 的加權計數，188 的 `work/s2f4`，讀之前核對 SHA-256：
  - `counts-200000.pkl` ×1，`ca2f1361a8b9a8fba403785b7a85dd259fb5ba14220d885c93b1bcd84edaa3cf`；
  - `counts-colloquial3.pkl` ×5，`13d0ab5bf8a89c715254234e28beffe287726960245787ab0fe2d3fcfbef97ef`。
- **分群**：`experiments/s2-classes/cluster.py`，參數 N = 40,000、K = 512、12 輪，在 188 上跑。
  - 輸出 `edges.npz` 與 `cls-40000-512.npz`，複製回 Mac 時核對雜湊。
  - 用環境變數 `S2K_COUNTS`、`S2K_OUT` 指定目錄。
- **語言模型**：`classlm.py` 讀 model-v3（`data/lm/bigram.sjlm`，SHA-256 `5c7d5a94…5a48`）。
  - 類別項只在回退分支混入，方法和 2026-10-05 的實驗相同。
  - 有保留條目的 bigram、詞庫分數、λ、beam、疊加層都不變。
- **一律用前文解碼**：`classlm.py` 的 `rows_of` 保留每列的前文，`run` 用 `start = L.history(L.context_key(前文), lm)` 解碼，和 `lm_eval.py --context` 相同。調參（§1.2）、評測（§1.3）、探針（§1.4.3）、保留集（§1.4.4，入口是 `aggregate.py`，開前文）都走這一條路。
- **核對**：μ = 0 時，這條路在 dev302 聊天與書面的第一名數，要等於 `lm_eval.py --lm <model-v3> --context` 的結果（235／240）；不相等是停止條件。

### 1.2 調參（只用調參集）

- **調參集**：
  - cvtune-tune：model-v3 重建時產生的 cvtune（188 的 `work/s2f4/tune/cvtune.txt`，SHA-256 `31de456d66ece98eb5d00154a31c3f7e9c9b38240690f7f76203485ca82acc51`），**扣掉和 cvtune-native 參考句相同的列**，避免調參和否決用到同一批句子。列數在執行時算出，寫進 README。
  - wikitune：同一次重建的那份（`work/s2f4/tune/wikitune.txt`，SHA-256 `8dcfe40c74cce73e0a070b49e166636580ef955da8555ea9ffbd65af20c998f2`，2,827 列）。
- **候選**：μ ∈ {0.8, 0.9, 0.95, 0.98}，聊天與書面兩種設定，對 model-v3 配對比較。
- **選擇規則**（事先寫死）：
  - 四格（cvtune-tune、wikitune × 聊天、書面）淨修好的總和最大；
  - 同分取弄壞總數較少的；
  - 再同分取較小的 μ。

### 1.3 評測（不調參，對 model-v3）

- **集合**：dev302、typing76、錯字回報（評測當時 main 上的全部列）、cvtune-native（`~/.cache/shanjie/work/s2f/cvtune-native.txt`，2,117 列，SHA-256 `8300f2a0e827865d7749ab803255e81e516d02a5d81698be3b58686175c52509`）。
- **條件**：聊天與書面，`--context`，McNemar 精確檢定。

### 1.4 判定規則（事先寫死）

全部成立才進第二段，任一不成立就停下來，把數字交給使用者：

1. **守門不否決**：dev302、typing76、cvtune-native 沒有任何一格淨值為負而且 p < 0.05。
2. **dev302 兩種設定淨值都 ≥ 0**。
3. **既有的檢查都過**：用 ClassLM、選定的 μ、前文，聊天與書面都要。第一名要和期望字串**逐字相同**，不用寬鬆比對（寬鬆比對會把異體字當成相同）。
   - `experiments/s2f/probe.txt`（16 列，第二欄是期望字串）、`probe-tie.txt`（世界線）、`probe-zao.txt`（竈門）；
   - `|大概十分鐘後到|ㄉㄚˋ ㄍㄞˋ ㄕˊ ㄈㄣ ㄓㄨㄥ ㄏㄡˋ ㄉㄠˋ`；
   - `好|吧|ㄅㄚ˙`。

   README 附逐列的表（期望字串、ClassLM 第一名、兩種設定）。用同樣的檢查跑 μ = 0，要重現 S2f 的 16/16、世界線、竈門。
4. **保留集**：由 fresh verifier 跑一次，只回數字；兩種設定都不能顯著變差（淨值為負而且 p < 0.05 就不過）。

### 1.5 記錄

- `experiments/s2-classes/README.md` 加一節「model-v3 重跑」，寫：
  - 分群的目標曲線與時間；
  - 調參表；
  - 評測表；
  - 判定結果；
  - 改變的列的例子。
- 研究紀錄寫一則。

## 2. 第二段（判定通過後另寫契約）

先列出要決定的事，第二段契約時再定：

- **檔案**：類別表放在哪裡。
  - 選項一：單獨的檔案（例如 `data/lm/classes.sjc`，含 K、類別陣列、類別 bigram 表、每類 unigram 總和，約 1.7 MB），和模型一起放進 Release；
  - 選項二：併進新的模型格式。
  - 原則：要有雜湊核對，載入失敗回傳碼 3。
- **對齊**：Python 參考實作與 Rust 逐位元相同；golden 由 Python 產生。
- **設定**：μ 是否依 profile 不同。
- **效能**：回退分支多幾次查表，需要量每鍵延遲；記憶體約多 2 MB。
- **出貨**：GitHub Release 的資產（使用者當下同意）、CI 下載、`.sha256`。

## 3. 停止條件、預算、限制

- **停止條件**：
  - §1.4 任一項不成立；
  - 分群在 188 超過 3 小時，或記憶體超過 48 GB；
  - 任何輸入檔的雜湊或列數不符；
  - μ = 0 的核對和 `lm_eval.py --context` 不相等；
  - 需要改模型檔格式。這只限第一段：第一段不改產品。
- **預算**：第一段 main 執行。188 分群一次；Mac 調參與評測一次。
- **限制**：
  - 不讀 `eval/holdout/`（只有收尾的 verifier 跑）；
  - 不讀私有資料；
  - 第一段不改 `core/`、`macos/`、`data/`。

## 4. 第二段：做進產品（2026-10-08，第一段判定通過後）

第一段通過（`experiments/s2-classes/README.md`「model-v3 重跑」）。使用者決定類別表做成單獨檔案，並放上新的 Release 資產。

### 4.1 類別檔 `classes.sjc`

- **產生**：`tools/build_classes.py` 讀三樣東西，寫 `data/lm/classes.sjc`（不進 git，和模型一樣從 Release 下載）：
  - 第一段的 `edges.npz` 與 `cls-40000-512.npz`（雜湊同第一段 README）；
  - 模型 `data/lm/bigram.sjlm`。
- **內容**（小端序）：
  - 魔數 `SJCL0001`；
  - 模型的 SHA-256（32 bytes）；
  - K（u32）、μ（f64，0.8）、模型詞彙數 V（u32）；
  - 每個模型詞 id 0..V−1 的類別（u16，`0xFFFF` 表示沒有類別）與發射機率（f64）。
    - id 0 是 `<s>`：類別 K+1、發射 1.0；id 1 是 `</s>`：類別 K+2、發射 1.0（和 `classlm.py` 寫死的相同）。
    - 其他詞：類別查 `edges.npz` 詞彙裡同一個字串；發射 = `edges.npz` 的 `uni[i]` ／ `cu[c]`，`cu` 是對 `edges.npz` 全部詞彙依類別加總，算法和 `classlm.py` 第 44 行相同。
  - 類別 bigram 表 P(d|c)（(K+3)² 個 f64，平滑 ε = 0.1，和 `classlm.py` 相同）。
- **對應的完整性**：`classlm.py` 依字串查類別，產品（Python `lm.py` 與 Rust）依模型詞 id 查。`build_classes.py` 記錄有類別但沒有模型 id 的詞數，以及其中詞庫產得出的數目，不斷言為 0。
  - **更正（2026-10-08，實作時量到，使用者同意）**：原本寫「這些詞詞庫產不出」是錯的。`edges.npz` 有 341,333 詞、模型 332,169 詞；有類別而沒有模型 id 的 9,187 詞全部詞庫產得出（模型裡計數為 0、被剪掉的罕見詞）。產品對它們不套類別項（退回 `back × pb`），和第一段的字串查法不同。
  - 量過的影響：依字串與依 id 兩種查法，在 dev302、typing76、錯字回報、cvtune-native 的聊天與書面設定，第一名逐列相同。其他輸入（第二名以後、預測列）沒有量。保留集仍照 §4.5 第 8 項，要等於第一段。
- **數值一律預先算好存檔**：Python 與 Rust 讀同一份數字，只做相同的乘加，才能逐位元相同；不在載入時各自重算。
- **檔案格式說明**寫在 `tools/build_classes.py` 開頭。

### 4.2 機率（Python `reference/proto/lm.py`、Rust `core/src/lm.rs`，逐位元相同）

- `prob(v, w, pb)`：有保留條目的 bigram 照舊。其餘情況（包含 v 沒有前文條目）是：
  - `back × ((1−μ)·pb + μ·P(c(w)|c(v))·emit(w))`；
  - v 沒有前文條目時 `back = 1`；
  - v 或 w 沒有類別時，退回原本的 `back × pb`。
  - 和 `classlm.py` 的 `ClassLM.prob` 相同。
- 用到 `prob` 的地方全部一致套用：解碼（含固定詞的接續）、`total_score`、句尾 `eos`、預測的 `word`／`word_by_id`。
- 運算順序寫死，Python 與 Rust 相同。

### 4.3 載入與設定

- **Rust**：`Lm` 在載入模型後讀同一個目錄的 `classes.sjc`。
  - 檔案不存在，或魔數、長度、模型雜湊任一不符，就是載入失敗（`load_lm` 回傳碼 3），不靜靜不用。
  - 只有測試用的建構子可以明確不載入。
  - 每個用到真實模型的測試，都要斷言 `classes.sjc` 存在；失敗訊息寫出 `classes-v1` 的下載指令，不得跳過（比照 `engine_lm.rs` 對模型檔的做法）。
- **CLI 與 Python**：`shanjie-eval` 與 `lm_eval.py` 預設載入 `data/lm/classes.sjc`，檔案不存在就報錯。加 `--no-classes` 明確關掉。摘要行在關掉時加 `-noclasses`，比照 `-nodemote`。
- **殼層**：不加設定（類別項是模型的一部分）。`scripts/build-app.sh` 把 `classes.sjc` 放進 App；`scripts/check-app.sh` 檢查它存在。

### 4.4 出貨

- 本機建好並全部驗收之後，先問使用者是否建立 GitHub Release `classes-v1`（附 `classes.sjc`），這是對外動作。
- 建好、下載回來比對雜湊相符之後，才做這些：
  - 加 `data/classes.sjc.sha256`；
  - CI（`ci.yml`、`release.yml`）下載它；
  - golden 測試釘它的雜湊；
  - CONTRIBUTING、LICENSES 更新說明。

### 4.5 驗收

1. **對齊**：依賴模型的 golden 全部用類別模型由 Python 重產，Rust 逐位元組相同。範圍是 `s2-lm.txt`、`s2-lm-dev302-top1.tsv`、`s2r-probe-top1.tsv`、`s2h-lm-context.txt`、`sw-probe.txt`、`sp-predict.txt`。每個變動要能追到類別項。
2. **數字等於第一段**：`lm_eval.py --context` 在 dev302、typing76、錯字回報、cvtune-native 的第一名數，等於第一段 README 的類別欄（241／248、66／69、14／16、1,853／1,863）。不等就是停止條件。
3. **單元檢查**：
   - 類別檔的讀寫來回；
   - 檔案不存在、魔數、模型雜湊、長度錯誤時載入失敗（Python 丟錯誤、Rust 回傳碼 3）；Rust 測試：一個只有 `bigram.sjlm`、沒有 `classes.sjc` 的目錄，`load_lm` 回傳碼 3；
   - `--no-classes` 時結果和沒有類別項逐位元相同。
4. **突變**（每一項都要讓指名的測試以斷言失敗，不是編譯錯誤）：
   - 類別項套到有保留條目的 bigram；
   - μ 改成 0；
   - v 沒有前文條目時不套類別項；
   - 不檢查模型雜湊；
   - `Lm::eos` 不套類別項；解碼器裡的句尾項（`lm.rs` 句尾那次 `prob_c`）不套類別項；
   - `End::Next`（固定詞的接續）不套類別項；
   - 預測的 `word_by_id` 不套類別項；`total_score`（`word`）不套類別項；
   - 引擎的 `load_lm` 建出沒有類別的 `Lm`。

   `sp-predict.txt` 與 `s2h-lm-context.txt` 在類別模型下必須有改變的列；如果某個 golden 沒改變，就加一個會改變的手造查詢，讓上面的突變抓得到。
5. **既有探針**：
   - 字形探針 16 列裡，「市占率」接受「市佔率」（使用者同意的例外），其餘逐字相同；
   - 同分探針、竈門、「大概十分鐘後到」、「好吧」；
   - SW 探針的降權條件（第 1 列開＝搞完這波、關＝睪丸這波；第 6–11 列開關相同）。
6. **效能**：
   - 每鍵 p95 < 16 ms：typing76 的按鍵序列，含前文，release，`uptime` 負載低於核心數時量；
   - 載入時間與常駐記憶體的增加量，寫進研究紀錄。
7. `cargo test`（debug、release）、`swift test`、C 冒煙測試全綠（本機有 `classes.sjc`）。
8. **保留集**：由 fresh verifier 收尾時跑一次，只回數字，第一名數要等於第一段（179／183）。
9. **實機**：使用者在實機打幾句。這一項需要使用者。

### 4.6 和 V3 引擎分支的順序

- `docs/v3-engine`（V3 引擎第一片與修訂一）**先合併**。這一段在它合併之後 rebase 到 main 再實作。
- rebase 後，所有新的語言模型呼叫（包含 V3 引擎加的預測列、學習重排）都要經過套用類別項的機率函式。
- 依賴模型的 golden 全部用類別模型重產，包含 `sp-predict.txt` 與 V3 引擎的 golden。
- 驗收時用 `rg 'prob_c|word_by_id|\.word\(|\.eos\('` 檢查 `core/` 裡沒有繞過類別項的呼叫。
- rebase 後 golden 對不上是停止條件。

### 4.7 停止條件、預算、限制

- **停止條件**：
  - 對齊不符；
  - 數字和第一段不同；
  - p95 ≥ 16 ms；
  - 需要改模型檔 `bigram.sjlm` 的格式。
- **預算**：executor 1 回合加 1 次修正。
- **executor 不可以做的事**：
  - 不連網；
  - 不讀 `eval/holdout/`、私有資料、學習檔、聊天紀錄；
  - 不安裝、不啟動 App，不執行 `.app` 裡的程式，不呼叫 TIS 或 lsregister，不碰 `~/Library` 與鑰匙圈；
  - 不 push、不開 PR、不建 Release，不碰其他 worktree。
