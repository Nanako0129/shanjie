# E3 整串縮寫輸入：測資、解碼、判讀

契約：`docs/contracts/derisk-1015.md` §3 E3、§4。共同環境變數（`PY`、`D`、`OUT`）、錨點與串接見 `../common/README.md`。

## 做了什麼

| 檔案 | 作用 |
|---|---|
| `gen_abbrev.py` | 從集合產生縮寫版測資：每個音節以機率 p 換成只剩聲母，沒有聲母的音節（ㄧ ㄨ ㄩ 開頭、單韻母、ㄦ）保留原樣。種子固定（預設 20261015，與集合名組成亂數種子）；每個音節抽一個 [0,1) 的數，音節在 `u < p` 時縮寫，所以 p=0.2 縮寫的音節必然也在 p=0.5、p=1.0 縮寫（曲線的三個點是巢狀的）。輸出 `<名稱>.p0.2.txt`、`.p0.5.txt`、`.p1.0.txt` 與 `<名稱>.manifest.json`（產生程式的 commit 與是否有未提交改動、種子、輸入檔 SHA-256、輸出檔 SHA-256）。前文與句子欄不動；只留三欄的列（`lm_eval.py` 的規則） |
| `abbrev.py` | `Expander`：把輸入裡的縮寫 token 展開成**詞庫裡真的有的**讀音（用讀音的前綴樹，只走聲母相符的音節，終點必須是詞庫的 key）。`make_decode`：和 `lm.decode` 同一個迴圈（束寬、計分、同分取捨、n-best 去重都一樣），只把 `lex.by_reading.get(key)` 換成「符合的所有 key（排序後）」 |
| `e3_eval.py` | 用 `common/patched_eval.py` 跑 `lm_eval.py`：`--abbrev off` 不換任何東西（零設定，錨點）；`--abbrev on` 換成展開版解碼，摘要行的設定欄加 `+abbr`。摘要行之後多印一行 `#E3 …`：n、top1、top9、縮寫 token 數、`readings_per_abbr`、每列計分的詞條數、解碼秒數 |
| `decide.py` | 判讀：dev302 在 p=0.2 的 top1 ÷ 完整輸入的 top1，聊天與書面都 ≥ 0.9 才 PASS |

## 約定與限制（先讀）

- **縮寫 token 的認定**：只有一個聲母字母的 token 就是縮寫，展開成詞庫裡所有以該聲母開頭的音節。這也包括 ㄓ ㄔ ㄕ ㄖ ㄗ ㄘ ㄙ 的第一聲（單獨一個字母本身就是完整音節）：真實輸入無法分辨，所以展開時一律把整組都算進去（含它自己）。結果：`--abbrev on` 對**完整輸入**的列也不等於零設定（這幾個音節會被當成縮寫）。零設定一律用 `--abbrev off`。要量這個差異，可把 `--abbrev on` 跑在未縮寫的原檔上，只報不判。
- **top1 單位是整列**，用 `lm_eval.py` 的寬鬆比對；top9 = 正解句在前 9 個不重複候選裡（寬鬆比對）；`oracle@64` 在摘要行裡（束寬 64 的候選全部）。
- **平均候選讀音數**（`readings_per_abbr`）= 每個縮寫 token 背後有多少個詞庫裡的不同音節（含聲調），對所有縮寫 token 取平均。Mac 上 20 列 dev302 量到約 63。
- **解碼時間**是 Python 解碼迴圈的秒數（不含載入模型），只作相對比較。每個讀音仍只展開前 `ime.PER_KEY`（12）個詞，所以縮寫的花費來自讀音數乘上跨度，不是每個讀音多取詞。Mac 上 20 列 dev302、聊天、單程序的量測（**只是量級，不是目標機器的數字**）：p=0.2 約 102 ms／列、每列計分約 910 個詞條；p=1.0 約 570 ms／列、約 5,500 個詞條。
- 測資是公開集合的衍生物，**放 repo 外**（`$OUT/e3/`）；repo 只記產生程式的 commit 與雜湊。
- 怎麼讀沒過的結果：看 `oracle@64` 和 top9。`oracle@64` 也跌 → 候選爆炸（束寬 64 把正解擠掉了）；`oracle@64` 還高、top1 低 → 語言模型分不開。

## 指令

測資：

```
mkdir -p $OUT/e3/rs $OUT/e3/out
for s in dev302 typing76 cvtune wikitune; do
  python3 $D/e3/gen_abbrev.py --set $s --out-dir $OUT/e3 > /dev/null
done
sha256sum $OUT/e3/*.txt                      # 與 manifest 相同；manifest 的 generator_dirty 必須是 false（先 commit 再產生）
cat $OUT/e3/*.manifest.json                  # 記進研究紀錄：generator_commit、輸入與輸出雜湊
```

解碼（每個集合 × 兩種設定；完整輸入用 `--abbrev off` 跑原檔，縮寫版用 `--abbrev on`）：

```
cell() {   # cell SET VERSION PROFILE : VERSION = full | p0.2 | p0.5 | p1.0
  s=$1; v=$2; prof=$3
  if [ $v = full ]; then mode=off; rows=$(python3 $D/common/setnames.py path $s)
  else mode=on; rows=$OUT/e3/$s.$v.txt; fi
  $PY $D/e3/e3_eval.py --abbrev $mode --lm data/lm/bigram.sjlm --profile $prof --rows $rows --context --name $s.$v \
      --rowstats $OUT/e3/rs/$s.$v.$prof.rs > $OUT/e3/out/$s.$v.$prof.out
}
export -f cell; export PY D OUT
for s in dev302 typing76 cvtune wikitune; do for v in full p0.2 p0.5 p1.0; do for prof in chat formal; do
  echo "$s $v $prof"; done; done; done | xargs -P 8 -L1 bash -c 'cell "$@"' _
```

（`-P 8` 是起點：每個程序各載入一份模型，記憶體由 main 依機器調整。p=1.0 的 cvtune、wikitune 最慢。預估時間是外推，沒有在目標機器量過。）

判讀與回報：

```
python3 $D/e3/decide.py $OUT/e3/out/dev302.full.chat.out $OUT/e3/out/dev302.p0.2.chat.out \
                        $OUT/e3/out/dev302.full.formal.out $OUT/e3/out/dev302.p0.2.formal.out
grep -h '^#E3' $OUT/e3/out/*.out | sort            # top9、readings_per_abbr、解碼時間
# 每個集合、每個 p、每種設定對完整輸入各一行（BASE = 完整輸入，註明在標籤）：
for s in dev302 typing76 cvtune wikitune; do for v in p0.2 p0.5 p1.0; do for prof in chat formal; do
  python3 tools/evalstats.py compare $OUT/e3/rs/$s.full.$prof.rs $OUT/e3/rs/$s.$v.$prof.rs --net --label "$s $v $prof（BASE=完整輸入）"
done; done; done
```

判讀只看 `decide.py` 的 PASS／FAIL（p=0.2、dev302、兩種設定都 ≥ 90%）；`compare` 的表是附帶資料（`evalstats` 的 McNemar 與改對／改壞給看得到的對照，不是 §3 E3 的判讀規則）。曲線（p = 0.2、0.5、1.0）三個點的 top1 與 top9 進研究紀錄。
