善解「語意校正」用的小型字元語言模型，新的 SJTL0004 格式：權重和 `tinylm-v1`～`v4` 相同，融合參數在 [model-v6](https://github.com/Nanako0129/shanjie/releases/tag/model-v6)＋[classes-v4](https://github.com/Nanako0129/shanjie/releases/tag/classes-v4)＋[kn-v1](https://github.com/Nanako0129/shanjie/releases/tag/kn-v1)（Kneser–Ney 開著）的候選上重新選過，檔頭多記了調參時用的 `kn.sjkn` 的 SHA-256。**這不是輸入法的發行版。**

- 檔案：`tinylm.sjtl`，14,638,352 bytes
- SHA-256：`d07de71dbbe6f45ceab0dd67aa8b607802f40ff5fbfe60a1fa87ed69c379070f`
- **需要的核心**：讀得懂 SJTL0004 的核心（`docs/contracts/model-v6.md` §2.7）。舊核心遇到這個版本會拒絕載入、只用 n-gram。
- 內容：和 `tinylm-v4` 相同的 1,440 萬參數字元語言模型（6 層、d 384、字表 9,714 字加 PAD、UNK、BOS 共 9,717 個 id；二維權重每列對稱 int8 加 float32 縮放，一維向量 float32）。只能在 n-gram 解碼出的前 8 名候選之間重排，不會產生新的文字。檔頭從 212 bytes 變成 244 bytes（最後 32 bytes 是 kn-v1 的 SHA-256）；檔頭之後的字表與張量和調參時用的暫時檔逐位元組相同（匯出後用切片比對過）。
- 融合參數（α、τ、δ；δ 是門檻：融合分數最高的候選要比 n-gram 第一名高出 δ 以上才換，`docs/contracts/s7c2c-delta.md`）：

| 設定 | 保守 | 標準 | 積極 |
|---|---|---|---|
| 聊天 | 1、2、3 | 0.3、2、0.25 | 0.3、2、0.25（同標準） |
| 書面 | 1、1、4 | 1、1、3 | 1、1、3（同標準） |

  換了 n-gram 模型，標準那一檔也重選：聊天選出的值和 `tinylm-v4` 相同，書面從 0.7、1、2 變成 1、1、3。積極兩種設定都沒過出貨門檻，照規則寫成標準值：聊天選出的 0.5、1、0 在立法院公報委員發言（`lytune`）改對 224 < 改壞 403；書面選出的 0.5、1、0.25 在 lytune 的淨值 −3，低於標準的 28。

- 值怎麼選（`docs/contracts/model-v6.md` §2.7、`docs/contracts/rerank-strength.md` §11 的做法，研究紀錄 2026-10-15）：在 model-v6＋classes-v4＋kn-v1＋常見專有名詞層的候選上，聊天用維護者自己的聊天訊息建的調參集（只用來選數字，檔案裡沒有任何訊息的文字），書面用 cvtune＋wikitune，依各檔的改壞上限或權重選值，再過契約的出貨門檻。
- 量測，對同一個 n-gram（model-v6）的第一名（改對／改壞；公開集合；錯字回報 114 列）：
  - 聊天標準（＝積極）：dev302 14／1、打字測驗 2／1、錯字回報 7／2、cvtune 81／2、wikitune 107／6、lytune 92／77、維基錯字表（`wikitypo`）0／0
  - 聊天保守：dev302 4／0、打字測驗 2／0、錯字回報 1／0、cvtune 28／1、wikitune 57／1、lytune 19／6、wikitypo 0／0
  - 書面標準（＝積極）：dev302 5／0、打字測驗 1／0、錯字回報 1／1、cvtune 33／0、wikitune 28／1、lytune 33／5、wikitypo 0／0
  - 書面保守：dev302 3／0、打字測驗 1／0、錯字回報 0／0、cvtune 18／0、wikitune 14／0、lytune 12／3、wikitypo 0／0
- 保留集（fresh verifier，2026-10-15，建 Release 之前；對 model-v6 的 n-gram 第一名，227 列）：聊天標準 8／1、保守 3／0、積極 8／1；書面標準 2／0、保守 1／0、積極 2／0。六格的淨值都不是負的。
- **綁定**：檔案記了 model-v6 `bigram.sjlm` 的 SHA-256 與 kn-v1 `kn.sjkn` 的 SHA-256。核心在 KN 開著時要求兩個都相符，KN 關著時拒絕這個檔（參數是開著 KN 調的）；不符就拒絕載入、只用 n-gram。換 n-gram 模型、詞類表或 KN 側檔都要重新調參、發新的檔（`docs/data-files.md`）。
- 產生：S7a 訓練出的 `tinylm-M.npz`（SHA-256 `32308122782d3c5aba8a838521f031a466671d47d48ec0f54dcfb9256451815e`，沒有另外發布；訓練見 `docs/contracts/s7a-tinylm.md`），用 `experiments/s7a-tinylm/export_sjtl.py` 以 `--type int8 --ngram-model <model-v6> --kn-side <kn-v1> --chat 0.3 2 0.25 --formal 1 1 3 --levels 1 2 3 0.3 2 0.25 1 1 4 1 1 3` 匯出。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。訓練文字和 `tinylm-v1`～`v4` 相同：
  - 中文維基百科 2026-10-01 dump 的前 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
- 不含任何私人對話資料。

下載：

```sh
gh release download tinylm-v5 -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm \
  && echo "d07de71dbbe6f45ceab0dd67aa8b607802f40ff5fbfe60a1fa87ed69c379070f  data/lm/tinylm.sjtl" | shasum -a 256 -c
```
