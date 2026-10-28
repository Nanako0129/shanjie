〔草稿：待維護者確認後才建 Release〕
善解的承接模型 **SJ-KN-2-397K**（Kneser–Ney 側檔，檔案 `kn.sjkn`，SJKN0002 格式；模型命名見 `docs/releases/README.md`），**要和 [sj-lm-7-9.7m](https://github.com/Nanako0129/shanjie/releases/tag/sj-lm-7-9.7m) 的 `bigram.sjlm` 一起用**，放在同一個資料夾，核心會自動載入。CI 與發布版從這裡下載。**這不是輸入法的發行版，也不含語言模型本身。**

- 檔案：`kn.sjkn`，1,587,772 bytes
- SHA-256：`880ee87c6cbedf4097a8f73540de335a5bfe6abb48413513a83e7f75254e985a`
- 綁定的模型：sj-lm-7-9.7m（`bigram.sjlm`，SHA-256 `e3fb6056…3d18db5b`）。檔頭記了模型的 SHA-256，配到別的模型會載入失敗。
- 內容（讀檔頭）：詞彙 396,927 個；每個詞「前面接過幾種不同的詞」（接續次數，θ = 1）；β 1；接續次數的總和 ΣN′ = 46,087,912（model-v6 的 [kn-v1](https://github.com/Nanako0129/shanjie/releases/tag/kn-v1) 是 23,905,107）。
- **兩種設定用的 β 不同**：聊天照檔頭的 β 1；書面用核心的常數 `FORMAL_KN_BETA` 0.75（`core/src/lm.rs`，研究紀錄 2026-10-26 階段 3），檔案本身不變。書面也開 KN 是這一版新增的（model-v6 的書面有連文表時不用 KN）。
- 產生：`tools/kn_cont.py --theta 1 --beta 1`，用和通語同一批計數與最終模型（契約 `docs/contracts/sj-lm-7.md` §2.6），指令的骨架在 `experiments/model-v6/README.md`；格式與規格見 `docs/contracts/kn-core.md`。
- 量測（階段 3，同一組模型，書面開 KN β 0.75 對照不開；改對／改壞）：標準檔 wikitune-clean 37／17（對 main：現行 −27 → −7）、cvtune-clean 書面 25／10、dev302 書面 2／3、錯字回報書面 2／1；維護者選書面開 KN β 0.75（研究紀錄 2026-10-26）。聊天側：聊天連文表開著時，KN 關比 KN 開少約 10 列（discordtune）、網路用語第二輪少約 20 列。整組的結果見 [sj-sr-7-15m](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-7-15m) 的說明。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ），和 sj-lm-7-9.7m 相同：由同一批語料的詞頻計數而來（來源見 sj-lm-7-9.7m 的說明，含 g0v `#rand0m`〔CC0〕與 PTT 公開看板的文章與推文〔只用於詞頻計數，不含原文〕）。
- 不含任何私人對話資料。

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
