# E-C 權重聯合調（探索；登記於跑之前）

`docs/iteration-plan.md` Phase 1 的 E-C。探索片：只用調參層選、開發層比較，不出貨，結論只寫方向；私人集合只記數字。這個分支不合併。

- **假設**：解碼權重（λ、KN 的 β、三元表下限）和融合門檻（α、τ、δ）一直是分開調的；一起調能在同樣的改壞率上界下多救一些列。
- **臂**（這個分支用環境變數覆寫，不設就是出貨值）：
  - 聊天：`SHANJIE_LAMBDA_CHAT` ∈ {0.3, 0.4, 0.5, 0.6, 0.7} × `SHANJIE_KN_BETA` ∈ {0.5, 0.75, 1.0}，共 15 格；出貨點是 (0.5, 1.0)。
  - 書面：`SHANJIE_LAMBDA_FORMAL` ∈ {0.5, 0.6, 0.7, 0.8, 0.9} × `SHANJIE_TRI_FLOOR` ∈ {0.3, 0.5, 0.7}，共 15 格；出貨點是 (0.7, 0.5)。
  - μ（口語計數的權重）在建模型時就決定，要重建計數，這次不調。
- **每一格**：出貨設定（`--context --packs acg --tinylm`）解碼並輸出小模型分數，再用上界規則（`select_weighted(cap_ub=0.005)`）在調參層選 α／τ／δ。
- **集合**：聊天調參 chatclause 調參期，開發 discordtune、lytune、chatjudge（已用過）；書面調參 cvtune-clean＋wikitune-clean，開發 wikijudge、wikijudge2、wikijudge3（退役）。不碰判讀集與保留集，chatjudge-2、wikijudge4 不讀。
- **指標**：每格的調參淨值（對 n-gram 第一名）；調參淨值最高的格在開發層對「出貨點＋同規則選的 α／τ／δ」的改對／改壞與精確 McNemar p，以及對 n-gram 的改壞率上界。
- **停止條件**：某一邊（聊天或書面）最好的格在調參層比出貨點同規則多不到 +10 列，那一邊就結案。
- **平台與預算**：全部在同一台 GCP C4D 上解碼與選值（`docs/methodology.md` 同一種 CPU 的規則），大集合按列切片；2 小時以內（約 1 美元）；Colab 0。
