# 模型與資料檔

善解的語言模型和詞類表很大，不放進 git，放在 GitHub Release 當附件。repo 裡只記它們的 SHA-256，CI、打包 App 與測試都先比對雜湊再用。這兩個 Release **不是輸入法的發行版**（發行版是 `v0.x.y`）。

## 現在用的兩個檔案

| 檔案 | Release | 內容 | 大小 | 雜湊檔 |
|---|---|---|---|---|
| `bigram.sjlm` | [`model-v3`](https://github.com/Nanako0129/shanjie/releases/tag/model-v3) | bigram 語言模型：詞與詞相鄰出現的機率 | 81,373,869 bytes | `data/bigram.sjlm.sha256` |
| `classes.sjc` | [`classes-v1`](https://github.com/Nanako0129/shanjie/releases/tag/classes-v1) | 詞類表（S2k）：512 個詞類、每個詞屬於哪一類、「這類詞後面接那類詞」的機率。兩個詞沒在語料一起出現過時，用它補估計 | 5,443,546 bytes | `data/classes.sjc.sha256` |

- **兩個一起用，放在同一個資料夾**（`data/lm/`）。`classes.sjc` 不含模型本身，`classes-v1` 這個 Release 也只有這一個檔案。
- **綁定**：`classes.sjc` 裡記了它是為哪個模型建的（model-v3 的 SHA-256）。缺檔、配到別的模型、或檔案內容不對，載入都會失敗（C ABI 的 `load_lm` 回傳 3），不會靜靜地用錯或不用。
- **App 裡**：打包時兩個檔案都放進 `善解輸入法.app/Contents/Resources/`，使用者不用另外下載。

## 下載

```sh
gh release download model-v3 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v1 -R Nanako0129/shanjie -p classes.sjc -D data/lm
shasum -a 256 -c data/bigram.sjlm.sha256 data/classes.sjc.sha256
```

沒有登入 `gh` 的話，改用 `https://github.com/Nanako0129/shanjie/releases/download/<Release>/<檔名>` 下載。

## 版本怎麼往前走

| 改了什麼 | 要發的新 Release |
|---|---|
| 語料、計數或模型格式（重建 `bigram.sjlm`） | `model-v4`，而且詞類表也要重建：`classes-v2`（舊的詞類表綁的是舊模型的雜湊，配新模型會載入失敗） |
| 只重新分群（類別數、μ、分群用的資料） | `classes-v2`，模型不變 |

舊的 Release 一直保留，可以回到任何一版。換版時同一個 PR 更新雜湊檔、CI 與 `release.yml` 的下載、`cli/tests/golden.rs` 的雜湊、`scripts/build-app.sh` 的提示，以及這份文件。

## 怎麼產生

| 檔案 | 工具 | 規格與量測 |
|---|---|---|
| `bigram.sjlm` | `tools/build_lm.py`（格式說明在檔頭） | `docs/contracts/s2f-variant-forms.md`、`experiments/s2f/README.md` |
| `classes.sjc` | `tools/build_classes.py`（格式說明在檔頭），輸入是第一段的分群結果與模型 | `docs/contracts/s2k-word-classes.md`、`experiments/s2-classes/README.md` |

## 授權

兩個檔案都是 **CC BY-SA 4.0**，來自同一批語料的詞頻計數，署名與來源見 `LICENSES/data.md`。不含任何私人對話資料。
