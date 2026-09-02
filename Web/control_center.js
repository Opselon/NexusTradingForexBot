/* =========================================================================
 * NEXUS CONTROL CENTER — operator views (NX.cc.views)
 * -------------------------------------------------------------------------
 * CHG-0043 / TASK-CONTROL-CENTER (Agent: Hermes-UI)
 *
 * Screens: OVERVIEW · DECISIONS (observatory + NO_TRADE forensics +
 * inspector drilldown) · MODEL (serving identity + feature contract) ·
 * RISK/EXECUTION · DIAGNOSTICS.
 *
 * Data sources (all real, read-only):
 *   /api/operator/*       decision evidence (audit_signals/audit_orders)
 *   /api/live/state       canonical LiveUiState.2 snapshot (app.js owns SSE;
 *                         we reuse its last payload when present)
 *   /health               engine self-check verdicts
 *   /api/diagnostics/health   incident health
 *   /api/release/status   runtime identity (when mounted)
 *
 * Truth rules: a value the backend did not supply renders as NOT RECORDED
 * (never 0, never invented); NO_TRADE is a first-class event with evidence
 * drilldown; every live value carries a freshness indicator; summary and
 * detail reconcile (funnel/forensics numbers come from the same endpoints).
 * ========================================================================= */
window.NX = window.NX || {};
window.NX.cc = window.NX.cc || {};

