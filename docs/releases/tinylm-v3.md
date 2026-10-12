善解「語意校正」用的小型字元語言模型（SJTL0002 格式）。權重和 `tinylm-v1`、`tinylm-v2` 相同，只有**聊天設定**的融合參數不同：改用聊天評測（E2）選出的值，改得比 v2 多；聊天評測判斷期的改壞率 0.24%，在預設開啟的上限 0.5% 以內。書面設定的參數和 v2 相同。**這不是輸入法的發行版。**

- 檔案：`tinylm.sjtl`，14,638,224 bytes
- SHA-256：`7e73ef677cd232099132b542c3f3e92aa6a1d7d17725fb0fbda25873509f12ce`
- **需要的核心**：和 `tinylm-v2` 相同（SJTL0002，含 S7c-2c [#122](https://github.com/Nanako0129/shanjie/pull/122) 之後的核心）。輸入法要等改讀這個檔的那一版（S7h，`docs/contracts/s7h-tinylm-v3.md`）才會用到它。
- 內容：和 `tinylm-v1`、`v2` 相同的 1,440 萬參數字元語言模型（6 層、d 384、字表 9,714 字加 PAD、UNK、BOS 共 9,717 個 id；二維權重每列對稱 int8 加 float32 縮放，一維向量 float32）。只能在 n-gram 解碼出的前 8 名候選之間重排，不會產生新的文字。和 `tinylm-v2` 的檔只差 9 個 byte（檔頭裡聊天的 α、τ）。
- 融合參數：**聊天 α 0.3、τ 2、δ 0.25**（v2 是 0.2、1、0.25）；書面 α 0.7、τ 1、δ 2（不變）。δ 是門檻：融合分數最高的候選，要比 n-gram 第一名高出 δ 以上才換（`docs/contracts/s7c2c-delta.md`）。
- 聊天的值怎麼選（`docs/contracts/e2-chat-eval.md`，研究紀錄 2026-10-13）：在維護者自己的聊天訊息建的評測集上，用「改對 − 改壞」（1:1）計分、改壞率上限 0.5%，在調參期的月份選值並先凍結，之後才在判斷期（2026-08）算一次。評測集只用來選三個數字；檔案裡沒有任何訊息的文字，評測集本身不公開。
- **綁定**：和 `tinylm-v2` 相同，檔案記了 [model-v5](https://github.com/Nanako0129/shanjie/releases/tag/model-v5) `bigram.sjlm` 的 SHA-256，配到別的 n-gram 模型就拒絕載入；這個核對不看詞類表，參數是在 model-v5＋[classes-v3](https://github.com/Nanako0129/shanjie/releases/tag/classes-v3) 上調的。換 n-gram 模型或詞類表都要重新調參、發新的檔（`docs/data-files.md`）。
- 產生：S7a 訓練出的 `tinylm-M.npz`（SHA-256 `32308122782d3c5aba8a838521f031a466671d47d48ec0f54dcfb9256451815e`，沒有另外發布；訓練見 `docs/contracts/s7a-tinylm.md`），用 `experiments/s7a-tinylm/export_sjtl.py` 以 `--type int8 --chat 0.3 2 0.25 --formal 0.7 1 2` 匯出（`docs/contracts/s7h-tinylm-v3.md`）。
- 量測，聊天設定（對 n-gram 第一名）：
  - 聊天評測的判斷期（2026-08，6,559 列，只記數字）：改對 125／改壞 16，淨值 +109，改壞率 0.24%（改壞數 95% 上界換成比例 0.37%）；`tinylm-v2` 的聊天值是 82／7、+75、0.11%。
  - Rust 的 int8 路徑重現 E2 的 numpy 表，8 個集合逐字元相同（研究紀錄 2026-10-13 的 S7h 一節）：
    - 公開集合：dev302 241 → 259（改對 18／改壞 0）、cvtune 83／4、wikitune 108／5、打字測驗 3／0、錯字回報（前 72 列）9／1、功能字測試句 4／1。
    - 口語守門集合：立法院公報委員發言（`lytune`）107／49、維基錯字表（`wikitypo`）0／2（p = 0.50）。
  - 錯字回報整個檔（77 列）：n-gram → v3 9／1，`tinylm-v2` → v3 6／1。
- 量測，書面設定：參數和 `tinylm-v2` 相同，dev302、cvtune 的結果逐位元組相同；對 n-gram 第一名，dev302 4／0、打字測驗 1／0、錯字回報 1／0。
- 保留集〔待 fresh verifier 的結果〕。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。訓練文字和 `tinylm-v1`、`v2` 相同，和 model-v5 的 `bigram.sjlm` 同源：
  - 中文維基百科 2026-10-01 dump 的前 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
- 不含任何私人對話資料。

下載（下載到另一個資料夾：S7h 換用 v3 之前，repo 的 `data/tinylm.sjtl.sha256` 記的還是 `tinylm-v2`，蓋掉 `data/lm/tinylm.sjtl` 會讓 `shasum -a 256 -c data/*.sha256` 失敗）：

```sh
gh release download tinylm-v3 -R Nanako0129/shanjie -p tinylm.sjtl -D tinylm-v3 \
  && echo "7e73ef677cd232099132b542c3f3e92aa6a1d7d17725fb0fbda25873509f12ce  tinylm-v3/tinylm.sjtl" | shasum -a 256 -c
```
