# E1（trigram 放寬模型上限、KN 重調、壓縮）與 E4 (b)（計數與解碼一起改）

契約：`docs/contracts/derisk-1015.md` §2、§3 E1、§3 E4 (b)、§4、§5。這個資料夾是 executor 寫的全部程式與單元測試；計數、建模、格點、壓縮量測由 main 在 Mac 以外的機器跑（契約 §1）。不改核心、`reference/proto/`、`tools/`、`experiments/s2/`（只 import）。

單元測試（合成資料，Mac 可跑，約 2.5 分鐘；輸出寫檔、看結束碼）：

```
cd <REPO> && python3 -W ignore experiments/derisk-1015/e1/test_e1.py > /tmp/e1-test.txt 2>&1; echo rc=$?; tail -3 /tmp/e1-test.txt
```

## 檔案

| 檔 | 做什麼 |
|---|---|
| `mapping.py` | E4 (b) 的**唯一**切分函式 `tokenize`：漢字段／數字段或英文字母段（每段一個 `<X>`）／其他。計數（`han_runs_with_left`）與解碼（`ends_with_x`）都經由模組呼叫它 |
| `count.py` | E1a。包 `experiments/s2/build_counts.py`、`build_counts_text.py`（import，不改）：一次輸出 uni／bi／**tri**（未對應）與同一趟的對應版 uni／bi |
| `sjlm_io.py` | SJLM 與 trigram 表 `SJT30001` 的 numpy 讀寫（格式寫在檔頭） |
| `trimodel.py` | trigram 表（建、剪、載入、查）與 `TriLM`：B0 加上 trigram 層的打分（雙歷史詞） |
| `build_models.py` | E1b。`bigram`：B0／B0m（`tools/build_lm.py` 配方）加 KN 側檔（kn-v1）；`tri`：各剪枝門檻的 trigram 表與大小表 |
| `harness.py` | `reference/proto/lm_eval.py` 的延伸（`--tri`、`--lam`、`--beta`、`--x-context`）；零設定時與它逐字相同 |
| `anchor.py` | §2 錨點：對同一個模型、同組選項跑 `lm_eval.py` 與 `harness.py`，摘要行（含 `top1_sha256`）與 rowstats 必須逐字／逐位元組相同 |
| `grid.py` | `select`：15 格 λ×β、凍結選擇 JSON；`judge`：在固定點對各集合寫 rowstats。可多程序 |
| `rowstats_concat.py` | 依契約順序串接 rowstats（列號從 1 重編） |
| `subset_rowstats.py` | E4 子集合（前文含句中標點、數字或英文）的 rowstats，只報不判 |
| `compress.py` | E1c。(i) 無損與 (ii) 8／4 位元對數級距量化的寫出、讀回與大小報告 |
| `common.py` | 路徑、SHA-256、commit 紀錄 |
| `test_e1.py` | 單元測試 |

## 契約沒寫死、我做的決定

