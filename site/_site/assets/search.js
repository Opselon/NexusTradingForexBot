/* Nexus docs search + progressive enhancement (mobile nav).
   No external service: the index is fetched relative to the page so it works
   under any base path (project Pages subpath included).
   Core documentation remains fully readable without JavaScript. */
(function () {
  "use strict";

  var base = (function () {
    var marker = "assets/search.js";
    var scripts = document.querySelectorAll("script[src]");
    for (var i = 0; i < scripts.length; i++) {
      var src = scripts[i].getAttribute("src") || "";
      if (src.indexOf(marker) !== -1) return src.slice(0, src.indexOf(marker));
    }
    return "";
  })();

  /* ------------------------------ mobile nav ------------------------------ */
  var toggle = document.getElementById("nav-toggle");
  var sidebar = document.getElementById("sidebar");
  if (toggle && sidebar) {
    toggle.addEventListener("click", function () {
      var open = document.body.classList.toggle("nav-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("click", function (ev) {
      if (
        document.body.classList.contains("nav-open") &&
        !sidebar.contains(ev.target) &&
        ev.target !== toggle &&
        !toggle.contains(ev.target)
      ) {
        document.body.classList.remove("nav-open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && document.body.classList.contains("nav-open")) {
        document.body.classList.remove("nav-open");
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  }


  /* ------------------------------ theme menu ----------------------------- */
  var themePicker = document.querySelector(".theme-picker");
  if (themePicker) {
    themePicker.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-theme-set]");
      if (!btn) return;
      var pref = btn.getAttribute("data-theme-set");
      try { localStorage.setItem("nexus-theme", pref); } catch (e) {}
      var dark = pref === "dark" || (pref === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
      document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
      document.documentElement.setAttribute("data-theme-pref", pref);
      themePicker.removeAttribute("open");
    });
  }

  /* ----------------------------- copy code ------------------------------- */
  var L = window.NEXUS_LOCALE || {};
  function tr(key, fallback) { return (L && L[key]) || fallback; }
  document.querySelectorAll(".copy-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var pre = btn.parentNode ? btn.parentNode.querySelector("pre") : null;
      if (!pre) return;
      var text = pre.innerText;
      var done = function (ok) {
        btn.textContent = ok ? "✓" : "✕";
        btn.classList.add(ok ? "copied" : "copy-err");
        setTimeout(function () {
          btn.textContent = "⧉";
          btn.classList.remove("copied", "copy-err");
        }, 1400);
        var live = document.getElementById("copy-live");
        if (live) live.textContent = ok ? tr("copied", "Copied") : tr("copy_error", "Copy failed");
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
      } else { done(false); }
    });
  });
  // aria-live region once
  if (!document.getElementById("copy-live") && document.body) {
    var live = document.createElement("span");
    live.id = "copy-live"; live.className = "visually-hidden"; live.setAttribute("aria-live", "polite");
    document.body.appendChild(live);
  }

  /* -------------------------------- search -------------------------------- */
  var input = document.getElementById("doc-search");
  if (!input) return;

  var results = null;
  var INDEX = null;
  var currentLang = document.documentElement.lang || "en";

  function ensureContainer() {
    if (results) return results;
    results = document.createElement("div");
    results.className = "search-results";
    results.setAttribute("role", "listbox");
    results.setAttribute("aria-label", "search results");
    input.parentNode.appendChild(results);
    return results;
  }

  function close() {
    if (results) {
      results.remove();
      results = null;
    }
  }

  function loadIndex(cb) {
    if (INDEX) return cb(INDEX);
    fetch(base + "search-index.json")
      .then(function (r) { return r.json(); })
      .then(function (data) { INDEX = data; cb(data); })
      .catch(function () { INDEX = []; cb(INDEX); });
  }

  function score(entry, q) {
    var t = entry.t.toLowerCase();
    var x = entry.x.toLowerCase();
    var s = 0;
    if (t.indexOf(q) !== -1) s += 10;
    if (x.indexOf(q) !== -1) s += 4;
    if (entry.l === currentLang) s += 2;
    var idx = x.indexOf(q);
    if (idx > -1 && idx < 60) s += 2;
    return s;
  }

  function render(q) {
    var box = ensureContainer();
    if (q.length < 2) { close(); return; }
    loadIndex(function (INDEX) {
      var hits = [];
      for (var i = 0; i < INDEX.length; i++) {
        var s = score(INDEX[i], q);
        if (s > 0) hits.push([s, INDEX[i]]);
      }
      hits.sort(function (a, b) { return b[0] - a[0]; });
      box.innerHTML = "";
      if (!hits.length) {
        box.innerHTML = "<div class='search-empty'>" + tr("no_results", "—") + "</div>";
        return;
      }
      hits.slice(0, 8).forEach(function (pair) {
        var e = pair[1];
        var a = document.createElement("a");
        a.href = base + e.u.replace(/^\//, "");
        a.innerHTML = e.t + " <span class='hit-lang'>· " + e.l + "</span>";
        box.appendChild(a);
      });
    });
  }

  input.addEventListener("input", function () { render(input.value.trim().toLowerCase()); });
  input.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") { close(); input.blur(); }
    if (ev.key === "Enter" && results) {
      var first = results.querySelector("a");
      if (first) { window.location.href = first.href; }
    }
  });
  document.addEventListener("click", function (ev) {
    if (results && !results.contains(ev.target) && ev.target !== input) close();
  });
})();