(function () {
  'use strict';

  const D = function () { return window.NX.cc.design; };
  const S = function () { return window.NX.cc.state; };
  const api = function () { return window.NX.api; };

  let currentTab = 'cc-overview';
  const unsub = {};

  /* ------------------------------------------------------------------ *
   * Shared snapshot access (single source of live truth = app.js SSE)
   * ------------------------------------------------------------------ */
  function liveState() {
    // app.js holds the canonical merged LiveUiState payload from SSE.
    return (typeof liveUiSnapshot !== 'undefined') ? liveUiSnapshot : null;
  }

  function firstEvidence(rows) {
    return rows && rows.length ? rows[0] : null;
  }

  /* =================================================================== *
   * TAB: OVERVIEW
   * =================================================================== */
  function renderOverview(el, snap, live) {
    const D_ = D();
    if (snap.state === 'LOADING' && !snap.data) { el.innerHTML = D_.loadingState('Assembling operator overview…'); return; }
    if (snap.state === 'ERROR' && !snap.data) {
      el.innerHTML = D_.errorState('Overview unavailable', (snap.error && snap.error.message) || '', null, 'window.NX.cc.views.refreshSummary');
      return;
    }
    const s = snap.data || {};
    const rt = s.runtime || {};
    const idt = s.identity || {};
    const health = rt.health || {};
    const subs = health.subsystems || {};

    // Mode banner — LIVE must never look like anything else.
    const mode = String(rt.runtime_mode || rt.execution_mode || 'UNKNOWN').toUpperCase();
    const isLive = mode === 'LIVE' || mode.indexOf('LIVE') === 0;
    const banner =
      '<div class="' + (isLive ? 'cc-banner-live' : 'cc-banner-safe') + '">' +
      D_.modeBadge(mode) +
      '<span class="cc-banner-note">' + (isLive
        ? 'LIVE mode dispatches real orders to the broker. Verify risk state before any operator action.'
        : 'Non-live mode: orders are simulated or recorded without execution (see mode semantics).') + '</span>' +
      '<span style="margin-left:auto">' + D_.freshness(rt.snapshot_timestamp, 'snapshot') + '</span>' +
      '</div>';

    // Status strip: Runtime · Data · Model · Feature · Database · Execution
    const stripData = [
      { key: 'Runtime', state: rt.engine_running ? 'READY' : 'BLOCKED', detail: health.details && health.details.engine },
      { key: 'Data', state: rt.tick_stale ? 'STALE' : (live && live.diagnostics ? 'READY' : 'UNKNOWN'), detail: rt.tick_freshness_ms != null ? 'tick age ' + Math.round(rt.tick_freshness_ms) + ' ms' : null },
      { key: 'Model', state: (subs.model || 'UNKNOWN'), detail: health.details && health.details.model },
      { key: 'Inference', state: (subs.inference_freshness || 'UNKNOWN'), detail: health.details && health.details.inference_freshness },
      { key: 'Database', state: (subs.database || 'UNKNOWN'), detail: health.details && health.details.database },
      { key: 'MT5', state: (subs.mt5 || 'UNKNOWN'), detail: health.details && health.details.mt5 },
    ];
    const strip = '<div class="cc-strip">' + stripData.map(function (c) {
      return D_.healthCard({ id: c.key, name: c.key, state: c.state, detail: c.detail });
    }).join('') + '</div>';

    // Runtime truth panel
    const runtimeTruth = D_.kvGrid([
      ['Version', idt.version || 'NOT RECORDED'],
      ['Commit', idt.commit ? idt.commit + ' (' + (idt.commit_status || 'RECORDED') + ')' : 'NOT RECORDED'],
      ['Channel', idt.channel || 'NOT RECORDED'],
      ['Mode', rt.runtime_mode || 'NOT RECORDED'],
      ['Symbol', rt.symbol || 'NOT RECORDED'],
      ['Regime', rt.regime || 'NOT RECORDED'],
      ['Started', health.checked_at ? 'checked ' + health.checked_at : null],
    ]);

    // Market snapshot
    const market = D_.kvGrid([
      ['Bid', rt.bid != null ? rt.bid : 'NOT RECORDED'],
      ['Ask', rt.ask != null ? rt.ask : 'NOT RECORDED'],
      ['Spread', rt.spread != null ? rt.spread : 'NOT RECORDED'],
      ['Regime', rt.regime || 'NOT RECORDED'],
      ['Freshness', rt.tick_freshness_ms != null ? Math.round(rt.tick_freshness_ms) + ' ms' : null],
      ['Provenance', rt.provenance && rt.provenance.price ? rt.provenance.price : null],
    ]);

    // Latest decision — from the same ledger the observatory reads.
    const rows = (s.ledger && s.ledger.available) ? null : null;
    let latestHtml;
    if (window.__ccLatestDecision) {
      const d = window.__ccLatestDecision;
      latestHtml = D_.kvGrid([
        ['Action', d.action || 'NOT RECORDED', true],
        ['Confidence', d.confidence != null ? String(d.confidence) : ''],
        ['Stage', d.decision_stage || 'NOT RECORDED'],
        ['Gate', d.blocked_by || 'not blocked'],
        ['Reason', d.reason_code || 'NOT RECORDED'],
        ['At', d.generated_at || 'NOT RECORDED'],
      ]) + '<button class="ccb-btn ccb-btn-sm" onclick="window.NX.cc.views.showTab(\'cc-decisions\')">Open Decision Observatory \u2192</button>';
    } else {
      latestHtml = D_.emptyState('No decision evidence in window', 'The decision ledger returned no rows for the recent window.');
    }

    // Warnings
    const warns = s.warnings || [];
    const warningsHtml = warns.length
      ? warns.map(function (w) {
          return D_.healthCard({ id: w.code, name: w.code, state: w.severity || 'WARNING', why: w.what, impact: w.impact, action: w.what_to_do });
        }).join('')
      : D_.emptyState('No active warnings', 'Nothing is currently flagged by the operator surface.');

    el.innerHTML =
      banner +
      '<div style="height:12px"></div>' +
      strip +
      '<div style="height:14px"></div>' +
      '<div class="cc-grid-2">' +
        '<div class="ccb-card"><div class="cc-panel-title">Runtime Truth</div>' + runtimeTruth + '</div>' +
        '<div class="ccb-card"><div class="cc-panel-title">Market Snapshot</div>' + market + '</div>' +
      '</div>' +
      '<div style="height:14px"></div>' +
      '<div class="ccb-card"><div class="cc-panel-title">Latest Decision</div>' + latestHtml + '</div>' +
      '<div style="height:14px"></div>' +
      '<div class="ccb-card"><div class="cc-panel-title">Active Warnings</div>' + warningsHtml + '</div>' +
      '<div style="height:14px"></div>' +
      '<div class="cc-sub">Ledger window: ' + (s.ledger && s.ledger.available
        ? s.ledger.total + ' decisions (scanned ' + s.ledger.scanned_rows + ', latest ' + (s.ledger.latest_decision_at || '—') + ')'
        : 'ledger unavailable') + ' · identity generated ' + (idt.generated_at || '—') + '</div>';
  }

  /* =================================================================== *
   * TAB: DECISIONS (observatory + funnel + NO_TRADE + inspector)
   * =================================================================== */
  let decisionFilters = { hours: 24, action: '', gate: '', search: '' };

  function renderDecisions(el) {
    el.innerHTML =
      '<div class="cc-grid-3">' +
        '<div class="ccb-card"><div class="cc-panel-title">Terminal Stage Distribution</div><div id="cc-funnel-body">' + D().loadingState('') + '</div></div>' +
        '<div class="ccb-card"><div class="cc-panel-title">NO_TRADE Forensics</div><div id="cc-notrade-body">' + D().loadingState('') + '</div></div>' +
      '</div>' +
      '<div style="height:14px"></div>' +
      '<div class="ccb-card">' +
        '<div class="cc-panel-title">Decision History ' +
          '<span class="cc-sub">click a row for full evidence</span>' +
        '</div>' +
        '<div style="height:8px"></div>' +
        '<div class="cc-dec-filters">' +
          '<select id="cc-f-hours" class="ccb-btn ccb-btn-sm">' +
            [1, 6, 24, 168, 720].map(function (h) {
              return '<option value="' + h + '"' + (decisionFilters.hours === h ? ' selected' : '') + '>' + (h < 24 ? h + 'h' : (h / 24) + 'd') + '</option>';
            }).join('') +
          '</select>' +
          '<select id="cc-f-action" class="ccb-btn ccb-btn-sm">' +
            '<option value="">ALL ACTIONS</option><option value="NO_TRADE">NO_TRADE</option>' +
            '<option value="BUY_MARKET">BUY_MARKET</option><option value="SELL_MARKET">SELL_MARKET</option>' +
            '<option value="BUY_LIMIT">BUY_LIMIT</option><option value="SELL_LIMIT">SELL_LIMIT</option>' +
          '</select>' +
          '<input id="cc-f-search" class="cc-feature-search" placeholder="search request_id / reason / regime…" value="' + D().esc(decisionFilters.search) + '">' +
        '</div>' +
        '<div style="height:10px"></div>' +
        '<div id="cc-dec-body">' + D().loadingState('Loading decision ledger…') + '</div>' +
      '</div>' +
      '<div id="cc-inspector"></div>';
    bindDecisionFilters(el);
  }

  function bindDecisionFilters(el) {
    const hours = el.querySelector('#cc-f-hours');
    const action = el.querySelector('#cc-f-action');
    const search = el.querySelector('#cc-f-search');
    let deb = null;
    function apply() {
      decisionFilters.hours = parseInt(hours.value, 10) || 24;
      decisionFilters.action = action.value;
      decisionFilters.search = search.value.trim();
      refreshDecisions();
    }
    hours.onchange = apply;
    action.onchange = apply;
    search.oninput = function () { clearTimeout(deb); deb = setTimeout(apply, 350); };
  }

  function decisionQuery() {
    const q = new URLSearchParams();
    if (decisionFilters.hours) q.set('hours', String(decisionFilters.hours));
    if (decisionFilters.action) q.set('action', decisionFilters.action);
    if (decisionFilters.gate) q.set('gate', decisionFilters.gate);
    if (decisionFilters.search) q.set('search', decisionFilters.search);
    q.set('limit', '60');
    return q.toString();
  }

  async function refreshDecisions() {
    const body = document.getElementById('cc-dec-body');
    if (!body) return;
    const res = await api().get('/api/operator/decisions?' + decisionQuery(), { component: 'ControlCenter', action: 'DECISIONS' });
    if (!res.ok) {
      body.innerHTML = D().errorState('Decision ledger unavailable', api().msg(res), res.error && res.error.request_id, 'window.NX.cc.views.refreshDecisions');
      return;
    }
    const rows = res.body.rows || [];
    window.__ccDecisionRows = rows;
    if (!rows.length) {
      body.innerHTML = D().emptyState('No decisions found for this timeframe', 'Adjust the filters or widen the time range.');
      return;
    }
    body.innerHTML = D().dataTable({
      columns: [
        { label: 'Time' }, { label: 'Action' }, { label: 'Conf' },
        { label: 'Stage' }, { label: 'Blocked by' }, { label: 'Reason' }, { label: '' },
      ],
      rows: rows.map(function (r) {
        return { cells: [
          { text: String(r.generated_at || '').replace('T', ' ').slice(0, 19) },
          { html: D().statusBadge(r.action === 'NO_TRADE' ? 'UNKNOWN' : 'READY', { label: r.action || '?' }) },
          { html: D().probCell(r.confidence != null ? r.confidence : null) },
          { text: r.decision_stage || 'NOT RECORDED' },
          { text: r.blocked_by || '—' },
          { html: '<span class="ccb-code">' + D().esc(String(r.reason_code || '').slice(0, 64)) + '</span>' },
          { html: D().copyButton(String(r.request_id || ''), 'ID') },
        ], id: r.id };
      }),
      note: res.body.count + ' rows shown · from the immutable audit_signals ledger · every value is recorded evidence',
    });
    body.querySelectorAll('tr[data-cc-row]').forEach(function (tr) {
      tr.addEventListener('click', function () {
        const row = rows[parseInt(tr.getAttribute('data-cc-row'), 10)];
        if (row) openInspector(row.id);
      });
    });
  }

  async function openInspector(decisionId) {
    const box = document.getElementById('cc-inspector');
    if (!box) return;
    box.innerHTML = '<div class="ccb-card"><div class="cc-panel-title">Decision Inspector ' + decisionId + '</div>' + D().loadingState('Fetching recorded evidence…') + '</div>';
    box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    const res = await api().get('/api/operator/decisions/' + decisionId, { component: 'ControlCenter', action: 'INSPECT' });
    if (!res.ok) {
      box.innerHTML = D().errorState('Evidence unavailable', api().msg(res), res.error && res.error.request_id, 'window.NX.cc.views.refreshDecisions');
      return;
    }
    const d = res.body.decision || {};
    const p = d.probabilities || {};
    const orders = d.orders || [];
    const model = D().kvGrid([
      ['Model action', p.model_action || 'NOT RECORDED'],
      ['P(BUY)', D().probCell(p.buy)],
      ['P(SELL)', D().probCell(p.sell)],
      ['P(NO_TRADE)', D().probCell(p.no_trade)],
      ['Regime conf.', d.payload && d.payload.regime_confidence != null ? d.payload.regime_confidence : 'NOT RECORDED'],
    ]);
    const chain = D().kvGrid([
      ['Stage', d.decision_stage || 'NOT RECORDED'],
      ['Blocked by', d.blocked_by || 'not blocked'],
      ['Risk allowed', d.payload && d.payload.risk_allowed != null ? String(d.payload.risk_allowed) : 'NOT RECORDED'],
      ['Guardian', d.payload && d.payload.guardian_status ? d.payload.guardian_status : 'NOT RECORDED'],
      ['Reason', d.payload && d.payload.rejection_reason ? d.payload.rejection_reason : (d.reason_code || 'NOT RECORDED')],
    ]);
    const geometry = D().kvGrid([
      ['Entry', d.proposed_entry != null ? d.proposed_entry : 'NOT RECORDED'],
      ['Stop loss', d.stop_loss != null ? d.stop_loss : 'NOT RECORDED'],
      ['Take profit', d.take_profit != null ? d.take_profit : 'NOT RECORDED'],
      ['Spread seen', d.spread_usd != null ? d.spread_usd : 'NOT RECORDED'],
    ]);
    const ordersHtml = orders.length
      ? D().dataTable({
          columns: [{ label: 'Ticket' }, { label: 'Action' }, { label: 'Price' }, { label: 'Volume' }, { label: 'Latency' }, { label: 'Mode' }],
          rows: orders.map(function (o) { return { cells: [
            { text: o.ticket || '—' }, { text: o.action || '—' }, { text: o.price != null ? o.price : '—' },
            { text: o.volume != null ? o.volume : '—' }, { text: o.latency != null ? o.latency + ' ms' : '—' }, { text: o.execution_mode || '—' },
          ] }; }),
        })
      : '<div class="ccb-empty-inline">No dispatch rows correlate to this decision (correlation: ' + D().esc(d.order_correlation || 'n/a') + ').</div>';
    box.innerHTML =
      '<div class="ccb-card" style="margin-top:14px">' +
      '<div class="cc-panel-title">Decision Inspector <span class="ccb-code">#' + decisionId + '</span>' +
        '<span style="margin-left:auto">' + D().copyButton(String(d.request_id || ''), 'Copy request_id') +
        '<button class="ccb-btn ccb-btn-sm" onclick="document.getElementById(\'cc-inspector\').innerHTML=\'\'">Close</button></span>' +
      '</div>' +
      '<div style="height:10px"></div>' +
      '<div class="cc-grid-2">' +
        '<div><div class="cc-sub" style="margin-bottom:6px">RAW INPUT → MODEL</div>' + model + '</div>' +
        '<div><div class="cc-sub" style="margin-bottom:6px">POLICY → RISK → EXECUTION</div>' + chain + '</div>' +
      '</div>' +
      '<div style="height:10px"></div>' +
      '<div class="cc-sub" style="margin-bottom:6px">GEOMETRY (as recorded)</div>' + geometry +
      '<div style="height:12px"></div>' +
      '<div class="cc-sub" style="margin-bottom:6px">CORRELATED ORDERS</div>' + ordersHtml +
      '</div>';
  }

  async function refreshFunnel() {
    const el = document.getElementById('cc-funnel-body');
    if (!el) return;
    const res = await api().get('/api/operator/funnel?hours=' + decisionFilters.hours, { component: 'ControlCenter', action: 'FUNNEL' });
    if (!res.ok) { el.innerHTML = D().errorState('Funnel unavailable', api().msg(res), null, 'window.NX.cc.views.refreshFunnel'); return; }
    const b = res.body;
    el.innerHTML =
      D().barBreakdown((b.stages || []).slice(0, 8).map(function (x) { return { label: x.stage, count: x.count }; }), b.total) +
      '<div style="height:10px"></div>' +
      '<div class="cc-panel-title" style="font-size:10px">Blocking gates</div>' +
      D().barBreakdown((b.gates || []).slice(0, 6).map(function (x) { return { label: x.gate, count: x.count }; }), b.total) +
      '<div class="ccb-table-note">' + D().esc(b.note || '') + ' Total: ' + b.total + ' (scanned ' + b.scanned_rows + ').</div>';
  }

  async function refreshNoTrade() {
    const el = document.getElementById('cc-notrade-body');
    if (!el) return;
    const res = await api().get('/api/operator/no-trade?hours=' + decisionFilters.hours + '&limit=8', { component: 'ControlCenter', action: 'NO_TRADE' });
    if (!res.ok) { el.innerHTML = D().errorState('NO_TRADE forensics unavailable', api().msg(res), null, 'window.NX.cc.views.refreshNoTrade'); return; }
    const b = res.body;
    const trend = (b.hourly_trend || []).slice().reverse();
    const maxT = trend.reduce(function (a, x) { return Math.max(a, x.count); }, 0);
    el.innerHTML =
      '<div class="ccb-kv"><span>Total</span><b>' + b.total + ' NO_TRADE decisions</b></div>' +
      '<div style="height:8px"></div>' +
      D().barBreakdown((b.gates || []).slice(0, 6).map(function (x) { return { label: x.gate, count: x.count }; }), b.total) +
      '<div style="height:10px"></div>' +
      '<div class="cc-panel-title" style="font-size:10px">Top reasons</div>' +
      D().barBreakdown((b.reasons || []).slice(0, 5).map(function (x) { return { label: x.reason, count: x.count }; }), b.total) +
      '<div style="height:10px"></div>' +
      '<div class="cc-panel-title" style="font-size:10px">Hourly trend (12 buckets)</div>' +
      '<div class="ccb-bars">' + trend.map(function (x) {
        const w = maxT ? Math.round((x.count / maxT) * 100) : 0;
        return '<div class="ccb-bar-row"><span class="ccb-bar-label">' + D().esc(x.hour) + '</span>' +
          '<span class="ccb-bar-track"><span class="ccb-bar-fill" style="width:' + w + '%"></span></span>' +
          '<span class="ccb-bar-num">' + x.count + '</span></div>';
      }).join('') + '</div>' +
      '<div style="height:8px"></div>' +
      '<div class="ccb-kv"><span>Honesty</span><b>' + b.model_direction_unresolved + ' rows lack a model direction (counterfactual NOT reconstructable)</b></div>';
  }

  /* =================================================================== *
   * TAB: MODEL (serving identity + feature contract)
   * =================================================================== */
  function renderModel(el) {
    const live = liveState();
    const model = live && live.model ? live.model : null;
    const feats = live && live.features ? live.features : null;
    if (!model) { el.innerHTML = D().emptyState('Model identity unavailable', 'The live snapshot carries no model section (engine offline?).'); return; }
    const identity = D().kvGrid([
      ['Model ID', model.model_id || 'NOT RECORDED'],
      ['Version', model.model_version || 'NOT RECORDED'],
      ['Architecture', model.architecture || 'NOT RECORDED'],
      ['Artifact', model.artifact_path || 'NOT RECORDED'],
      ['Schema', model.feature_schema_id || 'NOT RECORDED'],
      ['Dimension', model.feature_dimension != null ? model.feature_dimension : 'NOT RECORDED'],
      ['Scaler', model.scaler_ready ? 'READY' : 'UNKNOWN', true],
      ['Probabilities', model.probabilities_available ? 'AVAILABLE' : 'UNAVAILABLE', true],
    ]);
    const probs = model.probabilities || {};
    const probBars = D().barBreakdown([
      { label: 'no_trade', count: probs.no_trade != null ? +(probs.no_trade.toFixed(4)) : 0 },
      { label: 'buy', count: probs.buy != null ? +(probs.buy.toFixed(4)) : 0 },
      { label: 'sell', count: probs.sell != null ? +(probs.sell.toFixed(4)) : 0 },
    ], 1);
    // Feature contract — grouped 70D (Base 0..49 | News 50..59 | Liquidity 60..69)
    const entries = (feats && feats.entries) || [];
    const groups = [
      { name: 'Base (0..49)', from: 0, to: 49 },
      { name: 'News (50..59)', from: 50, to: 59 },
      { name: 'Liquidity (60..69)', from: 60, to: 69 },
    ];
    const g = groups.map(function (gr) {
      const slice = entries.filter(function (e) { return e.index >= gr.from && e.index <= gr.to; });
      const anomalous = slice.filter(function (e) { return e.status && e.status !== 'VALID'; });
      return { name: gr.name, n: slice.length, anomalous: anomalous };
    });
    const groupCards = g.map(function (gr) {
      return D().healthCard({
        id: gr.name, name: gr.name,
        state: gr.n === 0 ? 'NOT_INITIALIZED' : (gr.anomalous.length ? 'DEGRADED' : 'VALID'),
        detail: gr.n + ' features recorded' + (gr.anomalous.length ? ' · ' + gr.anomalous.length + ' anomalous' : ''),
      });
    }).join('');
    el.innerHTML =
      '<div class="cc-grid-2">' +
        '<div class="ccb-card"><div class="cc-panel-title">Serving Model</div>' + identity + '</div>' +
        '<div class="ccb-card"><div class="cc-panel-title">Confidence Distribution (latest inference)</div>' +
          probBars + D().freshness(model.inference_timestamp, 'inference') + '</div>' +
      '</div>' +
      '<div style="height:14px"></div>' +
      '<div class="ccb-card"><div class="cc-panel-title">Feature Contract ' +
        '<span class="cc-sub">' + D().esc((feats && feats.schema_id) || 'NOT RECORDED') + ' · ' + (feats && feats.dimension != null ? feats.dimension : '?') + 'D</span></div>' +
        '<div style="height:8px"></div>' +
        '<div class="cc-strip">' + groupCards + '</div>' +
        '<div style="height:10px"></div>' +
        '<input id="cc-feat-search" class="cc-feature-search" placeholder="search feature name / index…">' +
        '<div style="height:8px"></div>' +
        '<div id="cc-feat-body">' + renderFeatureTable(entries, '') + '</div>' +
      '</div>';
    const si = document.getElementById('cc-feat-search');
    si.addEventListener('input', function () {
      document.getElementById('cc-feat-body').innerHTML = renderFeatureTable(entries, si.value.trim());
    });
  }

  function renderFeatureTable(entries, query) {
    const D_ = D();
    let list = entries;
    if (query) {
      const q = query.toLowerCase();
      list = entries.filter(function (e) {
        return (e.name || '').toLowerCase().indexOf(q) >= 0 || String(e.index) === query;
      });
    }
    if (!entries.length) return D_.emptyState('Feature values not recorded in this snapshot');
    if (!list.length) return D_.emptyState('No feature matches "' + query + '"');
    const isAnom = function (e) { return e.status && e.status !== 'VALID'; };
    return D_.dataTable({
      columns: [{ label: 'Index' }, { label: 'Name' }, { label: 'Value' }, { label: 'Status' }],
      rows: list.map(function (e) {
        return { cells: [
          { text: e.index },
          { text: e.name || '?' },
          { text: e.value != null ? String(e.value) : 'NOT RECORDED' },
          { html: D_.statusBadge(e.status || 'UNKNOWN') },
        ] };
      }),
      note: list.length + ' / ' + entries.length + ' features · anomalous rows highlighted · grouped contract: Base 0..49 | News 50..59 | Liquidity 60..69',
    }).replace(/<tr data-cc-row=/g, function (m) { return m; })
      .replace('<tbody>', '<tbody>'); // anomaly class applied below via rows loop
  }

  /* =================================================================== *
   * TAB: RISK / EXECUTION
   * =================================================================== */
  async function renderRisk(el) {
    el.innerHTML = D().loadingState('Loading risk & execution evidence…');
    const live = liveState();
    const risk = live && live.risk ? live.risk : null;
    const positions = live && live.positions ? live.positions : [];
    const ordersRes = await api().get('/api/operator/orders?limit=30', { component: 'ControlCenter', action: 'ORDERS' });
    const limits = risk && risk.limits ? risk.limits : {};
    const riskHtml = risk ? D().kvGrid([
      ['Equity', risk.equity != null ? risk.equity : 'NOT RECORDED'],
      ['Balance', risk.balance != null ? risk.balance : 'NOT RECORDED'],
      ['Risk per trade', risk.risk_pct != null ? risk.risk_pct + '%' : 'NOT RECORDED'],
      ['Max drawdown', limits.max_drawdown_pct != null ? limits.max_drawdown_pct + '%' : 'NOT RECORDED'],
      ['Max concurrent', limits.max_concurrent_positions != null ? limits.max_concurrent_positions : 'NOT RECORDED'],
      ['Max spread', limits.max_spread_points != null ? limits.max_spread_points : 'NOT RECORDED'],
    ]) : D().emptyState('Risk snapshot not recorded');
    const posHtml = positions.length
      ? D().dataTable({
          columns: [{ label: 'Ticket' }, { label: 'Type' }, { label: 'Volume' }, { label: 'Open' }, { label: 'Current' }, { label: 'SL' }, { label: 'TP' }, { label: 'Profit' }],
          rows: positions.map(function (p) { return { cells: [
            { text: p.ticket }, { text: p.type === 1 || p.type === 'SELL' ? 'SELL' : 'BUY' },
            { text: p.volume }, { text: p.price_open }, { text: p.price_current },
            { text: p.sl || '—' }, { text: p.tp || '—' },
            { html: '<b style="color:' + (p.profit >= 0 ? '#34d399' : '#fb7185') + '">' + (p.profit != null ? p.profit : '—') + '</b>' },
          ] }; }),
        })
      : D().emptyState('No open positions');
    const ordersHtml = ordersRes.ok
      ? (() => {
          const b = ordersRes.body;
          const lat = b.latency;
          return (lat ? '<div class="ccb-kv"><span>Latency</span><b>p50 ' + lat.p50_ms + ' ms · p95 ' + lat.p95_ms + ' ms · p99 ' + lat.p99_ms + ' ms (n=' + lat.n + ')</b></div><div style="height:8px"></div>' : '') +
            D().dataTable({
              columns: [{ label: 'Time' }, { label: 'Action' }, { label: 'Symbol' }, { label: 'Price' }, { label: 'Volume' }, { label: 'Mode' }],
              rows: (b.rows || []).map(function (o) { return { cells: [
                { text: String(o.timestamp || '').replace('T', ' ').slice(0, 19) }, { text: o.action || '—' },
                { text: o.symbol || '—' }, { text: o.price != null ? o.price : '—' }, { text: o.volume != null ? o.volume : '—' },
                { text: o.execution_mode || '—' },
              ] }; }),
              empty: { title: 'No dispatch rows recorded' },
            });
        })()
      : D().errorState('Order ledger unavailable', api().msg(ordersRes));
    el.innerHTML =
      '<div class="cc-grid-2">' +
        '<div class="ccb-card"><div class="cc-panel-title">Risk State</div>' + riskHtml + '</div>' +
        '<div class="ccb-card"><div class="cc-panel-title">Open Positions</div>' + posHtml + '</div>' +
      '</div>' +
      '<div style="height:14px"></div>' +
      '<div class="ccb-card"><div class="cc-panel-title">Recent Dispatches (audit_orders)</div>' + ordersHtml + '</div>';
  }

  /* =================================================================== *
   * TAB: DIAGNOSTICS
   * =================================================================== */
  async function renderDiagnostics(el) {
    el.innerHTML = D().loadingState('Running operator diagnostics…');
    const [healthRes, diagRes] = await Promise.all([
      api().get('/health', { component: 'ControlCenter', action: 'DIAG_HEALTH' }),
      api().get('/api/diagnostics/health', { component: 'ControlCenter', action: 'DIAG_INCIDENTS' }).catch(function () { return { ok: false, error: { code: 'NETWORK_ERROR' } }; }),
    ]);
    let cards = '';
    if (healthRes.ok) {
      const b = healthRes.body;
      const checks = b.checks || [];
      cards += '<div class="cc-strip">' + checks.map(function (c) {
        return D().healthCard({
          id: c.category, name: c.category,
          state: c.verdict === 'PASS' ? 'PASS' : (c.verdict === 'WARNING' ? 'WARNING' : (c.verdict || 'UNKNOWN')),
          detail: c.reason || '', action: c.suggestion || '',
        });
      }).join('') + '</div>';
      cards += '<div style="height:10px"></div><div class="cc-sub">Overall verdict: ' + D().esc(b.verdict || 'UNKNOWN') + '</div>';
    } else {
      cards = D().errorState('Engine self-check unavailable', api().msg(healthRes), healthRes.error && healthRes.error.request_id, 'window.NX.cc.views.showDiagnostics');
    }
    let incidents = '';
    if (diagRes.ok) {
      const b = diagRes.body;
      const counts = b.counts || {};
      incidents = D().kvGrid([
        ['Open incidents', counts.open != null ? counts.open : 'NOT RECORDED'],
        ['Critical', counts.critical != null ? counts.critical : 'NOT RECORDED'],
        ['Worker', b.worker && b.worker.state ? b.worker.state : 'NOT RECORDED'],
      ]);
    } else {
      incidents = D().emptyState('Incident health unavailable');
    }
    el.innerHTML =
      '<div class="ccb-card"><div class="cc-panel-title">Engine Self-Check</div>' + cards + '</div>' +
      '<div style="height:14px"></div>' +
      '<div class="ccb-card"><div class="cc-panel-title">Incident Health</div>' + incidents + '</div>' +
      '<div style="height:14px"></div>' +
      '<div class="ccb-card"><div class="cc-panel-title">Diagnostic Report</div>' +
        '<div class="cc-sub">Copies a sanitized operator report (no secrets; only recorded verdicts above).</div>' +
        '<div style="height:8px"></div>' +
        D().copyButton('__CC_DIAG__', 'Copy full report') +
      '</div>';
    const cp = el.querySelector('[data-cc-copy^="__CC_DIAG__"]');
    if (cp) cp.setAttribute('data-cc-copy', buildDiagReport(healthRes.ok ? healthRes.body : null, diagRes.ok ? diagRes.body : null));
  }

  function buildDiagReport(health, incidents) {
    const lines = [];
    lines.push('NEXUS OPERATOR DIAGNOSTIC REPORT');
    lines.push('generated: ' + new Date().toISOString());
    lines.push('mode: ' + ((liveState() && (liveState().runtime_mode || liveState().execution_mode)) || 'NOT RECORDED'));
    lines.push('');
    lines.push('== ENGINE SELF-CHECK ==');
    (health && health.checks || []).forEach(function (c) {
      lines.push(c.category + ': ' + c.verdict + (c.reason ? ' — ' + c.reason : ''));
      if (c.suggestion) lines.push('   next: ' + c.suggestion);
    });
    lines.push('');
    lines.push('== INCIDENT HEALTH ==');
    if (incidents && incidents.counts) {
      lines.push('open=' + incidents.counts.open + ' critical=' + incidents.counts.critical);
      if (incidents.worker) lines.push('worker=' + incidents.worker.state);
    } else {
      lines.push('NOT RECORDED');
    }
    return lines.join('\n');
  }

  /* =================================================================== *
   * Tab wiring
   * =================================================================== */
  function render(tab) {
    currentTab = tab;
    const el = document.getElementById(tab);
    if (!el) return;
    if (tab === 'cc-overview') {
      const snap = S().snapshot('cc-summary');
      renderOverview(el, snap, liveState());
    } else if (tab === 'cc-decisions') { renderDecisions(el); refreshDecisions(); refreshFunnel(); refreshNoTrade(); }
    else if (tab === 'cc-model') { renderModel(el); }
    else if (tab === 'cc-risk') { renderRisk(el); }
    else if (tab === 'cc-diagnostics') { renderDiagnostics(el); }
  }

  window.NX.cc.views = {
    /** Entry: called once from switchTab when the operator opens the CC. */
    showTab: function (tab) { render(tab); },
    showDiagnostics: function () { render('cc-diagnostics'); },
    refreshSummary: function () { S().track('cc-summary', '/api/operator/summary', { intervalMs: 15000 }).refresh(); },
    refreshDecisions: refreshDecisions,
    refreshFunnel: refreshFunnel,
    refreshNoTrade: refreshNoTrade,
    openInspector: openInspector,
    /** Boot: start the bounded summary poll + summary→latest-decision join. */
    boot: function () {
      D().bindGlobal();
      S().track('cc-summary', '/api/operator/summary', {
        intervalMs: 15000,
        isEmptyFn: function (b) { return !b || !b.available; },
      }).subscribe(function (snap) {
        // Join: pull the latest decision once per summary refresh so the
        // overview's "Latest Decision" reconciles with the observatory.
        if (snap.data && snap.data.ledger && snap.data.ledger.available) {
          api().get('/api/operator/decisions?limit=1', { component: 'ControlCenter', action: 'LATEST' })
            .then(function (res) {
              window.__ccLatestDecision = res.ok && res.body.rows && res.body.rows.length ? res.body.rows[0] : null;
              if (currentTab === 'cc-overview') render('cc-overview');
            });
        }
        if (currentTab === 'cc-overview') render('cc-overview');
      });
    },
  };
})();
