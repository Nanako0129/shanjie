善解「語意校正」用的小型字元語言模型，會意（SJ-SR）第 10 版 **SJ-SR-10-31M**（版本號跳過 9），接替 [sj-sr-8-15m](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-8-15m)。和上一版不同的有三件：**模型變大並從頭訓練**（8 層、寬 512，參數約兩倍），**讀長前文**（游標前最後 62 個字元，標點與其他非漢字變成邊界記號；上一版只讀結尾最多 2 個漢字），以及**三檔的融合參數重選**。**這不是輸入法的發行版。**

- 檔案：`tinylm.sjtl`，30,927,424 bytes（實際匯出的檔，和由檔頭與格式說明的張量清單算出的長度相同；上一版 14,886,880 bytes，App 約多 16 MB）
- SHA-256：`ee109fb284cbd5fea8db825752fec5b8a08deb6541d772dc682091e2c99c70d4`
- 格式：SJTL0006（`docs/spec/semantic-rerank.md` §2.1），邊界格式（格式旗標 bit 0 為 1）、宣告的前文長度 L 62、政策鍵的門檻 K 4。內容（讀檔頭）：8 層、寬 512、8 頭、前文 64、字表 10,378 個 id（10,374 個字，加 PAD、UNK、BOS 與 `[SEP]`；`[SEP]` 是 id 10,377，不在碼位清單裡）；二維權重每列對稱 int8 加 float32 縮放，一維向量 float32。只在 n-gram 解碼出的前 8 名候選之間重排，不會產生新的文字。
- 參數數 30,566,400，是每個張量的元素數加總（和上一版的 14,646,912 寫成 15M 同一種算法，四捨五入到百萬）：
  - 字元嵌入 10,378 × 512 ＝ 5,313,536（輸入與輸出共用）
  - 位置嵌入 64 × 512 ＝ 32,768
  - 每層 3,152,384：`qkv_w` 512 × 1,536、`proj_w` 512 × 512、`fc1_w` 512 × 2,048、`fc2_w` 2,048 × 512 共 3,145,728，四個偏差向量 4,608，兩個 LayerNorm 2,048；8 層共 25,219,072
  - 最後的 LayerNorm 1,024
