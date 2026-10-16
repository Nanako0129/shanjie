善解的 Kneser–Ney 側檔（`kn.sjkn`，SJKN0002 格式），**要和 [model-v6](https://github.com/Nanako0129/shanjie/releases/tag/model-v6) 的 `bigram.sjlm` 一起用**，放在同一個資料夾，核心會自動載入。CI 與發布版從這裡下載。**這不是輸入法的發行版，也不含語言模型本身。**

- 檔案：`kn.sjkn`，1,414,052 bytes
- SHA-256：`cbefbced072f7e9d40dbfc3d2fad27c29f82e44bf801800dfb17b4eec962597e`
- 綁定的模型：model-v6（`bigram.sjlm`，SHA-256 `fc2bddd1…8b2cde42`）。檔頭記了模型的 SHA-256，配到別的模型會載入失敗。
- 內容：每個詞「前面接過幾種不同的詞」（接續次數，θ = 1），以及接續次數的總和 ΣN′ = 23,905,107、β = 1。沒在語料一起出現過的兩個詞，後一個詞的機率改用接續次數估計（常出現在各種詞後面的詞，估得比只在固定搭配裡出現的詞高）。詞彙 353,497 個。
- 產生：`tools/kn_cont.py --theta 1 --beta 1`，用 model-v6 的計數與最終模型，名單算進異體類（`--extra-overlay data/lexicon/names-add.tsv`）；指令在 `experiments/model-v6/README.md`，格式與規格見 `docs/contracts/kn-core.md`、`docs/contracts/model-v6.md` §2.3。Python 參考實作讀它時會用自己的詞庫重算 ΣN′ 並和檔頭比對。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ），和 model-v6 相同：由同一批語料的詞頻計數而來（見 model-v6 的說明）。
- 不含任何私人對話資料。

下載：

```sh
gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v4 -R Nanako0129/shanjie -p classes.sjc -D data/lm
gh release download kn-v1 -R Nanako0129/shanjie -p kn.sjkn -D data/lm
```
