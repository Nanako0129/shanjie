# 契約：關於頁的模型列表（about-models）

狀態：READY（2026-10-21，plan-verifier 收尾審查）。修訂一：一份清單、雙向核對。修訂二（第二次 REVISE 後逐項處置）：名稱不由建置檢查，改由審查核對。修訂三（2026-10-27，`docs/contracts/sj-lm-7-pr2.md` §2 第 6 條）：SJ-LM-7 的最終檔，新增聊天連文表一列，共七列，名稱照新版。

## 0. 起因、要做到什麼、怎麼看到

- **起因**：常談的契約寫了「關於頁的模型列表多一列」，但關於頁本來沒有模型列表（v0.5.0 虛擬機驗收時發現）。維護者 2026-10-21 選擇題：加一個，跟 v0.5.0 一起出。
- **要做到**：設定的「關於」頁多一節「模型」，列出這一版 App 內附的每個模型。每列顯示中文名與對外名稱（`docs/releases/README.md` 的命名規則，例如「通語」配「SJ-LM-7-9.7M」）。列表跟著 App 內附的檔案走：打包的模型就是表上的模型，每列的雜湊等於打包的那個檔；換了模型而表的雜湊沒跟著改，建置要失敗。名稱和雜湊在同一列，換模型時一起改，名稱由審查核對（建置不檢查名稱，§2）。
- **怎麼看到**：維護者在虛擬機裝 dry run，打開「設定 → 關於」，看到「模型」一節與七列，名稱和 `docs/releases/README.md` 的表一致。外觀照實機一輪一輪調。

## 1. 現況

- 關於頁（`macos/Sources/Shanjie/SettingsWindow.swift` 的 `AboutSections`）只有三節：版本、更新、網站與授權一句。
- App 內附的模型（`scripts/build-app.sh` 第 218 行附近複製進 `Resources/`）：`bigram.sjlm`、`classes.sjc`、`kn.sjkn`、`tinylm.sjtl`、`trigram.sjt3`、`colloquial.sjlm`。每個檔建置前都對 `data/<檔名>.sha256` 核對。
- 對外名稱只寫在 `docs/releases/README.md` 的表裡（文件），App 不知道。

## 2. 設計

- **資料**：新增追蹤檔 `data/models.tsv`，每列 `檔名<TAB>SHA-256<TAB>中文名<TAB>對外名稱`，依「通語、辨類、承接、連文（書面）、連文（聊天）、會意、常談」的順序（修訂三，`sj-lm-7-pr2.md`：兩張連文表各一列，中文名都是連文，規模不同；名稱照 SJ-LM-7 那一組最終檔的實際大小）：

  | 檔名 | 中文名 | 對外名稱 |
  |---|---|---|
  | `bigram.sjlm` | 通語 | SJ-LM-7-9.7M |
  | `classes.sjc` | 辨類 | SJ-WC-6-512C |
  | `kn.sjkn` | 承接 | SJ-KN-2-397K |
  | `trigram.sjt3` | 連文 | SJ-CX-2-7.7M |
  | `trigram-chat.sjt3` | 連文 | SJ-CX-2-950K |
  | `tinylm.sjtl` | 會意 | SJ-SR-7-15M |
  | `colloquial.sjlm` | 常談 | SJ-CL-2-69K |

  實作時逐一對 `docs/releases/README.md` 的表核對名稱與版本；SHA-256 欄等於 `data/<檔名>.sha256`。
- **建置**：`scripts/build-app.sh` 在建置前檢查（和其他模型檢查放在一起；寫成可以單獨呼叫的檢查，見 §3）：
  - **一份清單**：要打包的模型就是 `data/models.tsv` 列的檔案。`build-app.sh` 複製模型那一行改成照 `models.tsv` 的檔名欄複製，不再另寫一串檔名；要加模型只能加一列。
  - 每一列的 SHA-256 欄等於 `data/<檔名>.sha256`，而且 `data/lm/<檔名>` 存在、雜湊相符。
  - 名稱不由建置檢查（修訂二：命名表同一列也留著舊版本的名稱，用字串找分不出新舊；表裡也沒有雜湊，綁不住）。雜湊和名稱在 `models.tsv` 的同一列：換模型一定要改那一列的雜湊，名稱跟著在同一行改，由 `/code-review` 與 verifier 對照 `docs/releases/README.md` 核對。
  - 不符就停，訊息寫哪一列、哪個檔。
  - 通過後把 `models.tsv` 複製進 `Resources/`。
  - `scripts/check-app.sh` 雙向核對 bundle：`Resources/` 裡每個模型檔（副檔名 `.sjlm`、`.sjc`、`.sjkn`、`.sjtl`、`.sjt3`）在 `models.tsv` 剛好一列，`models.tsv` 的每一列在 `Resources/` 都有檔案，而且雜湊等於那個檔的實際雜湊。原本 `check-app.sh` 另外寫死的模型檔名清單，改成讀 `models.tsv`。
- **App**：
  - `AboutSections` 在「善解輸入法」那一節之後加一節「模型」，每列是 `LabeledContent(中文名, value: 對外名稱)`。
  - 讀 `Bundle.main` 的 `models.tsv`，解析成純函式（列的順序照檔案）。檔案不在或讀不了時，整節不顯示（開發環境、測試）。
  - 不加說明文字，維持關於頁的「第一層簡潔」。
- 外觀用系統預設的 `Form` 樣式，不另訂尺寸；實機有意見再一輪一輪調。

## 3. 驗收

1. `data/models.tsv` 的七列名稱和 `docs/releases/README.md` 表上目前出貨的那一版一致（審查核對）；SHA-256 等於 `data/*.sha256`（建置檢查）。
2. 建置檢查（用一個 shell 測試跑，不需要真的建完整個 App；檢查寫成可以單獨呼叫的腳本）。各一個案例，前三個都要停：
   - 改壞某一列的雜湊（和 `data/<檔名>.sha256` 不符）；
   - `data/lm/` 的檔案和列的雜湊不符；
   - 某一列的檔案不在 `data/lm/`；
   - 照原樣：通過。
3. Swift 單元測試：解析函式照順序回傳七列；空檔、欄位數不對的列、檔案不存在各一個案例。
4. `check-app.sh` 的雙向核對（同一個 shell 測試，用假的 `Resources/` 資料夾）：多一個沒有列的模型檔會失敗；少一個有列的檔會失敗；雜湊不符會失敗；照原樣通過。另在 dry run 的 bundle 上 `SKIP_RUN=1` 跑一次，通過。
5. 實機（維護者，虛擬機裡的 dry run）：「設定 → 關於」看到「模型」一節與七列。
6. `/code-review` ＋ fresh verifier ＋ CI。

## 4. 發佈、預算、退回

- 和 v0.5.0 一起出；發版說明一句：「關於頁列出內附的模型與版本」。
- 預算：實作自己做或交給 executor 一次，修正最多一次；不需要雲端。
- 退回：revert 這個 PR（關於頁回到三節，`models.tsv` 不再打包）。

## 5. 停止條件、範圍外

- **停止條件**：`docs/releases/README.md` 的表和 `data/*.sha256` 對不上（先查是哪一邊錯，問維護者）；要動到模型檔本身或核心才做得到。
- **範圍外**：每個模型的授權說明（已經在 `Resources/LICENSES`）；顯示模型「是否載入成功」；英文介面的翻譯調整（照 App 現有的做法）。
