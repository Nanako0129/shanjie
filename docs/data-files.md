# 模型與資料檔

善解的語言模型、詞類表和語意校正模型很大，不放進 git，放在 GitHub Release 當附件。repo 裡只記它們的 SHA-256，CI、打包 App 與測試都先比對雜湊再用。這些 Release **不是輸入法的發行版**（發行版是 `v0.x.y`）。

## 現在用的檔案

| 檔案 | Release | 內容 | 大小 | 雜湊檔 |
|---|---|---|---|---|
| `bigram.sjlm` | [`model-v5`](https://github.com/Nanako0129/shanjie/releases/tag/model-v5) | bigram 語言模型：詞與詞相鄰出現的機率；含少量用來辨識來源的指紋條目 | 82,535,147 bytes | `data/bigram.sjlm.sha256` |
| `classes.sjc` | [`classes-v3`](https://github.com/Nanako0129/shanjie/releases/tag/classes-v3) | 詞類表（S2k）：512 個詞類、每個詞屬於哪一類、「這類詞後面接那類詞」的機率。兩個詞沒在語料一起出現過時，用它補估計 | 5,575,836 bytes | `data/classes.sjc.sha256` |
| `tinylm.sjtl` | [`tinylm-v3`](https://github.com/Nanako0129/shanjie/releases/tag/tinylm-v3) | 「語意校正」用的小型字元語言模型（S7，SJTL0002）：6 層、字表 9,714 字，二維權重 int8；在 n-gram 的前幾名裡重新排序 | 14,638,224 bytes | `data/tinylm.sjtl.sha256` |

- **放在同一個資料夾**（`data/lm/`）。前兩個一起用；`classes.sjc` 不含模型本身，`classes-v3` 這個 Release 也只有這一個檔案。
- **綁定**：`classes.sjc` 裡記了它是為哪個模型建的（model-v5 的 SHA-256）。缺檔、配到別的模型、或檔案內容不對，載入都會失敗（C ABI 的 `load_lm` 回傳 3），不會靜靜地用錯或不用。
- **語意校正模型的綁定**：`tinylm.sjtl` 裡記了它調參時用的模型（model-v5 的 SHA-256），配到別的模型時核心拒絕載入、只用 n-gram。它不檢查詞類表，但它的 α、τ 是在 model-v5＋classes-v3 上調的，所以換詞類表也要重新調參、發新的 `tinylm-v…`（見下面的表）。
- **英文單字表**（中英混打，`docs/contracts/zhen-mixed.md`）不在這張表：它不是 Release 附件，也不進 git，每次建置時由 `scripts/build-app.sh` 產生（見下面「建置時產生的檔案」）。
- **App 裡**：打包時 `bigram.sjlm`、`classes.sjc` 放進 `善解輸入法.app/Contents/Resources/`，使用者不用另外下載。`tinylm.sjtl` 從語意校正上線（S7c-2b，`docs/contracts/s7c2b-shell-rerank.md`）起也一起放進去。

## 建置時產生的檔案

| 檔案 | 來源 | 雜湊 | 怎麼產生 |
|---|---|---|---|
| `english-words.txt`（`Resources/` 裡，中英混打的英文單字表） | SymSpell（`wolfgarbe/SymSpell`）釘住的 commit `c239062ae02961df18ab7da1671d01b4388204e0` 的 `SymSpell/frequency_dictionary_en_82_765.txt`，輸入 SHA-256 `c604e1121e398ae7c7fbf777f11e0a0f2fa66eda932cb9fba1321466cf3acd7b` | 輸出的 SHA-256 記在 `data/english-words.txt.sha256`（`3352d050…`，82,730 詞；切法 `LEN3_CAP` 3000、`LEN4_CAP` 不設上限，由 `docs/contracts/zhen-mixed.md` §4.4 的量測決定，研究紀錄 2026-10-14）；不符就建置失敗 | `scripts/build-app.sh` 在建置時下載輸入（快取在 `~/.cache/shanjie/english/`，有快取且雜湊相符就不再下載；下載失敗或雜湊不符就建置失敗），用 `tools/build_english_words.py`（切法與常數在檔頭；測試 `tools/test_build_english_words.py`）產生，核對輸出雜湊後放進 `Resources/`。授權見 `LICENSES/data.md` 與 `LICENSES/SCOWL.txt`、`LICENSES/SymSpell-MIT.txt` |

## 下載

```sh
gh release download model-v5 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v3 -R Nanako0129/shanjie -p classes.sjc -D data/lm
gh release download tinylm-v3 -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm
shasum -a 256 -c data/bigram.sjlm.sha256 data/classes.sjc.sha256 data/tinylm.sjtl.sha256
```

沒有登入 `gh` 的話，改用 `https://github.com/Nanako0129/shanjie/releases/download/<Release>/<檔名>` 下載。

## 版本怎麼往前走

| 改了什麼 | 要發的新 Release |
|---|---|
| 語料、計數或模型格式（重建 `bigram.sjlm`） | `model-v6`，而且詞類表也要重建：`classes-v4`（舊的詞類表綁的是舊模型的雜湊，配新模型會載入失敗）；語意校正模型也要重新調參，發下一個 `tinylm-v…`（舊的綁舊模型的雜湊，配新模型不會載入） |
| 只重新分群（類別數、μ、分群用的資料） | `classes-v4`，模型不變（`eval/golden/sp-predict.txt`、`sw-probe.txt` 的分數也依賴詞類表，要重產）；語意校正模型的 α、τ、δ 要重新調，發下一個 `tinylm-v…`（雜湊檔不會攔下這種錯配，要靠這張表） |
| 語意校正模型的訓練、大小、匯出格式或融合參數 | 下一個 `tinylm-v…`，n-gram 模型與詞類表不變（`tinylm-v2`、`tinylm-v3` 都是只換融合參數） |

舊的 Release 保留，可以回到那一版；例外是 model-v3 與 classes-v1，在 `docs/contracts/model-v4.md` §4 第 4 步的條件成立後移除。下一次重建模型時，發佈的檔案要先加上指紋條目再算雜湊，詞類表也對加過的檔案建。換版時同一個 PR 更新：雜湊檔（`data/*.sha256`）、CI 兩個 job 與 `release.yml` 的下載、`cli/tests/golden.rs` 的雜湊與訊息、`core/tests/engine_*.rs` 的缺檔訊息、`macos/Tests/ShanjieKitTests/Support.swift`、`scripts/build-app.sh` 的提示與內附的署名文字、README、CONTRIBUTING、`docs/verification.md`、`LICENSES/data.md`、`docs/PLAN.md` 的模型檔那一條，以及這份文件；換 `tinylm.sjtl` 時另外照 `docs/releases/README.md` 的上傳與核對步驟；換 `tinylm.sjtl` 而融合參數有變時，要重新量 selftest 的那一句還會被重排改掉（`Selftest.rerankRowStandard`、`rerankedText`、`ngramText`，與測試的 `RerankRow`），不會就重挑（tinylm-v2 的嚴格聊天值就讓原本的第 25 列不再被改，改用第 65 列；tinylm-v3 下第 65 列仍會被改，第 25 列又會被改，是實機句）；依模型的 golden 照各自的產生方式重產（`docs/contracts/model-v5.md` §2.5 的 PR 2 是完整的例子）。

## 怎麼產生

三個檔案從語料到發佈的完整流程（輸入、步驟順序、在哪裡訓練、重現與缺口）整理在 `docs/training.md`；下表只列工具與規格的位置。

| 檔案 | 工具 | 規格與量測 |
|---|---|---|
| `bigram.sjlm` | `tools/build_lm.py`（格式說明在檔頭）；model-v5 的計數加了 `--extra-lexicon`（加法計數，計數詞表由 `experiments/model-v5/count_lexicon.py` 產生）。發佈的檔案另含指紋條目，加的工具不在 repo，所以自己重建的模型雜湊不同 | `docs/contracts/model-v5.md`、`experiments/model-v5/README.md`（之前的配方：`docs/contracts/s2f-variant-forms.md`、`experiments/s2f/README.md`） |
| `classes.sjc` | `tools/build_classes.py`（格式說明在檔頭），輸入是分群結果與加過指紋的模型；classes-v3 的分群用 `experiments/s2-classes/cluster.py --keep-from`（沿用 classes-v2 那次的類別，188 上的資料），所以自己從頭分群的結果不同 | `docs/contracts/model-v5.md` §9、`docs/contracts/s2k-word-classes.md`、`experiments/s2-classes/README.md` |
| `tinylm.sjtl` | `experiments/s7a-tinylm/train.py` 訓練、`export_sjtl.py` 匯出（格式說明在檔頭），融合參數由 `fusegrid.py` 調 | `docs/contracts/s7a-tinylm.md`、`docs/contracts/s7c-core-tinylm.md`、`experiments/s7a-tinylm/README.md` |

## 授權

三個檔案都是 **CC BY-SA 4.0**：前兩個是語料的詞頻計數，`tinylm.sjtl` 是用同一批語料訓練的權重；署名與來源見 `LICENSES/data.md`。不含任何私人對話資料。
