# digits：數字語境的計數表與離線量測（契約 `docs/contracts/digit-context.md`，簡稱 C）

這個目錄是 C.4 的 Python 基準：把 `docs/contracts/digits.md`（簡稱 A）A.3.1–A.3.5 的評分切分在 Python 重做一份，量 C.4.2 的三樣東西，照 C.4.3 的規則出 R1／R2 的判讀。A 的 Rust 要和這裡逐位元相同。**本檔記的執行時間都是估計（標〔估〕），只有「實測」標的是在這台 Mac 上用合成資料量到的。**

| 檔案 | 做什麼 |
|---|---|
| `tools/build_digits.py`＋`tools/test_build_digits.py` | 表 `data/lexicon/digits.tsv` 的建置與讀取（`Table`：A.3.2 的每個量都是它的方法）；合成語料上逐一斷言 |
| `digits_score.py` | 數字字母表、串、緩衝（最多 4 鍵、第 5 鍵吐最舊的）、合法尾段、A.3.3 的評分與 θ、結構規則、`Sim`（逐鍵模擬）、A.3.5 的數字語境預測列 |
| `measure.py` | `build-set`（C.4.1 的數字串集合）、`tune`（θ 掃描）、`judge`（判讀半＋dev302＋typing76 的判讀）；R1／R2 的判讀函式 |
| `golden_digits.py` | 產生 `eval/golden/digits-score.txt`（30 列合成按鍵序列，每鍵的切分與分數） |
| `test_digits.py` | 合成詞庫、替身語言模型、手造計數表的單元測試（52 個，含 `measure.py` 的 `judge`／`tune`／`build-set` 用替身 Runner 的端到端測試） |

## 指令（main 在 Windows 機器或 Colab 跑；Mac 只跑單元測試）

單元測試（Mac 可跑；`make test-python` 已含）：

```
/opt/homebrew/bin/python3 -W ignore -m unittest tools/test_build_digits.py
cd experiments/digits && /opt/homebrew/bin/python3 -W ignore -m unittest test_digits
```

前置：`colloquial-train.txt`、`cvtune.txt`、`wikitune.txt` 是 `experiments/s2/build_tune.py` 的輸出（`$S2_WORK/tune/`，預設 `~/.cache/shanjie/work/s2/tune/`）。已有就核對雜湊再用；沒有就：

```
python3 experiments/s2/build_tune.py --part cv      # colloquial-train.txt、cvtune.txt，幾分鐘
python3 experiments/s2/build_tune.py --part wiki    # wikitune.txt，讀 30 萬篇，約 20 分鐘（該檔 docstring）
```

1. **建表**（C.3、C.6 項 1；表的前文 v 是評分端同一個函式 `lm.history(context_key(前文))` 對「同一行裡這個單位前面的文字」算的，所以建表要載入模型，每個程序載一次；`--holdout-rows` 不給＝`eval/holdout/` 底下全部 `.txt`，保留集比對一律開著）：

```
python3 tools/build_digits.py --wiki ~/.cache/shanjie/sources/zhwiki-20261001-pages-articles.xml.bz2 --articles 200000 \
  --colloquial $S2_WORK/tune/colloquial-train.txt --out data/lexicon/digits.tsv --procs 10
```

   輸出最後一行：`k=… bytes=… T=… N(D)=… sha256=…`（k 是大小回退選的最小次數，要記進研究紀錄）。檔頭一行記兩份輸入的 SHA-256、斷詞詞庫（`mcbpmf-data.txt`、`overlay-add.tsv`）與前文所用模型 `bigram.sjlm` 的 SHA-256。
2. **數字串集合**（C.4.1；到 repo 外的目錄；需要 `SHANJIE_MOEDICT`、`SHANJIE_HETERO`，同 `tools/readings.py`）：

```
python3 experiments/digits/measure.py build-set --out-dir $OUT --src ~/.cache/shanjie/sources
```

   寫出 `digitruns-tune.txt`（調參半）、`digitruns-judge.txt`（判讀半）、`digitruns.manifest.json`（種子 20261017、上限 3,000、候選數、輸出雜湊）。不到 1,000 列就以 `STOP` 結束。第一輪的判讀半已經看過兩次，現在當調參資料用（C.6 項 4）。

   **新的判讀集**（C.6 項 4：維基第 305,000–309,999 篇，沒有口語語料，所有列放一個檔，不分兩半；不到 500 列就 `STOP`）：

