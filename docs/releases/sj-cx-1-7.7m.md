善解的連文模型 **SJ-CX-1-7.7M**（trigram 表，檔案 `trigram.sjt3`，SJT30001 格式；模型命名見 `docs/releases/README.md`），**要和 [model-v6](https://github.com/Nanako0129/shanjie/releases/tag/model-v6) 的 `bigram.sjlm` 一起用**，放在同一個資料夾，核心會自動載入；只有書面設定用它（同時關掉 KN），聊天設定與預測列不用。CI 與發布版從這裡下載。**這不是輸入法的發行版，也不含語言模型本身。**

- 檔案：`trigram.sjt3`，143,643,936 bytes
- SHA-256：`fdd3b05d8c18cf85da434443d4fabbbfebf31e8c97a04e00e4a339bbe19eec84`
- 綁定的模型：model-v6（`bigram.sjlm`，SHA-256 `fc2bddd1…8b2cde42`）。檔頭記了模型的 SHA-256 與詞彙數，配到別的模型會載入失敗。
- 內容：4,101,808 個兩詞前文、7,700,964 個「前兩個詞之後接哪個詞」的次數，以及每個前文剪枝前的總次數（插值絕對折扣，D = 0.75）。
- 產生：derisk-1015 的 E1a 用 model-v6 的配方重算計數（維基 20 萬篇與口語語料，多算一張 trigram 表），E1e 用相對熵剪枝留下分數最高的 7,700,964 條（H3），再用 `experiments/derisk-1015/e1/remap_tri.py`（commit `6c52e7f`）把詞 id 換成 model-v6 的並綁定雜湊，輸出就是這個檔。`tools/build_trigram.py` 是之後移進 `tools/` 的同一個演算法（只換了讀寫檔的函式），沒有拿它重跑這個檔。量測與判讀見 `docs/contracts/derisk-1015.md`（E1e、E1g、E1h）、`docs/contracts/trigram-core.md` 與研究紀錄 2026-10-16。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ），和 model-v6 相同：由同一批語料的詞頻計數而來（見 model-v6 的說明）。
- 不含任何私人對話資料。

下載：

```sh
gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download classes-v5 -R Nanako0129/shanjie -p classes.sjc -D data/lm
gh release download kn-v1 -R Nanako0129/shanjie -p kn.sjkn -D data/lm
gh release download sj-cx-1-7.7m -R Nanako0129/shanjie -p trigram.sjt3 -D data/lm
```
