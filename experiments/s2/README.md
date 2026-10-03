# S2 實驗：n-gram 語言模型（Python 原型，2026-10-03）

在原型解碼上加 bigram，量各評測集的 top-1。語料只在本機 `~/.cache/shanjie/` 訓練用，不散布原文；計數檔也不進 repo。

## 語料

| 來源 | 授權 | 規模 | 程式 |
|---|---|---|---|
| 中文維基 2026-10-01 前 5 萬篇條目（sha1 和官方相符），用 OpenCC 轉台灣繁體 | CC BY-SA 4.0 | 5,697 萬詞次 | `build_counts.py` |
| Mozilla Common Voice zh-TW 句子（commit ff32a0e） | CC0 | 1.9 萬句 | `build_counts_text.py` |
| Tatoeba cmn 例句 | CC BY 2.0 FR | 8.9 萬句 | 同上 |
| Cerebras gpt-oss-120b（Apache-2.0 開放權重）合成的台灣口語聊天句；提示詞只寫主題與語氣，不放評測句 | 模型輸出 | 13.5 萬句（花費約 US$9，用偏高單價估算，上限是使用者核准的 US$10） | `synth_colloquial.py` |

口語三個來源合計 182 萬詞次。

## 方法

`eval_bigram.py`、`eval_mix.py`：透過原型的 learner 掛勾加分，加分＝λ·log10(P_bigram(w|v)/P_uni(w))。P_bigram 是 absolute discounting 插值（D=0.75），語料沒見過的詞不加分。口語計數乘上 k 之後再和維基相加。解碼用 `decode(beam=64)`，看第一名；Discord 與新聞稿的句子在本機私有檔，只印數字。

## 結果（λ=0.5，第一名寬鬆正確）

| 語料 | dev 302 | 打字測驗 76 | Discord 845 | 新聞稿 294 |
|---|---|---|---|---|
| 無 bigram | 163 | 44 | 648 | 213 |
| 維基 | 212 | 58 | 664 | 236 |
| 維基＋口語（Common Voice、Tatoeba），k=20 | 219 | 58 | 669 | 234 |
| **維基＋口語（加合成句），k=5** | **231** | **65** | **685** | 235 |
| 維基＋口語（加合成句），k=10 | 230 | 65 | 680 | 234 |
| 維基＋口語（加合成句），k=20 | 227 | 63 | 672 | 233 |

- λ 掃描（只用維基）：0.5 最佳；1.0 以上開始變差，2.0 甚至低於不加 bigram。
- 比較基準：macOS 內建在 Discord 錯 100 句、新聞稿錯 64 句。它學過使用者平常的習慣，也在測驗中學到測驗句，見 `docs/typing-test.md`。
- 「無 bigram」那一列的 Discord 和新聞稿數字，是用寬鬆標準重算的 top-1。
