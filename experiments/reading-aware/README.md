# 讀音感知 n-gram 第一片（reading-aware-0）

契約：`docs/contracts/reading-aware-0.md`（§3 四臂、§4 驗收、§5 停止條件）。這個資料夾是 executor 寫的全部程式與單元測試；188 與 Colab 的執行、雜湊核對、discordtune、人工判讀、研究紀錄由 main 做。不改核心、`reference/proto/`、`tools/`、`data/lexicon/demote.tsv`、`experiments/s2/` 與 `experiments/derisk-1015/` 的既有檔案（只 import）。

單元測試（合成資料；Mac 可跑，約 2.5 分鐘，因為有幾支會載入真的詞庫；輸出寫檔、看結束碼）：

```
cd <REPO> && /opt/homebrew/bin/python3 -W ignore -m unittest discover -s experiments/reading-aware -p 'test_*.py' > OUT.txt 2>&1; echo rc=$?
# 被 import 的既有模組的測試（沒有改，應該照舊通過）
python3 -W ignore -m unittest experiments/derisk-1015/common/test_common.py experiments/s2/test_expected.py
python3 -W ignore experiments/derisk-1015/e1/test_e1.py        # 約 2.5 分鐘；count.py、common.py、mapping.py 來自這裡
```

## 檔案

| 檔 | 做什麼（契約條） |
|---|---|
| `rcommon.py` | 路徑、`sys.path`、`U = "\x01"`、詞庫載入、單字讀音表、依讀音的最高分切分（`segment`）、`load_rows`（檔案或 `dev:N`） |
| `a1_table.py` | A1 降權表產生器（§3.2）：44 字清單（具名常數 `DEMOTE_44`，註解引契約）、只產生它們的 ˙ 列、δ 預設 1.0 可設、類別 `reading-neutral`、格式同 `sw-sensitive-demote.md` §2。每個字要同時有 ˙ 與非 ˙ 讀音，寫出後用 `lm.load_demote` 對詞庫載入一次，任何一項不成立就停 |
| `level1.py` | 第 1 級的字與列（§3.3）、固定方向的探針（˙ → 同音節一聲，只換單獨成詞的語尾字）、每個集合的探針列數、「原本就是一聲的列」。`list`、`probe`、`onetone` 三個子命令 |
| `ra_eval.py` | 評測包裝（§3.1–3.3、§3.4 解碼）：原封不動跑 `lm_eval.py`，只在這一次執行中包住 `lm.cap_overlay`（把額外的降權表併進回傳詞庫的 `demote`）；`--units` 時另換 `lm.decode`、`lm.history` 與封頂用的次數 |
| `buckets.py` | 第 0 步桶表（§3.0），輸入是 BASE 的 `--dump` |
| `units.py` | 位置標注（§3.4）：主要讀音、語尾字、了的得地還重空長行的規則、單位 |
| `count_units.py` | 計數包裝（§3.4）：`e1/count.py` 的配方，邊的鍵從詞字串改成單位；`--identity` 時單位就是詞字串（計數錨點） |
| `build_model.py` | 用 `tools/build_lm.py` 把計數建成模型；單位的異體類；`--expect-sha256`（計數錨點）、`--b0`（詞彙數上限） |
| `decode_units.py` | 延伸解碼（§3.4）：裸鍵不分讀音、`字 U+0001 讀音` 單位查詢、占比路徑、詞彙外路徑、前文取最多的單位 |
| `share_rows.py` | 「另外單獨報」的第二項：改壞的列裡有多少落在「用占比」的字上 |
| `sample_labels.py` | 標注錯誤率的抽樣（固定種子）與計算（`rate`，> 10% 結束碼 3） |
| `verdict.py` | 「顯著為正／負」一對 rowstats 的判定（與 `evalstats compare` 同一個 McNemar），`--stop-on-negative` 結束碼 5 |
| `testkit.py`、`test_*.py` | 合成詞庫／模型的測試輔助、單元測試 |

## 契約沒寫死、我做的決定

