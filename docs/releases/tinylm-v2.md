善解「語意校正」用的小型字元語言模型（SJTL0002 格式）。權重和 `tinylm-v1` 相同，差別在融合參數：多了門檻 δ，參數改成 S7e 的嚴格值，目標是少把對的字改錯。**這不是輸入法的發行版。**

- 檔案：`tinylm.sjtl`，14,638,224 bytes
- SHA-256：`710249c2988d7cd08af6b85ea960d0b4e8a8c23eb8e8fd44264171c3628ed7db`
- **需要的核心**：SJTL0002 的檔頭多了 δ，要含 S7c-2c（[#122](https://github.com/Nanako0129/shanjie/pull/122) ）之後的核心才讀得了；在那之前的核心只認得 SJTL0001。#122 之後，核心的語意校正預設關閉。輸入法要等改讀這個檔的那一版（[#118](https://github.com/Nanako0129/shanjie/pull/118) ）才會用到它。
- 內容：和 `tinylm-v1` 相同的 1,440 萬參數字元語言模型（6 層、d 384、字表 9,714 字加 PAD、UNK、BOS 共 9,717 個 id；二維權重每列對稱 int8 加 float32 縮放，一維向量 float32）。只能在 n-gram 解碼出的前 8 名候選之間重排，不會產生新的文字。
- 融合參數（S7e，`docs/contracts/s7e-fewer-breaks.md`）：聊天 α 0.2、τ 1、δ 0.25；書面 α 0.7、τ 1、δ 2。δ 是門檻：融合分數最高的候選，要比 n-gram 第一名高出 δ 以上才換（`docs/contracts/s7c2c-delta.md`）。
- **綁定**：和 `tinylm-v1` 相同，檔案記了 [model-v5](https://github.com/Nanako0129/shanjie/releases/tag/model-v5) `bigram.sjlm` 的 SHA-256，配到別的 n-gram 模型就拒絕載入；這個核對不看詞類表，參數是在 model-v5＋[classes-v3](https://github.com/Nanako0129/shanjie/releases/tag/classes-v3) 上調的。換 n-gram 模型或詞類表都要重新調參、發新的檔（`docs/data-files.md`）。
- 產生：S7a 訓練出的 `tinylm-M.npz`（SHA-256 `32308122782d3c5aba8a838521f031a466671d47d48ec0f54dcfb9256451815e`，沒有另外發布；訓練見 `docs/contracts/s7a-tinylm.md`），用 `experiments/s7a-tinylm/export_sjtl.py` 以 `--type int8 --chat 0.2 1 0.25 --formal 0.7 1 2` 匯出（`docs/contracts/s7c2c-delta.md`）。
- 量測（Rust 的 int8 路徑，對 n-gram 第一名；細節在研究紀錄 2026-10-12 的 S7e 與 S7c-2c 兩節）：公開集合 12 格和 S7e 的表逐格相同，例如聊天 dev302 241 → 249（改對 9／改壞 1）、cvtune 48／2、wikitune 81／0；使用者的聊天調參集（只記數字）聊天改對 70／改壞 10、書面 30／1。保留集（fresh verifier，2026-10-12）：聊天 180 → 187（改對 7／改壞 0，p = 0.0156），書面 184 → 187（3／0，p = 0.25）。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。訓練文字和 `tinylm-v1` 相同，和 model-v5 的 `bigram.sjlm` 同源：
  - 中文維基百科 2026-10-01 dump 的前 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
- 不含任何私人對話資料。

下載（下載到另一個資料夾：#118 換用 v2 之前，repo 的 `data/tinylm.sjtl.sha256` 記的還是 `tinylm-v1`，蓋掉 `data/lm/tinylm.sjtl` 會讓 `shasum -a 256 -c data/*.sha256` 失敗）：

```sh
gh release download tinylm-v2 -R Nanako0129/shanjie -p tinylm.sjtl -D tinylm-v2 \
  && echo "710249c2988d7cd08af6b85ea960d0b4e8a8c23eb8e8fd44264171c3628ed7db  tinylm-v2/tinylm.sjtl" | shasum -a 256 -c
```
