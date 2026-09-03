/* =========================================================================
 * STRATEGY MARKETPLACE UI — Control Center Marketplace Tab (CHG-0058)
 * -------------------------------------------------------------------------
 * Consumes the versioned API platform at /api/v1/marketplace.
 * Renders 6 sections:
 *   1. Installed Packs & Seeds (Install pack, view seed table)
 *   2. Available Seed Packs (Price Action, ICT, Ichimoku, Breakout, etc.)
 *   3. Research Lab (Trigger research run, view results)
 *   4. Rankings & 14-Factor Scores (Inspect factor dimensions)
 *   5. Strategy Repair & Evolution (Trigger repair on degraded seeds)
 *   6. Runtime Snapshot (Immutable enabled set & version)
 *
 * Safety & Design contract:
 *   - XSS-safe DOM building: uses document.createElement and textContent ONLY.
 *   - Honest uncertainty: missing or uncomputed scores render as "NOT_AVAILABLE"
 *     with neutral styling (never misleading green).
 *   - Uses window.NX.api for all HTTP requests (includes request IDs and
 *     safe error envelope handling).
 * ========================================================================= */
window.NX = window.NX || {};

(function () {
  'use strict';

  function renderMarketplaceTab() {
    const container = document.getElementById('tab-marketplace');
    if (!container) return;

    // Build the 6-panel marketplace UI if not already rendered
    if (container.dataset.rendered === 'true') {
      refreshMarketplaceData();
      return;
    }
    container.dataset.rendered = 'true';
    container.className = 'tab-content hidden space-y-6 p-6 bg-slate-900 text-slate-100 min-h-full font-sans';

    container.innerHTML = `
      <div class="flex items-center justify-between border-b border-slate-800 pb-4">
        <div>
          <h2 class="text-2xl font-black tracking-tight text-cyan-400 flex items-center gap-3">
            <i class="fa-solid fa-store text-xl"></i> Strategy Marketplace & Research Lab
          </h2>
          <p class="text-xs text-slate-400 mt-1">Discover, install, validate, score, repair, and govern strategy seeds across isolated research persistence.</p>
        </div>
        <div class="flex items-center gap-3">
          <button id="mkt-refresh-btn" class="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-200 transition border border-slate-700 flex items-center gap-2">
            <i class="fa-solid fa-rotate"></i> Refresh Marketplace
          </button>
        </div>
      </div>

      <!-- Grid: Top Row (Installed Seeds + Available Packs) -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <!-- Panel 1: Installed Seeds -->
        <div class="bg-slate-950/60 border border-slate-800 rounded-xl p-5 shadow-lg flex flex-col">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-bold uppercase tracking-wider text-slate-300 flex items-center gap-2">
              <i class="fa-solid fa-box-archive text-cyan-400"></i> Installed Strategy Seeds
            </h3>
            <span id="mkt-installed-count" class="text-xs font-mono px-2 py-0.5 rounded bg-cyan-950 text-cyan-300 border border-cyan-800">0 seeds</span>
          </div>
          <div class="overflow-x-auto flex-1 max-h-80 overflow-y-auto pr-1">
            <table class="w-full text-left text-xs">
              <thead class="text-slate-400 border-b border-slate-800 sticky top-0 bg-slate-950">
                <tr>
                  <th class="pb-2 font-semibold">Seed ID / Name</th>
                  <th class="pb-2 font-semibold">Family</th>
                  <th class="pb-2 font-semibold">Lifecycle</th>
                  <th class="pb-2 font-semibold text-right">Actions</th>
                </tr>
              </thead>
              <tbody id="mkt-installed-tbody" class="divide-y divide-slate-900 text-slate-300">
                <tr><td colspan="4" class="py-4 text-center text-slate-500 italic">No strategy seeds installed. Install a pack below.</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Panel 2: Available Packs -->
        <div class="bg-slate-950/60 border border-slate-800 rounded-xl p-5 shadow-lg flex flex-col">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-bold uppercase tracking-wider text-slate-300 flex items-center gap-2">
              <i class="fa-solid fa-boxes-stacked text-amber-400"></i> Available Installable Packs
            </h3>
            <span class="text-xs font-mono px-2 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-800">13 packs</span>
          </div>
          <div id="mkt-packs-grid" class="grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-80 overflow-y-auto pr-1">
            <div class="p-3 rounded-lg bg-slate-900 border border-slate-800 text-xs text-slate-400 animate-pulse">Loading packs catalog...</div>
          </div>
        </div>
      </div>

      <!-- Grid: Middle Row (Research Lab & Rankings) -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <!-- Panel 3: Research Lab -->
        <div class="bg-slate-950/60 border border-slate-800 rounded-xl p-5 shadow-lg flex flex-col">
          <h3 class="text-sm font-bold uppercase tracking-wider text-slate-300 mb-4 flex items-center gap-2">
            <i class="fa-solid fa-flask text-emerald-400"></i> Research Lab & Sandbox
          </h3>
          <div class="space-y-4 text-xs">
            <div>
              <label class="block text-slate-400 mb-1 font-medium">Select Installed Seed for Research Execution:</label>
              <select id="mkt-research-seed-select" class="w-full bg-slate-900 border border-slate-700 rounded p-2 text-slate-200">
                <option value="">-- Choose installed seed --</option>
              </select>
            </div>
            <div class="flex items-center gap-3">
              <button id="mkt-run-research-btn" class="px-4 py-2 rounded bg-emerald-600 hover:bg-emerald-500 text-white font-semibold transition flex items-center gap-2 shadow">
                <i class="fa-solid fa-play"></i> Run Research Pipeline
              </button>
              <span id="mkt-research-status" class="text-slate-400 italic">Ready.</span>
            </div>
            <div id="mkt-research-result-box" class="bg-slate-900 border border-slate-800 rounded p-3 font-mono text-[11px] text-slate-300 max-h-40 overflow-y-auto hidden">
              <!-- Research result JSON output -->
            </div>
          </div>
        </div>

        <!-- Panel 4: Rankings & 14-Factor Scores -->
        <div class="bg-slate-950/60 border border-slate-800 rounded-xl p-5 shadow-lg flex flex-col">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-bold uppercase tracking-wider text-slate-300 flex items-center gap-2">
              <i class="fa-solid fa-ranking-star text-purple-400"></i> Strategy Rankings & 14-Factor Scores
            </h3>
            <select id="mkt-rank-dim-select" class="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200">
              <option value="OVERALL">Dimension: Overall</option>
              <option value="PROFITABILITY">Profitability</option>
              <option value="ROBUSTNESS">Robustness</option>
              <option value="OOS">OOS Generalization</option>
              <option value="LIVE_READINESS">Live Readiness</option>
            </select>
          </div>
          <div class="overflow-x-auto flex-1 max-h-52 overflow-y-auto pr-1">
            <table class="w-full text-left text-xs">
              <thead class="text-slate-400 border-b border-slate-800 sticky top-0 bg-slate-950">
                <tr>
                  <th class="pb-2 font-semibold">Rank</th>
                  <th class="pb-2 font-semibold">Seed ID</th>
                  <th class="pb-2 font-semibold">Family</th>
                  <th class="pb-2 font-semibold text-right">Score</th>
                </tr>
              </thead>
              <tbody id="mkt-rankings-tbody" class="divide-y divide-slate-900 text-slate-300">
                <tr><td colspan="4" class="py-4 text-center text-slate-500 italic">No rankings computed yet.</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- Bottom Row: Repairs & Runtime Snapshot -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <!-- Panel 5: Repair & Evolution -->
        <div class="bg-slate-950/60 border border-slate-800 rounded-xl p-5 shadow-lg flex flex-col">
          <h3 class="text-sm font-bold uppercase tracking-wider text-slate-300 mb-4 flex items-center gap-2">
            <i class="fa-solid fa-wrench text-rose-400"></i> Strategy Repair & Mutation History
          </h3>
          <div class="space-y-3 text-xs flex-1">
            <div class="flex items-center gap-3">
              <select id="mkt-repair-seed-select" class="flex-1 bg-slate-900 border border-slate-700 rounded p-2 text-slate-200">
                <option value="">-- Choose seed to repair --</option>
              </select>
              <button id="mkt-trigger-repair-btn" class="px-3 py-2 rounded bg-rose-600 hover:bg-rose-500 text-white font-semibold transition flex items-center gap-2">
                <i class="fa-solid fa-hammer"></i> Trigger Repair
              </button>
            </div>
            <div class="overflow-x-auto max-h-36 overflow-y-auto pr-1 border border-slate-800 rounded bg-slate-900/50 p-2">
              <div id="mkt-repair-log" class="text-slate-400 italic">No repair runs recorded.</div>
            </div>
          </div>
        </div>

        <!-- Panel 6: Runtime Snapshot Store -->
        <div class="bg-slate-950/60 border border-slate-800 rounded-xl p-5 shadow-lg flex flex-col">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-bold uppercase tracking-wider text-slate-300 flex items-center gap-2">
              <i class="fa-solid fa-microchip text-blue-400"></i> Strategy Runtime Snapshot Store
            </h3>
            <span id="mkt-snapshot-version" class="text-xs font-mono px-2 py-0.5 rounded bg-blue-950 text-blue-300 border border-blue-800">Version: 0</span>
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded p-3 font-mono text-[11px] text-slate-300 max-h-40 overflow-y-auto" id="mkt-snapshot-content">
            <span class="text-slate-500 italic">Loading active runtime strategy set...</span>
          </div>
        </div>
      </div>

      <!-- Seed Detail Drawer Modal (Hidden by default) -->
      <div id="mkt-seed-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
        <div class="bg-slate-900 border border-slate-700 rounded-xl max-w-2xl w-full p-6 shadow-2xl space-y-4 max-h-[90vh] overflow-y-auto">
          <div class="flex items-center justify-between border-b border-slate-800 pb-3">
            <h3 id="mkt-modal-title" class="text-lg font-bold text-cyan-300">Seed Detail</h3>
            <button id="mkt-modal-close" class="text-slate-400 hover:text-slate-200 text-lg font-bold px-2">&times;</button>
          </div>
          <div id="mkt-modal-body" class="text-xs space-y-3 text-slate-300">
            <!-- Populated dynamically -->
          </div>
          <div class="border-t border-slate-800 pt-3 flex items-center justify-end gap-2" id="mkt-modal-actions">
            <!-- Enable / Disable buttons -->
          </div>
        </div>
      </div>
    `;

    // Bind event listeners
    document.getElementById('mkt-refresh-btn').addEventListener('click', refreshMarketplaceData);
    document.getElementById('mkt-run-research-btn').addEventListener('click', triggerResearchRun);
    document.getElementById('mkt-trigger-repair-btn').addEventListener('click', triggerSeedRepair);
    document.getElementById('mkt-rank-dim-select').addEventListener('change', loadRankings);
    document.getElementById('mkt-modal-close').addEventListener('click', closeModal);

    refreshMarketplaceData();
  }

  async function refreshMarketplaceData() {
    await Promise.all([
      loadPacks(),
      loadInstalledSeeds(),
      loadRankings(),
      loadRuntimeSnapshot()
    ]);
  }

  async function loadPacks() {
    const container = document.getElementById('mkt-packs-grid');
    if (!container) return;
    try {
      const res = await window.NX.api.get('/api/v1/marketplace/packs', { component: 'Marketplace', action: 'LIST_PACKS' });
      const packs = (res && res.data && res.data.packs) || [];
      if (!packs.length) {
        container.innerHTML = '<div class="text-slate-500 italic p-2">No packs available.</div>';
        return;
      }
      container.innerHTML = '';
      packs.forEach(pack => {
        const div = document.createElement('div');
        div.className = 'bg-slate-900 border border-slate-800 rounded-lg p-3 flex flex-col justify-between gap-2';
        
        const top = document.createElement('div');
        const h4 = document.createElement('h4');
        h4.className = 'font-bold text-slate-200 text-xs';
        h4.textContent = pack.name;
        const p = document.createElement('p');
        p.className = 'text-[11px] text-slate-400 mt-0.5 line-clamp-2';
        p.textContent = pack.description;
        top.appendChild(h4);
        top.appendChild(p);
        div.appendChild(top);

        const bot = document.createElement('div');
        bot.className = 'flex items-center justify-between pt-1 border-t border-slate-800/60 text-[10px] text-slate-500';
        const span = document.createElement('span');
        span.textContent = `${pack.seed_count || 25} seeds (${pack.family})`;
        
        const btn = document.createElement('button');
        btn.className = 'px-2.5 py-1 rounded bg-amber-600/80 hover:bg-amber-500 text-white font-semibold transition';
        btn.textContent = 'Install Pack';
        btn.addEventListener('click', () => installPack(pack.pack_id));

        bot.appendChild(span);
        bot.appendChild(btn);
        div.appendChild(bot);

        container.appendChild(div);
      });
    } catch (err) {
      console.warn('[Marketplace] loadPacks error:', err);
      container.innerHTML = '<div class="text-rose-400 italic p-2">Failed to load packs catalog.</div>';
    }
  }

  async function installPack(packId) {
    try {
      const res = await window.NX.api.post(`/api/v1/marketplace/packs/${packId}/install`, { count: 25 }, { component: 'Marketplace', action: 'INSTALL_PACK' });
      if (res && res.success) {
        alert(`Successfully installed pack ${packId}!`);
        refreshMarketplaceData();
      } else {
        alert(`Install failed: ${res && res.error ? res.error.message : 'Unknown error'}`);
      }
    } catch (err) {
      alert(`Install request failed: ${err.message || err}`);
    }
  }

  async function loadInstalledSeeds() {
    const tbody = document.getElementById('mkt-installed-tbody');
    const researchSelect = document.getElementById('mkt-research-seed-select');
    const repairSelect = document.getElementById('mkt-repair-seed-select');
    if (!tbody) return;

    try {
      const res = await window.NX.api.get('/api/v1/marketplace/seeds?page_size=100', { component: 'Marketplace', action: 'LIST_SEEDS' });
      const items = (res && res.data && res.data.items) || [];
      
      // Update dropdowns
      if (researchSelect) {
        researchSelect.innerHTML = '<option value="">-- Choose installed seed --</option>';
        items.forEach(s => {
          const opt = document.createElement('option');
          opt.value = s.seed_id;
          opt.textContent = `${s.name} (${s.seed_id})`;
          researchSelect.appendChild(opt);
        });
      }
      if (repairSelect) {
        repairSelect.innerHTML = '<option value="">-- Choose seed to repair --</option>';
        items.forEach(s => {
          const opt = document.createElement('option');
          opt.value = s.seed_id;
          opt.textContent = `${s.name} (${s.seed_id})`;
          repairSelect.appendChild(opt);
        });
      }

      const countSpan = document.getElementById('mkt-installed-count');
      if (countSpan) countSpan.textContent = `${items.length} seeds`;

      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="py-4 text-center text-slate-500 italic">No strategy seeds installed. Install a pack above.</td></tr>';
        return;
      }

      tbody.innerHTML = '';
      items.forEach(seed => {
        const tr = document.createElement('tr');
        tr.className = 'hover:bg-slate-900/60 transition';

        const td1 = document.createElement('td');
        td1.className = 'py-2.5 font-medium text-slate-200';
        const nameDiv = document.createElement('div');
        nameDiv.textContent = seed.name;
        const subDiv = document.createElement('div');
        subDiv.className = 'text-[10px] font-mono text-slate-500';
        subDiv.textContent = seed.seed_id;
        td1.appendChild(nameDiv);
        td1.appendChild(subDiv);

        const td2 = document.createElement('td');
        td2.className = 'py-2.5 text-slate-400';
        td2.textContent = seed.family;

        const td3 = document.createElement('td');
        td3.className = 'py-2.5';
        const badge = document.createElement('span');
        badge.className = 'px-2 py-0.5 rounded text-[10px] font-bold ' + getLifecycleBadgeClass(seed.lifecycle);
        badge.textContent = seed.lifecycle;
        td3.appendChild(badge);

        const td4 = document.createElement('td');
        td4.className = 'py-2.5 text-right space-x-2';
        
        const detailBtn = document.createElement('button');
        detailBtn.className = 'px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] font-semibold transition';
        detailBtn.textContent = 'View';
        detailBtn.addEventListener('click', () => openSeedDetail(seed.seed_id));

        const enableBtn = document.createElement('button');
        enableBtn.className = 'px-2 py-1 rounded bg-cyan-600 hover:bg-cyan-500 text-white text-[11px] font-semibold transition';
        enableBtn.textContent = 'Enable...';
        enableBtn.addEventListener('click', () => promptEnableSeed(seed.seed_id, seed.lifecycle));

        td4.appendChild(detailBtn);
        td4.appendChild(enableBtn);

        tr.appendChild(td1);
        tr.appendChild(td2);
        tr.appendChild(td3);
        tr.appendChild(td4);

        tbody.appendChild(tr);
      });
    } catch (err) {
      console.warn('[Marketplace] loadInstalledSeeds error:', err);
      tbody.innerHTML = '<tr><td colspan="4" class="py-4 text-center text-rose-400 italic">Failed to load installed seeds.</td></tr>';
    }
  }

  function getLifecycleBadgeClass(lc) {
    switch (lc) {
      case 'VALIDATED':
      case 'RESEARCH_VALIDATED': return 'bg-emerald-950 text-emerald-300 border border-emerald-800';
      case 'LIVE_ELIGIBLE':
      case 'LIVE_CANDIDATE': return 'bg-purple-950 text-purple-300 border border-purple-800';
      case 'RESEARCH_RUNNING':
      case 'RESEARCH_PENDING': return 'bg-amber-950 text-amber-300 border border-amber-800';
      case 'REJECTED':
      case 'QUARANTINED': return 'bg-rose-950 text-rose-300 border border-rose-800';
      default: return 'bg-slate-800 text-slate-300 border border-slate-700';
    }
  }

  async function loadRankings() {
    const tbody = document.getElementById('mkt-rankings-tbody');
    const dimSelect = document.getElementById('mkt-rank-dim-select');
    if (!tbody) return;
    const dim = dimSelect ? dimSelect.value : 'OVERALL';

    try {
      const res = await window.NX.api.get(`/api/v1/marketplace/rankings?dimension=${dim}`, { component: 'Marketplace', action: 'GET_RANKINGS' });
      const items = (res && res.data && res.data.rankings) || [];
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="py-4 text-center text-slate-500 italic">No rankings available yet. Run research on seeds.</td></tr>';
        return;
      }
      tbody.innerHTML = '';
      items.forEach((r, idx) => {
        const tr = document.createElement('tr');
        tr.className = 'hover:bg-slate-900/60 transition';

        const td1 = document.createElement('td');
        td1.className = 'py-2 font-mono text-slate-400';
        td1.textContent = `#${idx + 1}`;

        const td2 = document.createElement('td');
        td2.className = 'py-2 font-medium text-slate-200';
        td2.textContent = r.seed_id;

        const td3 = document.createElement('td');
        td3.className = 'py-2 text-slate-400';
        td3.textContent = r.family || 'HYBRID';

        const td4 = document.createElement('td');
        td4.className = 'py-2 text-right font-mono font-bold text-cyan-300';
        td4.textContent = (r.score !== undefined ? Number(r.score).toFixed(3) : 'NOT_AVAILABLE');

        tr.appendChild(td1);
        tr.appendChild(td2);
        tr.appendChild(td3);
        tr.appendChild(td4);
        tbody.appendChild(tr);
      });
    } catch (err) {
      console.warn('[Marketplace] loadRankings error:', err);
      tbody.innerHTML = '<tr><td colspan="4" class="py-4 text-center text-rose-400 italic">Failed to load rankings.</td></tr>';
    }
  }

  async function loadRuntimeSnapshot() {
    const box = document.getElementById('mkt-snapshot-content');
    const verSpan = document.getElementById('mkt-snapshot-version');
    if (!box) return;
    try {
      const res = await window.NX.api.get('/api/v1/marketplace/runtime-snapshot', { component: 'Marketplace', action: 'GET_SNAPSHOT' });
      const data = res && res.data;
      if (!data) {
        box.textContent = 'No runtime snapshot recorded.';
        return;
      }
      if (verSpan) verSpan.textContent = `Version: ${data.version || 0}`;
      box.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      box.textContent = 'Runtime snapshot unavailable (backend offline or unmounted).';
    }
  }

  async function triggerResearchRun() {
    const select = document.getElementById('mkt-research-seed-select');
    const statusSpan = document.getElementById('mkt-research-status');
    const resultBox = document.getElementById('mkt-research-result-box');
    if (!select || !select.value) {
      alert('Please choose an installed seed first.');
      return;
    }
    const seedId = select.value;
    if (statusSpan) statusSpan.textContent = 'Running research pipeline...';
    if (resultBox) {
      resultBox.classList.add('hidden');
      resultBox.textContent = '';
    }

    try {
      const res = await window.NX.api.post(`/api/v1/marketplace/seeds/${seedId}/run-research`, {}, { component: 'Marketplace', action: 'RUN_RESEARCH' });
      if (res && res.success) {
        if (statusSpan) statusSpan.textContent = 'Research run completed successfully!';
        if (resultBox) {
          resultBox.classList.remove('hidden');
          resultBox.textContent = JSON.stringify(res.data, null, 2);
        }
        refreshMarketplaceData();
      } else {
        if (statusSpan) statusSpan.textContent = 'Research run finished with errors.';
        if (resultBox) {
          resultBox.classList.remove('hidden');
          resultBox.textContent = JSON.stringify(res, null, 2);
        }
      }
    } catch (err) {
      if (statusSpan) statusSpan.textContent = `Research request failed: ${err.message || err}`;
    }
  }

  async function triggerSeedRepair() {
    const select = document.getElementById('mkt-repair-seed-select');
    const logBox = document.getElementById('mkt-repair-log');
    if (!select || !select.value) {
      alert('Please choose a seed to repair.');
      return;
    }
    const seedId = select.value;
    if (logBox) logBox.textContent = `Triggering repair for ${seedId}...`;

    try {
      const res = await window.NX.api.post(`/api/v1/marketplace/seeds/${seedId}/repair`, { trigger: 'MANUAL_TRIGGER' }, { component: 'Marketplace', action: 'REPAIR_SEED' });
      if (res && res.success) {
        if (logBox) logBox.textContent = JSON.stringify(res.data, null, 2);
        refreshMarketplaceData();
      } else {
        if (logBox) logBox.textContent = `Repair failed: ${JSON.stringify(res)}`;
      }
    } catch (err) {
      if (logBox) logBox.textContent = `Repair request failed: ${err.message || err}`;
    }
  }

  async function openSeedDetail(seedId) {
    const modal = document.getElementById('mkt-seed-modal');
    const title = document.getElementById('mkt-modal-title');
    const body = document.getElementById('mkt-modal-body');
    const actions = document.getElementById('mkt-modal-actions');
    if (!modal) return;

    if (title) title.textContent = `Seed Detail: ${seedId}`;
    if (body) body.textContent = 'Loading seed specifications and history...';
    if (actions) actions.innerHTML = '';
    modal.classList.remove('hidden');

    try {
      const res = await window.NX.api.get(`/api/v1/marketplace/seeds/${seedId}`, { component: 'Marketplace', action: 'GET_SEED' });
      const seed = res && res.data;
      if (!seed) {
        if (body) body.textContent = 'Seed not found.';
        return;
      }

      if (body) {
        body.innerHTML = '';
        const pre = document.createElement('pre');
        pre.className = 'bg-slate-950 p-3 rounded font-mono text-[11px] text-slate-300 overflow-x-auto max-h-60';
        pre.textContent = JSON.stringify(seed, null, 2);
        body.appendChild(pre);
      }

      if (actions) {
        const disBtn = document.createElement('button');
        disBtn.className = 'px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold transition';
        disBtn.textContent = 'Disable';
        disBtn.addEventListener('click', async () => {
          await window.NX.api.post(`/api/v1/marketplace/seeds/${seedId}/disable`, {}, { component: 'Marketplace', action: 'DISABLE_SEED' });
          closeModal();
          refreshMarketplaceData();
        });
        actions.appendChild(disBtn);
      }
    } catch (err) {
      if (body) body.textContent = `Failed to load seed detail: ${err.message || err}`;
    }
  }

  async function promptEnableSeed(seedId, currentLifecycle) {
    const mode = prompt('Enter enablement mode (RESEARCH, PAPER, SHADOW, LIVE_REQUEST):', 'RESEARCH');
    if (!mode) return;
    try {
      const res = await window.NX.api.post(`/api/v1/marketplace/seeds/${seedId}/enable`, { mode: mode.toUpperCase() }, { component: 'Marketplace', action: 'ENABLE_SEED' });
      if (res && res.success) {
        alert(`Enable result: ${JSON.stringify(res.data)}`);
        refreshMarketplaceData();
      } else {
        alert(`Enable rejected: ${JSON.stringify(res)}`);
      }
    } catch (err) {
      alert(`Enable request failed: ${err.message || err}`);
    }
  }

  function closeModal() {
    const modal = document.getElementById('mkt-seed-modal');
    if (modal) modal.classList.add('hidden');
  }

  // Hook into dashboard startup / tab switching if needed
  window.addEventListener('DOMContentLoaded', () => {
    // If tab is activated later, switchTab will call our refresh
  });

  // Export namespace
  window.NX = window.NX || {};
  window.NX.marketplace = {
    render: renderMarketplaceTab,
    refresh: refreshMarketplaceData
  };
})();
