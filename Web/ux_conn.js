/* ==========================================================================
 * Nexus Scalp Engine — Connectivity Banner Controller (NXConn)  · CHG-0048
 * --------------------------------------------------------------------------
 * Agent: Nexus-Main (UX). Presentation-layer ONLY.
 *
 * One authoritative banner for the whole client (no per-widget guessing):
 *   UP        — banner hidden.
 *   DEGRADED  — amber warning: stream stale; data on screen may be old.
 *   DOWN      — red alert: connection lost; last-update time + Retry now.
 *
 * Truth rules (NO UX FAKERY):
 *   - The banner only appears from real signal loss (SSE error, fetch
 *     failure, stale heartbeat) and disappears only on a real event.
 *   - "Last update" shows the real timestamp of the last live event.
 *   - Never starts DOWN on page load: before the first event the header
 *     badge CONNECTING state is the truthful signal.
 * ========================================================================== */
(function (global) {
    'use strict';

    var state = 'UP'; // UP | DEGRADED | DOWN
    var lastEventAt = null; // ms timestamp of the last REAL live event
    var retryBinded = false;
    var banner = null;

    function esc(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function t(key, fallback, vars) {
        var i = global.NX_I18N;
        var s = i ? i.t(key, fallback) : fallback;
        if (vars) Object.keys(vars).forEach(function (k) { s = s.split('{' + k + '}').join(vars[k]); });
        return s;
    }

    function ensure() {
        banner = document.getElementById('conn-lost-banner');
        if (!banner || retryBinded) return;
        retryBinded = true;
        var btn = document.getElementById('conn-retry-btn');
        if (btn) {
            btn.addEventListener('click', function () {
                try { fetchSystemSnapshot(); } catch (e) { /* app.js not ready */ }
                try { startSSE(); } catch (e) { /* not initialized yet */ }
            });
        }
    }

    function fmtClock(ms) {
        var d = new Date(ms);
        function p(n) { return (n < 10 ? '0' : '') + n; }
        return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
    }

    function render() {
        ensure();
        if (!banner) return; // index.html not loaded (embedded pages) — stay silent
        var detail = document.getElementById('conn-lost-detail');
        var lastEl = document.getElementById('conn-lost-last');
        var titleEl = document.getElementById('conn-lost-title');

        if (state === 'UP') {
            banner.classList.add('hidden');
            return;
        }

        banner.classList.remove('hidden');
        if (state === 'DEGRADED') {
            banner.className = banner.className.replace(/bg-rose-[^\s"]+/g, 'bg-amber-500/15')
                .replace(/border-rose-[^\s"]+/g, 'border-amber-500/40')
                .replace(/text-rose-[^\s"]+/g, 'text-amber-100');
            banner.setAttribute('role', 'status');
            banner.setAttribute('aria-live', 'polite');
            if (titleEl) titleEl.textContent = t('ux.conn.stale_title', 'DATA MAY BE STALE');
            if (detail) detail.textContent = t('ux.conn.stale_detail', 'No live updates for a while. Values shown are the last known.');
        } else {
            banner.className = banner.className.replace(/bg-amber-[^\s"]+/g, 'bg-rose-500/15')
                .replace(/border-amber-[^\s"]+/g, 'border-rose-500/40')
                .replace(/text-amber-[^\s"]+/g, 'text-rose-100');
            banner.setAttribute('role', 'alert');
            banner.setAttribute('aria-live', 'assertive');
            if (titleEl) titleEl.textContent = t('ux.conn.title', 'CONNECTION LOST');
            if (detail) detail.textContent = t('ux.conn.detail', 'Live updates stopped. Data on screen may be outdated.');
        }
        if (lastEl) {
            if (lastEventAt) {
                var ageSec = Math.max(0, Math.round((Date.now() - lastEventAt) / 1000));
                lastEl.textContent = t('ux.conn.last', 'Last update: {t} ({s}s ago)', { t: fmtClock(lastEventAt), s: ageSec });
            } else {
                lastEl.textContent = t('ux.conn.never', 'No live data received yet.');
            }
        }
    }

    global.NXConn = {
        /** Called on ANY real live event (SSE tick/state/heartbeat, snapshot OK). */
        setUp: function () {
            lastEventAt = Date.now();
            if (state === 'UP') return;
            state = 'UP';
            render();
        },
        /** Hard failure: SSE error, fetch exception, HTTP 5xx. */
        setDown: function (reason) {
            if (state === 'DOWN') { render(); return; } // refresh last-update while down
            state = 'DOWN';
            render();
            if (reason) console.warn('[UI_CONN] DOWN: ' + reason);
        },
        /** Soft failure: stream open but no events (stale), engine paused, etc. */
        setDegraded: function () {
            if (state === 'DOWN') { render(); return; }
            state = 'DEGRADED';
            render();
        },
        state: function () { return state; },
        lastEventAt: function () { return lastEventAt; },
        /** Internal: let the stale timer refresh the age label. */
        refresh: render
    };

    // Keep the "last update (Ns ago)" label honest while the banner shows.
    setInterval(function () { if (state !== 'UP') render(); }, 5000);
})(window);
