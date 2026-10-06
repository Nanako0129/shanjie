# S5k 契約：本機判斷器，不給清單的題型（Laya、Bonsai、Qwen3）

使用者 2026-10-05 決定：下一版 S5 的實驗和 S2n 第三次重建並行（PLAN 原寫和 S2w 並行，使用者稍晚改成先和 S2n 並行），候選依序是 Laya、1-bit Bonsai，以 Qwen3-1.7B 4-bit 當對照。這兩個候選都是使用者提的。

起點是 S5j 的結論（`experiments/s5-judges/README.md`）：Apple 端上模型「從 8 句挑一句」時是照位置挑，換成反序就有 49–55% 改選，在 discordtune 退步 81 句。Jev 的同類題型在 cvtune 進步 46 句，但它是雲端服務。這片要回答：**本機模型換成不依賴清單順序的題型，能不能在使用者的聊天上贏過第一名。**

這片只產生資料，**不改** `core/`、`macos/`、`data/`、`eval/`。接不接進輸入法由 S5 的契約決定。

## 1. 模型與版本（全部在本機執行）

| 代號 | 模型 | 來源與授權 | 執行環境 |
|---|---|---|---|
| L | Laya multilingual（322M，mmBERT-base 加決策頭） | `convaiinnovations/laya-multilingual`，Apache-2.0。用 `laya-mlx convert` 從這個官方權重自己轉成 MLX，不用第三方轉好的 `aac6fef/*` | `laya-mlx`（PyPI，Apache-2.0），venv A |
| B1、B4 | 1-bit Bonsai 1.7B、4B | `prism-ml/Bonsai-1.7B-mlx-1bit`、`prism-ml/Bonsai-4B-mlx-1bit`，Apache-2.0 | PrismML 的 MLX 分支（1-bit 核心尚未進上游），venv B |
| Q | Qwen3-1.7B 4-bit | `mlx-community/Qwen3-1.7B-4bit`（Qwen3 為 Apache-2.0） | `mlx-lm`，venv A |

- 每個模型與套件都釘死版本：PyPI 版本號、Hugging Face 的 commit、MLX 分支的 commit。實際用的版本寫進 `experiments/s5-local/README.md`。
- 兩個 venv 都放在 `~/.cache/shanjie/venv-s5k-a`、`venv-s5k-b`，不進 repo。venv A 裡的 `laya-mlx` 與 `mlx-lm` 若要求不相容的 MLX 版本，Qwen3 改放第三個 venv `venv-s5k-c`，並在 README 記下各自的 MLX 版本。
- **安裝要使用者當下同意**（repo CLAUDE.md：要裝軟體才停下來問）。由 main 安裝與下載模型；agent 不安裝。
- 冒煙測試與完整執行都設 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，模型只從本機路徑載入，執行中不連網。

## 2. 候選、集合與抽樣（沿用 S5j，可逐列配對）

- 候選、分差、抽樣與 discordtune 的 A／B 兩半，全部用 S5j 的 `prep` 產出，不重新產生：
  - discordtune：`~/side-project/shanjie-private/s5-judges/discordtune/`；
  - cvtune：`~/.cache/shanjie/work/s5-judges/cvtune/`；
  - dev302、typing76：`experiments/s5-judges/results/`。
