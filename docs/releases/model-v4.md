善解的 bigram 語言模型（SJLM0001 格式），CI 與發布版從這裡下載。**這不是輸入法的發行版。**

- 檔案：`bigram.sjlm`，81,373,933 bytes
- SHA-256：`06768f2949cf8b135d1f591056ffb16f3ae3f6d70aef5911ffd55de134250322`
- 產生：語料與建置流程同 model-v3（`tools/build_lm.py`，格式說明在該檔開頭；Python 參考實作 `reference/proto/lm.py`），**另含少量用來辨識來源的指紋條目**。用 `tools/build_lm.py` 自己重建的模型不含這些條目，雜湊不同。規格見 `docs/contracts/model-v4.md`。
- 量測：dev302、打字測驗、錯字回報在聊天與書面、有無前文共 12 組，每一列的結果都和 model-v3 相同；即時預測的 golden（2,938 個查詢）逐位元相同；保留集每一列的對錯都相同。
- 要和 [classes-v2](https://github.com/Nanako0129/shanjie/releases/tag/classes-v2) 的 `classes.sjc` 一起用，放在同一個資料夾。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。模型由下列語料的詞頻計數而來：
  - 中文維基百科 2026-10-01 dump 的 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
- 不含任何私人對話資料。

下載：

```sh
gh release download model-v4 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v2 -R Nanako0129/shanjie -p classes.sjc -D data/lm
```
