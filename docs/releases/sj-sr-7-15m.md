善解「語意校正」用的小型字元語言模型，會意（SJ-SR）第 7 版 **SJ-SR-7-15M**，接替 [sj-sr-6-14m](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-6-14m)。和上一版不同的是**權重重新訓練過**（加了新的口語語料與字幕），不只是換融合參數。**這不是輸入法的發行版。**

- 檔案：`tinylm.sjtl`，14,886,880 bytes
- SHA-256：`f7d0ac6377bfe973cf4239a949d7a763f1acb0095e50bc6c1323bd3ed09d3478`
- 格式：SJTL0004（和上一版相同）。內容（讀檔頭）：6 層、寬 384、6 頭、前文 64、字表 10,351 個 id（上一版 9,717）；二維權重每列對稱 int8 加 float32 縮放。參數數 14,646,912，由檔頭與格式說明的張量清單算出（同一式子算出的檔案大小等於實際位元組數，上一版也是）。只在 n-gram 解碼出的前 8 名候選之間重排，不會產生新的文字。
- **綁定**：檔頭記了 [sj-lm-7-9.7m](https://github.com/Nanako0129/shanjie/releases/tag/sj-lm-7-9.7m) `bigram.sjlm`（`e3fb6056…6148eb5a`）與 [sj-kn-2-397k](https://github.com/Nanako0129/shanjie/releases/tag/sj-kn-2-397k) `kn.sjkn`（`880ee87c…254e985a`）的 SHA-256，不符就拒絕載入、只用 n-gram。常談 [sj-cl-2-69k](https://github.com/Nanako0129/shanjie/releases/tag/sj-cl-2-69k) 由核心的 `PAIRED_TINYLM_SHA256` 配對到這個檔，換了會意檔常談就不生效。
- 融合參數（α、τ、δ；δ 是融合分數要比 n-gram 第一名高出多少才換）：書面三組在檔頭；聊天三組在核心 `core/src/colloquial.rs`（常談生效時用它；檔頭的聊天值只在口語項不是有限值時用）。

| 設定 | 保守 | 標準 | 積極 |
|---|---|---|---|
| 聊天 | 0.7、2、1 | 0.7、2、0 | 1.0、2、0.1 |
| 書面 | 0.5、2、1 | 0.5、2、0.5 | 0.5、2、0.5（同標準） |

- 訓練：Colab G4，`experiments/s7a-tinylm`（工具 `exp/sr7-tools` 的 `50bdb1ab`、`2d8e25b5`，前處理 `df6b7c7b`），配方照上一版（AdamW、cosine、峰值 1e-3、200 步 warmup，一個 epoch），臂 R1 的種子 20261018，36,005 步。選臂：R0（原配方重跑）、R1（加全部新語料）、R½（新語料一半）各 3 個種子，在 chatclause 上對上一版比 U2＝改對 − 2×改壞：R1 104／40、106／42、103／46，R½ 100／35、96／44、98／47，R0 的 U2 −34 到 −45；選 R1 中位數的種子（5,211 → 5,275，p < 0.0001）。匯出用 `export_sjtl.py --type int8 --ngram-model <sj-lm-7-9.7m> --kn-side <sj-kn-2-397k>`，融合參數取自上表〔推論：研究紀錄只寫了值，沒有寫完整命令列〕；用上一版的值重匯出要和 `5f881309…` 逐位元組相同，成立；最後一次只改了檔頭 164..187（書面保守）。
- 量測（對現行出貨，改對／改壞；研究紀錄 2026-10-26、10-27）：

| 單位 | 第 1 條 discordtune | 第 2 條 打字測驗 | 第 3 條 cvtune 90% 下界 | 第 4 條 錯字回報（本設定） | 動漫五集＋名單 |
|---|---|---|---|---|---|
| 聊天保守 | 360／41 | 1／0 | +1.55 pp | 79／1 | 42／36 |
| 聊天標準 | 314／46 | 2／0 | +0.67 pp | 72／1 | 44／36 |
| 聊天積極 | 311／47 | 2／0 | +0.21 pp | 72／2 | 47／40 |
| 書面保守 | — | 6／2 | — | 68／3 | 71／18 |
| 書面標準＝積極 | — | 5／0 | — | 71／4 | 77／16 |

  - 第 1 條用過濾後的 discordtune（4,919 列，拿掉暱稱、稱呼與故意的寫法；三檔改壞÷改對 0.11、0.15、0.15）。第 4 條以兩種設定合計判，六個單位都過。
  - **出貨標準第 2 條的例外**（維護者 2026-10-26 同意）：dev302 書面標準改壞 9 列（改對 15）、保守改壞 10 列（改對 20），主要來自通語等 n-gram 底層；wikitune-clean 書面標準 1,641 → 1,625。只報、結果是負的：資訊詞包檢查集聊天 3／14、書面 4／10。
  - 會意 L 規模（3,023 萬參數）與書面兩臂 N、W 的探索照登記沒有比 R1 好，沒有出貨。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。訓練文字：
  - 中文維基百科 2026-10-01 dump 的前 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）〔推論：沿用上一版的快照〕
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
  - g0v Slack `#rand0m` 頻道的公開訊息（CC0）
  - PTT 批踢踢實業坊公開看板的文章與推文（只用於訓練，不含原文）
  - OPUS OpenSubtitles v2018 繁體字幕（只用於訓練，不含原文；清理後 4,432,866 句、42,814,742 個漢字）。照 OPUS 的要求引用：P. Lison and J. Tiedemann, 2016, OpenSubtitles2016: Extracting Large Parallel Corpora from Movie and TV Subtitles. In Proceedings of the 10th International Conference on Language Resources and Evaluation (LREC 2016)；字幕來自 http://www.opensubtitles.org/ 。
- 不含任何私人對話資料；聊天的值在維護者自己的訊息建的評測集上選，檔案裡沒有任何訊息的文字。
- 權重來源：臂 R1 中位數的種子 20261018 的 `tinylm-M.npz`（SHA-256 `cb351ed7d210b61540ff0d629bdbd45882031ab6e8817e5670d9181c2a151c5c`，沒有另外發布）。
- 延遲與記憶體（Mac 閒置窗，對 main，研究紀錄 2026-10-28）：按鍵 p95 聊天 2.10 ms、書面 2.11 ms；重排 p95 8.0／7.9 ms；記憶體比 main 多 34～38 MiB（重播）、3～28 MiB（重排），都在門檻內。

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