| 項目 | 做法與理由 |
|---|---|
| trigram 插值 | **第 10 輪的 absolute discounting**（D = 0.75，和 bigram 層同一個 D）。`P3(w｜u,v) = max(c−D,0)/t3(u,v) + back3(u,v)·P2(w｜v)`，`t3` 是**剪枝前**的前文總次數，`back3 = 1 − Σ保留條目(c−D)/t3`；沒存的前文就是 `P2`。P2 是 B0 自己的機率（含 KN 回退與類別項）。理由：契約把 T_k 定義成「B0 加一張 trigram 表」。KN 式接續次數要把低階分布換成 N1+(·v w)，B0 就不再是 T_k 的低階，「零設定＝B0」的對照（與 §2 錨點）也不成立；absolute discounting 讓表可以任意剪而分布仍歸一（`test_e1` 對整個詞彙加總驗 1e-9，並和 `experiments/s2/iter2.py` 的 `LM`（order 3）逐 (u,v,w) 比對）|
| 變體合併 | trigram 照 `build_lm.merge_variants`：三個位置各自對應到類代表，前兩個歷史詞寫給類的每個成員，預測詞只放代表成員 |
| 檔案大小的量法 | **總大小 ＝ `bigram.sjlm` 的實際位元組 ＋ `.sjt3` 的實際位元組**。`.sjt3` ＝ 64 ＋ 20 × 前文數 ＋ 8 × 條目數（條目 `next_id u32 + count u32` ＝ 8 bytes，和 SJLM 相同；每個前文 `key u64 + total u64 + off u32` ＝ 20 bytes；64 ＝ 檔頭 60 ＋ 結尾的 `off`）。不含 `kn.sjkn`（1.4 MB，各檔相同）、詞類表、tinylm。`tri-sizes.json` 列每一檔的前文數、條目數、位元組，另有 1…256 門檻的大小曲線 |
| 記憶體估算 | 〔推論〕載入後 ≈ 檔案大小（numpy 直接用檔案 mmap，多程序共用頁面快取）加 Python 端 `BigramLM` 的字典；B0 自己的字典沒有量。表裡寫「估算」|
| 剪枝門檻怎麼選 | 整數門檻（加權、四捨五入後的次數 ≥ thr 才保留）。`--targets-mb 100,200,400` 對每個目標選總大小最接近（對數）的整數門檻，再加不剪（thr 1）；不足 4 檔就補 thr 2, 3, …。門檻小一格大小就差很多，所以實際大小只是「約」；`--thresholds 1,3,8,20` 可以直接指定。**實際選了哪些門檻、大小多少以 `tri-sizes.json` 為準，由 main 看過再跑格點** |
| 解碼器雙歷史詞 | 狀態 `(u, v)`。重組鍵：零設定沿用 `lm.decode` 的 `(表面字串, 最後一詞)`；有 trigram 時改 `(表面字串, 前一詞, 最後一詞)`（下一個分數依兩個詞，用舊鍵會錯併路徑）。所以 T_k 的光束內容和 B0 不完全相同，這是 trigram 的一部分，不是漂移 |
| 前文起始狀態 | 沒有前文：`(<s>, <s>)`（和計數時的 `(<s>,<s>,w1)` 對得上）。`--context` 給了一個歷史詞 x：`(None, x)`，第一個詞只用 B0（沒有第二個歷史詞可查；用 `(<s>, x)` 會去查「x 在句首」的計數，意思不對），第二個詞起 `(x, w1)` 照常查 trigram |
| λ、β 覆寫 | 一份 `kn.sjkn` 的 β 在檔頭。格點的 β 用 `Evaluator.set_beta` 在載入後覆寫 `lm.kn`，不必產三份側檔；非零設定時摘要行最後加 `+lam:`、`+beta:`。β 覆寫後 trigram 的字分數快取會清掉（`test_e1` 驗） |
| 同分取最近 | 契約只寫「同分取離出貨值（λ、β = 1）最近的那格」，沒給距離。用**格距 L1**（λ 以 0.05、β 以 0.25 為一步），再同分取 \|Δλ\| 小的、再取 β 大的（`grid.distance`）。15 格的分數都存在選擇 JSON 裡，要換規則可以重選，不必重跑 |
| `--context` | 格點與 judge 一律 `--context`（契約 §2）|
| B0 沒有詞類表 | 契約的 B0 只有 bigram＋KN 側檔，沒說詞類表；`classes.sjc` 綁模型雜湊，出貨的那份不能用。B0、T_k、B0m 都用 `--no-classes`，兩臂一致。**出貨 model-v6 的對照臂（說明重算計數與私有條目的差異）用有詞類表的出貨設定，所以那一行同時含「沒有詞類表」的差異**，表裡要寫明。若 main 要讓 B0 也有詞類表：`tools/build_classes.py --lm B0 …` 用 model-v6 步驟 4–5 的 `cls-40000-512-forced.npz` 重建（我沒做，契約沒要求）|
| 對應版（E4 (b)）的定義 | 「每段數字、每段英文」＝連續的 `[0-9０-９]` 一段、連續的 `[A-Za-zＡ-Ｚａ-ｚ]` 一段，各自一個 `<X>`（`iPhone15` 是兩段）。計數：一個漢字段（≥ 2 字，和原本一樣）若**緊貼**在數字／英文段右邊（中間沒有空白或標點），它的 `(<s>, a)` 改成 `(<X>, a)`，`<X>` 的 unigram ＋1；右邊界仍是 `</s>`（解碼只用左文；若改成 `<X>` 會少掉 `(a, </s>)` 次數）。對應版不含 trigram。`<X>` 的次數會加進 N（比例很小，`meta-*.json` 有 `x_runs`）|
| 解碼的 `<X>` | `--x-context`：前文**最後一個** token 是數字／英文段時，第一個詞的歷史是 `<X>`（模型要有 `<X>` 的歷史紀錄，否則退回 `<s>`；未對應的模型直接報錯）。句中標點之後的處理是 E4 (a)，不在這裡 |
| trigram 剪枝的計數門檻 | `count.py --tri-min`，預設 0.01，和 bigram 的 `EXPECTED_MIN` 相同（一段內路徑後驗低於它不記）。記憶體不夠時才調大，並在表裡寫明 |
| 壓縮的級距 | 每張表（bigram、trigram）一組級距，`2**bits` 個代表值：相異次數不超過級距數時全部保留（無損）；否則 cmin…m 的整數是各自一級、m 以上是等比階梯（比值 ≥ 1 + 1/m，取整後仍相異），一個次數歸到對數尺度最近的級。級距編號對次數單調，代表值落在自己的級距內（`test_e1` 驗）。只量化後繼詞的次數；前文總次數、unigram、後繼 id 都是精確的 |
| 有損讀回的補正 | 量化後一個前文的保留次數總和可能超過它（精確的）總次數，回退權重會變負。讀回時把該前文的總次數改成 `max(總次數, 保留次數總和)`；只動被進位撐破的前文。寫在 `compress.py` 檔頭，`test_e1` 驗 |
| 讀回的檔 | 無損：位元組與原檔相同（SHA-256 相同，所以 `kn.sjkn` 照用）。有損：`bigram.sjlm` 的雜湊變了，`decode --kn/--classes` 把側檔的雜湊欄改成新模型的，trigram 表也綁到新模型 |

