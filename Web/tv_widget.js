// Nexus Technicals widget — professional terminal redesign (TV-REDESIGN-1).
// Agent: Hermes-Main (Hermes-UI-01). UI layer only: SAME endpoint, SAME params,
// SAME payload shape as before (/api/v1/indicators?timeframe=TF&limit=2000).
// State machine: OK / STALE / ERROR (+ skeleton on first load) — all states are
// driven by real fetch outcomes; no fabricated timestamps, prices, or liveness.
// Gauges replaced (user-approved 2026-09-06) by count-proportional distribution
// bars; last_close + meta.generated_at surfaced from the real response.
(function(){
  var TF = 'M1';
  var POLL_MS = 10000;        // healthy cadence (unchanged contract)
  var POLL_ERR_MS = 5000;     // faster retry while degradped/stale
  var timer = null;
  var bound = false;
  var state = 'idle';         // idle | ok | stale | error
  var lastGood = null;        // { data, meta, at: Date }
  var inflight = false;

  var GLYPH = { buy: '\u25B2', sell: '\u25BC', neutral: '\u25CF' };

  // ── cycles palette — same red→purple→blue as your reference screenshot,
  //     tuned for dark: idle track #3b4a6b (not white), labels readable on #0d1526
  var TC = {
    arcIdle: '#3b4a6b',
    labelIdle: '#8ea0bd',
    labelSell: '#f87171',
    labelBuy: '#5fb3ff',
    verdictSell: '#f87171',
    verdictBuy: '#5fb3ff',
    verdictNeutral: '#dbe4f2',
    needle: '#e6edf7',
    hub: '#0d1526'
  };

  function el(id){ return document.getElementById(id); }
  function setText(id, t){ var n = el(id); if (n) n.textContent = t; }
  function setCls(n, cls, on){ if (!n) return; if (on) n.classList.add(cls); else n.classList.remove(cls); }

  function fmt(v){
    if (v === null || v === undefined) return '\u2014';
    if (typeof v !== 'number') return String(v);
    if (!isFinite(v)) return '\u2014';
    if (Math.abs(v) >= 1000) return v.toLocaleString('en-US',{minimumFractionDigits:3, maximumFractionDigits:3});
    var s = Math.abs(v) >= 100 ? v.toFixed(2) : v.toFixed(3);
    return s.replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,'');
  }
  function fmtPrice(v){
    if (v === null || v === undefined || !isFinite(v)) return '\u2014';
    return Number(v).toLocaleString('en-US',{minimumFractionDigits:2, maximumFractionDigits:2});
  }
  function fmtTime(d){
    if (!d) return '\u2014';
    function p(x){ return (x < 10 ? '0' : '') + x; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }
  function actionKind(a){
    if (a === 'Buy' || a === 'Strong buy') return 'buy';
    if (a === 'Sell' || a === 'Strong sell') return 'sell';
    return 'neutral';
  }

  // ── TV-style semicircular cycles (exact-colors match reference, dark substrate)
  // viewBox 200x115, center (100,100), r=80. Idle 180° arc in #3b4a6b; active
  // segment colored sell=red band from left, buy=blue band from right, neutral=grey.
  function drawCycle(svgId, label, angleDeg){
    var svg = el(svgId);
    if (!svg) return;
    var cx = 100, cy = 100, r = 80;
    function pt(deg){ var th = (180 - deg) * Math.PI / 180; return [cx + r * Math.cos(th), cy - r * Math.sin(th)]; }
    function arc(a0, a1){
      var p0 = pt(a0), p1 = pt(a1);
      var large = Math.abs(a1 - a0) > 180 ? 1 : 0;
      var sweep = a1 > a0 ? 1 : 0;
      return 'M ' + p0[0].toFixed(1) + ' ' + p0[1].toFixed(1) + ' A ' + r + ' ' + r + ' 0 ' + large + ' ' + sweep + ' ' + p1[0].toFixed(1) + ' ' + p1[1].toFixed(1);
    }
    var gid = 'g-' + svgId, gbid = 'gb-' + svgId;
    var html = '<defs>' +
      '<linearGradient id="' + gid + '" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#DC2626"/><stop offset="100%" stop-color="#F472B6"/></linearGradient>' +
      '<linearGradient id="' + gbid + '" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#3b82f6"/><stop offset="100%" stop-color="#38bdf8"/></linearGradient>' +
      '</defs>';
    html += '<path d="' + arc(0, 180) + '" fill="none" stroke="' + TC.arcIdle + '" stroke-width="11" stroke-linecap="round"/>';
    function band(a0, a1, stroke){ if (Math.abs(a1 - a0) < 2) return; html += '<path d="' + arc(a0, a1) + '" fill="none" stroke="' + stroke + '" stroke-width="11" stroke-linecap="butt"/>'; }
    if (label === 'Strong sell') band(0, 22, 'url(#' + gid + ')');
    else if (label === 'Sell') band(0, 55, 'url(#' + gid + ')');
    else if (label === 'Buy') band(125, 180, 'url(#' + gbid + ')');
    else if (label === 'Strong buy') band(158, 180, 'url(#' + gbid + ')');
    var L = [['Strong sell', 8, 108], ['Sell', 45, 34], ['Neutral', 90, 8], ['Buy', 135, 34], ['Strong buy', 172, 108]];
    for (var i = 0; i < L.length; i++){
      var name = L[i][0], isActive = (label === name);
      var col = TC.labelIdle;
      if (isActive) col = (name.indexOf('sell') >= 0) ? TC.labelSell : (name.indexOf('buy') >= 0 ? TC.labelBuy : '#ffffff');
      var w = isActive ? ' font-weight="800"' : ' font-weight="600"';
      html += '<text x="' + L[i][1] + '" y="' + L[i][2] + '" font-size="8.5" fill="' + col + '" text-anchor="middle" font-family="ui-sans-serif,system-ui,sans-serif"' + w + '>' + name + '</text>';
    }
    var ang = (typeof angleDeg === 'number' && isFinite(angleDeg)) ? angleDeg : 90;
    var theta = (180 - ang) * Math.PI / 180, nx = cx + 66 * Math.cos(theta), ny = cy - 66 * Math.sin(theta);
    html += '<line x1="' + cx + '" y1="' + cy + '" x2="' + nx.toFixed(1) + '" y2="' + ny.toFixed(1) + '" stroke="' + TC.needle + '" stroke-width="2.4" stroke-linecap="round"/>';
    html += '<circle cx="' + cx + '" cy="' + cy + '" r="5" fill="' + TC.hub + '" stroke="#e2e8f0" stroke-width="1.4"/>';
    svg.innerHTML = html;
  }

  // ── distribution bar (widths = real counts via flex-grow) — kept as primary signal
  function seg(id, count){
    var n = el(id);
    if (!n) return;
    n.style.flexGrow = String(Math.max(0, Number(count) || 0));
  }
  function drawBar(prefix, g){
    seg(prefix + 'seg-sell', g.sell);
    seg(prefix + 'seg-neu', g.neutral);
    seg(prefix + 'seg-buy', g.buy);
  }

  // ── signal cards — distribution bars kept; cycles are ADDITIONAL visual
  function verdictClass(label){
    if (label === 'Buy' || label === 'Strong buy') return 'is-buy';
    if (label === 'Sell' || label === 'Strong sell') return 'is-sell';
    return 'is-neutral';
  }
  function setVerdict(id, label){
    var n = el(id);
    if (!n) return;
    n.textContent = label;
    n.classList.remove('is-buy','is-sell','is-neutral');
    n.classList.add(verdictClass(label));
  }
  function setChip(id, label){
    var n = el(id);
    if (!n) return;
    n.textContent = label;
    n.classList.remove('is-buy','is-sell','is-neutral');
    n.classList.add(verdictClass(label));
  }
  function renderSignal(prefix, g, meta){
    setVerdict(prefix + 'label', g.label);
    setChip(prefix + 'chip', g.label);
    drawBar(prefix, g);
    setText(prefix + 'sell', String(g.sell));
    setText(prefix + 'neu', String(g.neutral));
    setText(prefix + 'buy', String(g.buy));
    if (meta) setText(prefix + 'meta', meta);
  }

  // ── tables ──────────────────────────────────────────────────────────────
  function rowHTML(r){
    var kind = actionKind(r.action);
    return '<tr>' +
      '<td class="tv-name">' + escapeHTML(r.name) + '</td>' +
      '<td class="tv-val">' + fmt(r.value) + '</td>' +
      '<td class="tv-act"><span class="tv-action is-' + kind + '"><i aria-hidden="true">' + GLYPH[kind] + '</i>' +
      escapeHTML(r.action) + '</span></td></tr>';
  }
  function emptyRowHTML(cols, msg){
    return '<tr><td colspan="' + cols + '" class="tv-empty">' + escapeHTML(msg) + '</td></tr>';
  }
  function escapeHTML(s){
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }
  function setBody(id, rows, cols, emptyMsg){
    var b = el(id);
    if (!b) return;
    b.innerHTML = rows.length ? rows.map(rowHTML).join('') : emptyRowHTML(cols, emptyMsg);
  }

  // ── loading skeletons / banner / toast ─────────────────────────────────
  var SKELETON_IDS = ['tv-price','tv-updated','tv-sum-label','tv-osc-label','tv-ma-label',
    'tv-gauge-sum','tv-gauge-osc','tv-gauge-ma',
    'tv-cycle-osc','tv-cycle-sum','tv-cycle-ma'];
  function skeleton(on){
    for (var i=0;i<SKELETON_IDS.length;i++){
      var n = el(SKELETON_IDS[i]);
      setCls(n, 'skeleton', on);
    }
    ['tv-sum-sell','tv-sum-neu','tv-sum-buy','tv-osc-sell','tv-osc-neu','tv-osc-buy',
     'tv-ma-sell','tv-ma-neu','tv-ma-buy'].forEach(function(id){
      setCls(el(id), 'skeleton', on);
    });
    if (on){
      setBody('tv-osc-body', [], 3, 'Loading oscillators\u2026');
      setBody('tv-ma-body', [], 3, 'Loading moving averages\u2026');
    }
  }

  var toastTimer = null;
  function toast(msg){
    var t = el('tv-toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('show');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function(){ t.classList.remove('show'); }, 3500);
  }

  function setBanner(kind, msg){
    var root = el('tv-indicator-widget');
    var bar = el('tv-error');
    if (!bar) return;
    setCls(root, 'tv-error', kind === 'error');
    setCls(root, 'tv-stale', kind === 'stale');
    setCls(bar, 'hidden', !msg);
    setCls(bar, 'is-stale', kind === 'stale');
    if (msg){
      var t = el('tv-error-text');
      if (t) t.textContent = msg;
    }
  }

  // ── main render ─────────────────────────────────────────────────────────
  function render(data, meta){
    if (!data) return;
    var osc = data.oscillators || [];
    var ma  = data.moving_averages || [];
    var piv = data.pivots || {levels:[],columns:[],rows:{}};
    var g   = data.gauges || {};

    var go = g.oscillators || {label:'Neutral',sell:0,neutral:0,buy:0};
    var gs = g.summary     || {label:'Neutral',sell:0,neutral:0,buy:0};
    var gm = g.moving_averages || {label:'Neutral',sell:0,neutral:0,buy:0};

    // context strip — every value from the real response
    setText('tv-symbol', data.symbol || 'XAUUSD');
    setText('tv-price', fmtPrice(data.last_close));
    setText('tv-timeframe-label', data.timeframe || TF);
    var total = gs.sell + gs.neutral + gs.buy;
    renderSignal('tv-sum-', gs, total + ' signals \u00B7 ' + (data.timeframe || TF));
    renderSignal('tv-osc-', go, (go.sell + go.neutral + go.buy) + ' oscillators');
    renderSignal('tv-ma-',  gm, (gm.sell + gm.neutral + gm.buy) + ' averages');

    // cycles — same gauges as your reference (red→blue, dark substrate), additive
    var aOsc = go.angle_deg, aSum = gs.angle_deg, aMa = gm.angle_deg;
    drawCycle('tv-cycle-osc', go.label, aOsc);
    drawCycle('tv-cycle-sum', gs.label, aSum);
    drawCycle('tv-cycle-ma',  gm.label, aMa);
    // cycle verdicts + counts mirror distribution bars (same data, second visual)
    function cycleVerdict(id, label){ var n = el(id); if (!n) return; n.textContent = label; n.classList.remove('is-buy','is-sell','is-neutral'); n.classList.add(label === 'Buy' || label === 'Strong buy' ? 'is-buy' : label === 'Sell' || label === 'Strong sell' ? 'is-sell' : 'is-neutral'); }
    cycleVerdict('tv-cycle-osc-label', go.label);
    cycleVerdict('tv-cycle-sum-label', gs.label);
    cycleVerdict('tv-cycle-ma-label', gm.label);
    setText('tv-cycle-osc-sell', String(go.sell)); setText('tv-cycle-osc-neu', String(go.neutral)); setText('tv-cycle-osc-buy', String(go.buy));
    setText('tv-cycle-sum-sell', String(gs.sell)); setText('tv-cycle-sum-neu', String(gs.neutral)); setText('tv-cycle-sum-buy', String(gs.buy));
    setText('tv-cycle-ma-sell',  String(gm.sell)); setText('tv-cycle-ma-neu',  String(gm.neutral)); setText('tv-cycle-ma-buy',  String(gm.buy));

    setBody('tv-osc-body', osc, 3, 'No oscillator data for this timeframe yet \u2014 waiting for completed bars.');
    setBody('tv-ma-body', ma, 3, 'No moving-average data for this timeframe yet \u2014 waiting for completed bars.');

    var pb = el('tv-pivot-body');
    if (pb){
      var levels = piv.levels || ['R3','R2','R1','P','S1','S2','S3'];
      var cols = piv.columns && piv.columns.length ? piv.columns : ['Classic','Fibonacci','Camarilla','Woodie','DM'];
      if (!levels.length){
        pb.innerHTML = emptyRowHTML(cols.length + 1, 'No pivot levels available yet.');
      } else {
        pb.innerHTML = levels.map(function(lv){
          var row = (piv.rows && piv.rows[lv]) || {};
          var tds = cols.map(function(col){
            var v = row[col];
            return '<td class="tv-val">' + (v === null || v === undefined ? '\u2014' : fmt(v)) + '</td>';
          }).join('');
          return '<tr' + (lv === 'P' ? ' class="is-pivot"' : '') + '><td class="tv-pivotlvl">' + escapeHTML(lv) + '</td>' + tds + '</tr>';
        }).join('');
      }
    }

    // live badge — honest: green only on a fresh successful fetch
    var badge = el('tv-live-badge');
    if (badge){
      var n = data.bar_count || 0;
      var m1b = data.source_bar_count || null;
      badge.classList.remove('hidden','is-warn');
      badge.textContent = (data.timeframe || TF) + ' \u00B7 ' + (data.symbol || '') +
        (m1b ? ' \u00B7 ' + m1b + ' M1 bars' : (n ? ' \u00B7 ' + n + ' bars' : ''));
    }
    // server-generated timestamp from the response envelope meta (real data)
    var upd = el('tv-updated');
    if (upd && meta && meta.generated_at){
      var t = new Date(meta.generated_at);
      if (!isNaN(t.getTime())) upd.textContent = 'Updated ' + fmtTime(t);
    }
  }

  // ── fetch + state transitions ───────────────────────────────────────────
  function fetchAndRender(){
    if (inflight) return;
    inflight = true;
    var firstLoad = !lastGood;
    if (firstLoad) skeleton(true);
    var url = '/api/v1/indicators?timeframe=' + encodeURIComponent(TF) + '&limit=2000';
    fetch(url).then(function(r){
      if (!r.ok) return r.json().then(function(j){ throw j; });
      return r.json();
    }).then(function(j){
      inflight = false;
      state = 'ok';
      lastGood = { data: j.data || j, meta: j.meta || null, at: new Date() };
      skeleton(false);
      setBanner('', '');
      render(lastGood.data, lastGood.meta);
    }).catch(function(e){
      inflight = false;
      var code = (e && e.error && e.error.code) || '';
      var msg = code === 'ENGINE_UNAVAILABLE' ? 'Waiting for engine\u2026'
        : code === 'RESOURCE_UNAVAILABLE' ? 'No bar history yet\u2026'
        : 'Indicator feed unavailable';
      if (lastGood && lastGood.data){
        // keep showing the last real data; never pretend the feed is alive
        state = 'stale';
        skeleton(false);
        setBanner('stale', 'Live feed interrupted \u2014 showing values from ' + fmtTime(lastGood.at) + ' (' + msg + ')');
      } else {
        state = 'error';
        setBanner('error', msg);
      }
    });
  }

  // ── timeframe buttons ───────────────────────────────────────────────────
  function bindTF(){
    if (bound) return;
    var nodes = document.querySelectorAll('#tv-timeframes .tv-tf');
    if (!nodes.length) return;
    bound = true;
    nodes.forEach(function(btn){
      btn.addEventListener('click', function(){
        TF = btn.getAttribute('data-tf') || 'M1';
        nodes.forEach(function(b){
          b.classList.remove('active');
          b.setAttribute('aria-pressed', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-pressed', 'true');
        fetchAndRender();
      });
    });
    var retry = el('tv-retry');
    if (retry) retry.addEventListener('click', fetchAndRender);
  }

  // ── polling lifecycle (pauses when the tab is hidden) ───────────────────
  function startPolling(){
    if (timer) return;
    timer = setInterval(fetchAndRender, state === 'ok' ? POLL_MS : POLL_ERR_MS);
  }
  function stopPolling(){
    if (timer){ clearInterval(timer); timer = null; }
  }
  function restartPolling(){
    stopPolling();
    startPolling();
  }

  function init(){
    if (!el('tv-indicator-widget')) return false;
    bindTF();
    fetchAndRender();
    startPolling();
    if (!bound) return true; // header may exist without buttons; still live
    document.addEventListener('visibilitychange', function(){
      if (document.hidden){ stopPolling(); }
      else { fetchAndRender(); restartPolling(); }
    });
    return true;
  }
  if (!init()){
    var iv = setInterval(function(){ if (init()) clearInterval(iv); }, 300);
    setTimeout(function(){ clearInterval(iv); }, 10000);
  }
  window.__tvIndicatorRefresh = fetchAndRender;
})();