| 項目 | 做法與理由 |
|---|---|
| 異體合併 | 計數階段不改字：邊的單位是 `原字 U+0001 讀音`。合併在建模：`build_model.unit_classes` 把 `build_lm.variant_classes` 的每一類擴充成「每個讀音一類」（`臺U+0001R` 與 `台U+0001R` 同類，代表成員的字串在前），交給 `build_lm.build(cls=…)`，所以 `merge_variants`、N、`eos_total` 的「類只算一次」都照 B0 的路徑。效果等於契約的「先合併字、再接讀音」（類的成員讀音集合相同，`variant_classes` 的條件）。沒有任何單位時，類就是 `build_lm` 自己算的，模型逐位元組相同（`test_units.ModelAnchor`） |
| 哪些字有單位 | 看**解碼用的詞庫**（基底加 `ime.overlays()` 三層疊加層）裡的單字讀音數（≥ 2），不看計數用的詞庫（4d47a50 的 `overlay-add.tsv` 加詞包）。計數與解碼因此用同一份「讀音表」 |
| 次數 ≥ 2 的字、標注的上下文 | 標注看的是**整句**（`convert` 之後、`bc.fold` 套 MERGE 之後的句子），不是漢字段：行前面的阿拉伯數字、標點都算上下文。只有 ≥ 2 字的漢字段才計數（和 E1 一樣），1 字的段不計 |
| 規則 0（人工表） | `tools/reading_overrides.tsv` 只取單字的列（多字詞的列是詞，不是位置）。表裡的讀音、`heterophony1.list` 的讀音不在詞庫這個字的讀音裡，就跳過這條規則（不是報 GAP） |
| 語尾字的 ˙ | 詞庫裡有好幾個 ˙ 讀音時取分數最高的那個（表的順序） |
| 占比的 `P(字｜前一個)` | 這個字**模型裡有的**每個單位 u 的 `lm.prob(前一個, u, 10^lp_u)` 相加（`lp_u` = 詞庫裡那個讀音的分數）；占比 = `10^lp / Σ 10^lp_讀音`，Σ 是詞庫裡這個字的全部讀音（傳給 decode 的那份詞庫，已封頂、未扣降權）。單位不在模型裡的那個讀音，路徑上的「前一個單位」仍是那個不在模型裡的字串，所以**下一個詞沒有二元組前文、直接回退**（契約沒寫，決定） |
| 次數（封頂用） | 單字的 `count(w)` = 裸鍵次數 + 它所有單位的次數（各自四捨五入後相加）。B0 的單字沒有單位，結果和 B0 相同 |
| 模型有單位時 | 不可有 `classes.sjc`、KN 側檔（單位不在裡面）：`decode_units.UnitIndex` 載入時丟錯。評測一律 `--no-classes --no-kn --no-tri`（契約本來就這樣）。`ra_eval.py` 對有單位的模型不給 `--units` 就停（否則單位全被當成未知詞，結果錯得很安靜） |
| 書面設定的 trigram | `decode3` 沒有延伸（B 兩臂都 `--no-tri`）。降權表走詞庫物件，`lm.decode` 與 `decode3` 都讀 `lex.demote`，所以 A1／A2 在有 trigram 的出貨形狀上也有效（`test_eval.MergedTable`） |
| 「單獨成詞」 | 探針、桶表(iii)、桶表(i)(ii)：依該句的讀音，用詞庫分數做最高分切分（`rcommon.segment`），切成單字的位置。BASE 自己的切法不在 `--dump` 裡，所以桶表(i)(ii) 是這個近似；被詞庫多字詞蓋住的字不算 |
| 第 3 級讀音 | 單獨成詞、字有 ≥ 2 個詞庫讀音、第一名用的讀音不是主要讀音、而且去掉聲調後的音節和主要讀音的不同（長 ㄓㄤˇ 對 ㄔㄤˊ 是；嗎 ㄇㄚˇ 對 ㄇㄚ˙ 不是，那是同音不同調）。主要讀音要 `--hetero` 才含規則 3 |
| A2 的 BASE 欄 | 契約寫「在 A1 之上」：A2 的表用 A1 當 BASE、A1＋δ_tone 當 CAND（只量一聲派的效果）；要對 BASE 看另有一份 A1 對 BASE 的表 |
| 摘要行的標籤 | 沒有額外表、δ_tone ＝ 0、模型沒有單位：和 `lm_eval.py` 逐字相同（錨點）。否則在 profile 欄尾端加 `+dm:<額外表檔案串接的 SHA-256 前 8 碼>`、`:t<δ_tone>`、`+units`（模型含單位時）。`+units` 不影響錨點：B0 沒有單位 |

## 輸入與要記進研究紀錄的雜湊

