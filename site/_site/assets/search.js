/* Nexus docs — flagship JS: nav, theme, copy, search, reveal, counters, chart, terminal, carousel, pipeline, progress, kbd */
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;

  /* base = depth-relative prefix inferred from assets/search.js path */
  var base = (function () {
    var m = "assets/search.js", scripts = doc.querySelectorAll("script[src]");
    for (var i = 0; i < scripts.length; i++) { var s = scripts[i].getAttribute("src") || ""; if (s.indexOf(m) !== -1) return s.slice(0, s.indexOf(m)); }
    return "";
  })();

  /* mobile nav + backdrop injected if missing */
  var toggle = doc.getElementById("nav-toggle"), sidebar = doc.getElementById("sidebar");
  var backdrop = doc.getElementById("sidebar-backdrop");
  if (!backdrop && sidebar) {
    backdrop = doc.createElement("div"); backdrop.id = "sidebar-backdrop"; backdrop.setAttribute("aria-hidden", "true");
    doc.body.insertBefore(backdrop, doc.body.firstChild);
  }
  function setNav(open) {
    doc.body.classList.toggle("nav-open", open);
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }
  if (toggle && sidebar) {
    toggle.addEventListener("click", function (e) { e.stopPropagation(); setNav(!doc.body.classList.contains("nav-open")); });
    backdrop && backdrop.addEventListener("click", function () { setNav(false); });
    doc.addEventListener("click", function (e) {
      if (doc.body.classList.contains("nav-open") && !sidebar.contains(e.target) && e.target !== toggle && !toggle.contains(e.target)) setNav(false);
    });
    doc.addEventListener("keydown", function (e) { if (e.key === "Escape" && doc.body.classList.contains("nav-open")) setNav(false); });
  }

  /* theme picker */
  var themePicker = doc.querySelector(".theme-picker");
  if (themePicker) {
    themePicker.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-theme-set]"); if (!btn) return;
      var pref = btn.getAttribute("data-theme-set");
      try { localStorage.setItem("nexus-theme", pref); } catch (err) {}
      var dark = pref === "dark" || (pref === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
      root.setAttribute("data-theme", dark ? "dark" : "light"); root.setAttribute("data-theme-pref", pref);
      themePicker.removeAttribute("open");
    });
  }

  /* copy buttons */
  var L = window.NEXUS_LOCALE || {};
  function tr(k, fb) { return (L && L[k]) || fb; }
  if (!doc.getElementById("copy-live") && doc.body) {
    var live = doc.createElement("span"); live.id = "copy-live"; live.className = "visually-hidden"; live.setAttribute("aria-live", "polite"); doc.body.appendChild(live);
  }
  doc.querySelectorAll(".copy-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var pre = btn.parentNode ? btn.parentNode.querySelector("pre") : null; if (!pre) return;
      var text = pre.innerText;
      var done = function (ok) {
        btn.textContent = ok ? "✓" : "✕"; btn.classList.add(ok ? "copied" : "copy-err");
        setTimeout(function () { btn.textContent = "⧉"; btn.classList.remove("copied", "copy-err"); }, 1400);
        var lv = doc.getElementById("copy-live"); if (lv) lv.textContent = ok ? tr("copied", "Copied") : tr("copy_error", "Copy failed");
      };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
      else done(false);
    });
  });

  /* reading progress */
  var prog = doc.getElementById("reading-progress");
  if (prog) {
    var onScroll = function () {
      var h = doc.documentElement;
      var max = h.scrollHeight - h.clientHeight;
      var pct = max > 0 ? (h.scrollTop / max) * 100 : 0;
      prog.style.width = pct + "%";
    };
    doc.addEventListener("scroll", onScroll, { passive: true }); onScroll();
  }

  /* reveal on scroll */
  var reveals = doc.querySelectorAll(".reveal");
  if (reveals.length && "IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { threshold: 0.14, rootMargin: "0px 0px -40px 0px" });
    reveals.forEach(function (el) { io.observe(el); });
  } else { reveals.forEach(function (el) { el.classList.add("in"); }); }

  /* counters */
  var counters = doc.querySelectorAll("[data-count]");
  if (counters.length) {
    var runCount = function (el) {
      var target = parseFloat(el.getAttribute("data-count")) || 0;
      var suffix = el.getAttribute("data-suffix") || "";
      var decimals = (el.getAttribute("data-decimals") | 0) || 0;
      var cur = 0, steps = 44, inc = target / steps, n = 0;
      var tick = function () {
        n++; cur += inc;
        if (n >= steps) { el.textContent = target.toFixed(decimals) + suffix; return; }
        el.textContent = cur.toFixed(decimals) + suffix;
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    };
    if ("IntersectionObserver" in window) {
      var cio = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { if (e.isIntersecting) { runCount(e.target); cio.unobserve(e.target); } });
      }, { threshold: 0.5 });
      counters.forEach(function (el) { cio.observe(el); });
    } else counters.forEach(runCount);
  }

  /* pipeline hover sync */
  var pipeNodes = doc.querySelectorAll(".pipe-node");
  pipeNodes.forEach(function (n) {
    n.addEventListener("mouseenter", function () { pipeNodes.forEach(function (x) { x.classList.toggle("is-active", x === n); }); });
    n.addEventListener("mouseleave", function () { pipeNodes.forEach(function (x) { x.classList.remove("is-active"); }); });
  });

  /* hero chart draw */
  var chartSvg = doc.getElementById("hero-chart-svg");
  if (chartSvg) {
    var W = 520, H = 190;
    /* simulated M1 candles as SVG path + filled bands */
    var pts = [], N = 64;
    var y = 95;
    for (var i = 0; i < N; i++) {
      y += (Math.sin(i * 0.55) * 9 + (Math.random() - 0.5) * 14);
      y = Math.max(28, Math.min(H - 28, y));
      pts.push({ x: (i / (N - 1)) * (W - 24) + 12, y: y });
    }
    var d = "M" + pts.map(function (p) { return p.x.toFixed(1) + "," + p.y.toFixed(1); }).join(" L");
    /* OB/FVG bands */
    var bands = [
      { x: 118, w: 62, label: "OB" },
      { x: 278, w: 54, label: "FVG" },
    ];
    var svg = '<rect width="100%" height="100%" fill="transparent"/>';
    bands.forEach(function (b) {
      svg += '<rect x="' + b.x + '" y="22" width="' + b.w + '" height="' + (H - 44) + '" rx="6" fill="currentColor" opacity="0.07"/>';
      svg += '<text x="' + (b.x + b.w / 2) + '" y="34" text-anchor="middle" font-size="8" font-weight="800" letter-spacing="0.08em" fill="currentColor" opacity="0.45">' + b.label + "</text>";
    });
    svg += '<path d="' + d + '" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" opacity="0.92"/>';
    /* entry/SL/TP markers */
    var ex = pts[Math.floor(N * 0.62)].x, ey = pts[Math.floor(N * 0.62)].y;
    svg += '<circle cx="' + ex + '" cy="' + ey + '" r="5" fill="#2563eb" stroke="white" stroke-width="2"/>';
    svg += '<line x1="' + ex + '" y1="' + (ey - 18) + '" x2="' + ex + '" y2="' + (ey + 18) + '" stroke="#2563eb" stroke-width="1.2" stroke-dasharray="4 4" opacity="0.55"/>';
    chartSvg.innerHTML = svg;
    chartSvg.style.color = getComputedStyle(root).getPropertyValue("--accent") || "#2563eb";
    /* live ticker numbers wiggle */
    var tickerEls = doc.querySelectorAll("[data-ticker]");
    if (tickerEls.length) {
      setInterval(function () {
        tickerEls.forEach(function (el) {
          var v = parseFloat(el.textContent.replace(/[^0-9.\-]/g, "")) || 2650;
          v += (Math.random() - 0.5) * 0.7;
          var dec = 2; el.textContent = v.toFixed(dec);
        });
      }, 1100);
    }
  }

  /* terminal typer */
  var termLines = doc.querySelectorAll(".terminal-body [data-type]");
  if (termLines.length) {
    var ti = 0;
    var typeNext = function () {
      if (ti >= termLines.length) return;
      var el = termLines[ti++]; var full = el.getAttribute("data-type") || ""; el.textContent = ""; el.classList.add("typed");
      var idx = 0;
      var step = function () {
        el.textContent = full.slice(0, idx++);
        if (idx <= full.length) setTimeout(step, 14 + Math.random() * 18);
        else { el.classList.remove("typed"); setTimeout(typeNext, 380); }
      };
      step();
    };
    if ("IntersectionObserver" in window) {
      var tIo = new IntersectionObserver(function (entries) { if (entries[0].isIntersecting) { typeNext(); tIo.disconnect(); } }, { threshold: 0.3 });
      tIo.observe(termLines[0].parentNode);
    } else typeNext();
  }

  /* shot carousel (optional) */
  var shotStage = doc.getElementById("shot-stage");
  if (shotStage) {
    var shots = JSON.parse(shotStage.getAttribute("data-shots") || "[]");
    var idx = 0;
    var img = shotStage.querySelector("img"), caption = doc.getElementById("shot-caption");
    var tabs = doc.querySelectorAll(".shot-tab");
    function showShot(i) {
      idx = (i + shots.length) % shots.length; var s = shots[idx];
      if (img) { img.src = s.src; img.alt = s.alt || ""; }
      if (caption) caption.textContent = s.cap || "";
      tabs.forEach(function (t, k) { t.classList.toggle("is-active", k === idx); });
    }
    tabs.forEach(function (t, k) { t.addEventListener("click", function () { showShot(k); }); });
    var prevBtn = doc.getElementById("shot-prev"), nextBtn = doc.getElementById("shot-next");
    if (prevBtn) prevBtn.addEventListener("click", function () { showShot(idx - 1); });
    if (nextBtn) nextBtn.addEventListener("click", function () { showShot(idx + 1); });
    if (shots.length) showShot(0);
  }

  /* faq accordion (progressive) */
  doc.querySelectorAll("[data-faq-q]").forEach(function (q) {
    q.addEventListener("click", function () {
      var a = q.nextElementSibling; if (!a) return;
      var open = a.style.display !== "none" && a.style.display !== "";
      a.style.display = open ? "none" : "block";
      q.setAttribute("aria-expanded", open ? "false" : "true");
    });
  });

  /* smooth anchor offset for sticky header */
  doc.querySelectorAll('a[href^="#"]').forEach(function (a) {
    a.addEventListener("click", function (e) {
      var id = a.getAttribute("href").slice(1); var t = id && doc.getElementById(id);
      if (!t) return; e.preventDefault();
      var top = t.getBoundingClientRect().top + window.scrollY - 72;
      window.scrollTo({ top: top, behavior: "smooth" }); history.pushState(null, "", "#" + id);
    });
  });

  /* ---------------- search (inline) + CmdK ---------------- */
  var input = doc.getElementById("doc-search"); if (!input) return;
  var results = null, INDEX = null, currentLang = root.lang || "en";
  function ensureContainer() {
    if (results) return results;
    results = doc.createElement("div"); results.className = "search-results"; results.setAttribute("role", "listbox"); results.setAttribute("aria-label", "search results");
    input.parentNode.appendChild(results); return results;
  }
  function closeResults() { if (results) { results.remove(); results = null; } }
  function loadIndex(cb) {
    if (INDEX) return cb(INDEX);
    fetch(base + "search-index.json").then(function (r) { return r.json(); }).then(function (d) { INDEX = d; cb(d); }).catch(function () { INDEX = []; cb(INDEX); });
  }
  function score(entry, q) {
    var t = (entry.t || "").toLowerCase(), x = (entry.x || "").toLowerCase(), s = 0;
    if (t.indexOf(q) !== -1) s += 10; if (x.indexOf(q) !== -1) s += 4;
    if (entry.l === currentLang) s += 2; var idx = x.indexOf(q); if (idx > -1 && idx < 60) s += 2; return s;
  }
  function renderInline(q) {
    var box = ensureContainer(); if (q.length < 2) { closeResults(); return; }
    loadIndex(function (IDX) {
      var hits = []; for (var i = 0; i < IDX.length; i++) { var s = score(IDX[i], q); if (s > 0) hits.push([s, IDX[i]]); }
      hits.sort(function (a, b) { return b[0] - a[0]; }); box.innerHTML = "";
      if (!hits.length) { box.innerHTML = "<div class='search-empty'>" + tr("no_results", "No results") + "</div>"; return; }
      hits.slice(0, 8).forEach(function (pair) {
        var e = pair[1], a = doc.createElement("a"); a.href = base + e.u.replace(/^\//, ""); a.innerHTML = e.t + " <span class='hit-lang'>· " + e.l + "</span>"; box.appendChild(a);
      });
    });
  }
  input.addEventListener("input", function () { renderInline(input.value.trim().toLowerCase()); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { closeResults(); input.blur(); }
    if (e.key === "Enter" && results) { var first = results.querySelector("a"); if (first) window.location.href = first.href; }
  });
  doc.addEventListener("click", function (e) { if (results && !results.contains(e.target) && e.target !== input) closeResults(); });

  /* CmdK palette */
  var cmdk = doc.getElementById("cmdk");
  if (cmdk) {
    var cmdkInput = cmdk.querySelector(".cmdk-input"), cmdkList = cmdk.querySelector(".cmdk-list");
    var cmdkHits = [];
    function openCmdk() {
      cmdk.classList.add("open"); cmdk.setAttribute("aria-hidden", "false");
      if (cmdkInput) { cmdkInput.value = ""; cmdkInput.focus(); if (cmdkList) cmdkList.innerHTML = "<div class='search-empty'>" + tr("search", "Search docs…") + "</div>"; }
      loadIndex(function () {});
    }
    function closeCmdk() { cmdk.classList.remove("open"); cmdk.setAttribute("aria-hidden", "true"); }
    function renderCmdk(q) {
      if (!cmdkList || !INDEX) return;
      if (q.length < 1) { cmdkList.innerHTML = "<div class='search-empty'>" + tr("search", "Search docs…") + "</div>"; return; }
      var hits = []; for (var i = 0; i < INDEX.length; i++) { var s = score(INDEX[i], q); if (s > 0) hits.push([s, INDEX[i]]); }
      hits.sort(function (a, b) { return b[0] - a[0]; }); cmdkHits = hits.slice(0, 8).map(function (p) { return p[1]; });
      if (!cmdkHits.length) { cmdkList.innerHTML = "<div class='search-empty'>" + tr("no_results", "No results") + "</div>"; return; }
      cmdkList.innerHTML = "";
      cmdkHits.forEach(function (e, idx) {
        var div = doc.createElement("div"); div.className = "cmdk-item" + (idx === 0 ? " is-active" : ""); div.setAttribute("role", "option");
        div.innerHTML = '<span><strong>' + e.t + '</strong> <span class="hit-lang">· ' + e.l + "</span></span><span class='chip'>↵</span>";
        div.addEventListener("click", function () { window.location.href = base + e.u.replace(/^\//, ""); });
        cmdkList.appendChild(div);
      });
    }
    doc.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); if (cmdk.classList.contains("open")) closeCmdk(); else openCmdk(); }
      if (e.key === "Escape" && cmdk.classList.contains("open")) closeCmdk();
    });
    if (cmdkInput) {
      cmdkInput.addEventListener("input", function () { renderCmdk(cmdkInput.value.trim().toLowerCase()); });
      cmdkInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && cmdkHits.length) { window.location.href = base + cmdkHits[0].u.replace(/^\//, ""); }
        if (e.key === "Escape") closeCmdk();
      });
    }
    cmdk.addEventListener("click", function (e) { if (e.target === cmdk) closeCmdk(); });
    var kbdBtn = doc.getElementById("cmdk-open");
    if (kbdBtn) kbdBtn.addEventListener("click", openCmdk);
  } else {
    doc.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); input.focus(); }
    });
  }
})();
