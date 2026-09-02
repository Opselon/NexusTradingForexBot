/* Nexus docs site — behavior: theme, search, mobile nav, language select. */
(function () {
  "use strict";

  /* ---------- theme ---------- */
  var root = document.documentElement;
  try {
    var saved = localStorage.getItem("nexus-theme");
    if (saved === "dark" || saved === "light") root.setAttribute("data-theme", saved);
    else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      root.setAttribute("data-theme", "dark");
    }
  } catch (e) { /* storage unavailable — default theme stays */ }

  var toggle = document.getElementById("theme-toggle");
  if (toggle) toggle.addEventListener("click", function () {
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("nexus-theme", next); } catch (e) {}
  });

  /* ---------- mobile nav ---------- */
  var navToggle = document.getElementById("nav-toggle");
  if (navToggle) navToggle.addEventListener("click", function () {
    var open = document.body.classList.toggle("nav-open");
    navToggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") document.body.classList.remove("nav-open");
  });

  /* ---------- language selector (act on change) ---------- */
  var langSel = document.getElementById("lang-select");
  if (langSel) langSel.addEventListener("change", function () {
    if (langSel.value) window.location.href = langSel.value;
  });

  /* ---------- search (client-side, fetched once, cached) ---------- */
  var input = document.getElementById("search-input");
  var box = document.getElementById("search-results");
  if (!input || !box) return;

  var DATA = null;
  varFlatten: {
    // placeholder label — real logic below
  }
  function load() {
    if (DATA) return Promise.resolve(DATA);
    var base = (document.querySelector('link[rel="stylesheet"]') || {}).href || "";
    var m = base.match(/^(.*\/)assets\//);
    var prefix = m ? m[1] : "/";
    return fetch(prefix + "assets/search.json")
      .then(function (r) { return r.json(); })
      .then(function (j) { DATA = j; return DATA; })
      .catch(function () { DATA = []; return DATA; });
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function snippet(text, q) {
    var idx = text.toLowerCase().indexOf(q.toLowerCase());
    if (idx < 0) idx = 0;
    var start = Math.max(0, idx - 60);
    var raw = text.slice(start, start + 160);
    return (start > 0 ? "…" : "") + esc(raw) + (start + 160 < text.length ? "…" : "");
  }

  var timer = null;
  input.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(run, 120);
  });
  input.addEventListener("focus", function () { if (input.value.trim()) run(); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && document.activeElement !== input &&
        !/^(input|textarea|select)$/i.test((document.activeElement || {}).tagName || "")) {
      e.preventDefault(); input.focus();
    }
    if (e.key === "Escape") { box.hidden = true; }
  });
  document.addEventListener("click", function (e) {
    if (!box.hidden && !box.contains(e.target) && e.target !== input) box.hidden = true;
  });

  function run() {
    var q = input.value.trim().toLowerCase();
    if (q.length < 2) { box.hidden = true; return; }
    load().then(function (data) {
      var scored = [];
      for (var i = 0; i < data.length; i++) {
        var d = data[i];
        var t = (d.t || "").toLowerCase();
        var x = (d.x || "").toLowerCase();
        var score = -1;
        if (t.indexOf(q) === 0) score = 100;
        else if (t.indexOf(q) >= 0) score = 70;
        else if (x.indexOf(q) >= 0) score = 40;
        if (score >= 0) scored.push({ d: d, s: score, pos: x.indexOf(q) });
      }
      scored.sort(function (a, b) { return b.s - a.s || a.pos - b.pos; });
      var top = scored.slice(0, 12);
      var htmlOut = "";
      if (!top.length) {
        htmlOut = '<div class="sr-empty">No results for “' + esc(q) + '”</div>';
      } else {
        for (var j = 0; j < top.length; j++) {
          var item = top[j].d;
          htmlOut += '<a class="sr-item" href="' + esc(item.u) + '">'
            + '<span class="sr-title">' + esc(item.t) + '</span>'
            + '<span class="sr-lang">' + esc(item.l) + '</span>'
            + '<span class="sr-snip">' + snippet(item.x || "", q) + '</span></a>';
        }
      }
      box.innerHTML = htmlOut;
      box.hidden = false;
    });
  }
})();
