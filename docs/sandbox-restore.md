# 把學習資料從沙盒搬回來

給「已經升級到 App 沙盒版、又降回沒有沙盒的版本（0.4.x 以前）」的使用者。規格在 `docs/contracts/app-sandbox.md` §2.5。

## 為什麼需要

- 沙盒版第一次啟動時，系統把學習資料（`~/Library/Application Support/shanjie`，新詞也在裡面的 `learning.tsv`）與偏好設定（`~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist`）**搬**進 `~/Library/Containers/com.nyanako.inputmethod.shanjie`。原位置的已經不在了。
- 降回沒有沙盒的版本，它只看原位置，所以看到的是空的學習資料與預設設定。
- 退回期間學到的，留在原位置。再升級成沙盒版時，container 已經存在，系統不會再搬，那些資料就看起來不見了。
- 所以搬回之後要刪掉**整個** container：下次升級時系統才會重新搬一次，帶上搬回來的資料。
- 不做自動搬回。

## 怎麼降版

- 自動更新（Sparkle）不會降版，「關於」頁的「更新到 X」只會往新版走。
- 安裝程式也不降版：已經裝了比較新的版本時，它只啟用、不複製。
- 所以退回要用舊版的 zip（GitHub Releases 的 `shanjie-<版本>.zip`）解開後，以 `scripts/install-ime.sh` 安裝；或先刪掉 `~/Library/Input Methods/善解輸入法.app`，再用舊版的安裝程式。
- 裝好舊版之後，照下面的步驟搬回資料。

## 步驟

先把輸入法切到別的（例如 ABC），再在「終端機」貼上下面整段。它會：

1. 結束善解的程序（沙盒版與舊版都算）；
2. 原位置如果已經有資料（退回期間學到的），停下來，請你先決定怎麼處理，不覆蓋；
3. 用 `ditto` 把 container 裡的學習資料夾複製回原位置，逐檔比對 SHA-256；
4. 偏好設定用 `defaults export` 讀出 container 裡的那一份，再以**原位置 plist 的完整路徑**當網域 `defaults import` 寫回，從同一個路徑匯出一次比對內容，並確認原位置真的有那個檔。不能用 `com.nyanako.inputmethod.shanjie` 這個網域名稱寫回：container 還在的時候，`defaults` 會把這個網域導向 container 裡的那一份，寫進去的是 container，接著刪掉 container 時設定就一起不見（2026-10-20 在虛擬機量到）。不直接複製 plist 檔：系統的 cfprefsd 有快取，直接換掉的檔案可能被忽略（推論，沒量過）；
5. 兩樣都相同，才刪掉**整個** container；任何一步不同就停下來，container 留著。

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
pkill -U "$(id -u)" -f "^($HOME/Library/Input Methods/|/Library/Input Methods/)(善解輸入法|shanjie)\.app/Contents/MacOS/shanjie( |\$)" || true
sleep 1
for p in "$DST_AS" "$DST_PREFS"; do
  [ ! -e "$p" ] || { echo "原位置已經有 ${p}（退回期間產生的，或上一次執行到一半停下時留下的複本）。先把它移到別處備份，再執行一次。" >&2; exit 1; }
done
ls "$C/Data/Library" > /dev/null   # 讀不到（例如系統擋住存取）就在這裡停下，不會把「讀不到」當成「沒有東西要搬」
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
sums() { (cd "$1" && find . -type f -exec shasum -a 256 {} + | sort -k 2); }
if [ -d "$SRC_AS" ]; then
  ditto "$SRC_AS" "$DST_AS"
  [ "$(sums "$SRC_AS")" = "$(sums "$DST_AS")" ] || { echo "學習資料複製後不一致，container 保留。" >&2; exit 1; }
fi
if [ -f "$SRC_PREFS" ]; then
  defaults export "${SRC_PREFS%.plist}" "$T/from-container.plist"
  LINES="$(plutil -p "$T/from-container.plist" | wc -l)"   # 空的設定檔印成兩行 {、}
  if [ "$LINES" -gt 2 ]; then
    defaults import "${DST_PREFS%.plist}" "$T/from-container.plist"
    [ -f "$DST_PREFS" ] || { echo "偏好設定沒有寫到原位置，container 保留。" >&2; exit 1; }
    defaults export "${DST_PREFS%.plist}" "$T/written.plist"
    plutil -convert xml1 "$T/from-container.plist" "$T/written.plist"
    cmp -s "$T/from-container.plist" "$T/written.plist" || { echo "偏好設定寫回後不一致，container 保留。" >&2; exit 1; }
  fi
fi
rm -rf "$C"
echo "已搬回，container 已刪除。"
ls -la "$DST_AS" 2>/dev/null || true
defaults read "$ID" 2>/dev/null || true
EOF
```

## 搬回之後

- 再切回善解，學過的選字、新詞與排列設定應該都在。
- `defaults read` 印出的設定如果和沙盒版用的不同（例如排列不對），請登出再登入一次，再看一次；還是不對就開 issue。
- 之後再升級成沙盒版，系統會重新搬一次，帶上現在原位置的資料。

## 還沒量過的

契約 §4.2 第 3 項在虛擬機量這些；任何一項不成立（跳詢問、資料不同），就是那一片的停止條件：

- 終端機讀取或刪除 `~/Library/Containers` 底下的資料夾時，macOS 會不會跳「存取其他 App 的資料」的詢問。
- ~~`defaults export` 以 container 裡 plist 的路徑當網域讀得出內容，`defaults import` 寫回之後舊版讀得到。~~ 2026-10-20 量過：讀得出；但以網域名稱 `defaults import` 會寫進 container，刪掉 container 時設定一起不見（契約 §4.2 第 3 項第一次就是這樣失敗的）。改成以原位置 plist 的完整路徑寫回之後，原位置有檔，刪掉 container 後 `defaults read com.nyanako.inputmethod.shanjie layout` 讀得到 `eten`（0.4.x 本身讀到這個值沒有量，是推論：container 刪掉之後兩者讀的是同一個檔）。
