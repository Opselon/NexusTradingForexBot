/* ==========================================================================
 * Nexus Scalp Engine — UX Safety & Feedback Layer (ux.js)  ·  CHG-0048
 * --------------------------------------------------------------------------
 * Agent: Nexus-Main (UX). Presentation-layer ONLY.
 * Relation to CHG-0043 (cc_components.js confirmDialog): the Control Center
 * modal (ACTION/CURRENT/IMPACT/RECOVERY + Enter/Escape) already guards
 * engine start/stop and PAPER/SHADOW->LIVE. This module EXTENDS the safety
 * net; it does not duplicate or bypass it:
 *
 *   1. NX.confirmToast(kind,msg)  — non-spammy feedback toasts (dedupe 4s).
 *   2. NX.confirmTyped(spec)      — typed-confirmation escalation for the
 *      MOST dangerous transitions (anything -> LIVE): the user must type
 *      the target mode. Falls back to NX.cc.design.confirmDialog when the
 *      typed layer is unavailable (never silently downgrades to none).
 *   3. Mode-switch gate hook: any -> LIVE requires typed "LIVE";
 *      PAPER <-> SHADOW keeps the CHG-0043 confirm.
 *   4. Stale-data marking helper: NX.markStale(el, ageSec).
 *
 * No trading logic. No backend calls. Values are never fabricated — when
 * the truth is unknown the UI shows the truth (— / UNAVAILABLE).
 * ========================================================================== */