## 契約留給 main 的、以及我沒做的

| 項目 | 狀態 |
|---|---|
| 〔未做〕「T_k 接上 tinylm-v5 對 B0 接上 tinylm-v5」（契約 E1b「對照（只報）」） | 沒做。`harness.py --dump` 的格式和 `lm_eval.py` 相同，但 `experiments/s7a-tinylm/run_sets.py` 寫死呼叫 `lm_eval.py`，要換成 `harness.py` 才能對 T_k 的候選重算 tinylm 分數；那是 tinylm 的分數檔流程，不是 E1 的判讀，需要時另開 |
| 〔未做〕E1 的延遲與載入時間 | 契約本來就不量（屬於 Rust 那一片）|
| 〔未量〕時間 | 格點的牆鐘時間**沒有量**（Mac 禁止跑重運算）。可用的數字只有：本機 `lm_eval.py` 對出貨模型跑 20 列含載入共 5 秒（含載入，所以每列遠小於 0.25 秒；載入本身約 5–8 秒）；trigram 查詢在 252 MB 的合成隨機表上每次約 2.4（找前文）到 3.7（再找後繼詞）微秒。先用 `grid.py select --limit 50` 在目標機器量一次再排程；整個 E1 超過 8 小時是停止條件（契約 §5）|
| 〔已量〕每個格點程序的記憶體 | 載入出貨模型（含詞類表與詞庫）的程序峰值 RSS 約 1.6 GB（Mac，`harness.Evaluator`）；16 個程序約 26 GB，記憶體不夠就減 `--procs`。trigram 表是 mmap，各程序共用 |
| 〔未量〕Colab 計數的記憶體 | trigram 的 `Counter` 在父程序累加，3 元組鍵每個相異條目約 200 B，相異條目數沒有量過；〔推論〕100–200 M 個、約 40–80 GB。看 RSS，逼近 140 GB 就停下來，改用 `--tri-min 0.05` 並在紀錄寫明 |
| `--extra-lexicon` 清單 | model-v6 的計數詞表（`acg-count.tsv` 與 names 的 `count_lexicon.py` 輸出，見契約 `docs/contracts/model-v6.md` §2.1）和補次數檔的路徑在 main 的建置機上，我不知道；下面用 `<…>` 代替，要和 model-v6 那次完全相同 |
| 私人集合 | `discordtune` 只由 main 跑：同一組指令多一個 `--set discordtune=<路徑>`，在放私人資料的機器上，只帶回表格數字 |

## 執行手冊

變數（每台機器自己設）：

```
REPO=<這個 commit 的 checkout，data/lm 在裡面>      E1=$REPO/experiments/derisk-1015/e1
WORK=<計數輸出，新的空資料夾>   M=<模型輸出>   R=<結果輸出>
export PYTHONUTF8=1
git -C $REPO rev-parse HEAD      # 記進研究紀錄；每個輸出 JSON 也會寫 commit（有未提交改動會有 +dirty，那種輸出不採用）
```