```
python3 experiments/digits/measure.py build-set --out-dir $OUT --src ~/.cache/shanjie/sources \
  --range 305000 310000 --cap 1500 --single digitruns-judge2.txt
```

   寫出 `digitruns-judge2.txt` 與 `digitruns-judge2.manifest.json`（範圍、種子、上限、候選數、雜湊）。
3. **調參**（θ 掃描；新判讀集不碰；調參資料＝上面第一輪的調參半＋判讀半，可把兩個檔合併成一個再給 `--digit-tune`）：

```
python3 experiments/digits/measure.py tune --table data/lexicon/digits.tsv --digit-tune $OUT/digitruns-tune.txt \
  --cvtune $S2_WORK/tune/cvtune.txt --wikitune $S2_WORK/tune/wikitune.txt --out $OUT/tune-report.txt --procs 8
```

   最後一行 `R2 theta: X` 就是 C.4.3 的 θ（沒有 θ 使 cvtune＋wikitune 的 (c)＝0 時印 `None`，R2 不過）。結構規則在 cvtune／wikitune 的 (c) > 0 時印 `STOP` 並以非零結束（不再往下跑）。
4. **判讀**（判讀集只看這一次；報告檔用 `x` 模式開，已存在就拒絕覆蓋）：

```
python3 experiments/digits/measure.py judge --table data/lexicon/digits.tsv --theta <tune 印的 θ，或 none> \
  --digit-judge $OUT/digitruns-judge2.txt --dev302 eval/bench/suite-v1/dev302.txt --typing76 eval/bench/suite-v1/typing76.txt \
  --out $OUT/judge-report.txt --procs 8
```

   印 R1（兩種設定各一行 McNemar）、R2 的 (a)(b)(c)(d)、時間（每鍵切分數與相容掃描次數的 p50／p95／max）與最後的 `R1:` `R2:` 兩行判讀；結構規則自己的 (c) > 0 時加印 `STOP` 並以非零結束。`--theta none`（tune 沒有 θ）只跑 R1 與結構規則在 dev302／typing76 的 (c)（停止條件用），不跑結構規則在判讀集上的 (a)(b)，也不跑評分版。
5. **golden**（在 macOS 產；Linux 的 libm 末位不同，和 `cli/tests/golden.rs` 的 f64 golden 同一個限制）：

```
python3 experiments/digits/golden_digits.py --table data/lexicon/digits.tsv --theta <θ> [--mode structural] --out eval/golden/digits-score.txt
```

   R2 判「結構規則」時加 `--mode structural`（分數欄是 `None`，只比切分）；判評分切分就用 tune 印的 θ。格式在 `golden_digits.py` 的 docstring；檔頭有 `digits.tsv`、三個模型檔與五個詞庫檔（`mcbpmf-data.txt`、`overlay-add.tsv`、`sandhi-add.tsv`、`names-add.tsv`、`demote.tsv`）的 SHA-256。有音節在列中途完成、之後還有鍵，卻沒在 `chars` 給字的列，產生器會拒絕（`strict`）。`eval/golden/digits-score.txt` 要在新表出來之後由 main 重產；Python 這邊沒有任何測試去比對已提交的那個檔（Rust 的比對是 A 的事）。

## 執行時間（2026-10-17 修訂三的一次跑，Colab G4，48 執行緒，`--procs 44`）

| 步驟 | 時間 |
|---|---|
| 建表（`build_digits.py`，20 萬篇＋口語，每個程序各載一份模型） | 2 分 11 秒（不含下載 dump） |
| `build-set --range 305000 310000 --single`（新判讀集） | 2 分 9 秒（單程序） |
| `tune`（θ 8 格＋結構規則＋預測列，2,991 列調參資料） | 2 分 13 秒 |
| `judge` | 44 秒 |
| `golden_digits.py` | Mac 8–13 秒（含載入；兩次實測） |

修訂三之前的一次（表 `4efd2acc`）：建表 1 分 57 秒、`tune`（θ 4 格）1 分 22 秒、`judge` 38 秒，Windows 機器的 WSL（16 執行緒）θ 8 格 `tune` 4 分 29 秒、`judge` 1 分 27 秒。

