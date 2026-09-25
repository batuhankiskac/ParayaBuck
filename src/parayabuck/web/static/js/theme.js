// Applies the saved (or system) colour theme before first paint.
(function () {
  "use strict";
  var theme = null;
  try { theme = window.localStorage.getItem("pb-theme"); } catch (_) { theme = null; }
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.setAttribute("data-theme", theme);
})();
