# derisk-1015 共用工具

契約：`docs/contracts/derisk-1015.md` §2、§4。E3、E4 (a) 的說明在 `../e3/README.md`、`../e4/README.md`；這份是它們共用的部分，以及在 Windows 機器（WSL）或 Colab 上要跑的共同指令。Mac 只跑單元測試與 20 列以內的煙霧測試。

| 檔案 | 作用 |
|---|---|
| `setnames.py` | 集合的順序（§2：cvtune、wikitune \| dev302、typing76、user-reported、lytune、wikitypo）與位置。位置取自 `tools/bench.py` 的 `SETS2`；`user-reported` 是整個 `eval/dev/user-reported.txt`（和 `tools/triage.py` 相同，bench 只釘前 80 列），它的雜湊不釘死。`list` 列出列數、SHA-256 與是否符合 bench 的釘死值；`path NAME` 只印路徑 |
| `concat_rowstats.py` | `concat OUT A B …`：依給定的順序串接 rowstats，列號從 1 重編。`select OUT IN IDXFILE`：只留 IDXFILE 列的列號（子集合）。輸入一律過 `tools/evalstats.py` 的 `read_rowstats`，格式不對就報錯、不寫輸出 |
| `anchor.py` | 錨點比對：兩份輸出的 `## ` 摘要行逐字相同才過（含 `top1_sha256`），不同就印出第一個差異的欄位並以 1 結束 |
| `patched_eval.py` | 把 `reference/proto/lm_eval.py` 原封不動跑起來，只在那一次執行中換掉 `lm.decode` 或 `lm.context_key`。E3、E4 都用它，所以「全關」設定走的是和參考實作同一份程式 |

## 單元測試

```
python3 -W ignore -m unittest experiments/derisk-1015/common/test_common.py experiments/derisk-1015/e3/test_e3.py experiments/derisk-1015/e4/test_e4.py -v > out.txt 2>&1; echo $?
```

## 共同環境（Windows 機器的 WSL；bash）

```
cd <REPO>                       # feat/derisk-e34 的 checkout；data/lm/ 放出貨的 model-v6，先核對 data/*.sha256
export PY="python3 -W ignore"
export D=experiments/derisk-1015
export OUT=<repo 外的快取>/derisk-1015   # E3 測資、各格輸出、rowstats 都放這裡，不進 repo
mkdir -p $OUT
python3 $D/common/setnames.py list      # 每一集都要在，PIN 不符就停（user-reported 沒有釘死值，記下雜湊）
```

出貨模型：`data/lm/bigram.sjlm`，同目錄的 `kn.sjkn`、`classes.sjc` 由 `lm_eval.py` 自動載入（摘要行的 `+kn:cbefbced:θ1:β1` 就是它）；名單層 `names-add.tsv` 已在 `ime.OVERLAYS` 裡，不要再給 `--extra-overlay`。Python 參考實作一律帶 `--context`。

## 錨點（§2、§5 的停止條件）

每個實驗開跑前、兩種設定各一次，延伸版的「全關」設定要和 `lm_eval.py` 逐字相同：

```
for prof in chat formal; do
  $PY reference/proto/lm_eval.py --lm data/lm/bigram.sjlm --profile $prof --dev 302 --context > $OUT/anchor.ref.$prof.out
  $PY $D/e3/e3_eval.py --abbrev off --lm data/lm/bigram.sjlm --profile $prof --dev 302 --context > $OUT/anchor.e3.$prof.out
  $PY $D/e4/e4_eval.py --punct reset --lm data/lm/bigram.sjlm --profile $prof --dev 302 --context > $OUT/anchor.e4.$prof.out
  for e in e3 e4; do python3 $D/common/anchor.py $OUT/anchor.$e.$prof.out $OUT/anchor.ref.$prof.out || echo "STOP: $e $prof"; done
done
```

`anchor ok` 才往下；`ANCHOR MISMATCH` 是 §5 的停止條件。Mac 上的煙霧版（同一段指令，`--dev 302` 換成 dev302 前 20 列的 `--rows`）已確認 E3、E4 在聊天、書面兩種設定都是 `anchor ok`；`--rowstats` 也逐位元組相同（聊天，E3）。完整的 302 列錨點尚未在目標機器上跑，由 main 跑。

## 串接與比較

串接一律用 `setnames.ORDER` 的順序，兩邊（BASE、CAND）用同一串檔名：

```
ORDER="cvtune wikitune dev302 typing76 user-reported lytune wikitypo"
JUDGE="dev302 typing76 user-reported lytune wikitypo"      # 判讀五集（不含調參集）
cat_rs() {   # cat_rs OUT DIR SUFFIX "SET SET …" : 串接 DIR/<set>SUFFIX，依給定的集合順序
  files=""; for s in $4; do files="$files $2/$s$3"; done
  python3 $D/common/concat_rowstats.py concat $1 $files
}
python3 tools/evalstats.py compare BASE.rs CAND.rs --net --label "名稱（BASE=…）"
```

每一組比較在表格的名稱欄寫明哪一份是 BASE。私人守門 `discordtune` 由 main 在放私人資料的機器上跑，同一個指令、同一個 `e3_eval.py`/`e4_eval.py`，只帶回 `compare --net` 的那一行。

子集合：`concat_rowstats.py select OUT IN IDXFILE` 取 IDXFILE（一行一個列號）列出的列、重編列號，再 `compare`。