- 所以每一列都能和 S5j 的 A-fwd、J-sent 配對。候選來自 c6abd58 的評測 CLI（chat）。
- **S5j 的 prep 檔只能唯讀使用，而且要先核對雜湊。** main 在 2026-10-05 記下的 SHA-256（discordtune 只記雜湊，不記內容）：

  | 檔案 | 列數 | SHA-256 |
  |---|---|---|
  | `shanjie-private/s5-judges/discordtune/rows.jsonl` | 1000 | `8217e3242c3b302b55485f9d65ffba9e5dcca47130dca0b885da63e8d761bbd9` |
  | `~/.cache/shanjie/work/s5-judges/cvtune/rows.jsonl` | 1000 | `98eec57ff74ca1cc423804c3cfa1afb250314f3eb32c869f728e60a1ce4e170d` |
  | `experiments/s5-judges/results/dev302/rows.jsonl` | 302 | `70aaee641fa78b757683c47595197745075d69b92ccb0b9d5846cd39e7f65c39` |
  | `experiments/s5-judges/results/typing76/rows.jsonl` | 76 | `ed0bb91342ad9bc64cefe4189e809725e0fca5b3acbe7eb8ef2919640c7bd34a` |
  | `shanjie-private/s5-judges/tau.json` | — | `2bc8a2f9983b323f9f9a0da89a9a698cfb29d2dc74aee4dedb1903ec3caa8423` |

  - 工具開啟這些檔案前先比對雜湊；檔案不存在或雜湊不符，就以非 0 結束並印固定字串，不重新產生。
  - **絕不呼叫** `experiments/s5-judges/s5.py` 的 `prep()`、`read_rows()`、`set_dir()`：`read_rows()` 在檔案不見時會用當下的 CLI 與模型悄悄重新產生，S2n 正在重建模型，重產的列不能和 S5j 配對。需要的讀檔與計分函式複製一份到新工具，或只 import 不會寫檔的純函式（`lenient_fn()`、配對統計）。
  - 配對要讀的 S5j 結果檔也先核對雜湊：

    | 檔案 | SHA-256 |
    |---|---|
    | `shanjie-private/s5-judges/discordtune/apple-fwd.tsv` | `bcaddc91ec8bdcfb78b7a3111058a74d4e185d885de03ba471e832c723d235e9` |
    | `~/.cache/shanjie/work/s5-judges/cvtune/apple-fwd.tsv` | `a53445736473f92ed9e60840217126ba9d3aba8c6db09ba236426ed4bbeabedd` |
    | `~/.cache/shanjie/work/s5-judges/cvtune/jev-sent-fwd.jsonl` | `ea8b5637606e0589758b7d51356ce1a29d21af343fbd02e1e6bcf3d64b925594` |
    | `experiments/s5-judges/results/dev302/apple-fwd.tsv` | `11698af04c35aea6764bd421152747811634b644e183428826646fa65431a96f` |
    | `experiments/s5-judges/results/dev302/jev-sent-fwd.jsonl` | `5a909cf09c3539609306614ebf3b0a658df18e460935380615f4dc3ac3b5fd5f` |
    | `experiments/s5-judges/results/typing76/apple-fwd.tsv` | `26b29dd0d6d370d8a73804705501cf87fb219abcad157618aa2701d22f48e47b` |
    | `experiments/s5-judges/results/typing76/jev-sent-fwd.jsonl` | `0577a26da9a7a77b6e6ca23c45a4cf9ace25e31f60c9174246548e17ca534e61` |

  - S5j 的四個位置（`~/side-project/shanjie-private/s5-judges/`、`~/.cache/shanjie/work/s5-judges/`、`experiments/s5-judges/results/dev302/`、`experiments/s5-judges/results/typing76/`）在執行前後不得多出檔案，也不得有檔案的修改時間改變。
  - S5k 的 τ 寫進 `s5-local/` 自己的檔案，不覆蓋 S5j 的 `tau.json`。
- cvtune 的參考句有簡繁轉換殘留（PLAN「待排入」），這個限制和 S5j 相同，README 要寫明。
- 保留集不碰，wikitune 不用。

## 3. 條件（題目與規則在跑之前寫死，看結果後不改）

共同規則：只有一個候選的列不送判斷器。判斷器挑出的候選是「最後的答案」；和第 1 名相同就等於沒改。

**Laya**（`laya-mlx`，fp16，GPU）。三個條件的狀態都是同一個 JSON 物件：有前文時 `{"context": "<前文>"}`，無前文時 `{"context": ""}`。題目文字照下面逐字使用（英文，和 J1 的風格一致）。
- **L-noul**（逐候選打分，主要條件）：每個候選各一題，`type` 是 `noul`，沒有 `criteria`。題目：
  > A user in Taiwan typed a Zhuyin (Bopomofo) phonetic input. The text the user had already written just before it is `context` (it may be empty). Is the following sentence exactly what the user meant: correct Traditional Chinese characters as used in Taiwan, grammatical, and sensible in context? Sentence: 「候選」
  - 取用的分數是 noul 回答裡「是」的機率，也就是 laya-mlx 回傳的 P(true)。冒煙測試要印出 noul 回答的鍵名集合（只印鍵名與數字），和這裡假設的一致才算通過；實際的鍵名或類別數不同，停下回報，不自行詮釋。
  - 挑 P(true) 最高的候選；同分取名次較前者。
