# 把學習資料從沙盒搬回來

給「已經升級到 App 沙盒版、又降回沒有沙盒的版本（0.4.x 以前）」的使用者。規格在 `docs/contracts/app-sandbox.md` §2.5。

## 為什麼需要

- 沙盒版第一次啟動時，系統把學習資料（`~/Library/Application Support/shanjie`）與偏好設定（`~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist`）**搬**進 `~/Library/Containers/com.nyanako.inputmethod.shanjie`。原位置的已經不在了。
- 降回沒有沙盒的版本，它只看原位置，所以看到的是空的學習資料與預設設定。
- 退回期間學到的，留在原位置。再升級成沙盒版時，container 已經存在，系統不會再搬，那些資料就看起來不見了。
- 所以搬回之後要刪掉**整個** container：下次升級時系統才會重新搬一次，帶上搬回來的資料。
- 不做自動搬回。

## 步驟

先把輸入法切到別的（例如 ABC），再在「終端機」貼上下面整段。它會：

1. 結束善解的程序（沙盒版與舊版都算）；
2. 原位置如果已經有資料（退回期間學到的），停下來，請你先決定怎麼處理，不覆蓋；
3. 用 `ditto` 把 container 裡的學習資料與偏好設定複製回原位置；
4. 逐檔比對 SHA-256，相同才刪掉整個 container；不同就停下來，container 留著。

```bash
bash -s <<'EOF'
set -euo pipefail
ID=com.nyanako.inputmethod.shanjie
C="$HOME/Library/Containers/$ID"
SRC_AS="$C/Data/Library/Application Support/shanjie"
SRC_PREFS="$C/Data/Library/Preferences/$ID.plist"
DST_AS="$HOME/Library/Application Support/shanjie"
DST_PREFS="$HOME/Library/Preferences/$ID.plist"
[ -d "$C" ] || { echo "沒有 ${C}，不需要搬回。"; exit 0; }
pkill -f "^$HOME/Library/Input Methods/(善解輸入法|shanjie)\.app/Contents/MacOS/shanjie( |\$)" || true
sleep 1
for p in "$DST_AS" "$DST_PREFS"; do
  [ ! -e "$p" ] || { echo "原位置已經有 ${p}（退回期間產生的）。先把它移到別處備份，再執行一次。" >&2; exit 1; }
done
sums() { (cd "$1" && find . -type f -exec shasum -a 256 {} + | sort -k 2); }
if [ -d "$SRC_AS" ]; then
  ditto "$SRC_AS" "$DST_AS"
  [ "$(sums "$SRC_AS")" = "$(sums "$DST_AS")" ] || { echo "學習資料複製後不一致，container 保留。" >&2; exit 1; }
fi
if [ -f "$SRC_PREFS" ]; then
  ditto "$SRC_PREFS" "$DST_PREFS"
  cmp -s "$SRC_PREFS" "$DST_PREFS" || { echo "偏好設定複製後不一致，container 保留。" >&2; exit 1; }
fi
rm -rf "$C"
echo "已搬回，container 已刪除。"
ls -la "$DST_AS" 2>/dev/null || true
defaults read "$ID" 2>/dev/null || true
EOF
```

## 搬回之後

- 再切回善解，學過的選字與排列設定應該都在。
- `defaults read` 印出的設定如果和沙盒版用的不同（例如排列不對），請登出再登入一次，再看一次；還是不對就開 issue。偏好設定由系統的 cfprefsd 快取，直接換檔之後它看到的值有沒有立刻更新，還沒量過（契約 §4.2 第 3 項在虛擬機量）。
- 之後再升級成沙盒版，系統會重新搬一次，帶上現在原位置的資料。

## 還沒量過的

- 終端機刪除或讀取 `~/Library/Containers` 底下的資料夾時，macOS 會不會跳「存取其他 App 的資料」的詢問。契約 §4.2 第 3 項在虛擬機量；跳詢問就是那一片的停止條件。