## 結果（2026-10-17 修訂三；表 `a75a328b`，k＝3，1,555,575 位元組）

| 項目 | 數字 |
|---|---|
| 調參資料 | 舊的調參半＋判讀半合併 2,991 列（兩半都看過，只用來調，不判讀）；cvtune 3,791 列、wikitune 2,827 列 |
| 新判讀集 | 維基第 305,000–309,999 篇，1,494 列（`digitruns-judge2.manifest.json` 記雜湊） |
| R1 預測列（新判讀集 hit@9） | 聊天 `<s>` 54.8% → 數字語境 85.7%（改對 462／改壞 1，McNemar p 3.9e-137）；書面 57.4% → 87.8%（455／2，p 5.6e-133）→ **數字語境** |
| (i) 還沒打注音時的數字語境列 hit@9 | 63.5%（後接詞在後繼層 98.7%） |
| R2 θ（調參資料） | θ 0／0.5／1／2 的 (c) 合計 61／18／4／2，θ 3 起 0 → **θ＝3** |
| R2 判讀（θ＝3，新判讀集） | 評分 (a) 1,342／1,494＝89.8%，結構規則 1,273＝85.2%；dev302、typing76 的 (c) 都是 0；(d) 每千音節 2.74、7.95 → 照 C.7 **評分切分**（原本的 90% 門檻差 3 列） |
| 翻轉次數（新判讀集，0／1／2／3+） | 評分 755／443／278／18；結構規則 313／403／337／441 |
| 時間（每鍵） | 切分數 p95 3、相容掃描 p95 2 |

修訂三之前（表 `4efd2acc`，前文是斷詞的前一個詞、θ 加在所有注音切分上；舊判讀半）：R1 聊天 55.9% → 84.4%、書面 59.4% → 86.2%；θ 3；(a) 91.0%（結構規則 86.8%）。

## 停止條件（C.5）

- `build_digits.py` 到 k＝50 仍超過 2,000,000 位元組：印 `STOP` 並以非零結束。
- 數字串集合不到 1,000 列：`build-set` 印 `STOP` 並以非零結束（檔仍寫出）。
- 結構規則的 (c) > 0（dev302 或 typing76）：`judge` 印 `STOP`（合法讀音上結構規則一定是 0，表示按鍵集合或工具有錯）。tune 報告裡結構規則的 cvtune／wikitune (c) 也要是 0，不是的話同樣停。
- 輸入雜湊不符、保留集比對的載入數是 0（`holdout_guard` 自己會停）：main 在跑之前核對。

## 契約沒定、這裡定下來的（A.8 說「規則錯或缺」的地方，照 C 的字面取最簡單一致的讀法）

**切分與評分**

