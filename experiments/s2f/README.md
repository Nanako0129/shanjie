# S2f：字形修正（契約 `docs/contracts/s2f-variant-forms.md`）

| 檔案 | 用途 |
|---|---|
| `overlay-variant-removed.tsv` | §2.5：疊加層裡只是基底詞異體寫法的詞（詞、基底寫法、讀音），`tools/build_overlay.py` 產生 |
| `make_probe.py` → `probe.txt` | §3.2：字形探針，`前文\|期望寫法\|讀音`；期望寫法是基底詞庫裡同讀音兩種寫法中分數較高的那個 |
| `cv_native.py` | §3.4：cvtune-native 子集（模型 E 的 cvtune 檔，SHA-256 核對）；舊轉換從提交 `dd7d2bf` 用 `git show` 讀成暫時模組，不在 repo 裡另存一份 |

cvtune-native 列數（Mac 上算，重建前；模型 E 的 cvtune 3756 列中）：**2117**。驗收時用同一支程式重算，必須相同。

## 在 188 上重建（照 S2n 的 `--expected` 流程）

前置：
- 188 的 `~/.cache/shanjie/sources/opencc/` 要有 **`TWVariants.txt`**（新增；SHA-256 `245b94eb5842957e735dd44b7e7d4ff469a3643126cc8fa511adda5281e9cb86`），`build_counts.py` 一 import 就核對，不符就中止。`STCharacters.txt` 現在也在使用前核對雜湊。
- 打包進去的 repo 除了 S2n 那些，還要 `tools/build_lm.py`（現在 import `build_counts`、`ime`，並讀 `data/lexicon/` 的三份詞庫）。
- `overlay-add.tsv`、`sandhi-add.tsv`、`overlay-variant-removed.tsv` 已在這個分支重產並提交（`tools/build_overlay.py` 要讀維基標題 dump，不在 188 上跑）。188 上只核對 `overlay-add.tsv` 的 SHA-256 等於分支裡的。

```
set S2_WORK=%USERPROFILE%\.cache\shanjie\work\s2f        rem 不蓋掉模型 E 的計數
rem 1. 參考句與口語訓練檔（cvtune、colloquial-train；轉換已是新的）
python -u experiments\s2\build_tune.py --part cv
rem 2. 口語計數（輸入同 S2n：colloquial-train.txt 加 synth.txt）
python -u experiments\s2\build_counts_text.py --expected %S2_WORK%\counts-colloquial3.pkl %S2_WORK%\tune\colloquial-train.txt <synth.txt>
rem 3. wikitune（約 20 分鐘）
python -u experiments\s2\build_tune.py --part wiki
rem 4. 維基計數
python -u experiments\s2\build_counts.py --articles 200000 --procs 10 --expected
rem 5. 建模型（合併在這裡；輸出 = 候選模型 F）
python tools\build_lm.py --out %S2_WORK%\bigram-F.sjlm
```

S2n 的批次檔慣例照舊：絕對路徑、`start /b /wait /affinity FFF`、`PYTHONUTF8=1`。

## 驗收指令（在 Mac，模型 F 放在 `data/lm/bigram.sjlm` 或用 `--lm` 指定）

```
python3 reference/proto/lm_eval.py --lm <F> --profile chat   --rows experiments/s2f/probe.txt --context --dump <chat.dump>
python3 reference/proto/lm_eval.py --lm <F> --profile formal --rows experiments/s2f/probe.txt --context --dump <formal.dump>
```
每列（`--dump` 的名次 1）要等於 `probe.txt` 的第二欄；不等就是停止條件，報分數。

cvtune-native 的守門（模型 E 與 F 各跑一次，兩種設定，配對比較）：
```
python3 experiments/s2f/cv_native.py                      # 寫 ~/.cache/shanjie/work/s2f/cvtune-native.txt，印列數
python3 reference/proto/lm_eval.py --lm <E|F> --profile chat|formal --rows ~/.cache/shanjie/work/s2f/cvtune-native.txt --context --dump <檔>
```
同樣的做法用在 dev302（`--dev 302`）與 typing76；`--context` 與否照各自的基準。

## 結果（2026-10-07）

模型 F：SHA-256 `81e336676929c0ac8f888241abb5ba29ab611edb26ce8414de3c854d2f870c50`，82,235,866 bytes（上限 100 MB），V=333,330、前文 254,625、條目 9,030,324。188 整批 12 分鐘，Python 記憶體峰值 11.53 GB。

- **字形探針**（`--context`，第 1 名逐字等於期望寫法）：聊天 16/16、書面 16/16（模型 E 寬鬆對照 10／8）。
- **使用者的句子**：「大概十分鐘後到」兩種設定都對；「好」＋ㄅㄚ˙ 仍是「吧」。
- **守門**（`guard.py`，`--context`，E → F，修好／弄壞，McNemar 雙尾）：

  | 集合 | 設定 | E → F | 修好 | 弄壞 | p |
  |---|---|---|---|---|---|
  | dev302 | chat | 234 → 235 | 1 | 0 | 1 |
  | dev302 | formal | 239 → 241 | 2 | 0 | 0.5 |
  | typing76 | chat | 66 → 66 | 0 | 0 | 1 |
  | typing76 | formal | 65 → 65 | 0 | 0 | 1 |
  | cvtune-native（2,117 列） | chat | 1,833 → 1,833 | 1 | 1 | 1 |
  | cvtune-native | formal | 1,837 → 1,836 | 0 | 1 | 1 |
  | 錯字回報（34 列，只報告） | chat／formal | 10 → 10／11 → 11 | 0 | 0 | 1 |

  沒有任何一格否決。第一名字串改變的列：異體寫法（散佈→散布、分佈→分布 ×2、臺鐵→台鐵），以及 9 列接近同分的翻轉（模型 E 裡第一、二名差 ≤ 0.04 log10，「她／他」完全同分），例如 率水壺→律水壺、電器→電氣、賣家→麥加。翻轉的原因是 §2.4 的合併：同類成員都寫回類別的總次數，模型的 N 從 147,191,453 變成 149,345,231（+1.46%），回退與二元組的平衡跟著移動。
- **次數**（`char_counts.py`，188；E → F，各字次數）：床／牀 0／13,897 → 13,879／0；秘／祕 0／41,303 → 32,184／9,118；灶／竈 0／1,991 → 1,989／0；粽／糉 0／698 → 698／0；庄／莊 0／49,723 → 11,925／37,801（庄 都來自繁體句原文，例如南庄、日治時期的「某某庄」；簡體句一律轉成 莊）；痴／癡 0／2,176 → 0／2,176（癡→痴 是排除條目）。
- **S2n 的殘留**（`experiments/s2n/measure_residue.py`）：七個詞 E、F 都是 0（≤ 6,282）。加權句段 32,878,760 → 32,878,760（不變），詞數 −0.01%。
- **疊加層**：拿掉 1,120 個詞（見研究紀錄）。
- **golden**：重產 4 個依賴模型的檔（13 行），每一行都對應上面的改變：`s2-lm-dev302-top1.tsv` 4 行（散佈、分佈 ×2、率水壺）、`s2r-probe-top1.tsv` 2 行（分佈、率水壺）、`s2-lm.txt` 與 `s2h-lm-context.txt` 的摘要行（typing76 書面無前文是「她→他」，E 裡差 0.0005）。疊加層帶動的 golden（`s1-dev302.txt`、`s2r-probe-unigram.txt`）不變。
