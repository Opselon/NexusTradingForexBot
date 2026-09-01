/* Nexus docs search — client-side over the generated JSON index.
   No external service; index ~ small enough to load once and filter. */
(function () {
  "use strict";
  var input = document.getElementById("doc-search");
  if (!input) return;

  var results = null;
  var INDEX = window.NEXUS_SEARCH || [];
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
    if (results) { results.remove(); results = null; }
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
    var hits = [];
    for (var i = 0; i < INDEX.length; i++) {
      var s = score(INDEX[i], q);
      if (s > 0) hits.push([s, INDEX[i]]);
    }
    hits.sort(function (a, b) { return b[0] - a[0]; });
    box.innerHTML = "";
    if (!hits.length) {
      box.innerHTML = "<div class='search-empty'>—</div>";
      return;
    }
    hits.slice(0, 8).forEach(function (pair) {
      var e = pair[1];
      var a = document.createElement("a");
      a.href = e.u;
      a.innerHTML = e.t + " <span class='hit-lang'>· " + e.l + "</span>";
      box.appendChild(a);
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