- **L-choice-fwd、L-choice-rev**：一題 `choice`，`criteria` 是 `{"c1": 候選1, …, "ck": 候選k}`（反序時 c1 是原本的最後一名）。題目是 S5j 的 `INSTR` 把 `` `rows[{i}].context` `` 換成 `` `context` `` 之後的字面：
  > A user in Taiwan typed a Zhuyin (Bopomofo) phonetic input. Every option below is a sentence with exactly the same pronunciation. The text the user had already written just before it is: `context` (it may be empty). Choose the option that is the sentence the user most likely meant: correct Traditional Chinese characters as used in Taiwan, grammatical, and sensible in context.
  - 正序與反序各跑一次，用來量順序翻轉率；這個題型是對照，不是主要條件。
- **L-pos**：Jevboard 的逐位置題型。出題、題目文字（S5j 的 `POS_INSTR`）與採用規則都和 S5j 的 J-pos 相同（機率 ≥ 0.5 且 ≥ 2 倍「不換」才採用）。狀態照上面的有／無前文兩種。**注意**：S5j 的 J-pos 實際送出的前文是空字串（`s5.py` 的程式行為，和 S5j 契約寫的「狀態只放前文」不同），所以 S5j 的 J-pos 對應的是這裡無前文的 L-pos。`POS_INSTR` 沒有提到前文，所以 L-pos 的有／無前文比較可能量不出差別，README 要寫明。

**Bonsai 與 Qwen3**（生成式模型，用機率打分，不生成）：
- **B1-ll、B4-ll、Q-ll**：每個候選算一次對數機率。
  - 前綴固定為「以下是台灣使用者用注音輸入法打的一句繁體中文。」，有前文時接一行「前文：…」，再接「句子：」。不加聊天範本，不加 BOS 以外的特殊 token。
  - **分數 = log P(前綴＋候選) − log P(前綴)**：把前綴＋候選整串切詞，從前綴的 token 數之後的位置開始，加總每個 token 的對數機率。
  - **切詞邊界檢查**：前綴＋候選切出來的 token，開頭必須正好等於單獨切前綴的 token。不相等的列不打分，計數；超過 1% 的列不相等就停下回報。
  - 不做長度正規化。候選讀音相同、字數相同，但 token 數可能不同，token 少的候選會佔便宜；這是刻意寫死的選擇，README 的限制要寫明。
  - 挑最高分；同分取名次較前者。
- 同一列的 8 個候選，前綴的 KV 只算一次（S5 的設計假設），不行時照實記錄並逐個算。

**前文**：上面每個條件都有「有前文」與「無前文」兩種，只在 discordtune 有前文的列比較兩者（S5j 的 A-ctx 只有這些列）。主要表格用「有前文」。

**門檻（gating）**：和 S5j 相同。分差 < τ 才採用；τ 的候選值是 discordtune 抽樣分差的 10%…90% 分位數加上「永遠採用」；只在 discordtune A 半為每個條件選淨修正最多的 τ，原封不動用到 B 半與 cvtune。

## 4. 指標

和 S5j §4 相同：寬鬆對照的整句正確率、oracle@8、A1b@8、修好／弄壞／McNemar 精確檢定（雙尾）／配對標準誤、挑第 1 名的比例、順序翻轉率（只有 L-choice）。另外：

- **和 S5j 配對**：L-noul、B1-ll、B4-ll、Q-ll 各自和同一列的 A-fwd（Apple）配對比較；cvtune 另和 J-sent-fwd（Jev）配對。
- **延遲**：每列 p50、p95、最大值，分成「載入後第一列」與「其餘」；另報模型載入時間與執行時的記憶體峰值。延遲只有在和使用者約好、使用者沒在用電腦的時段量到的才算有效；其他時段標「負載不明」，不拿來做決定。
- **8 選 1 換算到 64 選 1**：只報 8 個候選的數字，不推估 64 個的延遲。

