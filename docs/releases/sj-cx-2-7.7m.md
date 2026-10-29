善解的連文模型 **SJ-CX-2-7.7M**（書面設定用的 trigram 表，檔案 `trigram.sjt3`，SJT30001 格式；模型命名見 `docs/releases/README.md`），接替 [sj-cx-1-7.7m](https://github.com/Nanako0129/shanjie/releases/tag/sj-cx-1-7.7m)。**要和 [sj-lm-7-9.7m](https://github.com/Nanako0129/shanjie/releases/tag/sj-lm-7-9.7m) 的 `bigram.sjlm` 一起用**，放在同一個資料夾，核心會自動載入；只有書面設定用它（同時開 KN，β 0.75），聊天設定用另一張表 [sj-cx-2-950k](https://github.com/Nanako0129/shanjie/releases/tag/sj-cx-2-950k)。CI 與發布版從這裡下載。**這不是輸入法的發行版，也不含語言模型本身。**

- 檔案：`trigram.sjt3`，133,297,536 bytes（sj-cx-1-7.7m 是 143,643,936）
- SHA-256：`34305a2d749dcd7f8528250e03f24a8cb7f1042a940669a415374cdfbbc8d8eb`
- 綁定的模型：sj-lm-7-9.7m（`bigram.sjlm`，SHA-256 `e3fb6056…6148eb5a`）。檔頭記了模型的 SHA-256 與詞彙數，配到別的模型會載入失敗。
- 內容（讀檔頭）：3,584,488 個兩詞前文、7,700,964 條「前兩個詞之後接哪個詞」（和 sj-cx-1-7.7m 同條目數），詞彙 396,927；每個前文剪枝前的總次數照記；插值絕對折扣 D 0.75。
- 產生：維基部分用 通語 SJ-LM-7-9.7M 的詞庫與計數詞表重數三元組（`experiments/derisk-1015/e1/count.py`，`--no-trad-tw-phrases`、`--tri-min 0.01`），建不剪的表（`build_models.py tri --corpora`），再用相對熵剪枝（`entropy_prune.py`，和 sj-cx-1-7.7m 同一種剪法）剪成 7,700,964 條；指令在 `experiments/sj-lm-7/README.md`，探索與選擇見 `experiments/sj-lm-7/trigram-explore.md`（`exp/sj7-trigram`）。
- 資料：維基 20 萬篇 ×1 加舊口語池 ×5（研究紀錄的名稱「WC」；舊表的配方、新詞彙），**不含 g0v 與 PTT**。書面只用維基與舊口語池，因為含 PTT 的三元組讓書面更像口語，wikitune 與 dev302 變差（研究紀錄 2026-10-26，推論）。
- 量測（階段 1 之後，書面標準檔，會意 SJ-SR-6-14M；wikitune、錯字回報是第一名對的句數）：wikitune-clean 現行 1,641 → 這張表 1,614（含 PTT 的新表 1,588，只數維基的 1,615）；dev302 書面改壞 6 列（只數維基 7 列；出貨標準第 2 條的例外，維護者 2026-10-26 同意）；錯字回報書面 97–99；書面 RSS 371 MiB（現行 376）。整組的結果見 [sj-sr-7-15m](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-7-15m) 的說明。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ），和 sj-cx-1-7.7m 相同：由維基百科（CC BY-SA 4.0，署名：Wikipedia contributors）與口語語料（Mozilla Common Voice 繁體中文〔CC0〕、Tatoeba 中文句子〔CC BY 2.0 FR，署名：Tatoeba contributors〕、gpt-oss-120b 產生的台灣口語合成句〔模型權重本身為 Apache-2.0〕）的詞頻計數而來。
- 不含任何私人對話資料。
- 三元組計數的口語池是重產的 `colloquial-train.txt`（`eb905936…`）＋`synth.txt`（`bec7a6a2…`）＋`topup.txt`（`315939d7…`），計數照 B6 的設定不套 OpenCC `TWPhrases`（`--no-trad-tw-phrases`）。
- 和只數維基的同規模表相比，研究紀錄記到的差別是 wikitune 1,614 對 1,615、dev302 書面改壞 6 對 7（研究紀錄 2026-10-26）。

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
