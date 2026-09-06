// TradingView-exact widget — fetches /api/v1/indicators and renders gauges + tables.
// Polls every 10s; timeframe buttons drive the query param.
(function(){
  var TF = 'M1';
  var POLL_MS = 10000;
  var timer = null;

  function el(id){ return document.getElementById(id); }

  function fmt(v){
    if (v === null || v === undefined) return '—';
    if (typeof v !== 'number') return String(v);
    if (Math.abs(v) >= 1000) {
      return v.toLocaleString('en-US',{minimumFractionDigits:3, maximumFractionDigits:3});
    }
    var s = Math.abs(v) >= 100 ? v.toFixed(2) : v.toFixed(3);
    return s.replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,'');
  }
  function actionColor(a){
    if (a === 'Buy') return '#16A34A';
    if (a === 'Sell') return '#EF4444';
    if (a === 'Strong buy') return '#16A34A';
    if (a === 'Strong sell') return '#EF4444';
    return '#6B7280';
  }

  function drawGauge(svgId, angleDeg, label){
    var svg = el(svgId);
    if (!svg) return;
    // 180° arc: center (100,100) radius 80, from 180deg to 0deg
    var cx=100, cy=100, r=80;
    // classify label color / active arc fraction
    var isSell = label === 'Sell' || label === 'Strong sell';
    var isBuy  = label === 'Buy'  || label === 'Strong buy';
    var activeFrac = 0;
    if (label === 'Strong sell') activeFrac = 0.06;
    else if (label === 'Sell')   activeFrac = 0.14;
    else if (label === 'Neutral') activeFrac = 0.42;
    else if (label === 'Buy')     activeFrac = 0.14;
    else if (label === 'Strong buy') activeFrac = 0.06;
    var gradId = 'g-'+svgId;
    var html = '<defs><linearGradient id="'+gradId+'" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#EF4444"/><stop offset="100%" stop-color="#EC4899"/></linearGradient><linearGradient id="'+gradId+'-buy" x1="0%" y1="0%" x2="100%" y2="0%"><stop offset="0%" stop-color="#10B981"/><stop offset="100%" stop-color="#22C55E"/></linearGradient></defs>';
    // background
    html += '<path d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke="#E5E7EB" stroke-width="11" stroke-linecap="round"/>';
    if (activeFrac > 0) {
      var aLen = 180*activeFrac;
      if (isSell) {
        var rad = (180 - aLen)*Math.PI/180;
        var x2 = cx + r*Math.cos(Math.PI - aLen*Math.PI/180); // start at 180deg, sweep aLen
        // simpler: arc from 180deg (20,100) to 180-aLen deg
        var x = cx + r*Math.cos(rad);
        var y = cy - r*Math.sin(rad);
        // large-arc? no, aLen <90 so small arc
        html += '<path d="M 20 100 A 80 80 0 0 1 '+x.toFixed(1)+' '+y.toFixed(1)+'" fill="none" stroke="url(#'+gradId+')" stroke-width="11" stroke-linecap="round"/>';
      } else if (isBuy) {
        var rad2 = aLen*Math.PI/180;
        var xb = cx + r*Math.cos(rad2);
        var yb = cy - r*Math.sin(rad2);
        html += '<path d="M 180 100 A 80 80 0 0 1 '+xb.toFixed(1)+' '+yb.toFixed(1)+'" fill="none" stroke="url(#'+gradId+'-buy)" stroke-width="11" stroke-linecap="round"/>';
      } else {
        // Neutral: no colored arc (truthful) — label + needle carry the state.
      }
    }
    var labels = [['Strong sell',5,105],['Sell',33,38],['Neutral',100,12],['Buy',167,38],['Strong buy',195,105]];
    for (var i=0;i<labels.length;i++){
      var L=labels[i];
      var col = '#9CA3AF';
      if (label === 'Sell' && L[0]==='Sell') col='#EF4444';
      else if (label === 'Strong sell' && L[0]==='Strong sell') col='#EF4444';
      else if (label === 'Buy' && L[0]==='Buy') col='#16A34A';
      else if (label === 'Strong buy' && L[0]==='Strong buy') col='#16A34A';
      html += '<text x="'+L[1]+'" y="'+L[2]+'" font-size="8" fill="'+col+'" text-anchor="middle" font-family="ui-sans-serif,system-ui,sans-serif"'+(col!=='#9CA3AF'?' font-weight="700"':'')+'>'+L[0]+'</text>';
    }
    // needle: angleDeg 0=Strong sell (left, ~0deg horizontal), 90=Neutral (up), 180=Strong buy (right)
    // map: 0->180deg, 90->90deg, 180->0deg in math coords
    var theta = (180 - angleDeg)*Math.PI/180;
    var nx = cx + 72*Math.cos(theta);
    var ny = cy - 72*Math.sin(theta);
    html += '<line x1="'+cx+'" y1="'+cy+'" x2="'+nx.toFixed(1)+'" y2="'+ny.toFixed(1)+'" stroke="#111827" stroke-width="2.2" stroke-linecap="round"/>';
    html += '<circle cx="'+cx+'" cy="'+cy+'" r="4.5" fill="#111827" stroke="white" stroke-width="1"/>';
    svg.innerHTML = html;
  }

  function render(data){
    if (!data) return;
    var osc = data.oscillators || [];
    var ma  = data.moving_averages || [];
    var piv = data.pivots || {levels:[], columns:[], rows:{}};
    var g   = data.gauges || {};
    var sum = data.summary || {};

    // gauges: server gives {oscillators:{label,sell,neutral,buy,angle_deg}, moving_averages, summary}
    var go = g.oscillators || {label:'Neutral',sell:0,neutral:0,buy:0,angle_deg:90};
    var gs = g.summary     || {label:'Neutral',sell:0,neutral:0,buy:0,angle_deg:90};
    var gm = g.moving_averages || {label:'Neutral',sell:0,neutral:0,buy:0,angle_deg:90};

    drawGauge('tv-gauge-osc', go.angle_deg, go.label);
    drawGauge('tv-gauge-sum', gs.angle_deg, gs.label);
    drawGauge('tv-gauge-ma',  gm.angle_deg, gm.label);

    if (el('tv-osc-label')) { el('tv-osc-label').textContent = go.label; el('tv-osc-label').style.color = (go.label==='Sell'||go.label==='Strong sell') ? '#EF4444' : (go.label==='Buy'||go.label==='Strong buy' ? '#16A34A' : '#111827'); }
    if (el('tv-sum-label')) { el('tv-sum-label').textContent = gs.label; el('tv-sum-label').style.color = (gs.label==='Sell'||gs.label==='Strong sell') ? '#EF4444' : (gs.label==='Buy'||gs.label==='Strong buy' ? '#16A34A' : '#111827'); }
    if (el('tv-ma-label'))  { el('tv-ma-label').textContent  = gm.label; el('tv-ma-label').style.color  = (gm.label==='Sell'||gm.label==='Strong sell') ? '#EF4444' : (gm.label==='Buy'||gm.label==='Strong buy' ? '#16A34A' : '#111827'); }

    if (el('tv-osc-sell')) el('tv-osc-sell').textContent = String(go.sell);
    if (el('tv-osc-neu'))  el('tv-osc-neu').textContent  = String(go.neutral);
    if (el('tv-osc-buy'))  el('tv-osc-buy').textContent  = String(go.buy);
    if (el('tv-sum-sell')) el('tv-sum-sell').textContent = String(gs.sell);
    if (el('tv-sum-neu'))  el('tv-sum-neu').textContent  = String(gs.neutral);
    if (el('tv-sum-buy'))  el('tv-sum-buy').textContent  = String(gs.buy);
    if (el('tv-ma-sell')) el('tv-ma-sell').textContent = String(gm.sell);
    if (el('tv-ma-neu'))  el('tv-ma-neu').textContent  = String(gm.neutral);
    if (el('tv-ma-buy'))  el('tv-ma-buy').textContent  = String(gm.buy);
    if (el('tv-osc-head')) el('tv-osc-head').textContent = go.label;
    if (el('tv-ma-head'))  el('tv-ma-head').textContent  = gm.label;

    var ob = el('tv-osc-body');
    if (ob) {
      ob.innerHTML = osc.map(function(r){
        var c = actionColor(r.action);
        var w = c==='#6B7280' ? 500 : 700;
        return '<tr><td class="px-3 py-1.5 text-gray-800">'+r.name+'</td><td class="px-3 py-1.5 text-right font-mono tabular-nums text-gray-900">'+fmt(r.value)+'</td><td class="px-3 py-1.5 text-right" style="color:'+c+';font-weight:'+w+'">'+r.action+'</td></tr>';
      }).join('');
    }
    var mb = el('tv-ma-body');
    if (mb) {
      mb.innerHTML = ma.map(function(r){
        var c = actionColor(r.action);
        var w = c==='#6B7280' ? 500 : 700;
        return '<tr><td class="px-3 py-1.5 text-gray-800">'+r.name+'</td><td class="px-3 py-1.5 text-right font-mono tabular-nums text-gray-900">'+fmt(r.value)+'</td><td class="px-3 py-1.5 text-right" style="color:'+c+';font-weight:'+w+'">'+r.action+'</td></tr>';
      }).join('');
    }
    var pb = el('tv-pivot-body');
    if (pb) {
      var levels = piv.levels || ['R3','R2','R1','P','S1','S2','S3'];
      var cols = ['Classic','Fibonacci','Camarilla','Woodie','DM'];
      pb.innerHTML = levels.map(function(lv){
        var row = (piv.rows && piv.rows[lv]) || {};
        var tds = cols.map(function(col){ var v=row[col]; return '<td class="px-3 py-1.5 text-right">'+(v===null||v===undefined?'—':fmt(v))+'</td>'; }).join('');
        var bold = lv==='P' ? ' font-extrabold bg-gray-50/60' : ' font-semibold';
        return '<tr class="'+bold+'"><td class="px-3 py-1.5 font-sans'+bold+'">'+lv+'</td>'+tds+'</tr>';
      }).join('');
    }
    var badge = el('tv-live-badge');
    if (badge) {
      var n = data.bar_count || 0;
      badge.classList.remove('hidden');
      badge.textContent = (n ? n+' bars · ' : '') + (data.timeframe||TF) + ' · ' + (data.symbol||'');
      badge.className = 'ml-auto text-[10px] font-bold px-2 py-0.5 rounded-full border ' + (n>=50 ? 'bg-emerald-50 text-emerald-600 border-emerald-200' : 'bg-amber-50 text-amber-600 border-amber-200');
    }
  }

  function showError(msg){
    var badge = el('tv-live-badge');
    if (badge){ badge.classList.remove('hidden'); badge.textContent = msg; badge.className='ml-auto text-[10px] font-bold px-2 py-0.5 rounded-full border bg-rose-50 text-rose-600 border-rose-200'; }
  }

  function fetchAndRender(){
    var url = '/api/v1/indicators?timeframe='+encodeURIComponent(TF)+'&limit=300';
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
    var nodes = document.querySelectorAll('#tv-timeframes .tv-tf');
    nodes.forEach(function(btn){
      btn.addEventListener('click', function(){
        TF = btn.getAttribute('data-tf') || 'M1';
        nodes.forEach(function(b){ b.classList.remove('active','bg-gray-900','text-white','font-semibold'); b.classList.add('text-black'); b.classList.add('hover:bg-gray-50'); });
        btn.classList.add('active','bg-gray-900','text-white','font-semibold'); btn.classList.remove('text-black');
        btn.classList.remove('hover:bg-gray-50');
        fetchAndRender();
      });
    });
  }

  // init when widget is in DOM (tv_widget.html may be fetched async)
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
    setTimeout(function(){ clearInterval(iv); }, 5000);
  }
  window.__tvIndicatorRefresh = fetchAndRender;
})();
