/* =========================================================================
 * NEXUS CONTROL CENTER — Design System & Component Library (NX.cc.design)
 * -------------------------------------------------------------------------
 * CHG-0043 / TASK-CONTROL-CENTER (Agent: Hermes-UI)
 *
 * Reusable, dependency-free components shared by the Control Center views.
 * Every component enforces the brief's semantics:
 *   - explicit health states (HEALTHY/DEGRADED/BLOCKED/UNAVAILABLE/DISABLED/
 *     NOT_CONFIGURED/RECOVERING/UNKNOWN) — never collapsed to green/red;
 *   - non-healthy states carry WHAT/WHY/SINCE/IMPACT/WHAT-TO-DO when the
 *     backend supplies them (never invented);
 *   - meaning is never encoded by color alone (glyph + text always);
 *   - empty/error/loading are first-class rendered states;
 *   - dangerous actions require structured confirmation (action, current
 *     state, impact, recovery) — never a bare "Are you sure?".
 * No fake data anywhere: helpers render explicit "—" / NOT RECORDED
 * placeholders rather than zeros or fabricated values.
 * ========================================================================= */
window.NX = window.NX || {};
window.NX.cc = window.NX.cc || {};

(function () {
  'use strict';

  /* ------------------------------------------------------------------ *
   * State → visual vocabulary (single source; color + glyph + label)
   * ------------------------------------------------------------------ */
  const STATES = {
    HEALTHY:       { cls: 'ok',    glyph: '\u2713', label: 'HEALTHY' },
    READY:         { cls: 'ok',    glyph: '\u2713', label: 'READY' },
    PASS:          { cls: 'ok',    glyph: '\u2713', label: 'PASS' },
    AVAILABLE:     { cls: 'ok',    glyph: '\u2713', label: 'AVAILABLE' },
    FRESH:         { cls: 'ok',    glyph: '\u2713', label: 'FRESH' },
    ACTIVE:        { cls: 'ok',    glyph: '\u2713', label: 'ACTIVE' },
    ENABLED:       { cls: 'ok',    glyph: '\u2713', label: 'ENABLED' },
    VALID:         { cls: 'ok',    glyph: '\u2713', label: 'VALID' },
    DEGRADED:      { cls: 'warn',  glyph: '\u26A0', label: 'DEGRADED' },
    WARNING:       { cls: 'warn',  glyph: '\u26A0', label: 'WARNING' },
    STALE:         { cls: 'warn',  glyph: '\u26A0', label: 'STALE' },
    RECOVERING:    { cls: 'warn',  glyph: '\u21BB', label: 'RECOVERING' },
    BLOCKED:       { cls: 'bad',   glyph: '\u26D4', label: 'BLOCKED' },
    FAIL:          { cls: 'bad',   glyph: '\u2717', label: 'FAIL' },
    ERROR:         { cls: 'bad',   glyph: '\u2717', label: 'ERROR' },
    UNAVAILABLE:   { cls: 'bad',   glyph: '\u00D8', label: 'UNAVAILABLE' },
    DISABLED:      { cls: 'muted', glyph: '\u24D8', label: 'DISABLED' },
    NOT_CONFIGURED:{ cls: 'muted', glyph: '\u24D8', label: 'NOT CONFIGURED' },
    NOT_INITIALIZED:{cls: 'muted', glyph: '\u24D8', label: 'NOT INITIALIZED' },
    NOT_APPLICABLE:{ cls: 'muted', glyph: '\u2013', label: 'N/A' },
    NOT_RECORDED:  { cls: 'muted', glyph: '\u24D8', label: 'NOT RECORDED' },
    UNKNOWN:       { cls: 'muted', glyph: '?',     label: 'UNKNOWN' },
  };

  const LIVE_MODES = {
    LIVE:    { cls: 'live',    label: 'LIVE' },
    PAPER:   { cls: 'paper',   label: 'PAPER (SIM)' },
    SHADOW:  { cls: 'shadow',  label: 'SHADOW' },
    REPLAY:  { cls: 'replay',  label: 'REPLAY' },
    RESEARCH:{ cls: 'research',label: 'RESEARCH' },
  };

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function normState(s) {
    if (s == null || s === '') return STATES.UNKNOWN;
    return STATES[String(s).toUpperCase().replace(/[\s-]+/g, '_')] || STATES.UNKNOWN;
  }

  /* ------------------------------------------------------------------ *
   * StatusBadge — glyph + label, color never the only signal
   * ------------------------------------------------------------------ */
  function statusBadge(state, opts) {
    const st = normState(state);
    const o = opts || {};
    return '<span class="ccb-badge ccb-' + st.cls + (o.big ? ' ccb-big' : '') + '"' +
      (o.title ? ' title="' + esc(o.title) + '"' : '') + '>' +
      '<span class="ccb-glyph" aria-hidden="true">' + st.glyph + '</span> ' +
      esc(o.label || st.label) + '</span>';
  }

  /* ------------------------------------------------------------------ *
   * ModeBadge — LIVE/PAPER/SHADOW/REPLAY/RESEARCH separation
   * ------------------------------------------------------------------ */
  function modeBadge(mode) {
    const m = LIVE_MODES[String(mode || '').toUpperCase()] ||
      { cls: 'muted', label: String(mode || 'UNKNOWN').toUpperCase() };
    return '<span class="ccb-mode ccb-mode-' + m.cls + '" role="status">' +
      '<span class="ccb-glyph" aria-hidden="true">' + (m.cls === 'live' ? '\u25CF' : '\u25CB') + '</span> ' +
      esc(m.label) + '</span>';
  }

  /* ------------------------------------------------------------------ *
   * FreshnessIndicator — data age with honest STALE promotion
   * ------------------------------------------------------------------ */
  function freshAge(ts) {
    if (!ts) return { state: 'UNKNOWN', text: 'no timestamp', sec: null };
    const t = Date.parse(ts);
    if (isNaN(t)) return { state: 'UNKNOWN', text: 'unparseable timestamp', sec: null };
    const sec = (Date.now() - t) / 1000;
    if (sec < 0) return { state: 'UNKNOWN', text: 'clock skew (future timestamp)', sec: 0 };
    if (sec < 15) return { state: 'FRESH', text: sec.toFixed(1) + 's ago', sec: sec };
    if (sec < 60) return { state: 'FRESH', text: Math.round(sec) + 's ago', sec: sec };
    if (sec < 300) return { state: 'DEGRADED', text: Math.round(sec / 60) + 'm ago', sec: sec };
    return { state: 'STALE', text: Math.round(sec / 60) + 'm ago', sec: sec };
  }

  function freshness(ts, label) {
    const f = freshAge(ts);
    return '<span class="ccb-fresh ccb-fresh-' + normState(f.state).cls + '">' +
      (label ? '<span class="ccb-fresh-label">' + esc(label) + '</span> ' : '') +
      statusBadge(f.state, { label: f.text }) + '</span>';
  }

  /* ------------------------------------------------------------------ *
   * HealthCard — one subsystem: state + what/why/impact/action
   * ------------------------------------------------------------------ */
  function healthCard(card) {
    const st = normState(card.state);
    let body = '';
    if (card.detail) body += '<div class="ccb-kv"><span>Detail</span><b>' + esc(card.detail) + '</b></div>';
    if (card.why) body += '<div class="ccb-kv"><span>Why</span><b>' + esc(card.why) + '</b></div>';
    if (card.since) body += '<div class="ccb-kv"><span>Since</span><b>' + esc(card.since) + '</b></div>';
    if (card.impact) body += '<div class="ccb-kv"><span>Impact</span><b>' + esc(card.impact) + '</b></div>';
    if (card.action) body += '<div class="ccb-kv"><span>Next</span><b>' + esc(card.action) + '</b></div>';
    if (!body) body = '<div class="ccb-empty-inline">No additional evidence recorded.</div>';
    return '<div class="ccb-card ccb-card-' + st.cls + '" data-cc-card="' + esc(card.id || '') + '">' +
      '<div class="ccb-card-head">' +
        '<span class="ccb-card-name">' + esc(card.name || 'SUBSYSTEM') + '</span>' +
        statusBadge(card.state, { title: card.detail || '' }) +
      '</div>' + body +
      (card.drillId ? '<button class="ccb-btn ccb-btn-sm" data-cc-drill="' + esc(card.drillId) + '">Inspect \u2192</button>' : '') +
      '</div>';
  }

  /* ------------------------------------------------------------------ *
   * MetricCard — label + value + provenance/freshness footer
   * ------------------------------------------------------------------ */
  function metricCard(m) {
    const val = (m.value === null || m.value === undefined || m.value === '') ?
      '<span class="ccb-norecord">NOT RECORDED</span>' : esc(m.value);
    return '<div class="ccb-card" data-cc-metric="' + esc(m.id || '') + '">' +
      '<div class="ccb-metric-label">' + esc(m.label || '') + '</div>' +
      '<div class="ccb-metric-value">' + val + '</div>' +
      (m.footer ? '<div class="ccb-metric-footer">' + m.footer + '</div>' : '') +
      '</div>';
  }

  /* ------------------------------------------------------------------ *
   * Empty / Error / Loading states
   * ------------------------------------------------------------------ */
  function emptyState(title, hint) {
    return '<div class="ccb-state ccb-state-empty" role="status">' +
      '<div class="ccb-state-glyph" aria-hidden="true">\u2205</div>' +
      '<div class="ccb-state-title">' + esc(title || 'No data yet') + '</div>' +
      (hint ? '<div class="ccb-state-hint">' + esc(hint) + '</div>' : '') +
      '</div>';
  }

  function errorState(title, detail, requestId, retryFnName) {
    return '<div class="ccb-state ccb-state-error" role="alert">' +
      '<div class="ccb-state-glyph" aria-hidden="true">\u26A0</div>' +
      '<div class="ccb-state-title">' + esc(title || 'Request failed') + '</div>' +
      (detail ? '<div class="ccb-state-hint">' + esc(detail) + '</div>' : '') +
      (requestId ? '<div class="ccb-rid">request <code>' + esc(requestId) + '</code></div>' : '') +
      (retryFnName ? '<button class="ccb-btn ccb-btn-sm" onclick="' + esc(retryFnName) + '()">Retry</button>' : '') +
      '</div>';
  }

  function loadingState(label) {
    return '<div class="ccb-state ccb-state-loading" role="status" aria-live="polite">' +
      '<div class="ccb-state-glyph ccb-spin" aria-hidden="true">\u21BB</div>' +
      '<div class="ccb-state-title">' + esc(label || 'Loading…') + '</div></div>';
  }

  /* ------------------------------------------------------------------ *
   * ConfirmDialog — structured consequence confirmation
   * Rows: ACTION / CURRENT STATE / IMPACT / RECOVERY; buttons CANCEL /
   * CONFIRM <VERB>. Returns via onConfirm callback; ENTER confirms,
   * ESC cancels; focus is trapped while open. No generic "Are you sure?".
   * ------------------------------------------------------------------ */
  function confirmDialog(spec) {
    // spec: {action, current, impact, recovery, confirmVerb, onConfirm, onCancel}
    const existing = document.getElementById('ccb-confirm-overlay');
    if (existing) existing.remove();
    const overlay = document.createElement('div');
    overlay.id = 'ccb-confirm-overlay';
    overlay.className = 'ccb-confirm-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Confirm ' + (spec.action || 'action'));
    const rows = [
      ['ACTION', spec.action],
      ['CURRENT STATE', spec.current],
      ['IMPACT', spec.impact],
      ['RECOVERY', spec.recovery],
    ].filter(function (r) { return r[1] !== undefined && r[1] !== null && r[1] !== ''; });
    overlay.innerHTML =
      '<div class="ccb-confirm">' +
      '<div class="ccb-confirm-title">Operator action confirmation</div>' +
      rows.map(function (r) {
        return '<div class="ccb-confirm-row"><span>' + esc(r[0]) + '</span><b>' + esc(r[1]) + '</b></div>';
      }).join('') +
      '<div class="ccb-confirm-btns">' +
      '<button class="ccb-btn" id="ccb-confirm-cancel">CANCEL</button>' +
      '<button class="ccb-btn ccb-btn-danger" id="ccb-confirm-ok">CONFIRM ' + esc(spec.confirmVerb || 'ACTION') + '</button>' +
      '</div></div>';
    document.body.appendChild(overlay);
    function close(result) {
      overlay.remove();
      document.removeEventListener('keydown', onKey, true);
      if (result && typeof spec.onConfirm === 'function') spec.onConfirm();
      else if (typeof spec.onCancel === 'function') spec.onCancel();
    }
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); close(false); }
      else if (e.key === 'Enter') { e.preventDefault(); close(true); }
    }
    overlay.querySelector('#ccb-confirm-cancel').addEventListener('click', function () { close(false); });
    overlay.querySelector('#ccb-confirm-ok').addEventListener('click', function () { close(true); });
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(false); });
    document.addEventListener('keydown', onKey, true);
    overlay.querySelector('#ccb-confirm-cancel').focus();
  }

  /* ------------------------------------------------------------------ *
   * CopyButton + Toast
   * ------------------------------------------------------------------ */
  function copyButton(text, label) {
    return '<button class="ccb-btn ccb-btn-sm" data-cc-copy="' + esc(text) + '">' +
      esc(label || 'Copy') + '</button>';
  }

  let toastTimer = null;
  function toast(msg, kind) {
    let t = document.getElementById('ccb-toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'ccb-toast';
      t.className = 'ccb-toast';
      t.setAttribute('role', 'status');
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.className = 'ccb-toast ccb-toast-' + (normState(kind || 'READY').cls) + ' ccb-toast-show';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove('ccb-toast-show'); }, 2600);
  }

  /* ------------------------------------------------------------------ *
   * DataTable — sortable headers, progressive disclosure, copyable ids
   * rows: array of {cells:[{html|text, cls}], expand: html|undefined}
   * ------------------------------------------------------------------ */
  function dataTable(spec) {
    // spec: {columns:[{label, cls}], rows:[{cells, expand}], empty, note}
    if (!spec.rows || !spec.rows.length) {
      return spec.empty ? emptyState(spec.empty.title, spec.empty.hint) : emptyState();
    }
    const head = spec.columns.map(function (c, i) {
      return '<th scope="col" class="' + esc(c.cls || '') + '">' + esc(c.label) + '</th>';
    }).join('');
    const body = spec.rows.map(function (r, ri) {
      const cells = r.cells.map(function (c) {
        return '<td class="' + esc(c.cls || '') + '">' + (c.html != null ? c.html : esc(c.text)) + '</td>';
      }).join('');
      const exp = r.expand ?
        '<tr class="ccb-row-expand"><td colspan="' + spec.columns.length + '">' + r.expand + '</td></tr>' : '';
      return '<tr data-cc-row="' + ri + '">' + cells + '</tr>' + exp;
    }).join('');
    return '<div class="ccb-tablewrap"><table class="ccb-table">' +
      '<thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div>' +
      (spec.note ? '<div class="ccb-table-note">' + esc(spec.note) + '</div>' : '');
  }

  /* ------------------------------------------------------------------ *
   * Horizontal distribution bars (funnel / NO_TRADE reasons)
   * values: [{label, count}] normalized against total
   * ------------------------------------------------------------------ */
  function barBreakdown(values, total) {
    if (!values || !values.length) return emptyState('No rows in this window');
    const sum = (total != null) ? total : values.reduce(function (a, v) { return a + v.count; }, 0);
    const max = values.reduce(function (a, v) { return Math.max(a, v.count); }, 0);
    return '<div class="ccb-bars">' + values.map(function (v) {
      const pct = sum ? Math.round((v.count / sum) * 1000) / 10 : 0;
      const w = max ? Math.round((v.count / max) * 100) : 0;
      return '<div class="ccb-bar-row" data-cc-bar="' + esc(v.label) + '" title="' + esc(v.label) + ': ' + v.count + ' (' + pct + '%)">' +
        '<span class="ccb-bar-label">' + esc(v.label) + '</span>' +
        '<span class="ccb-bar-track"><span class="ccb-bar-fill" style="width:' + w + '%"></span></span>' +
        '<span class="ccb-bar-num">' + v.count + ' <em>(' + pct + '%)</em></span>' +
        '</div>';
    }).join('') + '</div>';
  }

  /* ------------------------------------------------------------------ *
   * Cell renderers shared by decision views
   * ------------------------------------------------------------------ */
  function probCell(p) {
    if (p == null || (typeof p !== 'number')) {
      return '<span class="ccb-norecord">EVIDENCE NOT RECORDED</span>';
    }
    return (p * 100).toFixed(1) + '%';
  }

  function kvGrid(pairs) {
    const rows = pairs.filter(function (r) { return r[1] !== null && r[1] !== undefined && r[1] !== ''; });
    if (!rows.length) return '<div class="ccb-empty-inline">EVIDENCE NOT RECORDED</div>';
    return '<div class="ccb-kvgrid">' + rows.map(function (r) {
      return '<div class="ccb-kv"><span>' + esc(r[0]) + '</span><b>' +
        (r[2] ? r[1] : esc(r[1])) + '</b></div>'; // r[2] raw-html flag
    }).join('') + '</div>';
  }

  /* ------------------------------------------------------------------ *
   * Global click delegation: copy buttons + drill buttons
   * ------------------------------------------------------------------ */
  function bindGlobal() {
    if (window.__ccDesignBound) return;
    window.__ccDesignBound = true;
    document.addEventListener('click', function (e) {
      const cp = e.target.closest && e.target.closest('[data-cc-copy]');
      if (cp) {
        const text = cp.getAttribute('data-cc-copy');
        const done = function () { toast('Copied to clipboard', 'READY'); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, function () { toast('Copy failed', 'ERROR'); });
        } else {
          toast('Clipboard unavailable', 'ERROR');
        }
      }
      const dr = e.target.closest && e.target.closest('[data-cc-drill]');
      if (dr) {
        const fn = window.NX.cc && window.NX.cc.drill;
        if (typeof fn === 'function') fn(dr.getAttribute('data-cc-drill'));
      }
    });
  }

  window.NX.cc.design = {
    STATES: STATES,
    esc: esc,
    normState: normState,
    statusBadge: statusBadge,
    modeBadge: modeBadge,
    freshAge: freshAge,
    freshness: freshness,
    healthCard: healthCard,
    metricCard: metricCard,
    emptyState: emptyState,
    errorState: errorState,
    loadingState: loadingState,
    confirmDialog: confirmDialog,
    copyButton: copyButton,
    toast: toast,
    dataTable: dataTable,
    barBreakdown: barBreakdown,
    probCell: probCell,
    kvGrid: kvGrid,
    bindGlobal: bindGlobal,
  };
})();
