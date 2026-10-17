善解「語意校正」用的小型字元語言模型。這一版是會意（SJ-SR）第 6 版：權重和 `tinylm-v5`（SJ-SR-5-14M）完全相同，只換了**書面設定**的融合參數，在現在的書面解碼器（[model-v6](https://github.com/Nanako0129/shanjie/releases/tag/model-v6)＋[kn-v1](https://github.com/Nanako0129/shanjie/releases/tag/kn-v1)，書面另用三元表 [sj-cx-1-7.7m](https://github.com/Nanako0129/shanjie/releases/tag/sj-cx-1-7.7m)）上重選。聊天設定的參數不變。**這不是輸入法的發行版。**

- 檔案：`tinylm.sjtl`，14,638,352 bytes
- SHA-256：`e7c179d85405ec42a40bb22adb58a7485c58f77aed89dfc2ca76a0d89568ed5e`
- 格式：SJTL0004（和 `tinylm-v5` 相同，現在的核心都讀得懂）。
- 內容：1,440 萬參數的字元語言模型（6 層、d 384、字表 9,717 個 id；二維權重每列對稱 int8 加 float32 縮放）。只在 n-gram 解碼出的前 8 名候選之間重排，不會產生新的文字。和 `tinylm-v5` 比，只有檔頭的書面融合參數不同（逐欄比對：書面 α、τ、δ 與書面積極檔三個數；字表與 76 個張量逐位元組相同，檔頭 244 bytes 之後沒有任何位元組不同）。
- 融合參數（α、τ、δ；δ 是融合分數要比 n-gram 第一名高出多少才換）：

| 設定 | 保守 | 標準 | 積極 |
|---|---|---|---|
| 聊天 | 1、2、3 | 0.3、2、0.25 | 0.3、2、0.25（同標準） |
| 書面 | 1、1、4 | **0.7、2、1**（原本 1、1、3） | **0.7、2、1**（同標準） |

- 值怎麼選（`docs/contracts/rerank-formal.md` §3、§8–§10，研究紀錄 2026-10-17）：書面候選與分數用現在出貨的設定重新產生；選值只用 cvtune＋wikitune，改對與改壞 1:1 計分；預設檔的改壞率上限 0.5%。原本選出的 0.5、2、0.25 在其他來源的改壞率 1.72%，沒有出貨。維護者要「改對更多、但守住 0.5%」，所以在 0.1%–0.5% 之間的幾個選值上限各選一格，用已看過的資料篩過，再在沒有任何人看過的維基判讀集（第 315,000–319,999 篇）上確認：0.7、2、1 對原本的書面值改對 39、改壞 11（p 0.0001），改壞率 0.35%。
- 量測，書面設定，對同一個 n-gram 的第一名（改對／改壞；公開集合；錯字回報 133 列）：
  - 書面標準（＝積極）：dev302 14／2、打字測驗 1／0、錯字回報 8／1、cvtune 76／4、wikitune 66／6、維基判讀集 84／6 與 67／10、lytune 96／35、維基錯字表 0／0
  - 書面保守：dev302 2／0、打字測驗 0／0、錯字回報 0／0、維基判讀集 15／0 與 16／1、lytune 7／1、維基錯字表 0／0
- 保留集（fresh verifier，2026-10-17，建 Release 之前；300 列）：書面標準與積極對 n-gram 12／0、對 `tinylm-v5` 8／0；書面保守對 n-gram 3／0、對 `tinylm-v5` 0／0。
- **綁定**：檔案記了 model-v6 `bigram.sjlm` 與 kn-v1 `kn.sjkn` 的 SHA-256，和 `tinylm-v5` 相同；不符就拒絕載入、只用 n-gram。
- 產生：`tinylm-M.npz`（SHA-256 `32308122782d3c5aba8a838521f031a466671d47d48ec0f54dcfb9256451815e`，沒有另外發布），用 `experiments/s7a-tinylm/export_sjtl.py` 以 `--type int8 --ngram-model <model-v6> --kn-side <kn-v1> --chat 0.3 2 0.25 --formal 0.7 2 1 --levels 1 2 3 0.3 2 0.25 1 1 4 0.7 2 1` 匯出。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。訓練文字和 `tinylm-v1`～`v5` 相同：
  - 中文維基百科 2026-10-01 dump 的前 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
