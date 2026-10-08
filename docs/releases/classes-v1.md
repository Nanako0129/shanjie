善解的詞類表（`classes.sjc`），**要和 [model-v3](https://github.com/Nanako0129/shanjie/releases/tag/model-v3) 的 `bigram.sjlm` 一起用**，放在同一個資料夾。CI 與發布版從這裡下載。**這不是輸入法的發行版，也不含語言模型本身。**

- 檔案：`classes.sjc`，5,443,546 bytes
- SHA-256：`80dbaa0898fff16f90d290fbc6ebf29edb30d917c95dbef1b72649183dd49a8a`
- 綁定的模型：model-v3（`bigram.sjlm`，SHA-256 `5c7d5a94…a5a48`）。檔案裡記了模型的 SHA-256，配到別的模型會載入失敗。
- 內容：512 個詞類（Brown 分群），每個模型詞的類別與發射機率，以及「這類詞後面接那類詞」的機率表，權重 μ = 0.8。語言模型對沒在語料一起出現過的兩個詞，用它來補估計。
- 產生：`tools/build_classes.py`（格式說明在該檔開頭），分群與量測見 `experiments/s2-classes/README.md`，規格見 `docs/contracts/s2k-word-classes.md`（S2k）。
- 量測：守門集沒有否決；cvtune-native 聊天／書面 +21／+26 列（p ≈ 0.002）；保留集第一名聊天 180、書面 184（不用詞類表時 178、184）。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ），和 model-v3 相同：由同一批語料的詞頻計數而來（中文維基百科，CC BY-SA 4.0，署名：Wikipedia contributors；Mozilla Common Voice 繁體中文（台灣）句子，CC0；Tatoeba 中文句子，CC BY 2.0 FR，署名：Tatoeba contributors；gpt-oss-120b 產生的台灣口語合成句）。
- 不含任何私人對話資料。

下載：

```sh
gh release download model-v3 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v1 -R Nanako0129/shanjie -p classes.sjc -D data/lm
```