1. **串中延續時 m＝0 的語境**（A.8-1）：統一用公式。數字總數 d＝已吐出＋m，`d ≥ 1` 時尾段的語境是數字語境 (v＝`run_ctx`, D)，`d＝0` 時是詞語境 C；`P(D|C)`、`P_len`、`P_ch` 對整串的前 d 個鍵算（已吐出的鍵也算）。所以同一鍵的各個切分分數可比。
2. **詞語境的 `P(w|C)`** 用 `lm.prob(C, w, pb)`（機率），**不是** `word_by_id`／`lm.word` 的混合分數（`λ·log p + (1−λ)·lp`）：A.3.3 的 lpD 與數字語境的 `P(w|v,D)` 都是機率，m＝0 與 m＞0 的切分要比的是同一種量。λ 只出現在預測列的排序（下一點）。
3. **`P(w)`**（A.3.2 的「模型的單字機率」）取 `lm.word` 裡的後備項 `pb`（詞庫 lp 的 10^lp，模型有 KN 側檔時照它的混合）；每個詞用「最佳相容讀音」的 lp。
4. **相容詞的總和**是候選順序（`predict3.reference`，即 `predict.Index` 的桶順序去重）上由左到右的一般浮點加法，不用 `fsum`；Rust 要用同一個順序才會逐位元相同。lpD 的相加順序：`log P(D|C)`、`log P_len`、再依鍵序加 `log P_ch`。
5. **d＝0 時 lpD＝0**（沒有 `1−P(D|C)` 這一項），照 A.3.3 字面。
6. **完整音節要有含漢字的詞條**（A.3.1）：取「這個音節出現在某個含漢字的詞條的讀音裡」（任何字數的詞），不要求有單字詞條；注音字母詞（「ㄅ」）不算。尾段的前綴＝詞庫任一去聲調音節的前綴（這也決定聲介韻順序）。
7. **θ**（C.6 項 3）：只在尾段是完成音節（聲調鍵或空白）時用，而且只加在「不含任何數字」的注音切分（已吐出的數字＋m＝0）上：這種切分的有效分數＝分數＋θ，含數字的注音切分（d ≥ 1）的有效分數就是分數；全數字切分要**嚴格大於**最好的有效注音分數才贏，否則取最好的注音切分（注音之間先比有效分數，平手取尾段較長者）。θ 的用途是不讓純中文的音節被當成數字；串裡已經有數字時，問題只是串在哪裡結束，加 θ 會把 `1984` 的 `84` 判成「啊」。其他鍵：取分數最大；平手時**目前顯示的數字總數不變**者優先，沒有就取 t 較大者（偏注音）；串的第一個鍵沒有「目前顯示」，平手取注音。
8. **結構規則**（`mode="structural"`）：合法切分裡取 t 最大者，不用表也不算分數（分數欄是 `None`）。
9. **不在字母表的注音鍵**：尾段＝緩衝最後 ≤3 個鍵＋這個鍵，前段吐成數字、尾段進入未完成音節、串結束；沒有任何合法尾段就把緩衝全吐成數字、這個鍵開新音節。空白鍵：尾段以空白完成（一聲）對「全數字＋邊界」；數字贏時空白是分隔（類別 `S`），不是數字。
10. **聲調鍵單獨起頭**（沒有緩衝）：直接是數字 token，並開一個串（`run_ctx` 依當時的前文）。
11. 每個鍵序列最後都補一個 Enter（邊界鍵）：緩衝全吐成數字，所以「最後」的類別是確定的。Enter 的翻轉不算進 (b)，Enter 之前的按鍵才算 (d)。
12. **前文的來源**：串開始時的 `run_ctx`＝`lm.history(context_key(前文＋已顯示的字串))`；數字 token 顯示數字字元，完成的音節顯示「這一列的正解字」（`chars`），所以第二個以上的串的語境用的是正解字，不是引擎實際解出來的字。golden 的 30 列裡只有 2 列（`hk4g4123` 的 123、`2026su062k7` 的第二個串）的語境取決於前面音節顯示的字；Rust 若要比這兩列，要讓引擎顯示同樣的字（列首的 `chars=`），否則那兩列不可比（A 的 executor 在 PR 註明）。

**表（C.3）**

13. **N(v)**（C.6 項 1）＝前文是 v 的單位（詞與數字串）個數；`<s>` 是行首、前面沒有漢字、或前面的漢字沒有歷史紀錄的單位。`D\t<s>` 列永遠寫，不受 k 影響。v 是 `lm.history(context_key(前文))`：三字以上的詞接數字串時，v 是它最後兩字或最後一字（看哪個在模型有歷史紀錄），和評分端一致；保留集比對命中的漢字段在前文裡當邊界。
14. **Q 列**：N(v→D) ≥ k 的 v 都寫，N(v,D→·)＝0 也寫（0 與沒有這一列算出的值相同，照契約字面）。
15. **大小回退**：k 依 1、2、3、5、10、20、30、50 試，2 MB 取 2,000,000 位元組（較嚴的讀法，也滿足 2 MiB）。D／Q 需要 N(v→D) ≥ k，W 需要 N(D→w) ≥ k，P 需要 ≥ max(2, k)；N(v) ≥ 1,000 的 D 列照留，不論 N(v→D)（C.6 項 2；原本 1 ≤ N(v→D) < k 的被截掉而退回 P(D)，常見詞反而比沒接過數字的詞大）。這些保留的 D 列沒有 Q 列（Q 要 N(v→D) ≥ k）。
16. **L 列**：長度是串的字元數（含 `.` `/` `-`），10 以上合計成 `10+`；`C` 列的 `ch` 是這 12 個字元。
17. 前處理：`experiments/s2/build_counts.py` 的 `article_runs` 拆出前半段 `article_sentences`（逐句、轉換後、取漢字段之前），`article_runs` 改成呼叫它再 `HAN.findall`；輸出逐位元不變（`test_build_digits.py` 與既有測試覆蓋）。`build_digits.py` 用同一個函式，只把最後一步換成保留數字串的切法。口語行照 `build_counts_text` 再 `bc.convert` 一次。
18. 保留集比對：命中的漢字段不計數，也當成邊界（`holdout_guard.build_matcher` 的比對器，`bc.fold` 兩邊折疊）。

