// Shared site behaviour: light/dark toggle + click-to-expand lightbox.

// --- light / dark toggle -------------------------------------------------
// Defaults to the OS preference; a click pins an explicit choice in
// localStorage and stamps data-theme on <html> (which wins over the media
// query, exactly like the reference artifact's shell does).
(function () {
  var root = document.documentElement;
  var KEY = "dd-theme";
  try {
    var saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);
  } catch (e) {}

  function current() {
    var attr = root.getAttribute("data-theme");
    if (attr) return attr;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark" : "light";
  }
  function label(btn) {
    if (btn) btn.textContent = current() === "dark" ? "☀ Light" : "☾ Dark";
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("themeBtn");
    label(btn);
    if (!btn) return;
    btn.addEventListener("click", function () {
      var next = current() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(KEY, next); } catch (e) {}
      label(btn);
    });
  });
})();

// --- lightbox ------------------------------------------------------------
// Any <img class="zoom"> expands on click. One overlay element, reused.
(function () {
  var box, imgEl;
  function ensure() {
    if (box) return;
    box = document.createElement("div");
    box.className = "lightbox";
    box.innerHTML = '<button class="lb-close" aria-label="Close">×</button><img alt="">';
    imgEl = box.querySelector("img");
    document.body.appendChild(box);
    box.addEventListener("click", close);
  }
  function open(src, alt) {
    ensure();
    imgEl.src = src; imgEl.alt = alt || "";
    box.classList.add("on");
    document.body.style.overflow = "hidden";
  }
  function close() {
    if (!box) return;
    box.classList.remove("on");
    imgEl.src = "";
    document.body.style.overflow = "";
  }
  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest("img.zoom");
    if (t) { e.preventDefault(); open(t.currentSrc || t.src, t.alt); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") close();
  });
})();
