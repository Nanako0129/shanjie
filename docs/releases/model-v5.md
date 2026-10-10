善解的 bigram 語言模型（SJLM0001 格式），CI 與發布版從這裡下載。**這不是輸入法的發行版。**

- 檔案：`bigram.sjlm`，82,535,147 bytes
- SHA-256：`f81a021e5dea08dc48dbca1db0d0f63517bb6f0f3e1b20fdac631542d13f45af`
- 產生：語料、權重與建置流程同 model-v4（`tools/build_lm.py`，格式說明在該檔開頭；Python 參考實作 `reference/proto/lm.py`）。不同的是計數時，動漫與遊戲詞包的詞也當成斷詞的詞，計數採「加法」：一般詞的計數照不含詞包的切法，詞包詞另外加上自己的計數。有同音衝突的詞包詞（以及讀音落在衝突讀音裡的片段）不進計數，同音名字的排序維持詞包原本的決定。**另含少量用來辨識來源的指紋條目**；用 `tools/build_lm.py` 自己重建的模型不含這些條目，雜湊不同。規格見 `docs/contracts/model-v5.md`。
- 量測（對 model-v4，開動漫與遊戲詞包，聊天／書面）：
  - dev302、打字測驗、錯字回報：每一列的第一名都和 model-v4 相同（開不開詞包都是）。
  - 動漫作品名 135→138／135→139、角色名 182→187／188→192、角色名片段 67→73／71→77（共 105 句）。
  - 調參集（cvtune、wikitune，約 6,600 句，聊天與書面各量一次）：少對 0 到 2 句，不顯著。
  - 詞包詞單獨打的時候不是第一名的比例 12.5% → 10.8%（兩字名字 59.2% → 46.7%）。
- 要和 [classes-v3](https://github.com/Nanako0129/shanjie/releases/tag/classes-v3) 的 `classes.sjc` 一起用，放在同一個資料夾。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。模型由下列語料的詞頻計數而來：
  - 中文維基百科 2026-10-01 dump 的 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
  - 斷詞用的詞包詞來自中文維基百科（CC BY-SA 4.0，署名：Wikipedia contributors）與 Wikidata（CC0）
- 不含任何私人對話資料。

下載：

```sh
gh release download model-v5 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v3 -R Nanako0129/shanjie -p classes.sjc -D data/lm
```