## 5. 預先寫死的判斷規則

和 S5j §5 相同，這次四個模型都在本機，所以 discordtune 可以全部使用：

| 設定 | 進步要在哪裡成立 | 守門 |
|---|---|---|
| 各條件不設門檻 | discordtune 全部 1,000 列：p < 0.05 且淨值為正 | cvtune 不被否決（p < 0.05 且淨值為負就否決） |
| 各條件設門檻 | discordtune B 半：p < 0.05 且淨值為正 | 同上 |
| 有前文 vs 無前文 | 不參加候選判定，只回答前文有沒有幫助 | — |

- 符合的條件才是 S5 的候選；每個設定都照實報告，不挑好看的。
- 這片約有 14 個設定（含門檻）都用 p < 0.05，沒有做多重比較校正，偶然出現一個「候選」的機率不低。報告要寫明這一點；真正要接進輸入法時，S5 的契約另外處理。
- 另外報 dev302、typing76 的數字，但只記錄；S5j 已經顯示 dev302 會指錯方向。

## 6. 隱私與資料

- discordtune 只給本機模型處理。所有由 discordtune 衍生的檔案，包括抽樣列、模型輸入輸出、分數、檢查點，都只寫進 `~/side-project/shanjie-private/s5-local/`。終端機、repo、報告只出現統計數字。
- 所有工具的 stdout、stderr 與錯誤訊息都不得含句子內容，只能有列號、數字與固定字串。
- cvtune 的逐列結果寫進 `~/.cache/shanjie/work/s5-local/`；dev302、typing76 的逐列結果可以進 `experiments/s5-local/results/`。

## 7. 產出與檔案範圍

- 新增 `experiments/s5-local/`：
  - `README.md`：版本、設定、結果表、限制；
  - 驅動與計分的 Python：沿用 `experiments/s5-judges/s5.py` 的讀檔、配對與計分；
  - `results/`：只放 dev302、typing76。
- `docs/research-log.md` 與 `docs/methodology.md` 由 main 寫。其他檔案都不改。

## 8. 負責人、執行方式與預算

1. main 問使用者能不能安裝。同意後由 main 建兩個 venv、裝釘死版本的套件、下載模型，並用 `laya-mlx convert` 轉出 Laya。
2. `pilotfish:executor` 實作工具，只用 dev302 的前 20 列做冒煙測試，每個條件、有無前文都要測到。回報完整執行的指令，並附上：
   - (a) 冒煙測試與刻意製造的錯誤路徑，輸出都不含句子內容；
   - (b) `git diff` 裡沒有任何 discordtune 的路徑或內容；
   - (c) 在 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 下全部條件都能載入與執行；
   - (d) 每個打分條件（L-noul、B1-ll、B4-ll、Q-ll，有前文與無前文）的「全同分列數」與「挑第 1 名比例」：全同分列數 ≤ 1，挑第 1 名比例 < 100%；
   - (e) 刻意改成「只算前綴」的錯誤實作跑一次，必須觸發退化檢查的停止；
   - (f) 刻意給錯 dev302 的 prep 檔雜湊跑一次（只用公開檔，錯誤路徑不碰 discordtune），必須以非 0 結束、印固定字串；執行前後 S5j 的四個位置沒有新檔，修改時間也沒變；
   - (g) L-noul 回答的鍵名集合（只有鍵名與數字）；
   - (h) **前文真的送進去**：dev302 的列都沒有前文，所以冒煙測試的「有前文」一律用固定的合成前文「我們等一下要去吃飯，」（照 S5j 的 `--synth-ctx`；只用在公開集合的冒煙測試，完整執行用各列真正的前文）。L-noul、B1-ll、B4-ll、Q-ll 各報「有前文與無前文分數不同的列數」，每個都要 ≥ 1；stdout 只印送出的前文字數與這些列數。
3. 完整執行由 main 在背景跑，**先問使用者**一個不用電腦的時段（PLAN 的 Mac 量測規則）。粗估：L-noul 約 2.4 萬題、B／Q 各約 1.9 萬次打分；實際時間以冒煙測試的速度換算後回報給使用者再跑。
4. 結果、解讀與研究紀錄由 main 負責。
5. 預算：executor 1 回合加 1 次修正。