- **讀的前文**：核心取殼傳來的前文的最後 62 個字元，把每一段連續的非漢字（標點、空白、英文、數字、換行）變成一個 `[SEP]`，照候選長度截到 `[BOS]`＋鍵＋候選不超過 64 個 token；這把長鍵含 4 個以上的漢字時用它，否則用上一版的鍵（結尾最多 2 個漢字）。規則在 `docs/spec/semantic-rerank.md` §5.2。和上一版一樣只讀候選的文字，不讀注音；讀音只經 n-gram 分數裡的讀音項進來。
- **綁定**：檔頭記了 [sj-lm-7-9.7m](https://github.com/Nanako0129/shanjie/releases/tag/sj-lm-7-9.7m) `bigram.sjlm`（`e3fb6056…6148eb5a`）與 [sj-kn-2-397k](https://github.com/Nanako0129/shanjie/releases/tag/sj-kn-2-397k) `kn.sjkn`（`880ee87c…254e985a`）的 SHA-256，不符就拒絕載入、只用 n-gram（和上一版相同）。常談 [sj-cl-2-69k](https://github.com/Nanako0129/shanjie/releases/tag/sj-cl-2-69k) 由核心的 `PAIRED_TINYLM_SHA256` 配對到這個檔，換了會意檔常談就不生效。
- 融合參數（α、τ、δ；δ 是融合分數要比 n-gram 第一名高出多少才換）：書面三組在檔頭；聊天三組在核心 `core/src/colloquial.rs`（常談生效時用它；檔頭的聊天值只在口語項不是有限值時用，照抄上一版，標準組 0.3、2、0.25）。

| 設定 | 保守 | 標準 | 積極 |
|---|---|---|---|
| 聊天 | 1.2、2、1 | 1.4、2、0 | 1.4、2、0（同標準） |
| 書面 | 1.4、1、1.5 | 1.8、2、0 | 1.8、2、0（同標準） |

  上一版是聊天 1.3、2、1／1.1、2、0／1.1、2、0，書面 0.8、2、1.5／0.8、2、0／0.8、2、0。
- 訓練：AWS g7e.2xlarge（RTX PRO 6000），**從頭訓練**（不接續上一版的權重、不蒸餾），一個 epoch、63,724 步，種子 20261012。
  - 文字的來源和上一版同類（見下「授權」），各來源在一個 epoch 裡重複的次數：g0v ×5、論壇推文 ×5、論壇內文 ×1、字幕 ×1。轉換照現行做法從原始文字重建：繁體句不套 OpenCC `TWPhrases`，維基另套校對表（`docs/spec/models.md` §9.8）。
  - 字表在這份文字上重算（出現 5 次以上的字）：10,374 個字，另加 `[SEP]`。
  - 序列有兩種。一般格式：每一行切成漢字段，每段一條 `[BOS] 段`，共 117,111,570 段、1,178,095,684 個 token。帶前文的格式：固定選中的一半的段裡，同一行前面有文字的，另外多放一條 `[BOS] 前文 段`，前文裡每一段連續的非漢字是一個 `[SEP]`，整條不超過 64 個 token，只有段的 token 算損失。每一段都留著一般格式那一條，所以沒有前文時的能力不因為加了前文而少練。更新步數是一般格式 35,974 步、帶前文 27,750 步。
  - 匯出用 `export_sjtl.py --type int8 --left-len 62 --boundary 4 --ngram-model <sj-lm-7-9.7m> --kn-side <sj-kn-2-397k>`。匯出方法核對：同一份權重、檔頭的格子改用上一版的值重匯出，要和先前量測用的檔（`5dce15a6…c37730`）逐位元組相同；這個檔只差檔頭的格子。核對結果：逐位元組相同；本檔和那個檔只差檔頭第 165–188 位元組（三檔的書面格）。
  - int8 對照：量測用的檔（`5dce15a6…`）在評測 CLI 與 numpy 參考（`tools/lib/tinylm.py`，同一份還原後的 int8 權重）之間逐候選比 LL，dev302、打字測驗、長前文集 longctx-1 兩種設定共 69,152 個候選，最大 |ΔLL| 1.88×10⁻⁵（容差 1×10⁻⁴）；長前文集 3,944 列裡 3,933 列走到長鍵與 `[SEP]`。
- 量測：
  - 聊天標準檔的確認（離線讀數，fp32 權重；新模型讀政策鍵、格 1.4、2、0，對上一版的出貨設定 1.1、2、0，兩邊都帶常談）：沒看過的聊天判讀集 cjudge-3a（2,997 列）改對 57、改壞 19、淨值 +38，精確 McNemar p 1.48×10⁻⁵，改壞÷改對 0.333（確認規則：淨值 > 0、p < 0.05、比例 ≤ 0.65）。
  - 書面標準檔的確認（wikijudge11，2,821 列，維基第 360,000～364,999 篇，正解不套 `TWPhrases`）：格 1.8、2、0 對上一版書面標準 0.8、2、0，改對 95、改壞 63、淨值 +32，精確 McNemar p 0.013，改壞÷改對 0.663，**比例超過 0.65，確認不成立**。換成調參層選出的 1.6、2、0.5 在另一份沒看過的 wikijudge12（2,839 列，第 365,000～369,999 篇）再確認一次：淨值 +17、p 0.118、比例 0.721，也不成立。兩份都是新模型整體對得比較多（2,262 → 2,294、2,316 → 2,333），書面標準格照 1.8、2、0 出貨，下一份書面判讀集再複核。
  - 保守檔的選格（調參層，FUSE-2 的規則：改壞不超過標準格的一半、取改對減 4 倍改壞最大）：聊天在 ctune 與 cvtune-clean-tw0 合計 8,758 列選出 1.2、2、1（改對 295、改壞 20；標準格 422、78），書面在 wikitune-clean-tw0 與私人新聞開發集合計 4,182 列選出 1.4、1、1.5（246、20；標準格 376、86）。保守檔的確認留到下一份沒看過的判讀集。
  - 產品片（本檔配上核心的新格，對上一版的出貨設定；出貨標準四條、三檔，App 的預設設定）：標準與積極四條都過（聊天判讀集 cdev 4,993 列淨值 +30、比例 0.531；cvtune-clean-tw0 淨值 +31、Δtop1 的 90% 下界 +0.57 個百分點；打字測驗（聊天、書面）與 dev302 書面合計改壞 1、淨值 +8；錯字回報兩種設定合計改對 26、改壞 10）。保守檔第 2 到 4 條過，第 1 條 cdev 淨值 +15、比例 0.706，**超過 0.6 不過**，照 1.2、2、1 出貨。產品路徑和離線讀數在打字測驗、dev302、cvtune-clean-tw0、錯字回報的四條結論相同。
  - 延遲與記憶體（重排 p95、最高 RSS，對上一版交錯量；同一份權重、檔頭格子不同的 `5dce15a6…` 那個檔，格子不影響速度）：GitHub Actions 的 macOS runner（Apple M1 虛擬機、3 核）上，64 字前文 × 8 個候選的重排 p95 139／137 ms（上限 300 ms），最高 RSS 比上一版多 77 MiB；Mac（M6）閒置時的 128 音節組字測試，開重排的每鍵 p95 14.96／14.65 ms（上一版 14.15／14.69 ms，上限 16 ms），單次重排工作 p95 72.5／74.1 ms（上一版 35.8／37.3 ms），程序最高 RSS 1,063／1,052 MiB（上一版 939／934 MiB）。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。訓練文字（來源和 [sj-sr-8-15m](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-8-15m) 同類）：
  - 中文維基百科 2026-10-01 dump 的前 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0）
  - g0v Slack `#rand0m` 頻道的公開訊息（CC0）
  - 台灣公開論壇（論壇 A）公開看板的文章與推文（只用於訓練，不含原文）
  - OPUS OpenSubtitles v2018 繁體字幕（只用於訓練，不含原文）。照 OPUS 的要求引用：P. Lison and J. Tiedemann, 2016, OpenSubtitles2016: Extracting Large Parallel Corpora from Movie and TV Subtitles. In Proceedings of the 10th International Conference on Language Resources and Evaluation (LREC 2016)；字幕來自 http://www.opensubtitles.org/ 。
- 不含任何私人對話資料；檔案裡沒有任何訊息的文字。
- 權重來源：`tinylm-L.npz`（SHA-256 `bedb4d35fb08f715b14d50e610454c322c295f95745f39f14166e17abebde583`，沒有另外發布）。

下載（一整組）：

```sh
gh release download sj-lm-7-9.7m -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download sj-wc-6-512c -R Nanako0129/shanjie -p classes.sjc -D data/lm
gh release download sj-kn-2-397k -R Nanako0129/shanjie -p kn.sjkn -D data/lm
gh release download sj-cx-2-7.7m -R Nanako0129/shanjie -p trigram.sjt3 -D data/lm
gh release download sj-cx-2-950k -R Nanako0129/shanjie -p trigram-chat.sjt3 -D data/lm
gh release download sj-sr-10-31m -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm
gh release download sj-cl-2-69k -R Nanako0129/shanjie -p colloquial.sjlm -p colloquial-attribution.tsv -D data/lm
```
