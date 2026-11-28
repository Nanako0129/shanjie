// 三段式色彩主題切換（跟隨系統／淺色／深色）。每一頁在 <head> 裡同步載入（不加 defer）：
// 頁面畫出來之前先依 localStorage("theme") 設定 <html data-theme>，避免載入瞬間閃爍。
// 沒記過或設為「跟隨系統」時移除 data-theme，由 CSS 的 prefers-color-scheme 決定外觀。
(function () {
  var KEY = "theme", root = document.documentElement;
  function readSaved() {
    try {
      var saved = localStorage.getItem(KEY);
      if (saved === "light" || saved === "dark") return saved;
    } catch (e) { /* 私密瀏覽或封鎖網站資料：照系統外觀 */ }
    return "system";
  }
  var initial = readSaved();
  if (initial === "light" || initial === "dark") {
    root.setAttribute("data-theme", initial);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var nav = document.querySelector(".site-nav .in");
    if (!nav) return;

    var switcher = document.createElement("span");
    switcher.className = "theme-switch";
    switcher.setAttribute("role", "group");
    switcher.setAttribute("aria-label", "色彩主題");

    var choices = [
      { value: "system", label: "跟隨系統" },
      { value: "light", label: "淺色" },
      { value: "dark", label: "深色" }
    ];
    var buttons = [];

    function syncPressed(active) {
      for (var i = 0; i < buttons.length; i++) {
        var btn = buttons[i];
        btn.setAttribute("aria-pressed", btn.getAttribute("data-theme-choice") === active ? "true" : "false");
      }
    }

    function applyChoice(val) {
      if (val === "light" || val === "dark") {
        root.setAttribute("data-theme", val);
        try { localStorage.setItem(KEY, val); } catch (e) { /* 存不了就只套用這一頁 */ }
        syncPressed(val);
      } else {
        root.removeAttribute("data-theme");
        try { localStorage.removeItem(KEY); } catch (e) { /* 忽略 */ }
        syncPressed("system");
      }
    }

    for (var i = 0; i < choices.length; i++) {
      var item = choices[i];
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "theme-opt";
      btn.setAttribute("data-theme-choice", item.value);
      btn.textContent = item.label;
      btn.addEventListener("click", (function (v) {
        return function () { applyChoice(v); };
      })(item.value));
      switcher.appendChild(btn);
      buttons.push(btn);
    }

    syncPressed(readSaved());

    // 切換器與下載鈕放進同一個靠右容器，桌機與手機都整齊對齊右端
    var dl = nav.querySelector(".dl"), end = document.createElement("span");
    end.className = "end";
    nav.insertBefore(end, dl);
    end.appendChild(switcher);
    if (dl) end.appendChild(dl);
  });
})();
