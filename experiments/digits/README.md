# digits：數字語境的計數表與離線量測（契約 `docs/contracts/digit-context.md`，簡稱 C）

這個目錄是 C.4 的 Python 基準：把 `docs/contracts/digits.md`（簡稱 A）A.3.1–A.3.5 的評分切分在 Python 重做一份，量 C.4.2 的三樣東西，照 C.4.3 的規則出 R1／R2 的判讀。A 的 Rust 要和這裡逐位元相同。**本檔記的執行時間都是估計（標〔估〕），只有「實測」標的是在這台 Mac 上用合成資料量到的。**

| 檔案 | 做什麼 |
|---|---|
| `tools/build_digits.py`＋`tools/test_build_digits.py` | 表 `data/lexicon/digits.tsv` 的建置與讀取（`Table`：A.3.2 的每個量都是它的方法）；合成語料上逐一斷言 |
| `digits_score.py` | 數字字母表、串、緩衝（最多 4 鍵、第 5 鍵吐最舊的）、合法尾段、A.3.3 的評分與 θ、結構規則、`Sim`（逐鍵模擬）、A.3.5 的數字語境預測列 |
| `measure.py` | `build-set`（C.4.1 的數字串集合）、`tune`（θ 掃描）、`judge`（判讀半＋dev302＋typing76 的判讀）；R1／R2 的判讀函式 |
| `golden_digits.py` | 產生 `eval/golden/digits-score.txt`（30 列合成按鍵序列，每鍵的切分與分數） |
| `test_digits.py` | 合成詞庫、替身語言模型、手造計數表的單元測試（42 個） |

## 指令（main 在 188 的 WSL／Windows 或 Colab 跑；Mac 只跑單元測試）

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

1. **建表**（C.3；`--holdout-rows` 不給＝`eval/holdout/` 底下全部 `.txt`，保留集比對一律開著）：

```
python3 tools/build_digits.py --wiki ~/.cache/shanjie/sources/zhwiki-20261001-pages-articles.xml.bz2 --articles 200000 \
  --colloquial $S2_WORK/tune/colloquial-train.txt --out data/lexicon/digits.tsv --procs 10
```

   輸出最後一行：`k=… bytes=… T=… N(D)=… sha256=…`（k 是大小回退選的最小次數，要記進研究紀錄）。檔頭一行記兩份輸入的 SHA-256、斷詞詞庫（`mcbpmf-data.txt`、`overlay-add.tsv`）的 SHA-256。
2. **數字串集合**（C.4.1；到 repo 外的目錄；需要 `SHANJIE_MOEDICT`、`SHANJIE_HETERO`，同 `tools/readings.py`）：

```
python3 experiments/digits/measure.py build-set --out-dir $OUT --src ~/.cache/shanjie/sources
```

   寫出 `digitruns-tune.txt`（調參半）、`digitruns-judge.txt`（判讀半）、`digitruns.manifest.json`（種子 20261017、上限 3,000、候選數、輸出雜湊）。不到 1,000 列就以 `STOP` 結束。
3. **調參**（θ 掃描；判讀半不碰）：

```
python3 experiments/digits/measure.py tune --table data/lexicon/digits.tsv --digit-tune $OUT/digitruns-tune.txt \
  --cvtune $S2_WORK/tune/cvtune.txt --wikitune $S2_WORK/tune/wikitune.txt --out $OUT/tune-report.txt --procs 8
```

   最後一行 `R2 theta: X` 就是 C.4.3 的 θ（沒有 θ 使 cvtune＋wikitune 的 (c)＝0 時印 `None`，R2 不過）。
4. **判讀**（判讀半只看這一次；報告檔用 `x` 模式開，已存在就拒絕覆蓋）：

```
python3 experiments/digits/measure.py judge --table data/lexicon/digits.tsv --theta <tune 印的 θ，或 none> \
  --digit-judge $OUT/digitruns-judge.txt --dev302 eval/bench/suite-v1/dev302.txt --typing76 eval/bench/suite-v1/typing76.txt \
  --out $OUT/judge-report.txt --procs 8
```

   印 R1（兩種設定各一行 McNemar）、R2 的 (a)(b)(c)(d)、時間（每鍵切分數與相容掃描次數的 p50／p95／max）與最後的 `R1:` `R2:` 兩行判讀；結構規則自己的 (c) > 0 時加印 `STOP`。
5. **golden**（在 macOS 產；Linux 的 libm 末位不同，和 `cli/tests/golden.rs` 的 f64 golden 同一個限制）：

