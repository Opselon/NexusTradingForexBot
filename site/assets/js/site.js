/* Nexus Documentation Platform — site behavior.
   Zero dependencies. Theme toggle, mobile sidebar, search (prebuilt JSON index),
   code copy buttons, language select. */
(function () {
  "use strict";

  var doc = document;
  var root = doc.documentElement;

  /* ---------- Theme ---------- */
  var THEME_KEY = "nexus-docs-theme";
  function applyTheme(t) {
    root.classList.toggle("dark", t === "dark");
    try { localStorage.setItem(THEME_KEY, t); } catch (e) { /* private mode */ }
    var btn = doc.getElementById("theme-toggle");
    if (btn) btn.textContent = t === "dark" ? "☀️" : "🌙";
  }
  var stored = null;
  try { stored = localStorage.getItem(THEME_KEY); } catch (e) { /* noop */ }
  applyTheme(stored || (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  var themeBtn = doc.getElementById("theme-toggle");
  if (themeBtn) themeBtn.addEventListener("click", function () {
    applyTheme(root.classList.contains("dark") ? "light" : "dark");
  });

  /* ---------- Mobile sidebar ---------- */
  var sidebar = doc.getElementById("sidebar");
  var backdrop = doc.getElementById("sidebar-backdrop");
  function closeSidebar() {
    if (sidebar) sidebar.classList.remove("open");
    if (backdrop) backdrop.classList.remove("show");
  }
  var menuBtn = doc.getElementById("menu-toggle");
  if (menuBtn) menuBtn.addEventListener("click", function () {
    sidebar.classList.toggle("open");
    backdrop.classList.toggle("show", sidebar.classList.contains("open"));
  });
  if (backdrop) backdrop.addEventListener("click", closeSidebar);
  doc.addEventListener("keydown", function (e) { if (e.key === "Escape") closeSidebar(); });

  /* ---------- Language select ---------- */
  var langSel = doc.getElementById("lang-select");
  if (langSel) langSel.addEventListener("change", function () {
    window.location.href = langSel.value;
  });

  /* ---------- Code copy buttons ---------- */
  doc.querySelectorAll("pre").forEach(function (pre) {
    if (pre.closest(".no-copy")) return;
    var wrap = doc.createElement("div");
    wrap.className = "code-wrap";
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);
    var btn = doc.createElement("button");
    btn.type = "button";
    btn.className = "copy-btn";
    btn.textContent = "copy";
    btn.setAttribute("aria-label", "Copy code to clipboard");
    btn.addEventListener("click", function () {
      var text = pre.innerText;
      function done(ok) {
        btn.textContent = ok ? "copied!" : "error";
        setTimeout(function () { btn.textContent = "copy"; }, 1400);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
      } else {
        var ta = doc.createElement("textarea");
        ta.value = text; doc.body.appendChild(ta); ta.select();
        try { doc.execCommand("copy"); done(true); } catch (e) { done(false); }
        doc.body.removeChild(ta);
      }
    });
    wrap.appendChild(btn);
  });

  /* ---------- Search (prebuilt JSON index) ---------- */
  var input = doc.getElementById("search-input");
  var results = doc.getElementById("search-results");
  var INDEX = null;
  function loadIndex(cb) {
    if (INDEX) return cb(INDEX);
    var base = doc.body.getAttribute("data-site-base") || "/";
    var xhr = new XMLHttpRequest();
    xhr.open("GET", base + "search-index.json", true);
    xhr.onload = function () {
      try { INDEX = JSON.parse(xhr.responseText); cb(INDEX); }
      catch (e) { cb(null); }
    };
    xhr.onerror = function () { cb(null); };
    xhr.send();
  }
  function snippet(text, q) {
    var i = text.toLowerCase().indexOf(q.toLowerCase());
    if (i < 0) return text.slice(0, 120);
    var start = Math.max(0, i - 40);
    return (start > 0 ? "…" : "") + text.slice(start, i + 90) + "…";
  }
  function renderResults(items, q) {
    results.innerHTML = "";
    if (!items.length) {
      var empty = doc.createElement("div");
      empty.className = "sr-empty";
      empty.textContent = results.getAttribute("data-empty-text") || "No results.";
      results.appendChild(empty);
    } else {
      items.slice(0, 12).forEach(function (it) {
        var a = doc.createElement("a");
        a.className = "sr-item";
        a.href = it.url;
        var t = doc.createElement("div"); t.className = "sr-title"; t.textContent = it.title;
        var s = doc.createElement("div"); s.className = "sr-snippet"; s.textContent = snippet(it.text, q);
        a.appendChild(t); a.appendChild(s);
        results.appendChild(a);
      });
    }
    results.classList.add("open");
  }
  if (input && results) {
    input.addEventListener("input", function () {
      var q = input.value.trim();
      if (q.length < 2) { results.classList.remove("open"); return; }
      loadIndex(function (idx) {
        if (!idx) return;
        var ql = q.toLowerCase();
        var hits = idx.filter(function (it) {
          return (it.title && it.title.toLowerCase().indexOf(ql) >= 0) ||
                 (it.text && it.text.toLowerCase().indexOf(ql) >= 0) ||
                 (it.terms && it.terms.toLowerCase().indexOf(ql) >= 0);
        });
        renderResults(hits, q);
      });
    });
    doc.addEventListener("click", function (e) {
      if (!results.contains(e.target) && e.target !== input) results.classList.remove("open");
    });
    doc.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") { e.preventDefault(); input.focus(); }
    });
  }
})();
