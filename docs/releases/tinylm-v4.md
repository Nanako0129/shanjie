善解「語意校正」用的小型字元語言模型（SJTL0003 格式）。權重和 `tinylm-v1`～`v3` 相同，「標準」的融合參數和 `tinylm-v3` 相同；新加「保守」與「積極」兩檔的參數，給設定視窗的語意校正強度用（`docs/contracts/rerank-strength.md`）。**這不是輸入法的發行版。**

- 檔案：`tinylm.sjtl`，14,638,320 bytes
- SHA-256：`7abc4e7177b3a8a5f7d17e458652f8de76a0f9d98c09b6de6367596f50d90262`
- **需要的核心**：讀得懂 SJTL0003 的核心（語意校正強度那一片，`docs/contracts/rerank-strength.md` §2）。v0.4.3 以前的輸入法與 CLI 不認得這個格式，會拒絕載入（量過：v0.4.2 的 `shanjie-eval --tinylm` 回「bad tiny language model file」，結束碼 1）。
- 內容：和 `tinylm-v3` 的檔只差檔頭多 96 bytes 的檔位參數：新檔 [12,116) 等於 `tinylm-v3` 的 [12,116)，新檔 212 之後等於 `tinylm-v3` 116 之後（研究紀錄 2026-10-14，比切片，結束碼 0）。只能在 n-gram 解碼出的前 8 名候選之間重排，不會產生新的文字。
- 融合參數（α、τ、δ；δ 是門檻：融合分數最高的候選要比 n-gram 第一名高出 δ 以上才換）：

| 設定 | 標準 | 保守 | 積極 |
|---|---|---|---|
| 聊天 | 0.3、2、0.25 | 1、2、3 | 0.3、2、0.25（和標準相同） |
| 書面 | 0.7、1、2 | 0.5、1、1.5 | 0.5、1、0.25 |

- 怎麼選（契約 §1、§4.3、§10）：保守與積極在選值集上各自用改壞率上限選出，再過出貨門檻。聊天的積極在立法院發言語料（`lytune`）改對 241 少於改壞 369，照門檻寫回標準值；書面的積極保留。
- 保留集（fresh verifier，2026-10-14，建 Release 之前；對 n-gram 第一名）：

| 設定 | 標準 | 保守 | 積極 |
|---|---|---|---|
| 聊天 | 改對 11／改壞 0 | 5／0 | 11／0 |
| 書面 | 3／0 | 2／0 | 13／1 |

- 各集合的完整表（dev302、打字測驗、錯字回報、cvtune、wikitune、lytune、wikitypo，以及聊天的私人調參集，只有數字）在研究紀錄 2026-10-14「語意校正強度（保守／標準／積極）的選值、匯出與保留集」。
- **綁定**：和 `tinylm-v3` 相同，檔案記了 [model-v5](https://github.com/Nanako0129/shanjie/releases/tag/model-v5) `bigram.sjlm` 的 SHA-256，配到別的 n-gram 模型就拒絕載入；參數是在 model-v5＋[classes-v3](https://github.com/Nanako0129/shanjie/releases/tag/classes-v3) 上調的。
- 產生：S7a 訓練出的 `tinylm-M.npz`（SHA-256 `32308122782d3c5aba8a838521f031a466671d47d48ec0f54dcfb9256451815e`，沒有另外發布），用 `experiments/s7a-tinylm/export_sjtl.py` 以 `--type int8 --chat 0.3 2 0.25 --formal 0.7 1 2 --levels 1 2 3 0.3 2 0.25 0.5 1 1.5 0.5 1 0.25` 匯出。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。訓練文字和 `tinylm-v1`～`v3` 相同，和 model-v5 的 `bigram.sjlm` 同源：
  - 中文維基百科 2026-10-01 dump 的前 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
- 不含任何私人對話資料。

下載（下載到另一個資料夾：換用 v4 的 PR 合併之前，repo 的 `data/tinylm.sjtl.sha256` 記的還是 `tinylm-v3`）：

```sh
gh release download tinylm-v4 -R Nanako0129/shanjie -p tinylm.sjtl -D tinylm-v4 \
  && echo "7abc4e7177b3a8a5f7d17e458652f8de76a0f9d98c09b6de6367596f50d90262  tinylm-v4/tinylm.sjtl" | shasum -a 256 -c
```