```
python3 experiments/digits/golden_digits.py --table data/lexicon/digits.tsv --theta <θ> [--mode structural] --out eval/golden/digits-score.txt
```

   R2 判「結構規則」時加 `--mode structural`（分數欄是 `None`，只比切分）；這次判評分切分，用 `--theta 3`。格式在 `golden_digits.py` 的 docstring；檔頭有 `digits.tsv` 與三個模型檔的 SHA-256。

## 執行時間（2026-10-17 實測）

| 步驟 | Colab G4（48 執行緒，`--procs 46`） | 188 的 WSL（16 執行緒，`--procs 16`） |
|---|---|---|
| 建表（`build_digits.py`，20 萬篇＋口語） | 1 分 57 秒（不含下載 dump） | 沒跑 |
| `build-set` | 2 分 7 秒（單程序） | 沒跑 |
| `tune`（θ 4 格＋結構規則＋預測列） | 1 分 22 秒 | θ 8 格：4 分 29 秒 |
| `judge` | 38 秒 | 1 分 27 秒 |
| `golden_digits.py` | — | Mac 約 8 秒（含載入） |

## 結果（2026-10-17；表 `4efd2acc`，k＝3）

| 項目 | 數字 |
|---|---|
| 數字串集合 | 調參半 1,495 列、判讀半 1,496 列（`digitruns.manifest.json` 記雜湊） |
| R1 預測列（判讀半 hit@9） | 聊天 `<s>` 55.9% → 數字語境 84.4%（改對 427／改壞 2，McNemar p 1.3e-124）；書面 59.4% → 86.2%（408／7，p 9.6e-111）→ **數字語境** |
| (i) 還沒打注音時的數字語境列 hit@9 | 判讀半 60.6%（後接詞在後繼層 98.3%） |
| R2 θ（調參資料） | θ 0／0.5／1／2 的 (c) 合計 58／28／4／2，θ 3 起 0 → **θ＝3**（格點在第一次跑之後由 2 延伸到 8，契約 C.4.3） |
| R2 判讀（θ＝3） | (a) 91.0%（結構規則 86.8%）；dev302、typing76 的 (c) 都是 0；(d) 每千音節 2.74、7.95 → **評分切分** |
| 翻轉次數（判讀半，0／1／2／3+） | 評分 677／446／299／74；結構規則 216／427／402／451 |
| 時間（每鍵） | 切分數 p95 3、相容掃描 p95 2 |

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
7. **θ**：只在尾段是完成音節（聲調鍵或空白）時用：全數字切分要**嚴格大於**最好的注音切分＋θ 才贏，否則取最好的注音切分（內部平手取尾段較長者）。其他鍵：取分數最大；平手時**目前顯示的數字總數不變**者優先，沒有就取 t 較大者（偏注音）；串的第一個鍵沒有「目前顯示」，平手取注音。
8. **結構規則**（`mode="structural"`）：合法切分裡取 t 最大者，不用表也不算分數（分數欄是 `None`）。
9. **不在字母表的注音鍵**：尾段＝緩衝最後 ≤3 個鍵＋這個鍵，前段吐成數字、尾段進入未完成音節、串結束；沒有任何合法尾段就把緩衝全吐成數字、這個鍵開新音節。空白鍵：尾段以空白完成（一聲）對「全數字＋邊界」；數字贏時空白是分隔（類別 `S`），不是數字。
10. **聲調鍵單獨起頭**（沒有緩衝）：直接是數字 token，並開一個串（`run_ctx` 依當時的前文）。
11. 每個鍵序列最後都補一個 Enter（邊界鍵）：緩衝全吐成數字，所以「最後」的類別是確定的。Enter 的翻轉不算進 (b)，Enter 之前的按鍵才算 (d)。
12. **前文的來源**：串開始時的 `run_ctx`＝`lm.history(context_key(前文＋已顯示的字串))`；數字 token 顯示數字字元，完成的音節顯示「這一列的正解字」（`chars`），所以第二個以上的串的語境用的是正解字，不是引擎實際解出來的字。golden 的 30 列裡只有 2 列（`hk4g4123` 的 123、`2026su062k7` 的第二個串）的語境取決於前面音節顯示的字；Rust 若要比這兩列，要讓引擎顯示同樣的字（列首的 `chars=`），否則那兩列不可比（A 的 executor 在 PR 註明）。

**表（C.3）**

