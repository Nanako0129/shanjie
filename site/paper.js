// 論文頁的複製按鈕。按鈕由這支程式加上：沒有 JavaScript 時不會出現按了沒反應的按鈕，引用字串照樣在 <pre> 裡可以手動選取。
// 每個 <pre data-copy="按鈕的 aria-label"> 後面加一顆按鈕與一個 aria-live 的狀態字；複製的是 <pre> 的 textContent，和頁上顯示的字串完全相同。
document.addEventListener("DOMContentLoaded", function () {
  if (!navigator.clipboard) return;
  document.querySelectorAll("pre[data-copy]").forEach(function (pre) {
    var row = document.createElement("p"), button = document.createElement("button"), status = document.createElement("span");
    row.className = "copy";
    button.type = "button";
    button.className = "btn secondary";
    button.textContent = "複製";
    button.setAttribute("aria-label", pre.dataset.copy);
    status.setAttribute("aria-live", "polite");
    button.addEventListener("click", function () {
      navigator.clipboard.writeText(pre.textContent).then(
        function () { status.textContent = "已複製"; },
        function () { status.textContent = "複製失敗，請手動選取"; });
    });
    row.append(button, status);
    pre.after(row);
  });
});