## 9. 停止條件

- 任一模型載入失敗，或輸出出現 NaN 或機率總和不為 1（choice）：停下回報，不換模型。
- PrismML 的 MLX 分支裝不起來或和 venv A 衝突：Bonsai 的條件停下回報，其他照做。
- prep 檔的雜湊和 S5j 紀錄不同：停下回報。
- 完整執行中斷：從中斷處續跑，不重抽樣。
- **退化檢查**（L-noul、B1-ll、B4-ll、Q-ll，有前文與無前文都做）：冒煙測試的 20 列裡，超過 1 列的候選分數全部相同（L-noul 以最大差 < 0.01 為「相同」），或挑第 1 名的比例是 100%：停下回報，不調題目。這條檢查要能抓到「只算到前綴」這類錯誤的實作。回報時要說明它也可能是模型的真實表現（例如真的每列都挑第 1 名），由 main 判斷。
- laya-mlx 的 noul 回答鍵名或類別數和 §3 的假設不同：停下回報。
- venv A 的套件衝突裝不起來，而且拆成 venv C 也不行：停下回報。

## 10. executor 不可以做的事

- 不得改 `core/`、`macos/`、`data/`、`eval/`、`site/`、`.github/`；除了 commit 不做其他 git 動作（不 push、不開 PR）。
- 不得讀 `eval/holdout/`；不得把 discordtune 的任何內容印到終端機或寫進 repo；冒煙測試不用 discordtune。
- 不得安裝軟體、下載模型、開 GUI App、碰鑰匙圈、改系統設定，也不得執行 `.app` 內的二進位。
- 不得自己跑完整實驗（超過冒煙測試的量）；回報指令給 main。

## 11. 修訂（2026-10-06）：188 上的 Qwen3-8B 4-bit（Q8-ll）