**量測（C.4.2）**

19. **預測列的排序**：候選＝`predict3.reference`（前綴解讀）；分數＝`λ·log10 P(w|v,D) + (1−λ)·lp`（`lm.word` 的形狀，P 換成 A.3.2 的 `P(w|v,D)`，`P(w)` 用第 3 點的 `pb`）；後繼層＝W 列的詞∪`P` 列 (v, ·) 的詞，其餘候選在第二層；`predict3.order_s`／`rank_s`（悲觀名次，hit@9）。(i)（還沒打注音）：後繼層依 `P(w|v,D)` 排名（沒有尾段，不混詞庫 lp），詞不在後繼層算沒中。基準＝歷史詞 `<s>`、`predict3` 的 S 順序，第一個注音鍵（`position_units` 的 P1）。R1 的 McNemar：「改對」＝數字語境中、基準沒中；「改壞」相反；顯著變差＝p < 0.05 且改壞 > 改對。
20. **數字串集合**：每個數字串（照 `build_digits.TOKEN`）若緊接一段能斷詞的漢字段，就是一列：前文＝它前面緊鄰的漢字段的最後 8 字（可空；前面是標點或英文則空）、後接詞＝後面那段的第一個詞；每句最多一列（種子隨機取），去重後取樣到上限 3,000，讀音用 `tools/readings.py`（CHECK 與讀不出的丟掉），再以種子 20261017 分成調參半與判讀半（各自保持輸入順序）。來源與切法同 `build_e4ctx.py`：Common Voice／Tatoeba 的 `is_tune` 行、維基第 300,000–304,999 篇（轉換後的原句）。
21. **(a)** 全部按鍵（數字串＋後接詞的注音）送完加 Enter 後，各鍵的類別＝前 len(串) 個是 `D`、其餘全是 `Z`；**(b)** 該列所有按鍵（不含最後的 Enter）每個鍵之後，先前已有類別的鍵改變類別的次數，分布 0／1／2／3+；**(c)** 一個音節只要有任何一個鍵最後是 `D` 就算一個（每千音節）；**(d)** 按鍵之後，這個鍵所屬的串（含剛結束的）顯示著數字的按鍵數（每千音節，含最後留成數字的）；時間＝每個有切分的鍵的切分數、其中 t＞0 的個數（每個要一次相容掃描，不算快取）。
21b. **效能與統計的細節**（C.6 項 5）：(b) 只數數字串自己那個串的鍵的翻轉（`sim.rid_of`），後接詞裡另起的串不算；(i) 每列只算一次（沒有設定之分），(ii) 兩個設定在同一趟裡算；`Table.successors` 依 v 快取；`tune` 的時間行取 θ 0 那一趟的結果；θ 的集合只寫在 `measure.THETAS`（訊息由它產生）。
22. θ 在 `tune` 只用 (c) 選（cvtune＋wikitune 合計 (c)＝0 的最小 θ）；調參半的 (a)(b) 與預測列一起印出來參考，但不進判讀。判讀半、dev302、typing76 都在 `judge` 一次看完。

**沒有做、留給 A 的 Rust 或 main 的**

- A.8-2…8（部分注音餵預測列、必要檔改可有可無、#37 的預期、#40 的基準、golden 的平台、數字後空白）是引擎端的事，Python 基準沒有引擎，不處理。(ii) 量的是「打了第一個注音鍵時」的預測列（`predict3` 的 P1），不是引擎 `recompute_pred` 的實際路徑。
- `docs/data-files.md`、`LICENSES/data.md`、`docs/PLAN.md`、研究紀錄：要真表的雜湊與數字，等 main 建完表再寫。
