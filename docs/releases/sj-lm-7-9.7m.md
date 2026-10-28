〔草稿：待維護者確認後才建 Release〕
善解的通語模型 **SJ-LM-7-9.7M**（bigram 語言模型，檔案 `bigram.sjlm`，SJLM0001 格式；模型命名見 `docs/releases/README.md`），接替 [model-v6](https://github.com/Nanako0129/shanjie/releases/tag/model-v6)（通語 SJ-LM-6-9.1M）。**要和同一組的另外六個檔一起用**（見最後的下載）：辨類、承接、兩張連文表、會意都記了這個檔的 SHA-256，配到別的版本會載入失敗；常談由會意的雜湊配對。要讀兩張連文表、照每個設定的 KN 解碼，需要通語 SJ-LM-7 PR 2 之後的核心（`docs/contracts/sj-lm-7-pr2.md`）。CI 與發布版從這裡下載。**這不是輸入法的發行版。**

- 檔案：`bigram.sjlm`，88,467,426 bytes（比 model-v6 的 83,445,569 bytes 多 5,021,857）
- SHA-256：`69826c2f2b84431b0f5426f5057730913e0be508477b2fc681e5f50a3d18db5b`
- 內容（讀檔頭與表頭）：詞彙 396,927，二元組合 9,709,502 條，227,749 個前文，加權 unigram 總次數 617,862,688；折扣 D 0.75，剪枝門檻 6（加權次數 < 6 的二元組不留，前文總次數仍用剪枝前的）。
- 和 model-v6 的差別（`docs/contracts/sj-lm-7.md` §2）：
  - 計數詞表只用常開詞庫（小麥基底、萌典補詞、疊加層、變調列、名單），動漫與資訊詞包詞照臂 B 的做法仍進計數（衝突讀音照 model-v6 的規則排除 655 詞）；詞庫的讀音修正（簡編本、萌典、倒掛字）與詞包處置跟著這一版，在同一個 PR 的 `data/lexicon`、`data/packs`。
  - 繁體句不再套 OpenCC `TWPhrases`（`build_counts.py --no-trad-tw-phrases`）。
  - 口語語料加量：g0v `#rand0m`（×5）、PTT 推文（×5）、PTT 內文（×1）；剪枝門檻從 2 改 6（門檻 2 時約 220 MB，超過記憶體預算）。
- 產生：配方照 `experiments/model-v6/README.md`，新增來源的清理與計數指令在 `experiments/sj-lm-7/README.md`（分支 `exp/sj7-stage0c`、`exp/sj7-trigram`）。權重：維基 20 萬篇 ×1、口語池 ×5（輸入：`colloquial-train.txt` 用新轉換重產，`eb905936…`，86,802 句；合成句 `synth.txt`，`bec7a6a2…`；補次數檔，`315939d7…`）、g0v（`cedad812…`）×5、PTT 推文（`6568bc66…`）×5、PTT 內文（`f42d5c8b…`）×1。挑句用全收，沒有用 Moore–Lewis（那一臂 discordtune 沒有變好，維護者選全收）。
- 量測（階段 1，臂 B 剪枝 6，會意 SJ-SR-6-14M 重新綁到這個檔；對 main 的現行出貨；改對／改壞；研究紀錄 2026-10-26）：
  - discordtune 4,366 → 4,534（201／33）；cvtune-clean 3,002 → 3,011；打字測驗聊天／書面 72／69 → 73／72；錯字回報聊天／書面 58／51 → 95／99；wikitune-clean 書面 1,641 → 1,610。
  - dev302 書面改壞 6 列（出貨標準第 2 條的例外，維護者 2026-10-26 同意，`docs/contracts/sj-lm-7.md` §2.8）。
  - CLI 載入全部模型的最高 RSS（Linux，只拿來比大小）：聊天 271 → 282 MiB、書面 376 → 380 MiB。
  - 整組（六個單位、出貨標準四條）的結果在 [sj-sr-7-15m](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-7-15m) 的說明。
  - 已知的退步（只報）：資訊詞包檢查集聊天 46 → 36；書面 39 → 27（臂 B，紀錄沒有分出剪枝 6 的書面數字）。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。模型由下列語料的詞頻計數而來：
  - 中文維基百科 2026-10-01 dump 的 20 萬篇條目（CC BY-SA 4.0，署名：Wikipedia contributors）〔推論：階段 1 的紀錄沒另寫 dump 日期，沿用 model-v6 的快照〕
  - Mozilla Common Voice 繁體中文（台灣）的句子（CC0）
  - Tatoeba 的中文句子（CC BY 2.0 FR，署名：Tatoeba contributors）
  - 台灣口語合成句（gpt-oss-120b 的模型輸出；模型權重本身為 Apache-2.0），含補次數的部分
  - g0v Slack `#rand0m` 頻道的公開訊息（CC0）
  - PTT 批踢踢實業坊公開看板的文章與推文（只用於詞頻計數，不含原文）
  - 斷詞用的詞庫與詞包詞：小麥注音 McBopomofo（MIT）、中文維基百科與維基詞典（CC BY-SA 4.0）、Wikidata（CC0）、教育部《重編國語辭典修訂本》（經 g0v/moedict-data，萌典補詞與讀音）；《國語辭典簡編本》（CC BY-ND 3.0 臺灣）只用來決定讀音，原資料不在檔案內；名單層的來源與授權同 model-v6 的說明
- 不含任何私人對話資料。
- 〔TODO：這個檔是否含用來辨識來源的指紋條目，研究紀錄與契約只寫「定案的建置才加」，沒有寫最終檔有沒有加；建 Release 前由維護者確認，model-v6 的說明有寫「另含少量指紋條目」，有的話這裡要同樣標示。〕
- 〔TODO：延遲與記憶體（Mac 閒置窗，契約 `sj-lm-7-pr2.md` §4）、chatjudge-3 確認一發、保留集、fresh verifier 都還沒有結果。〕

下載（一整組）：

```sh
gh release download sj-lm-7-9.7m -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download sj-wc-6-512c -R Nanako0129/shanjie -p classes.sjc -D data/lm
gh release download sj-kn-2-397k -R Nanako0129/shanjie -p kn.sjkn -D data/lm
gh release download sj-cx-2-7.7m -R Nanako0129/shanjie -p trigram.sjt3 -D data/lm
gh release download sj-cx-2-950k -R Nanako0129/shanjie -p trigram-chat.sjt3 -D data/lm
gh release download sj-sr-7-15m -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm
gh release download sj-cl-2-69k -R Nanako0129/shanjie -p colloquial.sjlm -p colloquial-attribution.tsv -D data/lm
```