13. **N(`<s>`)**＝段落數（連續單位序列，被邊界隔開；行首與行尾是邊界）；`D\t<s>` 列永遠寫，不受 k 影響。
14. **Q 列**：N(v→D) ≥ k 的 v 都寫，N(v,D→·)＝0 也寫（0 與沒有這一列算出的值相同，照契約字面）。
15. **大小回退**：k 依 1、2、3、5、10、20、30、50 試，2 MB 取 2,000,000 位元組（較嚴的讀法，也滿足 2 MiB）。D／Q 需要 N(v→D) ≥ k，W 需要 N(D→w) ≥ k，P 需要 ≥ max(2, k)；N(v→D)＝0 且 N(v) ≥ 1,000 的 D 列照留。N(v→D) 在 1 到 k−1 之間、N(v) ≥ 1,000 的詞，D 列被截掉（字面；那時用 P(D)）。
16. **L 列**：長度是串的字元數（含 `.` `/` `-`），10 以上合計成 `10+`；`C` 列的 `ch` 是這 12 個字元。
17. 前處理：`experiments/s2/build_counts.py` 的 `article_runs` 拆出前半段 `article_sentences`（逐句、轉換後、取漢字段之前），`article_runs` 改成呼叫它再 `HAN.findall`；輸出逐位元不變（`test_build_digits.py` 與既有測試覆蓋）。`build_digits.py` 用同一個函式，只把最後一步換成保留數字串的切法。口語行照 `build_counts_text` 再 `bc.convert` 一次。
18. 保留集比對：命中的漢字段不計數，也當成邊界（`holdout_guard.build_matcher` 的比對器，`bc.fold` 兩邊折疊）。

**量測（C.4.2）**

19. **預測列的排序**：候選＝`predict3.reference`（前綴解讀）；分數＝`λ·log10 P(w|v,D) + (1−λ)·lp`（`lm.word` 的形狀，P 換成 A.3.2 的 `P(w|v,D)`，`P(w)` 用第 3 點的 `pb`）；後繼層＝W 列的詞∪`P` 列 (v, ·) 的詞，其餘候選在第二層；`predict3.order_s`／`rank_s`（悲觀名次，hit@9）。(i)（還沒打注音）：後繼層依 `P(w|v,D)` 排名（沒有尾段，不混詞庫 lp），詞不在後繼層算沒中。基準＝歷史詞 `<s>`、`predict3` 的 S 順序，第一個注音鍵（`position_units` 的 P1）。R1 的 McNemar：「改對」＝數字語境中、基準沒中；「改壞」相反；顯著變差＝p < 0.05 且改壞 > 改對。
20. **數字串集合**：每個數字串（照 `build_digits.TOKEN`）若緊接一段能斷詞的漢字段，就是一列：前文＝它前面緊鄰的漢字段的最後 8 字（可空；前面是標點或英文則空）、後接詞＝後面那段的第一個詞；每句最多一列（種子隨機取），去重後取樣到上限 3,000，讀音用 `tools/readings.py`（CHECK 與讀不出的丟掉），再以種子 20261017 分成調參半與判讀半（各自保持輸入順序）。來源與切法同 `build_e4ctx.py`：Common Voice／Tatoeba 的 `is_tune` 行、維基第 300,000–304,999 篇（轉換後的原句）。
21. **(a)** 全部按鍵（數字串＋後接詞的注音）送完加 Enter 後，各鍵的類別＝前 len(串) 個是 `D`、其餘全是 `Z`；**(b)** 該列所有按鍵（不含最後的 Enter）每個鍵之後，先前已有類別的鍵改變類別的次數，分布 0／1／2／3+；**(c)** 一個音節只要有任何一個鍵最後是 `D` 就算一個（每千音節）；**(d)** 按鍵之後，這個鍵所屬的串（含剛結束的）顯示著數字的按鍵數（每千音節，含最後留成數字的）；時間＝每個有切分的鍵的切分數、其中 t＞0 的個數（每個要一次相容掃描，不算快取）。
22. θ 在 `tune` 只用 (c) 選（cvtune＋wikitune 合計 (c)＝0 的最小 θ）；調參半的 (a)(b) 與預測列一起印出來參考，但不進判讀。判讀半、dev302、typing76 都在 `judge` 一次看完。

**沒有做、留給 A 的 Rust 或 main 的**

- A.8-2…8（部分注音餵預測列、必要檔改可有可無、#37 的預期、#40 的基準、golden 的平台、數字後空白）是引擎端的事，Python 基準沒有引擎，不處理。(ii) 量的是「打了第一個注音鍵時」的預測列（`predict3` 的 P1），不是引擎 `recompute_pred` 的實際路徑。
- `docs/data-files.md`、`LICENSES/data.md`、`docs/PLAN.md`、研究紀錄：要真表的雜湊與數字，等 main 建完表再寫。
