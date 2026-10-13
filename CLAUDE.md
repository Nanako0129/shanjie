# 給 AI 代理的導引

這個檔案只做導引，不放專案事實與流程。

| 要找的 | 讀這裡 |
|---|---|
| 怎麼貢獻、怎麼建置與測試 | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| 計畫、每一片的狀態與驗收 | [`docs/PLAN.md`](docs/PLAN.md) |
| 每一片的規格（契約） | [`docs/contracts/`](docs/contracts/) |
| 評測方法、方法上的教訓 | [`docs/methodology.md`](docs/methodology.md)、[`docs/verification.md`](docs/verification.md) |
| 實驗、決定與更正（時間序） | [`docs/research-log.md`](docs/research-log.md) |

維護者本機的工作方式放在 `.agent-local/CLAUDE.md`（不進版控，`.gitignore` 排除）。存在的話，讀完上面的文件後讀它；在連結的 worktree 裡，它在主 checkout（`git rev-parse --git-common-dir` 的上一層）。它補充本機的流程，不覆蓋公開文件裡的規格。repo 文件裡寫到「`CLAUDE.md` 的某一節」（例如「改動分級」「評測與資料」）時，指的是那一份。

追蹤的檔案不放私人路徑、主機、帳號、金鑰的位置或本機工具的細節；這些放 `.agent-local/`。
