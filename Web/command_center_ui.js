/* =========================================================================
 * NEXUS COMMAND CENTER — Spatial 2.5D Renderer & UI Controller
 * -------------------------------------------------------------------------
 * Consumes /api/command-center/* endpoints and renders:
 *   - Spatial 2.5D lifecycle zones and strategy nodes
 *   - Strategy inspector drawer / modal with DNA, AI attribution, validation gates, debug hints
 *   - Fleet table and global filters
 *   - Historical time machine playback controller
 *   - Diagnostic debug console with event streaming
 * ========================================================================= */

(function () {
  'use strict';

  let currentSpatialData = null;
  let selectedStrategyId = null;
  let fleetRows = [];
  let overviewData = null;
  let reloadToken = 0; // guards against stale async responses after refresh/filter change

  async function loadCommandCenter() {
    if (!window.NX || !window.NX.api) return;
    const myToken = ++reloadToken;
    try {
      const [ovRes, fleetRes, spatialRes] = await Promise.all([
        window.NX.api.get('/api/command-center/overview', { component: 'scc', action: 'overview' }),
        window.NX.api.get('/api/command-center/fleet', { component: 'scc', action: 'fleet' }),
        window.NX.api.get('/api/command-center/spatial', { component: 'scc', action: 'spatial' }),
      ]);

      // Stale-response guard: a newer load() superseded this one. (Granted by
      // backend being authoritative — never apply a superseded snapshot.)
      if (myToken !== reloadToken) return;

      if (ovRes.ok) {
        overviewData = ovRes.body;
        renderOverview(overviewData);
      }
      if (fleetRes.ok) {
        fleetRows = fleetRes.body.rows || [];
        renderFleetTable(fleetRows);
        // Share the authoritative fleet payload with the debug console so its
        // bottleneck visualization and filters use REAL data (no fabrication).
        if (window.NX && window.NX.console && window.NX.console.setFleetContext) {
          window.NX.console.setFleetContext(fleetRows);
        }
      }
      if (spatialRes.ok) {
        currentSpatialData = spatialRes.body;
        // Push the authoritative payload straight to the renderer. The renderer
        // is the single source of truth for drawing; the old canvas stub here is
        // removed to avoid double-draw conflict.
        if (window.NX.spatial) window.NX.spatial.update(currentSpatialData);
        window.__lastSpatialPayload = currentSpatialData;
      }
    } catch (err) {
      console.warn('[SCC] load failed', err);
    }
  }

  function renderOverview(ov) {
    const totalEl = document.getElementById('scc-total');
    const activeEl = document.getElementById('scc-active');
    const blockedEl = document.getElementById('scc-blocked');
    const validEl = document.getElementById('scc-valid');
    if (totalEl) totalEl.textContent = ov.total_strategies || 0;
    if (activeEl) activeEl.textContent = (ov.by_lifecycle && ov.by_lifecycle.ACTIVE) || 0;
    if (blockedEl) blockedEl.textContent = ov.blocked_count || 0;
    if (validEl) validEl.textContent = (ov.by_lifecycle && ov.by_lifecycle.VALIDATED) || 0;
  }

  function renderFleetTable(rows) {
    const tbody = document.getElementById('scc-fleet-tbody');
    if (!tbody) return;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-6 text-center text-textMuted text-xs">No strategies found in authoritative registry.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(r => `
      <tr class="border-b border-borderClr hover:bg-darkBg/60 transition cursor-pointer" onclick="window.NX.scc.inspect('${r.strategy_id}')">
        <td class="px-4 py-3 font-mono font-bold text-accentCyan">${r.strategy_id}</td>
        <td class="px-4 py-3"><span class="px-2 py-0.5 rounded text-[10px] font-black bg-slate-800 text-gray-200 border border-borderClr">${r.lifecycle}</span></td>
        <td class="px-4 py-3 font-mono">${r.health_final !== null && r.health_final !== undefined ? Math.round(r.health_final * 100) + '%' : '—'}</td>
        <td class="px-4 py-3">
          <span class="px-2 py-0.5 rounded text-[10px] font-bold ${r.eligibility_state === 'YES' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30' : 'bg-rose-500/10 text-rose-400 border border-rose-500/30'}">
            ${r.eligibility_state}
          </span>
        </td>
        <td class="px-4 py-3 text-xs text-gray-300 font-mono">${r.sample_count} samples</td>
        <td class="px-4 py-3 text-right">
          <button class="px-2.5 py-1 rounded bg-accentCyan/10 text-accentCyan border border-accentCyan/30 text-xs font-bold hover:bg-accentCyan/20 transition">Inspect</button>
        </td>
      </tr>
    `).join('');
  }

  // Wire camera + filter controls once the DOM is live (called from onShow).
  function wireSpatialControls() {
    if (window.__sccControlsWired) return;
    window.__sccControlsWired = true;

    const SP = () => window.NX.spatial;
    const bind = (id, fn) => {
      const el = document.getElementById(id);
      if (el) el.onclick = fn;
    };
    bind('scc-fit-all', () => SP() && SP().fitAll());
    bind('scc-reset-cam', () => SP() && SP().resetCamera());
    bind('scc-focus-selected', () => SP() && SP().focusSelected());
    bind('scc-focus-stage', () => SP() && SP().focusStage());
    bind('scc-focus-blocked', () => SP() && SP().focusBlocked());
    bind('scc-focus-active', () => SP() && SP().focusActive());

    const filter = document.getElementById('scc-lifecycle-filter');
    if (filter) {
      filter.onchange = () => applyLifecycleFilter(filter.value);
    }
  }

  // Honest empty-state overlay for the spatial canvas (no fabricated counts).
  function showSpatialEmptyState(backendTotal, filterLabel, matching) {
    const el = document.getElementById('scc-spatial-empty');
    if (!el) return;
    el.classList.remove('hidden');
    el.innerHTML =
      '<div class="text-center px-6">' +
        '<p class="text-lg font-black text-rose-400 tracking-wide">NO VISIBLE STRATEGIES</p>' +
        '<p class="text-xs text-textMuted mt-2 font-mono">Backend strategies: ' + backendTotal + '</p>' +
        '<p class="text-xs text-textMuted font-mono">Current filter: ' + (filterLabel || 'ALL') + '</p>' +
        '<p class="text-xs text-textMuted font-mono">Matching: ' + matching + '</p>' +
      '</div>';
  }

  function hideSpatialEmptyState() {
    const el = document.getElementById('scc-spatial-empty');
    if (el) el.classList.add('hidden');
  }

  // Re-derive the visible node set from the last authoritative payload applying
  // the selected lifecycle filter, then feed the renderer and show empty state
  // if nothing matches.
  function applyLifecycleFilter(filterValue) {
    if (!currentSpatialData) return;
    if (filterValue && filterValue !== 'ALL') {
      const filtered = {
        ...currentSpatialData,
        nodes: (currentSpatialData.nodes || []).filter(n => n.zone === filterValue),
        zones: (currentSpatialData.zones || []).map(z =>
          z.zone === filterValue ? z : { ...z, count: 0 }),
      };
      if (window.NX.spatial) window.NX.spatial.update(filtered);
      const matching = (filtered.nodes || []).length;
      const backendTotal = (currentSpatialData.meta && currentSpatialData.meta.total_nodes)
        || (currentSpatialData.nodes || []).length;
      if (!matching) showSpatialEmptyState(backendTotal, filterValue, 0);
      else hideSpatialEmptyState();
      // After a filter the visible extent changed → fit-all to the subset.
      if (window.NX.spatial) setTimeout(() => window.NX.spatial.fitAll(), 30);
    } else {
      if (window.NX.spatial) window.NX.spatial.update(currentSpatialData);
      hideSpatialEmptyState();
      if (window.NX.spatial) setTimeout(() => window.NX.spatial.fitAll(), 30);
    }
  }

  // Called by the dashboard tab switch when CC becomes visible.
  async function onShowCommandCenter() {
    wireSpatialControls();
    if (window.NX && window.NX.spatial) {
      // Canvas becomes sized only once it is in the visible layout tree.
      window.NX.spatial.init('scc-spatial-canvas');
    }
    await loadCommandCenter();
    // INITIAL VIEW: fetch real fleet, AUTO FIT ALL so nodes are never off-viewport.
    // Backend may return up to its current cap (500 now, 1165 later) — never
    // hardcode the count.
    if (window.NX.spatial) {
      const fitted = window.NX.spatial.fitAll();
      const backendTotal = currentSpatialData && currentSpatialData.nodes
        ? currentSpatialData.nodes.length : 0;
      if (!fitted) {
        // No visible nodes at all → explicit empty state.
        showSpatialEmptyState(backendTotal || '—', 'ALL', 0);
      } else {
        hideSpatialEmptyState();
      }
    }
  }

  async function inspectStrategy(strategyId) {
    selectedStrategyId = strategyId;
    const drawer = document.getElementById('scc-inspector-drawer');
    if (drawer) drawer.classList.remove('translate-x-full');

    try {
      const res = await window.NX.api.get(`/api/command-center/inspector/${strategyId}`, { component: 'scc', action: 'inspector' });
      if (res.ok && res.body.available) {
        renderInspector(res.body);
      }
    } catch (err) {
      console.warn('[SCC] inspector load failed', err);
    }
  }

  function renderInspector(data) {
    const title = document.getElementById('scc-insp-title');
    const content = document.getElementById('scc-insp-content');
    if (title) title.textContent = `Strategy Inspector: ${data.strategy_id}`;
    if (!content) return;

    const ee = data.execution_eligibility || {};
    const health = data.health_score || {};
    const ev = data.evidence_summary || {};
    const attr = data.ai_attribution || {};
    const dbg = data.debug_intelligence || {};
    const comp = data.evidence_completeness || {};
    const dna = data.lineage_dna || {};
    const events = data.events || [];

    // --- CAN THIS STRATEGY TRADE? verdict banner (NEVER faked) ---
    // Source of truth is the REAL backend execution-eligibility response. If the
    // backend returned no eligibility, we honestly show UNKNOWN.
    const VERDICT = ee.eligibility_state || 'UNKNOWN';
    const VERDICT_STYLE = {
      YES: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40',
      SHADOW_ONLY: 'bg-yellow-500/15 text-yellow-300 border-yellow-500/40',
      NO: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
      BLOCKED: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
      UNKNOWN: 'bg-slate-700/40 text-slate-300 border-slate-500/40',
    }[VERDICT] || 'bg-slate-700/40 text-slate-300 border-slate-500/40';

    const canTradeHtml = `
      <div class="rounded-lg border ${VERDICT_STYLE} px-3 py-2 mb-3">
        <div class="flex items-center justify-between">
          <span class="text-[9px] font-black tracking-widest text-textMuted">CAN THIS STRATEGY TRADE</span>
          <span class="text-sm font-black">${VERDICT}</span>
        </div>
        <p class="text-[11px] mt-1">${ee.reason || 'No eligibility verdict returned by backend.'}</p>
        ${ee.required_gate ? `<p class="text-[10px] text-textMuted mt-0.5">required gate: ${ee.required_gate}</p>` : ''}
        ${Array.isArray(ee.blockers) && ee.blockers.length ? `<p class="text-[10px] text-rose-300/80 mt-0.5">blockers: ${ee.blockers.join(', ')}</p>` : ''}
      </div>`;

    // --- Evidence completeness (honest) ---
    const evidenceVerdict = comp.verdict || (ev.backtest_status || 'MISSING');
    const evCompleteHtml = `
      <div class="bg-darkBg p-3 rounded border border-borderClr">
        <p class="font-bold text-accentCyan mb-1">Evidence & Gates <span class="text-[10px] font-normal text-textMuted">(${evidenceVerdict})</span></p>
        <div class="grid grid-cols-2 gap-1 text-gray-300">
          <div>Backtest: <span class="font-mono text-white">${ev.backtest_status || 'MISSING'}</span></div>
          <div>Walk-Forward: <span class="font-mono text-white">${ev.walkforward_status || 'MISSING'}</span></div>
          <div>OOS: <span class="font-mono text-white">${ev.oos_status || 'MISSING'}</span></div>
          <div>Robustness: <span class="font-mono text-white">${ev.robustness_status || 'MISSING'}</span></div>
          <div>Score Verdict: <span class="font-mono text-white">${ev.score_verdict || 'MISSING'}</span></div>
          ${comp.missing ? `<div class="col-span-2 text-[10px] text-rose-300/80">missing: ${comp.missing.join(', ')}</div>` : ''}
        </div>
      </div>`;

    // --- AI Explainability (honest attribution only) ---
    const ATTR_STATUS = attr.status || 'UNAVAILABLE';
    const attrStatusLabel = {
      MEASURED: 'MEASURED',
      PARTIALLY_MEASURABLE: 'PARTIALLY MEASURED',
      NOT_AVAILABLE: 'UNAVAILABLE',
      UNAVAILABLE: 'UNAVAILABLE',
    }[ATTR_STATUS] || 'UNAVAILABLE';
    const measuredCount = (attr.measured && attr.measured.weights) || 0;
    const attrHtml = `
      <div class="bg-darkBg p-3 rounded border border-borderClr">
        <p class="font-bold text-accentCyan mb-1">AI Explainability (${attrStatusLabel})</p>
        ${measuredCount === 0 ? `<p class="text-[11px] text-amber-300/90 mb-2">NOT MEASURED — no numeric influence weights recorded for this decision.</p>`
          : `<p class="text-[11px] text-textMuted mb-2">${attr.measured && attr.measured.note ? attr.measured.note : ''}</p>`}
        <div class="space-y-1">
          ${(attr.contributions && attr.contributions.length)
            ? attr.contributions.map(c => `<div class="bg-panelBg p-1.5 rounded font-mono text-[10px] text-gray-300"><b>${c.source_type}</b>: ${c.kind}${c.weight_measured ? ` (w=${c.weight})` : ' (weight NOT measured)'}</div>`).join('')
            : '<div class="text-[10px] text-textMuted">No attribution records.</div>'}
        </div>
        ${(attr.timeline && attr.timeline.length)
          ? `<p class="text-[10px] text-textMuted mt-2">Timeline entries: ${attr.timeline.length}</p>` : ''}
      </div>`;

    // --- Debug Intelligence: strictly FACT / INFERENCE / HYPOTHESIS / RECOMMENDATION ---
    const CATEGORY_STYLE = {
      FACT: 'text-sky-300 border-sky-500/40',
      INFERENCE: 'text-violet-300 border-violet-500/40',
      HYPOTHESIS: 'text-amber-300 border-amber-500/40',
      RECOMMENDATION: 'text-emerald-300 border-emerald-500/40',
    };
    const hintsHtml = `
      <div class="bg-darkBg p-3 rounded border border-borderClr">
        <p class="font-bold text-accentCyan mb-1">Debug Intelligence</p>
        <div class="space-y-2">
          <div>Anomaly Score: <span class="font-mono text-white">${dbg.anomaly_score ? dbg.anomaly_score.anomaly_score : 0}</span></div>
          <div class="space-y-1 mt-1">
            ${(dbg.hints && dbg.hints.length)
              ? dbg.hints.map(h => {
                  const st = CATEGORY_STYLE[h.category] || 'text-gray-300 border-borderClr';
                  return `<div class="p-1.5 rounded bg-slate-800/80 border ${st} text-[10px]"><span class="font-black">${h.category}</span>: ${h.message}</div>`;
                }).join('')
              : '<div class="text-[10px] text-textMuted">No debug hints.</div>'}
          </div>
        </div>
      </div>`;

    // --- Strategy DNA / Lineage (honest; no invented descendants) ---
    const parents = (dna.parent_strategy_ids && dna.parent_strategy_ids.length)
      ? dna.parent_strategy_ids.join(', ') : '— (genesis / no recorded parent)';
    const dnaHtml = `
      <div class="bg-darkBg p-3 rounded border border-borderClr">
        <p class="font-bold text-accentCyan mb-1">Strategy DNA <span class="text-[10px] font-normal text-textMuted">(${dna.descendants_recorded ? 'LINEAGE RECORDED' : 'LINEAGE PARTIALLY RECORDED'})</span></p>
        <div class="grid grid-cols-2 gap-1 text-gray-300 text-[11px]">
          <div>Generation: <span class="font-mono text-white">${dna.generation || '—'}</span></div>
          <div>Parents: <span class="font-mono text-white">${parents}</span></div>
          <div class="col-span-2 text-[10px] text-amber-300/80">Descendants: not enumerated (registry-wide scan out of inspector scope)</div>
        </div>
      </div>`;

    // --- Identity & State ---
    const identityHtml = `
      <div class="bg-darkBg p-3 rounded border border-borderClr">
        <p class="font-bold text-accentCyan mb-1">Identity & State</p>
        <div class="grid grid-cols-2 gap-2 text-gray-300 font-mono text-[11px]">
          <div>State: <span class="text-white">${data.current_state}</span></div>
          <div>Version: <span class="text-white">${data.strategy_version}</span></div>
          <div>Eligibility: <span class="${ee.can_trade ? 'text-emerald-400' : 'text-rose-400'}">${ee.eligibility_state || 'UNKNOWN'}</span></div>
          <div>Confidence: <span class="text-white">${Math.round((data.confidence_score || 0) * 100)}%</span></div>
        </div>
        <p class="mt-2 text-[11px] text-textMuted">${ee.reason || ''}</p>
      </div>`;

    // --- Recent events (honest, derived from backend projection) ---
    const eventsHtml = `
      <div class="bg-darkBg p-3 rounded border border-borderClr">
        <p class="font-bold text-accentCyan mb-1">Recent Events (${events.length})</p>
        <div class="space-y-1">
          ${events.length ? events.slice(-20).reverse().map(e => `<div class="text-[10px] font-mono text-gray-400">${e.timestamp || e.executed_at || ''} · ${(e.event_type || e.decision || 'EVENT')}</div>`).join('') : '<div class="text-[10px] text-textMuted">No recorded events.</div>'}
        </div>
      </div>`;

    content.innerHTML = `
      <div class="space-y-3 text-xs">
        ${canTradeHtml}
        ${identityHtml}
        ${evCompleteHtml}
        ${attrHtml}
        ${hintsHtml}
        ${dnaHtml}
        ${eventsHtml}
      </div>
    `;
  }

  function closeInspector() {
    selectedStrategyId = null;
    const drawer = document.getElementById('scc-inspector-drawer');
    if (drawer) drawer.classList.add('translate-x-full');
  }

  window.NX.scc = {
    load: loadCommandCenter,
    inspect: inspectStrategy,
    closeInspector: closeInspector,
    onShow: onShowCommandCenter,
    applyLifecycleFilter: applyLifecycleFilter,
  };

  // Test hooks (exposed in same closure scope; harmless in production).
  window.NX.scc._test_renderFleet = renderFleetTable;
  window.NX.scc._test_renderOverview = renderOverview;
  window.NX.scc._test_renderInspector = renderInspector;
  window.NX.scc._test_applyLifecycleFilter = applyLifecycleFilter;
  window.NX.scc._test_showEmpty = showSpatialEmptyState;
  window.NX.scc._test_hideEmpty = hideSpatialEmptyState;
  window.NX.scc._test_setSpatialData = (data) => { currentSpatialData = data; };
  window.NX.scc._test_getSpatialData = () => currentSpatialData;
  window.NX.scc._test_getLastFleet = () => fleetRows;

  // When loaded standalone (command_center.html), boot immediately.
  // When embedded in the dashboard (index.html), the tab switch calls onShow().
  if (document.getElementById('scc-spatial-canvas') &&
      !window.__NX_DASHBOARD_EMBEDDED) {
    document.addEventListener('DOMContentLoaded', () => {
      wireSpatialControls();
      window.NX.spatial.init('scc-spatial-canvas');
      window.NX.tm.init();
      loadCommandCenter().then(() => {
        if (window.NX.spatial) {
          const fitted = window.NX.spatial.fitAll();
          const backendTotal = currentSpatialData && currentSpatialData.nodes
            ? currentSpatialData.nodes.length : 0;
          if (!fitted) showSpatialEmptyState(backendTotal || '—', 'ALL', 0);
          else hideSpatialEmptyState();
        }
      });
    });

    // Re-init + auto-fit when the parent dashboard makes this iframe visible.
    // The canvas is sized 0 while hidden; once shown we must recompute layout.
    window.addEventListener('message', (ev) => {
      if (ev && ev.data && ev.data.type === 'NX_SCC_SHOW') {
        if (window.NX.spatial) {
          window.NX.spatial.init('scc-spatial-canvas');
          // Re-fetch to get fresh authoritative snapshot, then fit.
          loadCommandCenter().then(() => {
            if (window.NX.spatial) {
              const fitted = window.NX.spatial.fitAll();
              const backendTotal = currentSpatialData && currentSpatialData.nodes
                ? currentSpatialData.nodes.length : 0;
              if (!fitted) showSpatialEmptyState(backendTotal || '—', 'ALL', 0);
              else hideSpatialEmptyState();
            }
          });
        }
      }
    });
  }
})();