- **起因**：S5k 最好的是 Qwen3-1.7B 的 Q-ll 加前文（discordtune B 半淨 +14，p = 0.081，沒過 §5）。使用者 2026-10-06 決定用較大的模型再量一次 log-likelihood，先在 188 上做（雲端供應商拿不到輸入文字的對數機率，研究紀錄同日）。
- **模型與環境**：`Qwen/Qwen3-8B`（Apache-2.0，釘 Hugging Face commit），在 188（RTX 3070 8 GB）用既有 venv `%USERPROFILE%\ime-research\proto\.venv`（torch、transformers、bitsandbytes，版本照實記錄）以 bitsandbytes 4-bit（nf4，計算型別 bfloat16 或 float16，照實記錄）載入。**不安裝任何套件**；venv 缺東西就停下回報。模型由 main 在 Mac 下載（固定 commit），再用 `scp` 傳到 188 的 `%USERPROFILE%\models\Qwen3-8B`（記憶「188 跑模型」：Mac 下載再傳較快）。執行時設 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`。
- **條件 Q8-ll**：和 §3 的 Q-ll 完全相同：同一個前綴、分數 = log P(前綴＋候選) − log P(前綴)、切詞邊界檢查（超過 1% 不相等就停）、不做長度正規化、同分取名次前者、有前文與無前文兩種、同一份 S5j prep 檔（雜湊檢查）。差別只有模型與執行環境。前綴不加聊天範本，只加分詞器預設的 BOS（Qwen3 沒有 BOS 時照實記錄）。
- **判斷規則**：§3 的門檻選法與 §5 的判斷規則原樣套用到 Q8-ll。另報 Q8-ll 與 Q-ll（1.7B）同條件的逐列配對（修好／弄壞／McNemar），只報告。
- **資料流**（188 的 repo 副本放在 `%USERPROFILE%\s5k-188\repo\`，`~` 在 188 是 `%USERPROFILE%`，所以 `s5k.py` 的相對位置不變）：
  - **傳到 188**：`experiments/s5-local/`、`experiments/s5-judges/s5.py` 與 `reference/proto/` 的程式；dev302、typing76 的 prep 檔（`experiments/s5-judges/results/<set>/rows.jsonl`，跟 repo 副本走）；cvtune 的 `~/.cache/shanjie/work/s5-judges/cvtune/rows.jsonl`；discordtune 的 `rows.jsonl`，放到 `%USERPROFILE%\s5k-188\private\s5-judges\discordtune\rows.jsonl`，執行時設 `SHANJIE_PRIVATE=%USERPROFILE%\s5k-188\private`。全部照 `s5k.py` 的雜湊檢查。
  - **傳回 Mac**：每個集合**只**拷回 `Q8-ll.*.jsonl`，放進 Mac 上該集合原本的輸出目錄（discordtune：`~/side-project/shanjie-private/s5-local/discordtune/`；cvtune：`~/.cache/shanjie/work/s5-local/cvtune/`；dev302、typing76：`experiments/s5-local/results/<set>/`）。188 的 meta 寫成同目錄的 `meta-188.jsonl`，不碰原本的 `meta.jsonl`。拷回前後記錄這些目錄裡既有檔案的 SHA-256，必須逐位元組相同。`score.json`、`tau.json` 在 Mac 上用 `score.py` 重產。
- **私有資料**（使用者 2026-10-06 同意）：discordtune 只在 dev302 冒煙通過之後才傳到 188，只供這次執行使用。逐列輸出只有列號、分數、挑選與數字，不含句子；所有 stdout、stderr 不含句子。**不論結果**（成功、任何停止條件、放棄），結束時都刪除 188 上的 `%USERPROFILE%\s5k-188\private\` 整個目錄並用 `dir` 確認不存在；要接著續跑時，最多保留到同一天結束或使用者另外決定。刪除與確認的結果寫進研究紀錄。
- **工具**：`experiments/s5-local/run_ll_cuda.py`，邏輯照 `run_ll.py`（同樣的參數 `--set`、`--limit`、`--ctx none|real|synth`，同樣的輸出檔格式與位置規則，讓 `score.py` 直接讀），模型呼叫改成 transformers。前綴的 KV 能重用就重用，不行就逐個算，照實記錄。
- **executor 的交付**（離線，在 Mac 上）：(0) 把 `Q8-ll` 加進 `s5k.CONDS` 與 `score.py` 的 `BASE_CONDS`，並加上 Q8-ll 對 Q-ll 同條件的逐列配對（修好／弄壞／McNemar，只報告）；在公開的 dev302 前 20 列目錄放 fixture `Q8-ll.noctx.jsonl`、`Q8-ll.ctx.jsonl`，`score.py --set dev302 --limit 20 --tau-self` 要印出 Q8-ll、Q8-ll＋ctx 與門檻列，各帶對 A-fwd、J-sent-fwd 與 Q-ll 的配對欄位，輸出不含句子（fixture 用完刪掉，不 commit）；(a) 把「token 對數機率 → 候選分數、邊界檢查、挑選」寫成不依賴 torch 的純函式，用假的 logits 測：分數等於手算、邊界不符時跳過並計數、同分取前者；(b) 刻意改成「只算前綴」的錯誤實作，退化檢查要觸發停止；(c) 輸出 JSON 的欄位與 `run_ll.py` 相同（同一個 fixture 比對鍵名）；(d) 錯誤路徑與 stdout 不含句子（用含句子的 fixture 檢查）。executor 不連 188、不下載模型、不讀私有資料。
- **main 的執行**：在 188 先用 dev302 前 20 列冒煙（有前文用 §8 的合成前文），報全同分列數、挑第 1 名比例、有無前文分數不同的列數、每列延遲、GPU 記憶體峰值；全部正常才跑 discordtune、cvtune、dev302、typing76。188 一次只跑一個程式，綁 P-core（`start "" /b /wait /affinity FFF`）。
- **停止條件**：§9 全部照舊，另加：8 GB 顯示卡記憶體不夠（OOM）、模型無法以 4-bit 載入、venv 缺套件、任何輸出含句子、刪除私有目錄失敗。
- **延遲**：188 的 GPU 延遲只報告，不和 Mac 的數字比，也不拿來決定能不能進輸入法。
- **預算**：executor 1 回合加 1 次修正；188 上的完整執行以冒煙測試的速度換算後回報使用者再跑。
