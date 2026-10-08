# Release 說明

每一個 GitHub Release 的說明本文都放在這裡，一個 Release 一個檔，檔名就是 tag（`v0.3.0.md`、`model-v3.md`、`classes-v1.md`）。內容是中文、手寫，不用 GitHub 自動產生的說明。以這裡為準：在網頁上改了說明，要同步回這個檔。契約：`docs/contracts/release-notes.md`。

- **輸入法的版本（`v*`）**：推 tag 之前，`<tag>.md` 要先寫好、經 PR 合併到 main（推 tag 仍要維護者當下同意）。`release.yml` 的 gate 找不到這個檔、或檔案只有空白，就不發版（在等 CI 之前就檢查）；publish 用它當說明（`--notes-file`）。它是目前最大的版本號時標成 Latest；替舊版本線補發的修正版不搶 Latest。重跑 publish 時 Release 已經存在，說明和這個檔不同會出警告。
- **模型與詞類表（`model-v…`、`classes-v…`）**：手動建，說明檔同樣先進 main：

  ```sh
  gh release create <tag> <檔案> -R Nanako0129/shanjie --title "<標題>" --notes-file docs/releases/<tag>.md --latest=false
  ```

  資料類的 Release 一律不標 Latest，Releases 頁的 Latest 永遠是輸入法的最新版。
