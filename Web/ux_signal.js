/* ==========================================================================
 * Nexus Scalp Engine — Decision Humanizer (NXSignal)  ·  CHG-0048
 * --------------------------------------------------------------------------
 * Agent: Nexus-Main (UX). Presentation-layer ONLY.
 *
 * Translates the raw decision payload into the two layers the brief
 * requires (§16/§17/§18):
 *   LAYER 1 (simple):  BUY / SELL / NO TRADE in plain language, with a
 *                      human "why" sentence and data freshness.
 *   LAYER 2 (detail):  the raw reason code + confidence + provenance the
 *                      technical user already had.
 *
 * TRUTH RULES:
 *   - Confidence 0.0 on a Guardian-blocked decision is NOT "0% confident
 *     of no-trade" — it means the model was NOT CONSULTED. The UI must
 *     say that, never render 0.0% as a real confidence value.
 *   - Reasons are translated by exact-match table; unknown codes fall
 *     back to the raw code (never invented explanations).
 * ========================================================================== */
(function (global) {
    'use strict';

    function t(key, fallback, vars) {
        var i = global.NX_I18N;
        var s = i ? i.t(key, fallback) : fallback;
        if (vars) Object.keys(vars).forEach(function (k) { s = s.split('{' + k + '}').join(vars[k]); });
        return s;
    }

    var REASONS = {
        BLOCKED_BY_GUARDIAN_UNSAFE_REGIME: {
            simple: 'Market conditions are currently not safe enough to enter. The engine stood aside.',
            detail: 'The regime guardian classified the market as unsafe; the model was not consulted (no confidence is produced).'
        },
        CONFIDENCE_GATE: {
            simple: 'The model saw an opportunity but its confidence stayed below the required level.',
            detail: 'The candidate was filtered by the confidence gate before reaching execution.'
        },
        NO_CANDIDATE: {
            simple: 'No qualifying trade setup at this moment.',
            detail: 'No setup passed the candidate filters.'
        },
        HIGH_IMPACT_NEWS: {
            simple: 'High-impact news window — trading paused for safety.',
            detail: 'News risk governor blocked the decision during a high-impact release.'
        }
    };

    var REASON_KEYS = {
        BLOCKED_BY_GUARDIAN_UNSAFE_REGIME: 'ux.reason.BLOCKED_BY_GUARDIAN_UNSAFE_REGIME',
        CONFIDENCE_GATE: 'ux.reason.CONFIDENCE_GATE',
        NO_CANDIDATE: 'ux.reason.NO_CANDIDATE'
    };

    var DECISION_LABEL_KEYS = {
        NO_TRADE: 'ux.signal.no_trade',
        BUY: 'ux.signal.buy',
        SELL: 'ux.signal.sell',
        WAIT: 'ux.signal.wait'
    };

    /** Returns {tone, human, detail, confidenceText, confidenceKind} or null. */
    function explain(payload) {
        if (!payload || payload.ai_decision == null) return null;
        var decision = String(payload.ai_decision);
        var reason = payload.ai_reason != null ? String(payload.ai_reason) : '';
        var out = {
            decision: decision,
            tone: decision === 'BUY' ? 'buy' : decision === 'SELL' ? 'sell' : 'hold',
            label: t(DECISION_LABEL_KEYS[decision] || '', decision)
        };

        var known = REASONS[reason] || null;
        if (known) {
            var key = REASON_KEYS[reason];
            out.human = key ? t(key, known.simple) : known.simple;
            out.detail = known.detail;
        } else if (reason) {
            out.human = null;           // unknown code: show it verbatim, invent nothing
            out.detail = reason;
        } else {
            out.human = null;
            out.detail = null;
        }

        // Confidence semantics (§52 no fakery): decision confidence 0.0 with a
        // guardian/reason block means the model was not consulted.
        var conf = payload.ai_confidence;
        if (conf == null) {
            out.confidenceKind = 'none';
            out.confidenceText = '—';
        } else if (conf === 0 && reason && reason !== 'CONFIDENCE_GATE') {
            out.confidenceKind = 'not_consulted';
            out.confidenceText = t('ux.signal.not_available', 'Signal not available');
        } else {
            out.confidenceKind = 'value';
            out.confidenceText = (conf * 100).toFixed(1) + '%';
        }

        // Freshness (§16: data freshness is part of the signal experience).
        var lf = payload.live_freshness || {};
        var infresh = (lf.inference && lf.inference.state) || (lf.market && lf.market.state) || null;
        out.freshness = infresh ? String(infresh) : null;
        return out;
    }

    /**
     * Renders the simple layer into the decision card and attaches the
     * technical layer as a toggle. Existing ids stay authoritative:
     *   ai-decision-badge, ai-confidence, ai-reason-text (+ new ai-why-simple,
     *   ai-why-details, freshness chip reuses monitor-tick-state semantics).
     */
    function render(payload) {
        var ex = explain(payload);
        var badge = document.getElementById('ai-decision-badge');
        if (!ex || !badge) return;

        // Tone classes — color-INDEPENDENT status (§34): text always carries
        // the label too; icons double-encode.
        var tone = {
            buy: 'text-emerald-400',
            sell: 'text-rose-400',
            hold: 'text-sky-300'
        }[ex.tone];

        badge.textContent = ex.label;
        badge.className = 'text-2xl font-black tracking-wider ' + tone;

        var confEl = document.getElementById('ai-confidence');
        if (confEl) {
            if (ex.confidenceKind === 'value') {
                confEl.textContent = t('ux.signal.confidence', 'Confidence') + ': ' + ex.confidenceText;
            } else if (ex.confidenceKind === 'not_consulted') {
                confEl.textContent = ex.confidenceText;
            } else {
                confEl.textContent = t('ux.signal.confidence', 'Confidence') + ': —';
            }
        }

        // Simple explanation line (Layer 1) — inserted above the technical one.
        var reasonEl = document.getElementById('ai-reason-text');
        if (reasonEl) {
            var simpleId = 'ai-why-simple';
            var simple = document.getElementById(simpleId);
            if (!simple) {
                simple = document.createElement('div');
                simple.id = simpleId;
                simple.className = 'text-xs mt-2 leading-relaxed text-gray-200';
                reasonEl.parentNode.insertBefore(simple, reasonEl);
            }
            simple.textContent = ex.human || '';
            simple.classList.toggle('hidden', !ex.human);
            // Layer 2: keep the raw technical reason exactly as before.
            reasonEl.textContent = ex.detail ? '"' + ex.detail + '"' : '';
            reasonEl.classList.toggle('hidden', !ex.detail);
        }
    }

    global.NXSignal = { explain: explain, render: render, REASONS: REASONS };
})(window);
