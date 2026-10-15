# E4 (a) 句中標點之後不重設前文（只改解碼）

契約：`docs/contracts/derisk-1015.md` §3 E4 (a)（含「修訂一」的 e4ctx）、§4。(b)（計數與解碼一起改）不在這裡。共同環境變數、錨點、串接見 `../common/README.md`。

## 現況（讀程式的結果）

| 位置 | 現況 | E4 (a) |
|---|---|---|
| 評測列的前文欄 → `lm_eval.py --context` → `lm.context_key` → `lm.history` | `context_key` 取尾端連續漢字、最多 2 字；前文結尾不是漢字（任何標點、數字、英文、空白）就是 `""`，`history` 回 `<s>`。有 2 字時 `history` 先試整段、再試最後 1 字，取第一個在模型裡有 bigram 歷史的 | 改這裡：先去掉前文**結尾**連續的句中標點，再套原本的 `context_key` |
| 句子欄／讀音欄 | 評測列的句子欄只有漢字、讀音欄只有注音音節，**不會有句中標點**。實測：七個集合（含整個 `user-reported`）的句子欄都沒有任何非漢字字元（`subset.py` 的最後一欄全是 0） | 沒有可改的 |
| 引擎的組字區（`core/src/engine.rs`、`lm.rs`，s3d §4） | 標點是句子邊界：標點前的一段以 `</s>` 結束，下一段從 `<s>` 開始；左文一樣用 `context_key` | **參考實作量不到**（沒有帶標點 token 的評測列）。如果 (a) 通過，核心契約要兩處都改：左文的 key，以及組字區裡標點之後那一段的起點。計數端（`experiments/s2/build_counts.py` 在每個非漢字字元處切段）不變 |

所以參考實作裡「兩個地方」其實只有一個存在（前文欄）。另一個在 Rust 引擎，這輪不碰。

重要的後果（先讀）：`subset.py` 在七個集合上的結果——

| 集合 | 列數 | 前文含句中標點、數字或英文 | 前文 key 會因 (a) 改變 |
|---|---|---|---|
| cvtune | 3,791 | 0 | 0 |
| wikitune | 2,827 | 0 | 0 |
| dev302 | 302 | 0 | 0 |
| typing76 | 76 | 0 | 0 |
| user-reported | 114 | 26 | 12 |
| lytune | 4,811 | 0 | 0 |
| wikitypo | 155 | 0 | 0 |

只有 `user-reported` 有 12 列的前文 key 會變；其他六個集合的前文都乾淨（沒有標點、數字、英文），兩臂在那些列上**必然逐位元相同**。因此 §3 E4 主判讀（調參集＋判讀五集串接、至少一種設定顯著為正、a = 0.025）實際上只有最多 12 列能動：精確 McNemar 要像 7:0（p = 0.016）、9:1（p = 0.021）這樣的結果才有 p < 0.025（6:0 是 0.031）。這不是程式的問題，是這個判讀規則對這批集合幾乎沒有檢定力；`discordtune`（私人）是唯一可能有更多受影響列的集合：main 先對它的列檔跑 `subset.py --rows … --name discordtune` 看 `changed` 欄。**修訂一（維護者選擇題）就是為這個加的**：另建公開評測集 e4ctx（下一節），把它接在判讀五集之後進主判讀的串接，兩個子集各自也是守門格。

## 檔案

| 檔案 | 作用 |
|---|---|
| `punct_ctx.py` | `MID`（12 個）、`FINAL`（10 個）與 `context_key_mid` |
| `e4_eval.py` | `--punct reset`（對照臂＝零設定，`lm.context_key` 不動）／`--punct mid`（`context_key_mid`，摘要行設定欄加 `+punct`）。**必須帶 `--context`**，沒帶就報錯（沒有 `--context` 時 `lm_eval.py` 不讀前文，兩臂會悄悄相同）。摘要行後多印 `#E4 … rows_with_changed_key=N`：兩條規則的 key 不同的列數 |
| `build_e4ctx.py` | 產生 e4ctx（見下一節） |
| `subset.py` | 子集合報告（預設依 `setnames.E4_ORDER`：七個集合再接 `e4ctx-punct`、`e4ctx-alnum`，缺任何一個就報錯）：每個集合的 `<名稱>.any.idx`（前文任何位置含句中標點、數字或英文字母，ASCII 與全形）與 `<名稱>.changed.idx`（(a) 會動到的列）、以及依給定集合順序串接後的 `concat.any.idx`、`concat.changed.idx`；印出計數表（只有數字）。`any` 是契約的「錯字回報第 112、113 列那一型」，`changed` 是其中解碼真的會變的 |

### 標點分類（精確清單）