### 1. 計數（E1a，Colab G4：177 GB、48 執行緒；契約 §5：超過 4 小時就停）

輸入與 model-v6 那次相同（`experiments/model-v6/README.md` 步驟 1、`experiments/model-v5/README.md` 的雜湊表）。`count.py` 會把每個輸入的 SHA-256 寫進 `$WORK/meta-*.json`；請和 model-v6 建置機的 `inputs.sha256` 逐一比對，不同就停：

| 輸入 | SHA-256 |
|---|---|
| `zhwiki-20261001-pages-articles.xml.bz2` | `5db9052e…7e2f` |
| `colloquial-train.txt` | `3e833d06…b4db` |
| `synth.txt` | `bec7a6a2…99f1` |
| 補次數檔（`topup.txt`） | `315939d7391205a2ea738504e71c4a7fd702f7ef3ee892d197ec177205814d54` |
| `mcbpmf-data.txt` | `0deae7b7…b7ac` |
| 計數用 `overlay-add.tsv`（commit `4d47a50` 的檔） | `96b8db8914d7f71afbc74f3997ac661354997881f50b4aba0f163976bede1620` |
| `TWVariants.txt`、`STCharacters.txt` | `245b94eb…cb86`、`a0ca1601…582b`（`build_counts.py` 讀檔前自己核對） |

```
git -C $REPO show 4d47a50:data/lexicon/overlay-add.tsv > $WORK/overlay-add-4d47a50.tsv   # 先算 sha256sum，要等於上表
OVL=$WORK/overlay-add-4d47a50.tsv
python3 -u $E1/count.py text --out $WORK --tag colloquial3 --overlay-add $OVL \
    --extra-lexicon <acg 計數詞表> --extra-lexicon <names 計數詞表> <colloquial-train.txt> <synth.txt> <topup.txt>
python3 -u $E1/count.py wiki --out $WORK --articles 200000 --procs 44 --overlay-add $OVL \
    --extra-lexicon <acg 計數詞表> --extra-lexicon <names 計數詞表>
```

產出：`$WORK/unmapped/{counts-200000,counts-colloquial3}.pkl`（uni／bi，和 `experiments/s2` 同形，`build_lm.py` 直接吃）、`unmapped/tri-*.pkl`（trigram，另檔）、`mapped/counts-*.pkl`（E4 (b)）、`meta-*.json`。

重現核對（建議；不一致不是停止條件，但要記）：`python3 $REPO/experiments/model-v5/compare_counts.py <model-v6 的 counts-200000.pkl> $WORK/unmapped/counts-200000.pkl`（口語檔同理）。維基計數是 `imap_unordered`，位元組不可重現，比內容。

### 2. 建模型（任何有 ≥ 64 GB 記憶體的機器；`tri` 這步最重）

```
python3 -u $E1/build_models.py bigram --counts $WORK/unmapped --out $M/b0      # B0 + kn.sjkn（theta 1、beta 1）
python3 -u $E1/build_models.py bigram --counts $WORK/mapped   --out $M/b0m     # B0m + kn.sjkn（E4 (b)）
python3 -u $E1/build_models.py tri --counts $WORK/unmapped --lm $M/b0/bigram.sjlm --out $M/tri --targets-mb 100,200,400
cat $M/tri/tri-sizes.json | python3 -c "import json,sys; [print(t) for t in json.load(sys.stdin)['tables']]"
```

`build.json` 記 `bigram_sha256`：若和 model-v6 步驟 2 的 `bigram-v6-nofp.sjlm` 的 SHA-256 相同，就是計數重現成功（沒有私有條目）；不同的話記下差了幾個條目。看過 `tri-sizes.json` 的門檻與大小（要 ≥ 4 檔、約 100／200／400 MB 與不剪），不滿意就用 `--thresholds` 重跑那一步。下面用 `T<thr>` 當標籤（例：`T6`），`tri-t<thr>.sjt3` 是檔名。

### 3. 錨點（§2；不同就是停止條件）

```
python3 $E1/anchor.py --lm $REPO/data/lm/bigram.sjlm            # 出貨的 data/lm（classes.sjc、kn.sjkn 在旁邊）
python3 $E1/anchor.py --lm $M/b0/bigram.sjlm --no-classes
python3 $E1/anchor.py --lm $M/b0m/bigram.sjlm --no-classes
```

