# 模型與資料檔

善解的語言模型、詞類表和語意校正模型很大，不放進 git，放在 GitHub Release 當附件。repo 裡只記它們的 SHA-256，CI、打包 App 與測試都先比對雜湊再用。這些 Release **不是輸入法的發行版**（發行版是 `v0.x.y`）。

## 現在用的檔案

| 檔案 | Release | 內容 | 大小 | 雜湊檔 |
|---|---|---|---|---|
| `bigram.sjlm` | [`model-v6`](https://github.com/Nanako0129/shanjie/releases/tag/model-v6) | bigram 語言模型：詞與詞相鄰出現的機率；含少量用來辨識來源的指紋條目 | 83,445,569 bytes | `data/bigram.sjlm.sha256` |
| `classes.sjc` | [`classes-v5`](https://github.com/Nanako0129/shanjie/releases/tag/classes-v5) | 詞類表（S2k）：512 個詞類、每個詞屬於哪一類、「這類詞後面接那類詞」的機率。兩個詞沒在語料一起出現過時，用它補估計；classes-v5 和 classes-v4 只差權重 μ（0.8 → 0.2） | 5,656,826 bytes | `data/classes.sjc.sha256` |
| `kn.sjkn` | [`kn-v1`](https://github.com/Nanako0129/shanjie/releases/tag/kn-v1) | Kneser–Ney 側檔（SJKN0002，`docs/contracts/kn-core.md`）：每個詞的接續次數，θ 1、β 1；放在模型旁邊時核心就開 KN 回退分布 | 1,414,052 bytes | `data/kn.sjkn.sha256` |
| `trigram.sjt3` | [`sj-cx-1-7.7m`](https://github.com/Nanako0129/shanjie/releases/tag/sj-cx-1-7.7m) | 書面設定用的 trigram 表（SJT30001，`docs/contracts/trigram-core.md`）：4,101,808 個兩詞前文、7,700,964 條剪枝後的次數，綁 model-v6 | 143,643,936 bytes | `data/trigram.sjt3.sha256` |
| `tinylm.sjtl` | [`tinylm-v5`](https://github.com/Nanako0129/shanjie/releases/tag/tinylm-v5) | 「語意校正」用的小型字元語言模型（S7，SJTL0004）：6 層、字表 9,714 字，二維權重 int8；在 n-gram 的前幾名裡重新排序；檔頭除了標準的融合參數，還有語意校正強度的保守與積極兩檔，以及調參時用的 `kn.sjkn` 的 SHA-256 | 14,638,352 bytes | `data/tinylm.sjtl.sha256` |

- **放在同一個資料夾**（`data/lm/`）。前三個一起用；`classes.sjc` 不含模型本身，`classes-v5` 這個 Release 也只有這一個檔案。
- **綁定**：`classes.sjc` 裡記了它是為哪個模型建的（model-v6 的 SHA-256）。缺檔、配到別的模型、或檔案內容不對，載入都會失敗（C ABI 的 `load_lm` 回傳 3），不會靜靜地用錯或不用。
- **Kneser–Ney 側檔的綁定**：`kn.sjkn` 裡記了模型的 SHA-256（model-v6）；模型旁邊有它，核心就開 KN，缺了就不開；檔案壞掉或配到別的模型時載入失敗（C ABI 的 `load_lm` 回傳 3）。
- **trigram 表（`trigram.sjt3`）**：SJT30001（`docs/contracts/trigram-core.md`、`reference/proto/trigram.py`），放在模型旁邊；檔頭綁模型的 SHA-256，核心載入時核對（壞了或配到別的模型回傳 3，沒有這個檔就關閉）。只有書面設定用它（書面有表時 KN 關）。由 `tools/build_trigram.py` 把研究用的表重新對應到出貨模型的詞 id。附件在 `sj-cx-1-7.7m`，`data/trigram.sjt3.sha256` 記雜湊，CI、發布與 App 都放；缺檔時 `cli/tests/golden.rs`、Swift 測試與 `scripts/build-app.sh` 以下載訊息失敗；Rust CLI 只在 `--profile formal` 且模型旁有它時載入（聊天與預測模式不讀），`--no-tri` 關閉，摘要行加 `+tri:<sha8>`。
- **語意校正模型的綁定**：`tinylm.sjtl` 裡記了它調參時用的模型（model-v6 的 SHA-256）與 KN 側檔（kn-v1 的 SHA-256，SJTL0004），任何一個配不上，核心就拒絕載入、只用 n-gram。它不檢查詞類表，但它的 α、τ、δ 是在 model-v6＋classes-v4＋kn-v1 上調的，所以重新分群也要重新調參、發新的 `tinylm-v…`（見下面的表）；classes-v5 只改 μ，是開著 tinylm-v5 量過才選的，沿用 tinylm-v5。
- **英文單字表**（中英混打，`docs/contracts/zhen-mixed.md`）不在這張表：它不是 Release 附件，也不進 git，每次建置時由 `scripts/build-app.sh` 產生（見下面「建置時產生的檔案」）。
- **App 裡**：打包時 `bigram.sjlm`、`classes.sjc`、`kn.sjkn`（model-v6 起）、`trigram.sjt3` 放進 `善解輸入法.app/Contents/Resources/`，使用者不用另外下載。`tinylm.sjtl` 從語意校正上線（S7c-2b，`docs/contracts/s7c2b-shell-rerank.md`）起也一起放進去。

## 建置時產生的檔案

| 檔案 | 來源 | 雜湊 | 怎麼產生 |
|---|---|---|---|
| `english-words.txt`（`Resources/` 裡，中英混打的英文單字表） | SymSpell（`wolfgarbe/SymSpell`）釘住的 commit `c239062ae02961df18ab7da1671d01b4388204e0` 的 `SymSpell/frequency_dictionary_en_82_765.txt`，輸入 SHA-256 `c604e1121e398ae7c7fbf777f11e0a0f2fa66eda932cb9fba1321466cf3acd7b` | 輸出的 SHA-256 記在 `data/english-words.txt.sha256`（`3352d050…`，82,730 詞；切法 `LEN3_CAP` 3000、`LEN4_CAP` 不設上限，由 `docs/contracts/zhen-mixed.md` §4.4 的量測決定，研究紀錄 2026-10-14）；不符就建置失敗 | `scripts/build-app.sh` 在建置時下載輸入（快取在 `~/.cache/shanjie/english/`，有快取且雜湊相符就不再下載；下載失敗或雜湊不符就建置失敗），用 `tools/build_english_words.py`（切法與常數在檔頭；測試 `tools/test_build_english_words.py`）產生，核對輸出雜湊後放進 `Resources/`。授權見 `LICENSES/data.md` 與 `LICENSES/SCOWL.txt`、`LICENSES/SymSpell-MIT.txt` |

## 下載

```sh
gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v5 -R Nanako0129/shanjie -p classes.sjc -D data/lm
gh release download kn-v1 -R Nanako0129/shanjie -p kn.sjkn -D data/lm
gh release download tinylm-v5 -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm
gh release download sj-cx-1-7.7m -R Nanako0129/shanjie -p trigram.sjt3 -D data/lm
shasum -a 256 -c data/bigram.sjlm.sha256 data/classes.sjc.sha256 data/kn.sjkn.sha256 data/tinylm.sjtl.sha256 data/trigram.sjt3.sha256
```

沒有登入 `gh` 的話，改用 `https://github.com/Nanako0129/shanjie/releases/download/<Release>/<檔名>` 下載。

## 版本怎麼往前走

| 改了什麼 | 要發的新 Release |
|---|---|
| 語料、計數或模型格式（重建 `bigram.sjlm`） | `model-v7`，而且詞類表也要重建：下一個 `classes-v…`（舊的詞類表綁的是舊模型的雜湊，配新模型會載入失敗）；KN 側檔也要重產：下一個 `kn-v…`（綁模型的雜湊）；語意校正模型也要重新調參，發下一個 `tinylm-v…`（舊的綁舊模型與舊側檔的雜湊，配新的不會載入）；trigram 表也要重發，見下面第一列之後的「重建 `bigram.sjlm` 時，trigram 表也要重發」 |
| 只重新分群（類別數、分群用的資料） | 下一個 `classes-v…`，模型不變（`eval/golden/sp-predict.txt`、`sw-probe.txt` 的分數也依賴詞類表，要重產）；語意校正模型的 α、τ、δ 要重新調，發下一個 `tinylm-v…`（雜湊檔不會攔下這種錯配，要靠這張表） |
| 只改詞類權重 μ（檔頭一個值，`experiments/lytune-attrib/set_mu.py`） | 下一個 `classes-v…`，依詞類表的 golden 一樣重產；μ 是開著出貨的小模型量過才選的，就沿用那個 `tinylm-v…`，沒量過就照上一列重調（classes-v5，`docs/contracts/classes-v5.md` §2 第 3 點） |
| 重建 `bigram.sjlm` 時，trigram 表也要重發 | 新的 trigram 表 Release（`sj-cx-…`）：`trigram.sjt3` 綁模型的 SHA-256，配不上的表會讓 `Lm::load_opts` 對每個設定都載入失敗（不只書面），App 就整個沒有語言模型，所以換模型一定要同時換表（`tools/build_trigram.py` 重新對應到新模型的詞 id；`data/trigram.sjt3.sha256`、CI／`release.yml`／`build-app.sh` 的下載與 golden 跟著換） |
| 語意校正模型的訓練、大小、匯出格式或融合參數 | 下一個 `tinylm-v…`，n-gram 模型與詞類表不變（`tinylm-v2`、`tinylm-v3` 都是只換融合參數；`tinylm-v4` 換檔頭格式、加強度兩檔；`tinylm-v5` 是 SJTL0004，記 KN 側檔的雜湊） |

舊的 Release 保留，可以回到那一版；例外是 model-v3 與 classes-v1，在 `docs/contracts/model-v4.md` §4 第 4 步的條件成立後移除。下一次重建模型時，發佈的檔案要先加上指紋條目再算雜湊，詞類表也對加過的檔案建。換版時同一個 PR 更新：雜湊檔（`data/*.sha256`）、CI 兩個 job 與 `release.yml` 的下載、`cli/tests/golden.rs` 的雜湊與訊息、`core/tests/engine_*.rs` 的缺檔訊息、`macos/Tests/ShanjieKitTests/Support.swift`、`scripts/build-app.sh` 的提示與內附的署名文字、README、CONTRIBUTING、`docs/verification.md`、`LICENSES/data.md`、`docs/PLAN.md` 的模型檔那一條，以及這份文件；換 `tinylm.sjtl` 時另外照 `docs/releases/README.md` 的上傳與核對步驟；換 `tinylm.sjtl` 而融合參數有變時，要重新量 selftest 的那一句還會被重排改掉（`Selftest.rerankRowStandard`、`rerankedText`、`ngramText`，與測試的 `RerankRow`），不會就重挑（tinylm-v2 的嚴格聊天值就讓原本的第 25 列不再被改，改用第 65 列；tinylm-v3 下第 65 列仍會被改，第 25 列又會被改，是實機句；tinylm-v4 的標準值與 v3 相同，第 65 列照樣被改，沒有換列）；換 `tinylm.sjtl` 的檔頭格式時，另外改 `experiments/s7a-tinylm/export_sjtl.py` 的讀檔器與 `tools/triage.py` 的 `tinylm_params`（它用那個讀檔器，不另寫解析），並在 `tools/test_triage.py` 補新版本的案例（SJTL0003 曾因此讓分流工具崩潰）；依模型的 golden 照各自的產生方式重產（`docs/contracts/model-v5.md` §2.5 的 PR 2 是完整的例子）。

## 怎麼產生

三個檔案從語料到發佈的完整流程（輸入、步驟順序、在哪裡訓練、重現與缺口）整理在 `docs/training.md`；下表只列工具與規格的位置。

| 檔案 | 工具 | 規格與量測 |
|---|---|---|
| `bigram.sjlm` | `tools/build_lm.py`（格式說明在檔頭）；model-v5 起計數加了 `--extra-lexicon`（加法計數，計數詞表由 `experiments/model-v5/count_lexicon.py` 產生），model-v6 另加名單詞的計數詞表、口語補次數；model-v6 是在 PR 1（`feat/model-v6`）的 commit 建的，那時名單層還沒進 `ime.OVERLAYS`，所以建置指令帶 `--extra-overlay data/lexicon/names-add.tsv`；在 HEAD（名單層已常開）重跑**不要**帶這個參數，帶了名單會讀兩次（完整指令與說明在 `experiments/model-v6/README.md`）。發佈的檔案另含指紋條目，加的工具不在 repo，所以自己重建的模型雜湊不同 | `docs/contracts/model-v6.md`、`experiments/model-v6/README.md`、`docs/contracts/model-v5.md`、`experiments/model-v5/README.md`（之前的配方：`docs/contracts/s2f-variant-forms.md`、`experiments/s2f/README.md`） |
| `classes.sjc` | `tools/build_classes.py`（格式說明在檔頭），輸入是分群結果與加過指紋的模型；classes-v3、classes-v4 的分群用 `experiments/s2-classes/cluster.py --keep-from`（沿用前一版的類別，188 上的資料；classes-v4 另用 `experiments/model-v6/force_classes.py` 把罕見量詞搬進「個」的類別），所以自己從頭分群的結果不同；classes-v5 是 classes-v4 用 `experiments/lytune-attrib/set_mu.py` 把 μ 改成 0.2（`tools/build_classes.py` 的 `MU` 也跟著改成 0.2） | `docs/contracts/model-v6.md` §2.2b、`docs/contracts/model-v5.md` §9、`docs/contracts/s2k-word-classes.md`、`experiments/s2-classes/README.md` |
| `tinylm.sjtl` | `experiments/s7a-tinylm/train.py` 訓練、`export_sjtl.py` 匯出（格式說明在檔頭），融合參數由 `fusegrid.py` 調 | `docs/contracts/s7a-tinylm.md`、`docs/contracts/s7c-core-tinylm.md`、`experiments/s7a-tinylm/README.md` |

## 授權

三個檔案都是 **CC BY-SA 4.0**：前兩個是語料的詞頻計數，`tinylm.sjtl` 是用同一批語料訓練的權重；署名與來源見 `LICENSES/data.md`。不含任何私人對話資料。