- **句中（保留前文）**：ASCII `,` `;` `:`；全形 `，`（U+FF0C）`；`（U+FF1B）`：`（U+FF1A）；`、`（U+3001）`､`（U+FF64）；小形 `﹐`（U+FE50）`﹑`（U+FE51）`﹔`（U+FE54）`﹕`（U+FE55）。共 12 個。連續出現（`，，`）一起略過。
- **句末（照舊重設）**：`。`（U+3002）`｡`（U+FF61）`.` `．`（U+FF0E）`？` `?` `！` `!` `…`（U+2026）`⋯`（U+22EF）。
- 其他非漢字（引號、括號、破折號、中點、空白、數字、英文、emoji）照舊重設，這一版沒有動它們。前文結尾若是「標點＋空白」也照舊重設（只略過**緊貼在結尾**的句中標點）。
- 「標點前最後一個詞」用原本的近似：略過標點後取尾端 ≤ 2 個漢字，交給 `history`（先整段、再最後 1 字）。評測列只有前文字串，沒有斷詞，這是和現行規則一致的做法。
- 逗號前若是數字或英文（`快1.5，`、`Colab,`），略過逗號後結尾仍不是漢字，key 仍是 `""`：(a) 管不到這一型，那是 (b) 的事。

## e4ctx：跨逗號、跨數字／英文的公開評測集（契約 §3 E4 修訂一）

原因：現有集合裡 (a) 只動得到 user-reported 的 12 列，discordtune 是 0 列。e4ctx 用和 cvtune、wikitune **同一批文字**切出前文跨過句中標點或數字／英文的列；E4 只比兩種解碼（或兩種計數），沒有選值，所以用調參集的原始句子不會污染判讀。

| 項目 | 做法 |
|---|---|
| 選哪些文字 | 同 `experiments/s2/build_tune.py`：cv 取 `is_tune(raw)` 為真的行（用未轉換的原文判斷，直接用 `build_tune.is_tune`）；維基取 `bc.articles(305_000)` 的第 300,000–304,999 篇 |
| 文字處理 | 完全是 `build_tune.py` 取 `HAN` 之前的步驟：cv `bc.convert(raw)`；維基 `TEMPLATE` ×3 → `MARKUP` → `bc.SENT` 切分 → `bc.convert`。保留標點、數字、英文。一行 cv、一個 `SENT` 匹配＝一個「原始句子」 |
| 切成一列 | 在原始句子裡，每一段「2–25 個漢字」（`[一-鿿]`）若**緊接在**一個 `MID` 字元（`punct_ctx.MID`）或一個 `WORDLIKE` 字元（`subset.WORDLIKE`：ASCII／全形數字與字母）之後，就切一列。前文＝原始句子裡那段漢字之前的全部文字（所以結尾就是那個字元）；句子＝那段漢字。標點與漢字之間隔了空白不切。前文含 `\|` 或控制字元的丟掉 |
| 兩個子集 | `e4ctx-punct`（前文以 `MID` 結尾）、`e4ctx-alnum`（前文以 `WORDLIKE` 結尾）。每個子集、每個來源（cv、wiki）至多 1,500 列，種子 20261015（再與來源、子集名組成亂數種子），每個原始句子在一個子集至多 1 列（候選裡隨機取一個），相同的（前文，句子）去重 |
| 讀音 | `tools/readings.py`（規則同其他集合；機器上資料不在預設位置時設 `SHANJIE_MOEDICT`、`SHANJIE_HETERO`）；抽樣在讀音**之前**，CHECK 列與拼不出的列丟掉（`build_tune.py` 的做法），所以實際列數會少於 1,500×2 |
| 輸出 | repo 外：`<DIR>/e4ctx-punct.txt`、`<DIR>/e4ctx-alnum.txt`（每個檔先 cv 後 wiki）、`<DIR>/e4ctx.manifest.json`（產生程式 commit 與是否有未提交改動、種子、每個來源的候選數／抽樣數／最後列數、輸入檔大小、輸出 SHA-256）。`<DIR>/*.in`、`*.all` 是 `readings.py` 的中間檔 |

輸入（描述，不是路徑）：`--src` 目錄（預設 `~/.cache/shanjie/sources`，和 `experiments/s2/build_counts.py` 相同）要有 `colloquial/cv-*` 與 `colloquial/cmn_sentences.tsv.bz2`、`opencc/`（轉換表，`bc.load_conv()` 會核對 SHA-256）、`zhwiki-20261001-pages-articles.xml.bz2`。這些原始資料在 Windows 機器與 Colab 上，不在 Mac：讀 305,000 篇的時間和 `build_tune.py` 的 wikitune 部分相同（約 20 分鐘），在 Windows 機器的 WSL 或 Colab 跑。

```
# 先 commit（manifest 的 generator_dirty 要是 false）
export E4CTX_DIR=$OUT/e4ctx
python3 $D/e4/build_e4ctx.py --out-dir $E4CTX_DIR --src <含 colloquial/、opencc/、zhwiki dump 的目錄>
cat $E4CTX_DIR/e4ctx.manifest.json     # 把 outputs 的 SHA-256 與 counts 記進研究紀錄，也補進這份 README 的「e4ctx 雜湊」
sha256sum $E4CTX_DIR/e4ctx-*.txt
python3 $D/common/setnames.py list     # e4ctx-punct、e4ctx-alnum 兩列要出現
```

