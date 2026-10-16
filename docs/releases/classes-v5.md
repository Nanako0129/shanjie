善解的詞類表（`classes.sjc`），**要和 [model-v6](https://github.com/Nanako0129/shanjie/releases/tag/model-v6) 的 `bigram.sjlm` 一起用**，放在同一個資料夾。CI 與發布版從這裡下載。**這不是輸入法的發行版，也不含語言模型本身。**

- 檔案：`classes.sjc`，5,656,826 bytes
- SHA-256：`9e36fa1be4518adb34bcddc2c79761006b0b06c539aa64a4564ba00397cf0262`
- 綁定的模型：model-v6（`bigram.sjlm`，SHA-256 `fc2bddd1…8b2cde42`）。檔案裡記了模型的 SHA-256，配到別的模型會載入失敗。
- 和 [classes-v4](https://github.com/Nanako0129/shanjie/releases/tag/classes-v4) 的差別：**只改了詞類項的權重 μ，0.8 → 0.2**（檔頭位移 44 的 8 個位元組），詞類、發射機率與類別接續機率都相同。μ 是在出貨設定（model-v6＋kn-v1＋tinylm-v5）上掃描 {0, 0.2, 0.4, 0.6, 0.8} 選出的：v0.4.4 起 lytune 顯著變差，歸因到詞類項和 KN 同時開的交互作用，降低 μ 把它拿回來，其他評測集合沒有顯著變差（`docs/contracts/lytune-attrib.md`、`docs/research-log.md` 2026-10-16）。
- 產生：`python3 experiments/lytune-attrib/set_mu.py classes-v4/classes.sjc classes.sjc 0.2`（印出 SHA-256 與讀回的 μ），規格見 `docs/contracts/classes-v5.md`。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ），和 model-v6 相同：由同一批語料的詞頻計數而來（見 model-v6 的說明）。
- 不含任何私人對話資料。

下載：

```sh
gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v5 -R Nanako0129/shanjie -p classes.sjc -D data/lm
gh release download kn-v1 -R Nanako0129/shanjie -p kn.sjkn -D data/lm
```
