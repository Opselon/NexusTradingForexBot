/* =========================================================================
 * NEXUS COMMAND CENTER — Live Event Stream + Debug Console (Frontend)
 * -------------------------------------------------------------------------
 * Consumes the live event feed + authoritative fleet/inspector data, rendering
 * structured diagnostic rows with:
 *   - distinct event classification (10 mutually-recognisable classes)
 *   - per-event context: timestamp, severity, type, strategy, generation,
 *     lifecycle, correlation id, source
 *   - controls: pause / resume / clear / search / filter (severity, strategy,
 *     event type, generation, time range) / auto-scroll / jump-to-live
 *   - click actions: inspect strategy, focus node, open timeline, open evidence,
 *     copy correlation id
 *   - a live BOTTLENECK visualization derived from real /fleet evidence data
 *
 * Namespace contract (do NOT rename): window.NX.console
 * The console never fabricates data — every number it shows comes from the
 * authoritative /api/command-center/fleet or inspector responses.
 * ========================================================================= */

(function () {
  'use strict';

  let events = [];
  let paused = false;
  let autoScroll = true;
  let liveTail = true; // are we parked at the bottom (jump-to-live state)?
  const MAX_EVENTS = 5000; // bounded retention

  // Filter state (UI-driven).
  const filters = {
    severity: 'ALL',
    family: 'ALL',
    strategy: '',
    eventType: 'ALL',
    generation: 'ALL',
    timeFrom: '', // ISO or '' = unbounded
    timeTo: '',
  };

  // ----------------------------------------------------------------------
  // EVENT CLASSIFICATION — distinct, so a GENERATION_SWEPT never looks like a
  // WALK_FORWARD_FAILURE. Classification is driven by the backend event_type
  // where it exists, mapped honestly; we never invent types.
  // ----------------------------------------------------------------------
  const SEVERITY_OF = {
    RESEARCH_FAILURE: 'ERROR',
    VALIDATION_FAILURE: 'ERROR',
    WALK_FORWARD_FAILURE: 'ERROR',
    OOS_FAILURE: 'ERROR',
    DATA_FAILURE: 'ERROR',
    SYSTEM_ERROR: 'ERROR',
    STALE_RUN_RECOVERY: 'INFO',
    GENERATION_COMPLETED: 'INFO',
    LIFECYCLE_TRANSITION: 'INFO',
    EXPECTED_REJECTION: 'WARN',
  };

  // Visual signature per class: [color, glyph, label]. Glyphs differ so the
  // classes are distinguishable even at a glance (not colour-only).
  const CLASS_SIG = {
    RESEARCH_FAILURE:      ['#f43f5e', '✖', 'RESEARCH FAIL'],
    VALIDATION_FAILURE:    ['#fb7185', '▲', 'VALIDATION FAIL'],
    WALK_FORWARD_FAILURE:  ['#ef4444', '⤢', 'WALK-FWD FAIL'],
    OOS_FAILURE:           ['#f97316', '◳', 'OOS FAIL'],
    DATA_FAILURE:          ['#a855f7', '⛒', 'DATA FAIL'],
    SYSTEM_ERROR:          ['#e11d48', '✷', 'SYSTEM ERR'],
    STALE_RUN_RECOVERY:    ['#22c55e', '↺', 'STALE RECOVER'],
    GENERATION_COMPLETED:  ['#38bdf8', '✦', 'GEN COMPLETE'],
    LIFECYCLE_TRANSITION:  ['#94a3b8', '⇄', 'LIFECYCLE'],
    EXPECTED_REJECTION:    ['#eab308', '⊘', 'EXPECTED REJECT'],
  };

  // Event FAMILY classification (5 mutually-exclusive families). The console
  // distinguishes: CANDIDATE_LIFECYCLE, EVALUATION_PROGRESS, VALIDATION_RESULT,
  // GENERATION_EVENT, SYSTEM_RECOVERY. Family is a coarser, always-present
  // bucket that is independent of the fine-grained class; it never fabricates.
  const CLASS_FAMILY = {
    RESEARCH_FAILURE:      'EVALUATION_PROGRESS',
    VALIDATION_FAILURE:    'EVALUATION_PROGRESS',
    WALK_FORWARD_FAILURE:  'EVALUATION_PROGRESS',
    OOS_FAILURE:           'EVALUATION_PROGRESS',
    DATA_FAILURE:          'EVALUATION_PROGRESS',
    SYSTEM_ERROR:          'SYSTEM_RECOVERY',
    STALE_RUN_RECOVERY:    'SYSTEM_RECOVERY',
    GENERATION_COMPLETED:  'GENERATION_EVENT',
    LIFECYCLE_TRANSITION:  'CANDIDATE_LIFECYCLE',
    EXPECTED_REJECTION:    'VALIDATION_RESULT',
  };
  const FAMILY_ORDER = [
    'CANDIDATE_LIFECYCLE', 'EVALUATION_PROGRESS', 'VALIDATION_RESULT',
    'GENERATION_EVENT', 'SYSTEM_RECOVERY',
  ];
  function familyOf(cls) {
    return CLASS_FAMILY[cls] || 'SYSTEM_RECOVERY';
  }

  function classifyEvent(payload) {
    if (!payload) return 'SYSTEM_ERROR';
    // Prefer the explicit event_type so e.g. GENERATION_SWEPT is classified by
    // its own type, never by incidental substrings elsewhere in the payload.
    const et = String(payload.event_type || payload.type || '').toUpperCase();
    if (et) {
      if (et.includes('SWEPT') || et.includes('STALE')) return 'STALE_RUN_RECOVERY';
      if (et.includes('GENERATION') && (et.includes('COMPLET') || et.includes('DONE') || et.includes('SWEEP')))
        return 'GENERATION_COMPLETED';
      if (et.includes('GENERATION_COMPLETED')) return 'GENERATION_COMPLETED';
      if (et.includes('REJECT') && !et.includes('UNEXPECT')) return 'EXPECTED_REJECTION';
      if (        et.includes('OOS') && et.includes('FAIL')) return 'OOS_FAILURE';
      if (et.includes('WALK') && et.includes('FAIL')) return 'WALK_FORWARD_FAILURE';
      if (et.includes('VALIDATION') && et.includes('FAIL')) return 'VALIDATION_FAILURE';
      if (et.includes('RESEARCH') && (et.includes('FAIL') || et.includes('ERROR'))) return 'RESEARCH_FAILURE';
      if (et.includes('DATA') && (et.includes('FAIL') || et.includes('ERROR'))) return 'DATA_FAILURE';
      if (et.includes('LIFECYCLE') || et.includes('TRANSITION')) return 'LIFECYCLE_TRANSITION';
      if (et.includes('SYSTEM') || et.includes('INFRA') || et.includes('DISK') || et.includes('NETWORK'))
        return 'SYSTEM_ERROR';
      if (et.includes('FAIL') || et.includes('ERROR')) {
        // Map by stage token to keep the failure classes distinct.
        if (et.includes('WALK') || et.includes('OOS') || et.includes('BACKTEST') ||
            et.includes('ROBUST') || et.includes('VALIDAT'))
          return 'VALIDATION_FAILURE';
        if (et.includes('EXEC')) return 'SYSTEM_ERROR';
        if (et.includes('DATA')) return 'DATA_FAILURE';
        if (et.includes('RESEARCH')) return 'RESEARCH_FAILURE';
        return 'SYSTEM_ERROR';
      }
    }
    // Fall back to content scanning only when no usable event_type is present.
    const txt = JSON.stringify(payload || {}).toUpperCase();
    if (txt.includes('OOS') && txt.includes('FAIL')) return 'OOS_FAILURE';
    if (txt.includes('WALK') && txt.includes('FAIL')) return 'WALK_FORWARD_FAILURE';
    if (txt.includes('BACKTEST') && txt.includes('FAIL')) return 'VALIDATION_FAILURE';
    if (txt.includes('SWEEP') || txt.includes('STALE')) return 'STALE_RUN_RECOVERY';
    if (txt.includes('REJECT')) return 'EXPECTED_REJECTION';
    if (txt.includes('RESEARCH') && txt.includes('FAIL')) return 'RESEARCH_FAILURE';
    if (txt.includes('DATA') && txt.includes('FAIL')) return 'DATA_FAILURE';
    if (txt.includes('LIFECYCLE') || txt.includes('TRANSITION')) return 'LIFECYCLE_TRANSITION';
    if (txt.includes('INFRA') || txt.includes('DISK') || txt.includes('NETWORK')) return 'SYSTEM_ERROR';
    return 'SYSTEM_ERROR';
  }

  function parseTime(ev) {
    const raw = ev.timestamp || ev.executed_at || ev.timestamp_iso || '';
    if (!raw) return null;
    const ms = Date.parse(raw.endsWith('Z') || raw.includes('+') ? raw : raw + 'Z');
    return Number.isNaN(ms) ? null : ms;
  }

  function withinTimeRange(ev) {
    if (!filters.timeFrom && !filters.timeTo) return true;
    const t = parseTime(ev);
    if (t === null) return true; // unknown time → keep (never drop on missing data)
    if (filters.timeFrom) {
      const f = Date.parse(filters.timeFrom.endsWith('Z') ? filters.timeFrom : filters.timeFrom + 'Z');
      if (!Number.isNaN(f) && t < f) return false;
    }
    if (filters.timeTo) {
      const u = Date.parse(filters.timeTo.endsWith('Z') ? filters.timeTo : filters.timeTo + 'Z');
      if (!Number.isNaN(u) && t > u) return false;
    }
    return true;
  }

  // Effective severity = explicit field, else the class's canonical severity.
  function effectiveSeverity(ev) {
    if (ev.severity) return String(ev.severity).toUpperCase();
    if (ev._class && SEVERITY_OF[ev._class]) return SEVERITY_OF[ev._class];
    return String(ev._class || '').toUpperCase();
  }

  // Sync filter state from the live DOM controls (safe if an element is absent).
  function syncFiltersFromDOM() {
    const get = (id) => { const el = document.getElementById(id); return el ? el.value : null; };
    const sev = get('scc-console-sev'); filters.severity = (sev && sev.trim()) ? sev : 'ALL';
    const fam = get('scc-console-family'); filters.family = (fam && fam.trim()) ? fam : 'ALL';
    const type = get('scc-console-type'); filters.eventType = (type && type.trim()) ? type : 'ALL';
    const strat = get('scc-console-strategy'); filters.strategy = (strat && strat.trim()) ? strat : '';
    const gen = get('scc-console-gen'); filters.generation = (gen && gen.trim()) ? gen : 'ALL';
    const from = get('scc-console-from'); filters.timeFrom = (from && from.trim()) ? from : '';
    const to = get('scc-console-to'); filters.timeTo = (to && to.trim()) ? to : '';
  }

  function matchesFilters(ev) {
    if (filters.severity !== 'ALL') {
      const sev = effectiveSeverity(ev);
      if (sev !== filters.severity.toUpperCase() && ev._class !== filters.severity) return false;
    }
    if (filters.family !== 'ALL' && (ev._family || familyOf(ev._class)) !== filters.family) return false;
    if (filters.eventType !== 'ALL' && ev._class !== filters.eventType) return false;
    if (filters.strategy && (ev.strategy_id || '').toUpperCase() !== filters.strategy.toUpperCase()) return false;
    if (filters.generation !== 'ALL' && String(ev.generation || '?') !== filters.generation) return false;
    return withinTimeRange(ev);
  }

  // ----------------------------------------------------------------------
  // RENDER — event rows
  // ----------------------------------------------------------------------
  function buildRow(ev, prepend) {
    const tbody = document.getElementById('scc-console-body');
    if (!tbody) return;
    const sig = CLASS_SIG[ev._class] || ['#94a3b8', '·', ev._class];
    const [color, glyph, label] = sig;
    const ts = (ev.timestamp || (ev.executed_at || '').slice(11, 23) || new Date().toISOString().slice(11, 23));
    const sid = ev.strategy_id || '';
    const gen = ev.generation != null && ev.generation !== '' ? ('G' + ev.generation) : '—';
    const lc = ev.lifecycle || (ev.to_state || ev.current_state || '');
    const src = ev.source || ev.actor || ev.event_type || '';
    const corr = ev.correlation_id || '';
    const msg = ev.message || ev.reason || ev.run_outcome || ev.detail || '';
    const family = ev._family || familyOf(ev._class);

    const row = document.createElement('div');
    row.className = 'scc-event-row border-b border-borderClr py-1 px-2 font-mono text-[10px] flex gap-2 items-start';
    if (row.dataset) {
      row.dataset._class = ev._class;
      row.dataset.family = family;
      row.dataset.strategy = sid;
      row.dataset.generation = ev.generation != null ? String(ev.generation) : '';
      row.dataset.severity = ev.severity || ev._class;
    }
    row.style.color = color;
    row.innerHTML =
      `<span class="opacity-50 whitespace-nowrap">${ts}</span>` +
      `<span class="font-bold whitespace-nowrap" title="${ev._class}">${glyph} ${label}</span>` +
      `<span class="px-1 rounded bg-slate-800/80 text-slate-400 whitespace-nowrap" title="event family">${family}</span>` +
      (sid ? `<span class="text-accentCyan cursor-pointer underline decoration-dotted" onclick="window.NX.console.action('inspect','${sid}','${corr}')">${sid}</span>` : '') +
      `<span class="text-gray-400 flex-1 truncate" title="${msg}">${msg}</span>` +
      (gen !== '—' ? `<span class="opacity-70 whitespace-nowrap">${gen}</span>` : '') +
      (lc ? `<span class="opacity-70 whitespace-nowrap">${lc}</span>` : '') +
      (src ? `<span class="opacity-50 whitespace-nowrap hidden xl:inline">${src}</span>` : '') +
      (corr ? `<span class="text-slate-500 cursor-pointer" title="copy correlation id" onclick="window.NX.console.action('copy','${sid}','${corr}')">${corr.slice(0, 8)}</span>` : '');

    if (prepend) tbody.insertBefore(row, tbody.firstChild);
    else tbody.appendChild(row);
  }

  function addEvent(ev) {
    if (paused) return;
    const enriched = Object.assign({}, ev, { _class: classifyEvent(ev) });
    enriched._family = familyOf(enriched._class);
    events.push(enriched);
    if (events.length > MAX_EVENTS) events.shift();
    if (matchesFilters(enriched)) {
      buildRow(enriched, !liveTail);
      if (autoScroll && liveTail) scrollToLive();
    }
  }

  function scrollToLive() {
    const body = document.getElementById('scc-console-body');
    if (body) body.scrollTop = body.scrollHeight;
  }

  // ----------------------------------------------------------------------
  // FILTERING (re-render from in-memory events; never mutate authoritative data)
  // ----------------------------------------------------------------------
  function applyFilters() {
    syncFiltersFromDOM();
    const tbody = document.getElementById('scc-console-body');
    if (!tbody) return;
    tbody.innerHTML = '';
    const visible = events.filter(matchesFilters);
    for (const ev of visible) buildRow(ev, false);
    if (autoScroll && liveTail) scrollToLive();
  }

  // ----------------------------------------------------------------------
  // CLICK ACTIONS
  // ----------------------------------------------------------------------
  function action(kind, strategyId, correlationId) {
    if (!strategyId) return;
    if (kind === 'copy') {
      if (correlationId && navigator.clipboard) navigator.clipboard.writeText(correlationId);
      return;
    }
    if (kind === 'inspect' && window.NX && window.NX.scc) {
      window.NX.scc.inspect(strategyId);
      return;
    }
    if (kind === 'focus' && window.NX && window.NX.spatial) {
      window.NX.spatial.select(strategyId);
      window.NX.spatial.focusSelected();
      return;
    }
    if (kind === 'timeline' && window.NX && window.NX.scc) {
      window.NX.scc.openTimeline && window.NX.scc.openTimeline(strategyId);
      return;
    }
    if (kind === 'evidence' && window.NX && window.NX.scc) {
      window.NX.scc.inspect(strategyId); // evidence lives inside the inspector
    }
  }

  // ----------------------------------------------------------------------
  // CONTROLS
  // ----------------------------------------------------------------------
  function togglePause() {
    paused = !paused;
    const btn = document.getElementById('scc-console-pause');
    if (btn) btn.textContent = paused ? 'RESUME' : 'PAUSE';
  }

  function clear() {
    events = [];
    const tbody = document.getElementById('scc-console-body');
    if (tbody) tbody.innerHTML = '';
  }

  function setAutoScroll(on) {
    autoScroll = !!on;
    const btn = document.getElementById('scc-console-autoscroll');
    if (btn) btn.textContent = autoScroll ? 'AUTO-SCROLL: ON' : 'AUTO-SCROLL: OFF';
    if (autoScroll) { liveTail = true; scrollToLive(); }
  }

  function jumpToLive() {
    liveTail = true;
    scrollToLive();
    const btn = document.getElementById('scc-console-live');
    if (btn) btn.classList.remove('ring-2', 'ring-accentCyan');
  }

  function detectTailScroll() {
    const body = document.getElementById('scc-console-body');
    if (!body) return;
    const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 24;
    if (!atBottom && liveTail) {
      liveTail = false;
      const btn = document.getElementById('scc-console-live');
      if (btn) btn.classList.add('ring-2', 'ring-accentCyan');
    } else if (atBottom && !liveTail) {
      liveTail = true;
    }
  }

  // ----------------------------------------------------------------------
  // BOTTLENECK VISUALIZATION — derived from REAL /fleet evidence data.
  // Per spec, four metrics: Backtest, Walk-forward, OOS, Robustness.
  // Each shows pass rate, failure rate, count, current generation, lifetime.
  // Scope is ALWAYS displayed (CURRENT / GENERATION / LIFETIME / HISTORICAL).
  // ----------------------------------------------------------------------
  let bottleneckScope = 'LIFETIME'; // only honest scopes we can compute from fleet

  function computeBottleneck(fleetRows) {
    const metrics = {
      BACKTEST:    { pass: 0, fail: 0, total: 0, gens: new Set() },
      WALK_FORWARD:{ pass: 0, fail: 0, total: 0, gens: new Set() },
      OOS:         { pass: 0, fail: 0, total: 0, gens: new Set() },
      ROBUSTNESS:  { pass: 0, fail: 0, total: 0, gens: new Set() },
    };
    for (const r of fleetRows || []) {
      const ev = r.evidence || {};
      for (const key of ['BACKTEST', 'WALK_FORWARD', 'OOS', 'ROBUSTNESS']) {
        const st = (ev['evidence_status_' + key.toLowerCase()] || '').toUpperCase();
        if (!st || st === 'MISSING' || st === 'NOT_RUN') continue;
        const m = metrics[key];
        m.total += 1;
        if (r.generation != null) m.gens.add(r.generation);
        if (st === 'PASS') m.pass += 1; else if (st === 'FAIL') m.fail += 1;
      }
    }
    // Identify the bottleneck: highest failure rate among metrics with data,
    // weighted by volume so a 100% failure on n=1 is not overstated.
    let worst = null;
    for (const key of Object.keys(metrics)) {
      const m = metrics[key];
      if (m.total === 0) continue;
      const failRate = m.fail / m.total;
      const score = failRate * Math.sqrt(m.total); // volume-weighted severity
      if (!worst || score > worst.score) worst = { key, failRate, total: m.total, score };
    }
    return { metrics, worst };
  }

  function renderBottleneck(rows) {
    const el = document.getElementById('scc-bottleneck');
    if (!el) return;
    const { metrics, worst } = computeBottleneck(rows);
    const fmtPct = (n) => Math.round(n * 100) + '%';
    let html = `<div class="px-3 py-2 border-b border-borderClr">` +
      `<div class="flex items-center justify-between"><p class="text-[10px] font-bold text-textMuted tracking-wider">BOTTLENECK · SCOPE: ${bottleneckScope}</p>` +
      `<span class="text-[9px] text-textMuted">real /fleet evidence</span></div>`;
    for (const key of ['BACKTEST', 'WALK_FORWARD', 'OOS', 'ROBUSTNESS']) {
      const m = metrics[key];
      const total = m.total;
      const passRate = total ? m.pass / total : 0;
      const failRate = total ? m.fail / total : 0;
      const isWorst = worst && worst.key === key;
      const gens = Array.from(m.gens).sort().join(', ') || '—';
      const barColor = !total ? '#475569' : (failRate > 0.5 ? '#ef4444' : failRate > 0.2 ? '#eab308' : '#22c55e');
      html += `<div class="mt-1.5 ${isWorst ? 'bg-rose-500/10 rounded px-1' : ''}">` +
        `<div class="flex justify-between text-[10px]"><span class="${isWorst ? 'text-rose-400 font-bold' : 'text-gray-300'}">${key}${isWorst ? ' ◀ BOTTLENECK' : ''}</span>` +
        `<span class="font-mono text-gray-400">${total ? fmtPct(passRate * 100) : '—'} pass · ${total ? fmtPct(failRate * 100) : '—'} fail · n=${total} · gen:${gens}</span></div>` +
        `<div class="h-1.5 bg-darkBg rounded mt-0.5 overflow-hidden"><div style="width:${total ? (passRate * 100) : 0}%;background:${barColor};height:100%"></div></div>` +
        `</div>`;
    }
    html += `</div>`;
    el.innerHTML = html;
  }

  // Populate the strategy <select> for the console filter from the fleet.
  function populateStrategyFilter(rows) {
    const sel = document.getElementById('scc-console-strategy');
    if (!sel) return;
    const current = filters.strategy;
    const ids = Array.from(new Set((rows || []).map(r => r.strategy_id).filter(Boolean))).sort();
    sel.innerHTML = '<option value="">ALL STRATEGIES</option>' +
      ids.map(id => `<option value="${id}">${id}</option>`).join('');
    sel.value = current;
  }

  function populateGenerationFilter(rows) {
    const sel = document.getElementById('scc-console-gen');
    if (!sel) return;
    const gens = Array.from(new Set((rows || []).map(r => r.generation).filter(g => g != null && g !== ''))).sort();
    sel.innerHTML = '<option value="ALL">ALL GENERATIONS</option>' +
      gens.map(g => `<option value="${g}">GEN ${g}</option>`).join('');
  }

  // Called by ui.js after each fleet load so the console shares one real source.
  function setFleetContext(rows) {
    renderBottleneck(rows);
    populateStrategyFilter(rows);
    populateGenerationFilter(rows);
  }

  function setScope(scope) {
    bottleneckScope = scope;
    // Re-render if we already have fleet context.
    if (window.NX && window.NX.scc && window.NX.scc._test_getLastFleet) {
      setFleetContext(window.NX.scc._test_getLastFleet());
    }
  }

  function pushFrame(frameConsoleEvents) {
    clear();
    for (const ev of (frameConsoleEvents || [])) {
      addEvent(ev);
    }
  }

  window.NX = window.NX || {};
  window.NX.console = {
    add: addEvent,
    pushFrame,
    clear,
    togglePause,
    setAutoScroll,
    jumpToLive,
    applyFilters,
    setFleetContext,
    setScope,
    action,
    detectTailScroll,
    getEvents: () => events,
    classifyEvent,           // exposed for tests
    computeBottleneck,       // exposed for tests
    familyOf,                // exposed for tests
    FAMILY_ORDER,            // exposed for tests
    SEVERITY_OF,             // exposed for tests
    CLASS_SIG,               // exposed for tests
  };

  // Wire console-scroll detection once the DOM exists.
  if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
      const body = document.getElementById('scc-console-body');
      if (body) body.addEventListener('scroll', () => detectTailScroll());
    });
  }
})();
