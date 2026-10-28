〔草稿：待維護者確認後才建 Release〕
善解的辨類模型 **SJ-WC-6-512C**（詞類表，檔案 `classes.sjc`，SJCL0001 格式；模型命名見 `docs/releases/README.md`），**要和 [sj-lm-7-9.7m](https://github.com/Nanako0129/shanjie/releases/tag/sj-lm-7-9.7m) 的 `bigram.sjlm` 一起用**，放在同一個資料夾。CI 與發布版從這裡下載。**這不是輸入法的發行版，也不含語言模型本身。**

- 檔案：`classes.sjc`，6,091,126 bytes
- SHA-256：`4315f0ed90ab5512a50e2a529693d0f551f842484a97c8f82ddccfef302446ae`
- 綁定的模型：sj-lm-7-9.7m（`bigram.sjlm`，SHA-256 `69826c2f…3d18db5b`）。檔案裡記了模型的 SHA-256，配到別的模型會載入失敗。
- 內容（讀檔頭）：512 個詞類，詞類項權重 μ 0.2（和 [classes-v5](https://github.com/Nanako0129/shanjie/releases/tag/classes-v5) 相同），詞彙 396,927；每個詞的類別、發射機率和類別接續機率都依這一版通語重算。
- 產生：用和通語同一批計數（`docs/contracts/sj-lm-7.md` §2.6：辨類、承接、通語同一批計數）重做分群，再由 `tools/build_classes.py` 寫出；指令的骨架在 `experiments/model-v6/README.md`（分群 `experiments/s2-classes/cluster.py`，加了 `--corpora`），這一版實際跑的完整指令列，研究紀錄只寫了「分群、詞類表、KN 6 分鐘」。
- 量測：研究紀錄沒有把詞類表單獨拆出來量；階段 1 到定案的所有量測都載入這一份（整組的結果見 [sj-sr-7-15m](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-7-15m) 的說明）。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ），和 sj-lm-7-9.7m 相同：由同一批語料的詞頻計數而來（來源見 sj-lm-7-9.7m 的說明，含 g0v `#rand0m`〔CC0〕與 PTT 公開看板的文章與推文〔只用於詞頻計數，不含原文〕）。
- 不含任何私人對話資料。
- 〔TODO：`cluster.py` 的 `--N`、`--keep-from` 與是否帶 `--extra-overlay`，這一版的指令列沒有寫進研究紀錄；要寫進說明的話由跑它的人補。〕

下載（一整組）：

```sh
gh release download sj-lm-7-9.7m -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download sj-wc-6-512c -R Nanako0129/shanjie -p classes.sjc -D data/lm
gh release download sj-kn-2-397k -R Nanako0129/shanjie -p kn.sjkn -D data/lm
gh release download sj-cx-2-7.7m -R Nanako0129/shanjie -p trigram.sjt3 -D data/lm
gh release download sj-cx-2-950k -R Nanako0129/shanjie -p trigram-chat.sjt3 -D data/lm
gh release download sj-sr-7-15m -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm
gh release download sj-cl-2-69k -R Nanako0129/shanjie -p colloquial.sjlm -p colloquial-attribution.tsv -D data/lm
```
