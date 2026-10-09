# model-v5 計數批次（188）

契約：`docs/contracts/model-v5.md` §2.1、§2.5 步驟 2–3。腳本改動：`experiments/s2/build_counts.py`、`build_counts_text.py` 的 `--extra-lexicon FILE`（可給多次；疊加層格式，接在 `overlay-add.tsv` 後面）。單元測試：`experiments/s2/test_extra_lexicon.py`、`experiments/model-v5/test_compare_counts.py`。

兩個批次檔都是**範本**：頂端的 `REPO`、`SYNTH` 先換成 188 上的絕對路徑。慣例同 S2n／S2f：絕對路徑、`start "" /b /wait /affinity FFF`、`PYTHONUTF8=1`。`S2_WORK` 都指向新目錄，model-v3 的計數（`work\s2f4`）只讀、不會被寫；目標目錄已有 `counts-200000.pkl` 就停。

| 檔 | `S2_WORK` | `--extra-lexicon` | 產出 |
|---|---|---|---|
| `repro.bat` | `work\modelv5-repro` | 無 | 口語與維基計數、`bigram-repro.sjlm` |
| `count.bat` | `work\modelv5` | `data\packs\acg-add.tsv` | 同上、`bigram-v5-nofp.sjlm`（尚未加指紋） |

先跑 `repro.bat` 做重現核對，全部成立才跑 `count.bat`（A2 已合併、本分支已 rebase、`acg-add.tsv` 的 SHA-256 等於 main 的）。口語訓練檔直接用 model-v3 的 `%M3%\tune\colloquial-train.txt`，不重產。

## 輸入雜湊（填表）

批次檔開頭會把這些檔的 SHA-256 寫進 `%S2_WORK%\inputs.sha256`。model-v3 的值取自 model-v3 建置那個 commit 的檔案與 188 `work\s2f4` 的紀錄。除了 `acg-add.tsv`，每一列都必須相同。

| 輸入 | model-v3 | repro | count |
|---|---|---|---|
| `zhwiki-20261001-pages-articles.xml.bz2` | | | |
| `colloquial-train.txt` | | | |
| `synth.txt` | | | |
| `mcbpmf-data.txt` | | | |
| `overlay-add.tsv` | | | |
| `TWVariants.txt` | `245b94eb…` | | |
| `STCharacters.txt` | | | |
| `acg-add.tsv` | （無） | （不用） | 等於 main 的 |

## 重現核對（`repro.bat` 的產出對 model-v3）

任何一項不成立就停。

1. **口語計數，位元組比對**：`certutil -hashfile %S2_WORK%\counts-colloquial3.pkl SHA256` 等於 `13d0ab5b…`（`docs/contracts/s2k-word-classes.md`）。
2. **維基計數，比內容**（`imap_unordered` 讓位元組不可重現）：

   ```
   python3 experiments\model-v5\compare_counts.py %M3%\counts-200000.pkl %S2_WORK%\counts-200000.pkl
   ```

   鍵集合（`uni`、`bi`）、`runs`、`articles` 相同，每個值相對誤差 ≤ 1e-9（`--tol` 預設值）；印 `OK` 結束碼 0。model-v3 的檔 SHA-256 應是 `ca2f1361…`，先確認。
3. **重建模型的第一名**：把 `bigram-repro.sjlm` 拿到 Mac。同一次、同一個 CLI 執行檔，兩邊都 `--no-classes`、都不加 `--packs`，對 `bigram-repro.sjlm`（不加指紋）和維護者本機的 model-v3（也不加指紋），跑 dev302、打字測驗、錯字回報，聊天與書面、加 `--context`；每一組兩邊的 `top1_sha256` 要相同。用 `--no-classes` 是因為詞類表綁模型雜湊。模型位元組不同時，記下差了幾個條目，但不以此為停止條件。

## 之後

`count.bat` 的 `bigram-v5-nofp.sjlm` 交給 main 加指紋、重新分群與 `build_classes.py`（§2.5 步驟 3 後半），這些不在批次檔裡。
