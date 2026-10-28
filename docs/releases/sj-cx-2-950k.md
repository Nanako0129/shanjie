善解的連文模型 **SJ-CX-2-950K**（聊天設定用的 trigram 表，檔案 `trigram-chat.sjt3`，SJT30001 格式；模型命名見 `docs/releases/README.md`）。這是新的一張表：之前聊天設定不用連文表，這一版起聊天也開 trigram（KN 開，β 1）。**要和 [sj-lm-7-9.7m](https://github.com/Nanako0129/shanjie/releases/tag/sj-lm-7-9.7m) 的 `bigram.sjlm` 一起用**，放在同一個資料夾，核心會自動載入；只有聊天設定用它，書面設定用 [sj-cx-2-7.7m](https://github.com/Nanako0129/shanjie/releases/tag/sj-cx-2-7.7m)。要讀它需要通語 SJ-LM-7 PR 2 之後的核心（舊核心只認 `trigram.sjt3`）。CI 與發布版從這裡下載。**這不是輸入法的發行版，也不含語言模型本身。**

- 檔案：`trigram-chat.sjt3`，15,114,784 bytes
- SHA-256：`24ee8645817060508f12ec2731995244df00b913b305161f46e46527ea122275`
- 綁定的模型：sj-lm-7-9.7m（`bigram.sjlm`，SHA-256 `e3fb6056…3d18db5b`）。檔頭記了模型的 SHA-256 與詞彙數，配到別的模型會載入失敗；App 裡任何一張連文表壞了，整個載入都失敗。
- 內容（讀檔頭）：375,736 個兩詞前文、950,000 條「前兩個詞之後接哪個詞」，詞彙 396,927；每個前文剪枝前的總次數照記；插值絕對折扣 D 0.75。
- 產生（探索 `experiments/sj-lm-7/trigram-explore.md`，契約 `docs/contracts/sj-lm-7.md` §2.8）：用 SJ-LM-7 全部語料與同樣的權重重數三元組（`experiments/derisk-1015/e1/count.py`，`exp/sj7-trigram` 的 `b6a3c8c8`，`--no-trad-tw-phrases`、`--tri-min 0.01`），建不剪的表（`build_models.py tri --corpora`），再用相對熵剪枝（`entropy_prune.py --keep 950000`，承接用 [sj-kn-2-397k](https://github.com/Nanako0129/shanjie/releases/tag/sj-kn-2-397k)）剪成 950,000 條；指令在 `experiments/sj-lm-7/README.md`。剪成 7.7M、3.85M、1.9M、0.95M 四個大小比過，聊天選 0.95M：守得住記憶體預算（現行 +40 MiB）的只有 1.9M 與 0.95M，0.95M 的 RSS 多 25 MiB。
- 量測（階段 1 追加，聊天開 trigram 且 KN 開，對不開；會意與常談照當時出貨；改對的句數）：discordtune 4,534 → 4,554、cvtune-clean 3,011 → 3,021、錯字回報聊天 95 → 99、動漫角色片段 79 → 82、dev302 聊天 268 → 269；網路用語第二輪 837 → 833、店家 197 → 192（兩個小退步）。CLI 最高 RSS（Linux，只拿來比大小）聊天 282 → 296 MiB。整組的結果見 [sj-sr-7-15m](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-7-15m) 的說明。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ），和 sj-lm-7-9.7m 相同：由同一批語料的詞頻計數而來，來源有維基百科 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）、Mozilla Common Voice 繁體中文（CC0）、Tatoeba 中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）、台灣口語合成句（gpt-oss-120b 的模型輸出；權重本身為 Apache-2.0）、g0v Slack `#rand0m` 頻道的公開訊息（CC0）、PTT 批踢踢實業坊公開看板的文章與推文（只用於詞頻計數，不含原文）。
- 不含任何私人對話資料。
- 三元組計數的口語池是重產的 `colloquial-train.txt`（`eb905936…`）＋`synth.txt`（`bec7a6a2…`）＋`topup.txt`（`315939d7…`），計數照 B6 的設定不套 OpenCC `TWPhrases`（`--no-trad-tw-phrases`）。

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
