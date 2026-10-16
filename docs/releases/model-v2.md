善解的 bigram 語言模型（SJLM0001 格式），CI 與發布版從這裡下載。**這不是輸入法的發行版。**

- 檔案：`bigram.sjlm`，80,704,045 bytes
- SHA-256：`8847b73a7b9cf127b4882328191c3c5250fe9a55912926d5050e351ab644d240`
- 產生：`tools/build_lm.py`（格式說明在該檔開頭），計數用 `experiments/s2/build_counts.py --expected` 與 `build_counts_text.py --expected`；Python 參考實作 `reference/proto/lm.py`。規格見 `docs/contracts/s2n-simplified-residue.md`（S2n），量測見 `docs/research-log.md`。
- 和 model-v1 的差別：
  - 語料先判斷簡繁再轉換，維基轉換後殘留的簡體寫法（之后、由于、最后、哪里…）降到 0；吃、著、為、說照台灣用字。
  - 訓練計數改成詞圖上的期望次數：同一段文字的每種切法都依機率計入，詞庫裡的長詞（例如「後到」「好吧」）不再吃掉拆開時的二元組。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。模型由下列語料的詞頻計數而來：
  - 中文維基百科 2026-10-01 dump 的 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
- 不含任何私人對話資料。
