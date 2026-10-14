善解「語意校正」用的小型字元語言模型，新的 SJTL0003 格式：權重和 `tinylm-v1`～`v3` 相同，檔頭多了「語意校正強度」的保守與積極兩檔融合參數；標準那一檔的值和 `tinylm-v3` 相同。**這不是輸入法的發行版。**

- 檔案：`tinylm.sjtl`，14,638,320 bytes
- SHA-256：`7b39c7529071d97fa911380ce96aad93f9f89b69ef554e383eacacca425c0865`
- **需要的核心**：讀得懂 SJTL0003 的核心（語意校正強度那一片，`docs/contracts/rerank-strength.md`）。舊核心只認 SJTL0001、SJTL0002，遇到這個版本會拒絕載入、只用 n-gram。輸入法要等切換到這個檔的那一版才會用到它。
- 內容：和 `tinylm-v3` 相同的 1,440 萬參數字元語言模型（6 層、d 384、字表 9,714 字加 PAD、UNK、BOS 共 9,717 個 id；二維權重每列對稱 int8 加 float32 縮放，一維向量 float32）。只能在 n-gram 解碼出的前 8 名候選之間重排，不會產生新的文字。檔頭從 116 bytes 變成 212 bytes（多了兩種設定 × 保守、積極的 α、τ、δ），bytes 12–115 與檔頭之後的字表、張量都和 `tinylm-v3` 逐位元組相同（匯出後用切片比對過）。
- 融合參數（α、τ、δ；δ 是門檻：融合分數最高的候選要比 n-gram 第一名高出 δ 以上才換，`docs/contracts/s7c2c-delta.md`）：

| 設定 | 保守 | 標準 | 積極 |
|---|---|---|---|
| 聊天 | 1、2、3 | 0.3、2、0.25 | 0.3、2、0.25（同標準） |
| 書面 | 0.7、0.5、2 | 0.7、1、2 | 0.5、1、0.25 |

  聊天的積極選出的是 0.5、1、0，在立法院公報委員發言（`lytune`）改對 236 < 改壞 374，沒過出貨門檻，照規則寫成標準值。

- 值怎麼選（`docs/contracts/rerank-strength.md` §11，研究紀錄 2026-10-14「語意校正強度修訂一」）：在 v0.4.3 的候選上，聊天用維護者自己的聊天訊息建的調參集（只用來選數字，檔案裡沒有任何訊息的文字），書面用 cvtune＋wikitune，依各檔的改壞上限或權重選值，再過契約 §4.3 的門檻。
- 量測，對 n-gram 第一名（改對／改壞；公開集合；錯字回報是當時的 107 列）：
  - 聊天標準（＝積極）：dev302 18／0、打字測驗 3／0、錯字回報 10／2、cvtune 80／3、wikitune 108／5、lytune 102／50、維基錯字表（`wikitypo`）0／2
  - 聊天保守：dev302 6／0、打字測驗 3／0、錯字回報 0／0、cvtune 29／2、wikitune 62／1、lytune 26／5、wikitypo 0／0
  - 書面標準：dev302 4／0、打字測驗 1／0、錯字回報 0／1、cvtune 40／1、wikitune 30／0、lytune 33／3、wikitypo 0／0
  - 書面保守：dev302 4／0、打字測驗 0／0、錯字回報 0／0、cvtune 36／0、wikitune 26／0、lytune 30／1、wikitypo 0／0
  - 書面積極：dev302 19／1、打字測驗 1／0、錯字回報 15／3、cvtune 118／15、wikitune 112／17、lytune 157／126、wikitypo 0／1
- 保留集（fresh verifier，2026-10-14，建 Release 之前；對 n-gram 第一名，227 列）：聊天標準 8／0、保守 4／0、積極 8／0；書面標準 3／0、保守 2／0、積極 12／1。六格的淨值都不是負的。
- **綁定**：和 `tinylm-v3` 相同，檔案記了 [model-v5](https://github.com/Nanako0129/shanjie/releases/tag/model-v5) `bigram.sjlm` 的 SHA-256，配到別的 n-gram 模型就拒絕載入；這個核對不看詞類表，參數是在 model-v5＋[classes-v3](https://github.com/Nanako0129/shanjie/releases/tag/classes-v3) 上調的。換 n-gram 模型或詞類表都要重新調參、發新的檔（`docs/data-files.md`）。
- 產生：S7a 訓練出的 `tinylm-M.npz`（SHA-256 `32308122782d3c5aba8a838521f031a466671d47d48ec0f54dcfb9256451815e`，沒有另外發布；訓練見 `docs/contracts/s7a-tinylm.md`），用 `experiments/s7a-tinylm/export_sjtl.py` 以 `--type int8 --chat 0.3 2 0.25 --formal 0.7 1 2 --levels 1 2 3 0.3 2 0.25 0.7 0.5 2 0.5 1 0.25` 匯出。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。訓練文字和 `tinylm-v1`～`v3` 相同，和 model-v5 的 `bigram.sjlm` 同源：
  - 中文維基百科 2026-10-01 dump 的前 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
- 不含任何私人對話資料。

下載（下載到另一個資料夾：切換到 v4 之前，repo 的 `data/tinylm.sjtl.sha256` 記的還是 `tinylm-v3`，蓋掉 `data/lm/tinylm.sjtl` 會讓 `shasum -a 256 -c data/*.sha256` 失敗）：

```sh
gh release download tinylm-v4 -R Nanako0129/shanjie -p tinylm.sjtl -D tinylm-v4 \
  && echo "7b39c7529071d97fa911380ce96aad93f9f89b69ef554e383eacacca425c0865  tinylm-v4/tinylm.sjtl" | shasum -a 256 -c
```
