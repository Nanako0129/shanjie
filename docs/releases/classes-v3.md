善解的詞類表（`classes.sjc`），**要和 [model-v5](https://github.com/Nanako0129/shanjie/releases/tag/model-v5) 的 `bigram.sjlm` 一起用**，放在同一個資料夾。CI 與發布版從這裡下載。**這不是輸入法的發行版，也不含語言模型本身。**

- 檔案：`classes.sjc`，5,575,836 bytes
- SHA-256：`75a5efb3d050a9b0d21bca707ff6b1fd71945a8e059b869aa856618290e55827`
- 綁定的模型：model-v5（`bigram.sjlm`，SHA-256 `f81a021e…13f45af`）。檔案裡記了模型的 SHA-256，配到別的模型會載入失敗。
- 和 classes-v2 的差別：model-v4 已有的詞沿用 classes-v2 的類別（重新分群會讓一般詞的詞類整體漂移），只替 model-v5 新進詞彙的詞包詞找類別（`experiments/s2-classes/cluster.py --keep-from`）；發射機率與類別接續機率照 model-v5 的計數重算。
- 內容：512 個詞類（Brown 分群），每個模型詞的類別與發射機率，以及「這類詞後面接那類詞」的機率表，權重 μ = 0.8。語言模型對沒在語料一起出現過的兩個詞，用它來補估計。
- 產生：`tools/build_classes.py`（格式說明在該檔開頭），分群見 `experiments/s2-classes/README.md`，規格見 `docs/contracts/s2k-word-classes.md`（S2k）與 `docs/contracts/model-v5.md`。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ），和 model-v5 相同：由同一批語料的詞頻計數而來（中文維基百科，CC BY-SA 4.0，署名：Wikipedia contributors；Mozilla Common Voice 繁體中文（台灣）句子，CC0；Tatoeba 中文句子，CC BY 2.0 FR，署名：Tatoeba contributors；gpt-oss-120b 產生的台灣口語合成句；斷詞用的詞包詞來自中文維基百科與 Wikidata）。
- 不含任何私人對話資料。

下載：

```sh
gh release download model-v5 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v3 -R Nanako0129/shanjie -p classes.sjc -D data/lm
```
