# 給 AI 代理的導引

這個檔案只做導引，不放專案事實與流程。

| 要找的 | 讀這裡 |
|---|---|
| 怎麼貢獻、怎麼建置與測試 | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| 計畫、每一片的狀態與驗收 | [`docs/PLAN.md`](docs/PLAN.md) |
| 每一片的規格（契約） | [`docs/contracts/`](docs/contracts/) |
| 評測方法、方法上的教訓 | [`docs/methodology.md`](docs/methodology.md)、[`docs/verification.md`](docs/verification.md) |
| 實驗、決定與更正（時間序） | [`docs/research-log.md`](docs/research-log.md) |

維護者本機的工作方式放在 `.agent-local/CLAUDE.md`，由指向它的 `CLAUDE.local.md` 自動載入；兩者都不進版控（`.gitignore`）。公開的 clone 裡沒有這兩個檔，照上表的公開文件做事即可；repo 文件裡寫到「`CLAUDE.md` 的某一節」（例如「改動分級」「評測與資料」）時，指的是維護者本機那一份，公開文件裡的規格照舊適用。

新寫進追蹤檔案的內容不放私人路徑、主機、帳號或金鑰的位置（既有文件裡還有一些，之後另案整理）；這些放 `.agent-local/`。
