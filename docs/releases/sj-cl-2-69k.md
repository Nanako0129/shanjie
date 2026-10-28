善解的常談模型 **SJ-CL-2-69K**（口語詞頻表，檔案 `colloquial.sjlm`，SJLM0001 格式；模型命名見 `docs/releases/README.md`），接替 [sj-cl-1-68k](https://github.com/Nanako0129/shanjie/releases/tag/sj-cl-1-68k)：聊天設定的語意校正多參考一項「這個說法在口語裡常不常見」。只有聊天設定、搭配會意 [sj-sr-7-15m](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-7-15m)（`tinylm.sjtl`）時核心才會用它（核心的 `PAIRED_TINYLM_SHA256` 是 sj-sr-7-15m 的雜湊，換了會意檔就不生效）；保守、標準、積極三種強度都使用，各用自己的值（`docs/contracts/colloquial-levels.md`）。用 [sj-lm-7-9.7m](https://github.com/Nanako0129/shanjie/releases/tag/sj-lm-7-9.7m) 的計數詞表重算，詞彙和上一版不同。CI 與發布版從這裡下載。**這不是輸入法的發行版。**

- 檔案：`colloquial.sjlm`，4,713,933 bytes（上一版 4,680,680）
- SHA-256：`81c2470d3956ee3a2c9377d7b8bc317739c1fe5b0c4793c29d6228a848fec8c4`
- 內容（讀檔頭）：一般的二元詞頻表（不用詞類、不用 KN），詞彙 69,457，二元組合 365,888 條，35,997 個前文；折扣 0.75，剪枝 2。它不綁 `bigram.sjlm` 的雜湊。
- 聊天三檔的融合值（α、τ、δ，`core/src/colloquial.rs`）：保守 0.7、2、1；標準 0.7、2、0；積極 1.0、2、0.1。在 24 格上用事先登記的規則選出（`docs/contracts/sj-lm-7.md` §2.9），用過濾後的 chatclause 重打一次，選值相同。
- 附件：
  - `colloquial-attribution.tsv`（2,000,233 bytes，SHA-256 `eb6fb69abf8d062b80479e20d2b1608381af2a6d51ba96435d62d3ca47015f22`）：和上一版同一個檔、沒有重建，因為口語池是同一份；各來源句數加總 WildChat 3,491、YODAS 90,080、Tatoeba 36,839、MASSIVE 13,485（合計 143,895），不含句子文字。App 一起打包（`Resources/LICENSES/`）。
  - `sj-cl-2-69k.NOTICE.txt`（`docs/releases/sj-cl-2-69k.NOTICE.txt`）：來源的名稱、授權與連結，來源和上一版相同。
- 產生（階段 4 的臂 C1，登記 `f34003e4`）：臂 B 計數詞表下的口語計數 `counts-colloquial3.pkl` ×1，加口語池 `pool.tsv`（143,895 句，約 300 萬漢字，SHA-256 `19fdc47430a63e24addf0ad925da090eca0ad0002656c454f757485aa00045da`，和上一版同一份）用同一組計數詞表重數 ×1：

  ```sh
  python3 experiments/s2/build_counts_text.py --expected --extra-lexicon <計數詞表> counts-pool.pkl pool.tsv
  python3 tools/build_lm.py --corpora counts-colloquial3.pkl:1 counts-pool.pkl:1 --prune 2 --out data/lm/colloquial.sjlm
  ```

  不含 g0v 與 PTT（臂 C2 加了它們，RSS 超過預算、保守檔 discordtune 顯著變差，沒有選）。
- 量測（階段 4，聊天，改對／改壞）：C1 對沿用上一版的 C0，discordtune 標準 4／0、保守 0／0、積極 3／0，其他集合每格都 ≤ 3；C1 對 main 標準檔 discordtune 4,366 → 4,558（229／37）、錯字回報聊天 +40；CLI 最高 RSS（Linux，只拿來比大小）聊天 296 MiB（main 271）。維護者選 C1。整組的結果見 [sj-sr-7-15m](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-7-15m) 的說明。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ），來源和上一版相同：(1) 口語計數（Mozilla Common Voice 繁體中文〔CC0〕、Tatoeba〔CC BY 2.0 FR，署名：Tatoeba contributors〕、為這個專案寫的合成句）；(2) 143,895 句的公開口語池，四個來源的授權都只要求署名，可以併進 BY-SA：WildChat-1M（ODC-BY 1.0）、YODAS（CC BY 3.0）、Tatoeba（CC BY 2.0 FR）、MASSIVE（CC BY 4.0）。YODAS 只給影片 id，所以照資料集提供的內容署名（id、網址、句數，見 `colloquial-attribution.tsv`）。完整署名在 NOTICE 與 App 的 `Resources/LICENSES/CC-BY-SA-4.0-attribution.txt`。
- 不含任何私人對話資料；表裡只有詞與詞對的次數。三檔的值在維護者自己的訊息建的評測集上選，檔案裡沒有任何訊息的文字。
- 配方與量測見研究紀錄 2026-10-26「階段 4 結果——常談 C0、C1、C2」。

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

建立這個 Release（維護者同意、草稿標記拿掉之後；附件三個，雜湊檔核對的是 `data/lm/` 的那兩個）：

```sh
shasum -a 256 -c data/colloquial.sjlm.sha256 data/colloquial-attribution.tsv.sha256   # 上傳前
gh release create sj-cl-2-69k data/lm/colloquial.sjlm data/lm/colloquial-attribution.tsv docs/releases/sj-cl-2-69k.NOTICE.txt \
  -R Nanako0129/shanjie --title "常談 SJ-CL-2-69K" --notes-file docs/releases/sj-cl-2-69k.md --latest=false
rm -rf /tmp/check-sj-cl-2-69k && gh release download sj-cl-2-69k -R Nanako0129/shanjie -p colloquial.sjlm -p colloquial-attribution.tsv -D /tmp/check-sj-cl-2-69k/data/lm \
  && (cd /tmp/check-sj-cl-2-69k && shasum -a 256 -c "$OLDPWD/data/colloquial.sjlm.sha256" "$OLDPWD/data/colloquial-attribution.tsv.sha256")   # 上傳後
```
