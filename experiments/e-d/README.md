# E-D 候選覆蓋探針（探索；登記於跑之前）

`docs/iteration-plan.md` Phase 1 的 E-D。探索片：只用調參層與開發層集合，不出貨，結論只寫方向。這次只做 `PER_KEY` 這一半；「前一詞的後繼詞當候選」另外登記。

- **假設**：LM 解碼器每個讀音只試詞庫前 12 名（`core::PER_KEY`）。正解的詞排在 12 名外時，整句就不會出現在 64 名內。放寬後 oracle@64 會變高。
- **臂**：`PER_KEY` 12（對照，等於 main）、24、48、1000。只有這個分支的 `core/src/lm.rs` 讀環境變數 `SHANJIE_PER_KEY`，不設時就是 12；這個分支不合併。
- **設定**：出貨設定 `--context --packs acg --tinylm`，兩種設定（聊天、書面）都跑；模型照 `data/*.sha256`。
- **集合**（調參層與開發層）：chatclause 調參期、cvtune-clean、wikitune-clean、discordtune、lytune、fw。不碰判讀集與保留集。
- **指標**：主指標是 oracle@64 對對照臂的增幅（百分點）；副指標是 top1 的改對、改壞與淨值（看放寬有沒有把第一名換壞），以及每列解碼時間。
- **停止條件**：聊天的兩個集合（chatclause 調參期、discordtune）oracle@64 的增幅都 < 0.5 個百分點，就結案、不往下做。
- **平台**：四臂全在同一種 CPU 上打分（GCP C4D），照 `docs/methodology.md`「2026-10-18：同一次比較的各臂，在同一種 CPU 上打分」。
- **預算**：GCP C4D 24 vCPU 2 小時以內（約 1 美元）；Windows 機器 0；Colab 0。
