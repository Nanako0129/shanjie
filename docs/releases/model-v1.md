善解的 bigram 語言模型（SJLM0001 格式），CI 與發布版從這裡下載。**這不是輸入法的發行版。**

- 檔案：`bigram.sjlm`，80,040,411 bytes
- SHA-256：`9879fd8b264b1c1f4c083ccedf84dc5625cd8d2595bd2d13150eed5a0520a923`
- 產生：`tools/build_lm.py`（格式說明在該檔開頭），Python 參考實作 `reference/proto/lm.py`；詳見 `docs/PLAN.md` §S2、§S2c
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。模型由下列語料的詞頻計數而來：
  - 中文維基百科 2026-10-01 dump 的 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
- 不含任何私人對話資料。
