善解的詞類表（`classes.sjc`），**要和 [model-v4](https://github.com/Nanako0129/shanjie/releases/tag/model-v4) 的 `bigram.sjlm` 一起用**，放在同一個資料夾。CI 與發布版從這裡下載。**這不是輸入法的發行版，也不含語言模型本身。**

- 檔案：`classes.sjc`，5,443,546 bytes
- SHA-256：`9e343d3e3ce83f1008e371f62de5d8e62721df97e7b02562503ef2cc57226f3f`
- 綁定的模型：model-v4（`bigram.sjlm`，SHA-256 `06768f29…250322`）。檔案裡記了模型的 SHA-256，配到別的模型會載入失敗。
- 和 classes-v1 的差別：只有檔頭記的模型 SHA-256（改綁 model-v4）；分群、發射機率與類別接續機率逐位元組相同。
- 內容：512 個詞類（Brown 分群），每個模型詞的類別與發射機率，以及「這類詞後面接那類詞」的機率表，權重 μ = 0.8。語言模型對沒在語料一起出現過的兩個詞，用它來補估計。
- 產生：`tools/build_classes.py`（格式說明在該檔開頭），分群與量測見 `experiments/s2-classes/README.md`，規格見 `docs/contracts/s2k-word-classes.md`（S2k）與 `docs/contracts/model-v4.md`。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ），和 model-v4 相同：由同一批語料的詞頻計數而來（中文維基百科，CC BY-SA 4.0，署名：Wikipedia contributors；Mozilla Common Voice 繁體中文（台灣）句子，CC0；Tatoeba 中文句子，CC BY 2.0 FR，署名：Tatoeba contributors；gpt-oss-120b 產生的台灣口語合成句）。
- 不含任何私人對話資料。

下載：

```sh
gh release download model-v4 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v2 -R Nanako0129/shanjie -p classes.sjc -D data/lm
```
