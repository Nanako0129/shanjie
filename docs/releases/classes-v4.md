善解的詞類表（`classes.sjc`），**要和 [model-v6](https://github.com/Nanako0129/shanjie/releases/tag/model-v6) 的 `bigram.sjlm` 一起用**，放在同一個資料夾。CI 與發布版從這裡下載。**這不是輸入法的發行版，也不含語言模型本身。**

- 檔案：`classes.sjc`，5,656,826 bytes
- SHA-256：`efac3184170e9795cef35855fc6615cf5672882bc0e20c624b620597aeab7a56`
- 綁定的模型：model-v6（`bigram.sjlm`，SHA-256 `fc2bddd1…8b2cde42`）。檔案裡記了模型的 SHA-256，配到別的模型會載入失敗。
- 和 classes-v3 的差別：classes-v3 已有的詞沿用原本的類別（39,679 個），只替 model-v6 新進詞彙的詞找類別（321 個，`experiments/s2-classes/cluster.py --keep-from`）；常用量詞表（`experiments/model-v6/classifiers.txt`）裡加權次數少於 1,000 的量詞（縷、滴、撮、趟、盞、棵、綑、匙、簍、籃）併進「個」的類別（`experiments/model-v6/force_classes.py`），讓少見的量詞借用常見量詞的接續機率；發射機率與類別接續機率照 model-v6 的計數重算；名單詞算進產得出的詞（`--extra-overlay data/lexicon/names-add.tsv`）。
- 內容：512 個詞類（Brown 分群），每個模型詞的類別與發射機率，以及「這類詞後面接那類詞」的機率表，權重 μ = 0.8。
- 產生：`tools/build_classes.py`（格式說明在該檔開頭），指令在 `experiments/model-v6/README.md`，規格見 `docs/contracts/model-v6.md` §2.2、§2.2b。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ），和 model-v6 相同：由同一批語料的詞頻計數而來（見 model-v6 的說明）。
- 不含任何私人對話資料。

下載：

```sh
gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v4 -R Nanako0129/shanjie -p classes.sjc -D data/lm
gh release download kn-v1 -R Nanako0129/shanjie -p kn.sjkn -D data/lm
```
