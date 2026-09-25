/* =========================================================================
 * NEXUS CONTROL CENTER — Central API Client & Error Contract (app.js part 1)
 * -------------------------------------------------------------------------
 * Every HTTP call in the dashboard goes through `nx.api`. It:
 *   - attaches a correlation request_id (X-Request-ID) to every request,
 *   - parses the SAFE server error envelope {error:{code,message,request_id}},
 *   - logs a [UI_ERROR] diagnostic line on failure,
 *   - presents a user-friendly message and preserves request_id,
 *   - dedupes in-flight requests per key (no duplicate polling),
 *   - never fabricates data: no fake PnL, no random fallbacks.
 * ========================================================================= */
window.NX = window.NX || {};

(function () {
  'use strict';

  let seq = 0;
  function rid() {
    seq += 1;
    return 'req_' + Date.now().toString(36) + seq.toString(36);
  }

  function uiError(component, action, endpoint, status, requestId, extra) {
    const line = '[UI_ERROR] component=' + component + ' action=' + action +
      ' endpoint=' + endpoint + ' status=' + (status || 'network') +
      ' request_id=' + (requestId || '-') + (extra ? ' ' + extra : '');
    console.warn(line);
  }

  const inflight = {};

  // BUG-212: network failures must reach the connectivity controller. The SSE
  // path flips the banner via NXConn.setDown, but a browser that holds a
  // half-open EventSource fires NO error event, and boot-time REST polling is
  // the only periodic traffic — so the UI stayed visually UP while every
  // request failed. Bounded: 2 consecutive failures arm the banner; any OK
  // request resets (no flap on a single dropped call).
  let connFailStreak = 0;
  function noteConnFailure(kind) {
    connFailStreak += 1;
    if (window.NXConn && connFailStreak >= 2 && window.NXConn.state() !== 'DOWN') {
      window.NXConn.setDown(kind === 'net' ? 'Network request failed. ' : ('Server error (HTTP ' + arguments[1] + '). '));
    }
  }
  function noteConnOk() { connFailStreak = 0; if (window.NXConn) window.NXConn.setUp(); }


  async function request(url, opts, key) {
    const reqId = opts.headers && opts.headers['X-Request-ID'] || rid();
    const headers = Object.assign({ 'X-Request-ID': reqId }, (opts.headers || {}));
    const init = Object.assign({}, opts, { headers });
    if (opts.signal) init.signal = opts.signal; // AbortController passthrough (cc_state timeout budget)
    const cacheKey = key || url + '|' + (opts.method || 'GET');
    if (opts.method === 'GET' || !opts.method) {
      if (inflight[cacheKey]) return inflight[cacheKey];
    }
    const p = (async () => {
      let res;
      try {
        res = await fetch(url, init);
      } catch (err) {
        uiError(opts.component || 'api', opts.action || 'FETCH', url, 0, reqId, String(err && err.message || err));
        noteConnFailure('net');
        return { ok: false, status: 0, error: { code: 'NETWORK_ERROR', message: 'Network request failed.', request_id: reqId } };
      }
      let body = null;
      try { body = await res.json(); } catch (e) { body = null; }
      if (!res.ok) {
        let code = 'INTERNAL_ERROR', message = 'The server could not complete this request.';
        if (body && body.error) { code = body.error.code || code; message = body.error.message || message; }
        else if (body && body.detail) { message = typeof body.detail === 'string' ? body.detail : 'Request failed.'; }
        uiError(opts.component || 'api', opts.action || 'FETCH', url, res.status, reqId, 'code=' + code);
        if (res.status >= 500) noteConnFailure('srv', res.status);
        return { ok: false, status: res.status, error: { code, message, request_id: reqId } };
      }
      noteConnOk();
      return { ok: true, status: res.status, body: body || {}, request_id: reqId };
    })();
    if (opts.method === 'GET' || !opts.method) {
      inflight[cacheKey] = p;
      p.finally(() => { if (inflight[cacheKey] === p) delete inflight[cacheKey]; });
    }
    return p;
  }

  window.NX.api = {
    get(url, opts) { return request(url, Object.assign({ method: 'GET' }, opts), url); },
    post(url, body, opts) {
      return request(url, Object.assign({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }, opts));
    },
    put(url, body, opts) {
      return request(url, Object.assign({ method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }, opts));
    },
    del(url, opts) {
      return request(url, Object.assign({ method: 'DELETE' }, opts));
    },
    // User-facing error presentation: shows request_id so the operator can
    // correlate with the server log.
    msg(result, fallback) {
      if (result && result.error) {
        return (result.error.message || fallback || 'Request failed.') + (result.error.request_id ? ' (request ' + result.error.request_id + ')' : '');
      }
      return fallback || '';
    }
  };
})();