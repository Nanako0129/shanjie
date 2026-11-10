善解「語意校正」用的小型字元語言模型，會意（SJ-SR）第 8 版 **SJ-SR-8-15M**，接替 [sj-sr-7-15m](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-7-15m)。和上一版不同的有兩件：**權重從上一版接續訓練過**（訓練文字的繁體句不再套 OpenCC `TWPhrases`，所以「權限」不會被抹成「許可權」），以及**書面三檔的融合參數換了**。**這不是輸入法的發行版。**

- 檔案：`tinylm.sjtl`，14,886,880 bytes
- SHA-256：`356a37beede1a754cb6e963a52d0f20ae6574cbcd332d0b1bfc9686c0a97c5de`
- 格式：SJTL0004（和上一版相同）。內容（讀檔頭）：6 層、寬 384、6 頭、前文 64、字表 10,351 個 id（和上一版相同）；二維權重每列對稱 int8 加 float32 縮放。參數數 14,646,912，由檔頭與格式說明的張量清單算出（同一式子算出的檔案大小等於實際位元組數，和上一版一樣）。只在 n-gram 解碼出的前 8 名候選之間重排，不會產生新的文字。
- **綁定**：檔頭記了 [sj-lm-7-9.7m](https://github.com/Nanako0129/shanjie/releases/tag/sj-lm-7-9.7m) `bigram.sjlm`（`e3fb6056…6148eb5a`）與 [sj-kn-2-397k](https://github.com/Nanako0129/shanjie/releases/tag/sj-kn-2-397k) `kn.sjkn`（`880ee87c…254e985a`）的 SHA-256，不符就拒絕載入、只用 n-gram（和上一版相同）。常談 [sj-cl-2-69k](https://github.com/Nanako0129/shanjie/releases/tag/sj-cl-2-69k) 由核心的 `PAIRED_TINYLM_SHA256` 配對到這個檔，換了會意檔常談就不生效。
- 融合參數（α、τ、δ；δ 是融合分數要比 n-gram 第一名高出多少才換）：書面三組在檔頭；聊天三組在核心 `core/src/colloquial.rs`（常談生效時用它；檔頭的聊天值只在口語項不是有限值時用，這一版沒有改）。

| 設定 | 保守 | 標準 | 積極 |
|---|---|---|---|
| 聊天 | 1.3、2、1 | 1.1、2、0 | 1.1、2、0（同標準） |
| 書面 | 0.8、2、1.5 | 0.8、2、0 | 0.8、2、0（同標準） |

  上一版是聊天 0.7、2、1／0.7、2、0／1.0、2、0.1，書面 0.5、2、1／0.5、2、0.5／0.5、2、0.5。
- **常談句首溫和版**（同一個產品片，不在這個檔裡）：聊天設定的常談項，句首詞在常談表的句首上下文裡沒出現過時，第一個詞用單字估計，不用句首退回值；核心的 `Engine::load_colloquial` 載入後就開著。
- 訓練：Colab G4，從上一版（SJ-SR-7-15M，`tinylm-M.npz` SHA-256 `cb351ed7…2a151c5c`）的權重接續訓練，3,000 步、峰值學習率 1e-4、暖身 100 步、weight decay 0、種子 20261019，用上一版的字表；每批最多 32,768 個字元，訓練共見 88,360,886 個。訓練文字和上一版同源（見下），差別在轉換：繁體句不套 `TWPhrases`，簡體來源的句子只換 331 條大陸用詞（`TWPhrases` 的子集，`SJ-LM-7` §2.7 量過的）。匯出用 `export_sjtl.py --type int8 --ngram-model <sj-lm-7-9.7m> --kn-side <sj-kn-2-397k>`；匯出方法核對：用上一版的 npz、現行檔頭的全部值重匯出，要和上一版逐位元組相同（成立）；這一版的 npz 用同一套方法、現行檔頭的值匯出，得到量測用的檔（`2c69a761…`），再只改檔頭 52..211 的書面三檔（共 26 個位元組不同），得到這個檔。
- 量測（SR-TW-R、FUSE-1、FUSE-2，會意 SJ-SR-8-15M 的權重與這些格；集合是沒看過的 wikijudge7／8／9，正解不套 `TWPhrases`）：
  - SR-TW-R（書面標準對上一版，格是舊的 0.5、2、0.5）：top1 2,255 → 2,254，改對 5、改壞 6、淨值 −1，Δtop1 的 90% 區間下界 −0.25 個百分點（門檻 ≥ −1.0，通過）。
  - FUSE-1（新格對舊格）：書面標準 0.8、2、0 對 0.5、2、0.5：改對 90、改壞 45、淨值 +45、改壞÷改對 0.50；聊天標準 1.1、2、0 對 0.7、2、0：改對 43、改壞 17、淨值 +26、比例 0.40。
  - FUSE-2：聊天保守 1.3、2、1 對 0.7、2、1：改對 68、改壞 19、淨值 +49，Δtop1 的 90% 區間下界 +1.20 個百分點；書面保守 0.8、2、1.5 對 0.5、2、1：改對 14、改壞 0、淨值 +14，下界 +0.28 個百分點。
  - 積極兩檔等於標準（不另外量）。
  - 產品片（本檔配上核心的新格與溫和版，對上一版的出貨設定）：出貨標準四條三檔都過。標準檔：discordtune（聊天）改對 29、改壞 11、淨值 +18；cvtune-clean-tw0（聊天）Δtop1 的 90% 區間下界 +0.10 個百分點；錯字回報兩種設定合計改對 24、改壞 6；dev302 書面改對 9、改壞 2，打字測驗兩種設定都不變。第 1、3 條的 12 個執行在 GCP 與 188 兩台機器上逐位元組相同。保守檔 discordtune 淨值 +35、錯字回報 +19／−0；積極檔 discordtune 淨值 +9、錯字回報 +23／−6。
  - 已知限制：錯字回報第 179 列（「權限加好了」）書面標準與積極仍選錯（聊天對）；句首學習集書面教一次之後，上一版 145／164 列對、這一版 140。這一版量到：學習後仍錯的列裡有 8 列，同組的教學句第一次就打對、沒有改選，所以打這一列之前那組還沒有學習紀錄（例如「再賣一次」第一次就對、同組的「再賣三個」錯）；學習項放大到 2.5 倍，書面也只多對 1 列。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。訓練文字（和 [sj-sr-7-15m](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-7-15m) 相同）：
  - 中文維基百科 2026-10-01 dump 的前 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
  - g0v Slack `#rand0m` 頻道的公開訊息（CC0）
  - 台灣公開論壇（論壇 A）公開看板的文章與推文（只用於訓練，不含原文）
  - OPUS OpenSubtitles v2018 繁體字幕（只用於訓練，不含原文）。照 OPUS 的要求引用：P. Lison and J. Tiedemann, 2016, OpenSubtitles2016: Extracting Large Parallel Corpora from Movie and TV Subtitles. In Proceedings of the 10th International Conference on Language Resources and Evaluation (LREC 2016)；字幕來自 http://www.opensubtitles.org/ 。
- 不含任何私人對話資料；檔案裡沒有任何訊息的文字。
- 權重來源：接續訓練的 `tinylm-M.npz`（SHA-256 `99c7f44c989a98e60ec6618479aab6c4a4aefd8af08c0a18e95fab82699bf801`，沒有另外發布）。

下載（一整組）：

```sh
gh release download sj-lm-7-9.7m -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download sj-wc-6-512c -R Nanako0129/shanjie -p classes.sjc -D data/lm
gh release download sj-kn-2-397k -R Nanako0129/shanjie -p kn.sjkn -D data/lm
gh release download sj-cx-2-7.7m -R Nanako0129/shanjie -p trigram.sjt3 -D data/lm
gh release download sj-cx-2-950k -R Nanako0129/shanjie -p trigram-chat.sjt3 -D data/lm
gh release download sj-sr-8-15m -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm
gh release download sj-cl-2-69k -R Nanako0129/shanjie -p colloquial.sjlm -p colloquial-attribution.tsv -D data/lm
```