| 輸入 | 怎麼取 |
|---|---|
| repo commit | `git -C $REPO rev-parse HEAD`，不可有 `+dirty`（`count_units`／`build_model` 的 JSON 也記） |
| 出貨模型 | `sha256sum $REPO/data/lm/{bigram.sjlm,kn.sjkn,classes.sjc,trigram.sjt3}`，核對 `data/*.sha256` |
| 詞庫與降權表 | `sha256sum $REPO/data/lexicon/{mcbpmf-data.txt,overlay-add.tsv,sandhi-add.tsv,names-add.tsv,demote.tsv}` |
| 集合 | `python3 $D/common/setnames.py list`（列數、SHA-256、釘死值是否相符）。`user-reported` 用開工時 main 上的全部列，雜湊記進研究紀錄，這一片之內不換 |
| A1 表 | `sha256sum $OUT/a1.tsv`（再來一份 δ_big 不是 1.0 的就另存另記） |
| 計數輸入 | 同 `experiments/derisk-1015/e1/README.md` 的表（維基 dump、`colloquial-train.txt`、`synth.txt`、`topup.txt`、4d47a50 的 `overlay-add.tsv`、acg／names 計數詞表）；`count_units` 的 `meta-*.json` 會寫每個輸入的 SHA-256，和 model-v6 建置機的 `inputs.sha256` 逐一比對 |
| 讀音來源 | `sha256sum $HETERO $REPO/tools/reading_overrides.tsv`（`heterophony1.list` 在維護者機器的 `ime-research` 下，傳到 Colab 要核對雜湊） |
| 模型 | `$M/b0id/bigram.sjlm`（要以 `f7777be3` 開頭）、`$M/b0u/bigram.sjlm`；`build.json` 有 V、條目數、位元組、`unit_types`、SHA-256 |

## 執行手冊

變數（每台機器自己設；WSL 是 bash）：

```
REPO=<這個 commit 的 checkout，data/lm 在裡面>     RA=$REPO/experiments/reading-aware     D=$REPO/experiments/derisk-1015
OUT=<repo 外的輸出資料夾>     PY="python3 -W ignore"     export PYTHONUTF8=1
git -C $REPO rev-parse HEAD
```

### A. Mac（不重運算）

```
$PY -m unittest discover -s $RA -p 'test_*.py'      # 上面的單元測試
$PY $RA/a1_table.py --out $OUT/a1.tsv                         # 44 列；之後給維護者過目並凍結
sha256sum $OUT/a1.tsv
```

清單送維護者過目後才凍結；改了清單就改 `a1_table.DEMOTE_44` 與契約。

### B. 188（Windows 的 WSL）：評測

共同函式（所有指令照這個樣子）：

```
SETS="cvtune wikitune dev302 typing76 user-reported lytune wikitypo"      # setnames.ORDER；判讀五集是後五個
JUDGE="dev302 typing76 user-reported lytune wikitypo"
rows_of() { $PY $D/common/setnames.py path $1; }
# ref ARM PROFILE NAME ROWSFILE [lm_eval 選項…]：lm_eval.py 本身（BASE 與錨點用）
ref() { a=$1; p=$2; n=$3; r=$4; shift 4; mkdir -p $OUT/$a
  $PY $REPO/reference/proto/lm_eval.py --profile $p --rows $r --name $n --context --rowstats $OUT/$a/$n.$p.rs --dump $OUT/$a/$n.$p.dump "$@" > $OUT/$a/$n.$p.out; }
# run ARM PROFILE NAME ROWSFILE [ra_eval 選項…]：評測包裝
run() { a=$1; p=$2; n=$3; r=$4; shift 4; mkdir -p $OUT/$a
  $PY $RA/ra_eval.py --profile $p --rows $r --name $n --context --rowstats $OUT/$a/$n.$p.rs --dump $OUT/$a/$n.$p.dump "$@" > $OUT/$a/$n.$p.out; }
cat_rs() { out=$1; a=$2; p=$3; shift 3; files=""; for s in "$@"; do files="$files $OUT/$a/$s.$p.rs"; done; $PY $D/common/concat_rowstats.py concat $out $files; }
```

BASE 的模型是出貨的（`data/lm`，`classes.sjc`、`kn.sjkn`、`trigram.sjt3` 自動載入）；不加 `--extra-overlay`、不開詞包。每個函式一次一個集合×一種設定；集合多，用 `xargs -P`／多個 shell 分開跑（188 不限程序數；從 SSH 啟動的程序要先關 power throttling，見維護者的記憶 host-188-gpu）。

