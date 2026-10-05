# 善解 shanjie

以「選字正確」為第一優先的開源 macOS 注音輸入法（開發中）。

- 計畫：`docs/PLAN.md`
- 授權：程式碼 Apache-2.0（`LICENSE`）；資料見 `LICENSES/data.md`
- 想參與開發：見 [`CONTRIBUTING.md`](CONTRIBUTING.md)

## 進度

最新發佈是 [v0.1.2](https://github.com/Nanako0129/shanjie/releases)：可以安裝使用的注音輸入法，含標準與倚天鍵盤、整句轉換（詞庫＋bigram 語言模型）、標點候選、教育部「一／不／法」讀音變體。

**已合併、等下一版發佈**：
- 選字記憶：從候選窗的改選學習，可在選單清除；
- 標點候選旁顯示和蘋果相同的名稱；
- `[`、`]`、`\`、`'`、`=`、`` ` `` 不按 Shift 也打出標點（比照蘋果注音）。

**進行中**：自建的 Liquid Glass 候選窗。第一步是收合的一列，取代系統候選窗，讓名稱可以用小字；第二步是按 ↓ 展開成網格，加上展開動畫。契約在 `docs/contracts/s3b2-glass-panel.md`。

## 路線圖

| 階段 | 內容 | 狀態 |
|---|---|---|
| 詞庫與候選（S1、L） | 開放授權詞庫、精簡格式、候選生成 | 完成 |
| 語言模型（S2） | bigram 整句轉換、讀音變體 | 完成，持續修正 |
| 輸入法本體（S3） | InputMethodKit 殼、安裝、簽章與公證發佈、Homebrew | 完成；自建候選窗進行中 |
| 選字記憶（S4） | 依前文學習改選、可清除、隱私規則 | 已合併，待發佈 |
| 端上重排（S5） | 本機小模型從候選裡挑一句，不產生新文字 | 實驗中：「從清單挑一句」的題型不合格，改試逐候選打分數 |
| 雲端校正（S6） | 選用、預設關閉，只能從本機候選裡挑 | 尚未開始 |
| 自訓小模型（S7） | 研究中 | 尚未開始 |
| 設定頁與對外說明（S8） | 聊天 App 清單、更新檢查、隱私說明 | 尚未開始 |

詳細的計畫、驗收數字與每一片的取捨在 `docs/PLAN.md`；每一片實作前先寫的契約在 `docs/contracts/`；實驗紀錄在 `docs/research-log.md`。

## 安裝

需要 macOS 26（Tahoe）以上、Apple 晶片。下面兩種方式擇一；要換方式時，先刪掉 `~/Library/Input Methods/善解輸入法.app`。

**Homebrew**

```sh
brew install --cask nanako0129/tap/shanjie
"$HOME/Library/Input Methods/善解輸入法.app/Contents/MacOS/shanjie" install
```

第二行註冊並啟用輸入方式。第一次安裝通常要登出再登入，系統才會接受新的輸入法：第二行結束碼是 3 時，登出、再登入，然後再執行一次。

**手動**

從 [Releases](https://github.com/Nanako0129/shanjie/releases) 下載 `shanjie-<版本>.zip`，用 Finder 解壓，然後在這個 repo 的根目錄執行：

```sh
scripts/install-ime.sh <解壓出來的 善解輸入法.app 路徑>
```

也可以從 Releases 下載 `shanjie-installer-<版本>.zip`，解壓後點兩下「安裝善解輸入法」（測試中）。

裝好後在選單列的輸入法選單選「善解輸入法」；標準／倚天鍵盤在它自己的選單裡切換。
