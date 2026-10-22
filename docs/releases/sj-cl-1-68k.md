善解的常談模型 **SJ-CL-1-68K**（口語詞頻表，檔案 `colloquial.sjlm`，SJLM0001 格式；模型命名見 `docs/releases/README.md`）：聊天設定的語意校正多參考一項「這個說法在口語裡常不常見」。只有聊天設定（保守、標準、積極三種強度都用，各用自己的值）、搭配語意校正模型 [`sj-sr-6-14m`](https://github.com/Nanako0129/shanjie/releases/tag/sj-sr-6-14m)（`tinylm.sjtl`）時核心才會用它（`docs/contracts/colloquial-term.md`）；其他設定不載入也沒差，核心自己判斷。CI 與發布版從這裡下載。**這不是輸入法的發行版。**

- 檔案：`colloquial.sjlm`，4,680,680 bytes
- SHA-256：`2984eeb525a06b12c244b3c04b84641c9a67492486132ebf404198372b684c33`
- 內容：一般的二元詞頻表（SJLM0001，不用詞類、不用 KN），詞彙 68,055；折扣 0.75、剪枝 2。它不綁 `bigram.sjlm` 的雜湊（核心當成獨立的一張表讀）。
- 附件：
  - `colloquial-attribution.tsv`（2,000,233 bytes，SHA-256 `eb6fb69abf8d062b80479e20d2b1608381af2a6d51ba96435d62d3ca47015f22`）：口語池每個來源單位一列（YODAS 影片 id 與網址、Tatoeba 句子 id 與作者、WildChat 與 MASSIVE 的資料集出處與授權），附句數；各來源句數加總 WildChat 3,491、YODAS 90,080、Tatoeba 36,839、MASSIVE 13,485（合計 143,895）。不含任何句子文字。App 一起打包（`Resources/LICENSES/`）。
  - `sj-cl-1-68k.NOTICE.txt`（本 repo 的 `docs/releases/sj-cl-1-68k.NOTICE.txt`）：四個來源的名稱、授權與連結。
- 產生（P4 的表 T2，`experiments/p4-colloquial/README.md`）：model-v6 建置時的口語計數 `counts-colloquial3.pkl`（SHA-256 `1e7a15ef…`）加上 Phase 2 的公開口語池的計數，權重各 ×1：

  ```sh
  # 口語池的計數（和 model-v6 的口語計數同一套斷詞；acg-count.tsv、names-count.tsv 是 model-v6 建置時的兩份計數詞表，SHA-256 前綴 e8757303、5c14a359）
  python3 experiments/s2/build_counts_text.py --expected --extra-lexicon acg-count.tsv --extra-lexicon names-count.tsv counts-pool.pkl pool.tsv
  # 口語表（計數檔放在 $S2_WORK 底下；--corpora 一定要給 --out，不會蓋掉 data/lm/bigram.sjlm）
  python3 tools/build_lm.py --corpora counts-colloquial3.pkl:1 counts-pool.pkl:1 --out data/lm/colloquial.sjlm
  ```

  口語池 `pool.tsv`（143,895 句，約 300 萬個漢字）完整 SHA-256：`19fdc47430a63e24addf0ad925da090eca0ad0002656c454f757485aa00045da`；它由 Phase 2 的資料準備程式建（`docs/research-log.md` 2026-10-18「Phase 2 公開句子池」），已對公開評測集去重、對保留集比對過；池與計數檔不進 repo。這個 Release 的檔案是 P4 當時建的那一份，這一片沒有重建（只用上面的雜湊核對）。
- 量測：P4d（`experiments/p4d-colloquial/README.md`）在 chatjudge-2 確認：改對 108、改壞 39，淨值 +69，p 1.1×10⁻⁸，改壞÷改對 0.36。
- 授權：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/ ）。口語池的四個來源各自的授權都只要求署名（WildChat-1M：ODC-BY 1.0；YODAS：CC BY 3.0；Tatoeba：CC BY 2.0 FR；MASSIVE：CC BY 4.0），可以併進 BY-SA；model-v6 的口語計數本來就以 CC BY-SA 4.0 隨 model-v6 出貨。YODAS 資料集只給影片 id（查不到頻道名稱），所以照資料集提供的內容署名：資料集名稱、授權、每支影片的 id、網址與句數（`colloquial-attribution.tsv`）。完整的署名在 `sj-cl-1-68k.NOTICE.txt` 與 App 的 `Resources/LICENSES/CC-BY-SA-4.0-attribution.txt`。
- 不含任何私人對話資料；表裡是詞與詞對的次數，沒有句子文字。

下載：

```sh
gh release download sj-cl-1-68k -R Nanako0129/shanjie -p colloquial.sjlm -p colloquial-attribution.tsv -D data/lm
shasum -a 256 -c data/colloquial.sjlm.sha256 data/colloquial-attribution.tsv.sha256
```

建立這個 Release（維護者同意之後；附件三個，雜湊檔核對的是 `data/lm/` 的那兩個）：

```sh
shasum -a 256 -c data/colloquial.sjlm.sha256 data/colloquial-attribution.tsv.sha256   # 上傳前
gh release create sj-cl-1-68k data/lm/colloquial.sjlm data/lm/colloquial-attribution.tsv docs/releases/sj-cl-1-68k.NOTICE.txt \
  -R Nanako0129/shanjie --title "常談 SJ-CL-1-68K" --notes-file docs/releases/sj-cl-1-68k.md --latest=false
rm -rf /tmp/check-sj-cl-1-68k && gh release download sj-cl-1-68k -R Nanako0129/shanjie -p colloquial.sjlm -p colloquial-attribution.tsv -D /tmp/check-sj-cl-1-68k/data/lm \
  && (cd /tmp/check-sj-cl-1-68k && shasum -a 256 -c "$OLDPWD/data/colloquial.sjlm.sha256" "$OLDPWD/data/colloquial-attribution.tsv.sha256")   # 上傳後
```