1. **BASE 與評測錨點（§3.1；全部集合、兩種設定）**

   ```
   for p in chat formal; do for s in $SETS; do
     ref base $p $s "$(rows_of $s)"
     run anchor1 $p $s "$(rows_of $s)"          # 不加任何表、δ_tone 0、不給 --units
   done; done
   for p in chat formal; do
     cat $(for s in $SETS; do echo $OUT/base/$s.$p.out; done) > $OUT/base.$p.all
     cat $(for s in $SETS; do echo $OUT/anchor1/$s.$p.out; done) > $OUT/anchor1.$p.all
     $PY $D/common/anchor.py $OUT/anchor1.$p.all $OUT/base.$p.all; echo "anchor1 $p rc=$?"
   done
   ```

   `anchor ok`（rc 0）才往下；rc 1＝停止條件。也核對 rowstats：`cmp $OUT/base/$s.$p.rs $OUT/anchor1/$s.$p.rs`。

2. **第 0 步：量 δ_big 與桶表（§3.2、§3.0）**

   ```
   # 第 29、94 列的分差：看 base 的 --dump 前幾名裡含「巴」的與含「吧」的候選的分數差，兩種設定都 < 1.0 就用 1.0；否則 δ_big = 最大分差 + 0.1（事先寫定）
   for p in chat formal; do awk -F'\t' '($1==29 || $1==94) && $2<=8' $OUT/base/user-reported.$p.dump; done
   # 桶表（每種設定一次；只出數字）
   for p in chat formal; do
     $PY $RA/buckets.py --label $p --hetero $HETERO \
       $(for s in $SETS; do echo --set $s "$(rows_of $s)" $OUT/base/$s.$p.dump; done) > $OUT/buckets.$p.tsv
   done
   ```

   δ_big 不是 1.0：`$PY $RA/a1_table.py --out $OUT/a1.tsv --delta <值>`，並記下（另一份雜湊）。discordtune 的 BASE 與桶表由 main 在放私人資料的機器上跑，同樣的指令，只回數字。

3. **A1（§3.2；全部集合、兩種設定）**

   ```
   for p in chat formal; do for s in $SETS; do run a1 $p $s "$(rows_of $s)" --extra-demote $OUT/a1.tsv; done; done
   # 驗收 3：第 29、94 列的第一名都是「吧」
   for p in chat formal; do awk -F'\t' '($1==29 || $1==94) && $2==1' $OUT/a1/user-reported.$p.dump; done
   # 每個集合一行 --net 表（BASE=base，CAND=a1）；判讀五集串接另有一行
   for p in chat formal; do
     for s in $SETS; do $PY $REPO/tools/evalstats.py compare $OUT/base/$s.$p.rs $OUT/a1/$s.$p.rs --net --label "A1 $p $s"; done
     cat_rs $OUT/base.$p.judge.rs base $p $JUDGE; cat_rs $OUT/a1.$p.judge.rs a1 $p $JUDGE
     $PY $REPO/tools/evalstats.py compare $OUT/base.$p.judge.rs $OUT/a1.$p.judge.rs --net --label "A1 $p judge5"
     for s in $SETS; do echo -n "$p $s: "; $PY $RA/verdict.py $OUT/base/$s.$p.rs $OUT/a1/$s.$p.rs --stop-on-negative; echo "rc=$?"; done
   done
   ```

   驗收：dev302、typing76、user-reported 每格「改壞」是 0（`verdict.py` 第二欄）；其他集合沒有 `negative`（rc 5＝停止條件）。表上標明 dev302、typing76、user-reported 是真人打的讀音，其餘四集的讀音是 `tools/readings.py` 產生的。

