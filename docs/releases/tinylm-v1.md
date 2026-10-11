善解「語意校正」用的小型字元語言模型（SJTL0001 格式），CI 與發布版從這裡下載。**這不是輸入法的發行版。**

- 檔案：`tinylm.sjtl`，14,638,208 bytes
- SHA-256：`8725204cf590d08bd0660d6170a23678a708155cee6690fbdf2b471fc8389bdb`
- 內容：一個 1,440 萬參數的字元語言模型（6 層、d 384、字表 9,717 字），權重以每列對稱 int8 加 float32 縮放存放，載入時還原成 float32。用途是在 n-gram 解碼出的前 8 名候選之間重排，只能從這 8 個候選裡挑，不會產生新的文字。聊天與書面設定的融合參數都是 α 0.7、τ 2。
- **綁定**：檔案裡記了它調參時用的 n-gram 模型的 SHA-256（[model-v5](https://github.com/Nanako0129/shanjie/releases/tag/model-v5) 的 `bigram.sjlm`）。配到別的 n-gram 模型時載入會失敗、輸入法照常只用 n-gram，不會用錯。之後換 n-gram 模型（model-v6）要一起發新的語意校正模型。
- 產生：訓練與匯出見 `docs/contracts/s7a-tinylm.md`（訓練）、`docs/contracts/s7c-core-tinylm.md`（`experiments/s7a-tinylm/export_sjtl.py` 匯出 int8）。
- 量測（Rust 的 int8 路徑，對 n-gram 第一名，聊天設定）：dev302 241 → 276、錯字回報 15 → 32、保留集 180 → 200（227 句，fresh verifier 跑一次）；每個集合、兩種設定都沒有顯著變差的格。細節在 `docs/research-log.md` 的 S7a、S7c-1 兩節。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。訓練文字和 model-v5 的 `bigram.sjlm` 同源：
  - 中文維基百科 2026-10-01 dump 的前 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
- 不含任何私人對話資料。

下載：

```sh
gh release download tinylm-v1 -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm
shasum -a 256 -c data/tinylm.sjtl.sha256
```
