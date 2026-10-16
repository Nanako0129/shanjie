善解的 bigram 語言模型（SJLM0001 格式），CI 與發布版從這裡下載。**這不是輸入法的發行版。**

- 檔案：`bigram.sjlm`，81,373,869 bytes
- SHA-256：`5c7d5a94f762e7c5d87e14e47b03c1138222df4ea194e70e503bdd9a71ab5a48`
- 產生：`tools/build_lm.py`（格式說明在該檔開頭），計數用 `experiments/s2/build_counts.py --expected` 與 `build_counts_text.py --expected`；Python 參考實作 `reference/proto/lm.py`。規格見 `docs/contracts/s2f-variant-forms.md`（S2f），量測見 `experiments/s2f/README.md` 與 `docs/research-log.md`。
- 和 model-v2 的差別：
  - 台灣常用字形（床、秘、灶、粽、庄…）不再被當成簡體字轉成異體字形，「起牀」「祕密」這類寫法回到「起床」「秘密」。
  - 轉換後再套台灣字形表；少數換字後會改壞詞庫詞的字（排泄、棱錐、繁體句的竈門…）保留原字。
  - 同讀音的異體寫法合併計數；疊加層拿掉只是基底詞異體寫法的詞。
  - 守門集（dev302、打字測驗、錯字回報、cvtune-native）沒有否決；保留集第一名和 model-v2 逐列相同。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。模型由下列語料的詞頻計數而來：
  - 中文維基百科 2026-10-01 dump 的 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
- 不含任何私人對話資料。