預設 dev302、聊天與書面、`--context`；最後一行要印 `ANCHOR OK`（結束碼 0）。輸出裡有 commit 與模型雜湊，貼進研究紀錄。冒煙版：加 `--rows <檔> --limit 20`。

### 4. λ×β 格點與凍結選擇（E1b；每個 模型×設定 一個獨立工作，可分給 188 與 Colab）

```
SETS="--set cvtune=<cvtune.txt> --set wikitune=<wikitune.txt>"
for P in chat formal; do
  python3 -u $E1/grid.py select --label B0 --lm $M/b0/bigram.sjlm --no-classes --profile $P $SETS --procs 16 --out $R/sel
  for THR in <各檔門檻>; do
    python3 -u $E1/grid.py select --label T$THR --lm $M/b0/bigram.sjlm --tri $M/tri/tri-t$THR.sjt3 --no-classes --profile $P $SETS --procs 16 --out $R/sel
  done
done
```

每個工作寫 `$R/sel/<label>-<profile>-selection.json`：15 格分數、選到的 (λ, β)、調參集的雜湊與列數、模型雜湊、commit。選完凍結，把 JSON 內容寫進研究紀錄。`--procs` 照機器的執行緒數（每個程序各載一份 B0；trigram 表用 mmap 共用）。先用 `--limit 50` 量時間。

### 5. 各集合的 rowstats（judge）

```
DEVSETS="--set dev302=dev:302 --set typing76=$REPO/eval/dev/user-typing.txt --set user-reported=$REPO/eval/dev/user-reported.txt --set lytune=<lytune.txt> --set wikitypo=<wikitypo.txt>"
TUNESETS="--set cvtune=<cvtune.txt> --set wikitune=<wikitune.txt>"
for P in chat formal; do
  # 選定點
  python3 -u $E1/grid.py judge --label B0 --selection $R/sel/B0-$P-selection.json --lm $M/b0/bigram.sjlm --no-classes --profile $P $DEVSETS --procs 16 --out $R/judge
  python3 -u $E1/grid.py judge --label T$THR --selection $R/sel/T$THR-$P-selection.json --lm $M/b0/bigram.sjlm --tri $M/tri/tri-t$THR.sjt3 --no-classes --profile $P $DEVSETS --procs 16 --out $R/judge
done
# 出貨點（KN 重調的對照臂；E4 (b) 的兩臂也用它）：chat 0.5、formal 0.7，beta 1
python3 -u $E1/grid.py judge --label B0ship --lam 0.5 --beta 1 --lm $M/b0/bigram.sjlm --no-classes --profile chat $DEVSETS $TUNESETS --procs 16 --out $R/judge
python3 -u $E1/grid.py judge --label B0ship --lam 0.7 --beta 1 --lm $M/b0/bigram.sjlm --no-classes --profile formal $DEVSETS $TUNESETS --procs 16 --out $R/judge
# 出貨的 model-v6（只報；有詞類表）
python3 -u $E1/grid.py judge --label V6 --lam 0.5 --beta 1 --lm $REPO/data/lm/bigram.sjlm --profile chat $DEVSETS --procs 16 --out $R/judge      # formal 同理 --lam 0.7
# E4 (b)：新臂 B0m，出貨的 λ、beta 1，加 --x-context（對照臂是上面的 B0ship）
python3 -u $E1/grid.py judge --label B0m --lam 0.5 --beta 1 --x-context --lm $M/b0m/bigram.sjlm --no-classes --profile chat $DEVSETS $TUNESETS --procs 16 --out $R/judge   # formal 同理
```

輸出 `$R/judge/<label>/<profile>/<集合>.rowstats`、`<集合>.summary`（`lm_eval.py` 格式的摘要行，非零設定尾端有 `+tri:`、`+lam:`、`+beta:`、`+xctx` 標籤）、`judge.json`（每個集合的路徑／雜湊／列數、top1、`top1_sha256`、commit）。`typing76`、`user-reported` 用 repo 的檔（`user-reported.txt` 會增列，雜湊在 `judge.json`；要凍結就先複製一份）。`discordtune` 由 main 加 `--set discordtune=<私人路徑>` 在放它的機器上跑。

### 6. 比較表

