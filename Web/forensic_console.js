/* =========================================================================
 * NEXUS CONTROL CENTER — Forensic Incident Center shared infrastructure
 * -------------------------------------------------------------------------
 * Foundation layer for the Forensic Incident Center + Agent Mode + Task
 * Generation. Provides, dependency-free (no build system, vanilla JS):
 *
 *   - Toast notification system (bottom-right, ARIA live, stack, dismiss)
 *   - Error normalization (NEVER leak raw stack traces like
 *     "TypeError: Failed to fetch" into the page)
 *   - Single-source-of-truth incident model + derived KPI selectors
 *   - Incident data normalization boundary (severity / status vocab)
 *   - Agent Mode state machine (OFF / IDLE / TRACING / ANALYZING /
 *     GENERATING_TASK / RESOLVING / ERROR)
 *   - Task provider abstraction (Jira / ClickUp / GitHub-safe surface)
 *   - Modal helper (focus trap, Escape, backdrop, focus restore)
 *
 * All HTTP goes through window.NX.api (already present in api_client.js);
 * that client already normalizes server errors. This module adds the
 * user-facing presentation + structured logging layer on top.
 * ========================================================================= */
window.NX = window.NX || {};
NX.Forensic = NX.Forensic || {};

(function () {
  'use strict';

  /* -----------------------------------------------------------------------
   * 1. TOAST NOTIFICATION SYSTEM
   *    Bottom-right, stacked, ARIA-live, auto-dismiss + manual dismiss.
   * --------------------------------------------------------------------- */
  var TOAST_CONTAINER_ID = 'nx-toast-region';
  var TOAST_TTL = { info: 6000, success: 5000, warning: 8000, error: 10000 };

  function ensureToastRegion() {
    var region = document.getElementById(TOAST_CONTAINER_ID);
    if (region) return region;
    region = document.createElement('div');
    region.id = TOAST_CONTAINER_ID;
    region.className = 'nx-toast-region';
    region.setAttribute('role', 'region');
    region.setAttribute('aria-label', 'Notifications');
    region.setAttribute('aria-live', 'polite');
    region.setAttribute('aria-atomic', 'false');
    document.body.appendChild(region);
    return region;
  }

  // type: success | warning | error | info
  function toast(message, opts) {
    opts = opts || {};
    var type = opts.type || 'info';
    var region = ensureToastRegion();

    var el = document.createElement('div');
    el.className = 'nx-toast nx-toast-' + type;
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    el.setAttribute('tabindex', '0');

    var icon = document.createElement('span');
    icon.className = 'nx-toast-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = { success: '✓', warning: '!', error: '✕', info: 'i' }[type] || 'i';

    var body = document.createElement('div');
    body.className = 'nx-toast-body';

    var msg = document.createElement('div');
    msg.className = 'nx-toast-msg';
    msg.textContent = String(message == null ? '' : message); // textContent => no HTML injection

    body.appendChild(msg);

    if (opts.detail) {
      var detail = document.createElement('div');
      detail.className = 'nx-toast-detail';
      detail.textContent = String(opts.detail);
      body.appendChild(detail);
    }

    var close = document.createElement('button');
    close.className = 'nx-toast-close';
    close.setAttribute('aria-label', 'Dismiss notification');
    close.type = 'button';
    close.textContent = '×';
    close.addEventListener('click', function () { dismissToast(el); });

    el.appendChild(icon);
    el.appendChild(body);
    el.appendChild(close);

    var ttl = opts.ttl != null ? opts.ttl : (TOAST_TTL[type] || 6000);
    var timer = null;
    if (ttl > 0) {
      timer = setTimeout(function () { dismissToast(el); }, ttl);
    }
    // Pause auto-dismiss on hover/focus for accessibility.
    el.addEventListener('mouseenter', function () { if (timer) { clearTimeout(timer); timer = null; } });
    el.addEventListener('focus', function () { if (timer) { clearTimeout(timer); timer = null; } });
    el.addEventListener('mouseleave', function () { if (!timer && ttl > 0) timer = setTimeout(function () { dismissToast(el); }, 2500); });

    region.appendChild(el);
    return el;
  }

  function dismissToast(el) {
    if (!el || el._dismissed) return;
    el._dismissed = true;
    el.classList.add('nx-toast-leaving');
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 220);
  }

  NX.Forensic.toast = {
    success: function (m, o) { return toast(m, Object.assign({ type: 'success' }, o)); },
    error: function (m, o) { return toast(m, Object.assign({ type: 'error' }, o)); },
    warning: function (m, o) { return toast(m, Object.assign({ type: 'warning' }, o)); },
    info: function (m, o) { return toast(m, Object.assign({ type: 'info' }, o)); },
    dismiss: dismissToast,
  };

  /* -----------------------------------------------------------------------
   * 2. ERROR NORMALIZATION
   *    Convert a safe NX.api result (or thrown error) into a user-facing
   *    string + a structured diagnostic log. Never surface raw
   *    "TypeError: Failed to fetch" text to the operator.
   * --------------------------------------------------------------------- */
  var FRIENDLY_NETWORK = 'Unable to reach the incident service. Check connectivity and try again.';
  var FRIENDLY_GENERIC = 'The request could not be completed. Please retry.';

  // Returns { message, detail } suitable for a toast.
  function normalizeError(err, ctx) {
    ctx = ctx || {};
    // Already-normalized NX.api failure envelope.
    if (err && err.error && err.error.message) {
      var detail = err.error.request_id ? ('request ' + err.error.request_id) : undefined;
      return { message: err.error.message, detail: detail, code: err.error.code || 'SERVER_ERROR' };
    }
    if (err && err.ok === false && err.error) {
      return { message: err.error.message || FRIENDLY_GENERIC, detail: err.error.request_id, code: err.error.code };
    }
    // A raw thrown Error (e.g. inside a try that did not go through NX.api).
    // We deliberately do NOT return err.message verbatim to the user.
    var rawMsg = err && err.message ? String(err.message) : String(err == null ? 'unknown error' : err);
    // Structured, correlation-friendly console log (operators can correlate
    // with the server; never shown to the user).
    logDiagnostic({
      component: ctx.component || 'Forensic',
      action: ctx.action || 'REQUEST',
      endpoint: ctx.endpoint || '-',
      message: rawMsg,
      friendly: true,
    });
    // Detect the classic browser network failure without echoing it.
    if (rawMsg.indexOf('Failed to fetch') !== -1 || rawMsg.indexOf('NetworkError') !== -1) {
      return { message: FRIENDLY_NETWORK, detail: ctx.hint, code: 'NETWORK_ERROR' };
    }
    return { message: FRIENDLY_GENERIC, detail: ctx.hint, code: 'CLIENT_ERROR' };
  }

  function logDiagnostic(d) {
    var line = '[UI_ERROR] component=' + (d.component || '?') +
      ' action=' + (d.action || '?') +
      ' endpoint=' + (d.endpoint || '-') +
      ' message=' + (d.message || '');
    try { console.warn(line); } catch (e) { /* noop */ }
  }

  // Promise-returning helper: run an NX.api call, on success call onOk(body),
  // on failure present a toast + return null. Keeps call sites clean and
  // guarantees no raw error text leaks into the DOM.
  async function apiSafe(method, url, bodyOrOpts, ctx) {
    ctx = ctx || {};
    var res;
    try {
      if (method === 'POST') res = await NX.api.post(url, bodyOrOpts || {}, { component: ctx.component, action: ctx.action });
      else if (method === 'PUT') res = await NX.api.put(url, bodyOrOpts || {}, { component: ctx.component, action: ctx.action });
      else if (method === 'DELETE') res = await NX.api.del(url, { component: ctx.component, action: ctx.action });
      else res = await NX.api.get(url, { component: ctx.component, action: ctx.action });
    } catch (e) {
      var n1 = normalizeError(e, ctx);
      NX.Forensic.toast.error(n1.message, { detail: n1.detail });
      return null;
    }
    if (!res || !res.ok) {
      var n2 = normalizeError(res, ctx);
      NX.Forensic.toast.error(n2.message, { detail: n2.detail });
      return null;
    }
    return res.body;
  }

  NX.Forensic.normalizeError = normalizeError;
  NX.Forensic.logDiagnostic = logDiagnostic;
  NX.Forensic.apiSafe = apiSafe;

  /* -----------------------------------------------------------------------
   * 3. INCIDENT DATA MODEL + DERIVED KPI SELECTORS
   *    Single authoritative incident array. KPIs are DERIVED from it so
   *    the header can never contradict the list (fixes the
   *    OPEN=2 / CRITICAL=1 / HIGH=1 / MEDIUM=3 impossible state).
   * --------------------------------------------------------------------- */
  var OPEN_STATUSES = ['OPEN', 'INVESTIGATING', 'ROOT_CAUSE_IDENTIFIED', 'CONTAINED', 'RECOVERY_READY'];
  var RESOLVED_STATUSES = ['RECOVERED', 'CLOSED', 'FALSE_POSITIVE', 'RESOLVED_BY_AGENT'];

  // Normalize severity/status to the canonical vocabulary. Backend may emit
  // "CRITICAL" or "critical" or unknown values; this is the single boundary.
  function normSeverity(v) {
    if (v == null) return 'UNKNOWN';
    var s = String(v).toUpperCase();
    if (['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'].indexOf(s) !== -1) return s;
    return 'UNKNOWN';
  }

  function normStatus(v) {
    if (v == null) return 'UNKNOWN';
    var s = String(v).toUpperCase();
    // Map a few known aliases to canonical open/closed buckets.
    var openAliases = ['NEW', 'ACTIVE', 'TRIAGED'];
    var closedAliases = ['RESOLVED', 'WONTFIX'];
    if (OPEN_STATUSES.indexOf(s) !== -1 || openAliases.indexOf(s) !== -1) return s;
    if (RESOLVED_STATUSES.indexOf(s) !== -1 || closedAliases.indexOf(s) !== -1) return s;
    return 'UNKNOWN';
  }

  function isOpen(inc) {
    var st = normStatus(inc && inc.status);
    if (OPEN_STATUSES.indexOf(st) !== -1) return true;
    if (st === 'NEW' || st === 'ACTIVE' || st === 'TRIAGED') return true;
    return false;
  }

  function isResolved(inc) {
    var st = normStatus(inc && inc.status);
    if (RESOLVED_STATUSES.indexOf(st) !== -1 || st === 'RESOLVED' || st === 'WONTFIX') return true;
    return false;
  }

  // Derive ALL KPI numbers from a single array. No independent counters.
  // `incidents` is the FULL authoritative list returned by the backend.
  function deriveKpis(incidents) {
    var arr = Array.isArray(incidents) ? incidents : [];
    var open = arr.filter(isOpen);
    // Severity counts are computed over the OPEN set, matching the list that
    // the UI actually renders (so header always equals list).
    var critical = open.filter(function (i) { return normSeverity(i.severity) === 'CRITICAL'; }).length;
    var high = open.filter(function (i) { return normSeverity(i.severity) === 'HIGH'; }).length;
    var medium = open.filter(function (i) { return normSeverity(i.severity) === 'MEDIUM'; }).length;
    var low = open.filter(function (i) { return normSeverity(i.severity) === 'LOW'; }).length;
    var info = open.filter(function (i) { return normSeverity(i.severity) === 'INFO'; }).length;
    var unknown = open.filter(function (i) { return normSeverity(i.severity) === 'UNKNOWN'; }).length;
    var resolved = arr.filter(isResolved).length;
    var resolvedByAgent = arr.filter(function (i) {
      return isResolved(i) && (i.resolved_by === 'AGENT' || normStatus(i.status) === 'RESOLVED_BY_AGENT');
    }).length;
    return {
      total: arr.length,
      open: open.length,
      critical: critical,
      high: high,
      medium: medium,
      low: low,
      info: info,
      unknown: unknown,
      resolved: resolved,
      resolvedByAgent: resolvedByAgent,
    };
  }

  NX.Forensic.model = {
    OPEN_STATUSES: OPEN_STATUSES,
    RESOLVED_STATUSES: RESOLVED_STATUSES,
    normSeverity: normSeverity,
    normStatus: normStatus,
    isOpen: isOpen,
    isResolved: isResolved,
    deriveKpis: deriveKpis,
  };

  /* -----------------------------------------------------------------------
   * 4. AGENT MODE STATE MACHINE (front-end representation of truth)
   *    The UI only shows a state when a real backend signal drives it.
   *    OFF is the default and shows nothing.
   * --------------------------------------------------------------------- */
  var AGENT_STATES = {
    OFF: { label: 'Off', color: 'inactive' },
    IDLE: { label: 'Agent: Idle', color: 'agent' },
    QUEUED: { label: 'Agent: Queued', color: 'agent' },
    TRACING: { label: 'Agent: Tracing Lineage', color: 'agent' },
    ANALYZING: { label: 'Agent: Analyzing', color: 'agent' },
    GENERATING_TASK: { label: 'Agent: Generating Task', color: 'agent' },
    TASK_READY: { label: 'Agent: Task Ready', color: 'agent' },
    RESOLVING: { label: 'Agent: Resolving', color: 'agent' },
    RESOLVED: { label: 'Agent: Resolved', color: 'success' },
    FAILED: { label: 'Agent: Failed', color: 'error' },
  };

  function agentBadge(state) {
    var meta = AGENT_STATES[state] || AGENT_STATES.OFF;
    return { state: state, label: meta.label, color: meta.color };
  }

  NX.Forensic.agent = {
    STATES: AGENT_STATES,
    badge: agentBadge,
    isActive: function (s) { return s && s !== 'OFF'; },
  };

  /* -----------------------------------------------------------------------
   * 5. TASK PROVIDER ABSTRACTION
   *    Front-end-safe surface. The actual provider REST endpoints are not
   *    yet implemented server-side, so this is a typed interface + a safe
   *    "not configured" truthful state. NO secrets are ever placed here.
   * --------------------------------------------------------------------- */
  var TASK_PROVIDERS = {
    jira: { id: 'jira', label: 'Jira' },
    clickup: { id: 'clickup', label: 'ClickUp' },
    github: { id: 'github', label: 'GitHub Issues' },
  };

  // Returns a truthful descriptor of the provider surface (no creds).
  function taskProviderSurface() {
    // Backend capability is not implemented yet; we surface the available
    // provider labels and a disabled-by-default flag so the UI can render a
    // truthful "review before submit" state without pretending success.
    return {
      providers: Object.keys(TASK_PROVIDERS).map(function (k) { return TASK_PROVIDERS[k]; }),
      configured: false, // no backend endpoint exists => honest placeholder
      submitEndpoint: null,
    };
  }

  NX.Forensic.taskProvider = {
    TASK_PROVIDERS: TASK_PROVIDERS,
    surface: taskProviderSurface,
  };

  /* -----------------------------------------------------------------------
   * 6. MODAL HELPER (focus trap, Escape, backdrop, focus restore)
   * --------------------------------------------------------------------- */
  function openModal(modalEl) {
    if (!modalEl) return;
    if (modalEl._lastFocused == null) {
      try { modalEl._lastFocused = document.activeElement; } catch (e) { modalEl._lastFocused = null; }
    }
    modalEl.classList.remove('hidden');
    modalEl.classList.add('nx-modal-open');
    document.addEventListener('keydown', modalKeyHandler, true);

    // Move focus to the first focusable element (or the modal itself).
    var focusables = getFocusable(modalEl);
    var target = focusables[0] || modalEl;
    if (target && target.focus) {
      setTimeout(function () { try { target.focus(); } catch (e) {} }, 10);
    }
  }

  function closeModal(modalEl) {
    if (!modalEl) return;
    modalEl.classList.add('hidden');
    modalEl.classList.remove('nx-modal-open');
    document.removeEventListener('keydown', modalKeyHandler, true);
    // Not all browsers support :focus-within; restore focus explicitly.
    if (modalEl._lastFocused && modalEl._lastFocused.focus) {
      try { modalEl._lastFocused.focus(); } catch (e) {}
    }
    modalEl._lastFocused = null;
  }

  function modalKeyHandler(e) {
    if (e.key === 'Escape') {
      var open = document.querySelectorAll('.nx-modal-open');
      for (var i = 0; i < open.length; i++) {
        var m = open[i];
        // Only dismiss if the modal opts into Escape-to-close.
        if (m.getAttribute('data-esc-close') === 'true') {
          e.preventDefault();
          var closer = m.querySelector('[data-modal-close]');
          if (closer) { closer.click(); }
          else { closeModal(m); }
          return;
        }
      }
    }
    if (e.key === 'Tab') {
      // Focus trap within the topmost open modal.
      var modals = document.querySelectorAll('.nx-modal-open');
      if (!modals.length) return;
      var top = modals[modals.length - 1];
      var f = getFocusable(top);
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  function getFocusable(root) {
    var sel = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
    var nodes = root.querySelectorAll(sel);
    var out = [];
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].offsetParent !== null || nodes[i] === document.activeElement) out.push(nodes[i]);
    }
    return out;
  }

  NX.Forensic.modal = { open: openModal, close: closeModal };

  /* -----------------------------------------------------------------------
   * 7. ASYNC BUTTON STATE HELPER (running / disabled guard)
   *    Prevents duplicate submissions while a request is in flight.
   * --------------------------------------------------------------------- */
  function withButtonLock(btn, runningLabel, fn) {
    if (!btn) return fn();
    if (btn._busy) return Promise.resolve(null); // duplicate click guard
    var prevHtml = btn.innerHTML;
    var prevDisabled = btn.disabled;
    btn._busy = true;
    btn.disabled = true;
    if (runningLabel) btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i>' + runningLabel;
    return Promise.resolve().then(fn).then(function (r) {
      btn._busy = false; btn.disabled = prevDisabled; btn.innerHTML = prevHtml; return r;
    }, function (e) {
      btn._busy = false; btn.disabled = prevDisabled; btn.innerHTML = prevHtml; throw e;
    });
  }

  NX.Forensic.withButtonLock = withButtonLock;

  // Expose a small helper used by call sites for consistent ids.
  NX.Forensic.uid = function (p) { return (p || 'id') + '_' + Math.random().toString(36).slice(2, 9); };

})();
