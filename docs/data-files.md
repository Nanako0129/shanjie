# 模型與資料檔

善解的語言模型和詞類表很大，不放進 git，放在 GitHub Release 當附件。repo 裡只記它們的 SHA-256，CI、打包 App 與測試都先比對雜湊再用。這兩個 Release **不是輸入法的發行版**（發行版是 `v0.x.y`）。

## 現在用的兩個檔案

| 檔案 | Release | 內容 | 大小 | 雜湊檔 |
|---|---|---|---|---|
| `bigram.sjlm` | [`model-v5`](https://github.com/Nanako0129/shanjie/releases/tag/model-v5) | bigram 語言模型：詞與詞相鄰出現的機率；含少量用來辨識來源的指紋條目 | 82,535,147 bytes | `data/bigram.sjlm.sha256` |
| `classes.sjc` | [`classes-v3`](https://github.com/Nanako0129/shanjie/releases/tag/classes-v3) | 詞類表（S2k）：512 個詞類、每個詞屬於哪一類、「這類詞後面接那類詞」的機率。兩個詞沒在語料一起出現過時，用它補估計 | 5,575,836 bytes | `data/classes.sjc.sha256` |

- **兩個一起用，放在同一個資料夾**（`data/lm/`）。`classes.sjc` 不含模型本身，`classes-v3` 這個 Release 也只有這一個檔案。
- **綁定**：`classes.sjc` 裡記了它是為哪個模型建的（model-v5 的 SHA-256）。缺檔、配到別的模型、或檔案內容不對，載入都會失敗（C ABI 的 `load_lm` 回傳 3），不會靜靜地用錯或不用。
- **App 裡**：打包時兩個檔案都放進 `善解輸入法.app/Contents/Resources/`，使用者不用另外下載。

## 下載

```sh
gh release download model-v5 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v3 -R Nanako0129/shanjie -p classes.sjc -D data/lm
shasum -a 256 -c data/bigram.sjlm.sha256 data/classes.sjc.sha256
```

沒有登入 `gh` 的話，改用 `https://github.com/Nanako0129/shanjie/releases/download/<Release>/<檔名>` 下載。

## 版本怎麼往前走

| 改了什麼 | 要發的新 Release |
|---|---|
| 語料、計數或模型格式（重建 `bigram.sjlm`） | `model-v6`，而且詞類表也要重建：`classes-v4`（舊的詞類表綁的是舊模型的雜湊，配新模型會載入失敗） |
| 只重新分群（類別數、μ、分群用的資料） | `classes-v4`，模型不變（`eval/golden/sp-predict.txt`、`sw-probe.txt` 的分數也依賴詞類表，要重產） |

舊的 Release 保留，可以回到那一版；例外是 model-v3 與 classes-v1，在 `docs/contracts/model-v4.md` §4 第 4 步的條件成立後移除。下一次重建模型時，發佈的檔案要先加上指紋條目再算雜湊，詞類表也對加過的檔案建。換版時同一個 PR 更新：雜湊檔（`data/*.sha256`）、CI 兩個 job 與 `release.yml` 的下載、`cli/tests/golden.rs` 的雜湊與訊息、`core/tests/engine_*.rs` 的缺檔訊息、`macos/Tests/ShanjieKitTests/Support.swift`、`scripts/build-app.sh` 的提示與內附的署名文字、README、CONTRIBUTING、`docs/verification.md`、`LICENSES/data.md`、`docs/PLAN.md` 的模型檔那一條，以及這份文件；依模型的 golden 照各自的產生方式重產（`docs/contracts/model-v5.md` §2.5 的 PR 2 是完整的例子）。

## 怎麼產生

| 檔案 | 工具 | 規格與量測 |
|---|---|---|
| `bigram.sjlm` | `tools/build_lm.py`（格式說明在檔頭）；model-v5 的計數加了 `--extra-lexicon`（加法計數，計數詞表由 `experiments/model-v5/count_lexicon.py` 產生）。發佈的檔案另含指紋條目，加的工具不在 repo，所以自己重建的模型雜湊不同 | `docs/contracts/model-v5.md`、`experiments/model-v5/README.md`（之前的配方：`docs/contracts/s2f-variant-forms.md`、`experiments/s2f/README.md`） |
| `classes.sjc` | `tools/build_classes.py`（格式說明在檔頭），輸入是分群結果與加過指紋的模型；classes-v3 的分群用 `experiments/s2-classes/cluster.py --keep-from`（沿用 classes-v2 那次的類別，188 上的資料），所以自己從頭分群的結果不同 | `docs/contracts/model-v5.md` §9、`docs/contracts/s2k-word-classes.md`、`experiments/s2-classes/README.md` |

## 授權

兩個檔案都是 **CC BY-SA 4.0**，來自同一批語料的詞頻計數，署名與來源見 `LICENSES/data.md`。不含任何私人對話資料。