4. **A2（§3.3；δ_tone ∈ {0.25, 0.5}）**

   ```
   # 探針列：現算，寫在 $OUT（不進 repo）。四個公開集合；discordtune 由 main 在私人機器上同樣處理
   mkdir -p $OUT/probe
   for s in dev302 typing76 user-reported cvtune; do
     $PY $RA/level1.py probe --rows "$(rows_of $s)" --out $OUT/probe/$s.txt --idx $OUT/probe/$s.idx     # 印：集合、列數、原本就是一聲的列、探針列
     $PY $RA/level1.py onetone --rows "$(rows_of $s)" --idx $OUT/probe/$s.one.idx
   done
   for t in 0.25 0.5; do for p in chat formal; do for s in $SETS; do
     run a2_$t $p $s "$(rows_of $s)" --extra-demote $OUT/a1.tsv --delta-tone $t
   done; done; done
   # 探針列上：BASE、A1、A1＋δ_tone
   for p in chat formal; do for s in dev302 typing76 user-reported cvtune; do
     ref  pbase $p $s "$OUT/probe/$s.txt"
     run  pa1   $p $s "$OUT/probe/$s.txt" --extra-demote $OUT/a1.tsv
     for t in 0.25 0.5; do run pa2_$t $p $s "$OUT/probe/$s.txt" --extra-demote $OUT/a1.tsv --delta-tone $t; done
   done; done
   ```

   - **前提（S2r，`docs/methodology.md`）**：BASE 在探針列上，對調後對調前的 top1 差：
     `$PY $D/common/concat_rowstats.py select $OUT/before.$p.$s.rs $OUT/base/$s.$p.rs $OUT/probe/$s.idx` 然後 `evalstats compare $OUT/before.$p.$s.rs $OUT/pbase/$s.$p.rs --net`。Δtop1 的 95% 區間半寬 ÷ 1.96 ≈ 一個配對標準誤；差距小於它就是旋鈕沒有意義，照實寫。
   - **每一檔兩張 `--net` 表**（聊天、書面各一行）：原集合 `evalstats compare $OUT/a1/$s.$p.rs $OUT/a2_$t/$s.$p.rs --net`（BASE=A1，CAND=A1＋δ_tone）；探針集 `evalstats compare $OUT/pa1/$s.$p.rs $OUT/pa2_$t/$s.$p.rs --net`。分開標「原本就是一聲的列」（`$OUT/probe/$s.one.idx`，用 `concat_rowstats.py select` 取子集）與「探針換成一聲的列」。
   - **第 88 列**（user-reported）：哪一檔開始改對：`for t in 0.25 0.5; do awk -F'\t' '$1==88 && $2==1' $OUT/a2_$t/user-reported.$p.dump; done`，和 `$OUT/a1/…` 比。
   - 不設通過門檻；維護者看表選 δ_tone（含 0）。

5. **B 的評測（§3.4；Colab 算出模型之後）**

   兩臂都 `--no-classes --no-kn --no-tri`。模型 `$M/b0id`（恆等標注，= B0）與 `$M/b0u`（單位）放到 188。

   ```
   B0=$M/b0id/bigram.sjlm; B0U=$M/b0u/bigram.sjlm; FLAGS="--no-classes --no-kn --no-tri"
   # 解碼錨點（§3.4；全部集合、兩種設定）：B0 上，延伸解碼對 lm_eval.py
   for p in chat formal; do for s in $SETS; do
     ref  banchor_ref $p $s "$(rows_of $s)" --lm $B0 $FLAGS
     run  banchor_ext $p $s "$(rows_of $s)" --lm $B0 $FLAGS --units
   done; done
   for p in chat formal; do
     cat $(for s in $SETS; do echo $OUT/banchor_ref/$s.$p.out; done) > $OUT/banchor_ref.$p.all
     cat $(for s in $SETS; do echo $OUT/banchor_ext/$s.$p.out; done) > $OUT/banchor_ext.$p.all
     $PY $D/common/anchor.py $OUT/banchor_ext.$p.all $OUT/banchor_ref.$p.all; echo "decode anchor $p rc=$?"
   done
   # 對照臂：B0 加 A1 表；候選臂：B0u，不加表
   for p in chat formal; do for s in $SETS; do
     run bctrl $p $s "$(rows_of $s)" --lm $B0  $FLAGS --extra-demote $OUT/a1.tsv
     run bcand $p $s "$(rows_of $s)" --lm $B0U $FLAGS --units
   done; done
   for p in chat formal; do
     for s in $SETS; do $PY $REPO/tools/evalstats.py compare $OUT/bctrl/$s.$p.rs $OUT/bcand/$s.$p.rs --net --label "B $p $s"; done
     cat_rs $OUT/bctrl.$p.judge.rs bctrl $p $JUDGE; cat_rs $OUT/bcand.$p.judge.rs bcand $p $JUDGE
     $PY $REPO/tools/evalstats.py compare $OUT/bctrl.$p.judge.rs $OUT/bcand.$p.judge.rs --net --label "B $p judge5"
     echo -n "$p judge5: "; $PY $RA/verdict.py $OUT/bctrl.$p.judge.rs $OUT/bcand.$p.judge.rs
   done
   ```

   判讀（§4.5）：判讀五集串接至少一種設定 `positive`、另一種不是 `negative`；discordtune（main 跑）兩種設定都不是 `negative`。
   - **另外單獨報**：
     - 一聲打語尾字的列（B0u 對對照臂）：真人集合 `evalstats compare` 前用 `concat_rowstats.py select` 取 `$OUT/probe/$s.one.idx` 的子集；探針列同樣評測（`run bctrl/bcand … "$OUT/probe/$s.txt"`，同上面的旗標）各一行 `--net`。
     - 改壞的列落在「用占比」的字上：
       `$PY $RA/share_rows.py --lm $B0U $(for s in $JUDGE; do echo --set $s "$(rows_of $s)" $OUT/bctrl/$s.chat.rs $OUT/bcand/$s.chat.rs $OUT/bctrl/$s.chat.dump $OUT/bcand/$s.chat.dump; done)`（書面同理）。
   - 模型大小、詞彙數、條目數：兩個 `build.json`；詞彙數比 `build_model.py --b0` 已印。

