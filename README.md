# 善解 shanjie

以「選字正確」為第一優先的開源 macOS 注音輸入法（開發中）。

- 計畫：`docs/PLAN.md`
- 目前階段：S3b（最小可安裝版，契約見 `docs/contracts/s3b.md`）
- 授權：程式碼 Apache-2.0（`LICENSE`）；資料見 `LICENSES/data.md`

## 安裝

需要 macOS 26（Tahoe）以上、Apple 晶片。下面兩種方式擇一；要換方式時，先刪掉 `~/Library/Input Methods/善解輸入法.app`。

**Homebrew**

```sh
brew install --cask nanako0129/tap/shanjie
"$HOME/Library/Input Methods/善解輸入法.app/Contents/MacOS/shanjie" install
```

第二行註冊並啟用輸入方式。如果它說輸入方式清單還沒載入，登出再登入後再執行一次。

**手動**

從 [Releases](https://github.com/Nanako0129/shanjie/releases) 下載 `shanjie-<版本>.zip`，用 Finder 解壓，然後在這個 repo 的根目錄執行：

```sh
scripts/install-ime.sh <解壓出來的 善解輸入法.app 路徑>
```

裝好後在選單列的輸入法選單選「善解輸入法」；標準／倚天鍵盤在它自己的選單裡切換。
