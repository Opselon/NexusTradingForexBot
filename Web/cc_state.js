/* =========================================================================
 * NEXUS CONTROL CENTER — UI state machine & polling engine (NX.cc.state)
 * -------------------------------------------------------------------------
 * CHG-0043 / TASK-CONTROL-CENTER (Agent: Hermes-UI)
 *
 * Explicit finite state per tracked view-model: LOADING → READY | STALE |
 * ERROR | EMPTY. Impossible combinations are unrepresentable (state is one
 * enum, not scattered booleans).
 *
 * Polling model (bounded, visibility-aware, no WebSocket complexity unless
 * the backend already offers it — it offers SSE, which app.js owns):
 *   - bounded interval per resource (never 1s-everything),
 *   - document.hidden pauses timers (visibilitychange),
 *   - fetch timeout + exponential backoff on repeated errors (capped),
 *   - in-flight dedup (NX.api already dedupes GETs; we add staleness),
 *   - STALE promotion when the last success ages past 2× interval,
 *   - cancellation via unsubscribe handles (no leaks on re-render).
 * ========================================================================= */
window.NX = window.NX || {};
window.NX.cc = window.NX.cc || {};

(function () {
  'use strict';

  const DEFAULTS = {
    intervalMs: 15000,   // bounded default poll
    timeoutMs: 8000,     // fetch abort budget
    backoffMaxMs: 60000, // error backoff cap
    staleFactor: 2.5,    // STALE after 2.5× interval without success
  };

  const resources = Object.create(null);

  function now() { return Date.now(); }

  function computeState(r) {
    if (r.fetching) return 'LOADING';
    if (!r.lastOkAt) return 'ERROR';
    const age = now() - r.lastOkAt;
    if (age > r.opts.intervalMs * r.opts.staleFactor) return 'STALE';
    if (r.lastEmpty) return 'EMPTY';
    return 'READY';
  }

  function notify(r) {
    const snapshot = {
      key: r.key,
      state: computeState(r),
      data: r.data,
      error: r.error,
      lastOkAt: r.lastOkAt,
      fetchedAt: r.fetchedAt,
    };
    r.listeners.forEach(function (fn) {
      try { fn(snapshot); } catch (e) { console.warn('[CC_STATE] listener failed', r.key, e); }
    });
  }

  function schedule(r) {
    clearTimeout(r.timer);
    const delay = r.errorStreak > 0
      ? Math.min(r.opts.intervalMs * Math.pow(2, r.errorStreak), r.opts.backoffMaxMs)
      : r.opts.intervalMs;
    r.timer = setTimeout(function () { tick(r); }, delay);
  }

  async function tick(r) {
    if (r.stopped) return; // untracked mid-flight: never reschedule
    if (typeof document !== 'undefined' && document.hidden) {
      // Visibility-aware: skip the fetch, retry shortly after wake.
      schedule(r);
      return;
    }
    r.fetching = true;
    notify(r);
    const ctl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const kill = ctl ? setTimeout(function () { ctl.abort(); }, r.opts.timeoutMs) : null;
    try {
      const res = await window.NX.api.get(r.url, { component: 'ControlCenter', action: 'POLL', signal: ctl && ctl.signal });
      if (kill) clearTimeout(kill);
      if (res.ok) {
        r.errorStreak = 0;
        r.lastOkAt = now();
        r.fetchedAt = now();
        const body = res.body || {};
        r.lastEmpty = r.isEmptyFn ? r.isEmptyFn(body) : false;
        r.data = body;
      } else {
        r.errorStreak += 1;
        r.error = res.error || { code: 'REQUEST_FAILED', message: 'Request failed.' };
        r.lastErrorAt = now();
        notify(r);
      }
    } catch (err) {
      if (kill) clearTimeout(kill);
      r.errorStreak += 1;
      r.error = { code: 'NETWORK_ERROR', message: String(err && err.message || err) };
      r.lastErrorAt = now();
      notify(r);
    } finally {
   r.fetching = false;
   if (!r.stopped) {
     notify(r);
     schedule(r);
   }
 }
 }

  window.NX.cc.state = {
    /** Track a resource. Returns {unsubscribe(), refresh()}. */
    track: function (key, url, opts) {
      if (resources[key]) return resources[key].handle;
      const o = Object.assign({}, DEFAULTS, opts || {});
      const r = {
        key: key, url: url, opts: o, listeners: [],
        data: null, error: null, fetching: false,
        lastOkAt: 0, lastErrorAt: 0, lastEmpty: false, errorStreak: 0,
        timer: null, isEmptyFn: (opts && opts.isEmptyFn) || null,
      };
      r.handle = {
        subscribe: function (fn) {
          r.listeners.push(fn);
          // Immediate snapshot so the subscriber renders from current truth.
          fn({ key: r.key, state: computeState(r), data: r.data, error: r.error, lastOkAt: r.lastOkAt });
          return function () {
            const i = r.listeners.indexOf(fn);
            if (i >= 0) r.listeners.splice(i, 1);
          };
        },
        refresh: function () { r.errorStreak = 0; tick(r); },
      };
      resources[key] = r;
      // First fetch immediately (visibility still respected inside tick).
      tick(r);
      return r.handle;
    },

    /** Stop polling and drop all state for a key. */
    untrack: function (key) {
      const r = resources[key];
      if (!r) return;
      r.stopped = true;
      clearTimeout(r.timer);
      delete resources[key];
    },

    /** Read-only snapshot for synchronous renders/tests. */
    snapshot: function (key) {
      const r = resources[key];
      if (!r) return { key: key, state: 'ERROR', data: null, error: { code: 'NOT_TRACKED' } };
      return { key: r.key, state: computeState(r), data: r.data, error: r.error, lastOkAt: r.lastOkAt };
    },

    /** Testing hook: reset everything. */
    _reset: function () {
      Object.keys(resources).forEach(function (k) {
        resources[k].stopped = true;
        clearTimeout(resources[k].timer);
        delete resources[k];
      });
    },

    _internals: { computeState: computeState, DEFAULTS: DEFAULTS },
  };
})();
