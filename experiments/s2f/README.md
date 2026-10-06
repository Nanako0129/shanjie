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
