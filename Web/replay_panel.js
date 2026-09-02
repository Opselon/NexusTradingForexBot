/*CHG-0043 replay-on-chart frontend (Web/replay_panel.js).

Wires the EXISTING dashboard to the real replay pipeline (REPLAY_API v1):

* Session panel: dataset contract inputs -> POST /api/replay/session
* Transport controls: step / play / pause / reset / seek -> /api/replay/control
* Cursor strip: KNOWN (rendered candles up to cursor) vs UNKNOWN (dimmed
  placeholder region) — the chart NEVER renders future indicators as known
* Cursor state: /api/replay/state -> price, counts, equity, open position,
  regime, transitions
* NO_TRADE drill-down: /api/replay/decision -> engine-truth evidence table

Design rule (brief section 21): the panel consumes ONLY cursor-bounded state;
future data is a COUNT, never a payload the strategy or indicators can read.
*/
(function () {
  'use strict';

  const S = {
    replayId: null,
    timer: null,
    playing: false,
    lastState: null,
  };

  function el(id) { return document.getElementById(id); }

  function fmt(x, d) {
    if (x === null || x === undefined) return '—';
    return Number(x).toFixed(d === undefined ? 2 : d);
  }

  async function api(method, path, body) {
    const opts = { method, headers: { 'X-Request-ID': 'replay_' + Date.now().toString(36) } };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      console.warn('[REPLAY_UI] ' + path + ' -> ' + res.status, json);
      throw new Error((json && json.detail) || ('HTTP ' + res.status));
    }
    return json;
  }

  // ---- session creation -------------------------------------------------
  async function createSession() {
    const start = el('rp-start').value;
    const end = el('rp-end').value;
    if (!start || !end) { setMsg('Start/end time required'); return; }
    const payload = {
      dataset_id: 'UI-M1-' + new Date().toISOString().slice(0, 10).replace(/-/g, ''),
      dataset_fingerprint: 'uipick-' + start + '-' + end,
      symbol: 'XAUUSD',
      replay_mode: 'BAR_REPLAY',
      start_time: start,
      end_time: end,
      git_commit: '',
      regime_enabled: !!(el('rp-regime') && el('rp-regime').checked),
    };
    try {
      const res = await api('POST', '/api/replay/session', payload);
      S.replayId = res.replay_id;
      setMsg('Session ' + res.replay_id + ' ready · 70D model bound');
      refreshState();
    } catch (e) {
      setMsg('Session failed: ' + e.message);
    }
  }

  // ---- transport ---------------------------------------------------------
  async function control(action, extra) {
    if (!S.replayId) { setMsg('Create a session first'); return; }
    const body = Object.assign({ action: action, replay_id: S.replayId }, extra || {});
    try {
      const res = await api('POST', '/api/replay/control', body);
      const r = res.result || {};
      if (r.status === 'END_OF_DATA') { stopPlay(); setMsg('END_OF_DATA reached'); }
      refreshState();
    } catch (e) {
      setMsg(action + ' failed: ' + e.message);
      stopPlay();
    }
  }

  function startPlay() {
    if (S.playing) return;
    S.playing = true;
    const speed = Math.max(1, parseInt((el('rp-speed') || {}).value || '1', 10) || 1);
    const intervalMs = Math.max(60, 800 / speed);
    S.timer = setInterval(() => control('step_bar', { n: 1 }), intervalMs);
    const btn = el('rp-play');
    if (btn) btn.innerHTML = '<i class="fa-solid fa-pause"></i> Pause';
  }

  function stopPlay() {
    S.playing = false;
    if (S.timer) { clearInterval(S.timer); S.timer = null; }
    const btn = el('rp-play');
    if (btn) btn.innerHTML = '<i class="fa-solid fa-play"></i> Play';
  }

  function togglePlay() { if (S.playing) { stopPlay(); control('pause'); } else { startPlay(); } }

  function seekTo() {
    const t = el('rp-seek').value;
    if (!t) { setMsg('Seek time required'); return; }
    stopPlay();
    control('seek', { seek_time: new Date(t).toISOString() });
  }

  // ---- cursor state -------------------------------------------------------
  async function refreshState() {
    if (!S.replayId) return;
    try {
      const st = await api('GET', '/api/replay/state?replay_id=' + encodeURIComponent(S.replayId));
      S.lastState = st;
      renderState(st);
      drawKnownBoundary(st);
    } catch (e) { /* session gone */ }
  }

  function renderState(st) {
    const set = (id, v) => { const n = el(id); if (n) n.textContent = v; };
    set('rp-clock', st.clock || '—');
    set('rp-phase', st.phase || '—');
    const c = st.counts || {};
    set('rp-bars', c.bars != null ? c.bars : '—');
    set('rp-decisions', c.decisions != null ? c.decisions : '—');
    set('rp-known', st.known_events != null ? st.known_events : '—');
    set('rp-unknown', st.unknown_events != null ? st.unknown_events : '—');
    set('rp-equity', st.equity != null ? fmt(st.equity, 2) : '—');
    const lp = st.last_price || {};
    set('rp-price', lp.close != null ? fmt(lp.close, 2) : (lp.bid != null ? fmt(lp.bid, 2) : '—'));
    const op = st.open_position;
    set('rp-position', op ? (op.direction + ' @ ' + fmt(op.entry_price, 2)) : 'FLAT');
    const rg = st.regime;
    set('rp-regime-now', rg && rg.regime ? rg.regime : (st.regime_enabled ? 'WARMUP' : 'DISABLED'));
  }

  // ---- KNOWN/UNKNOWN chart boundary ---------------------------------------
  function drawKnownBoundary(st) {
    // Uses the global candle chart canvas when present: a vertical accent line
    // at the cursor + dimming of the UNKNOWN region (right of the line).
    const canvas = el('candleChart');
    if (!canvas || !candleData || !candleData.length) return;
    const ctx = canvas.getContext('2d');
    const clock = st.clock;
    if (!clock) return;
    let idx = -1;
    for (let i = candleData.length - 1; i >= 0; i--) {
      if (candleData[i].time && candleData[i].time.slice(0, 16) <= clock.slice(0, 16)) { idx = i; break; }
    }
    if (idx < 0) return;
    const rect = canvas.getBoundingClientRect();
    const w = rect.width; const h = rect.height;
    const x = chartPanX + idx * (candleWidth + candleGap) + candleWidth / 2;
    if (x < 0 || x > w) return;
    // UNKNOWN region: dim overlay right of the cursor (data exists locally but
    // is NOT decision-visible at T — brief section 7/21)
    ctx.save();
    ctx.fillStyle = 'rgba(2, 6, 23, 0.55)';
    ctx.fillRect(x, 0, w - x, h);
    ctx.strokeStyle = '#22d3ee';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#22d3ee';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('REPLAY CURSOR (KNOWN)', Math.min(x + 4, w - 130), 12);
    ctx.fillText('FUTURE = UNKNOWN', Math.min(x + 4, w - 130), 24);
    ctx.restore();
  }

  // ---- NO_TRADE drill-down --------------------------------------------------
  async function loadDecision(seq) {
    if (!S.replayId) return;
    try {
      const res = await api('GET', '/api/replay/decision?seq=' + seq + '&replay_id=' + encodeURIComponent(S.replayId));
      const d = res.decision || {};
      const box = el('rp-decision-box');
      if (!box) return;
      const rows = [
        ['Timestamp', d.ts], ['Action', d.action], ['Confidence', fmt(d.confidence, 4)],
        ['Regime', d.regime || '—'], ['Reason', d.reason_code || '—'],
        ['Blocked by', d.blocked_by || '—'], ['Stage', d.decision_stage || '—'],
        ['Entry/SL/TP', [d.entry, d.stop_loss, d.take_profit].map((v) => (v ? fmt(v, 2) : '—')).join(' / ')],
        ['Risk accepted', String(d.risk_accepted)],
        ['Probs (N/B/S/W)', (d.probs || []).map((p) => fmt(p, 3)).join(' | ')],
      ];
      box.innerHTML = rows.map(([k, v]) =>
        '<div class="flex justify-between text-xs py-0.5"><span class="text-textMuted">' +
        k + '</span><span class="font-mono text-white">' + (v == null ? '—' : v) +
        '</span></div>').join('');
    } catch (e) {
      setMsg('Decision ' + seq + ': ' + e.message);
    }
  }

  function setMsg(m) { const n = el('rp-msg'); if (n) n.textContent = m; }

  // ---- boot ------------------------------------------------------------------
  function init() {
    if (!el('rp-create')) return; // panel not mounted
    el('rp-create').addEventListener('click', createSession);
    el('rp-play').addEventListener('click', togglePlay);
    el('rp-step').addEventListener('click', () => { stopPlay(); control('step_bar', { n: 1 }); });
    el('rp-reset').addEventListener('click', () => { stopPlay(); control('reset'); });
    el('rp-seek-btn').addEventListener('click', seekTo);
    el('rp-decision-load').addEventListener('click', () => loadDecision(parseInt(el('rp-decision-seq').value || '0', 10)));
    // default window: last 8 hours of the available dataset window
    const endInput = el('rp-end');
    if (endInput && !endInput.value) {
      const now = new Date();
      endInput.value = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    }
    const startInput = el('rp-start');
    if (startInput && !startInput.value) {
      const now = new Date();
      startInput.value = new Date(now.getTime() - now.getTimezoneOffset() * 60000 - 8 * 3600 * 1000).toISOString().slice(0, 16);
    }
    console.log('[REPLAY_UI] panel ready');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // export for inline handlers
  window.ReplayPanel = { createSession, control, togglePlay, loadDecision, refreshState };
})();