```
for ARM in B0 T$THR; do python3 $E1/rowstats_concat.py --dir $R/judge/$ARM/chat --group judge5 --out $R/judge/$ARM/chat/judge5.rowstats; done
python3 tools/evalstats.py compare $R/judge/B0/chat/judge5.rowstats $R/judge/T$THR/chat/judge5.rowstats --label "T$THR vs B0 chat judge5 (BASE=B0 selected)" --net
```

| 比較 | BASE | CAND | 集合與串接 | a |
|---|---|---|---|---|
| trigram（每檔、每種設定） | `B0`（選定點） | `T<thr>`（選定點） | `--group judge5` | 0.0125 |
| KN 重調 | `B0ship` | `B0`（選定點） | `--group judge5` | 0.05 |
| 說明用 | `V6` | `B0ship` | `--group judge5` | 只報 |
| E4 (b) 主判讀 | `B0ship` | `B0m` | `--group all`（調參集＋判讀五集） | 0.025 |
| E4 (b) 守門格 | `B0ship` | `B0m` | 每個集合各自 `compare`（兩種設定；加 discordtune） | 不能顯著為負 |

`--group all` 的順序是 cvtune、wikitune、dev302、typing76、user-reported、lytune、wikitypo；兩臂的順序是常數，`evalstats compare` 還會核對每列正解字數相同。E4 子集合（只報）：

```
for ARM in B0ship B0m; do python3 $E1/subset_rowstats.py --arm $R/judge/$ARM/chat --set cvtune=<cvtune.txt> --set wikitune=<wikitune.txt> --set dev302=dev:302 --set typing76=$REPO/eval/dev/user-typing.txt --set user-reported=$REPO/eval/dev/user-reported.txt --set lytune=<lytune.txt> --set wikitypo=<wikitypo.txt> --out $R/judge/$ARM/chat/subset.rowstats; done
python3 tools/evalstats.py compare $R/judge/B0ship/chat/subset.rowstats $R/judge/B0m/chat/subset.rowstats --label "E4(b) subset chat" --net
```

### 7. 壓縮（E1c；對象 B0，以及通過的最小 T_k，都沒通過就用約 200 MB 那檔）

```
python3 $E1/compress.py report --lm $M/b0/bigram.sjlm --out $R/compress/b0.json                                  # raw / zip / 無損 / q8 / q4 的位元組，與各自 zip（deflate）後
python3 $E1/compress.py report --lm $M/b0/bigram.sjlm --tri $M/tri/tri-t$THR.sjt3 --out $R/compress/t$THR.json
for BITS in 0 8 4; do
  python3 $E1/compress.py encode --lm $M/b0/bigram.sjlm --tri $M/tri/tri-t$THR.sjt3 --bits $BITS --out $R/compress/t$THR.q$BITS.sjz
  python3 $E1/compress.py decode --in $R/compress/t$THR.q$BITS.sjz --out-dir $R/compress/dec$BITS --kn $M/b0/kn.sjkn
done
cmp $R/compress/dec0/bigram.sjlm $M/b0/bigram.sjlm && cmp $R/compress/dec0/tri.sjt3 $M/tri/tri-t$THR.sjt3 && echo LOSSLESS-IDENTICAL   # 不同＝實作錯，停止（§5）
for BITS in 0 8 4; do for P in chat formal; do
  python3 -u $E1/grid.py judge --label Z$BITS --selection $R/sel/T$THR-$P-selection.json --lm $R/compress/dec$BITS/bigram.sjlm --tri $R/compress/dec$BITS/tri.sjt3 --no-classes --profile $P $DEVSETS $TUNESETS --procs 16 --out $R/judge
done; done
for P in chat formal; do for S in cvtune wikitune dev302 typing76 user-reported lytune wikitypo; do cmp $R/judge/T$THR/$P/$S.rowstats $R/judge/Z0/$P/$S.rowstats; done; done   # (i) 逐列相同
```

(ii)：每個位元數、每個集合各自 `evalstats compare`（BASE ＝ 同一個選定點的未壓縮 `T$THR`，CAND ＝ `Z8`／`Z4`），每集兩種設定、加 discordtune，全不顯著為負才列為候選格式。B0 的壓縮同理（不帶 `--tri`、用 B0 的選擇 JSON）。`--tri` 的 judge 在 `Z0` 要等於 `T$THR`：多一個保險是 `Z0` 的 `judge.json` 的 `top1_sha256` 和 `T$THR` 的相同。