### C. Colab G4：計數與建模（契約 §5：單次超過 4 小時就停）

輸入與 model-v6 相同，見 `experiments/derisk-1015/e1/README.md` 的表；`HETERO` 傳到 Colab 後先核對雜湊。計數用的詞表（`<acg…>`、`<names…>`）和 e1 README 的一樣。

```
WORK_ID=<新的空資料夾>   WORK_LAB=<新的空資料夾>   M=<模型輸出>   OVL=$WORK_ID/overlay-add-4d47a50.tsv
git -C $REPO show 4d47a50:data/lexicon/overlay-add.tsv > $OVL ; sha256sum $OVL     # 要等於 96b8db89…1620
EXTRA="--extra-lexicon <acg 計數詞表> --extra-lexicon <names 計數詞表>"

# 1. 恆等標注：計數錨點（約 15 分鐘 ×2）
timeout 4h $PY -u $RA/count_units.py text --out $WORK_ID --tag colloquial3 --overlay-add $OVL $EXTRA --identity <colloquial-train.txt> <synth.txt> <topup.txt>; echo rc=$?
timeout 4h $PY -u $RA/count_units.py wiki --out $WORK_ID --articles 200000 --procs 44 --overlay-add $OVL $EXTRA --identity; echo rc=$?
$PY $RA/build_model.py --counts $WORK_ID/unmapped --out $M/b0id --expect-sha256 f7777be3; echo "count anchor rc=$?"

# 2. 單位標注
timeout 4h $PY -u $RA/count_units.py text --out $WORK_LAB --tag colloquial3 --overlay-add $OVL $EXTRA --hetero $HETERO <colloquial-train.txt> <synth.txt> <topup.txt>; echo rc=$?
timeout 4h $PY -u $RA/count_units.py wiki --out $WORK_LAB --articles 200000 --procs 44 --overlay-add $OVL $EXTRA --hetero $HETERO; echo rc=$?
$PY $RA/build_model.py --counts $WORK_LAB/unmapped --out $M/b0u --b0 $M/b0id/bigram.sjlm; echo "vocab rc=$?"
```

- `rc=124` 是超過 4 小時；計數錨點 `rc=2`；詞彙數 `rc=4`。
- 維基計數用 `imap_unordered`，累加順序不固定：位元組不一定可重現。恆等標注的模型雜湊若不是 `f7777be3…`，先看 `meta-*.json` 的輸入雜湊與 `e1/README.md` 的「重現核對」（`compare_counts.py`），不要直接當成程式錯。
- `meta-*.json` 的 `label_fallbacks` 是「規則給的讀音詞庫沒有、改標主要讀音」的次數（逐字）；`unit_types` 是帶讀音的單位種類數。

### D. 標注錯誤率（§3.4、§5；公開語料，在放 `~/.cache/shanjie/sources` 的機器上）

