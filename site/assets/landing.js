/* Nexus landing — cinematic interactions. Buildless vanilla JS, loaded only
   on body.landing-page. Degrades gracefully: WebGL optional, reduced-motion
   honored, all live data has N/A fallback and never blocks render.

   Truth rules enforced here:
   * No fabricated numbers. Live fields start "N/A" and only change from
     generated metadata (data/project-status.json) or the public GitHub API.
   * The market canvas is deterministic (seeded PRNG) and labelled DEMO. */
(function () {
  "use strict";
  var doc = document;
  if (!doc.body || doc.body.className.indexOf("landing-page") < 0) return;
  var root = doc.documentElement;
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // Depth-relative base of this page, derived from this script's own src
  // ("../assets/landing.js" on /fa/, "assets/landing.js" at the EN root).
  var self = doc.currentScript;
  var base = self && self.src ? self.src.replace(/assets\/landing\.js.*$/, "") : "";

  function prefersDark() {
    var t = root.getAttribute("data-theme");
    if (t === "dark") return true;
    if (t === "light") return false;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  /* ---------- reveal on scroll (landing sections) ---------- */
  var reveals = doc.querySelectorAll(".land-sec, .land-hero");
  if (reveals.length && "IntersectionObserver" in window && !reduced) {
    var rio = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); rio.unobserve(e.target); } });
    }, { threshold: 0.1, rootMargin: "0px 0px -40px 0px" });
    reveals.forEach(function (el) { rio.observe(el); });
  } else {
    reveals.forEach(function (el) { el.classList.add("in"); });
  }

  /* ---------- section-nav scroll spy ---------- */
  var navLinks = Array.prototype.slice.call(doc.querySelectorAll(".land-nav a[href^='#']"));
  if (navLinks.length && "IntersectionObserver" in window) {
    var byId = {};
    navLinks.forEach(function (l) { byId[l.getAttribute("href").slice(1)] = l; });
    var spy = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        var link = byId[e.target.id];
        if (!link) return;
        if (e.isIntersecting) {
          navLinks.forEach(function (l) { l.removeAttribute("aria-current"); });
          link.setAttribute("aria-current", "true");
        }
      });
    }, { rootMargin: "-30% 0px -60% 0px" });
    Object.keys(byId).forEach(function (id) {
      var sec = doc.getElementById(id);
      if (sec) spy.observe(sec);
    });
  }

  /* ---------- seeded PRNG (deterministic demo data) ---------- */
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* ---------- hero 3D core: lightweight WebGL (no library) ---------- */
  var coreCanvas = doc.getElementById("core-canvas");
  var coreStage = doc.getElementById("nexus-core");
  function webglCore() {
    var gl = null;
    try { gl = coreCanvas.getContext("webgl", { antialias: true, alpha: true, powerPreference: "low-power" }); } catch (e) {}
    if (!gl) { return false; }
    var vs =
      "attribute vec3 p; uniform float t; uniform float s;" +
      "void main(){ vec3 q=p; float a=t*0.3+q.y*1.7;" +
      "  q.xz = mat2(cos(a),-sin(a),sin(a),cos(a))*q.xz;" +
      "  q *= 1.0 + 0.06*sin(t*1.4 + q.y*6.0);" +
      "  gl_Position = vec4(q*s, 0.0, 1.0); gl_PointSize = 2.6; }";
    var fs =
      "precision mediump float; uniform vec3 c; uniform float o;" +
      "void main(){ float d = length(gl_PointCoord-0.5); if(d>0.5) discard;" +
      "  gl_FragColor = vec4(c, o*(1.0-d*2.0)); }";
    function sh(type, src) {
      var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error("shader");
      return s;
    }
    var prog;
    try {
      prog = gl.createProgram();
      gl.attachShader(prog, sh(gl.VERTEX_SHADER, vs));
      gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, fs));
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error("link");
    } catch (e) { return false; }
    gl.useProgram(prog);
    // Fibonacci-sphere points + two orbital rings — a stylized engine core
    var pts = [], N = 1400, GA = Math.PI * (3 - Math.sqrt(5));
    for (var i = 0; i < N; i++) {
      var y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = GA * i;
      pts.push(Math.cos(th) * r, y, Math.sin(th) * r);
    }
    function ring(rad, tilt, count) {
      for (var k = 0; k < count; k++) {
        var a = (k / count) * Math.PI * 2;
        var x = Math.cos(a) * rad, z = Math.sin(a) * rad, yy = 0;
        yy = z * Math.sin(tilt); z = z * Math.cos(tilt);
        pts.push(x, yy, z);
      }
    }
    ring(1.28, 0.5, 420); ring(1.52, -0.7, 420);
    var data = new Float32Array(pts);
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(prog, "p");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 0, 0);
    var uT = gl.getUniformLocation(prog, "t"), uS = gl.getUniformLocation(prog, "s"),
        uC = gl.getUniformLocation(prog, "c"), uO = gl.getUniformLocation(prog, "o");
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
    var dpr = Math.min(window.devicePixelRatio || 1, 1.75);
    function resize() {
      var w = coreStage.clientWidth, h = coreStage.clientHeight;
      coreCanvas.width = Math.max(2, w * dpr); coreCanvas.height = Math.max(2, h * dpr);
      gl.viewport(0, 0, coreCanvas.width, coreCanvas.height);
    }
    resize();
    if (window.ResizeObserver) new ResizeObserver(resize).observe(coreStage);
    else window.addEventListener("resize", resize);
    gl.clearColor(0, 0, 0, 0);
    var t0 = performance.now(), raf = 0, visible = true;
    function frame(now) {
      if (!visible) return;
      gl.clear(gl.COLOR_BUFFER_BIT);
      var t = (now - t0) / 1000;
      gl.uniform1f(uT, t); gl.uniform1f(uS, 0.82);
      var dark = prefersDark();
      function layer(cx, cy, cz, o) {
        gl.uniform3f(uC, cx, cy, cz); gl.uniform1f(uO, o);
        gl.drawArrays(gl.POINTS, 0, data.length / 3);
      }
      if (dark) {
        layer(0.13, 0.83, 0.93, 0.5); // cyan
        layer(0.20, 0.90, 0.60, 0.16); // teal halo
      } else {
        layer(0.10, 0.55, 0.65, 0.55);
        layer(0.16, 0.50, 0.35, 0.2);
      }
      if (!reduced) raf = requestAnimationFrame(frame);
    }
    function start() {
      if (reduced) { raf = 0; frame(performance.now() + 600); return; }
      if (!raf) raf = requestAnimationFrame(frame);
    }
    // pause when off-screen / tab hidden (battery)
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (es) {
        visible = es[0].isIntersecting;
        if (visible) start(); else if (raf) { cancelAnimationFrame(raf); raf = 0; }
      }, { threshold: 0 }).observe(coreStage);
    }
    doc.addEventListener("visibilitychange", function () {
      if (doc.hidden && raf) { cancelAnimationFrame(raf); raf = 0; }
      else if (!doc.hidden && visible) start();
    });
    root.setAttribute("data-webgl", "on");
    start();
    return true;
  }
  var lowPower = (navigator.hardwareConcurrency || 8) <= 4 || (navigator.deviceMemory || 8) <= 2 ||
    window.innerWidth < 640;
  if (coreCanvas && coreStage && !reduced && !lowPower) {
    // try WebGL; CSS fallback rings remain underneath regardless
    try { webglCore(); } catch (e) { /* fallback stays visible */ }
  } else if (reduced || lowPower) {
    if (reduced) {
      // still freeze one WebGL frame when possible, else pure CSS fallback
      try { if (!webglCore()) { /* keep CSS rings */ } } catch (e) {}
    }
  }

  /* ---------- XAUUSD DEMO market canvas (2D, deterministic) ---------- */
  var demo = doc.getElementById("demo-canvas");
  function drawDemo() {
    if (!demo || !demo.getContext) return;
    var W = demo.width, H = demo.height, dpr = Math.min(window.devicePixelRatio || 1, 2);
    demo.width = W * dpr; demo.height = H * dpr;
    var c = demo.getContext("2d");
    c.scale(dpr, dpr);
    demo.style.width = "100%"; demo.style.height = "auto";
    var rnd = mulberry32(9012); // fixed seed => identical every load
    var n = 64, pad = 26, cw = (W - pad * 2) / n;
    var y = H * 0.52, hi = -1e9, lo = 1e9, candles = [];
    for (var i = 0; i < n; i++) {
      var drift = Math.sin(i * 0.16) * 7 + (rnd() - 0.5) * 16;
      var o = y, cl = y - drift;
      var hiw = Math.max(o, cl) + rnd() * 6, low = Math.min(o, cl) - rnd() * 6;
      candles.push({ o: o, c: cl, h: hiw, l: low });
      y = cl;
      hi = Math.max(hi, hiw); lo = Math.min(lo, low);
    }
    function Y(v) { return pad + (1 - (v - lo) / (hi - lo)) * (H - pad * 2); }
    var cyan = "#22d3ee", green = "#34d399", red = "#fb7185", gold = "#fbbf24";
    c.clearRect(0, 0, W, H);
    // liquidity zones + order block + FVG (illustrative SMC/ICT overlay)
    function band(x0, x1, lo2, hi2, col, label) {
      c.fillStyle = col; c.fillRect(x0, Y(hi2), (x1 - x0) * cw, Y(lo2) - Y(hi2));
      c.strokeStyle = col; c.setLineDash([4, 5]); c.lineWidth = 1;
      c.strokeRect(x0, Y(hi2), (x1 - x0) * cw, Y(lo2) - Y(hi2)); c.setLineDash([]);
      c.fillStyle = "#c9d7ef"; c.font = "700 10px ui-monospace, monospace";
      c.fillText(label, x0 + 4, Y(hi2) - 4);
    }
    band(118 * 0 + 10 * cw, 17 * cw, lo + (hi - lo) * 0.16, lo + (hi - lo) * 0.3, "rgba(52,211,153,.10)", "ORDER BLOCK");
    band(30 * cw, 35 * cw, lo + (hi - lo) * 0.62, lo + (hi - lo) * 0.76, "rgba(34,211,238,.10)", "FVG");
    band(2 * cw, 8 * cw, hi - (hi - lo) * 0.06, hi, "rgba(251,191,36,.08)", "LIQUIDITY SWEEP");
    // candles
    for (var k2 = 0; k2 < n; k2++) {
      var d = candles[k2], x = pad + k2 * cw + cw / 2, up = d.c <= d.o, col = up ? green : red;
      c.strokeStyle = col; c.fillStyle = col; c.globalAlpha = 0.9; c.lineWidth = 1;
      c.beginPath(); c.moveTo(x, Y(d.h)); c.lineTo(x, Y(d.l)); c.stroke();
      var bw = Math.max(2.4, cw * 0.55);
      c.fillRect(x - bw / 2, Y(Math.max(d.o, d.c)), bw, Math.max(2, Math.abs(Y(d.o) - Y(d.c))));
    }
    c.globalAlpha = 1;
    // entry / SL / TP envelope on a synthetic long (labelled DEMO above)
    var ex = 40;
    function line(yv, col, label) {
      c.strokeStyle = col; c.lineWidth = 1.2; c.setLineDash([6, 5]);
      c.beginPath(); c.moveTo(ex * cw, Y(yv)); c.lineTo(W - pad, Y(yv)); c.stroke(); c.setLineDash([]);
      c.fillStyle = col; c.font = "700 10px ui-monospace, monospace";
      c.fillText(label, W - pad - 52, Y(yv) - 4);
    }
    var ev = candles[ex].c;
    line(ev, cyan, "ENTRY");
    line(ev - (hi - lo) * 0.09, red, "SL");
    line(ev + (hi - lo) * 0.2, green, "TP");
    c.fillStyle = cyan; c.beginPath(); c.arc(pad + ex * cw + cw / 2, Y(ev), 4, 0, 7); c.fill();
  }
  if (demo) {
    if ("IntersectionObserver" in window && !reduced) {
      var dio = new IntersectionObserver(function (es) {
        if (es[0].isIntersecting) { drawDemo(); dio.disconnect(); }
      }, { rootMargin: "240px" });
      dio.observe(demo);
    } else { drawDemo(); }
  }

  /* ---------- three modes tabs ---------- */
  var tabs = doc.querySelectorAll(".mode-tab");
  function showMode(key) {
    tabs.forEach(function (t) {
      var on = t.getAttribute("data-mode") === key;
      t.classList.toggle("is-active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
    });
    doc.querySelectorAll(".mode-panel").forEach(function (p) {
      p.classList.toggle("is-active", p.className.indexOf("m-" + key) >= 0);
    });
  }
  tabs.forEach(function (t) {
    t.addEventListener("click", function () { showMode(t.getAttribute("data-mode")); });
  });

  /* ---------- project pulse: build-time JSON + GitHub refresh, N/A-safe ---------- */
  var STATUS_URL = base + "data/project-status.json";
  var SYNC_TTL_MS = 6 * 3600 * 1000;
  function field(name) { return doc.querySelector("[data-pulse-field='" + name + "']"); }
  function setText(name, v) { var el = field(name); if (el && v) el.textContent = String(v); }
  function fmtDate(iso) { return (iso || "").slice(0, 10) || "N/A"; }
  function render(data) {
    if (!data) return;
    setText("version", data.version ? "v" + data.version : null);
    if (data.release) {
      setText("release", data.release.tag || "N/A");
      if (data.release.tag && data.release.url) {
        var rel = field("release");
        if (rel) { var a = doc.createElement("a"); a.href = data.release.url; a.target = "_blank"; a.rel = "noopener"; a.textContent = data.release.tag; rel.textContent = ""; rel.appendChild(a); }
      }
      var log = doc.querySelector("[data-pulse-changelog]");
      if (log && data.release.highlights && data.release.highlights.length) {
        log.hidden = false; log.textContent = "";
        data.release.highlights.slice(0, 3).forEach(function (h) {
          var d = doc.createElement("div"); d.className = "pl-item"; d.textContent = h; log.appendChild(d);
        });
      }
    }
    if (data.repo_stats && data.repo_stats.latest_commit) {
      setText("commit", data.repo_stats.latest_commit.sha || null);
    }
    if (data.repo_stats && data.repo_stats.ci) {
      var ci = data.repo_stats.ci;
      setText("ci", ci.conclusion ? ci.conclusion.toUpperCase() : (ci.status ? ci.status.toUpperCase() : "N/A"));
    }
    if (data.repo_stats) {
      setText("stars", typeof data.repo_stats.stars === "number" ? data.repo_stats.stars : "N/A");
      setText("forks", typeof data.repo_stats.forks === "number" ? data.repo_stats.forks : "N/A");
      setText("open_issues", typeof data.repo_stats.open_issues === "number" ? data.repo_stats.open_issues : "N/A");
    }
    setText("last_sync", fmtDate(data.generated_at));
  }
  var xhr = new XMLHttpRequest();
  xhr.open("GET", STATUS_URL, true);
  xhr.timeout = 8000;
  xhr.onload = function () {
    var data = null;
    try { data = JSON.parse(xhr.responseText); } catch (e) {}
    if (!data) return;
    render(data);
    // Fresh-enough? Build metadata can lag a release; only top up from the API
    // when the page was built a while ago. No token — rate-limited public API.
    var age = Date.now() - new Date(data.generated_at || 0).getTime();
    var repo = (data.repo || "").replace(/^https?:\/\/github\.com\//, "");
    if (isFinite(age) && age > SYNC_TTL_MS && repo && /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) {
      var api = new XMLHttpRequest();
      api.open("GET", "https://api.github.com/repos/" + repo, true);
      api.timeout = 6000;
      api.setRequestHeader("Accept", "application/vnd.github+json");
      api.onload = function () {
        try {
          var g = JSON.parse(api.responseText);
          if (!g || typeof g !== "object" || g.message) return; // rate-limited or missing — keep build values
          setText("stars", g.stargazers_count);
          setText("forks", g.forks_count);
          setText("open_issues", g.open_issues_count);
          setText("last_sync", fmtDate(new Date().toISOString()) + " · GitHub");
        } catch (e) { /* keep build values */ }
      };
      api.onerror = api.ontimeout = function () { /* keep build values */ };
      try { api.send(); } catch (e) {}
    }
  };
  xhr.onerror = xhr.ontimeout = function () { /* placeholders stay N/A */ };
  try { xhr.send(); } catch (e) {}

  /* ---------- cursor glow (desktop, non-touch, motion allowed) ---------- */
  if (!reduced && window.matchMedia && window.matchMedia("(pointer: fine)").matches) {
    var glow = doc.createElement("div");
    glow.setAttribute("aria-hidden", "true");
    glow.style.cssText = "position:fixed;z-index:0;width:520px;height:520px;border-radius:50%;pointer-events:none;left:0;top:0;transform:translate(-50%,-50%);background:radial-gradient(circle, rgba(34,211,238,.05), transparent 60%);";
    doc.body.appendChild(glow);
    var gx = 0, gy = 0, tx = 0, ty = 0, pend = false;
    window.addEventListener("pointermove", function (e) {
      tx = e.clientX; ty = e.clientY;
      if (!pend && !reduced) { pend = true; requestAnimationFrame(function step() {
        gx += (tx - gx) * 0.12; gy += (ty - gy) * 0.12;
        glow.style.left = gx + "px"; glow.style.top = gy + "px";
        if (Math.abs(tx - gx) + Math.abs(ty - gy) > 0.6) requestAnimationFrame(step); else pend = false;
      }); }
    }, { passive: true });
  }
})();
