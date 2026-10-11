善解「語意校正」用的小型字元語言模型（SJTL0002 格式）。和 `tinylm-v1` 是同一個模型，差別只在融合參數：多了門檻 δ，參數改成 S7e 的嚴格值，目標是少把對的字改錯。語意校正預設關閉。**這不是輸入法的發行版。**

〔草稿，2026-10-12：這個 Release 還沒有建立；建立要使用者當下同意，`docs/releases/README.md`。〕

- 檔案：`tinylm.sjtl`，14,638,224 bytes
- SHA-256：`710249c2988d7cd08af6b85ea960d0b4e8a8c23eb8e8fd44264171c3628ed7db`
- 內容：和 `tinylm-v1` 相同的 1,440 萬參數字元語言模型（6 層、d 384、字表 9,714 字加 PAD、UNK、BOS 共 9,717 個 id；每列對稱 int8 加 float32 縮放，一維向量 float32）。只能在 n-gram 解碼出的前 8 名候選之間重排，不會產生新的文字。
- 融合參數（S7e，`docs/contracts/s7e-fewer-breaks.md`）：聊天 α 0.2、τ 1、δ 0.25；書面 α 0.7、τ 1、δ 2。δ 是門檻：融合分數最高的候選，要比 n-gram 第一名高出 δ 以上才換（`docs/contracts/s7c2c-delta.md`）。
- **綁定**：和 `tinylm-v1` 相同，檔案記了 model-v5 `bigram.sjlm` 的 SHA-256；換 n-gram 模型或詞類表都要重新調參、發新的檔。
- 產生：`experiments/s7a-tinylm/export_sjtl.py --npz tinylm-M.npz --out tinylm.sjtl --type int8 --ngram-model data/lm/bigram.sjlm --chat 0.2 1 0.25 --formal 0.7 1 2`，來源是 S7a 的 `tinylm-M.npz`（`32308122…`）。
- 量測（Rust 的 int8 路徑，對 n-gram 第一名；細節在研究紀錄 2026-10-12 的 S7e 與 S7c-2c 兩節）：公開集合 12 格和 S7e 的表逐格相同，例如聊天 dev302 241 → 249（改對 9／改壞 1）、cvtune 48／2、wikitune 81／0；使用者的聊天調參集（只記數字）聊天改對 70／改壞 10、書面 30／1。保留集（fresh verifier，2026-10-12）：聊天 180 → 187（改對 7／改壞 0，p = 0.0156），書面 184 → 187（3／0，p = 0.25）。
- 授權：**CC BY-SA 4.0**，訓練文字與 `tinylm-v1` 相同（中文維基百科、Common Voice、Tatoeba、gpt-oss-120b 合成句），不含任何私人對話資料。

下載：

```sh
gh release download tinylm-v2 -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm
echo "710249c2988d7cd08af6b85ea960d0b4e8a8c23eb8e8fd44264171c3628ed7db  data/lm/tinylm.sjtl" | shasum -a 256 -c
```

（`data/tinylm.sjtl.sha256` 在換成 v2 的那個 PR 才改成上面的雜湊；之前它記的是 `tinylm-v1`。）