```
$PY $RA/sample_labels.py sample --out $OUT/label-wiki.tsv --seed 20261017 --n 150 --hetero $HETERO --wiki --articles 200000
$PY $RA/sample_labels.py sample --out $OUT/label-spoken.tsv --seed 20261017 --n 150 --hetero $HETERO --text <colloquial-train.txt>
cat $OUT/label-wiki.tsv $OUT/label-spoken.tsv > $OUT/label-sample.tsv       # 每一行最後一欄由 main 填 ok 或 x
$PY $RA/sample_labels.py rate $OUT/label-judged.tsv; echo rc=$?
```

每行一個「短名單字（得 地 還 重 空 長 行）單獨成詞」的位置（單獨成詞 = 該漢字段詞庫最高分切法裡的單字），欄位：句號、來源、句子、位置、字、標注、前後三字的上下文、判讀。`rate` 印整體與逐字的錯誤率，整體 > 10% 結束碼 3（§5）。

## 停止條件（§5）對應

| 條件 | 看哪裡 |
|---|---|
| 任何錨點不相同 | 評測／解碼錨點：`anchor.py` 印 `ANCHOR MISMATCH: …` 並結束碼 1（上面 B-1、B-5 的 `rc=`）；rowstats 另用 `cmp`。計數錨點：`build_model.py --expect-sha256 f7777be3` 印 `STOP: counting anchor…` 並結束碼 2 |
| 標注人工抽樣整體錯誤率 > 10% | `sample_labels.py rate` 結束碼 3（stderr `STOP: …`）；分母是 300 句裡短名單字單獨成詞的全部位置，逐字的錯誤率只報 |
| B0u 的詞彙數比 B0 多超過 5% | `build_model.py --b0 …` 印 `ratio`，> 1.05 時結束碼 4 |
| A1 在任何集合顯著為負 | `verdict.py … --stop-on-negative` 結束碼 5；等價於 `evalstats compare` 那行的「改壞 > 改對」且 p < 0.05 |
| Colab 單次超過 4 小時 | 計數指令外面包 `timeout 4h`，結束碼 124 |
| 需要讀私人訊息的原文 | 這些工具都不讀私人原文：`buckets.py` 與 `verdict.py` 只印數字；discordtune 由 main 在私人機器上跑同樣的指令，只帶回 `compare` 的那一行與桶表數字；`sample_labels.py` 的句子只來自公開語料 |

## 單元測試涵蓋（契約 §4.1）

| 項目 | 測試 |
|---|---|
| 降權表產生器：只產 44 字的 ˙ 列、δ、每列都在詞庫、缺一種讀音就停 | `test_tables.A1Table`（含真的詞庫：44 列、清單外的 23 字都不在） |
| 第 1 級列：只選同音節的一聲（嗎 ㄇㄚˇ、嘛、喔、哪不算；真的詞庫剛好是 吧嗎呢啦啊呀嘞哩） | `test_tables.Level1` |
| 探針 `flip`：只換單獨成詞的語尾字、位置已是一聲的不動、一列換掉全部可換位置、沒得換的略過 | `test_tables.Level1` |
| 單位標注：多字詞不拆、單一讀音的字不加讀音、語尾字、每條前文規則一正一反（含 p2 ＝ p 在句首不誤判）、每個單位的讀音都在詞庫、同一位置標注相同、規則給的讀音詞庫沒有時改標主要讀音並計數 | `test_units.Labels`、`test_units.Counting` |
| 單位的解碼：單位存在／占比／詞彙外；裸鍵模型和 `lm.decode` 逐位元相同（兩種設定、有無降權表、多個前文、`beam`）；只有 `字 U+0001 R1` 的模型對 R2 乘占比 | `test_decode` |
| 錨點的單元測試版：評測包裝（無表）、延伸解碼（無單位的模型）對 `lm_eval.py` 的摘要行、rowstats、`--dump` 逐位元組；恆等標注的 `expected_counts`、加法計數、整批計數對 E1 的 `Acc`；恆等計數建出的模型和 E1 的路徑逐位元組相同 | `test_eval.Wrapper`、`test_units.Counting`、`test_units.ModelAnchor` |
| 降權表兩條解碼路徑（`lm.decode`、`decode3`）都看得到；重複的鍵停下來；表真的經由包住的 `cap_overlay` 進到 `lm_eval.main`（換 `DEMOTE_TSV` 沒用：`lm_eval` 用預設參數呼叫，預設值在函式定義時就綁死了） | `test_eval.MergedTable`、`test_eval.Wrapper` |
