/* ==========================================================================
 * Nexus Scalp Engine — Attention Strip (NXAttention)  ·  CHG-0048
 * --------------------------------------------------------------------------
 * Agent: Nexus-Main (UX). Presentation-layer ONLY.
 *
 * Answers the HOME question (brief §5): "Do I need to do anything right
 * now?" from data the client ALREADY receives — /api/status health,
 * live_freshness, is_stale, runtime_mode. No new backend calls, no new
 * backend work (brief §51).
 *
 * Priority classes (§6 attention-first design):
 *   CRITICAL   — connection lost / runtime BLOCKED / model unavailable
 *   ACTION     — stale data, degraded subsystem, DB warning surfaced by /health
 *   CALM       — everything fine: explicit "all good, nothing to do"
 *
 * The strip NEVER invents a problem and NEVER hides a real one: every row
 * it renders comes from the payload; when the payload says nothing is
 * wrong it says so explicitly (calm confidence, not silence).
 * ========================================================================== */
(function (global) {
    'use strict';

    var host = null;
    var lastRenderKey = '';

    function t(key, fallback, vars) {
        var i = global.NX_I18N;
        var s = i ? i.t(key, fallback) : fallback;
        if (vars) Object.keys(vars).forEach(function (k) { s = s.split('{' + k + '}').join(vars[k]); });
        return s;
    }

    function esc(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function ensureHost() {
        if (host && document.body.contains(host)) return host;
        var banner = document.getElementById('conn-lost-banner');
        host = document.createElement('div');
        host.id = 'ux-attention-strip';
        host.className = 'hidden w-full px-6 py-2 border-b text-xs';
        host.setAttribute('role', 'region');
        host.setAttribute('aria-label', 'Attention summary');
        if (banner && banner.parentNode) banner.parentNode.insertBefore(host, banner.nextSibling);
        else document.body.insertBefore(host, document.body.firstChild);
        return host;
    }

    function row(kind, icon, text) {
        var cls = {
            critical: 'text-rose-300',
            action: 'text-amber-300',
            ok: 'text-emerald-300'
        }[kind];
        return '<div class="flex items-center gap-2 ' + cls + '">' +
            '<i class="fa-solid ' + icon + '" aria-hidden="true"></i><span>' + esc(text) + '</span></div>';
    }

    /**
     * payload = the canonical /api/status snapshot the app already holds.
     * Cheap: string-key dedupe, re-renders only on state change.
     */
    function render(payload) {
        var el = ensureHost();
        if (!payload) return;

        var items = [];
        var conn = global.NXConn && global.NXConn.state ? global.NXConn.state() : 'UP';
        var health = payload.health || {};
        var subs = health.subsystems || {};
        var runtime = String(payload.runtime_mode || payload.execution_mode || '').toUpperCase();

        // --- CRITICAL -------------------------------------------------------
        if (conn === 'DOWN') items.push(['critical', 'fa-plug-circle-xmark', t('ux.conn.title', 'Connection lost — live updates stopped.')]);
        if (runtime.indexOf('BLOCKED') !== -1 || runtime.indexOf('DISCONNECTED') !== -1) {
            items.push(['critical', 'fa-ban', t('ux.attention.runtime_blocked', 'Runtime is {m}. Trading is not possible until it recovers.', { m: runtime })]);
        }
        if (payload.model && payload.model.available === false) {
            items.push(['critical', 'fa-brain', t('ux.attention.model_unavailable', 'Model unavailable — decisions cannot be produced.')]);
        }

        // --- ACTION ----------------------------------------------------------
        if (payload.is_stale) {
            items.push(['action', 'fa-clock', t('ux.attention.stale', 'Data is stale — the shown values are the last known.')]);
        }
        ['engine', 'mt5', 'database', 'news', 'workers'].forEach(function (k) {
            var st = String(subs[k] || '').toUpperCase();
            if (st && st !== 'READY' && st !== 'FRESH' && st !== 'DISABLED') {
                items.push(['action', 'fa-triangle-exclamation',
                    t('ux.attention.subsystem', '{s}: {v}', { s: k.toUpperCase(), v: st })]);
            }
        });
        if (conn === 'DEGRADED' && !payload.is_stale) {
            items.push(['action', 'fa-hourglass-half', t('ux.conn.stale_title', 'Data may be stale — no live updates recently.')]);
        }

        // --- CALM ------------------------------------------------------------
        var key = JSON.stringify(items);
        if (key === lastRenderKey) return; // no state change -> no re-render
        lastRenderKey = key;

        if (!items.length) {
            el.className = 'w-full px-6 py-1.5 border-b border-emerald-500/20 bg-emerald-500/5 text-xs';
            el.innerHTML = '<div class="max-w-7xl mx-auto flex items-center gap-2 text-emerald-300">' +
                '<i class="fa-solid fa-circle-check" aria-hidden="true"></i>' +
                '<span>' + esc(t('ux.attention.allgood', 'All systems normal. No action needed.')) + '</span></div>';
            el.classList.remove('hidden');
            return;
        }

        var critical = items.some(function (i) { return i[0] === 'critical'; });
        el.className = 'w-full px-6 py-2 border-b text-xs ' +
            (critical ? 'border-rose-500/30 bg-rose-500/10' : 'border-amber-500/30 bg-amber-500/10');
        el.innerHTML = '<div class="max-w-7xl mx-auto space-y-1">' + items.map(function (i) { return row(i[0], i[1], i[2]); }).join('') + '</div>';
        el.classList.remove('hidden');
    }

    global.NXAttention = { render: render };
})(window);
