// Nexus Technicals widget — native dark theme, AA contrast.
// Fetches /api/v1/indicators?timeframe=TF (server resamples M1 bars → TF) and renders
// gauges + tables. Polls every 10s; timeframe buttons refetch immediately.
(function(){
  var TF = 'M1';
  var POLL_MS = 10000;
  var timer = null;
  var bound = false;

  var C = {
    bg: '#0b1220',
    arcIdle: '#3b4a6b',          // visible gray-blue idle arc (NOT near-black)
    labelIdle: '#cbd5e1',        // slate-300 idle labels
    labelActiveSell: '#f87171',  // red-400
    labelActiveBuy: '#4ade80',   // green-400
    verdictSell: '#f87171',
    verdictBuy: '#4ade80',
    verdictNeutral: '#e2e8f0',
    needle: '#f8fafc',
    hub: '#0b1220',
    rowName: '#e2e8f0',
    rowValue: '#f1f5f9',
    rowNeutral: '#94a3b8',
    rowSell: '#f87171',
    rowBuy: '#4ade80',
    zebra: 'rgba(148,163,184,0.06)',
    hover: 'rgba(56,189,248,0.10)'
  };

  function el(id){ return document.getElementById(id); }

  function fmt(v){
    if (v === null || v === undefined) return '—';
    if (typeof v !== 'number') return String(v);
    if (!isFinite(v)) return '—';
    if (Math.abs(v) >= 1000) return v.toLocaleString('en-US',{minimumFractionDigits:3, maximumFractionDigits:3});
    var s = Math.abs(v) >= 100 ? v.toFixed(2) : v.toFixed(3);
    return s.replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,'');
  }
  function actionColor(a){
    if (a === 'Buy' || a === 'Strong buy') return C.rowBuy;
    if (a === 'Sell' || a === 'Strong sell') return C.rowSell;
    return C.rowNeutral;
  }

  // Semicircle gauge, viewBox 200x115, center (100,100), r=80.
  function drawGauge(svgId, angleDeg, label){
    var svg = el(svgId);
    if (!svg) return;
    var cx=100, cy=100, r=80;
    function pt(deg){ var th=(180-deg)*Math.PI/180; return [cx + r*Math.cos(th), cy - r*Math.sin(th)]; }
    function arc(a0, a1){
      var p0 = pt(a0), p1 = pt(a1);
      var large = Math.abs(a1-a0) > 180 ? 1 : 0;
      var sweep = a1 > a0 ? 1 : 0;
      return 'M '+p0[0].toFixed(1)+' '+p0[1].toFixed(1)+' A '+r+' '+r+' 0 '+large+' '+sweep+' '+p1[0].toFixed(1)+' '+p1[1].toFixed(1);
    }
    var html = '<defs>' +
      '<linearGradient id="g-'+svgId+'" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#DC2626"/><stop offset="100%" stop-color="#F472B6"/></linearGradient>' +
      '<linearGradient id="gb-'+svgId+'" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#34D399"/><stop offset="100%" stop-color="#10B981"/></linearGradient>' +
      '</defs>';
    // idle arc (full 180)
    html += '<path d="'+arc(0,180)+'" fill="none" stroke="'+C.arcIdle+'" stroke-width="11" stroke-linecap="round"/>';
    // active segment (TV-style: sell fills from left tip, buy from right tip)
    function band(a0, a1, stroke){
      if (Math.abs(a1-a0) < 2) return;
      html += '<path d="'+arc(a0,a1)+'" fill="none" stroke="'+stroke+'" stroke-width="11" stroke-linecap="butt"/>';
    }
    if (label === 'Strong sell') band(0, 22, 'url(#g-'+svgId+')');
    else if (label === 'Sell') band(0, 55, 'url(#g-'+svgId+')');
    else if (label === 'Buy') band(125, 180, 'url(#gb-'+svgId+')');
    else if (label === 'Strong buy') band(158, 180, 'url(#gb-'+svgId+')');
    // labels: five zones, positioned ON the arc, readable
    var L = [
      ['Strong sell', 8,   108],
      ['Sell',        45,  34],
      ['Neutral',     90,  8],
      ['Buy',         135, 34],
      ['Strong buy',  172, 108]
    ];
    for (var i=0;i<L.length;i++){
      var name = L[i][0];
      var isActive = (label === name);
      var col = C.labelIdle;
      if (isActive) col = (name.indexOf('sell')>=0) ? C.labelActiveSell : (name.indexOf('buy')>=0 ? C.labelActiveBuy : '#ffffff');
      var w = isActive ? ' font-weight="800"' : ' font-weight="600"';
      html += '<text x="'+L[i][1]+'" y="'+L[i][2]+'" font-size="8.5" fill="'+col+'" text-anchor="middle" font-family="ui-sans-serif,system-ui,sans-serif"'+w+'>'+name+'</text>';
    }
    // needle
    var theta = (180 - angleDeg)*Math.PI/180;
    var nx = cx + 66*Math.cos(theta);
    var ny = cy - 66*Math.sin(theta);
    html += '<line x1="'+cx+'" y1="'+cy+'" x2="'+nx.toFixed(1)+'" y2="'+ny.toFixed(1)+'" stroke="'+C.needle+'" stroke-width="2.4" stroke-linecap="round"/>';
    html += '<circle cx="'+cx+'" cy="'+cy+'" r="5" fill="'+C.hub+'" stroke="#e2e8f0" stroke-width="1.4"/>';
    svg.innerHTML = html;
  }

  function verdictColor(label){
    if (label === 'Sell' || label === 'Strong sell') return C.verdictSell;
    if (label === 'Buy' || label === 'Strong buy') return C.verdictBuy;
    return C.verdictNeutral;
  }

  function rowHTML(r){
    var c = actionColor(r.action);
    var w = c === C.rowNeutral ? 500 : 700;
    return '<tr style="background:transparent">' +
      '<td class="px-3 py-1.5" style="color:'+C.rowName+'">'+r.name+'</td>' +
      '<td class="px-3 py-1.5 text-right font-mono tabular-nums" style="color:'+C.rowValue+'">'+fmt(r.value)+'</td>' +
      '<td class="px-3 py-1.5 text-right" style="color:'+c+';font-weight:'+w+'">'+r.action+'</td></tr>';
  }

  function render(data){
    if (!data) return;
    var osc = data.oscillators || [];
    var ma  = data.moving_averages || [];
    var piv = data.pivots || {levels:[], columns:[], rows:{}};
    var g   = data.gauges || {};

    var go = g.oscillators || {label:'Neutral',sell:0,neutral:0,buy:0,angle_deg:90};
    var gs = g.summary     || {label:'Neutral',sell:0,neutral:0,buy:0,angle_deg:90};
    var gm = g.moving_averages || {label:'Neutral',sell:0,neutral:0,buy:0,angle_deg:90};

    drawGauge('tv-gauge-osc', go.angle_deg, go.label);
    drawGauge('tv-gauge-sum', gs.angle_deg, gs.label);
    drawGauge('tv-gauge-ma',  gm.angle_deg, gm.label);

    function setVerdict(id, label){ var n = el(id); if (n){ n.textContent = label; n.style.color = verdictColor(label); } }
    setVerdict('tv-osc-label', go.label);
    setVerdict('tv-sum-label', gs.label);
    setVerdict('tv-ma-label',  gm.label);

    function cnt(id, v){ var n = el(id); if (n) n.textContent = String(v); }
    cnt('tv-osc-sell', go.sell); cnt('tv-osc-neu', go.neutral); cnt('tv-osc-buy', go.buy);
    cnt('tv-sum-sell', gs.sell); cnt('tv-sum-neu', gs.neutral); cnt('tv-sum-buy', gs.buy);
    cnt('tv-ma-sell',  gm.sell); cnt('tv-ma-neu',  gm.neutral); cnt('tv-ma-buy',  gm.buy);
    cnt('tv-osc-head', 0); // placeholder to no-op
    var oh = el('tv-osc-head'); if (oh){ oh.textContent = go.label; oh.style.color = verdictColor(go.label); }
    var mh = el('tv-ma-head'); if (mh){ mh.textContent = gm.label; mh.style.color = verdictColor(gm.label); }

    var ob = el('tv-osc-body'); if (ob) ob.innerHTML = osc.map(rowHTML).join('');
    var mb = el('tv-ma-body');  if (mb) mb.innerHTML = ma.map(rowHTML).join('');

    var pb = el('tv-pivot-body');
    if (pb) {
      var levels = piv.levels || ['R3','R2','R1','P','S1','S2','S3'];
      var cols = ['Classic','Fibonacci','Camarilla','Woodie','DM'];
      pb.innerHTML = levels.map(function(lv){
        var row = (piv.rows && piv.rows[lv]) || {};
        var tds = cols.map(function(col){
          var v = row[col];
          return '<td class="px-3 py-1.5 text-right" style="color:'+C.rowValue+'">'+(v===null||v===undefined?'—':fmt(v))+'</td>';
        }).join('');
        var isP = lv === 'P';
        var bg = isP ? ' style="background:'+C.zebra+'"' : '';
        var wgt = isP ? 'font-extrabold' : 'font-bold';
        return '<tr'+bg+'><td class="px-3 py-1.5 '+wgt+'" style="color:#ffffff">'+lv+'</td>'+tds+'</tr>';
      }).join('');
    }

    var badge = el('tv-live-badge');
    if (badge) {
      var n = data.bar_count || 0;
      var m1b = data.source_bar_count || null;
      badge.classList.remove('hidden');
      badge.textContent = (data.timeframe||TF) + ' · ' + (data.symbol||'') + (m1b ? ' · '+m1b+' M1 bars' : (n ? ' · '+n+' bars' : ''));
      badge.className = 'ml-auto text-[10px] font-bold px-2.5 py-1 rounded-full border ' +
        (n >= 50 ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40' : 'bg-amber-500/15 text-amber-300 border-amber-500/40');
    }
  }

  function showError(msg){
    var badge = el('tv-live-badge');
    if (badge){
      badge.classList.remove('hidden');
      badge.textContent = msg;
      badge.className = 'ml-auto text-[10px] font-bold px-2.5 py-1 rounded-full border bg-rose-500/15 text-rose-300 border-rose-500/40';
    }
  }

  function fetchAndRender(){
    var url = '/api/v1/indicators?timeframe='+encodeURIComponent(TF)+'&limit=2000';
    fetch(url).then(function(r){
      if (!r.ok) return r.json().then(function(j){ throw j; });
      return r.json();
    }).then(function(j){
      render(j.data || j);
    }).catch(function(e){
      var code = (e && e.error && e.error.code) || '';
      if (code === 'ENGINE_UNAVAILABLE') showError('Waiting for engine…');
      else if (code === 'RESOURCE_UNAVAILABLE') showError('No bar history yet…');
      else showError('Indicator feed unavailable');
    });
  }

  function bindTF(){
    if (bound) return;
    var nodes = document.querySelectorAll('#tv-timeframes .tv-tf');
    if (!nodes.length) return;
    bound = true;
    nodes.forEach(function(btn){
      btn.addEventListener('click', function(){
        TF = btn.getAttribute('data-tf') || 'M1';
        nodes.forEach(function(b){
          b.classList.remove('active','bg-white','font-bold');
          b.classList.add('text-gray-300');
        });
        btn.classList.add('active','bg-white','font-bold');
        btn.classList.remove('text-gray-300');
        fetchAndRender();
      });
    });
  }

  function init(){
    if (!el('tv-indicator-widget')) return false;
    bindTF();
    fetchAndRender();
    if (timer) clearInterval(timer);
    timer = setInterval(fetchAndRender, POLL_MS);
    return true;
  }
  if (!init()){
    var iv = setInterval(function(){ if (init()) clearInterval(iv); }, 300);
    setTimeout(function(){ clearInterval(iv); }, 10000);
  }
  window.__tvIndicatorRefresh = fetchAndRender;
})();
