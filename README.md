# 善解輸入法（BopoMola）

以「選字正確」為第一優先的開源 macOS 注音輸入法（持續開發中）：每按一個鍵就在本機把整句重新選一次，停頓時再用小模型看前文，從前幾名裡挑最通順的一句。除了檢查更新不連網。

網站：https://shanjie.nyanako.com （介紹、安裝、使用說明、更新紀錄、常見問題）

- 怎麼選字與實測數字：https://shanjie.nyanako.com/how
- 每一版的變化、已合併還沒發版的改動、已知問題：https://shanjie.nyanako.com/updates ；發佈說明在 `docs/releases/`
- 現在的行為與規格：`docs/spec/`
- 授權：程式碼 Apache-2.0（`LICENSE`）；App 內含的 Sparkle（自動更新）是 MIT（`LICENSES/Sparkle-MIT.txt`，含它附帶的第三方授權）；資料見 `LICENSES/data.md`，各來源的整理在 https://shanjie.nyanako.com/licenses
- 想參與開發、回報選錯的字：見 [`CONTRIBUTING.md`](CONTRIBUTING.md)

## 安裝

需要 macOS 26（Tahoe）以上、Apple 晶片。下面的方式擇一，不要混用；完整步驟、更新、換安裝方式與解除安裝見 https://shanjie.nyanako.com/install 。

**安裝程式**（推薦）：從 [Releases 的 Latest](https://github.com/Nanako0129/shanjie/releases/latest) 下載 `shanjie-installer-<版本>.zip`，解壓後點兩下「安裝善解輸入法」。歡迎頁可選「這台 Mac」（每個帳號都能用，有程式開著安全輸入時照常能用，安裝與更新各要一次管理員密碼）或「只有我的帳號」（不需要密碼）。

**Homebrew**（只裝在你的帳號）：

```sh
brew install --cask nanako0129/tap/shanjie
"$HOME/Library/Input Methods/善解輸入法.app/Contents/MacOS/shanjie" install
```

第二行在系統還不認得善解時註冊並啟用輸入方式；結束碼是 3 時，到「系統設定 → 鍵盤 → 輸入方式」按「+」，選「繁體中文」裡的「善解輸入法」，按「加入」。

**手動**（只裝在你的帳號）：從 Releases 下載 `shanjie-<版本>.zip`，用 Finder 解壓，在這個 repo 的根目錄執行 `scripts/install-ime.sh <解壓出來的 善解輸入法.app 路徑>`。

裝好後在選單列的輸入法選單選「善解輸入法」；選單裡的「善解設定…」打開設定視窗。v0.4.7 起可以在設定的「關於」頁直接更新。

## 給唯音輸入法使用者

唯音輸入法（vChewing）可以用一份從善解的詞庫與模型轉出來的辭典：[`vchewing-dict-2`](https://github.com/Nanako0129/shanjie/releases/tag/vchewing-dict-2)，依唯音 5.1.0 的第三方建置規範建出（繁體字形沒有依唯音的正字規範校對，理由見 Release 說明），繁體、簡體模式都能用，需要唯音 5.1.0 以上（第一版 `vchewing-dict-1` 只有繁體欄，已撤下）。兩個專案合作的經過與唯音那邊的載入方式見 [issue #143](https://github.com/Nanako0129/shanjie/issues/143)。

## 贊助

善解是一個人利用空閒時間開發、免費開源的專案。贊助會用在模型訓練的雲端運算、測試與發版的基礎設施，以及持續改進選字的時間。如果善解對你有幫助，可以在 Patreon 支持它的開發。

[![在 Patreon 支持善解](https://img.shields.io/badge/Support_on_Patreon-FF424D?style=for-the-badge&logo=patreon&logoColor=white)](https://www.patreon.com/cw/Nanako0129/membership)
