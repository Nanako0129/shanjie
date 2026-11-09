// 淺色／深色手動切換。每一頁在 <head> 裡同步載入（不加 defer）：先依記住的選擇設定 <html data-theme>，
// 頁面畫出來之前就定好，所以不會先閃一下錯的主題。沒記過、或讀不到 localStorage 時不設，CSS 照系統外觀（prefers-color-scheme）。
// 按鈕由這支程式插進導覽列：沒有 JavaScript 時不會出現一個按了沒反應的按鈕，網站照系統外觀顯示。
(function () {
  var KEY = "theme", root = document.documentElement;
  try {
    var saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);
  } catch (e) { /* 私密瀏覽或封鎖網站資料：照系統外觀 */ }
  var system = window.matchMedia("(prefers-color-scheme: dark)");
  function current() { return root.getAttribute("data-theme") || (system.matches ? "dark" : "light"); }
  function show(button) {
    var dark = current() === "dark";
    button.textContent = dark ? "☀︎" : "☾︎"; // ☀ 或 ☾，︎ 讓它用文字樣式而不是彩色 emoji
    button.setAttribute("aria-label", dark ? "切換成淺色主題" : "切換成深色主題");
    button.title = button.getAttribute("aria-label");
  }
  document.addEventListener("DOMContentLoaded", function () {
    var nav = document.querySelector(".site-nav .in");
    if (!nav) return;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "theme";
    button.addEventListener("click", function () {
      var next = current() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* 存不了就只換這一頁 */ }
      show(button);
    });
    system.addEventListener("change", function () { show(button); });
    show(button);
    // 切換鈕和下載鈕放進同一個固定在右端的容器：手機寬時導覽列橫捲，兩顆鈕都一直看得到
    var dl = nav.querySelector(".dl"), end = document.createElement("span");
    end.className = "end";
    nav.insertBefore(end, dl);
    end.append(button);
    if (dl) end.append(dl);
  });
})();