e4ctx 雜湊：（尚未產生；main 跑完後填：`e4ctx-punct.txt` ＿＿、`e4ctx-alnum.txt` ＿＿、產生程式 commit ＿＿。）

因為這些列的前文都跨過標點或數字／英文，(a) 在 `e4ctx-punct` 會動到逗號前是漢字的那些列（前文 key 非空；逗號前是數字／英文的列不變）；`e4ctx-alnum` 的前文結尾是數字／英文，(a) 只略過句中標點，所以那個子集裡兩臂應該**逐位元相同**（(b) 才會動它）：這是個健全性檢查，也是 (a) 的守門格。

## 指令

```
mkdir -p $OUT/e4/rs $OUT/e4/out
ORDER="cvtune wikitune dev302 typing76 user-reported lytune wikitypo"
E4_ORDER="$ORDER e4ctx-punct e4ctx-alnum"      # 修訂一：e4ctx 接在判讀五集後面
export E4CTX_DIR=$OUT/e4ctx                    # build_e4ctx.py 的輸出（見上）
cell() {   # cell SET PROFILE ARM : ARM = reset | mid
  s=$1; prof=$2; arm=$3
  $PY $D/e4/e4_eval.py --punct $arm --lm data/lm/bigram.sjlm --profile $prof --rows "$(python3 $D/common/setnames.py path $s)" \
      --context --name $s --rowstats $OUT/e4/rs/$s.$prof.$arm.rs > $OUT/e4/out/$s.$prof.$arm.out
}
export -f cell; export PY D OUT
for s in $E4_ORDER; do for prof in chat formal; do for arm in reset mid; do echo "$s $prof $arm"; done; done; done \
  | xargs -P 8 -L1 bash -c 'cell "$@"' _
```

檢查（前文不變的列兩臂必須相同；其他六個舊集合和 `e4ctx-alnum` 應該完全相同）：

```
for s in cvtune wikitune dev302 typing76 lytune wikitypo e4ctx-alnum; do for prof in chat formal; do
  cmp $OUT/e4/rs/$s.$prof.reset.rs $OUT/e4/rs/$s.$prof.mid.rs || echo "DIFF $s $prof"
done; done
grep -h '^#E4' $OUT/e4/out/*.out | sort | uniq -c        # rows_with_changed_key 與 subset.py 的 changed 欄相同
```

主判讀（調參集＋判讀五集＋e4ctx-punct＋e4ctx-alnum，兩種設定各一行，BASE = 對照臂 reset）與守門：

```
for prof in chat formal; do
  cat_rs $OUT/e4/all.$prof.reset.rs $OUT/e4/rs ".$prof.reset.rs" "$E4_ORDER"     # cat_rs 在 common/README.md
  cat_rs $OUT/e4/all.$prof.mid.rs   $OUT/e4/rs ".$prof.mid.rs"   "$E4_ORDER"
  python3 tools/evalstats.py compare $OUT/e4/all.$prof.reset.rs $OUT/e4/all.$prof.mid.rs --net --label "E4a 調參集＋判讀五集＋e4ctx $prof（BASE=reset）"
  for s in $E4_ORDER; do   # 守門格：每個集合各自（含 e4ctx-punct、e4ctx-alnum 兩個子集）
    python3 tools/evalstats.py compare $OUT/e4/rs/$s.$prof.reset.rs $OUT/e4/rs/$s.$prof.mid.rs --net --label "E4a $s $prof（BASE=reset）"
  done
done
```

判讀（契約，修訂一）：主判讀的串接是調參集＋判讀五集＋`e4ctx-punct`＋`e4ctx-alnum`；至少一種設定「改對 > 改壞 且 p < 0.025」；守門：每個集合（含兩個 e4ctx 子集各自）、兩種設定、加上兩種設定的 discordtune，都不能「改壞 > 改對 且 p < 0.05」。`discordtune` 由 main 在放私人資料的機器上用同一個 `e4_eval.py` 與 `compare` 跑。

子集合（只報不判）：

```
python3 $D/e4/subset.py --out-dir $OUT/e4/subset                       # 九個集合依 E4_ORDER，印計數表
for prof in chat formal; do for kind in any changed; do
  python3 $D/common/concat_rowstats.py select $OUT/e4/sub.$prof.$kind.reset.rs $OUT/e4/all.$prof.reset.rs $OUT/e4/subset/concat.$kind.idx
  python3 $D/common/concat_rowstats.py select $OUT/e4/sub.$prof.$kind.mid.rs   $OUT/e4/all.$prof.mid.rs   $OUT/e4/subset/concat.$kind.idx
  python3 tools/evalstats.py compare $OUT/e4/sub.$prof.$kind.reset.rs $OUT/e4/sub.$prof.$kind.mid.rs --net --label "E4a 子集合 $kind $prof（BASE=reset）"
done; done
```

`concat.*.idx` 的列號是依上面 `E4_ORDER` 串接後的列號（`all.*.rs` 同一個順序），兩者必須用同一個順序。`any`（舊集合裡 26 列，另加 e4ctx 的列）是契約說的子集合；`changed`（12 列）是 (a) 實際會動的列。
