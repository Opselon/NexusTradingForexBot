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

  async function loadCommandCenter() {
    if (!window.NX || !window.NX.api) return;
    try {
      const [ovRes, fleetRes, spatialRes] = await Promise.all([
        window.NX.api.get('/api/command-center/overview', { component: 'scc', action: 'overview' }),
        window.NX.api.get('/api/command-center/fleet', { component: 'scc', action: 'fleet' }),
        window.NX.api.get('/api/command-center/spatial', { component: 'scc', action: 'spatial' }),
      ]);

      if (ovRes.ok) {
        overviewData = ovRes.body;
        renderOverview(overviewData);
      }
      if (fleetRes.ok) {
        fleetRows = fleetRes.body.rows || [];
        renderFleetTable(fleetRows);
      }
      if (spatialRes.ok) {
        currentSpatialData = spatialRes.body;
        renderSpatialCanvas(currentSpatialData);
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

  function renderSpatialCanvas(spatial) {
    const canvas = document.getElementById('scc-spatial-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Resize canvas to parent
    const rect = canvas.parentElement.getBoundingClientRect();
    canvas.width = rect.width || 800;
    canvas.height = 500;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw background grid & zones
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    const zones = spatial.zones || [];
    const zoneHeight = canvas.height / Math.max(1, zones.length);

    zones.forEach((z, idx) => {
      const y = idx * zoneHeight;
      ctx.strokeStyle = '#334155';
      ctx.lineWidth = 1;
      ctx.strokeRect(20, y + 10, canvas.width - 40, zoneHeight - 20);

      ctx.fillStyle = '#94a3b8';
      ctx.font = 'bold 11px sans-serif';
      ctx.fillText(`${z.zone} (${z.count})`, 30, y + 30);
    });

    // Draw nodes
    const nodes = spatial.nodes || [];
    nodes.forEach(n => {
      const zoneIdx = zones.findIndex(z => z.zone === n.zone);
      const yBase = zoneIdx >= 0 ? zoneIdx * zoneHeight + zoneHeight / 2 : 100;
      const cx = canvas.width / 2 + (n.x || 0);
      const cy = yBase + ((n.y || 0) * 0.2);

      const isSelected = n.strategy_id === selectedStrategyId;

      ctx.beginPath();
      ctx.arc(cx, cy, isSelected ? 10 : 6, 0, Math.PI * 2);
      ctx.fillStyle = isSelected ? '#38bdf8' : (n.zone === 'ACTIVE' ? '#10b981' : (n.zone === 'REJECTED' ? '#f43f5e' : '#64748b'));
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = isSelected ? '#ffffff' : '#1e293b';
      ctx.stroke();

      ctx.fillStyle = '#cbd5e1';
      ctx.font = '10px monospace';
      ctx.fillText(n.strategy_id.substring(0, 10), cx + 10, cy + 4);
    });
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

    content.innerHTML = `
      <div class="space-y-4 text-xs">
        <div class="bg-darkBg p-3 rounded border border-borderClr">
          <p class="font-bold text-accentCyan mb-1">Identity & State</p>
          <div class="grid grid-cols-2 gap-2 text-gray-300 font-mono">
            <div>State: <span class="text-white">${data.current_state}</span></div>
            <div>Version: <span class="text-white">${data.strategy_version}</span></div>
            <div>Eligibility: <span class="${ee.can_trade ? 'text-emerald-400' : 'text-rose-400'}">${ee.eligibility_state}</span></div>
            <div>Confidence: <span class="text-white">${Math.round((data.confidence_score || 0) * 100)}%</span></div>
          </div>
          <p class="mt-2 text-[11px] text-textMuted">${ee.reason || ''}</p>
        </div>

        <div class="bg-darkBg p-3 rounded border border-borderClr">
          <p class="font-bold text-accentCyan mb-1">Evidence & Gates</p>
          <div class="grid grid-cols-2 gap-1 text-gray-300">
            <div>Backtest: <span class="font-mono text-white">${ev.backtest_status}</span></div>
            <div>Walk-Forward: <span class="font-mono text-white">${ev.walkforward_status}</span></div>
            <div>OOS: <span class="font-mono text-white">${ev.oos_status}</span></div>
            <div>Robustness: <span class="font-mono text-white">${ev.robustness_status}</span></div>
            <div>Score Verdict: <span class="font-mono text-white">${ev.score_verdict}</span></div>
          </div>
        </div>

        <div class="bg-darkBg p-3 rounded border border-borderClr">
          <p class="font-bold text-accentCyan mb-1">AI Explainability (${attr.status || 'NOT_AVAILABLE'})</p>
          <p class="text-[11px] text-textMuted mb-2">${attr.measured && attr.measured.note ? attr.measured.note : 'Honest attribution provenance.'}</p>
          <div class="space-y-1">
            ${(attr.contributions || []).map(c => `<div class="bg-panelBg p-1.5 rounded font-mono text-[10px] text-gray-300"><b>${c.source_type}</b>: ${c.kind}</div>`).join('')}
          </div>
        </div>

        <div class="bg-darkBg p-3 rounded border border-borderClr">
          <p class="font-bold text-accentCyan mb-1">Debug Intelligence</p>
          <div class="space-y-2">
            <div>Anomaly Score: <span class="font-mono text-white">${dbg.anomaly_score ? dbg.anomaly_score.anomaly_score : 0}</span></div>
            <div class="space-y-1 mt-1">
              ${(dbg.hints || []).map(h => `<div class="p-1.5 rounded bg-slate-800/80 border border-borderClr text-[10px]"><span class="font-bold text-accentGold">${h.category}</span>: ${h.message}</div>`).join('')}
            </div>
          </div>
        </div>
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
  };

  // Test hooks (exposed in same closure scope; harmless in production).
  window.NX.scc._test_renderFleet = renderFleetTable;
  window.NX.scc._test_renderOverview = renderOverview;
  window.NX.scc._test_renderInspector = renderInspector;
  window.NX.scc._test_renderSpatial = renderSpatialCanvas;

  // Auto-load on init when tab selected
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(loadCommandCenter, 1500);
  });
})();
