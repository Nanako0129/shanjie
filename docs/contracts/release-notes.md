# Release 說明進版控、每次手寫（2026-10-08）

## 0. 起因與目標

使用者 2026-10-08：「release notes 要進版控，然後每次的 release 都改自己寫」。現在 `release.yml` 建 App 版時用 `--generate-notes`（GitHub 依 PR 標題自動產生，英文），之後再到網頁手動改寫成中文；模型與詞類表的 Release 說明只存在 GitHub 上。同一天也發現資料類 Release 被 GitHub 自動標成 Latest（這個 PR 已加 `--latest`，見 §2）。

**目標**：每一個 Release 的說明都是 repo 裡的一個檔案，發版時直接用它；沒有檔案就不能發版。

## 1. 檔案

- 位置：`docs/releases/<tag>.md`，一個 Release 一個檔，檔名就是 tag（`v0.3.0.md`、`model-v3.md`、`classes-v1.md`）。
- 內容：Release 頁面的說明本文（Markdown），中文、手寫，風格照現有的中文說明。標題不在檔案裡（App 版的標題由 `release.yml` 給「善解 <版本>」；資料類 Release 的標題建立時另給）。
- **補齊現有的 9 個**：`v0.1.0`、`v0.1.1`、`v0.1.2`、`v0.2.0`、`v0.2.1`、`model-v1`、`model-v2`、`model-v3`、`classes-v1`，內容逐位元組照抄 GitHub 上現在的說明本文（`gh release view <tag> --json body -q .body`），不改寫。
- `docs/releases/README.md`：一段說明這個資料夾的規則（§3）。

## 2. `release.yml`

- **gate**（不碰任何 secret）：checkout 的 sparse 路徑加上 `docs/releases`；新步驟「Require the release notes」：
  - `push` 一個 `v*` tag 時，`docs/releases/$GITHUB_REF_NAME.md` 必須存在而且不是空的（去掉空白後仍有內容），否則以錯誤結束、訊息寫出要補哪個檔；
  - `workflow_dispatch`（main 上的試跑，不發佈）不檢查。
- **publish**：checkout 的 sparse 路徑加上 `docs/releases`；`gh release create` 把 `--generate-notes` 換成 `--notes-file "$GITHUB_WORKSPACE/docs/releases/$GITHUB_REF_NAME.md"`，保留 `--latest`。Release 已經存在、重跑這個 job 的路徑不變（不改說明）。
- 其他步驟不動。

## 3. 規則（寫進 repo 根目錄 `CLAUDE.md`「發版」與 `docs/releases/README.md`）

- 推 `v*` tag 之前，`docs/releases/<tag>.md` 要先寫好、經 PR 合併到 main；推 tag 仍要使用者當下同意。
- 資料類 Release（`model-v…`、`classes-v…`）照樣手動建，一律 `gh release create <tag> <檔案> --notes-file docs/releases/<tag>.md --latest=false`；說明檔同樣先進 main。
- 之後在網頁上改了說明，要同步回這個檔（以 repo 為準）。

## 4. 驗收

1. 9 個補齊的檔案：每個都和 `gh release view <tag> --json body -q .body` 的輸出逐位元組相同（比對時兩邊都去掉結尾換行）。
2. `release.yml` 能被 YAML 解析；gate 新步驟的 shell 片段抽出來在本機跑：檔案不存在 → 結束碼非 0 且訊息含檔名；只有空白 → 非 0；有內容 → 0；`workflow_dispatch` → 0（不檢查）。
3. `gh release create --help` 有 `--notes-file` 與 `--latest`（本機 gh 2.101.0）。
4. 文件一致：`CLAUDE.md`「發版」、`docs/releases/README.md` 說同一套規則；`rg -- '--generate-notes' .github` 沒有輸出。
5. 這片不推 tag、不建或改任何 Release；`release.yml` 的完整流程要等下一次推 `v*` tag（v0.3.0）才會真的跑到，屆時發版後確認 Release 說明等於檔案、標成 Latest。

## 5. 不在這一片

- 改寫舊的說明內容。
- 在一般 PR 的 CI 檢查說明檔（發版時的 gate 已足夠）。
- v0.3.0 的說明本身（v0.3.0 發版那片寫）。

## 6. 停止條件、限制

- **停止條件**：任何補齊的檔案和 GitHub 上的說明不一致而又無法照抄（例如本文含 GitHub 自動加的內容）——停下來報告，不猜。
- **限制**：main 自己做（改動小）；不推 tag、不跑 `release.yml`、不改 GitHub 上任何 Release。