(function (global) {
    'use strict';

    var inst = null; // typed-confirm singleton
    var Z_DIALOG = 'z-[9997]';
    var Z_TOAST = 'z-[9998]';

    function esc(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    global.NX = global.NX || {};
    global.NX._esc = global.NX._esc || esc;

    // ------------------------------ toasts ----------------------------------
    var toastHost = null;
    var toastGuard = {};

    function ensureToasts() {
        if (toastHost && document.body.contains(toastHost)) return;
        toastHost = document.createElement('div');
        toastHost.className = 'fixed bottom-4 right-4 ' + Z_TOAST + ' flex flex-col gap-2 items-end pointer-events-none';
        document.body.appendChild(toastHost);
    }

    function toast(msg, kind, opts) {
        ensureToasts();
        kind = kind || 'info';
        opts = opts || {};
        var key = kind + '|' + msg;
        var now = Date.now();
        if (!opts.force && toastGuard[key] && now - toastGuard[key] < 4000) return; // non-spam dedupe
        toastGuard[key] = now;
        var palette = {
            ok: 'bg-emerald-500/15 border-emerald-500/40 text-emerald-200',
            warn: 'bg-amber-500/15 border-amber-500/40 text-amber-200',
            err: 'bg-rose-500/15 border-rose-500/40 text-rose-200',
            info: 'bg-sky-500/15 border-sky-500/40 text-sky-200'
        };
        var icon = { ok: 'fa-circle-check', warn: 'fa-triangle-exclamation', err: 'fa-circle-xmark', info: 'fa-circle-info' }[kind];
        var t = document.createElement('div');
        t.className = 'pointer-events-auto max-w-sm text-sm rounded-lg border px-4 py-2.5 shadow-xl backdrop-blur-sm transition-all duration-300 ' + (palette[kind] || palette.info);
        t.setAttribute('role', kind === 'err' ? 'alert' : 'status');
        t.innerHTML = '<i class="fa-solid ' + icon + ' mr-2"></i>' + esc(msg);
        t.style.opacity = '0';
        t.style.transform = 'translateY(8px)';
        toastHost.appendChild(t);
        requestAnimationFrame(function () { t.style.opacity = '1'; t.style.transform = 'none'; });
        var ttl = opts.ttl || (kind === 'err' ? 7000 : 3500);
        setTimeout(function () {
            t.style.opacity = '0';
            setTimeout(function () { t.remove(); }, 320);
        }, ttl);
    }
    global.NX.toast = toast;

    // --------------------- typed confirmation (LIVE gate) -------------------
    function ensureDialog() {
        if (inst && document.body.contains(inst.backdrop)) return inst;
        var backdrop = document.createElement('div');
        backdrop.className = 'fixed inset-0 ' + Z_DIALOG + ' hidden items-center justify-center bg-black/60 backdrop-blur-sm';
        var box = document.createElement('div');
        box.className = 'w-full max-w-md mx-4 bg-panelBg border border-borderClr rounded-xl shadow-2xl p-5';
        box.setAttribute('role', 'alertdialog');
        box.setAttribute('aria-modal', 'true');
        box.innerHTML =
            '<h3 class="text-base font-bold text-white mb-2 flex items-center gap-2" data-ux-title></h3>' +
            '<div class="text-sm text-gray-300 leading-relaxed mb-3" data-ux-body></div>' +
            '<div class="text-xs rounded-lg border p-3 mb-3 leading-relaxed bg-rose-500/10 border-rose-500/30 text-rose-200" data-ux-impact></div>' +
            '<div class="mb-3" data-ux-ttrow>' +
            '<label class="block text-[11px] font-bold text-textMuted uppercase mb-1" data-ux-ttlabel></label>' +
            '<input type="text" autocomplete="off" spellcheck="false" class="w-full bg-darkBg border border-borderClr rounded px-3 py-2 text-sm font-mono text-white focus:outline-none focus:border-rose-400" data-ux-input />' +
            '</div>' +
            '<div class="flex justify-end gap-2 pt-3 border-t border-borderClr">' +
            '<button class="px-4 py-2 rounded-lg text-sm font-bold bg-darkBg border border-borderClr text-gray-300 hover:bg-panelBg transition" data-ux-cancel></button>' +
            '<button class="px-4 py-2 rounded-lg text-sm font-black transition bg-slate-700 text-slate-400 cursor-not-allowed" data-ux-ok disabled></button>' +
            '</div>';
        backdrop.appendChild(box);
        document.body.appendChild(backdrop);
        inst = {
            backdrop: backdrop, box: box,
            title: box.querySelector('[data-ux-title]'),
            body: box.querySelector('[data-ux-body]'),
            impact: box.querySelector('[data-ux-impact]'),
            ttrow: box.querySelector('[data-ux-ttrow]'),
            ttlabel: box.querySelector('[data-ux-ttlabel]'),
            input: box.querySelector('[data-ux-input]'),
            cancel: box.querySelector('[data-ux-cancel]'),
            ok: box.querySelector('[data-ux-ok]'),
            lastFocus: null, _resolve: null, _requireText: null
        };
        backdrop.addEventListener('mousedown', function (ev) { if (ev.target === backdrop) resolve(false); });
        inst.cancel.addEventListener('click', function () { resolve(false); });
        inst.ok.addEventListener('click', function () {
            if (inst._requireText && inst.input.value !== inst._requireText) return;
            resolve(true);
        });
        inst.input.addEventListener('input', function () {
            var match = !inst._requireText || inst.input.value === inst._requireText;
            inst.ok.disabled = !match;
            inst.ok.className = 'px-4 py-2 rounded-lg text-sm font-black transition ' +
                (match ? 'bg-rose-500 text-white hover:bg-rose-400' : 'bg-slate-700 text-slate-400 cursor-not-allowed');
        });
        document.addEventListener('keydown', function (ev) {
            if (!inst || inst.backdrop.classList.contains('hidden')) return;
            if (ev.key === 'Escape') { resolve(false); return; }
            if (ev.key === 'Tab') {
                var f = inst.box.querySelectorAll('button, input');
                if (!f.length) return;
                var first = f[0], last = f[f.length - 1];
                if (ev.shiftKey && document.activeElement === first) { last.focus(); ev.preventDefault(); }
                else if (!ev.shiftKey && document.activeElement === last) { first.focus(); ev.preventDefault(); }
            }
        });
        return inst;
    }

    function resolve(v) {
        if (!inst || !inst._resolve) return;
        var fn = inst._resolve;
        inst._resolve = null;
        inst.backdrop.classList.add('hidden');
        inst.backdrop.classList.remove('flex');
        if (inst.lastFocus && inst.lastFocus.focus) { try { inst.lastFocus.focus(); } catch (e) { /* detached */ } }
        fn(v);
    }

    var I18N = function () { return global.NX_I18N || null; };
    function T(key, fallback, vars) {
        var i = I18N();
        var s = i ? i.t(key, fallback, vars) : fallback;
        if (vars) {
            Object.keys(vars).forEach(function (k) { s = s.split('{' + k + '}').join(vars[k]); });
        }
        return s;
    }

    /**
     * Typed confirm. spec = {title, body, impact, requireText, confirmLabel}
     * Resolves true ONLY on explicit confirm. requireText makes the confirm
     * button dead until the exact token is typed (used for -> LIVE).
     */
    function confirmTyped(spec) {
        spec = spec || {};
        ensureDialog();
        inst.lastFocus = document.activeElement;
        inst.title.innerHTML = '<i class="fa-solid fa-triangle-exclamation text-rose-400"></i> ' + esc(spec.title || T('ux.confirm.title', 'Confirm action'));
        inst.body.innerHTML = spec.body || '';
        inst.impact.innerHTML = spec.impact || '';
        inst.impact.classList.toggle('hidden', !spec.impact);
        inst._requireText = spec.requireText || null;
        if (inst._requireText) {
            inst.ttrow.classList.remove('hidden');
            inst.ttlabel.textContent = T('ux.confirm.type', 'Type {w} to enable confirmation', { w: inst._requireText });
            inst.input.value = '';
        } else {
            inst.ttrow.classList.add('hidden');
        }
        inst.cancel.textContent = T('ux.confirm.cancel', 'Cancel');
        inst.ok.textContent = spec.confirmLabel || T('ux.confirm.ok', 'Confirm');
        inst.ok.disabled = !!inst._requireText;
        inst.ok.className = 'px-4 py-2 rounded-lg text-sm font-black transition ' +
            (inst._requireText ? 'bg-slate-700 text-slate-400 cursor-not-allowed' : 'bg-rose-500 text-white hover:bg-rose-400');
        inst.backdrop.classList.remove('hidden');
        inst.backdrop.classList.add('flex');
        return new Promise(function (res) {
            inst._resolve = res;
            setTimeout(function () { (inst._requireText ? inst.input : inst.cancel).focus(); }, 30);
        });
    }
    global.NX.confirmTyped = confirmTyped;

    var MODE_IMPACT = {
        PAPER: 'Simulated fills only — no real orders reach the broker.',
        SHADOW: 'Signals are computed but never dispatched as orders.',
        LIVE: 'The engine will dispatch REAL orders to the connected broker account.'
    };

    /**
     * Mode-switch gate. Anything -> LIVE requires typed "LIVE".
     * PAPER <-> SHADOW gets a single confirm (impact preview, no typing).
     * Used by app.js mode handler; returns Promise<boolean>.
     */
    function confirmModeChange(fromMode, toMode) {
        var toLive = toMode === 'LIVE';
        return confirmTyped({
            title: T('ux.mode.title', 'Switch execution mode: {from} → {to}?', { from: fromMode, to: toMode }),
            body: T('ux.mode.body', 'This changes how the engine executes orders.'),
            impact: '<b>' + esc(T('ux.mode.impact_label', 'What changes') ) + ':</b> ' +
                esc(MODE_IMPACT[toMode] || toMode) +
                (toLive ? '<br><b class="text-rose-300">' + esc(T('ux.mode.live_warning', 'Real money is at risk. This affects your live broker account.')) + '</b>' : ''),
            requireText: toLive ? 'LIVE' : null,
            confirmLabel: toLive ? T('ux.mode.confirm_live', 'Arm LIVE execution') : T('ux.confirm.ok', 'Confirm')
        });
    }
    global.NX.confirmModeChange = confirmModeChange;

    // --------------------- stale-data marking helper ------------------------
    /** Marks an element's dataset stale; returns nothing. Truthful only. */
    function markStale(el, ageSec) {
        if (!el) return;
        el.dataset.uxStale = '1';
        el.classList.add('ux-stale');
        var badge = el.querySelector ? el.querySelector('[data-ux-stale-badge]') : null;
        if (!badge && el.insertAdjacentHTML) {
            el.insertAdjacentHTML('beforeend',
                '<span data-ux-stale-badge class="ml-2 text-[9px] font-bold px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30 align-middle">' +
                esc(T('ux.stale', 'STALE {s}s', { s: ageSec != null ? Math.round(ageSec) : '?' })) + '</span>');
        } else if (badge && ageSec != null) {
            badge.textContent = T('ux.stale', 'STALE {s}s', { s: Math.round(ageSec) });
        }
    }
    global.NX.markStale = markStale;
})(window);
