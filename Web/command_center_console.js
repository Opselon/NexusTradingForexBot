/* =========================================================================
 * NEXUS COMMAND CENTER — Live Event Stream + Debug Console (Frontend)
 * -------------------------------------------------------------------------
 * Consumes /api/command-center/timeline/{id} and the live SSE feed, rendering
 * structured diagnostic rows with severity classification, filtering,
 * correlation ID tracking, and inspector linkage. Events drive node
 * animation in the spatial view (see command_center_spatial.js).
 * ========================================================================= */

(function () {
  'use strict';

  let events = [];
  let paused = false;
  const MAX_EVENTS = 5000; // bounded retention

  function classifyError(payload) {
    if (!payload) return 'SYSTEM';
    // Check event_type FIRST so e.g. GENERATION_SWEPT is classified by its own
    // type, not by incidental substrings elsewhere in the payload.
    const et = String(payload.event_type || payload.type || '').toUpperCase();
    if (et.includes('SWEPT') || et.includes('STALE')) return 'STALE_RUN_RECOVERY';
    if (et.includes('REJECT')) return 'EXPECTED_REJECTION';
    if (et.includes('FAILURE') || et.includes('FAIL')) {
      if (et.includes('WALK') || et.includes('OOS') || et.includes('BACKTEST') ||
          et.includes('ROBUST') || et.includes('VALIDATION') || et.includes('VALIDAT')) {
        return 'VALIDATION_FAILURE';
      }
      if (et.includes('EXEC')) return 'EXECUTION_FAILURE';
      if (et.includes('DATA')) return 'DATA_FAILURE';
      if (et.includes('RESEARCH')) return 'RESEARCH_FAILURE';
      return 'SYSTEM_ERROR';
    }
    if (et.includes('EXEC')) return 'EXECUTION_FAILURE';
    const txt = JSON.stringify(payload).toUpperCase();
    if (txt.includes('OOS')) return 'VALIDATION_FAILURE';
    if (txt.includes('WALK') || txt.includes('WALKFORWARD')) return 'VALIDATION_FAILURE';
    if (txt.includes('BACKTEST')) return 'VALIDATION_FAILURE';
    if (txt.includes('SWEEP') || txt.includes('STALE')) return 'STALE_RUN_RECOVERY';
    if (txt.includes('REJECT')) return 'EXPECTED_REJECTION';
    if (txt.includes('EXEC')) return 'EXECUTION_FAILURE';
    if (txt.includes('INFRA') || txt.includes('DISK') || txt.includes('NETWORK')) return 'INFRASTRUCTURE_FAILURE';
    if (txt.includes('USER') || txt.includes('OPERATOR')) return 'USER_ACTION';
    return 'SYSTEM_ERROR';
  }

  function addEvent(ev) {
    if (paused) return;
    const enriched = Object.assign({}, ev, { _class: classifyError(ev) });
    events.push(enriched);
    if (events.length > MAX_EVENTS) events.shift();
    renderEventRow(enriched, true);
    if (window.NX && window.NX.spatial && ev.strategy_id) {
      // A lifecycle transition event: force a spatial refresh to animate movement
      if (ev.event_type === 'LIFECYCLE_TRANSITION' && window.NX.scc) {
        window.NX.scc.load();
      }
    }
  }

  function renderEventRow(ev, prepend) {
    const tbody = document.getElementById('scc-console-body');
    if (!tbody) return;
    const sev = (ev.severity || ev._class || 'INFO');
    const color = sev.includes('FAIL') || sev.includes('REJECT') ? '#f43f5e'
      : sev.includes('RECOVER') || sev.includes('STALE') ? '#22c55e'
      : sev.includes('WARN') ? '#eab308'
      : '#94a3b8';
    const row = document.createElement('div');
    row.className = 'scc-event-row border-b border-borderClr py-1 px-2 font-mono text-[10px] flex gap-2';
    row.style.color = color;
    row.innerHTML =
      `<span class="opacity-60">${ev.timestamp || new Date().toISOString().slice(11, 23)}</span>` +
      `<span class="font-bold">[${sev}]</span>` +
      `<span class="text-white">${ev.event_type || ev.type || ev._class}</span>` +
      (ev.strategy_id ? `<span class="text-accentCyan cursor-pointer" onclick="window.NX.scc.inspect('${ev.strategy_id}')">${ev.strategy_id}</span>` : '') +
      `<span class="text-gray-400 flex-1 truncate">${ev.message || ev.reason || ''}</span>` +
      (ev.correlation_id ? `<span class="text-slate-500 cursor-pointer" title="copy" onclick="navigator.clipboard.writeText('${ev.correlation_id}')">${ev.correlation_id.slice(0, 8)}</span>` : '');
    if (prepend) tbody.insertBefore(row, tbody.firstChild);
    else tbody.appendChild(row);
  }

  function clear() {
    events = [];
    const tbody = document.getElementById('scc-console-body');
    if (tbody) tbody.innerHTML = '';
  }

  function applyFilters() {
    const sev = (document.getElementById('scc-console-sev') || {}).value || 'ALL';
    const txt = ((document.getElementById('scc-console-search') || {}).value || '').toLowerCase();
    const tbody = document.getElementById('scc-console-body');
    if (!tbody) return;
    tbody.querySelectorAll('.scc-event-row').forEach(row => {
      const matchSev = sev === 'ALL' || row.textContent.includes(`[${sev}]`);
      const matchTxt = !txt || row.textContent.toLowerCase().includes(txt);
      row.style.display = matchSev && matchTxt ? '' : 'none';
    });
  }

  function togglePause() {
    paused = !paused;
    const btn = document.getElementById('scc-console-pause');
    if (btn) btn.textContent = paused ? 'RESUME' : 'PAUSE';
  }

  window.NX = window.NX || {};
  window.NX.console = {
    add: addEvent,
    clear,
    togglePause,
    applyFilters,
    getEvents: () => events,
  };
})();
