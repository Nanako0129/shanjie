善解的 bigram 語言模型（SJLM0001 格式），CI 與發布版從這裡下載。**這不是輸入法的發行版。**

- 檔案：`bigram.sjlm`，83,445,569 bytes
- SHA-256：`fc2bddd1f0c2029deb7e7568f64868c11f535b3d5c5c7c97c3d0599c8b2cde42`
- 產生：建置流程同 model-v5（`tools/build_lm.py`，格式說明在該檔開頭；Python 參考實作 `reference/proto/lm.py`）。和 model-v5 不同的地方：計數時常見專有名詞（名單，`data/lexicon/names-add.tsv`）也當成斷詞的詞；動漫與遊戲詞包換成 A3 版；拿掉粵語用字的疊加層詞；口語合成句多補 50 萬字（讓量詞、口語詞有足夠的次數）；建模時名單當成疊加層（`--extra-overlay data/lexicon/names-add.tsv`）。**另含少量用來辨識來源的指紋條目**；用 `tools/build_lm.py` 自己重建的模型不含這些條目，雜湊不同。指令在 `experiments/model-v6/README.md`，規格見 `docs/contracts/model-v6.md`。
- 要和 [classes-v4](https://github.com/Nanako0129/shanjie/releases/tag/classes-v4) 的 `classes.sjc`、[kn-v1](https://github.com/Nanako0129/shanjie/releases/tag/kn-v1) 的 `kn.sjkn` 一起用，放在同一個資料夾；名單 `data/lexicon/names-add.tsv` 要一起載入。
- 量測（對 model-v5＋classes-v3，兩邊都開動漫與遊戲詞包、看前文，只有 n-gram、不開語意校正；新版另開 kn-v1 與名單；聊天／書面的第一名對的句數）：
  - dev302：241→246／250→250；打字測驗（76 句）：66→69／69→69；錯字回報（114 句）：25→29／32→40。
  - 調參集 cvtune（3,791 句）：3,366→3,366／3,370→3,375；wikitune（2,827 句）：2,084→2,093／2,145→2,165。
  - 專有名詞檢查集（61 句）：44→58（兩種設定相同）；動漫角色名 191→193／193→196，角色名片段 78→79／80→81，含間隔號的名字 39→41／36→36，作品名與近期作品不變。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。模型由下列語料的詞頻計數而來：
  - 中文維基百科 2026-10-01 dump 的 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0），含這一版補的 50 萬字
  - 斷詞用的詞包詞來自中文維基百科（CC BY-SA 4.0，署名：Wikipedia contributors）與 Wikidata（CC0）
  - 斷詞用的常見專有名詞（名單）：name-suggestion-index（https://github.com/osmlab/name-suggestion-index ，BSD-3-Clause，不以作者名義背書）、中文維基百科（CC BY-SA 4.0）與 Wikidata（CC0）、教育部統計處〈大專校院名錄〉與〈一般高級中等學校名錄〉（政府資料開放授權條款第 1 版，https://data.gov.tw/license ）
- 不含任何私人對話資料。

下載：

```sh
gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v4 -R Nanako0129/shanjie -p classes.sjc -D data/lm
gh release download kn-v1 -R Nanako0129/shanjie -p kn.sjkn -D data/lm
```
