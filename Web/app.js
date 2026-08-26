// app.js

// Front-End Engine for Nexus Scalp Engine (NSE) Control Center



let eventSource = null;

let currentTab = 'tab-monitoring';

let currentFeatureCategory = 'volatility';

let candleData = []; // [{time, open, high, low, close, volume, is_complete}]

let predictions = []; // [{time, action, confidence, actual_delta, outcome}]

let selectedConfig = {};



let supportLevels = [];

let resistanceLevels = [];

let lastFeatures = [];

let visualOverlays = { rectangles: [], order_lines: null };



const FEATURE_NAMES_JS = [

    "upper_wick_ratio",             // feat_0

    "lower_wick_ratio",             // feat_1

    "body_to_range_ratio",          // feat_2

    "is_doji",                      // feat_3

    "pinbar_sig",                   // feat_4

    "engulfing_sig",                // feat_5

    "close_location_value",         // feat_6

    "consecutive_momentum_count",   // feat_7

    "norm_displacement",            // feat_8

    "rapid_reversal_spike_val",     // feat_9

    "dist_to_swing_high_20",        // feat_10

    "dist_to_swing_low_20",         // feat_11

    "price_compression_flag_ratio", // feat_12

    "extreme_sig",                  // feat_13

    "stop_hunt_depth",              // feat_14

    "liquidity_sweep_signal",       // feat_15

    "session_tokyo",                // feat_16

    "session_london",               // feat_17

    "session_ny",                   // feat_18

    "session_overlap_london_ny",    // feat_19

    "lag_1_log_return",             // feat_20

    "lag_2_log_return",             // feat_21

    "lag_3_log_return",             // feat_22

    "lag_1_atr_ratio",              // feat_23

    "lag_1_volume_z",               // feat_24

    "lag_1_clv",                    // feat_25

    "fvg_sig",                      // feat_26

    "order_block_type",             // feat_27

    "choch_sig",                    // feat_28

    "breakout_sig",                 // feat_29

    "norm_tk_diff",                 // feat_30

    "tk_cross_signal",              // feat_31

    "kumo_sig",                     // feat_32

    "norm_kumo_width",              // feat_33

    "norm_rsi",                     // feat_34

    "dist_to_ema_21",               // feat_35

    "dist_to_ema_50",               // feat_36

    "cross_asset_z_score",          // feat_37

    "norm_dist_to_tenkan",          // feat_38

    "norm_dist_to_kijun",           // feat_39

    "htf_h4_trend",                 // feat_40

    "htf_h1_momentum",              // feat_41

    "htf_m30_structure",            // feat_42

    "htf_m15_confirmation",         // feat_43

    "support_zone_dist",            // feat_44

    "resistance_zone_dist",         // feat_45

    "feat_ob_valid_bos",            // feat_46

    "feat_ob_equilibrium_ratio",    // feat_47

    "feat_ob_liquidity_swept",      // feat_48

    "feat_ob_fib_50_60_alignment",  // feat_49

];



// Chart state variables for interactive Zoom, Pan, Drag, and Tooltip

let liveMode = true; // Auto scroll to newest candle

let uiPaused = false; // Ignore incoming state updates

let candleWidth = 10;

let candleGap = 3;

let chartPanX = 0; // Negative values translate to historical panning

let isDragging = false;

let dragStartX = 0;

let lastPanX = 0;

let lastTouchDist = 0; // Pinch to zoom support

let crosshairX = -1;

let crosshairY = -1;



// AI VIEW / FORENSIC REPLAY mode state (Phase 14)

let aiViewEnabled = false;      // when true, chart shows the AI-visible context of the selected candle

let aiViewCandleIdx = -1;       // selected candle index for AI VIEW

let lastAiSnapshot = null;      // last live payload used to derive per-candle snapshots



// Canonical UTC time formatting for chart timestamps (transport is UTC ISO).

function formatUTCTime(t) {

    if (!t) return '--';

    const s = String(t);

    // ISO "2026-08-17T02:31:00+00:00" -> "02:31:00Z" (slice before offset)

    const m = s.match(/(\d{2}):(\d{2}):(\d{2})/);

    return m ? `${m[1]}:${m[2]}:${m[3]}Z` : s;

}



// Toggle AI VIEW: shows the exact market/feature/model snapshot the AI saw at

// the candle nearest the crosshair (or the latest candle).

function toggleAiView() {

    aiViewEnabled = !aiViewEnabled;

    const btn = document.getElementById('btn-ai-view');

    const panel = document.getElementById('ai-snapshot-panel');

    if (panel) panel.classList.toggle('hidden', !aiViewEnabled);

    if (btn) {

        if (aiViewEnabled) {

            btn.className = "px-2 py-0.5 rounded bg-accentGold/10 text-accentGold hover:bg-accentGold/20 border border-accentGold/30 transition";

            btn.innerHTML = `<i class="fa-solid fa-robot mr-1"></i> AI VIEW ON`;

            // Default to the newest candle when enabling.

            if (candleData.length > 0) {

                aiViewCandleIdx = candleData.length - 1;

            }

            renderAiSnapshotPanel();

            if (currentTab === 'tab-monitoring') drawChart();

        } else {

            btn.className = "px-2 py-0.5 rounded bg-darkBg hover:bg-borderClr border border-borderClr text-gray-400 transition";

            btn.innerHTML = `<i class="fa-solid fa-robot mr-1"></i> AI View`;

        }

    }

}



// Per-candle AI snapshot: OHLC + spread/ATR/regime + probability distribution

// (from the last live state) + strategy + structure. This is the honest

// representation of what the model saw: the live feature vector belongs to the

// LATEST inference, so older candles show the market context and mark model

// output as belonging to the latest inference (no fabricated per-candle

// probabilities).

function renderAiSnapshotPanel() {

    const panel = document.getElementById('ai-snapshot-panel');

    if (!panel) return;



    if (!aiViewEnabled || candleData.length === 0) {

        panel.innerHTML = '<div class="text-textMuted italic text-[11px]">Enable AI VIEW and hover a candle to inspect the exact AI-visible market context.</div>';

        return;

    }



    const idx = (aiViewCandleIdx >= 0 && aiViewCandleIdx < candleData.length) ? aiViewCandleIdx : candleData.length - 1;

    const c = candleData[idx];

    const snap = lastAiSnapshot || {};



    const reg = snap.regime || '—';

    const atr = (snap.atr != null) ? Number(snap.atr).toFixed(2) : '—';

    const spread = (snap.spread != null) ? snap.spread : '—';

    const prov = snap.provenance || {};



    // Latest live model output (single authoritative inference).

    const probs = snap.probs || {};

    let probHtml = '';

    if (probs.available && probs.no_trade != null) {

        probHtml = `

            <div class="text-[10px] font-mono">

                <div class="flex justify-between"><span class="text-accentCyan">NO_TRADE</span><span class="text-white font-bold">${(probs.no_trade * 100).toFixed(1)}%</span></div>

                <div class="flex justify-between"><span class="text-emerald-400">BUY</span><span class="text-white font-bold">${(probs.buy * 100).toFixed(1)}%</span></div>

                <div class="flex justify-between"><span class="text-rose-400">SELL</span><span class="text-white font-bold">${(probs.sell * 100).toFixed(1)}%</span></div>

            </div>`;

    } else {

        probHtml = '<div class="text-[10px] text-textMuted">Model inference unavailable at this snapshot.</div>';

    }



    const decision = snap.ai_decision || '—';

    const conf = (snap.ai_confidence != null) ? (snap.ai_confidence * 100).toFixed(1) + '%' : '—';



    panel.innerHTML = `

        <div class="grid grid-cols-2 gap-3 text-[10px] font-mono">

            <div class="bg-darkBg/50 rounded p-2 border border-borderClr/40">

                <div class="text-textMuted uppercase mb-1">Candle [${idx}]</div>

                <div class="text-white">${formatUTCTime(c.time)}</div>

                <div class="mt-1 text-gray-300">O <span class="text-white">${c.open.toFixed(2)}</span></div>

                <div class="text-gray-300">H <span class="text-emerald-400">${c.high.toFixed(2)}</span></div>

                <div class="text-gray-300">L <span class="text-rose-400">${c.low.toFixed(2)}</span></div>

                <div class="text-gray-300">C <span class="text-white">${c.close.toFixed(2)}</span></div>

                <div class="text-gray-300">V <span class="text-white">${c.volume}</span></div>

                <div class="mt-1 text-textMuted">${c.is_complete ? 'COMPLETED' : 'FORMING'}</div>

            </div>

            <div class="space-y-2">

                <div class="bg-darkBg/50 rounded p-2 border border-borderClr/40">

                    <div class="text-textMuted uppercase mb-1">Market Context</div>

                    <div class="text-gray-300">Regime <span class="text-accentGold font-bold">${reg}</span></div>

                    <div class="text-gray-300">ATR <span class="text-white">${atr}</span></div>

                    <div class="text-gray-300">Spread <span class="text-white">${spread} pts</span></div>

                </div>

                <div class="bg-darkBg/50 rounded p-2 border border-borderClr/40">

                    <div class="text-textMuted uppercase mb-1">Model Output (latest inference)</div>

                    ${probHtml}

                </div>

            </div>

        </div>

        <div class="mt-2 bg-darkBg/50 rounded p-2 border border-borderClr/40 text-[10px] font-mono">

            <div class="text-textMuted uppercase mb-1">Policy Snapshot</div>

            <div class="text-gray-300">Decision <span class="text-accentCyan font-bold">${decision}</span> · Confidence <span class="text-white">${conf}</span></div>

            <div class="text-gray-300 mt-1">${snap.ai_reason || '—'}</div>

            <div class="mt-1 text-textMuted">

                provenance: ${prov.price || 'UNAVAILABLE'} · ${prov.features || 'UNAVAILABLE'} · ${prov.model || 'UNAVAILABLE'}

            </div>

        </div>

    `;

}



// Feature Delta View: compares the live 50D vector against the previous snapshot.

function renderFeatureDeltas() {

    const box = document.getElementById('feature-delta-view');

    if (!box) return;

    if (!lastFeatures || lastFeatures.length === 0) {

        box.innerHTML = '<div class="text-textMuted italic text-[11px]">No feature snapshots recorded yet.</div>';

        return;

    }

    const rows = lastFeatures.slice(0, 12).map(f => {

        const val = (f.value != null) ? f.value.toFixed(2) : '—';

        return `<div class="flex justify-between text-[9px] font-mono border-b border-borderClr/30 py-0.5">

            <span class="text-textMuted truncate">${f.name}</span>

            <span class="text-white">${val}</span>

        </div>`;

    }).join('');

    box.innerHTML = `<div class="text-[10px] text-textMuted mb-1 uppercase">Current live values (top 12)</div>${rows}`;

}



// ===========================================================================

// TASK-02-70D-INTEGRATION: LIQUIDITY INTELLIGENCE UI

// Real backend state only (GET /api/liquidity/state). The toggle calls

// POST /api/liquidity/toggle which persists via SettingsService and

// hot-applies the runtime governor. No fake values, no silent failures.

// ===========================================================================

function liqStatusStyle(status) {

    const s = String(status || '').toUpperCase();

    const styles = {

        ENABLED: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',

        LIVE: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',

        DISABLED: 'bg-slate-500/10 text-slate-300 border-slate-500/30',

        DEGRADED: 'bg-amber-500/10 text-amber-400 border-amber-500/30',

        STALE: 'bg-amber-500/10 text-amber-400 border-amber-500/30',

        UNAVAILABLE: 'bg-rose-500/10 text-rose-400 border-rose-500/30',

        ERROR: 'bg-rose-500/10 text-rose-400 border-rose-500/30',

        INVALID: 'bg-rose-500/10 text-rose-400 border-rose-500/30',

        VALID: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',

    };

    return styles[s] || styles.UNAVAILABLE;

}



function liqValueColor(v) {

    if (v === null || v === undefined || Number.isNaN(v)) return 'text-textMuted';

    // Structural data is NOT a directional signal (brief 18): magnitude is

    // never rendered as BUY/SELL pressure; only a sign tint is applied.

    if (v >= 1.0) return 'text-emerald-400';

    if (v <= -1.0) return 'text-rose-400';

    return 'text-accentCyan';

}



function setText(id, val) {

    const el = document.getElementById(id);

    if (el && val !== undefined && val !== null) el.textContent = val;

}



function renderLiquidityPanel(state) {

    if (!state || typeof state !== 'object') return;

    const st = state.status || 'UNAVAILABLE';
    // BUG-111: every value is rendered from its BACKEND provenance —
    // per-feature index/source/status come from the API payload, NEVER
    // derived from a hardcoded base dimension in JS.
    const backendIdx = (state.feature_names || []).map((n, i) => {
        const f = (state.features || {})[n];
        return f && typeof f === 'object' && f.index != null ? f.index : null;
    });
    const feaAvail = state.feature_availability || (state.available ? 'AVAILABLE' : 'UNAVAILABLE');
    const disabled = state.enabled === false;

    const badge = document.getElementById('liq-status-badge');

    if (badge) {

        badge.textContent = st;

        badge.className = 'text-[10px] font-black px-2 py-1 rounded border ' + liqStatusStyle(st);

    }

    const navBadge = document.getElementById('liq-nav-state');

    if (navBadge) { navBadge.textContent = st; navBadge.className = 'text-[9px] font-black px-1.5 py-0.5 rounded border ' + liqStatusStyle(st); }



    setText('liq-status-value', st);

    setText('liq-schema', (state.schema && state.schema.id) || '--');
    setText('liq-algo-version', state.algorithm_version || '--');

    setText('liq-dim', (state.schema && state.schema.dimension) != null ? String(state.schema.dimension) + 'D' : '--');

    setText('liq-feature-count', state.feature_count != null ? String(state.feature_count) : '--');

    setText('liq-source', state.source || '--');

    setText('liq-source-status', state.source_status || state.source || '--');

    setText('liq-calc-status', state.calculation_status || '--');

    setText('liq-causal', state.causal_state || '--');

    // BUG-111: wall-clock last_update (never the 1970 monotonic sentinel);
    // a retained snapshot while DISABLED shows its snapshot timestamp too,
    // so old values can never masquerade as live.
    let lastUpdateTxt = state.last_update ? String(state.last_update).replace('T', ' ').slice(0, 19) : '--';
    if (state.snapshot_timestamp && state.snapshot_timestamp !== state.last_update) {
        lastUpdateTxt += ' · snap ' + String(state.snapshot_timestamp).replace('T', ' ').slice(0, 19);
    }
    setText('liq-last-update', lastUpdateTxt);

    setText('liq-latency', state.latency_ms != null ? state.latency_ms.toFixed(2) + ' ms' : '--');

    // BUG-111 explicit availability semantics: AVAILABLE / STALE_CACHE /
    // UNAVAILABLE / NOT_ACTIVE — never a bare 'Available' next to
    // 'Source: UNAVAILABLE'.
    const availMap = {
        AVAILABLE: 'Available',
        STALE_CACHE: 'STALE CACHE',
        UNAVAILABLE: 'Unavailable',
        NOT_ACTIVE: 'NOT ACTIVE',
    };
    setText('liq-available', availMap[feaAvail] || feaAvail || (state.available ? 'Available' : 'Unavailable'));
    const aEl = document.getElementById('liq-available');
    if (aEl) {
        const baseCls = 'text-sm font-mono font-bold mt-1 ';
        if (feaAvail === 'AVAILABLE') aEl.className = baseCls + 'text-emerald-400';
        else if (feaAvail === 'STALE_CACHE' || feaAvail === 'NOT_ACTIVE') aEl.className = baseCls + 'text-amber-400';
        else aEl.className = baseCls + 'text-rose-400';
    }



    const mc = state.model_compatibility || {};

    const mcEl = document.getElementById('liq-model-compat');

    if (mcEl) {

        const res = mc.result || 'UNKNOWN';

        mcEl.textContent = res + (mc.reason ? ' (' + mc.reason + ')' : '');

        mcEl.className = 'text-sm font-mono font-bold mt-1 ' + (res === 'PASS' ? 'text-emerald-400' : (res === 'BLOCK' ? 'text-rose-400' : 'text-amber-400'));

    }

    // BUG-123: model contract diagnostics — runtime/model dimension, schemas,
    // feature-order hash, normalization. Rendered from the backend payload only.
    const contract = state.liquidity_contract || {};

    const mcModel = document.getElementById('liq-model-contract');

    if (mcModel) {

        const rt = (contract && contract.dimension) ? contract.dimension : null;

        const md = (mc && mc.model_dimension != null) ? mc.model_dimension : null;

        const ms = (mc && mc.model_schema_id) ? mc.model_schema_id : '?';

        const mih = (mc && mc.model_input_dimension != null) ? mc.model_input_dimension : null;

        const bits = [];

        if (md != null) bits.push('model ' + md + 'D (' + ms + (mih != null ? ' / tensor ' + mih + 'D' : '') + ')');

        if (rt != null) bits.push('runtime ' + rt + 'D');

        mcModel.textContent = bits.join(' \u00b7 ') || '--';

    }

    const mcReason = document.getElementById('liq-model-compat-reason');

    if (mcReason) {

        let reasonTxt = mc.reason || '';

        if (mc.action) reasonTxt += ' \u2026 ' + mc.action;

        mcReason.textContent = reasonTxt || '--';

        mcReason.className = 'text-[10px] font-mono mt-1 ' + ((mc.result || 'UNKNOWN') === 'BLOCK' ? 'text-rose-400' : ((mc.result || 'UNKNOWN') === 'PASS' ? 'text-emerald-400' : 'text-gray-300'));

    }

    setText('liq-state-revision', state.state_revision != null ? '# ' + state.state_revision : '--');



    const btn = document.getElementById('liq-toggle-btn');

    if (btn) {

        if (state.enabled) {

            btn.textContent = 'Disable Liquidity Intelligence';

            btn.className = 'bg-rose-500/10 text-rose-400 border border-rose-500/30 rounded px-3 py-1 text-xs font-bold hover:bg-rose-500/20 transition';

        } else {

            btn.textContent = 'Enable Liquidity Intelligence';

            btn.className = 'bg-accentCyan/10 text-accentCyan border border-accentCyan/30 rounded px-3 py-1 text-xs font-bold hover:bg-accentCyan/20 transition';

        }

    }



    const grid = document.getElementById('liq-features-grid');

    if (grid) {

        const feats = state.features || {};

        const names = state.feature_names || Object.keys(feats);

        if (!names.length) {

            grid.innerHTML = '<div class="text-textMuted italic text-xs col-span-5">No liquidity snapshot yet — waiting for the live engine.</div>';

        } else {

            grid.innerHTML = names.map((name, i) => {

                const raw = feats[name];
                // BUG-111: per-feature provenance object from the backend
                // (index/source/status/timestamp); fall back to a bare
                // number ONLY for legacy payloads — never derive the index
                // from a JS-computed base dimension.
                const meta = (raw && typeof raw === 'object') ? raw : null;
                const v = meta ? meta.value : raw;
                const idx = meta && meta.index != null ? meta.index : (backendIdx[i] != null ? backendIdx[i] : '');
                const src = meta ? (meta.source || '') : '';
                const fst = meta ? (meta.status || '') : '';
                const fa = meta ? (meta.feature_availability || feaAvail) : feaAvail;

                const valStr = (v !== null && v !== undefined) ? Number(v).toFixed(3) : '—';

                // Explicit provenance row: index + source + status. A
                // NOT_ACTIVE / STALE_CACHE value is visibly marked — never
                // presented as a live model input.
                const provBits = [];
                if (idx !== '') provBits.push('idx ' + idx);
                if (src) provBits.push(src);
                if (fa && fa !== 'AVAILABLE') provBits.push(fa);
                else if (fst) provBits.push(fst);

                const cardCls = (fa === 'STALE_CACHE' || fa === 'NOT_ACTIVE' || disabled)
                    ? 'bg-darkBg/40 border border-amber-500/30 p-2.5 rounded-lg opacity-80'
                    : 'bg-darkBg/40 border border-borderClr/60 p-2.5 rounded-lg';

                return '<div class="' + cardCls + '" title="' + escHtml(name) + ' (index ' + (idx === '' ? '?' : idx) + ')">' +

                    '<div class="text-[9px] text-textMuted font-bold uppercase truncate">' + escHtml(name).replace(/_/g, ' ') + '</div>' +

                    '<div class="flex items-baseline justify-between mt-1">' +

                        '<span class="text-sm font-mono font-black ' + liqValueColor(v) + '">' + valStr + '</span>' +

                        '<span class="text-[8px] text-textMuted font-mono">' + provBits.join(' · ') + '</span>' +

                    '</div>' +

                '</div>';

            }).join('');

        }

    }



    const errEl = document.getElementById('liq-error');

    if (errEl) {

        if (state.error) {

            errEl.textContent = 'Error: ' + state.error;

            errEl.classList.remove('hidden');

        } else {

            errEl.classList.add('hidden');

        }

    }

}



async function loadLiquidityState() {

    const endpoint = '/api/liquidity/state';

    try {

        const res = await fetch(endpoint, { headers: { 'X-Request-ID': 'liq_' + Date.now().toString(36) } });

        if (!res.ok) {

            console.warn('[LIQUIDITY_UI] event=request_failed endpoint=' + endpoint + ' status=' + res.status + ' correlation_id=liq_fail_' + Date.now());

            return;

        }

        const data = await res.json();

        if (data && data.success !== false) {

            renderLiquidityPanel(data);

            console.log('[LIQUIDITY_UI] event=state_loaded schema=' + (data.schema ? data.schema.id : '?') + ' dimension=' + (data.schema ? data.schema.dimension : '?') + ' enabled=' + (data.enabled === true) + ' available=' + (data.available === true));

        } else {

            console.warn('[LIQUIDITY_UI] event=load_failed endpoint=' + endpoint + ' status=200 body_error=' + (data && data.reason ? data.reason : '?'));

        }

    } catch (err) {

        console.warn('[LIQUIDITY_UI] event=load_failed endpoint=' + endpoint + ' status=network correlation_id=liq_net_' + Date.now() + ' error=' + (err && err.message ? err.message : String(err)));

    }

}



async function toggleLiquidity() {

    const btn = document.getElementById('liq-toggle-btn');

    const current = btn ? !btn.textContent.startsWith('Enable') : false;

    const endpoint = '/api/liquidity/toggle';

    try {

        const res = await fetch(endpoint, {

            method: 'POST',

            headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'liq_toggle_' + Date.now().toString(36) },

            body: JSON.stringify({ enabled: !current }),

        });

        if (!res.ok) {

            console.warn('[LIQUIDITY_UI] event=toggle_failed endpoint=' + endpoint + ' status=' + res.status + ' correlation_id=liq_tgl_' + Date.now());

            return;

        }

        const data = await res.json();

        if (data && data.success !== false) {

            renderLiquidityPanel(data);

            console.log('[LIQUIDITY_UI] event=toggle_applied enabled=' + (data.enabled === true) + ' status=' + (data.status || '?'));

        } else {

            console.warn('[LIQUIDITY_UI] event=toggle_rejected endpoint=' + endpoint + ' reason=' + (data && data.error ? data.error : '?'));

        }

    } catch (err) {

        console.warn('[LIQUIDITY_UI] event=toggle_failed endpoint=' + endpoint + ' status=network error=' + (err && err.message ? err.message : String(err)));

    }

}



// Keep the liquidity panel in sync from the canonical snapshot (SSE/status).

// BUG-111 stale-SSE guard: liquidity carries its own monotonic
// state_revision; the UI drops older revisions so a delayed SSE tick
// can never overwrite a newer toggle/snapshot with stale state.
let __liquidityLastRevision = -1;

function syncLiquidityFromSnapshot(payload) {

    if (payload && payload.liquidity) {

        const liq = payload.liquidity;
        const rev = (liq.state_revision != null) ? Number(liq.state_revision) : -1;
        if (rev >= 0 && __liquidityLastRevision >= 0 && rev < __liquidityLastRevision) {
            console.warn('[LIQUIDITY_UI] event=STALE_REVISION_DROPPED rev=' + rev + ' last=' + __liquidityLastRevision);
            return;
        }
        if (rev >= 0) __liquidityLastRevision = rev;

        renderLiquidityPanel(liq);

        window.__liquidityPools = liq.pools || [];

        if (typeof drawChart === 'function') drawChart();

    }

}



// On Startup

window.addEventListener('load', () => {

    initApp();

    initDebugHub();

    // REST snapshot first (canonical), then SSE for incremental updates.

    // This guarantees a complete initial render even when the SSE stream is

    // slow to open, and gives refresh/reconnect a full fresh snapshot.

    fetchSystemSnapshot();

    startSSE();

    loadConfiguration();

    loadLiquidityState();

    setInterval(updateHeartbeats, 5000);

});



// GET /api/status canonical snapshot -> render full UI immediately.

// Called on page load AND after every SSE reconnect so the dashboard always

// converges back to live state without a manual refresh.

async function fetchSystemSnapshot() {

    try {

        const res = await fetch('/api/status', { headers: { 'X-Request-ID': 'snapshot_' + Date.now().toString(36) } });

        if (!res.ok) {

            console.warn('[UI_ERROR] component=State action=LOAD_SNAPSHOT status=' + res.status);

            setSystemBadge('disconnected');

            return;

        }

        const data = await res.json();

        lastApiResponseAt = Date.now();

        lastSnapshotVersion = data.state_version != null ? data.state_version : lastSnapshotVersion;

        lastSnapshotAt = Date.now();

        updateObsStrip();

        handleIncomingLiveTick(data, { isSnapshot: true });
        renderMarketRadar(liveUiSnapshot && liveUiSnapshot.radar);


    } catch (err) {

        console.warn('[UI_ERROR] component=State action=LOAD_SNAPSHOT status=network', err);

        setSystemBadge('disconnected');

    }

}



// Health badge coloring (LiveUiState.2 health section).

function healthBadgeStyle(status) {

    const styles = {

        READY: 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30',

        IDLE: 'bg-slate-500/10 text-slate-300 border border-slate-500/30',

        WARMING_UP: 'bg-amber-500/10 text-amber-400 border border-amber-500/30',

        STALE: 'bg-amber-500/10 text-amber-400 border border-amber-500/30',

        DEGRADED: 'bg-amber-500/10 text-amber-400 border border-amber-500/30',

        DISCONNECTED: 'bg-rose-500/10 text-rose-400 border border-rose-500/30',

        ERROR: 'bg-rose-500/10 text-rose-400 border border-rose-500/30',

        UNAVAILABLE: 'bg-rose-500/10 text-rose-400 border border-rose-500/30',

        STOPPED: 'bg-rose-500/10 text-rose-400 border border-rose-500/30',

        DISABLED: 'bg-slate-500/10 text-slate-300 border border-slate-500/30',

    };

    return styles[status] || styles.UNAVAILABLE;

}



function initApp() {

    console.log("Nexus Scalp Engine Front-End Booted.");

    console.log("[UI_STATE] canonical state source: GET /api/status snapshot + /api/ticks/stream SSE");



    // FORENSIC HARDENING: no dummy feature seed. The Feature Matrix renders

    // only real ENGINE_STATE values; before the first snapshot arrives it

    // shows an explicit waiting state (zeros were previously rendered as if

    // they were live values - a fake-data masquerade).

    lastFeatures = [];



    // Hook up some simulation button controls

    document.getElementById('btn-toggle-engine').addEventListener('click', toggleEngineRunning);

    // Forensic Incident Center: Stop Bot type-to-confirm + task drawer close.
    var stopInput = document.getElementById('stop-bot-confirm-input');
    if (stopInput) stopInput.addEventListener('input', onStopBotInput);
    var taskBackdrop = document.getElementById('task-drawer-backdrop');
    if (taskBackdrop) taskBackdrop.addEventListener('click', closeTaskDrawer);



    // Register interactive zoom and pan events on canvas container

    const container = document.getElementById('chart-container');

    const canvas = document.getElementById('candleChart');

    if (container && canvas) {

        // Drag scrolling (mouse)

        container.addEventListener('mousedown', (e) => {

            isDragging = true;

            dragStartX = e.clientX;

            lastPanX = chartPanX;

            liveMode = false;

            updateLiveToggleUI();

        });



        // Click selects the candle for AI VIEW forensic inspection.

        container.addEventListener('click', (e) => {

            const rect = canvas.getBoundingClientRect();

            const x = e.clientX - rect.left;

            const idx = Math.floor((x - chartPanX) / (candleWidth + candleGap));

            if (idx >= 0 && idx < candleData.length) {

                aiViewCandleIdx = idx;

                if (aiViewEnabled) {

                    renderAiSnapshotPanel();

                    drawChart();

                }

            }

        });



        window.addEventListener('mousemove', (e) => {

            // Mouse move coordinate track for crosshair tooltip

            const rect = canvas.getBoundingClientRect();

            crosshairX = e.clientX - rect.left;

            crosshairY = e.clientY - rect.top;



            if (isDragging) {

                const deltaX = e.clientX - dragStartX;

                chartPanX = lastPanX + deltaX;

                drawChart();

            } else {

                updateCrosshairTooltip();

            }

        });



        window.addEventListener('mouseup', () => {

            isDragging = false;

        });



        // Mousewheel zoom centered on pointer

        container.addEventListener('wheel', (e) => {

            e.preventDefault();

            const rect = canvas.getBoundingClientRect();

            const mouseX = e.clientX - rect.left;

            const priceX = mouseX - chartPanX;



            const oldWidth = candleWidth;

            if (e.deltaY < 0) {

                candleWidth = Math.min(50, candleWidth + 1);

            } else {

                candleWidth = Math.max(3, candleWidth - 1);

            }



            // Adjust pan position to keep zoom centered under pointer

            const ratio = candleWidth / oldWidth;

            chartPanX = mouseX - priceX * ratio;

            liveMode = false;

            updateLiveToggleUI();

            drawChart();

        }, { passive: false });



        // Touch swipe scrolling & pinch-to-zoom (mobile)

        container.addEventListener('touchstart', (e) => {

            isDragging = true;

            liveMode = false;

            updateLiveToggleUI();

            if (e.touches.length === 1) {

                dragStartX = e.touches[0].clientX;

                lastPanX = chartPanX;

            } else if (e.touches.length === 2) {

                lastTouchDist = Math.hypot(

                    e.touches[0].clientX - e.touches[1].clientX,

                    e.touches[0].clientY - e.touches[1].clientY

                );

            }

        });



        container.addEventListener('touchmove', (e) => {

            if (!isDragging) return;

            if (e.touches.length === 1) {

                const deltaX = e.touches[0].clientX - dragStartX;

                chartPanX = lastPanX + deltaX;

                drawChart();

            } else if (e.touches.length === 2) {

                const dist = Math.hypot(

                    e.touches[0].clientX - e.touches[1].clientX,

                    e.touches[0].clientY - e.touches[1].clientY

                );

                const delta = dist - lastTouchDist;

                lastTouchDist = dist;

                candleWidth = Math.max(3, Math.min(50, candleWidth + (delta * 0.05)));

                drawChart();

            }

        });



        container.addEventListener('touchend', () => {

            isDragging = false;

        });



        container.addEventListener('mouseleave', () => {

            crosshairX = -1;

            crosshairY = -1;

            updateCrosshairTooltip();

        });

    }



    // Window auto-resize handling

    window.addEventListener('resize', () => {

        if (currentTab === 'tab-monitoring') {

            drawChart();

        }

    });



    // Fetch initial historical OHLC bars & overlays immediately to bootstrap the canvas visualizer

    NX.api.get('/api/chart/history', { component: 'Chart', action: 'LOAD_HISTORY' })

        .then(payload => {

            if (!payload.ok) {

                console.warn('[UI_ERROR] component=Chart action=LOAD_HISTORY ' + NX.api.msg(payload, 'Chart history unavailable.'));

                setChartStatus('error');

                drawChart();

                return;

            }

            const body = payload.body || {};

            if (body.bars && body.bars.length > 0) {

                candleData = body.bars;

                setChartStatus('ok');

            } else {

                candleData = [];

                // Explicit empty state - never synthetic candles.

                setChartStatus('empty');

                drawChart();

                return;

            }

            if (body.visual_overlays) {

                visualOverlays = body.visual_overlays;

            }

            // Auto fit and paint the candles immediately

            autoFitChart();

            drawChart();

        })

        .catch(err => {

            console.warn('[UI_ERROR] component=Chart action=LOAD_HISTORY status=network', err);

            setChartStatus('error');

            drawChart();

        });

}



function setChartStatus(state) {

    const el = document.getElementById('chart-status');

    if (!el) return;

    if (state === 'ok') {

        el.textContent = '';

        el.className = '';

    } else if (state === 'empty') {

        el.textContent = 'NO CANDLE DATA — engine offline or no bars yet.';

        el.className = 'text-[10px] font-mono text-textMuted mt-1';

    } else if (state === 'error') {

        el.textContent = 'Chart data unavailable — check server logs (request_id in console).';

        el.className = 'text-[10px] font-mono text-rose-400 mt-1';

    } else if (state === 'stale') {

        el.textContent = 'Live stream stale — no updates for 30s.';

        el.className = 'text-[10px] font-mono text-amber-400 mt-1';

    }

}



// RESYNC (BUG-054): re-fetch the full broker candle history and swap it into

// the chart. Called manually via the Resync button, after every SSE reconnect,

// and by the stale watchdog. The server mirrors the broker bars into the

// engine aggregator + ServerState, so the whole dashboard converges.

let chartResyncInFlight = false;

let lastChartResyncAt = 0;



async function resyncChart() {

    if (chartResyncInFlight) return;

    chartResyncInFlight = true;

    const btn = document.getElementById('btn-resync-chart');

    if (btn) {

        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Resyncing…';

        btn.disabled = true;

    }

    try {

        const res = await fetch('/api/chart/history?count=900', {

            headers: { 'X-Request-ID': 'resync_' + Date.now().toString(36) }

        });

        if (!res.ok) {

            console.warn('[UI_ERROR] component=Chart action=RESYNC status=' + res.status);

            setChartStatus('error');

            return;

        }

        const body = await res.json();

        if (body.bars && body.bars.length > 0) {

            candleData = body.bars;

            setChartStatus('ok');

            if (body.visual_overlays) visualOverlays = body.visual_overlays;

            if (body.source) {

                const srcBadge = document.getElementById('chart-source-badge');

                if (srcBadge) srcBadge.textContent = 'sync ' + body.source + ' ✓';

            }

            autoFitChart();

            drawChart();

        } else {

            setChartStatus('empty');

            drawChart();

        }

        lastChartResyncAt = Date.now();

    } catch (err) {

        console.warn('[UI_ERROR] component=Chart action=RESYNC status=network', err);

        setChartStatus('error');

    } finally {

        chartResyncInFlight = false;

        if (btn) {

            btn.innerHTML = '<i class="fa-solid fa-rotate mr-1"></i> Resync';

            btn.disabled = false;

        }

    }

}



function toggleLiveMode() {

    liveMode = !liveMode;

    updateLiveToggleUI();

    if (liveMode) {

        autoFitChart();

    }

}



function updateLiveToggleUI() {

    const btn = document.getElementById('btn-live-toggle');

    if (btn) {

        if (liveMode) {

            btn.className = "px-2 py-0.5 rounded bg-accentCyan/10 text-accentCyan hover:bg-accentCyan/20 border border-accentCyan/30 transition";

        } else {

            btn.className = "px-2 py-0.5 rounded bg-darkBg hover:bg-borderClr border border-borderClr text-gray-400 transition";

        }

    }

}



function togglePlayPause() {

    uiPaused = !uiPaused;

    const btn = document.getElementById('btn-play-pause');

    if (btn) {

        if (uiPaused) {

            btn.innerHTML = `<i class="fa-solid fa-play mr-1"></i> Resume UI`;

            btn.className = "px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 border border-amber-500/30 transition";

        } else {

            btn.innerHTML = `<i class="fa-solid fa-pause mr-1"></i> Pause UI`;

            btn.className = "px-2 py-0.5 rounded bg-darkBg hover:bg-borderClr border border-borderClr text-gray-300 transition";

        }

    }

}



function autoFitChart() {

    const canvas = document.getElementById('candleChart');

    if (!canvas || candleData.length === 0) return;

    const rect = canvas.getBoundingClientRect();

    const w = rect.width;



    // Auto calculate ideal candle width and pan to fit all elements

    candleWidth = Math.max(3, Math.min(30, (w - 100) / candleData.length - candleGap));

    chartPanX = w - 60 - candleData.length * (candleWidth + candleGap);

    drawChart();

}



// Tab Switching Mechanism

function switchTab(tabId, element) {

    // Hide all tabs

    document.querySelectorAll('.tab-content').forEach(tab => tab.classList.add('hidden'));

    document.querySelectorAll('.tab-content').forEach(tab => tab.classList.remove('active'));

    // Deactivate all nav buttons

    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));



    // Show selected tab

    const targetTab = document.getElementById(tabId);

    if (targetTab) {

        targetTab.classList.remove('hidden');

        targetTab.classList.add('active');

    }



    // Activate selected button

    if (element) {

        element.classList.add('active');

    }

    currentTab = tabId;



    if (tabId === 'tab-monitoring') {

        drawChart();

    }

    if (tabId === 'tab-account') {

        // Charts need a visible canvas (getBoundingClientRect must be > 0).

        // Refresh accounting panel + charts now that the tab is shown.

        setTimeout(() => {

            loadAccountPerformance();

            loadAdvancedMetrics();

            loadAccountCharts();

            loadClosedTrades();

            loadAccountPeriod(window.__currentPeriodKind || 'DAY', document.querySelector('.acct-period-btn'));

        }, 80);

    }

    if (tabId === 'tab-rules') {

        loadRules();

    }

    if (tabId === 'tab-ai-analysis') {

        loadIntelligenceSummary();

        renderMarketRadar(liveUiSnapshot && liveUiSnapshot.radar);

    }

    if (tabId === 'tab-research') {

        loadResearchSummary();

        loadResearchHealth();

        loadResearchWorker();

        loadResearchDiag();

    }

    if (tabId === 'tab-factory') {

        loadFactoryStatus();

    }

    if (tabId === 'tab-news') {
        loadNewsState();
        if (window.NewsIntel && NewsIntel.startProConsole) { try { NewsIntel.startProConsole(); } catch(_){} }
        // Timeline needs a visible canvas — defer one frame so the tab is laid out
        setTimeout(()=>{ try{ setNewsTimeframe(__newsTfSec); }catch(_){ try{ loadNewsTimeline(); }catch(__){} } }, 80);
    }

    if (tabId === 'tab-command-center') {
        // The Command Center lives in an isolated iframe (clean namespace,
        // avoids double-loading the CC scripts into the dashboard bundle).
        // It boots on its own DOMContentLoaded, but its canvas is sized 0 while
        // the tab is hidden, so once the tab is visible we post a message telling
        // the CC to re-init at the correct size and AUTO FIT ALL.
        const frame = document.getElementById('scc-iframe');
        if (frame && frame.contentWindow) {
            setTimeout(() => {
                try {
                    frame.contentWindow.postMessage({ type: 'NX_SCC_SHOW' }, '*');
                } catch (_) { /* cross-origin guard — same origin here */ }
            }, 60);
        }
    }

    if (tabId === 'tab-liquidity') {

        loadLiquidityState();

    }

    if (tabId === 'tab-incidents') {

        loadIncidents();

    }

    if (tabId === 'tab-debug') {

        startDebugHub();

    } else {

        stopDebugHub();

    }

}



// =============================================================================

// DEBUG & DIAGNOSTICS HUB

// =============================================================================



let debugRefreshTimer = null;

let debugFeatureCache = [];

let debugIpcSeenIds = new Set();

let debugIpcCleared = false;



const DEBUG_STATUS_STYLES = {

    HEALTHY:      { text: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', icon: 'fa-circle-check',        dot: 'bg-emerald-400' },

    DEGRADED:     { text: 'text-accentGold',  bg: 'bg-amber-500/10',   border: 'border-amber-500/30',   icon: 'fa-triangle-exclamation', dot: 'bg-amber-400' },

    UNHEALTHY:    { text: 'text-rose-400',    bg: 'bg-rose-500/10',    border: 'border-rose-500/30',    icon: 'fa-circle-xmark',         dot: 'bg-rose-400' },

    DISCONNECTED: { text: 'text-slate-400',   bg: 'bg-slate-500/10',   border: 'border-slate-500/30',   icon: 'fa-plug-circle-xmark',    dot: 'bg-slate-400' },

    UNKNOWN:      { text: 'text-slate-400',   bg: 'bg-slate-500/10',   border: 'border-slate-500/30',   icon: 'fa-circle-question',      dot: 'bg-slate-400' }

};



function debugStatusStyle(status) {

    return DEBUG_STATUS_STYLES[status] || DEBUG_STATUS_STYLES.UNKNOWN;

}



// Wire up the input-mode selector and anomaly filter once the DOM is ready.

function initDebugHub() {

    const modeSelect = document.getElementById('debug-model-input-mode');

    const customBox = document.getElementById('debug-model-custom-vector');

    if (modeSelect && customBox) {

        modeSelect.addEventListener('change', () => {

            if (modeSelect.value === 'custom') {

                customBox.classList.remove('hidden');

                if (!customBox.value.trim()) {

                    customBox.value = JSON.stringify(new Array(50).fill(0));

                }

            } else {

                customBox.classList.add('hidden');

            }

        });

    }



    const anomalyToggle = document.getElementById('debug-feature-anomalies-only');

    if (anomalyToggle) {

        anomalyToggle.addEventListener('change', () => renderDebugFeatures(debugSnapshotCache ? debugSnapshotCache.features : []));

    }

    const featureFilter = document.getElementById('debug-feature-filter');

    if (featureFilter) {

        featureFilter.addEventListener('input', () => renderDebugFeatures(debugSnapshotCache ? debugSnapshotCache.features : []));

    }



    const autoToggle = document.getElementById('debug-autorefresh');

    if (autoToggle) {

        autoToggle.addEventListener('change', () => {

            if (currentTab === 'tab-debug') {

                startDebugHub();

            }

        });

    }

}



function startDebugHub() {

    refreshDebugHub();

    stopDebugHub();

    const autoToggle = document.getElementById('debug-autorefresh');

    if (autoToggle && autoToggle.checked) {

        debugRefreshTimer = setInterval(refreshDebugHub, 3000);

    }

}



function stopDebugHub() {

    if (debugRefreshTimer) {

        clearInterval(debugRefreshTimer);

        debugRefreshTimer = null;

    }

}



// Pull every diagnostics endpoint in parallel so one slow subsystem cannot stall the UI.

async function refreshDebugHub() {

    await Promise.all([

        loadDebugHealth(),

        loadDebugIpcTelemetry(),

        loadDebugSnapshot()

    ]);

}



async function loadDebugHealth() {

    try {

        const res = await fetch('/api/debug/health');

        const data = await res.json();



        const overallEl = document.getElementById('debug-overall-status');

        const navBadge = document.getElementById('debug-nav-badge');

        const style = debugStatusStyle(data.overall_status);



        if (overallEl) {

            overallEl.textContent = data.overall_status;

            overallEl.className = `text-sm font-black font-mono ${style.text}`;

        }

        if (navBadge) {

            navBadge.textContent = (data.overall_status || 'NA').substring(0, 3);

            navBadge.className = `ml-auto text-[9px] font-black px-1.5 py-0.5 rounded ${style.bg} ${style.text} border ${style.border}`;

        }



        const grid = document.getElementById('debug-health-grid');

        if (!grid) return;



        const subsystems = data.subsystems || [];

        if (subsystems.length === 0) {

            grid.innerHTML = `<div class="bg-panelBg border border-borderClr rounded-xl p-4 text-xs text-textMuted italic">No subsystem data returned.</div>`;

            return;

        }



        grid.innerHTML = subsystems.map(sub => {

            const st = debugStatusStyle(sub.status);

            const metricRows = Object.keys(sub.metrics || {}).map(key => `

                <div class="flex justify-between text-[10px] font-mono">

                    <span class="text-textMuted truncate mr-2">${key}</span>

                    <span class="text-gray-300 truncate">${formatDebugMetric(sub.metrics[key])}</span>

                </div>

            `).join('');



            return `

                <div class="bg-panelBg border ${st.border} rounded-xl p-4 flex flex-col space-y-3 shadow-md hover:shadow-lg transition-all duration-300">

                    <div class="flex items-start justify-between">

                        <span class="text-[11px] font-black text-white leading-tight pr-2">${sub.name}</span>

                        <span class="w-2 h-2 rounded-full ${st.dot} mt-1 shrink-0 ${sub.status === 'HEALTHY' ? 'animate-pulse' : ''}"></span>

                    </div>

                    <div class="flex items-center space-x-1.5">

                        <i class="fa-solid ${st.icon} ${st.text} text-xs"></i>

                        <span class="text-[10px] font-black font-mono ${st.text}">${sub.status}</span>

                    </div>

                    <p class="text-[10px] text-textMuted leading-snug">${sub.detail || ''}</p>

                    ${metricRows ? `<div class="pt-2 border-t border-borderClr/60 space-y-1">${metricRows}</div>` : ''}

                </div>

            `;

        }).join('');

    } catch (err) {

        console.error("Failed to load debug health", err);

    }

}



function formatDebugMetric(value) {

    if (value === null || value === undefined) return '--';

    if (typeof value === 'boolean') return value ? 'true' : 'false';

    if (typeof value === 'number') {

        return Number.isInteger(value) ? String(value) : value.toFixed(2);

    }

    const str = String(value);

    return str.length > 26 ? '...' + str.slice(-23) : str;

}



async function loadDebugFeatures() {

    try {

        const res = await fetch('/api/debug/features');

        const data = await res.json();



        debugFeatureCache = data.features || [];



        const validCount = debugFeatureCache.length - (data.anomaly_count || 0);

        const validEl = document.getElementById('debug-feature-valid-count');

        const anomalyEl = document.getElementById('debug-feature-anomaly-count');

        const staleEl = document.getElementById('debug-feature-stale');



        if (validEl) validEl.textContent = `VALID ${validCount}/${debugFeatureCache.length}`;

        if (anomalyEl) {

            anomalyEl.textContent = `ANOMALY ${data.anomaly_count || 0}`;

            anomalyEl.className = (data.anomaly_count || 0) === 0

                ? 'px-2 py-1 rounded bg-slate-500/10 text-slate-400 border border-slate-500/30 font-mono'

                : 'px-2 py-1 rounded bg-rose-500/10 text-rose-400 border border-rose-500/30 font-mono';

        }

        if (staleEl) {

            const age = data.age_seconds;

            staleEl.textContent = age === null || age === undefined ? 'AGE --' : `AGE ${age.toFixed(1)}s`;

            staleEl.className = data.is_stale

                ? 'px-2 py-1 rounded bg-amber-500/10 text-accentGold border border-amber-500/30 font-mono'

                : 'px-2 py-1 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 font-mono';

        }



        renderDebugFeatures(debugFeatureCache);

    } catch (err) {

        console.error("Failed to load debug features", err);

    }

}



function renderDebugFeatures(features) {

    const tbody = document.getElementById('debug-features-table');

    if (!tbody) return;



    const anomaliesOnly = document.getElementById('debug-feature-anomalies-only');

    let list = features || [];

    if (anomaliesOnly && anomaliesOnly.checked) {

        list = list.filter(f => !f.is_valid);

    }



    if (list.length === 0) {

        tbody.innerHTML = `

            <tr>

                <td colspan="4" class="py-6 text-center text-textMuted italic font-sans">

                    ${(anomaliesOnly && anomaliesOnly.checked) ? 'No anomalies detected — all 50 features are valid.' : 'Awaiting feature stream...'}

                </td>

            </tr>

        `;

        return;

    }



    tbody.innerHTML = list.map(feat => {

        const isValid = feat.is_valid;

        const tagClass = isValid

            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'

            : 'bg-rose-500/10 text-rose-400 border-rose-500/30';

        const valClass = isValid

            ? (feat.value >= 1.0 ? 'text-emerald-400' : (feat.value <= -1.0 ? 'text-rose-400' : 'text-gray-200'))

            : 'text-rose-400';

        return `

            <tr class="hover:bg-darkBg/40 transition ${isValid ? '' : 'bg-rose-500/5'}">

                <td class="py-1.5 px-3 text-textMuted">${feat.key}</td>

                <td class="py-1.5 px-3 text-gray-300">${feat.name}</td>

                <td class="py-1.5 px-3 text-right font-black ${valClass}">${Number(feat.value).toFixed(6)}</td>

                <td class="py-1.5 px-3 text-right pr-4">

                    <span class="px-1.5 py-0.5 rounded text-[9px] font-extrabold border ${tagClass}">${feat.status}</span>

                </td>

            </tr>

        `;

    }).join('');

}



// Fire a real PyTorch forward pass and render the resulting probability bars.

async function runModelDiagnosticsTest() {

    const btn = document.getElementById('debug-run-model-btn');

    const mode = document.getElementById('debug-model-input-mode');

    const customBox = document.getElementById('debug-model-custom-vector');



    let payload = { use_live_features: true };



    if (mode) {

        if (mode.value === 'zeros') {

            payload = { features: new Array(50).fill(0), use_live_features: false };

        } else if (mode.value === 'custom') {

            try {

                const parsed = JSON.parse(customBox.value);

                if (!Array.isArray(parsed) || parsed.length !== 50) {

                    alert("Custom vector must be a JSON array of exactly 50 numbers.");

                    return;

                }

                payload = { features: parsed.map(Number), use_live_features: false };

            } catch (e) {

                alert("Custom vector is not valid JSON: " + e.message);

                return;

            }

        }

    }



    if (btn) {

        btn.disabled = true;

        btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> <span>Running Inference...</span>`;

    }



    try {

        const res = await fetch('/api/debug/model-test', {

            method: 'POST',

            headers: { 'Content-Type': 'application/json' },

            body: JSON.stringify(payload)

        });



        if (!res.ok) {

            const errBody = await res.json().catch(() => ({ detail: res.statusText }));

            alert("Model test failed: " + (errBody.detail || res.statusText));

            return;

        }



        const data = await res.json();

        applyModelTestResult(data);

    } catch (err) {

        console.error("Model diagnostics test failed", err);

        alert("Model diagnostics test failed: " + err.message);

    } finally {

        if (btn) {

            btn.disabled = false;

            btn.innerHTML = `<i class="fa-solid fa-flask-vial"></i> <span>Run Model Diagnostics Test</span>`;

        }

    }

}



function applyModelTestResult(data) {

    const setBar = (valueId, barId, prob) => {

        const pct = (Number(prob) * 100);

        const valEl = document.getElementById(valueId);

        const barEl = document.getElementById(barId);

        if (valEl) valEl.textContent = `${pct.toFixed(2)}%`;

        if (barEl) barEl.style.width = `${Math.max(0, Math.min(100, pct))}%`;

    };



    setBar('debug-prob-buy', 'debug-prob-buy-bar', data.ai_buy);

    setBar('debug-prob-sell', 'debug-prob-sell-bar', data.ai_sell);

    setBar('debug-prob-no-trade', 'debug-prob-no-trade-bar', data.ai_no_trade);



    const predEl = document.getElementById('debug-model-prediction');

    if (predEl) {

        predEl.textContent = data.predicted_label || '--';

        predEl.className = 'text-[11px] font-mono font-black ' + (

            data.predicted_label === 'BUY_MARKET' ? 'text-emerald-400'

            : data.predicted_label === 'SELL_MARKET' ? 'text-rose-400'

            : 'text-white'

        );

    }



    const latEl = document.getElementById('debug-model-latency');

    if (latEl) latEl.textContent = `${Number(data.latency_ms || 0).toFixed(2)}ms`;



    const srcEl = document.getElementById('debug-model-source');

    if (srcEl) srcEl.textContent = data.model_source === 'LIVE_BUNDLE' ? 'LIVE' : 'FRESH';



    appendIpcLine(

        `MODEL_TEST ${data.predicted_label} conf=${(Number(data.confidence) * 100).toFixed(1)}% ` +

        `buy=${(Number(data.ai_buy) * 100).toFixed(1)}% sell=${(Number(data.ai_sell) * 100).toFixed(1)}% ` +

        `nt=${(Number(data.ai_no_trade) * 100).toFixed(1)}% [${data.latency_ms}ms / ${data.model_source}]`,

        'text-accentCyan'

    );

}



async function loadDebugIpcTelemetry() {

    try {

        const res = await fetch('/api/debug/ipc-telemetry?limit=60');

        const data = await res.json();



        const latEl = document.getElementById('debug-ipc-latency');

        if (latEl) latEl.textContent = `AVG ${Number(data.avg_latency_ms || 0).toFixed(2)} ms`;



        const expEl = document.getElementById('debug-ipc-exposure');

        if (expEl && data.exposure) {

            const total = (data.exposure.positions || 0) + (data.exposure.pendings || 0);

            expEl.textContent = `EXPOSURE ${total}/${data.max_total_exposure} (P${data.exposure.positions || 0}/L${data.exposure.pendings || 0})`;

            expEl.className = total > (data.max_total_exposure || 1)

                ? 'px-2 py-1 rounded bg-rose-500/10 text-rose-400 border border-rose-500/30 font-mono'

                : 'px-2 py-1 rounded bg-accentCyan/10 text-accentCyan border border-accentCyan/30 font-mono';

        }



        // The endpoint returns newest-first; replay oldest-first so the console reads

        // chronologically as lines are appended.

        const events = (data.events || []).slice().reverse();

        events.forEach(ev => {

            if (debugIpcSeenIds.has(ev.id)) return;

            debugIpcSeenIds.add(ev.id);



            const reason = String(ev.reason || '');

            let color = 'text-gray-300';

            if (/REJECT|FAIL|BLOCK|ABORT/i.test(reason) || /REJECTED/i.test(ev.action || '')) {

                color = 'text-rose-400';

            } else if (/AI_REVERSAL/i.test(reason)) {

                color = 'text-accentGold';

            } else if (/cancel|expire/i.test(reason)) {

                color = 'text-amber-400';

            } else if (/Executed/i.test(ev.action || '')) {

                color = 'text-emerald-400';

            }



            const latencyMs = ev.latency !== null && ev.latency !== undefined

                ? (Number(ev.latency) * 1000).toFixed(1) + 'ms'

                : '--';



            appendIpcLine(

                `#${ev.ticket || 0} ${String(ev.action || 'EVENT').toUpperCase()} ` +

                `${ev.symbol || ''} vol=${ev.volume ?? 0} px=${ev.price ?? 0} ` +

                `sl=${ev.stop_loss ?? 0} tp=${ev.take_profit ?? 0} ` +

                `mode=${ev.execution_mode || 'STANDARD'} ipc=${latencyMs} :: ${reason}`,

                color,

                ev.timestamp

            );

        });

    } catch (err) {

        console.error("Failed to load IPC telemetry", err);

    }

}



function appendIpcLine(text, colorClass, timestamp) {

    const consoleEl = document.getElementById('debug-ipc-console');

    if (!consoleEl) return;



    if (!debugIpcCleared) {

        // Drop the placeholder on the first real line.

        const placeholder = consoleEl.querySelector('.font-sans');

        if (placeholder) placeholder.remove();

        debugIpcCleared = true;

    }



    const ts = timestamp ? String(timestamp).substring(11, 19) : new Date().toISOString().substring(11, 19);

    const line = document.createElement('div');

    line.className = `${colorClass || 'text-gray-300'} whitespace-pre-wrap break-all`;

    line.textContent = `[${ts}] ${text}`;

    consoleEl.appendChild(line);



    // Cap the console buffer so long sessions cannot grow the DOM without bound.

    while (consoleEl.childElementCount > 400) {

        consoleEl.removeChild(consoleEl.firstElementChild);

    }

    consoleEl.scrollTop = consoleEl.scrollHeight;

}



function clearIpcConsole() {

    const consoleEl = document.getElementById('debug-ipc-console');

    if (consoleEl) {

        consoleEl.innerHTML = '<div class="text-textMuted italic font-sans">Console cleared.</div>';

        debugIpcCleared = false;

    }

}




// =============================================================================
// DEBUG 70D FORENSIC CONSOLE — snapshot rendering (backend truth only)
// =============================================================================

let debugSnapshotCache = null;      // latest full /api/debug/state payload
let debugSnapshotHistory = [];      // snapshot ids from /api/debug/snapshots

function debugFmt(v, digits = 4) {
    if (v === null || v === undefined) return '--';
    if (typeof v === 'number') {
        if (!isFinite(v)) return String(v);
        return Number.isInteger(v) ? String(v) : v.toFixed(digits);
    }
    return String(v);
}

function debugTone(status) {
    // status -> {text, bg, border} for consistent visual rules
    const s = String(status || '').toUpperCase();
    if (s.includes('OK') || s === 'PASS' || s === 'VALID' || s === 'READY' || s === 'RUNNING' || s === 'LIVE' || s === 'CONNECTED' || s === 'ENABLED' || s === 'HEALTHY') {
        return { text: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', dot: 'bg-emerald-400', icon: 'fa-circle-check' };
    }
    if (s.includes('BROKEN') || s.includes('INVALID') || s === 'FAIL' || s === 'BLOCK' || s === 'BLOCKED' || s === 'ERROR' || s === 'REJECT') {
        return { text: 'text-rose-400', bg: 'bg-rose-500/10', border: 'border-rose-500/30', dot: 'bg-rose-400', icon: 'fa-circle-xmark' };
    }
    if (s.includes('DEGRADED') || s === 'WARN' || s === 'STALE' || s === 'WARNING') {
        return { text: 'text-accentGold', bg: 'bg-amber-500/10', border: 'border-amber-500/30', dot: 'bg-amber-400', icon: 'fa-triangle-exclamation' };
    }
    if (s.includes('UNAVAILABLE') || s === 'UNKNOWN' || s === 'DISABLED' || s === 'DISCONNECTED' || s === 'STOPPED' || s === 'EMPTY') {
        return { text: 'text-slate-400', bg: 'bg-slate-500/10', border: 'border-slate-500/30', dot: 'bg-slate-400', icon: 'fa-circle-question' };
    }
    return { text: 'text-slate-300', bg: 'bg-slate-500/15', border: 'border-slate-500/30', dot: 'bg-slate-400', icon: 'fa-circle-info' };
}

function debugStatusChip(label, status, extraClass) {
    const t = debugTone(status);
    return `<span class="px-1.5 py-0.5 rounded text-[9px] font-extrabold border ${t.bg} ${t.text} ${t.border} ${extraClass || ''}">${label}: ${String(status)}</span>`;
}

function debugKV(label, value, tone) {
    const t = debugTone(tone || 'INFO');
    return `<div class="flex justify-between gap-2 py-0.5"><span class="text-textMuted">${label}</span><span class="${t.text} font-bold">${debugFmt(value)}</span></div>`;
}

// ---------------------------------------------------------------------------
// MAIN SNAPSHOT LOADER — consumes /api/debug/state (canonical endpoint)
// ---------------------------------------------------------------------------
async function loadDebugSnapshot() {
    try {
        const res = await fetch('/api/debug/state');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        debugSnapshotCache = data;
        renderDebugSnapshot(data);
        loadDebugSnapshotHistory(true);
    } catch (err) {
        console.error('Failed to load debug snapshot', err);
    }
}

function renderDebugSnapshot(d) {
    // correlation / snapshot identity (brief 28)
    const corrEl = document.getElementById('debug-correlation-id');
    if (corrEl) corrEl.textContent = d.correlation_id || '--';
    const snapEl = document.getElementById('debug-snapshot-id');
    if (snapEl) snapEl.textContent = (d.snapshot_id || '--').substring(0, 22);

    renderDebugRuntime(d.runtime);
    renderDebugContractBanner(d.contract);
    renderDebugFeatures(d.features);
    renderDebugModel(d.model);
    renderDebugConfidence(d.confidence);
    renderDebugPolicy(d.policy);
    renderDebugRisk(d.risk);
    renderDebugExposure(d.exposure);
    renderDebugExecution(d.execution);
    renderDebugPositions(d.positions);
    renderDebugExit(d.exit);
    renderDebugLiquidity(d.liquidity);
    renderDebugMslie(d.mslie);
    renderDebugNews(d.news);
    renderDebugWorkers(d.workers);
    renderDebugDatabase(d.database);
    renderDebugCaches(d.caches);
    renderDebugChart(d.chart, d.sse);
    renderDebugErrors(d.errors);
    debugSnapshotForTree = d;
    renderDebugJsonTree(d, false);
}


function renderDebugRuntime(rt) {
    const map = {
        'dbg-mode': rt && rt.mode, 'dbg-symbol': rt && rt.symbol,
        'dbg-timeframe': rt && rt.timeframe, 'dbg-runtime': rt && rt.runtime,
        'dbg-inference': rt && rt.inference, 'dbg-warmup': rt && rt.warmup,
        'dbg-model-id': rt && rt.model_id, 'dbg-model-version': rt && rt.model_version,
        'dbg-schema': rt && rt.schema_id, 'dbg-dimension': rt && rt.dimension,
        'dbg-schema-hash': rt && rt.schema_hash, 'dbg-algo-version': rt && rt.algorithm_version,
        'dbg-feature-update': rt && rt.last_feature_update, 'dbg-feature-latency': rt && rt.feature_latency_ms ? (rt.feature_latency_ms).toFixed(2) + ' ms' : '--'
    };
    for (const [id, val] of Object.entries(map)) {
        const el = document.getElementById(id);
        if (el) el.textContent = val === null || val === undefined ? '--' : String(val);
    }
    // runtime / inference colored
    const rtEl = document.getElementById('dbg-runtime');
    if (rtEl && rt) { const t = debugTone(rt.runtime); rtEl.className = 'text-sm font-black font-mono ' + t.text; }
    const infEl = document.getElementById('dbg-inference');
    if (infEl && rt) { const t = debugTone(rt.inference); infEl.className = 'text-sm font-black font-mono ' + t.text; }
    const dimEl = document.getElementById('dbg-dimension');
    if (dimEl && rt && rt.dimension) {
        const t = (rt.dimension === 70) ? debugTone('PASS') : debugTone('WARN');
        dimEl.className = 'text-sm font-black font-mono ' + t.text;
    }
    // subsystem flags
    const flagsEl = document.getElementById('debug-subsystem-flags');
    if (flagsEl && rt && rt.subsystems) {
        const labels = {
            broker_connected: 'Broker', tick_stream: 'Tick Stream', bar_stream: 'Bar Stream',
            news: 'News', liquidity: 'Liquidity', shadow: 'Shadow', shadow70: 'Shadow70',
            research: 'Research', training: 'Training', accounting: 'Accounting', telegram: 'Telegram'
        };
        flagsEl.innerHTML = Object.entries(labels).map(([k, label]) => {
            const v = rt.subsystems[k];
            const tone = debugTone(v === true ? 'ENABLED' : String(v));
            return `<div class="bg-darkBg/40 border ${tone.border} rounded-lg px-2 py-1 text-center"><div class="text-[8px] uppercase tracking-widest text-textMuted">${label}</div><div class="text-[10px] font-black font-mono ${tone.text}">${v === true ? 'ON' : v === false ? 'OFF' : String(v)}</div></div>`;
        }).join('');
    }
}

function renderDebugContractBanner(ct) {
    const banner = document.getElementById('debug-contract-banner');
    if (!banner) return;
    if (!ct || !ct.status) { banner.classList.add('hidden'); return; }
    const broken = String(ct.status).includes('BROKEN') || String(ct.model_status || '').includes('INVALID');
    const tone = broken ? debugTone('BROKEN') : debugTone('PASS');
    const dimOk = ct.dimension_match ? '' : ` | EXPECTED DIM ${ct.expected_dimension} vs ACTUAL ${debugFmt(ct.actual_dimension)}`;
    const clsOk = ct.classes_match ? '' : ` | EXPECTED CLASSES ${ct.expected_classes} vs ACTUAL ${debugFmt(ct.actual_classes)}`;
    const vecOk = ct.vector_match ? '' : ` | LIVE VECTOR ${debugFmt(ct.live_vector_len)} != EXPECTED ${debugFmt(ct.expected_dimension)}`;
    banner.className = `rounded-xl border ${tone.border} ${tone.bg} px-4 py-3 text-xs font-mono ${tone.text}`;
    banner.innerHTML = `<i class="fa-solid ${broken ? 'fa-triangle-exclamation' : 'fa-circle-check'} mr-2"></i>
        <b>${ct.status}</b> — schema ${debugFmt(ct.actual_schema_id)} dim ${debugFmt(ct.actual_dimension)}${dimOk}${clsOk}${vecOk}
        <span class="opacity-60">| model: ${ct.model_status}</span>`;
    banner.classList.remove('hidden');
}

function renderDebugFeatures(feats) {
    const tbody = document.getElementById('debug-features-table');
    if (!tbody) return;
    const rows = (feats && feats.rows) || [];
    const health = (feats && feats.health) || {};
    const badges = {
        'TOTAL': health.total, 'VALID': health.valid, 'INVALID': health.invalid,
        'FALLBACK': health.fallback, 'UNAVAIL': health.unavailable, 'STALE': health.stale
    };
    const badgeEls = document.querySelectorAll('#debug-feature-health-badges span');
    badgeEls.forEach((el, i) => {
        const label = el.textContent.split(' ')[0];
        el.textContent = `${label} ${badges[label] ?? '--'}`;
    });
    const staleEl = document.getElementById('debug-feature-stale');
    const age = feats && feats.timestamp ? ((Date.now() - new Date(feats.timestamp).getTime()) / 1000).toFixed(1) : null;
    if (staleEl) staleEl.textContent = age === null ? 'AGE --' : `AGE ${age}s`;

    const issuesOnly = document.getElementById('debug-feature-anomalies-only');
    const filterEl = document.getElementById('debug-feature-filter');
    let list = rows.slice();
    if (issuesOnly && issuesOnly.checked) list = list.filter(r => String(r.status) !== 'VALID');
    if (filterEl && filterEl.value.trim()) {
        const q = filterEl.value.trim().toLowerCase();
        list = list.filter(r => r.name.toLowerCase().includes(q) || r.family.toLowerCase().includes(q) || String(r.index).includes(q));
    }
    if (!list.length) {
        tbody.innerHTML = `<tr><td colspan="10" class="py-6 text-center text-textMuted italic font-sans">No features match the current filter.</td></tr>`;
        return;
    }
    tbody.innerHTML = list.map(f => {
        const tone = debugTone(f.status);
        const cls = tone.text;
        const familyColor = f.family === 'base' ? 'text-gray-300' : f.family === 'news' ? 'text-accentGold' : 'text-accentCyan';
        const val = typeof f.final === 'number' ? f.final.toFixed(6) : f.final;
        return `<tr class="hover:bg-darkBg/40 transition cursor-pointer" onclick="showDebugFeatureDetail(${f.index})">
            <td class="py-1.5 px-3 text-textMuted">${f.index}</td>
            <td class="py-1.5 px-3 text-gray-200">${f.name}</td>
            <td class="py-1.5 px-3 ${familyColor}">${f.family}</td>
            <td class="py-1.5 px-3 text-right font-mono">${debugFmt(f.raw)}</td>
            <td class="py-1.5 px-3 text-right font-mono">${debugFmt(f.normalized)}</td>
            <td class="py-1.5 px-3 text-right font-mono">${debugFmt(f.clipped)}</td>
            <td class="py-1.5 px-3 text-right font-black ${cls}">${val}</td>
            <td class="py-1.5 px-3"><span class="px-1.5 py-0.5 rounded text-[9px] font-extrabold border ${tone.bg} ${tone.text} ${tone.border}">${f.status}</span></td>
            <td class="py-1.5 px-3 text-textMuted">${debugFmt(f.source)}</td>
            <td class="py-1.5 px-3 text-textMuted">${debugFmt(f.causality)}</td>
        </tr>`;
    }).join('');
}

function showDebugFeatureDetail(index) {
    const d = debugSnapshotCache;
    if (!d || !d.features || !d.features.rows) return;
    const f = d.features.rows.find(r => r.index === index);
    if (!f) return;
    const panel = document.getElementById('debug-feature-detail');
    const liq = d.liquidity && d.liquidity.report && d.liquidity.report.features;
    const poolInfo = (f.family === 'liquidity' && liq && liq[f.name]) ? liq[f.name] : null;
    panel.classList.remove('hidden');
    let extra = '';
    if (f.family === 'liquidity') {
        extra = `<div class="mt-3 pt-2 border-t border-borderClr/60">
            <div class="text-[10px] uppercase tracking-widest text-textMuted mb-1">Liquidity Pool Context</div>
            ${poolInfo && typeof poolInfo === 'object'
                ? Object.entries(poolInfo).map(([k, v]) => `<div class="flex justify-between py-0.5"><span class="text-textMuted">${k}</span><span class="text-gray-300">${debugFmt(v)}</span></div>`).join('')
                : 'Pool detail only available in the Liquidity Intelligence section.'}
        </div>`;
    }
    panel.innerHTML = `<div class="flex justify-between items-center border-b border-borderClr pb-3 mb-3">
        <h3 class="text-md font-bold text-white"><i class="fa-solid fa-magnifying-glass mr-2 text-accentCyan"></i>FEATURE DETAIL — ${f.index} ${f.name}</h3>
        <button onclick="document.getElementById('debug-feature-detail').classList.add('hidden')" class="text-textMuted hover:text-white"><i class="fa-solid fa-xmark"></i></button>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-3 gap-3 text-xs font-mono">
        ${debugKV('Feature Index', f.index)}${debugKV('Feature Name', f.name)}${debugKV('Family', f.family)}
        ${debugKV('Current Value', f.final, f.status)}${debugKV('Raw Value', f.raw)}${debugKV('Normalization', f.normalized)}
        ${debugKV('Clip Range', f.clipped)}${debugKV('Status', f.status, f.status)}${debugKV('Source', f.source)}
        ${debugKV('Source Timestamp', f.timestamp)}${debugKV('Causality', f.causality)}
    </div>${extra}`;
}


function renderDebugModel(m) {
    const body = document.getElementById('debug-model-body');
    if (!body) return;
    if (!m || !m.available) {
        body.innerHTML = `<div class="text-textMuted italic font-sans">${(m && m.reason) || 'Model state unavailable.'}</div>`;
        return;
    }
    const tone = debugTone(m.status);
    const probs = m.probabilities || {};
    const input = m.input_tensor || [];
    const inputPreview = input.length ? '[' + input.slice(0, 12).map(v => Number(v).toFixed(4)).join(', ') + (input.length > 12 ? ', ...' : '') + ']' : '--';
    let html = `
        <div class="flex flex-wrap gap-2 mb-2">
            ${debugStatusChip('MODEL OUTPUT', m.status)}
            ${m.num_classes ? debugStatusChip('CLASSES', m.num_classes, m.num_classes === 4 ? '' : 'bg-rose-500/20 text-rose-400') : ''}
            ${m.schema_id ? debugStatusChip('SCHEMA', m.schema_id) : ''}
        </div>
        ${debugKV('Model ID', m.model_id)}${debugKV('Model Version', m.model_version)}
        ${debugKV('Schema ID', m.schema_id)}${debugKV('Dimension', m.dimension)}
        ${debugKV('Schema Hash', m.schema_hash)}${debugKV('Scaler Hash', m.scaler_hash)}
        ${debugKV('Scaler Ready', m.scaler_ready)}${debugKV('Input Shape', m.input_tensor_shape ? JSON.stringify(m.input_tensor_shape) : '--')}
        ${debugKV('Input Dtype', m.input_dtype)}${debugKV('Device', m.device)}
        ${debugKV('Inference Latency', m.inference_latency_ms !== null && m.inference_latency_ms !== undefined ? Number(m.inference_latency_ms).toFixed(2) + ' ms' : '--')}
        <div class="mt-2 pt-2 border-t border-borderClr/60">
            <div class="text-[10px] uppercase tracking-widest text-textMuted mb-1">Feature Tensor (post-scaler, exact live input)</div>
            <div class="text-[10px] text-gray-400 break-all">${inputPreview}</div>
            <div class="text-[9px] text-textMuted mt-1">Full ${input.length} values expandable in the JSON tree (DEBUG SNAPSHOT JSON).</div>
        </div>
        <div class="mt-2 pt-2 border-t border-borderClr/60">
            <div class="text-[10px] uppercase tracking-widest text-textMuted mb-1">Probabilities</div>
            ${debugKV('NO_TRADE', probs.NO_TRADE, 'INFO')}${debugKV('BUY_MARKET', probs.BUY_MARKET, 'INFO')}${debugKV('SELL_MARKET', probs.SELL_MARKET, 'INFO')}${debugKV('WAIT', probs.WAIT, 'INFO')}
            ${debugKV('Predicted Class', m.predicted_class === null || m.predicted_class === undefined ? '--' : ['NO_TRADE','BUY_MARKET','SELL_MARKET','WAIT'][m.predicted_class], 'INFO')}
            ${debugKV('Confidence (max prob)', m.confidence)}
        </div>
    `;
    body.innerHTML = html;
}

function renderDebugConfidence(c) {
    const body = document.getElementById('debug-confidence-body');
    if (!body) return;
    if (!c || !c.available) { body.innerHTML = `<div class="text-textMuted italic font-sans">${(c && c.reason) || 'No confidence state.'}</div>`; return; }
    const decision = c.decision;
    const decTone = decision === 'PASS' ? 'PASS' : (decision === 'REJECT' ? 'FAIL' : 'INFO');
    let html = `<div class="flex flex-wrap gap-2 mb-2">${debugStatusChip('DECISION', decision || 'N/A', decTone)}</div>`;
    html += c.stages.map(s => {
        const val = s.value;
        const shown = val === null || val === undefined ? '--' : (typeof val === 'number' ? val.toFixed(4) : debugFmt(val));
        const note = s.name === 'FINAL' ? ' (proposal)' : (s.name === 'RAW_MODEL' ? ' (max prob)' : '');
        return `<div class="flex justify-between py-0.5"><span class="text-textMuted">${s.name}${note}</span><span class="text-gray-300 font-bold">${shown}</span></div>`;
    }).join('');
    html += `
        ${debugKV('Required Threshold', c.required_threshold)}
        ${debugKV('Final Action', c.final_action)}
        <div class="mt-2 pt-2 border-t border-borderClr/60 text-[10px] text-textMuted">
            Calibration is reported only when the runtime applies one. Stages not present are never fabricated.
        </div>`;
    body.innerHTML = html;
}

function renderDebugPolicy(p) {
    const body = document.getElementById('debug-policy-body');
    if (!body) return;
    if (!p || !p.available || !p.gates) { body.innerHTML = `<div class="text-textMuted italic font-sans">No policy trace.</div>`; return; }
    const tone = debugTone(p.decision);
    let html = `<div class="flex flex-wrap gap-2 mb-2">
        ${debugStatusChip('DECISION', p.decision || 'N/A', p.decision)}
        ${debugStatusChip('STAGE', p.decision_stage || '-')}
        ${p.blocked_by ? debugStatusChip('BLOCKED BY', p.blocked_by, 'FAIL') : ''}
    </div>`;
    html += p.gates.map(g => {
        const t = debugTone(g.status);
        return `<div class="flex items-center justify-between py-1 border-b border-borderClr/20">
            <span class="text-textMuted font-bold w-24">${g.name}</span>
            <span class="text-gray-300 w-40 truncate">${debugFmt(g.actual)} / ${debugFmt(g.threshold)}</span>
            <span class="px-1.5 py-0.5 rounded text-[9px] font-extrabold border ${t.bg} ${t.text} ${t.border}">${g.status}</span>
        </div>
        <div class="text-[9px] text-textMuted pb-1">${(g.reason || '').substring(0, 160)}</div>`;
    }).join('');
    body.innerHTML = html;
}

function renderDebugRisk(r) {
    const body = document.getElementById('debug-risk-body');
    if (!body) return;
    if (!r || !r.available) { body.innerHTML = `<div class="text-textMuted italic font-sans">Risk engine unavailable.</div>`; return; }
    const acc = r.account || {};
    let html = `<div class="flex flex-wrap gap-2 mb-2">
        ${debugStatusChip('DECISION', r.decision, r.decision)}
        ${r.kill_switch_active ? debugStatusChip('KILL SWITCH', 'ACTIVE', 'FAIL') : ''}
        ${r.survival_mode ? debugStatusChip('SURVIVAL MODE', 'ACTIVE', 'WARN') : ''}
    </div>`;
    html += `
        ${debugKV('Balance', acc.balance)}${debugKV('Equity', acc.equity)}${debugKV('Free Margin', acc.margin_free)}
        ${debugKV('Margin', acc.margin)}${debugKV('Margin Level', acc.margin_level)}${debugKV('Drawdown %', acc.drawdown_pct)}
        ${debugKV('Risk %', r.risk_per_trade_pct)}${debugKV('Max Lots', r.max_allowed_lots)}${debugKV('Hard Max Lots', r.hard_max_lots)}
        ${debugKV('Max Positions', r.max_concurrent_positions)}${debugKV('Max Spread', r.max_spread_points)}
        ${debugKV('Min R:R', r.min_risk_reward_ratio)}${debugKV('Max DD %', r.max_account_drawdown_pct)}
        <div class="mt-2 pt-2 border-t border-borderClr/60">${debugKV('Reason', r.reason)}</div>`;
    body.innerHTML = html;
}


function renderDebugExposure(x) {
    const body = document.getElementById('debug-exposure-body');
    if (!body) return;
    if (!x || !x.available) { body.innerHTML = `<div class="text-textMuted italic font-sans">Exposure unavailable.</div>`; return; }
    const int = x.internal || {}, brk = x.broker || {};
    let html = `<div class="grid grid-cols-2 gap-3">
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2">
            <div class="text-[10px] uppercase tracking-widest text-textMuted mb-1">INTERNAL STATE</div>
            ${debugKV('Positions', int.positions)}${debugKV('Pending Orders', int.pendings)}${debugKV('Total', int.total)}
            ${debugKV('Max Total Exposure', int.max_total_exposure)}
        </div>
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2">
            <div class="text-[10px] uppercase tracking-widest text-textMuted mb-1">BROKER TRUTH</div>
            ${debugKV('Positions', brk.positions)}${debugKV('Pending Orders', brk.pendings)}
        </div>
    </div>`;
    const mismatch = x.mismatch;
    html += `<div class="mt-2">
        ${debugKV('Last Reconciliation', x.last_reconciliation)}
        ${debugKV('Reconciliation Age (s)', x.reconciliation_age_sec)}
        ${debugKV('Mismatch', mismatch === null ? '--' : (mismatch ? 'YES' : 'NO'), mismatch ? 'FAIL' : 'PASS')}
    </div>`;
    body.innerHTML = html;
}

function renderDebugExecution(e) {
    const body = document.getElementById('debug-execution-body');
    if (!body) return;
    if (!e || !e.available) { body.innerHTML = `<div class="text-textMuted italic font-sans">Execution unavailable.</div>`; return; }
    const conn = e.connection || {};
    let html = '';
    html += `${debugKV('Adapter', e.adapter)}${debugKV('Global State', e.global_state, e.global_state)}${debugKV('Consecutive Failures', e.consecutive_failures)}
        ${debugKV('Processed Orders', e.processed_orders_count)}`;
    if (conn) {
        html += `<div class="mt-2 pt-2 border-t border-borderClr/60"><div class="text-[10px] uppercase tracking-widest text-textMuted mb-1">Broker Connection</div>`;
        Object.entries(conn).forEach(([k, v]) => { if (typeof v !== 'object') html += debugKV(k, v); });
        html += `</div>`;
    }
    body.innerHTML = html;
}

function renderDebugPositions(ps) {
    const body = document.getElementById('debug-positions-body');
    if (!body) return;
    if (!ps || !ps.available) { body.innerHTML = `<div class="text-textMuted italic font-sans">${(ps && ps.reason) || 'No position state.'}</div>`; return; }
    if (!ps.positions || !ps.positions.length) { body.innerHTML = `<div class="text-textMuted italic font-sans">No open positions.</div>`; return; }
    let html = '';
    ps.positions.forEach(p => {
        const dir = p.direction;
        const dirTone = String(dir || '').toUpperCase().includes('SELL') ? 'FAIL' : 'PASS';
        html += `<div class="border border-borderClr/40 rounded-lg p-2 mb-2 bg-darkBg/30">
            <div class="flex flex-wrap gap-2 mb-1">
                ${debugStatusChip('#' + p.ticket, String(dir || '').toUpperCase(), dirTone)}
                ${p.pnl !== null && p.pnl !== undefined ? debugStatusChip('PnL', Number(p.pnl).toFixed(2), p.pnl >= 0 ? 'PASS' : 'FAIL') : ''}
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-1 text-[10px]">
                ${debugKV('Lots', p.lots)}${debugKV('Entry', p.entry)}${debugKV('Current', p.current)}
                ${debugKV('SL', p.sl)}${debugKV('TP', p.tp)}${debugKV('MFE', p.mfe)}
                ${debugKV('MAE', p.mae)}${debugKV('Peak PnL', p.peak_pnl)}${debugKV('Hold (s)', p.hold_seconds)}
                ${debugKV('Breakeven Armed', p.breakeven_armed)}${debugKV('Trailing Armed', p.trailing_armed)}
            </div>
        </div>`;
    });
    body.innerHTML = html;
}

function renderDebugExit(ex) {
    const body = document.getElementById('debug-exit-body');
    if (!body) return;
    if (!ex || !ex.available) { body.innerHTML = `<div class="text-textMuted italic font-sans">${(ex && ex.reason) || 'No exit forensics.'}</div>`; return; }
    if (!ex.positions || !ex.positions.length) { body.innerHTML = `<div class="text-textMuted italic font-sans">No open positions to exit-forensic.</div>`; return; }
    let html = '';
    ex.positions.forEach(p => {
        html += `<div class="border border-borderClr/40 rounded-lg p-2 mb-2 bg-darkBg/30">
            <div class="flex flex-wrap gap-2 mb-1">
                ${debugStatusChip('#' + p.ticket, String(p.direction || '').toUpperCase())}
                ${debugStatusChip('AI STATE', p.ai_state || 'IDLE')}
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-1 text-[10px]">
                ${debugKV('Regime', p.regime)}${debugKV('News State', p.news_state)}${debugKV('MFE', p.mfe)}
                ${debugKV('MAE', p.mae)}${debugKV('Hold (s)', p.hold_seconds)}${debugKV('Strategy State', p.strategy_state)}
            </div>
            ${(p.exit_candidates && p.exit_candidates.length) ? `<div class="mt-1 pt-1 border-t border-borderClr/40 text-[10px]">
                <div class="text-[10px] uppercase tracking-widest text-textMuted mb-0.5">Exit Candidates</div>
                ${p.exit_candidates.map(c => `<div class="flex justify-between py-0.5"><span class="text-gray-300">${c.reason}</span><span class="text-textMuted">${c.status}</span></div>`).join('')}
            </div>` : ''}
        </div>`;
    });
    body.innerHTML = html;
}


function renderDebugMslie(ms) {
    const body = document.getElementById('debug-mslie-body');
    if (!body) return;
    if (!ms || !ms.available) { body.innerHTML = `<div class="text-textMuted italic font-sans">${(ms && ms.reason) || 'Market Intelligence Engine unavailable.'}</div>`; return; }
    const es = ms.engine_status || {};
    const ctx = ms.market_context || {};
    const ctxRegime = (ctx.regime || {});
    const zones = ms.liquidity_map || [];
    const sweep = ms.last_sweep || null;
    const fv = ms.feature_vector || {};
    const fvRegime = (fv.regime || {});
    const sm = (fv.smart_money || {});
    const bq = (fv.breakout_quality || {});
    const memory = fv.memory || [];
    let html = `<div class="flex flex-wrap gap-2 mb-2">
        ${debugStatusChip('STRUCTURE ENGINE', es.market_structure_engine || 'STANDBY', es.market_structure_engine === 'ONLINE' ? 'PASS' : 'WARN')}
        ${debugStatusChip('LIQUIDITY ENGINE', es.liquidity_engine || 'STANDBY', es.liquidity_engine === 'ACTIVE' ? 'PASS' : 'WARN')}
        ${debugStatusChip('FEATURE GEN', es.feature_generator || 'IDLE', es.feature_generator === 'RUNNING' ? 'PASS' : 'WARN')}
        ${debugStatusChip('STATUS', ms.status || 'UNKNOWN', ms.status)}
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2 mb-2 text-[10px]">
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">Last Update</div><div class="text-accentCyan font-black">${debugFmt(es.last_update)}</div></div>
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">Latency</div><div class="text-accentGold font-black">${debugFmt(es.latency_ms)}ms</div></div>
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">Computes</div><div class="text-accentCyan font-black">${debugFmt(es.compute_count)}</div></div>
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">Symbol</div><div class="text-accentCyan font-black">${debugFmt(ctx.symbol)}</div></div>
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">Timeframe</div><div class="text-accentCyan font-black">${debugFmt(ctx.timeframe)}</div></div>
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">Regime</div><div class="text-accentCyan font-black">${debugFmt(ctxRegime.regime_label)}</div></div>
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">Bias</div><div class="${debugTone(ctx.bias).text} font-black">${debugFmt(ctx.bias)}</div></div>
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">Structure</div><div class="text-accentCyan font-black">${debugFmt(ctx.structure)}</div></div>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2 text-[10px]">
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">Trend Dir</div><div class="text-accentCyan font-black">${debugFmt(fvRegime.trend_direction)}</div></div>
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">Trend Strength</div><div class="text-accentCyan font-black">${debugFmt(fvRegime.trend_strength)}</div></div>
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">Volatility</div><div class="text-accentGold font-black">${debugFmt(fvRegime.volatility_state)}</div></div>
        <div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">Confidence</div><div class="text-accentCyan font-black">${debugFmt(ctx.confidence)}%</div></div>
    </div>`;
    // Liquidity map
    if (zones.length) {
        const bsl = zones.find(z => z.side === 'BUY_SIDE');
        const ssl = zones.find(z => z.side === 'SELL_SIDE');
        html += `<div class="grid grid-cols-1 md:grid-cols-2 gap-2 mb-2 text-[10px]">
            <div class="bg-darkBg/40 border border-emerald-500/30 rounded-lg p-2"><div class="text-emerald-400 font-black">BUY SIDE (BSL)</div><div class="text-accentCyan text-base font-black mt-1">${bsl ? debugFmt(bsl.price, 2) : '--'}</div><div class="text-textMuted">Strength ${bsl ? debugFmt(bsl.strength_score) : '--'} · Rank ${bsl ? debugFmt(bsl.rank) : '--'} · Tests ${bsl ? debugFmt(bsl.number_of_tests) : '--'}</div></div>
            <div class="bg-darkBg/40 border border-rose-500/30 rounded-lg p-2"><div class="text-rose-400 font-black">SELL SIDE (SSL)</div><div class="text-accentCyan text-base font-black mt-1">${ssl ? debugFmt(ssl.price, 2) : '--'}</div><div class="text-textMuted">Strength ${ssl ? debugFmt(ssl.strength_score) : '--'} · Rank ${ssl ? debugFmt(ssl.rank) : '--'} · Tests ${ssl ? debugFmt(ssl.number_of_tests) : '--'}</div></div>
        </div>`;
    }
    // Sweep detection
    if (sweep) {
        const tone = sweep.after_event_state === 'REVERSAL' ? 'PASS' : (sweep.after_event_state === 'CONTINUATION' ? 'WARN' : 'INFO');
        html += `<div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2 mb-2">
            <div class="text-[10px] uppercase tracking-widest text-textMuted">Last Sweep Event</div>
            <div class="flex flex-wrap gap-3 mt-1 items-center">
                <span class="text-amber-300 font-black">${debugFmt(sweep.direction)} LIQUIDITY SWEEP</span>
                ${debugStatusChip('CONFIDENCE', debugFmt(sweep.confidence) + '%', tone)}
                ${debugStatusChip('STATE', sweep.after_event_state, tone)}
                <span class="text-textMuted">Pool ${debugFmt(sweep.pool_price, 2)} · Type ${debugFmt(sweep.liquidity_type)} · Depth ${debugFmt(sweep.sweep_strength)}σ</span>
            </div>
        </div>`;
    }
    // Breakout quality
    if (bq.real_breakout_probability !== undefined) {
        html += `<div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2 mb-2">
            <div class="text-[10px] uppercase tracking-widest text-textMuted">Breakout Quality</div>
            <div class="flex flex-wrap gap-2 mt-1">
                ${debugStatusChip('REAL', debugFmt(bq.real_breakout_probability), bq.real_breakout_probability >= 0.6 ? 'PASS' : 'INFO')}
                ${debugStatusChip('FAKE/TRAP', debugFmt(bq.fake_breakout_probability), bq.fake_breakout_probability >= 0.6 ? 'WARN' : 'INFO')}
                <span class="text-textMuted">close ${debugFmt(bq.closing_strength)} · vol ${debugFmt(bq.volume_support)} · mom ${debugFmt(bq.momentum_support)} · retest ${debugFmt(bq.retest_confirmation)}</span>
            </div>
        </div>`;
    }
    // Smart money
    html += `<div class="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2 text-[10px]">
        ${debugKV('OB Type', sm.order_block_type)}${debugKV('OB Strength', sm.order_block_strength)}${debugKV('FVG Count', sm.fvg_count)}${debugKV('FVG σ', sm.fvg_strength)}
        ${debugKV('Displacement σ', sm.displacement_strength)}${debugKV('Inducement', sm.inducement_levels)}${debugKV('Premium/Discount', sm.premium_discount_position)}${debugKV('Last OB σ', sm.last_mitigated_order_block)}
    </div>`;
    // Memory
    if (memory.length) {
        html += `<div class="mt-2 pt-2 border-t border-borderClr/60">
            <div class="text-[10px] uppercase tracking-widest text-textMuted mb-1">Market Memory (${memory.length} institutional levels)</div>
            <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2 text-[10px]">
                ${memory.map(m => `<div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2">
                    <div class="flex justify-between"><span class="text-accentCyan font-black">${debugFmt(m.level, 2)}</span><span class="text-textMuted">${debugFmt(m.created)}</span></div>
                    <div class="text-textMuted">touches ${debugFmt(m.touch_count)} · ${m.events.slice(-3).join(' · ')}</div>
                </div>`).join('')}
            </div>
        </div>`;
    }
    html += `<div class="mt-2 text-[10px] text-textMuted">swings H/L ${debugFmt(fv.swing_count_high)}/${debugFmt(fv.swing_count_low)} · vector ${debugFmt(fv.version)} · algo ${debugFmt(ms.algorithm_version)}</div>`;
    body.innerHTML = html;
}

function renderDebugLiquidity(lq) {
    const body = document.getElementById('debug-liquidity-body');
    if (!body) return;
    if (!lq || !lq.available) { body.innerHTML = `<div class="text-textMuted italic font-sans">${(lq && lq.reason) || 'Liquidity unavailable.'}</div>`; return; }
    const rep = lq.report || {};
    const feats = (rep.features && typeof rep.features === 'object') ? rep.features : {};
    const pools = rep.pools || [];
    let html = `<div class="flex flex-wrap gap-2 mb-2">
        ${debugStatusChip('STATUS', rep.status || 'UNAVAILABLE', rep.status)}
        ${debugStatusChip('CAUSAL', rep.causal_state || 'UNAVAILABLE', rep.causal_state)}
        ${debugStatusChip('SOURCE', rep.source || 'UNAVAILABLE')}
        ${debugStatusChip('SCHEMA', (rep.schema || {}).id || '--')}
    </div>
    <div class="grid grid-cols-2 md:grid-cols-5 gap-2 mb-2 text-[10px]">
        ${['bsl_distance_atr','ssl_distance_atr','eqh_strength','eql_strength','htf_liquidity_score'].map(n => `<div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">${n}</div><div class="text-accentCyan font-black">${debugFmt(feats[n])}</div></div>`).join('')}
    </div>
    <div class="grid grid-cols-2 md:grid-cols-5 gap-2 mb-2 text-[10px]">
        ${['internal_liquidity_distance','external_liquidity_distance','liquidity_confluence','liquidity_sweep_state','post_sweep_displacement'].map(n => `<div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-2"><div class="text-textMuted">${n}</div><div class="text-accentCyan font-black">${debugFmt(feats[n])}</div></div>`).join('')}
    </div>`;
    if (pools.length) {
        html += `<div class="mt-2 pt-2 border-t border-borderClr/60">
            <div class="text-[10px] uppercase tracking-widest text-textMuted mb-1">Active Pools (BSL/SSL/EQH/EQL — why each distance is what it is)</div>
            <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2 text-[10px]">
                ${pools.map(p => `<div class="bg-darkBg/40 border ${debugTone(p.state).border} rounded-lg p-2">
                    <div class="flex justify-between"><span class="text-accentCyan font-black">${debugFmt(p.side)}</span><span class="${debugTone(p.state).text} font-bold">${debugFmt(p.state)}</span></div>
                    <div class="text-textMuted">${debugFmt(p.source)} @ ${debugFmt(p.price, 2)}</div>
                    <div class="text-textMuted">Confr: ${debugFmt(p.confirmed_at)}</div>
                </div>`).join('')}
            </div>
        </div>`;
    }
    html += `<div class="mt-2 text-[10px] text-textMuted">Last update ${debugFmt(rep.last_update)} · age ${debugFmt(rep.age_sec)}s · latency ${debugFmt(rep.latency_ms)}ms · algo ${debugFmt(rep.algorithm_version)}</div>`;
    body.innerHTML = html;
}

function renderDebugNews(n) {
    const body = document.getElementById('debug-news-body');
    if (!body) return;
    if (!n || !n.available) { body.innerHTML = `<div class="text-textMuted italic font-sans">News unavailable.</div>`; return; }
    let html = `<div class="flex flex-wrap gap-2 mb-2">
        ${debugStatusChip('ENABLED', n.enabled ? 'YES' : 'NO', n.enabled ? 'PASS' : 'INFO')}
        ${debugStatusChip('STATE', n.state || 'UNAVAILABLE', n.state)}
        ${n.stale ? debugStatusChip('FRESHNESS', 'STALE', 'WARN') : ''}
    </div>`;
    html += `
        ${debugKV('Available', n.available)}${debugKV('Freshness', n.freshness)}${debugKV('Bullish', n.bullish)}
        ${debugKV('Bearish', n.bearish)}${debugKV('Mixed/Conflict', n.mixed)}${debugKV('High Impact', n.high_impact)}
        ${debugKV('XAUUSD Relevance', n.xauusd_relevance)}${debugKV('USD Relevance', n.usd_relevance)}
        ${debugKV('Consensus', n.consensus)}${debugKV('Confidence', n.confidence)}
        ${debugKV('Context Timestamp', n.timestamp)}`;
    if (n.active_events && n.active_events.length) {
        html += `<div class="mt-2 pt-2 border-t border-borderClr/60"><div class="text-[10px] uppercase tracking-widest text-textMuted mb-1">Active Events</div>`;
        html += n.active_events.map(ev => `<div class="text-[10px] text-amber-300">• ${debugFmt(ev)}</div>`).join('');
        html += `</div>`;
    }
    if (n.model_dimensions && n.model_dimensions.length) {
        html += `<div class="mt-2 pt-2 border-t border-borderClr/60">
            <div class="text-[10px] uppercase tracking-widest text-textMuted mb-1">News Dimensions Active in Model (indices 50..59)</div>
            <div class="grid grid-cols-2 md:grid-cols-5 gap-1 text-[10px] text-accentGold">${n.model_dimensions.map(x => `<div>${x.index} ${x.name}</div>`).join('')}</div>
        </div>`;
    }
    body.innerHTML = html;
}


function renderDebugWorkers(w) {
    const body = document.getElementById('debug-workers-body');
    if (!body) return;
    if (!w || !w.available) { body.innerHTML = `<div class="text-textMuted italic font-sans">Workers unavailable.</div>`; return; }
    const wk = w.workers || {};
    const labels = { accounting: 'Accounting', history_sync: 'History Sync', intelligence: 'Intelligence', research: 'Research', training: 'Training', shadow: 'Shadow', shadow70: 'Shadow70', news: 'News', telegram: 'Telegram' };
    let html = '';
    Object.entries(labels).forEach(([key, label]) => {
        const worker = wk[key] || {};
        const state = worker.state || 'UNAVAILABLE';
        const tone = debugTone(state);
        // degraded classification: RUNNING but no recent useful work
        let degraded = false;
        if (state === 'RUNNING') {
            const ls = worker.last_success;
            if (ls) {
                const ageSec = (Date.now() - new Date(ls).getTime()) / 1000;
                if (ageSec > 600) degraded = true; // 10 min
            }
        }
        const dispState = degraded ? 'DEGRADED' : state;
        const dispTone = degraded ? debugTone('DEGRADED') : tone;
        html += `<div class="border border-borderClr/40 rounded-lg p-2 mb-1 bg-darkBg/30">
            <div class="flex justify-between items-center">
                <span class="text-gray-200 font-bold">${label}</span>
                <span class="px-1.5 py-0.5 rounded text-[9px] font-extrabold border ${dispTone.bg} ${dispTone.text} ${dispTone.border}">${dispState}${degraded ? ' (idle>10m)' : ''}</span>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-1 text-[10px] mt-1">
                ${debugKV('Cycle', worker.cycle)}${debugKV('Last Start', worker.last_start)}${debugKV('Last Success', worker.last_success)}
                ${debugKV('Duration ms', worker.duration_ms)}${debugKV('Queue', worker.queue)}${debugKV('Last Error', worker.last_error)}
            </div>
        </div>`;
    });
    body.innerHTML = html;
}

function renderDebugDatabase(db) {
    const body = document.getElementById('debug-database-body');
    if (!body) return;
    if (!db || !db.available) { body.innerHTML = `<div class="text-textMuted italic font-sans">Database state unavailable.</div>`; return; }
    const dbs = db.databases || {};
    const labels = { audit: 'audit.db', news: 'news.db', candle_intel: 'candle_intel.db', research: 'research storage' };
    let html = '';
    Object.entries(labels).forEach(([key, label]) => {
        const d = dbs[key] || {};
        const tone = debugTone(d.health);
        html += `<div class="border border-borderClr/40 rounded-lg p-2 mb-1 bg-darkBg/30">
            <div class="flex justify-between items-center">
                <span class="text-gray-200 font-bold">${label}</span>
                <span class="px-1.5 py-0.5 rounded text-[9px] font-extrabold border ${tone.bg} ${tone.text} ${tone.border}">${d.health || 'UNAVAILABLE'}</span>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-1 text-[10px] mt-1">
                ${debugKV('Path', d.path)}${debugKV('Size', d.size_bytes !== null && d.size_bytes !== undefined ? (d.size_bytes / 1024).toFixed(1) + ' KB' : '--')}
                ${debugKV('WAL', d.wal_bytes !== null && d.wal_bytes !== undefined ? (d.wal_bytes / 1024).toFixed(1) + ' KB' : '--')}
                ${debugKV('Reason', d.reason)}
            </div>
        </div>`;
    });
    body.innerHTML = html;
}

function renderDebugCaches(ca) {
    const body = document.getElementById('debug-caches-body');
    if (!body) return;
    if (!ca || !ca.available) { body.innerHTML = `<div class="text-textMuted italic font-sans">Cache state unavailable.</div>`; return; }
    const caches = ca.caches || {};
    const labels = { model: 'Model', feature: 'Feature', liquidity: 'Liquidity', news: 'News', exposure: 'Exposure', chart: 'Chart', research: 'Research' };
    let html = '';
    Object.entries(labels).forEach(([key, label]) => {
        const c = caches[key] || {};
        const tone = debugTone(c.status);
        html += `<div class="border border-borderClr/40 rounded-lg p-2 mb-1 bg-darkBg/30">
            <div class="flex justify-between items-center">
                <span class="text-gray-200 font-bold">${label}</span>
                <span class="px-1.5 py-0.5 rounded text-[9px] font-extrabold border ${tone.bg} ${tone.text} ${tone.border}">${c.status || '--'}</span>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-1 text-[10px] mt-1">
                ${debugKV('Size', c.size)}${debugKV('Age s', c.age_sec !== null && c.age_sec !== undefined ? c.age_sec.toFixed(1) : '--')}
                ${debugKV('TTL', c.ttl)}${debugKV('Last Update', c.last_update)}
            </div>
        </div>`;
    });
    body.innerHTML = html;
}

function renderDebugChart(ch, sse) {
    const body = document.getElementById('debug-chart-body');
    if (!body) return;
    let html = '';
    if (ch && ch.available) {
        html += `<div class="border border-borderClr/40 rounded-lg p-2 mb-1 bg-darkBg/30">
            <div class="text-gray-200 font-bold mb-1">Chart</div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-1 text-[10px]">
                ${debugKV('Data Source', ch.data_source)}${debugKV('Bars Received', ch.bars_received)}
                ${debugKV('First TS', ch.first_timestamp)}${debugKV('Last TS', ch.last_timestamp)}
                ${debugKV('Timeframe', ch.timeframe)}
            </div>
            <div class="grid grid-cols-3 gap-1 text-[10px] mt-1">
                ${debugKV('Liquidity OL', ch.overlays ? ch.overlays.liquidity : false, ch.overlays && ch.overlays.liquidity ? 'PASS' : 'INFO')}
                ${debugKV('News OL', ch.overlays ? ch.overlays.news : false, ch.overlays && ch.overlays.news ? 'PASS' : 'INFO')}
                ${debugKV('SMC OL', ch.overlays ? ch.overlays.smc : false, ch.overlays && ch.overlays.smc ? 'PASS' : 'INFO')}
            </div>
        </div>`;
    }
    if (sse) {
        const tone = debugTone(sse.connection);
        html += `<div class="border border-borderClr/40 rounded-lg p-2 mb-1 bg-darkBg/30">
            <div class="flex justify-between items-center">
                <span class="text-gray-200 font-bold">SSE</span>
                <span class="px-1.5 py-0.5 rounded text-[9px] font-extrabold border ${tone.bg} ${tone.text} ${tone.border}">${sse.connection || 'UNKNOWN'}</span>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-1 text-[10px] mt-1">
                ${debugKV('Connected At', sse.connected_at)}${debugKV('Last Event', sse.last_event)}
                ${debugKV('Event Count', sse.event_count)}${debugKV('Last Latency ms', sse.last_latency_ms)}
                ${debugKV('Serialization Errors', sse.serialization_errors, sse.serialization_errors > 0 ? 'FAIL' : 'PASS')}
                ${debugKV('Reconnects', sse.reconnect_count)}
            </div>
            ${sse.serialization_error ? `<div class="mt-1 text-[10px] text-rose-400">
                <i class="fa-solid fa-triangle-exclamation mr-1"></i>SSE_SERIALIZATION_ERROR — ${debugFmt(sse.serialization_error.correlation_id)} :: ${debugFmt((sse.serialization_error.failed_fields || []).join(', '))}
            </div>` : ''}
        </div>`;
    }
    body.innerHTML = html || '<div class="text-textMuted italic font-sans">No chart/SSE state.</div>';
}

function renderDebugErrors(er) {
    const body = document.getElementById('debug-errors-body');
    if (!body) return;
    const errors = (er && er.errors) || [];
    if (!errors.length) { body.classList.add('hidden'); return; }
    body.classList.remove('hidden');
    body.innerHTML = `<div class="text-[10px] uppercase tracking-widest text-rose-400 mb-1">NO HIDDEN ERRORS — backend failures surfaced</div>` +
        errors.map(e => `<div class="py-1 border-b border-rose-500/20">
            ${debugFmt(e.timestamp)} · ${e.component} · ${e.endpoint} · ${e.error_code} · corr ${debugFmt(e.correlation_id)}
            <div class="text-[9px] text-textMuted">${debugFmt(e.exception)}</div>
        </div>`).join('');
}


let debugSnapshotForTree = null;

async function loadDebugSnapshotHistory(preserveSelection) {
    try {
        const res = await fetch('/api/debug/snapshots');
        const data = await res.json();
        debugSnapshotHistory = (data.snapshots || []).slice().reverse(); // newest first
        const selA = document.getElementById('debug-snapshot-compare-a');
        const selB = document.getElementById('debug-snapshot-compare-b');
        if (!selA || !selB) return;
        const opts = debugSnapshotHistory.map(s => `<option value="${s.snapshot_id}">${(s.snapshot_id || '').substring(0, 18)} ${(s.timestamp || '').substring(11, 19)}</option>`).join('');
        if (!preserveSelection || selA.options.length === 0) {
            selA.innerHTML = opts;
            selB.innerHTML = opts;
            if (debugSnapshotHistory.length >= 2) {
                selA.selectedIndex = Math.min(1, debugSnapshotHistory.length - 1);
                selB.selectedIndex = 0;
            }
        }
    } catch (err) {
        console.error('Failed to load snapshot history', err);
    }
}

async function compareDebugSnapshots() {
    const body = document.getElementById('debug-compare-body');
    if (!body) return;
    const a = document.getElementById('debug-snapshot-compare-a').value;
    const b = document.getElementById('debug-snapshot-compare-b').value;
    if (!a || !b || a === b) { body.innerHTML = '<div class="text-accentGold text-[10px]">Select two different snapshots.</div>'; return; }
    try {
        const res = await fetch(`/api/debug/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
        const d = await res.json();
        const diffs = d.feature_diffs || [];
        const html = [
            `<div class="text-[10px] uppercase tracking-widest text-textMuted mb-1">FEATURE DIFF — ${(d.a_timestamp || '').substring(11, 19)} → ${(d.b_timestamp || '').substring(11, 19)} (${diffs.length} changed)</div>`,
            ...diffs.slice(0, 40).map(f => {
                const deltaTone = f.delta >= 0 ? 'text-emerald-400' : 'text-rose-400';
                return `<div class="flex justify-between text-[10px] py-0.5 border-b border-borderClr/20">
                    <span class="text-textMuted w-10">${f.index}</span><span class="text-gray-300 w-40 truncate">${f.name}</span>
                    <span class="text-gray-400">${debugFmt(f.t0)} → ${debugFmt(f.t1)}</span>
                    <span class="${deltaTone} font-black">Δ ${f.delta >= 0 ? '+' : ''}${debugFmt(f.delta)}</span>
                </div>`;
            }).join(''),
            `<div class="mt-2 pt-2 border-t border-borderClr/40 text-[10px] text-textMuted">Model/Confidence/Policy/Risk changes: see JSON tree or use the single-snapshot panels.</div>`
        ].join('');
        body.innerHTML = html;
    } catch (err) {
        body.innerHTML = `<div class="text-rose-400 text-[10px]">Compare failed: ${err.message}</div>`;
    }
}

function copyDebugSnapshot() {
    if (!debugSnapshotCache) return;
    navigator.clipboard.writeText(JSON.stringify(debugSnapshotCache, null, 2)).then(() => {
        const el = document.getElementById('debug-compare-body');
        if (el) el.innerHTML = '<div class="text-emerald-400 text-[10px]">Snapshot copied to clipboard.</div>';
    }).catch(() => alert('Clipboard unavailable — use Download instead.'));
}

function downloadDebugSnapshot() {
    if (!debugSnapshotCache) return;
    const blob = new Blob([JSON.stringify(debugSnapshotCache, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${debugSnapshotCache.snapshot_id || 'debug'}.json`;
    a.click();
    URL.revokeObjectURL(url);
}

function toggleDebugJsonTree() {
    const tree = document.getElementById('debug-json-tree');
    if (!tree) return;
    tree.classList.toggle('hidden');
    if (!tree.classList.contains('hidden')) renderDebugJsonTree(debugSnapshotCache, true);
}

function renderDebugJsonTree(d, force) {
    const tree = document.getElementById('debug-json-tree');
    if (!tree) return;
    if (tree.classList.contains('hidden') && !force) return;
    tree.textContent = d ? JSON.stringify(d, null, 2) : 'No snapshot captured yet.';
}

// Server-Sent Events (SSE) Stream Subscriber

// HARDENED (LiveUiState.2): typed events (`state` full snapshot / `tick`

// incremental), bounded exponential reconnect (no reconnect storm), explicit

// DISCONNECTED badge on failure, HEARTBEAT-driven staleness mark, monotonic

// state_version guard (out-of-order updates are dropped), and payload-level

// sanitization (never render malformed candles silently).

let sseRetryDelay = 1000;

let sseLastEventAt = 0;

let sseStaleTimer = null;



// FRONTEND OBSERVABILITY (Phase 14): track last snapshot / SSE timestamps so

// the operator can verify data age and synchronization.

let lastApiResponseAt = 0;

let lastSnapshotVersion = null;

let lastSnapshotAt = 0;



// LiveUiState.2 merge model: the UI keeps ONE authoritative snapshot object.

// REST bootstrap replaces it; SSE `tick` events merge into it; SSE `state`

// events replace it. Out-of-order versions are rejected.

let liveUiSnapshot = null;



function updateObsStrip() {

    const el = document.getElementById('obs-strip');

    if (!el) return;

    const now = Date.now();

    const apiAge = lastApiResponseAt ? Math.round((now - lastApiResponseAt) / 1000) + 's' : '—';

    const sseAge = sseLastEventAt ? Math.round((now - sseLastEventAt) / 1000) + 's' : '—';

    const snapTs = lastSnapshotAt ? new Date(lastSnapshotAt).toISOString().substring(11, 19) + 'Z' : '—';

    el.textContent =

        `rest ${apiAge} · sse ${sseAge} · v${lastSnapshotVersion || '—'} @ ${snapTs}`;

}



function sseStaleCheck() {

    if (!eventSource) return;

    // If no live event has arrived in 30s, mark stream stale (amber) but do

    // not tear down - a paused engine still keeps the stream open.

    if (sseLastEventAt && (Date.now() - sseLastEventAt) > 30000) {

        const badge = document.getElementById('system-status-badge');

        if (badge) {

            badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-amber-400 mr-1.5"></span> STALE';

            badge.className = 'ml-3 text-xs px-2.5 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/30 flex items-center justify-center font-bold';

        }

        setChartStatus('stale');

        // RESYNC (BUG-054): a stale live stream after a reconnect/downtime

        // must re-fetch the full broker candle history (throttled to once

        // per 30s so a dead stream can't hammer the server).

        if (Date.now() - lastChartResyncAt > 30000) {

            resyncChart();

        }

    }

    updateObsStrip();

}



function setSystemBadge(state) {

    const badge = document.getElementById('system-status-badge');

    if (!badge) return;

    if (state === 'connecting') {
        // BUG-130: gradient animated "connecting" state — MetaTrader link /
        // SSE stream is retrying (cold terminal, IPC timeout). Perfect UI/UX:
        // the user SEES the retry progress, never a dead-looking badge.
        badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-gradient-to-r from-cyan-400 via-sky-400 to-blue-500 animate-pulse mr-1.5"></span> CONNECTING';
        badge.className = 'ml-3 text-xs px-2.5 py-0.5 rounded-full bg-gradient-to-r from-cyan-500/15 via-sky-500/15 to-blue-500/15 text-sky-300 border border-sky-500/40 flex items-center justify-center font-bold shadow-[0_0_12px_rgba(56,189,248,0.25)]';
        sseRetryDelay = 1000;
    } else if (state === 'active') {

        badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse mr-1.5"></span> ACTIVE';

        badge.className = 'ml-3 text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 flex items-center justify-center font-bold';

        sseRetryDelay = 1000;

    } else if (state === 'paused') {

        badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-amber-400 mr-1.5"></span> PAUSED';

        badge.className = 'ml-3 text-xs px-2.5 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/30 flex items-center justify-center font-bold';

        sseRetryDelay = 1000;

    } else {

        badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-rose-500 mr-1.5"></span> DISCONNECTED';

        badge.className = 'ml-3 text-xs px-2.5 py-0.5 rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/30 flex items-center justify-center font-bold';

    }

}



function startSSE() {

    if (eventSource) {

        eventSource.close();

        eventSource = null;

    }



    console.log('[UI_STREAM] event=CONNECTING endpoint=/api/ticks/stream');



    // Connect to server Sent Events streaming endpoint

    setSystemBadge('connecting');

    eventSource = new EventSource('/api/ticks/stream');



    eventSource.onopen = () => {

        console.log('[UI_STREAM] event=CONNECTED');

        sseRetryDelay = 1000;

        if (sseStaleTimer) clearInterval(sseStaleTimer);

        sseStaleTimer = setInterval(sseStaleCheck, 5000);

        // After any (re)connect, pull a fresh canonical snapshot so the UI

        // converges to live state even if SSE events were missed while down.

        console.log('[UI_STREAM] event=RESYNC reason=RECONNECT');

        fetchSystemSnapshot();

        // RESYNC (BUG-054): after downtime the chart must also re-fetch the

        // full broker candle history (throttled to once per 10s).

        if (Date.now() - lastChartResyncAt > 10000) {

            resyncChart();

        }

    };



    eventSource.onmessage = (event) => {

        sseRetryDelay = 1000;

        sseLastEventAt = Date.now();

        try {

            const data = JSON.parse(event.data);

            lastApiResponseAt = Date.now();

            if (data.state_version != null) lastSnapshotVersion = data.state_version;

            updateObsStrip();

            handleIncomingLiveTick(data);

        } catch (err) {

            console.error('[UI_ERROR] component=SSE action=PARSE request_id=-', err);

        }

    };



    eventSource.addEventListener('state', (event) => {

        sseRetryDelay = 1000;

        sseLastEventAt = Date.now();

        try {

            const data = JSON.parse(event.data);

            lastApiResponseAt = Date.now();

            if (data.state_version != null) lastSnapshotVersion = data.state_version;

            updateObsStrip();

            // Full snapshot: replace the merged state, then render.

            liveUiSnapshot = data;
            renderMarketRadar(liveUiSnapshot && liveUiSnapshot.radar);


            handleIncomingLiveTick(data, { isSnapshot: true });

        } catch (err) {

            console.error('[UI_ERROR] component=SSE action=PARSE_STATE request_id=-', err);

        }

    }, false);



    eventSource.addEventListener('tick', (event) => {

        sseRetryDelay = 1000;

        sseLastEventAt = Date.now();

        try {

            const data = JSON.parse(event.data);

            lastApiResponseAt = Date.now();

            // Monotonic version guard: drop out-of-order updates.

            const v = data.state_version;

            if (v != null && lastSnapshotVersion != null && v <= lastSnapshotVersion) {

                console.warn('[UI_STREAM] event=DROPPED reason=OUT_OF_ORDER version=' + v);

                return;

            }

            if (v != null) lastSnapshotVersion = v;

            updateObsStrip();

            handleIncomingLiveTick(data);

        } catch (err) {

            console.error('[UI_ERROR] component=SSE action=PARSE_TICK request_id=-', err);

        }

    }, false);



    eventSource.addEventListener('heartbeat', () => {

        sseLastEventAt = Date.now();

        updateObsStrip();

    }, false);



    eventSource.onerror = (err) => {

        // Bounded exponential reconnect: EventSource auto-reconnects; we

        // additionally cap the rate to avoid a reconnect storm on a dead server.

        console.warn('[UI_ERROR] component=SSE action=RECONNECT status=network', err);

        setSystemBadge('disconnected');

        if (eventSource) {

            eventSource.close();

            eventSource = null;

            const delay = Math.min(sseRetryDelay, 15000);

            sseRetryDelay = Math.min(sseRetryDelay * 2, 15000);

            setTimeout(() => {

                if (!eventSource) startSSE();

            }, delay);

        }

    };

}



// Handle Incoming Live Market Tick & State Updates

function handleIncomingLiveTick(payload, opts) {

    if (uiPaused) return; // Prevent updates if user paused the visualizer

    const isSnapshot = !!(opts && opts.isSnapshot);



    // Merge incremental updates into the authoritative snapshot. Full

    // snapshots replace it; ticks overlay only the changed sections so the

    // heavyweight lists (bars/features/predictions) survive between full

    // events and refresh never destroys the UI shell.

    if (isSnapshot) {

        liveUiSnapshot = payload;

    } else if (liveUiSnapshot) {

        liveUiSnapshot = Object.assign({}, liveUiSnapshot, payload);

    } else {

        liveUiSnapshot = payload;

    }

    payload = liveUiSnapshot;
    renderMarketRadar(liveUiSnapshot && liveUiSnapshot.radar);




    // Retain the latest payload for AI VIEW per-candle snapshots.

    lastAiSnapshot = payload;



    // Update Connection State badge & engine toggle button (Reconciled single truth)
    const badge = document.getElementById('system-status-badge');
    const engineBtn = document.getElementById('btn-toggle-engine');
    if (payload.engine_running) {
        if (badge) {
            badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse mr-1.5"></span> RUNNING`;
            badge.className = "ml-3 text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 flex items-center font-bold";
        }
        if (engineBtn) {
            engineBtn.innerHTML = `<i class="fa-solid fa-circle-stop"></i> <span>Stop Bot</span>`;
            engineBtn.className = "flex-1 bg-rose-500 hover:bg-rose-600 text-white font-bold py-1.5 px-3 rounded text-xs transition shadow-md shadow-rose-500/10 flex items-center justify-center space-x-1";
        }
    } else {
        if (badge) {
            badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-amber-400 mr-1.5"></span> PAUSED`;
            badge.className = "ml-3 text-xs px-2.5 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/30 flex items-center font-bold";
        }
        if (engineBtn) {
            engineBtn.innerHTML = `<i class="fa-solid fa-circle-play"></i> <span>Start Bot</span>`;
            engineBtn.className = "flex-1 bg-emerald-500 hover:bg-emerald-600 text-white font-bold py-1.5 px-3 rounded text-xs transition shadow-md shadow-emerald-500/10 flex items-center justify-center space-x-1";
        }
    }



    // Provenance + snapshot identity (FULL-STATE diagnostic strip)

    const prov = payload.provenance || {};

    const snapId = document.getElementById('state-version-indicator');

    if (snapId) snapId.textContent = 'v' + (payload.state_version || '--') + ' · ' + (payload.snapshot_timestamp || '').substring(11, 19);



    // Top Header Stats (null-safe: render explicit unavailable, never fake)

    const setTxt = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };

    if (payload.symbol != null) setTxt('quick-symbol', payload.symbol);

    if (payload.bid != null && payload.ask != null) {
        const __d = (payload.price_digits != null) ? payload.price_digits : (String(payload.symbol||"").startsWith("XAU")||String(payload.symbol||"").startsWith("GOLD") ? 2 : 5);
        setTxt('quick-bid', payload.bid != null ? payload.bid.toFixed(__d) : '—');
        setTxt('quick-ask', payload.ask != null ? payload.ask.toFixed(__d) : '—');
    }

    if (payload.regime != null) setTxt('quick-regime', payload.regime);

    if (payload.execution_mode != null) {

        const sel = document.getElementById('execution-mode-selector');

        if (sel) {

            sel.value = payload.execution_mode;

            window.__serverExecutionMode = payload.execution_mode;

        }

    }

    // Real runtime mode (backend-derived from MT5 connection state; the

    // configured-mode selector above never lies about being LIVE).

    const runtimeMode = payload.runtime_mode || payload.execution_mode || null;

    const modeBadge = document.getElementById('runtime-mode-badge');

    if (modeBadge && runtimeMode) {

        modeBadge.textContent = runtimeMode;

        const isLive = String(runtimeMode).indexOf('LIVE') === 0;

        const isDegraded = String(runtimeMode).indexOf('DISCONNECTED') !== -1 || String(runtimeMode).indexOf('BLOCKED') !== -1;

        modeBadge.className = 'text-[10px] px-2 py-0.5 rounded font-black border ' +

            (isLive && !isDegraded

                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'

                : isDegraded

                    ? 'bg-rose-500/10 text-rose-400 border-rose-500/30'

                    : 'bg-amber-500/10 text-amber-400 border-amber-500/30');

    }



    // Header health: engine/mode badge + last-update age.

    const health = payload.health || {};

    const healthBadge = document.getElementById('header-health-badge');

    if (healthBadge && health.subsystems) {

        const overall = health.overall || 'UNAVAILABLE';

        const style = healthBadgeStyle(overall);

        healthBadge.textContent = overall;

        healthBadge.className = 'text-[10px] px-2 py-0.5 rounded font-black ' + style;

    }

    const healthDetail = document.getElementById('header-health-detail');

    if (healthDetail && health.details) {

        healthDetail.textContent = health.details.engine || health.details.mt5 || '—';

    }

    // BUG-130: gradient MT5 connection pill in the IPC console header. The
    // state comes from the real health snapshot (READY / DISCONNECTED /
    // WAITING_TICK / STALE) so the user always sees live broker link status.
    const mt5Pill = document.getElementById('mt5-connect-pill');

    if (mt5Pill) {

        const mState = String(health.subsystems?.mt5 || health.details?.mt5 || '').toUpperCase();

        let pillTxt = 'MT5 ' + (mState.split(' ')[0] || '--');

        let pillCls = 'px-2 py-1 rounded font-mono border ';

        if (mState.includes('READY')) {

            pillCls += 'bg-gradient-to-r from-emerald-500/20 to-teal-500/20 text-emerald-300 border-emerald-500/40';

        } else if (mState.includes('DISCONNECT') || mState.includes('ERROR')) {

            pillCls += 'bg-gradient-to-r from-rose-500/20 to-red-500/20 text-rose-300 border-rose-500/40';

        } else if (mState.includes('WAITING') || mState.includes('STALE')) {

            pillCls += 'bg-gradient-to-r from-amber-500/20 to-orange-500/20 text-amber-300 border-amber-500/40';

        } else if (mState.includes('CONNECT')) {

            pillCls += 'bg-gradient-to-r from-cyan-500/25 via-sky-500/25 to-blue-500/25 text-sky-300 border-sky-500/40 animate-pulse shadow-[0_0_12px_rgba(56,189,248,0.3)]';

        } else {

            pillCls += 'bg-gradient-to-r from-darkBg to-panelBg text-textMuted border-borderClr/60';

        }

        mt5Pill.textContent = pillTxt;

        mt5Pill.className = pillCls;

    }

    const lastUpdate = document.getElementById('header-last-update');

    if (lastUpdate) {

        lastUpdate.textContent = lastSnapshotAt

            ? Math.round((Date.now() - lastSnapshotAt) / 1000) + 's ago'

            : '—';

    }



    // Monitoring Panel

    { const __d=(payload.price_digits!=null)?payload.price_digits:(String(payload.symbol||"").startsWith("XAU")||String(payload.symbol||"").startsWith("GOLD")?2:5); if (payload.bid != null) setTxt('monitor-bid', payload.bid.toFixed(__d)); if (payload.ask != null) setTxt('monitor-ask', payload.ask.toFixed(__d)); }

    if (payload.spread != null) setTxt('monitor-spread', `${payload.spread} pts`);

    // Explicit STALE marker when the broker tick is old (task section 11:

    // never show a stale price as current).

    const tickStaleEl = document.getElementById('monitor-tick-state');

    if (tickStaleEl) {

        if (payload.tick_stale) {

            tickStaleEl.textContent = 'STALE';

            tickStaleEl.className = 'text-[10px] font-mono font-black text-rose-400';

        } else if (payload.tick_freshness_ms != null) {

            tickStaleEl.textContent = 'LIVE';

            tickStaleEl.className = 'text-[10px] font-mono font-black text-emerald-400';

        } else {

            tickStaleEl.textContent = '—';

            tickStaleEl.className = 'text-[10px] font-mono font-black text-textMuted';

        }

    }

    if (payload.atr != null) {

        const volVal = payload.atr;

        setTxt('monitor-atr', (volVal < 0.1) ? volVal.toFixed(6) : volVal.toFixed(2));

    }

    if (payload.regime != null) setTxt('monitor-regime', payload.regime);



    // AI Prediction Card

    if (payload.ai_decision != null) setTxt('ai-decision-badge', payload.ai_decision);

    if (payload.ai_confidence != null) setTxt('ai-confidence', `Conf: ${(payload.ai_confidence * 100).toFixed(2)}%`);

    if (payload.ai_reason != null) setTxt('ai-reason-text', `"${payload.ai_reason}"`);



    // Softmax probabilities (available flag; no fake 99.5/0.2/0.3 defaults)

    const probs = payload.probs || {};

    if (probs.available && probs.no_trade != null && probs.buy != null && probs.sell != null) {

        const pNoTrade = (probs.no_trade * 100).toFixed(1);

        const pBuy = (probs.buy * 100).toFixed(1);

        const pSell = (probs.sell * 100).toFixed(1);



        setTxt('prob-no-trade', `${pNoTrade}%`);

        setTxt('prob-buy', `${pBuy}%`);

        setTxt('prob-sell', `${pSell}%`);

        const ntBar = document.getElementById('prob-no-trade-bar');

        const bBar = document.getElementById('prob-buy-bar');

        const sBar = document.getElementById('prob-sell-bar');

        if (ntBar) ntBar.style.width = `${pNoTrade}%`;

        if (bBar) bBar.style.width = `${pBuy}%`;

        if (sBar) sBar.style.width = `${pSell}%`;

    }



    // ScalpNet panel (real model metadata; renders "—" until a live inference exists)

    const model = payload.model || {};

    if (model.available) {

        setTxt('model-id', model.model_id || '—');

        setTxt('model-version', model.model_version || '—');

        setTxt('model-architecture', model.architecture || '—');

        setTxt('model-artifact', model.artifact_path || '—');

        setTxt('model-schema', model.feature_schema_id || '—');

        setTxt('model-scaler', model.scaler_ready ? 'READY' : 'NOT READY');

        // AI Hub integrity verdict (backend-decided; the UI never derives
        // model health locally from a file path — BUG-110 discipline).
        NX.api.get('/api/models/integrity', { component: 'AIHub', action: 'LOAD_INTEGRITY' })
            .then(function (resp) {
                if (!resp || !resp.available) return;
                const el = document.getElementById('model-integrity');
                if (el) { el.textContent = resp.compatibility || '--'; el.className = 'text-accentCyan font-bold truncate ' + ((resp.compatibility === 'VALID') ? '' : 'text-rose-400'); }
                const st = document.getElementById('model-state');
                if (st) st.textContent = resp.state || '--';
                const cls = document.getElementById('model-classes');
                if (cls) cls.textContent = resp.actual_output_classes != null ? String(resp.actual_output_classes) : '--';
            })
            .catch(function () { /* integrity endpoint unavailable: cards stay '—' */ });

        // Inference latency — honest staged breakdown (TASK latency forensics).
        // The single 'Latency' number was ambiguous: it covered validate + scaler +
        // tensor + debug copy + forward. Now the UI shows Model Forward separately
        // from Feature Build / Preprocess / E2E (brief 3).
        const lb = model.latency_breakdown || {};
        const modelFwd = (lb.model_ms != null) ? lb.model_ms : model.model_forward_ms;
        const featMs = (lb.feature_ms != null) ? lb.feature_ms : model.feature_ms;
        const e2eMs = (lb.e2e_ms != null) ? lb.e2e_ms : model.e2e_ms;
        const scalMs = lb.scaling_ms;
        const tensMs = lb.tensor_ms;
        const preprocMs = (scalMs != null && tensMs != null) ? (scalMs + tensMs) : null;
        const infTotalMs = (modelFwd != null && featMs != null) ? (featMs + modelFwd) : null;
        if (modelFwd != null) {
            setTxt('model-inference-time', `${Number(modelFwd).toFixed(2)}ms`);
            setTxt('latency-model-forward', `${Number(modelFwd).toFixed(2)} ms`);
        } else if (model.latency_ms != null) {
            setTxt('model-inference-time', `${Number(model.latency_ms).toFixed(2)}ms`);
            setTxt('latency-model-forward', `${Number(model.latency_ms).toFixed(2)} ms (legacy single-timer)`);
        }
        setTxt('latency-feature', featMs != null ? `${Number(featMs).toFixed(2)} ms` : '--');
        setTxt('latency-preprocess', preprocMs != null ? `${Number(preprocMs).toFixed(2)} ms` : '--');
        setTxt('latency-inference-total', infTotalMs != null ? `${Number(infTotalMs).toFixed(2)} ms` : '--');
        setTxt('latency-decision', lb.decision_ms != null ? `${Number(lb.decision_ms).toFixed(2)} ms` : '--');
        setTxt('latency-e2e', e2eMs != null ? `${Number(e2eMs).toFixed(2)} ms` : '--');
        setTxt('latency-queue', lb.queue_ms != null ? `${Number(lb.queue_ms).toFixed(2)} ms` : '--');

        if (payload.probs && payload.probs.available) {

            setTxt('model-data-source', 'LIVE INFERENCE');

        } else {

            setTxt('model-data-source', 'AWAITING FIRST INFERENCE');

        }

    } else {

        setTxt('model-data-source', 'AWAITING LIVE STATE');

    }



    // Account Section (fields may be null when the broker adapter is

        // unavailable - render an explicit unavailable state, never $"NaN")

        const accBal = payload.account && payload.account.balance;

        const accEq = payload.account && payload.account.equity;

        const accFloat = payload.account && payload.account.floating;

        const accDd = payload.account && payload.account.drawdown;

        const accWr = payload.account && payload.account.win_rate;

        const accMargin = payload.account && payload.account.margin_free;

        setTxt('acc-balance', (accBal != null) ? `$${accBal.toLocaleString('en-US', {minimumFractionDigits: 2})}` : '—');

        setTxt('acc-equity', (accEq != null) ? `$${accEq.toLocaleString('en-US', {minimumFractionDigits: 2})}` : '—');

        setTxt('acc-floating', (accFloat != null) ? `${accFloat >= 0 ? '+' : ''}$${accFloat.toFixed(2)}` : '—');

        const flEl = document.getElementById('acc-floating');

        if (flEl) flEl.className = `text-lg font-black font-mono ${(accFloat != null && accFloat < 0) ? 'text-rose-400' : 'text-emerald-400'}`;

        setTxt('acc-drawdown', (accDd != null) ? `${accDd.toFixed(2)}%` : '—');

        setTxt('acc-winrate', (accWr != null) ? `${accWr.toFixed(1)}%` : '—');

        setTxt('acc-margin-free', (accMargin != null) ? `$${accMargin.toLocaleString('en-US', {minimumFractionDigits: 2})}` : '—');

        setTxt('acc-open-positions', (payload.account && payload.account.open_positions != null) ? String(payload.account.open_positions) : '—');



    // TRADE-CLOSE DETECTOR (live update with orders):

    // when open_positions drops or floating PnL settles from non-zero to

    // zero, a position just closed — refresh the accounting panel NOW so the

    // charts and PnL statistics update with the order (not on the 30s timer).

    const curOpen = (payload.account && payload.account.open_positions != null) ? Number(payload.account.open_positions) : null;

    const curFloat = (payload.account && payload.account.floating != null) ? Number(payload.account.floating) : null;

    if (curOpen != null && window.__lastOpenCount != null && curOpen < window.__lastOpenCount) {

        window.__acctRefreshAt = Date.now();

        loadAccountPerformance();

        loadAdvancedMetrics();

        loadAccountCharts();

        loadClosedTrades();

    } else if (curFloat != null && window.__lastFloatVal != null && window.__lastFloatVal !== 0 && curFloat === 0) {

        window.__acctRefreshAt = Date.now();

        loadAccountPerformance();

        loadAdvancedMetrics();

        loadAccountCharts();

        loadClosedTrades();

    }

    window.__lastOpenCount = curOpen;

    window.__lastFloatVal = curFloat;

        // Real broker account identity (from the typed snapshot)

        const acc = payload.account || {};

        setTxt('acc-login', (acc.login != null) ? String(acc.login) : '—');

        setTxt('acc-server', acc.server || '—');

        setTxt('acc-currency', acc.currency || '—');

        setTxt('acc-leverage', (acc.leverage != null) ? `1:${acc.leverage}` : '—');

        setTxt('acc-margin-level', (acc.margin_level != null && acc.margin_level > 0) ? `${acc.margin_level.toFixed(0)}%` : '—');

        setTxt('acc-trade-allowed', (acc.trade_allowed != null) ? (acc.trade_allowed ? 'YES' : 'NO') : '—');



    if (payload.support_levels) {

        supportLevels = payload.support_levels;

    }

    if (payload.resistance_levels) {

        resistanceLevels = payload.resistance_levels;

    }

    if (payload.visual_overlays) {

        visualOverlays = payload.visual_overlays;

    }



    // TASK-02-70D-INTEGRATION: canonical snapshot liquidity section.

    if (payload.liquidity) {

        syncLiquidityFromSnapshot(payload);

    }



    // Dynamic Candle updates (Single Source of truth includes forming bar)

    if (payload.bars && payload.bars.length > 0) {

        candleData = payload.bars;

        const barsMeta = document.getElementById('chart-bars-meta');

        if (barsMeta) {

            const forming = candleData.filter(b => b.is_complete === false).length;

            barsMeta.textContent = `${candleData.length - forming} closed + ${forming} forming`;

        }

        const srcBadge = document.getElementById('chart-source-badge');

        if (srcBadge) {

            const ts = (payload.timestamps && payload.timestamps.tick) || '';

            srcBadge.textContent = `price ${(payload.symbol || '—')} @ ${ts ? ts.substring(11, 19) + 'Z' : '—'}`;

        }

        if (currentTab === 'tab-monitoring') {

            if (liveMode && !isDragging) {

                // Pin view to the right side (newest bar) in live tracking mode

                const canvas = document.getElementById('candleChart');

                if (canvas) {

                    const rect = canvas.getBoundingClientRect();

                    chartPanX = rect.width - 60 - candleData.length * (candleWidth + candleGap);

                }

            }

            drawChart();

            updateCrosshairTooltip();

        }

    } else if (isSnapshot && candleData.length === 0) {

        // Explicit empty snapshot: keep the canvas in a clear empty state.

        setChartStatus('empty');

        drawChart();

    }



    // Populate active positions table

    populatePositionsTable(payload.positions || []);



    // Populate AI Analysis Category (real ENGINE_STATE features only)

    if (payload.features && payload.features.length > 0) {

        updateFeaturesGrid(payload.features);

        renderFeatureDeltas();

    }



    // Populate Prediction Outcomes (real audit_signals history)

    if (payload.predictions && payload.predictions.length > 0) {

        predictions = payload.predictions;

        updatePredictionsTable();

    } else if (isSnapshot) {

        predictions = [];

        updatePredictionsTable();

    }

}



// Helper to retrieve visible candles indices

function getVisibleIndices(w) {

    const startIdx = Math.max(0, Math.floor(-chartPanX / (candleWidth + candleGap)));

    const endIdx = Math.min(candleData.length - 1, Math.ceil((w - 60 - chartPanX) / (candleWidth + candleGap)));

    return { startIdx, endIdx };

}



// Draw candlestick data onto HTML5 Canvas with smooth responsive rendering and auto scaling

function drawChart() {

    const canvas = document.getElementById('candleChart');

    if (!canvas) return;

    const ctx = canvas.getContext('2d');



    // Handle retina display scaling (setTransform resets any prior scale)

    const dpr = window.devicePixelRatio || 1;

    const rect = canvas.getBoundingClientRect();

    canvas.width = rect.width * dpr;

    canvas.height = rect.height * dpr;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);



    const w = rect.width;

    const h = rect.height;



    // Draw grid background

    ctx.fillStyle = '#090d16';

    ctx.fillRect(0, 0, w, h);



    if (candleData.length === 0) {

        ctx.fillStyle = '#94a3b8';

        ctx.font = '12px sans-serif';

        ctx.textAlign = 'center';

        ctx.fillText("Awaiting historical OHLC stream mapping...", w / 2, h / 2);

        return;

    }



    // Determine boundaries for visible range only (prevents compressed squash look)

    const { startIdx, endIdx } = getVisibleIndices(w);

    let highPrice = -Infinity;

    let lowPrice = Infinity;

    for (let i = startIdx; i <= endIdx; i++) {

        const c = candleData[i];

        if (c.high > highPrice) highPrice = c.high;

        if (c.low < lowPrice) lowPrice = c.low;

    }



    // Fallback if no candles are visible

    if (highPrice === -Infinity || lowPrice === Infinity) {

        candleData.forEach(c => {

            if (c.high > highPrice) highPrice = c.high;

            if (c.low < lowPrice) lowPrice = c.low;

        });

    }



    const priceRange = (highPrice - lowPrice) || 1.0;

    const padding = priceRange * 0.08;

    const minPrice = lowPrice - padding;

    const maxPrice = highPrice + padding;

    const priceRangePadded = maxPrice - minPrice;



    // Grid lines matched to visible price scaling

    ctx.strokeStyle = '#121826';

    ctx.lineWidth = 1;

    for (let i = 40; i < h - 20; i += 40) {

        ctx.beginPath();

        ctx.moveTo(0, i);

        ctx.lineTo(w - 60, i);

        ctx.stroke();

    }

    // Vertical grid lines

    const step = (candleWidth + candleGap) * 5;

    for (let i = chartPanX % step; i < w - 60; i += step) {

        ctx.beginPath();

        ctx.moveTo(i, 0);

        ctx.lineTo(i, h - 20);

        ctx.stroke();

    }



    // Render validated zones (transparent rectangles)

    if (typeof visualOverlays !== 'undefined' && visualOverlays && visualOverlays.rectangles && visualOverlays.rectangles.length > 0) {

        visualOverlays.rectangles.forEach(rect => {

            const yHigh = h - 20 - ((rect.price_high - minPrice) / priceRangePadded) * (h - 40);

            const yLow = h - 20 - ((rect.price_low - minPrice) / priceRangePadded) * (h - 40);

            const rectHeight = Math.max(1, yLow - yHigh);



            let color = 'rgba(0, 230, 118, 0.25)'; // Green Box for Bullish FVG

            let label = rect.type;



            if (rect.type === 'BULLISH_ORDER_BLOCK' || rect.type === 'BEARISH_ORDER_BLOCK') {

                color = 'rgba(255, 255, 255, 0.08)'; // white/transparent box for valid OBs

                label = `ob (${(rect.ai_confidence * 100).toFixed(0)}%)`;

            } else if (rect.type === 'BEARISH_FVG') {

                color = 'rgba(255, 23, 68, 0.25)'; // Red Box for Bearish FVG

            } else if (rect.type === 'STOP_HUNT_ZONE') {

                color = 'rgba(255, 215, 0, 0.35)'; // Gold Box for Swept Liquidity Pools

            }



            let xStart = 0;

            let xEnd = w - 60;

            if (rect.time) {

                const candleIdx = candleData.findIndex(c => c.time === rect.time);

                if (candleIdx !== -1) {

                    xStart = Math.max(0, chartPanX + candleIdx * (candleWidth + candleGap));

                }

            }

            const rectWidth = Math.max(1, xEnd - xStart);



            ctx.fillStyle = color;

            ctx.fillRect(xStart, yHigh, rectWidth, rectHeight);



            if (rect.type === 'BULLISH_ORDER_BLOCK' || rect.type === 'BEARISH_ORDER_BLOCK') {

                ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';

                ctx.lineWidth = 1;

                ctx.strokeRect(xStart, yHigh, rectWidth, rectHeight);

            }



            // Display zone type & AI Confidence % inside the box

            ctx.fillStyle = 'rgba(226, 232, 240, 0.9)';

            ctx.font = 'bold 9px sans-serif';

            ctx.textAlign = 'left';

            const textY = Math.min(Math.max(yHigh + 12, 12), h - 22);

            ctx.fillText(label, xStart + 8, textY);

        });

    }



    // Render BOS Lines (Break of Structure)

    if (typeof visualOverlays !== 'undefined' && visualOverlays && visualOverlays.bos_lines && visualOverlays.bos_lines.length > 0) {

        ctx.lineWidth = 1.5;

        ctx.strokeStyle = 'rgba(230, 81, 0, 0.85)'; // Orange/Gold

        ctx.setLineDash([4, 2]);

        visualOverlays.bos_lines.forEach(line => {

            const y = h - 20 - ((line.price - minPrice) / priceRangePadded) * (h - 40);

            if (y >= 0 && y < h - 20) {

                ctx.beginPath();

                ctx.moveTo(0, y);

                ctx.lineTo(w - 60, y);

                ctx.stroke();



                ctx.fillStyle = 'rgba(230, 81, 0, 0.9)';

                ctx.font = 'bold 9px sans-serif';

                ctx.fillText("BOS", 25, y - 4);

            }

        });

        ctx.setLineDash([]);

    }



    // Render 50% Midline through middle of the impulse leg

    if (typeof visualOverlays !== 'undefined' && visualOverlays && visualOverlays.midlines && visualOverlays.midlines.length > 0) {

        ctx.lineWidth = 1.2;

        ctx.strokeStyle = 'rgba(148, 163, 184, 0.8)'; // Silver

        ctx.setLineDash([6, 4]);

        visualOverlays.midlines.forEach(line => {

            const y = h - 20 - ((line.price - minPrice) / priceRangePadded) * (h - 40);

            if (y >= 0 && y < h - 20) {

                ctx.beginPath();

                ctx.moveTo(0, y);

                ctx.lineTo(w - 60, y);

                ctx.stroke();



                ctx.fillStyle = 'rgba(148, 163, 184, 0.9)';

                ctx.font = 'bold 9px sans-serif';

                ctx.fillText("50%", w - 90, y - 4);

            }

        });

        ctx.setLineDash([]);

    }



    // Render LIQ Markers at liquidity sweep points

    if (typeof visualOverlays !== 'undefined' && visualOverlays && visualOverlays.liq_markers && visualOverlays.liq_markers.length > 0) {

        ctx.font = 'bold 9px sans-serif';

        ctx.textAlign = 'center';

        visualOverlays.liq_markers.forEach(liq => {

            const y = h - 20 - ((liq.price - minPrice) / priceRangePadded) * (h - 40);

            let x = w / 2;

            if (liq.time) {

                const candleIdx = candleData.findIndex(c => c.time === liq.time);

                if (candleIdx !== -1) {

                    x = chartPanX + candleIdx * (candleWidth + candleGap) + candleWidth / 2;

                }

            }

            if (y >= 0 && y < h - 20) {

                ctx.fillStyle = '#ffeb3b'; // Vivid Yellow

                ctx.fillText("liq", x, liq.type === 'LIQ_HIGH' ? y - 10 : y + 14);

            }

        });

        ctx.textAlign = 'left';

    }



    // TASK-02-70D-INTEGRATION: Liquidity pool overlays (REAL backend values

    // only — the engine's snapshot pools; absent -> no lines, chart intact).

    const liqOverlays = (typeof window.__liquidityPools !== 'undefined') ? (window.__liquidityPools || []) : [];

    if (liqOverlays.length > 0) {

        ctx.font = 'bold 8px sans-serif';

        ctx.textAlign = 'right';

        liqOverlays.forEach(pool => {

            const price = Number(pool.price);

            if (!isFinite(price)) return;

            const y = h - 20 - ((price - minPrice) / priceRangePadded) * (h - 40);

            if (y < 0 || y >= h - 20) return;

            ctx.strokeStyle = (pool.side === 1 || pool.side === 'BSL') ? 'rgba(16,185,129,0.45)' : 'rgba(244,63,94,0.45)';

            ctx.setLineDash([4, 3]);

            ctx.lineWidth = 1;

            ctx.beginPath();

            ctx.moveTo(0, y);

            ctx.lineTo(w, y);

            ctx.stroke();

            ctx.setLineDash([]);

            ctx.fillStyle = ctx.strokeStyle;

            const tag = (pool.state !== undefined && pool.state !== null) ? 'pool:' + pool.state : 'pool';

            ctx.fillText(tag, w - 4, y - 3);

        });

        ctx.textAlign = 'left';

    }



    // Draw candles within visible bounds

    candleData.forEach((candle, idx) => {

        if (idx < startIdx || idx > endIdx) return;

        const x = chartPanX + idx * (candleWidth + candleGap);



        // Translate price to Y pixels

        const yOpen = h - 20 - ((candle.open - minPrice) / priceRangePadded) * (h - 40);

        const yClose = h - 20 - ((candle.close - minPrice) / priceRangePadded) * (h - 40);

        const yHigh = h - 20 - ((candle.high - minPrice) / priceRangePadded) * (h - 40);

        const yLow = h - 20 - ((candle.low - minPrice) / priceRangePadded) * (h - 40);



        const isGreen = candle.close >= candle.open;

        // Styling matching professional TradingView layout

        const color = isGreen ? '#10b981' : '#f43f5e';



        ctx.strokeStyle = color;

        ctx.fillStyle = color;

        ctx.lineWidth = Math.max(1, candleWidth * 0.15);



        // Draw shadow wick line

        ctx.beginPath();

        ctx.moveTo(x + candleWidth / 2, yHigh);

        ctx.lineTo(x + candleWidth / 2, yLow);

        ctx.stroke();



        // Draw solid candle body

        const rectHeight = Math.max(1, Math.abs(yClose - yOpen));

        ctx.fillRect(x, Math.min(yOpen, yClose), candleWidth, rectHeight);



        // Draw light dashed border on uncompleted live forming candles

        if (candle.is_complete === false) {

            ctx.strokeStyle = '#38bdf8';

            ctx.setLineDash([2, 2]);

            ctx.strokeRect(x, Math.min(yOpen, yClose), candleWidth, rectHeight);

            ctx.setLineDash([]);

        }

    });



    // Draw Support levels

    if (typeof supportLevels !== 'undefined' && supportLevels.length > 0) {

        ctx.strokeStyle = 'rgba(16, 185, 129, 0.45)'; // semi-transparent green

        ctx.lineWidth = 1.5;

        ctx.setLineDash([3, 3]);

        supportLevels.forEach(level => {

            const y = h - 20 - ((level - minPrice) / priceRangePadded) * (h - 40);

            if (y >= 0 && y < h - 20) {

                ctx.beginPath();

                ctx.moveTo(0, y);

                ctx.lineTo(w - 60, y);

                ctx.stroke();



                // Draw label "Support [price]" on the right edge

                ctx.fillStyle = '#10b981';

                ctx.font = '8px monospace';

                ctx.fillText(`S: ${level.toFixed(2)}`, w - 110, y - 3);

            }

        });

        ctx.setLineDash([]);

    }



    // Draw Resistance levels

    if (typeof resistanceLevels !== 'undefined' && resistanceLevels.length > 0) {

        ctx.strokeStyle = 'rgba(244, 63, 94, 0.45)'; // semi-transparent red

        ctx.lineWidth = 1.5;

        ctx.setLineDash([3, 3]);

        resistanceLevels.forEach(level => {

            const y = h - 20 - ((level - minPrice) / priceRangePadded) * (h - 40);

            if (y >= 0 && y < h - 20) {

                ctx.beginPath();

                ctx.moveTo(0, y);

                ctx.lineTo(w - 60, y);

                ctx.stroke();



                // Draw label "Resistance [price]" on the right edge

                ctx.fillStyle = '#f43f5e';

                ctx.font = '8px monospace';

                ctx.fillText(`R: ${level.toFixed(2)}`, w - 110, y - 3);

            }

        });

        ctx.setLineDash([]);

    }



    // Render active order execution lines & premium interactive tooltip

    if (typeof visualOverlays !== 'undefined' && visualOverlays && visualOverlays.order_lines && visualOverlays.order_lines.active) {

        const line = visualOverlays.order_lines;

        const yEntry = h - 20 - ((line.entry_price - minPrice) / priceRangePadded) * (h - 40);

        const ySL = h - 20 - ((line.sl_price - minPrice) / priceRangePadded) * (h - 40);

        const yTP = h - 20 - ((line.tp_price - minPrice) / priceRangePadded) * (h - 40);



        if (yEntry >= 0 && yEntry < h - 20) {

            // Blue Line for entry (Blue solid)

            ctx.strokeStyle = 'rgba(0, 230, 240, 0.95)';

            ctx.lineWidth = 1.8;

            ctx.beginPath();

            ctx.moveTo(0, yEntry);

            ctx.lineTo(w - 60, yEntry);

            ctx.stroke();



            ctx.fillStyle = 'rgba(0, 230, 240, 0.95)';

            ctx.font = 'bold 9px monospace';

            ctx.fillText(`ENTRY: ${line.entry_price.toFixed(2)}`, 15, yEntry - 4);

        }



        if (ySL >= 0 && ySL < h - 20) {

            // Red Dashed Line for Stop Loss (Red dashed)

            ctx.strokeStyle = 'rgba(255, 23, 68, 0.95)';

            ctx.lineWidth = 1.8;

            ctx.setLineDash([4, 4]);

            ctx.beginPath();

            ctx.moveTo(0, ySL);

            ctx.lineTo(w - 60, ySL);

            ctx.stroke();

            ctx.setLineDash([]);



            ctx.fillStyle = 'rgba(255, 23, 68, 0.95)';

            ctx.font = 'bold 9px monospace';

            ctx.fillText(`SL: ${line.sl_price.toFixed(2)}`, 15, ySL - 4);

        }



        if (yTP >= 0 && yTP < h - 20) {

            // Green Dashed Line for Take Profit (Green dashed)

            ctx.strokeStyle = 'rgba(0, 230, 118, 0.95)';

            ctx.lineWidth = 1.8;

            ctx.setLineDash([4, 4]);

            ctx.beginPath();

            ctx.moveTo(0, yTP);

            ctx.lineTo(w - 60, yTP);

            ctx.stroke();

            ctx.setLineDash([]);



            ctx.fillStyle = 'rgba(0, 230, 118, 0.95)';

            ctx.font = 'bold 9px monospace';

            ctx.fillText(`TP: ${line.tp_price.toFixed(2)}`, 15, yTP - 4);

        }



        // Draw Interactive Tooltip beside lines

        const tooltipX = Math.max(10, w - 380);

        const tooltipY = Math.min(Math.max(yEntry + 15, 30), h - 85);



        ctx.fillStyle = 'rgba(18, 24, 38, 0.95)';

        ctx.strokeStyle = '#1e293b';

        ctx.lineWidth = 1.5;



        // Tooltip container box

        ctx.beginPath();

        if (typeof ctx.roundRect === 'function') {

            ctx.roundRect(tooltipX, tooltipY, 310, 52, 6);

        } else {

            ctx.rect(tooltipX, tooltipY, 310, 52);

        }

        ctx.fill();

        ctx.stroke();



        // Tooltip Text

        ctx.fillStyle = '#e2e8f0';

        ctx.font = 'bold 9px sans-serif';

        ctx.textAlign = 'left';

        ctx.fillText(`Order Evaluated: ${line.direction}`, tooltipX + 10, tooltipY + 16);



        ctx.fillStyle = '#94a3b8';

        ctx.font = '9px monospace';

        ctx.fillText(`Target RR: 1:${line.risk_reward_ratio.toFixed(2)} | Dollar Risk: $${line.risk_usd.toFixed(2)}`, tooltipX + 10, tooltipY + 30);

        ctx.fillText(`Potential Profit: $${line.profit_usd.toFixed(2)} | Zone Score: ${line.zone_score.toFixed(0)}%`, tooltipX + 10, tooltipY + 42);

    }



    // Draw side price scale axis labels

    ctx.fillStyle = '#475569';

    ctx.font = '9px monospace';

    ctx.textAlign = 'left';

    ctx.strokeStyle = '#1e293b';

    ctx.lineWidth = 1;

    ctx.beginPath();

    ctx.moveTo(w - 60, 0);

    ctx.lineTo(w - 60, h - 20);

    ctx.stroke();



    const numPriceLabels = 6;

    for (let i = 0; i <= numPriceLabels; i++) {

        const p = minPrice + (priceRangePadded * i) / numPriceLabels;

        const y = h - 20 - (i / numPriceLabels) * (h - 40);

        ctx.fillText(p.toFixed(2), w - 55, y + 3);

    }



    // Draw crosshair hover guides if active inside bounds

    if (crosshairX >= 0 && crosshairX < w - 60 && crosshairY >= 0 && crosshairY < h - 20) {

        ctx.strokeStyle = 'rgba(148, 163, 184, 0.25)';

        ctx.setLineDash([3, 3]);

        ctx.lineWidth = 1;



        // Horiz cross

        ctx.beginPath();

        ctx.moveTo(0, crosshairY);

        ctx.lineTo(w - 60, crosshairY);

        ctx.stroke();



        // Vert cross

        ctx.beginPath();

        ctx.moveTo(crosshairX, 0);

        ctx.lineTo(crosshairX, h - 20);

        ctx.stroke();

        ctx.setLineDash([]);

    }

}



// Update crosshair info bubble overlay details

function updateCrosshairTooltip() {

    const canvas = document.getElementById('candleChart');

    const tooltip = document.getElementById('chart-tooltip');

    if (!canvas || !tooltip || candleData.length === 0) return;



    const w = canvas.getBoundingClientRect().width;

    const h = canvas.getBoundingClientRect().height;



    if (crosshairX < 0 || crosshairX >= w - 60 || crosshairY < 0 || crosshairY >= h - 20) {

        tooltip.classList.add('hidden');

        return;

    }



    // Find the candle under mouse crosshair

    const targetIdx = Math.floor((crosshairX - chartPanX) / (candleWidth + candleGap));

    if (targetIdx >= 0 && targetIdx < candleData.length) {

        const c = candleData[targetIdx];

        const isGreen = c.close >= c.open;

        const ohlcText = `

            <div class="flex justify-between space-x-4">

                <span>${formatUTCTime(c.time)}</span>

                <span class="${isGreen ? 'text-emerald-400' : 'text-rose-400'}">${c.is_complete ? 'Completed' : 'Forming'}</span>

            </div>

            <div class="grid grid-cols-2 gap-x-3 mt-1 text-[9px] text-gray-400">

                <div>O: <span class="text-white font-bold">${c.open.toFixed(2)}</span></div>

                <div>H: <span class="text-white font-bold">${c.high.toFixed(2)}</span></div>

                <div>L: <span class="text-white font-bold">${c.low.toFixed(2)}</span></div>

                <div>C: <span class="text-white font-bold">${c.close.toFixed(2)}</span></div>

                <div>V: <span class="text-white font-bold">${c.volume}</span></div>

            </div>

        `;

        tooltip.innerHTML = ohlcText;

        tooltip.classList.remove('hidden');



        // Position tooltip bubble nicely next to mouse cursor

        tooltip.style.left = `${Math.min(w - 180, crosshairX + 15)}px`;

        tooltip.style.top = `${Math.min(h - 90, crosshairY + 15)}px`;

    } else {

        tooltip.classList.add('hidden');

    }

}



function resetChart() {

    candleWidth = 10;

    candleGap = 3;

    liveMode = true;

    updateLiveToggleUI();

    autoFitChart();

}



// Populate Broker Position Management Table

function populatePositionsTable(positions) {

    const tableBody = document.getElementById('open-positions-table');

    document.getElementById('pos-count-badge').textContent = `${positions.length} Open`;



    if (positions.length === 0) {

        tableBody.innerHTML = `

            <tr>

                <td colspan="9" class="py-6 text-center text-textMuted italic font-sans text-xs">No active positions currently registered on broker.</td>

            </tr>

        `;

        return;

    }



    tableBody.innerHTML = positions.map(pos => `

        <tr class="hover:bg-darkBg/30 transition">

            <td class="py-3.5 pl-2 font-bold text-gray-300">#${pos.ticket}</td>

            <td class="py-3.5 text-accentCyan font-bold">${pos.symbol}</td>

            <td class="py-3.5">

                <span class="px-2 py-0.5 rounded text-[11px] font-extrabold ${pos.type === 'BUY' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}">

                    ${pos.type}

                </span>

            </td>

            <td class="py-3.5 text-gray-200">${pos.volume.toFixed(2)}</td>

            <td class="py-3.5 text-gray-400">${pos.price_open.toFixed(2)}</td>

            <td class="py-3.5 text-rose-400/80">${pos.sl > 0 ? pos.sl.toFixed(2) : '-'}</td>

            <td class="py-3.5 text-emerald-400/80">${pos.tp > 0 ? pos.tp.toFixed(2) : '-'}</td>

            <td class="py-3.5 font-bold ${pos.profit >= 0 ? 'text-emerald-400' : 'text-rose-400'}">

                ${pos.profit >= 0 ? '+' : ''}$${pos.profit.toFixed(2)}

            </td>

            <td class="py-3.5 pr-2 text-right space-x-1.5 font-sans">

                <button onclick="openModifyModal(${pos.ticket}, '${pos.symbol}', ${pos.sl}, ${pos.tp})" class="bg-darkBg hover:bg-borderClr border border-borderClr text-accentCyan hover:text-cyan-300 px-2.5 py-1 rounded text-xs font-semibold transition">

                    Modify SL/TP

                </button>

                <button onclick="executeClosePosition(${pos.ticket})" class="bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 text-rose-400 px-2.5 py-1 rounded text-xs font-semibold transition">

                    Close Deal

                </button>

            </td>

        </tr>

    `).join('');

}



// Open SL/TP modifications modal popup

function openModifyModal(ticket, symbol, sl, tp) {

    document.getElementById('modify-ticket').value = ticket;

    document.getElementById('modify-ticket-display').textContent = `#${ticket}`;

    document.getElementById('modify-symbol-display').textContent = symbol;

    document.getElementById('modify-sl-input').value = sl || '';

    document.getElementById('modify-tp-input').value = tp || '';

    document.getElementById('modify-modal').classList.remove('hidden');

}



function closeModifyModal() {

    document.getElementById('modify-modal').classList.add('hidden');

}



// REST callout: submit order modifications directly to active broker

async function submitModifyPosition() {

    const ticket = parseInt(document.getElementById('modify-ticket').value);

    const sl = parseFloat(document.getElementById('modify-sl-input').value) || 0.0;

    const tp = parseFloat(document.getElementById('modify-tp-input').value) || 0.0;



    try {

        const result = await NX.api.post('/api/positions/modify', { ticket, stop_loss: sl, take_profit: tp }, { component: 'Positions', action: 'MODIFY' });

        if (result.ok && result.body.success) {

            console.log("SL/TP bracket modification successfully executed.");

            closeModifyModal();

        } else {

            alert('Execution failed: ' + NX.api.msg(result, 'Unknown error'));

        }

    } catch (err) {

        console.error("IPC failure", err);

    }

}



// REST callout: close live positions

async function executeClosePosition(ticket) {

    if (!confirm(`Are you absolutely sure you want to close position ticket #${ticket} immediately?`)) {

        return;

    }



    try {

        const result = await NX.api.post('/api/positions/close', { ticket }, { component: 'Positions', action: 'CLOSE' });

        if (result.ok && result.body.success) {

            console.log(`Live Position #${ticket} closed successfully.`);

        } else {

            alert('Execution failed: ' + NX.api.msg(result, 'Unknown error'));

        }

    } catch (err) {

        console.error("IPC failure during close", err);

    }

}



// AI Feature Selection Category Switcher

function selectFeatureCategory(category, element) {

    document.querySelectorAll('.category-btn').forEach(btn => btn.classList.remove('active'));

    if (element) {

        element.classList.add('active');

    } else if (event && event.currentTarget) {

        event.currentTarget.classList.add('active');

    }

    currentFeatureCategory = category;



    const titles = {

        'volatility': 'Volatility & Microstructure Features (Log Scale)',

        'candlestick': 'Candlestick Anatomy & Patterns',

        'patterns': 'Structure & Swing Patterns (Distance metrics)',

        'sessions': 'Market Sessions Time Lags',

        'ict': 'ICT Smart Money Concepts (FVG / Order Block)',

        'ichimoku': 'Ichimoku Kinko Hyo (Cloud conformance)',

        'multitimeframe': 'Multi-Timeframe Context & Support/Resistance Levels'

    };

    document.getElementById('feature-category-title').textContent = titles[category] || titles['volatility'];

    updateFeaturesGrid();

}



// Dynamic Grid populate for 50D AI features (real ENGINE_STATE values only)

function updateFeaturesGrid(features) {

    if (features) {

        lastFeatures = features;

    } else {

        features = lastFeatures;

    }



    const grid = document.getElementById('features-grid');

    if (!grid) return;

    if (!features || features.length === 0) {

        grid.innerHTML = '<div class="col-span-3 text-center text-textMuted italic py-8 text-xs">Awaiting live feature stream from engine\u2026</div>';

        return;

    }



    // BUG-125: update the dimension label dynamically from the features count
    const dimLabel = document.getElementById('feature-dim-label');
    if (dimLabel && features.length > 0) {
        dimLabel.textContent = features.length;
    }

    // TASK-02-70D-INTEGRATION (brief 9): three-group feature matrix header.
    // BASE 0..49 | NEWS 50..59 (news_context_v1 family) | LIQUIDITY 60..69.
    const groupHeader = document.getElementById('feature-groups');
    if (groupHeader) {
        const hasNews = features.length > 50;
        const hasLiquidity = features.length > 60;
        groupHeader.innerHTML =
            '<div class="flex flex-wrap gap-2 text-[9px] font-mono">' +
            '<span class="px-2 py-0.5 rounded bg-accentCyan/10 text-accentCyan border border-accentCyan/30">BASE 0..49 (' + Math.min(features.length, 50) + ' live)</span>' +
            (hasNews ? '<span class="px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/30">NEWS 50..59 (family slot)</span>' : '') +
            (hasLiquidity ? '<span class="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">LIQUIDITY 60..69</span>' : '') +
            '</div>';
    }

    // Filter features based on active selection category

    let activeList = [];

    if (currentFeatureCategory === 'volatility') {

        activeList = features.slice(0, 6);

    } else if (currentFeatureCategory === 'candlestick') {

        activeList = features.slice(6, 14);

    } else if (currentFeatureCategory === 'patterns') {

        activeList = features.slice(14, 20);

    } else if (currentFeatureCategory === 'sessions') {

        activeList = features.slice(20, 28);

    } else if (currentFeatureCategory === 'ict') {

        activeList = features.slice(28, 34);

    } else if (currentFeatureCategory === 'ichimoku') {

        activeList = features.slice(34, 40);

    } else if (currentFeatureCategory === 'multitimeframe') {

        activeList = features.slice(40, 50);

    } else {

        activeList = features.slice(0, 6);

    }



    grid.innerHTML = activeList.map(feat => {

        const val = feat.value;

        const valStr = (val != null) ? val.toFixed(4) : '—';

        const colorClass = (val == null) ? 'text-textMuted'

            : (val >= 1.0 ? 'text-emerald-400' : (val <= -1.0 ? 'text-rose-400' : 'text-accentCyan'));

        return `

            <div class="bg-darkBg/40 border border-borderClr/60 p-3 rounded-lg flex flex-col justify-between hover:border-borderClr transition shadow-sm">

                <span class="text-[10px] text-textMuted font-bold uppercase truncate tracking-wide">${feat.name}</span>

                <div class="flex items-baseline justify-between mt-1.5">

                    <span class="text-sm font-mono font-black ${colorClass}">${valStr}</span>

                    <span class="text-[9px] text-textMuted font-semibold">Dim ${feat.index}</span>

                </div>

            </div>

        `;

    }).join('');

}



// Update Past Signal Outcomes & Accuracy Tracking

// FORENSIC HARDENING: predictions now come from the real audit_signals ledger

// (immutable per-M1 model decisions with actual softmax probabilities). The

// old simulated_outcomes list was fabricated per-click and is no longer used.

function updatePredictionsTable() {

    const tbody = document.getElementById('prediction-vs-movement-table');

    if (!tbody) return;



    if (!predictions || predictions.length === 0) {

        tbody.innerHTML = `

            <tr>

                <td colspan="5" class="py-4 text-center text-textMuted italic font-sans">No AI predictions recorded yet (audit_signals empty — waiting for live engine decisions).</td>

            </tr>

        `;

        return;

    }



    let trueCount = 0;

    let falseCount = 0;



    tbody.innerHTML = predictions.map(p => {

        const conf = (p.confidence != null) ? (p.confidence * 100).toFixed(1) + '%' : '--';

        const probs = p.probabilities || {};

        const probsStr = (probs.no_trade != null || probs.buy != null || probs.sell != null)

            ? `NT ${(probs.no_trade != null ? (probs.no_trade * 100).toFixed(0) : '--')}% ` +

              `B ${(probs.buy != null ? (probs.buy * 100).toFixed(0) : '--')}% ` +

              `S ${(probs.sell != null ? (probs.sell * 100).toFixed(0) : '--')}%`

            : '';

        const timeStr = String(p.time || '').replace('T', ' ');



        return `

            <tr class="hover:bg-darkBg/10" title="${probsStr} ${p.reason || ''}">

                <td class="py-2 text-textMuted">${timeStr || '--'}</td>

                <td class="py-2 text-white font-bold">${p.action || '--'}</td>

                <td class="py-2 text-accentCyan">${conf}</td>

                <td class="py-2 text-textMuted text-[9px]">${(p.regime || '--').substring(0, 14)}</td>

                <td class="py-2 text-right">${p.reason ? `<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-darkBg border border-borderClr/40">${p.reason.substring(0, 18)}</span>` : ''}</td>

            </tr>

        `;

    }).join('');



    // Accuracy stats remain from live-evaluated outcomes only (audit ledger).

    document.getElementById('acc-true-signals').textContent = trueCount;

    document.getElementById('acc-false-signals').textContent = falseCount;

    document.getElementById('acc-bar').style.width = '0%';

}



// Simulate Interactive Tick Injection (real backend: /api/simulation/tick)

async function injectSimTick(type) {

    try {

        const result = await NX.api.post('/api/simulation/tick', { type }, { component: 'Simulation', action: 'INJECT_TICK' });

        if (!result.ok) {

            console.warn(NX.api.msg(result, 'Simulation dispatch failed.'));

            const el = document.getElementById('sim-status');

            if (el) el.textContent = 'Failed';

            return;

        }

        console.log(`Simulation tick of type ${type} successfully dispatched to engine pipeline.`);

        const el = document.getElementById('sim-status');

        if (el) el.textContent = "Dispatched";

        setTimeout(() => { if (el) el.textContent = "Ready"; }, 1000);

    } catch (err) {

        console.error('[UI_ERROR] component=Simulation action=INJECT_TICK', err);

    }

}



// Toggle Historical Replay status (real backend: /api/replay/toggle)

async function toggleReplay() {

    const isReplaying = document.getElementById('btn-replay-play').textContent.includes("Stop");

    const speed = parseInt(document.getElementById('replay-speed').value, 10) || 1;



    try {

        const result = await NX.api.post('/api/replay/toggle', { active: !isReplaying, speed }, { component: 'Replay', action: 'TOGGLE' });

        if (!result.ok) {

            console.warn(NX.api.msg(result, 'Replay toggle failed.'));

            return;

        }

        if (result.body.success) {

            if (!isReplaying) {

                document.getElementById('btn-replay-play').innerHTML = `<i class="fa-solid fa-stop"></i> <span>Stop Replay</span>`;

                document.getElementById('btn-replay-play').className = "bg-rose-500 hover:bg-rose-600 text-white font-bold py-1.5 px-4 rounded transition";

            } else {

                document.getElementById('btn-replay-play').innerHTML = `<i class="fa-solid fa-play"></i> <span>Start Replay</span>`;

                document.getElementById('btn-replay-play').className = "bg-accentCyan hover:bg-cyan-500 text-black font-bold py-1.5 px-4 rounded transition";

            }

        }

    } catch (err) {

        console.error('[UI_ERROR] component=Replay action=TOGGLE', err);

    }

}



// Load configurations from disk live.yaml

async function loadConfiguration() {

    try {

        const res = await fetch('/api/config');

        selectedConfig = await res.json();



        // Populate Form

        document.getElementById('cfg-exec-symbol').value = selectedConfig.execution.symbol;

        document.getElementById('cfg-exec-timeframe').value = selectedConfig.execution.timeframe;

        document.getElementById('cfg-exec-magic').value = selectedConfig.execution.magic_number;

        document.getElementById('cfg-exec-slippage').value = selectedConfig.execution.max_slippage_points;



        document.getElementById('cfg-risk-drawdown').value = selectedConfig.risk.max_account_drawdown_pct;

        document.getElementById('cfg-risk-pertrade').value = selectedConfig.risk.risk_per_trade_pct;

        document.getElementById('cfg-risk-maxpos').value = selectedConfig.risk.max_concurrent_positions;

        document.getElementById('cfg-risk-maxspread').value = selectedConfig.risk.max_spread_points;

        document.getElementById('cfg-risk-maxlots').value = selectedConfig.risk.max_allowed_lots;

        document.getElementById('cfg-risk-enforce-sl').checked = selectedConfig.risk.enforce_stop_loss;



        document.getElementById('cfg-model-threshold').value = selectedConfig.model.confidence_threshold;

        document.getElementById('cfg-model-path').value = selectedConfig.model.model_artifact_path;



        if (selectedConfig.telegram) {

            document.getElementById('cfg-telegram-enabled').checked = selectedConfig.telegram.enabled || false;

            const rawToken = selectedConfig.telegram.bot_token || '';

            const tokenField = document.getElementById('cfg-telegram-token');

            // BUG-072: server returns a MASKED token; the field must not be

            // submitted back as a real credential. Placeholder holds the mask.

            if (rawToken.indexOf('*') >= 0) {

                tokenField.value = '';

                tokenField.placeholder = rawToken;

                tokenField.dataset.masked = '1';

            } else {

                tokenField.value = rawToken;

                tokenField.dataset.masked = '';

            }

            document.getElementById('cfg-telegram-admin').value = selectedConfig.telegram.admin_id || '';

        } else {

            document.getElementById('cfg-telegram-enabled').checked = false;

            document.getElementById('cfg-telegram-token').value = '';

            document.getElementById('cfg-telegram-admin').value = '';

        }



    } catch (err) {

        console.error("Failed to load configurations", err);

    }



    try {

        const res = await fetch('/api/algo/config');

        const algoConfig = await res.json();



        document.getElementById('tuner-atr-sl-buffer').value = algoConfig.atr_sl_buffer_multiplier;

        document.getElementById('val-atr-sl-buffer').innerText = algoConfig.atr_sl_buffer_multiplier;



        document.getElementById('tuner-min-rr').value = algoConfig.min_risk_reward_ratio;

        document.getElementById('val-min-rr').innerText = algoConfig.min_risk_reward_ratio;



        document.getElementById('tuner-zone-conf').value = algoConfig.ai_zone_confidence_threshold;

        document.getElementById('val-zone-conf').innerText = algoConfig.ai_zone_confidence_threshold;



        document.getElementById('tuner-fvg-mitigation').value = algoConfig.fvg_mitigation_sensitivity;

        document.getElementById('val-fvg-mitigation').innerText = algoConfig.fvg_mitigation_sensitivity;



        document.getElementById('tuner-ob-lookback').value = algoConfig.order_block_lookback_bars;

        document.getElementById('val-ob-lookback').innerText = algoConfig.order_block_lookback_bars;

    } catch (err) {

        console.error("Failed to load algo configuration", err);

    }

}



// Save dynamic algorithm parameters back to disk and hot-swap them

async function saveAlgoTuner() {

    const updated = {

        atr_sl_buffer_multiplier: parseFloat(document.getElementById('tuner-atr-sl-buffer').value),

        min_risk_reward_ratio: parseFloat(document.getElementById('tuner-min-rr').value),

        ai_zone_confidence_threshold: parseFloat(document.getElementById('tuner-zone-conf').value),

        fvg_mitigation_sensitivity: parseFloat(document.getElementById('tuner-fvg-mitigation').value),

        order_block_lookback_bars: parseInt(document.getElementById('tuner-ob-lookback').value)

    };



    try {

        const result = await NX.api.put('/api/algo/config', updated, { component: 'Tuner', action: 'SAVE_ALGO' });

        if (result.ok && result.body.success) {

            const v = result.body.configuration_version ?? result.body.runtime_version ?? '?';

            alert(`Dynamic Algorithm thresholds APPLIED (configuration v${v}) — runtime hot-reloaded, no restart.`);

        } else {

            alert('Failed to save algorithm thresholds: ' + NX.api.msg(result, 'Unknown error'));

        }

    } catch (err) {

        console.error("Failed to save algo configurations", err);

    }

}



// Save configuration back to disk live.yaml

async function saveConfiguration() {

    const updated = {

        execution: {

            symbol: document.getElementById('cfg-exec-symbol').value,

            timeframe: document.getElementById('cfg-exec-timeframe').value,

            magic_number: parseInt(document.getElementById('cfg-exec-magic').value),

            max_slippage_points: parseInt(document.getElementById('cfg-exec-slippage').value)

        },

        risk: {

            max_account_drawdown_pct: parseFloat(document.getElementById('cfg-risk-drawdown').value),

            risk_per_trade_pct: parseFloat(document.getElementById('cfg-risk-pertrade').value),

            max_concurrent_positions: parseInt(document.getElementById('cfg-risk-maxpos').value),

            max_spread_points: parseInt(document.getElementById('cfg-risk-maxspread').value),

            max_allowed_lots: parseFloat(document.getElementById('cfg-risk-maxlots').value),

            enforce_stop_loss: document.getElementById('cfg-risk-enforce-sl').checked

        },

        model: {

            confidence_threshold: parseFloat(document.getElementById('cfg-model-threshold').value),

            model_artifact_path: document.getElementById('cfg-model-path').value,

            feature_schema_version: selectedConfig.model.feature_schema_version

        },

        telegram: {

            enabled: document.getElementById('cfg-telegram-enabled').checked,

            bot_token: resolveTelegramTokenField(),

            admin_id: document.getElementById('cfg-telegram-admin').value

        },

        mt5: selectedConfig.mt5

    };



    try {

        const result = await NX.api.post('/api/config', updated, { component: 'Config', action: 'SAVE_CONFIG' });

        if (result.ok && result.body.success) {

            const v = result.body.configuration_version ?? result.body.runtime_version ?? '?';

            if (result.body.runtime_applied === false) {

                alert('Configuration saved (v' + v + ') but NOT applied to the running engine: ' + (result.body.reason || 'engine offline'));

            } else {

                alert(`Configuration saved & APPLIED at runtime (configuration v${v}) — hot reload, no restart.`);

            }

        } else {

            // Clearer failure UX: distinguish "server unreachable" from a real

            // backend rejection so the operator knows what to do.

            if (result.status === 0 || (result.error && result.error.code === 'NETWORK_ERROR')) {

                alert('Failed to save: the web server is not reachable (network request failed).\n\nIs the engine running? Start it with:  nse run  (web UI on http://127.0.0.1:8080)\n\nRequest ID: ' + (result.error && result.error.request_id || '-'));

            } else {

                alert('Failed to save: ' + NX.api.msg(result, 'Unknown error'));

            }

        }

    } catch (err) {

        console.error("Failed to save config", err);

    }

}



// BUG-072: the server never returns the real token. When the field holds a

// masked placeholder, submitting must NOT overwrite the stored secret.

function resolveTelegramTokenField() {

    const field = document.getElementById('cfg-telegram-token');

    if (field && field.dataset && field.dataset.masked === '1') {

        return '';  // keep the existing secure-store secret untouched

    }

    return field ? field.value : '';

}



// Telegram connectivity test: sends a test message through the engine notifier.

async function testTelegram() {

    const statusEl = document.getElementById('telegram-test-status');

    if (statusEl) statusEl.textContent = 'Sending...';

    // Save the current token/admin first so the live notifier picks them up.

    try {

        const updated = {

            execution: selectedConfig.execution,

            risk: selectedConfig.risk,

            model: selectedConfig.model,

            telegram: {

                enabled: document.getElementById('cfg-telegram-enabled').checked,

                bot_token: resolveTelegramTokenField(),

                admin_id: document.getElementById('cfg-telegram-admin').value

            },

            mt5: selectedConfig.mt5

        };

        const save = await NX.api.post('/api/config', updated, { component: 'Config', action: 'SAVE_TELEGRAM' });

        if (!save.ok) {

            if (save.status === 0 || (save.error && save.error.code === 'NETWORK_ERROR')) {

                if (statusEl) statusEl.textContent = '\u274c Server unreachable \u2014 engine not running?';

                return;

            }

            if (statusEl) statusEl.textContent = '\u274c Save failed: ' + NX.api.msg(save, 'error');

            return;

        }

        const result = await NX.api.post('/api/telegram/test', {}, { component: 'Telegram', action: 'TEST' });

        if (result.ok && result.body.success) {

            const mid = result.body.message_id ? ' (message_id=' + result.body.message_id + ')' : '';

            if (statusEl) statusEl.textContent = '\u2705 Test message delivered!' + mid;

        } else if (result.status === 0 || (result.error && result.error.code === 'NETWORK_ERROR')) {

            if (statusEl) statusEl.textContent = '\u274c Server unreachable \u2014 engine not running?';

        } else {

            const errBody = result.body && result.body.error;

            const category = errBody && errBody.category ? ' [' + errBody.category + ']' : '';

            if (statusEl) statusEl.textContent = '\u274c ' + NX.api.msg(result, 'send failed') + category;

        }

    } catch (err) {

        console.error("Telegram test failed", err);

        if (statusEl) statusEl.textContent = '\u274c Error: ' + (err && err.message || err);

    }

}



// Bind telegram test button on DOMContentLoaded (idempotent).

document.addEventListener('DOMContentLoaded', function bindTelegramTest() {

    const btn = document.getElementById('btn-telegram-test');

    if (btn && !btn.dataset.bound) {

        btn.dataset.bound = '1';

        btn.addEventListener('click', testTelegram);

    }

});



// Control switch: start/stop bot thread

async function toggleEngineRunning() {

    const isStopping = document.getElementById('btn-toggle-engine').textContent.includes("Stop");



    try {

        const result = await NX.api.post('/api/engine/toggle', { active: !isStopping }, { component: 'Engine', action: 'TOGGLE' });

        if (result.ok && result.body.success) {

            console.log("Engine running state successfully toggled.");

        } else {

            console.warn('[UI_ERROR] component=Engine action=TOGGLE ' + NX.api.msg(result, 'Engine toggle failed.'));

        }

    } catch (err) {

        console.error('[UI_ERROR] component=Engine action=TOGGLE', err);

    }

}



// Update telegram bot heartbeats

async function updateHeartbeats() {

    try {

        const res = await fetch('/api/observability/stats');

        const data = await res.json();

        document.getElementById('tg-queue').textContent = `${data.tg_queue} pending`;

        document.getElementById('tg-channel').textContent = data.tg_enabled ? 'Active' : 'Disabled';

        document.getElementById('tg-status-badge').className = `w-2 h-2 rounded-full ${data.tg_enabled ? 'bg-emerald-400' : 'bg-gray-400'}`;

        // BUG-072: truthful worker state (READY/DEGRADED/STOPPED + counters)

        const tg = data.telegram;

        const liveStatus = document.getElementById('telegram-live-status');

        const workerEl = document.getElementById('tg-status-worker');

        if (tg && liveStatus && workerEl) {

            const st = tg.status || 'STOPPED';

            const color = st === 'READY' ? 'text-emerald-400' : (st === 'DEGRADED' ? 'text-amber-400' : 'text-rose-400');

            workerEl.innerHTML = `<span class="${color} font-bold">${st}</span> &middot; sent=${tg.sent_count} failed=${tg.failed_count} retries=${tg.retry_count}` +

                (tg.failure_category ? ` &middot; last_failure=<span class="text-rose-400">${tg.failure_category}</span>` : '') +

                (tg.last_success && tg.last_success !== '-' ? ` &middot; last_success=${tg.last_success}` : '');

            liveStatus.classList.remove('hidden');

        }

    } catch (err) {}

}



// Fetch all rules from database and render the UI panel dynamically

async function loadRules() {

    const t0 = performance.now();

    console.log('[UI_API] endpoint=/api/rules event=REQUEST');

    try {

        const res = await fetch('/api/rules');

        if (!res.ok) {

            console.error('[UI_ERROR] component=RuleMatrix endpoint=/api/rules status=' + res.status + ' error_code=HTTP_' + res.status);

            throw new Error('HTTP ' + res.status);

        }

        const rules = await res.json();

        console.log('[UI_API] endpoint=/api/rules event=SUCCESS status=200 latency_ms=' + Math.round(performance.now() - t0) + ' rules=' + (Array.isArray(rules) ? rules.length : 'n/a'));



        // Categorize rules

        const categorized = {};

        rules.forEach(rule => {

            if (!categorized[rule.category]) {

                categorized[rule.category] = [];

            }

            categorized[rule.category].push(rule);

        });



        const container = document.getElementById('rules-categories-container');

        if (!container) return;



        // Render Categorized Grid Panels

        container.innerHTML = Object.keys(categorized).map(catName => {

            const catRules = categorized[catName];

            return `

                <div class="border border-borderClr bg-darkBg/20 rounded-lg p-5 space-y-4">

                    <h4 class="text-sm font-black text-accentCyan tracking-wider uppercase border-b border-borderClr pb-2 flex items-center">

                        <i class="fa-solid fa-folder-open mr-2 text-accentGold"></i> ${catName}

                    </h4>

                    <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">

                        ${catRules.map(rule => renderRuleCard(rule)).join('')}

                    </div>

                </div>

            `;

        }).join('');

    } catch (err) {

        console.error('[UI_ERROR] component=RuleMatrix endpoint=/api/rules action=LOAD message=' + (err && err.message));

        const container = document.getElementById('rules-categories-container');

        if (container && !container.innerHTML.trim()) {

            container.innerHTML = '<div class="text-accentRed italic p-4 border border-accentRed/30 rounded">Failed to load rules: ' + esc(err && err.message ? String(err.message) : 'unknown error') + '</div>';

        }

    }

}



// Generate premium, responsive HTML card for individual rule control

function renderRuleCard(rule) {

    const isChecked = rule.is_enabled ? 'checked' : '';

    const params = typeof rule.parameters === 'string' ? JSON.parse(rule.parameters) : rule.parameters;

    const ruleId = rule.rule_name;



    // Build parameter input fields

    const paramInputs = Object.keys(params).map(key => {

        const val = params[key];

        const type = typeof val === 'number' ? 'number' : (typeof val === 'boolean' ? 'checkbox' : 'text');

        const step = typeof val === 'number' && !Number.isInteger(val) ? '0.01' : '1';



        if (type === 'checkbox') {

            const cbChecked = val ? 'checked' : '';

            return `

                <div class="flex items-center justify-between text-xs py-1">

                    <span class="text-gray-400 font-semibold font-mono">${key}</span>

                    <input type="checkbox" id="param-${ruleId}-${key}" ${cbChecked} class="w-3.5 h-3.5 text-accentCyan bg-darkBg border-borderClr rounded cursor-pointer">

                </div>

            `;

        } else {

            return `

                <div class="flex flex-col space-y-1 text-xs py-1">

                    <span class="text-gray-400 font-semibold font-mono">${key}</span>

                    <input type="${type}" step="${step}" id="param-${ruleId}-${key}" value="${val}" class="bg-darkBg border border-borderClr/60 text-white rounded px-2 py-1 text-[11px] font-mono focus:outline-none focus:border-accentCyan">

                </div>

            `;

        }

    }).join('');



    return `

        <div class="bg-panelBg/85 border border-borderClr/80 hover:border-borderClr p-4 rounded-xl flex flex-col justify-between space-y-3 shadow-md hover:shadow-lg hover:shadow-accentCyan/5 transition-all duration-300">

            <div class="flex items-start justify-between">

                <div class="space-y-1">

                    <span class="text-[11px] font-mono font-black text-gray-300 tracking-wider flex items-center">

                        <i class="fa-solid fa-crosshair text-accentRose mr-1.5 text-[10px]"></i> ${rule.rule_name}

                    </span>

                    <p class="text-[10px] text-textMuted font-sans">Dynamic Matrix Scalper</p>

                </div>



                <!-- Slide Toggle Switch -->

                <label class="relative inline-flex items-center cursor-pointer">

                    <input type="checkbox" id="toggle-${ruleId}" ${isChecked} onchange="toggleRuleState('${ruleId}')" class="sr-only peer">

                    <div class="w-9 h-5 bg-darkBg border border-borderClr peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-gray-400 after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-accentCyan peer-checked:after:bg-black peer-checked:after:border-black"></div>

                </label>

            </div>



            <!-- Parameters Collapsible Header -->

            <div class="bg-darkBg/40 border border-borderClr/60 p-2.5 rounded-lg space-y-2">

                <div class="text-[10px] font-bold text-accentCyan flex items-center justify-between uppercase">

                    <span>Config Parameters</span>

                    <i class="fa-solid fa-gears text-textMuted"></i>

                </div>

                <div class="space-y-1.5">

                    ${paramInputs || '<span class="text-[10px] text-textMuted italic">No configurable parameters.</span>'}

                </div>

            </div>



            <!-- Action Save Bar -->

            <div class="flex items-center justify-end pt-1">

                <button onclick="saveRuleParameters('${ruleId}', '${encodeURIComponent(JSON.stringify(params))}')" class="text-[10px] bg-accentCyan/10 hover:bg-accentCyan/20 text-accentCyan hover:text-cyan-300 px-2.5 py-1 rounded font-bold border border-accentCyan/20 transition flex items-center space-x-1">

                    <i class="fa-solid fa-floppy-disk"></i> <span>Save Parameters</span>

                </button>

            </div>

        </div>

    `;

}



// Toggle enabled status of a specific rule dynamically

async function toggleRuleState(ruleId) {

    const isEnabled = document.getElementById(`toggle-${ruleId}`).checked;



    try {

        const result = await NX.api.post('/api/rules/toggle', {

            rule_name: ruleId,

            is_enabled: isEnabled,

            parameters: null

        }, { component: 'Rules', action: 'TOGGLE' });

        if (result.ok && result.body.success) {

            console.log(`Rule ${ruleId} has been successfully toggled to ${isEnabled}.`);

        } else {

            alert('Failed to toggle rule state: ' + NX.api.msg(result, 'Unknown error'));

        }

    } catch (err) {

        console.error('[UI_ERROR] component=Rules action=TOGGLE', err);

    }

}



// Update specific rule parameters on disk/db

async function saveRuleParameters(ruleId, originalParamsEncoded) {

    const originalParams = JSON.parse(decodeURIComponent(originalParamsEncoded));

    const isEnabled = document.getElementById(`toggle-${ruleId}`).checked;



    const updatedParams = {};

    Object.keys(originalParams).forEach(key => {

        const originalVal = originalParams[key];

        const inputElement = document.getElementById(`param-${ruleId}-${key}`);

        if (!inputElement) return;



        if (typeof originalVal === 'boolean') {

            updatedParams[key] = inputElement.checked;

        } else if (typeof originalVal === 'number') {

            updatedParams[key] = Number(inputElement.value);

        } else {

            updatedParams[key] = inputElement.value;

        }

    });



    try {

        const res = await fetch('/api/rules/toggle', {

            method: 'POST',

            headers: { 'Content-Type': 'application/json' },

            body: JSON.stringify({

                rule_name: ruleId,

                is_enabled: isEnabled,

                parameters: updatedParams

            })

        });

        const result = await res.json();

        if (result.success) {

            alert(`Parameters for ${ruleId} successfully updated & saved dynamically!`);

            loadRules(); // reload to refresh Original params state encoded

        } else {

            alert("Failed to save parameters.");

        }

    } catch (err) {

        console.error("Failed to save rule parameters", err);

    }

}



/* =========================================================================

 * PHASE 08: ACCOUNT PERFORMANCE & INTELLIGENCE PANEL

 * All numbers are fetched from the canonical AccountingCore REST endpoints.

 * There is NO synthetic fallback: unavailable data renders explicit states.

 * ========================================================================= */



function acctLineChart(canvasId, emptyId, labels, series, color, fmt) {

    const canvas = document.getElementById(canvasId);

    const empty = document.getElementById(emptyId);

    if (!canvas) return;

    // Hidden-tab guard: canvases inside a hidden tab report 0 size and draw

    // nothing. Fall back to the attribute height so the chart still renders;

    // the tab-switch path re-runs the loaders once visible anyway.

    if (canvas.getBoundingClientRect().height === 0) {

        canvas.style.height = (canvas.getAttribute('height') || 180) + 'px';

    }

    if (!labels || labels.length === 0 || !series || series.every(v => v == null)) {

        if (empty) empty.classList.remove('hidden');

        canvas.style.display = 'none';

        return;

    }

    if (empty) empty.classList.add('hidden');

    canvas.style.display = 'block';

    const ctx = canvas.getContext('2d');

    const dpr = window.devicePixelRatio || 1;

    const rect = canvas.getBoundingClientRect();

    canvas.width = rect.width * dpr;

    canvas.height = rect.height * dpr;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const w = rect.width || 300;

    const h = rect.height || 180;

    ctx.clearRect(0, 0, w, h);

    ctx.fillStyle = '#090d16';

    ctx.fillRect(0, 0, w, h);



    const padL = 52, padR = 12, padT = 14, padB = 26;

    const values = series.filter(v => v != null).map(Number);

    if (values.length === 0) return;

    const min = Math.min(...values), max = Math.max(...values);

    const range = (max - min) || 1;

    const lo = min - range * 0.08, hi = max + range * 0.08;

    const px = i => padL + (i / (labels.length - 1 || 1)) * (w - padL - padR);

    const py = v => padT + (1 - (v - lo) / (hi - lo)) * (h - padT - padB);



    ctx.strokeStyle = '#121826';

    ctx.fillStyle = '#64748b';

    ctx.font = '9px monospace';

    ctx.lineWidth = 1;

    for (let g = 0; g <= 4; g++) {

        const v = lo + (hi - lo) * g / 4;

        const y = py(v);

        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();

        ctx.textAlign = 'right';

        ctx.fillText(fmt ? fmt(v) : v.toFixed(2), padL - 4, y + 3);

    }

    ctx.strokeStyle = color || '#22d3ee';

    ctx.lineWidth = 1.6;

    ctx.beginPath();

    series.forEach((v, i) => {

        if (v == null) return;

        const x = px(i), y = py(Number(v));

        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);

    });

    ctx.stroke();

    const lastIdx = series.map((v, i) => v == null ? -1 : i).filter(i => i >= 0).pop();

    if (lastIdx != null) {

        ctx.fillStyle = color || '#22d3ee';

        ctx.beginPath();

        ctx.arc(px(lastIdx), py(Number(series[lastIdx])), 2.5, 0, Math.PI * 2);

        ctx.fill();

    }

    ctx.textAlign = 'center';

    const step = Math.max(1, Math.floor(labels.length / 6));

    labels.forEach((lab, i) => {

        if (i % step !== 0) return;

        ctx.fillText(String(lab), px(i), h - 8);

    });

}



function acctFmtMoney(v) {

    if (v == null) return '--';

    const n = Number(v);

    return (n >= 0 ? '+' : '-') + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2 });

}



function acctFmtPct(v) {

    if (v == null) return '--';

    return Number(v).toFixed(2) + '%';

}



function acctFmtNum(v, digits) {

    if (v == null) return '--';

    return Number(v).toFixed(digits == null ? 2 : digits);

}



// =============================================================================

// MODEL GOVERNANCE PANEL (TASK-6) — canonical governance API consumer

// =============================================================================

async function loadGovernancePanel() {

    const results = await Promise.allSettled([

        NX.api.get('/api/models/governance/health', { component: 'Governance', action: 'LOAD_HEALTH' }),

        NX.api.get('/api/models/governance/registry', { component: 'Governance', action: 'LOAD_REGISTRY' }),

        NX.api.get('/api/models/governance/events', { component: 'Governance', action: 'LOAD_EVENTS' })

    ]);

    let health = null, registry = null, events = [];

    if (results[0].status === 'fulfilled' && results[0].value.ok) health = results[0].value.body.health;

    if (results[1].status === 'fulfilled' && results[1].value.ok) registry = results[1].value.body.registry;

    if (results[2].status === 'fulfilled' && results[2].value.ok) {

        const body = results[2].value.body;

        events = Array.isArray(body.events) ? body.events.slice(0, 20) : [];

    }

    renderGovernanceHealth(health);

    renderGovernanceRegistry(registry);

    renderGovernanceEvents(events);

    loadPromotionStatus();

    if (health) {

        const st = document.getElementById('gov-nav-state');

        if (st) {

            const sh = health.shadow || {};

            st.textContent = sh.running ? 'ON' : 'OFF';

            st.className = 'ml-auto text-[9px] font-black px-1.5 py-0.5 rounded border ' + (sh.running ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' : 'bg-slate-500/20 text-slate-300 border-slate-500/30');

        }

    }

}



function renderGovernanceHealth(health) {

    const champ = document.getElementById('gov-champ-health');

    const chalState = document.getElementById('gov-chal-state');

    if (!health) {

        if (champ) champ.textContent = 'NO DATA';

        if (chalState) chalState.textContent = '--';

        return;

    }

    const c = health.champion || {};

    const k = health.challenger || {};

    const s = health.shadow || {};

    if (champ) {

        champ.textContent = c.healthy ? 'HEALTHY' : 'DEGRADED';

        champ.className = 'text-[10px] font-black px-2 py-1 rounded uppercase ' + (c.healthy ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-rose-500/20 text-rose-300 border border-rose-500/30');

    }

    if (chalState) chalState.textContent = k.state || 'NONE';

}



function renderGovernanceRegistry(registry) {

    const champBody = document.getElementById('gov-champ-body');

    const chalBody = document.getElementById('gov-chal-body');

    const shadowBody = document.getElementById('gov-shadow-body');

    const latencyBody = document.getElementById('gov-latency-body');

    const promoBody = document.getElementById('gov-promo-body');

    if (!registry) {

        ['gov-champ-body', 'gov-chal-body', 'gov-shadow-body', 'gov-latency-body', 'gov-promo-body'].forEach(function (id) {

            const el = document.getElementById(id);

            if (el) el.innerHTML = '<div class=\'text-textMuted italic\'>No data</div>';

        });

        return;

    }

    const cats = registry.categories || {};

    const champ = cats.CURRENT_CHAMPION || {};

    const chal = cats.CURRENT_CHALLENGER || {};

    const verify = registry.champion_verification || {};

    const gate = (verify.load_gate || {});

    if (champBody) {

        champBody.innerHTML = (

            '<div><span class=\'text-textMuted\'>ID   </span>' + escHtml(champ.model_id || '?') + ' @ ' + escHtml(champ.version || '?') + '</div>' +

            '<div><span class=\'text-textMuted\'>Schema</span> ' + escHtml(champ.schema_id || '?') + ' / ' + (champ.input_dimension || 0) + 'D</div>' +

            '<div><span class=\'text-textMuted\'>Hash  </span>' + escHtml((verify.hash || champ.artifact_hash || '?').slice(0, 16)) + '</div>' +

            '<div><span class=\'text-textMuted\'>Life  </span>' + escHtml(champ.lifecycle_state || '?') + '  (gate: ' + (gate.passed ? 'PASS' : 'FAIL/' + escHtml((gate.failing_gate || '?'))) + ')</div>'

        );

    }

    if (chalBody) {

        if (chal && chal.model_id) {

            chalBody.innerHTML = (

                '<div><span class=\'text-textMuted\'>ID   </span>' + escHtml(chal.model_id) + ' @ ' + escHtml(chal.version || '?') + '</div>' +

                '<div><span class=\'text-textMuted\'>Schema</span> ' + escHtml(chal.schema_id || '?') + ' / ' + (chal.input_dimension || 0) + 'D</div>' +

                '<div><span class=\'text-textMuted\'>Life  </span>' + escHtml(chal.lifecycle_state || '?') + '</div>'

            );

        } else {

            chalBody.innerHTML = '<div class=\'text-textMuted italic\'>No validated challenger attached</div>';

        }

    }

    const sh = registry.shadow || {};

    if (shadowBody) {

        shadowBody.innerHTML = (

            '<div>comparisons ' + (sh.comparisons != null ? sh.comparisons : '--') + '</div>' +

            '<div>errors      ' + (sh.errors != null ? sh.errors : '--') + '</div>' +

            '<div>dropped     ' + (sh.dropped != null ? sh.dropped : '--') + '</div>'

        );

    }

    if (latencyBody) {

        latencyBody.innerHTML = (

            '<div>avg ' + (sh.avg_latency_ms != null ? sh.avg_latency_ms : '--') + ' ms</div>' +

            '<div>p95 ' + (sh.p95_latency_ms != null ? sh.p95_latency_ms : '--') + ' ms</div>'

        );

    }

    if (promoBody) {

        promoBody.innerHTML = '<div class=\'text-accentGold font-bold\'>' + escHtml((cats.SHADOW && cats.SHADOW.lifecycle_state) || 'SHADOW') + '</div><div class=\'text-textMuted\'>NO AUTO PROMOTION</div>';

    }

}



function renderGovernanceEvents(events) {

    const body = document.getElementById('gov-events-body');

    if (!body) return;

    if (!events || !events.length) {

        body.innerHTML = '<div class=\'text-textMuted italic\'>No governance events yet</div>';

        return;

    }

    body.innerHTML = events.map(function (e) {

        return '<div class=\'flex justify-between gap-2\'><span class=\'text-accentCyan\'>' + escHtml(e.event || '') + '</span><span>' + escHtml(String(e.model_id || '').slice(0, 24)) + '</span><span class=\'text-textMuted\'>' + escHtml((e.timestamp || '').slice(0, 19)) + '</span></div>';

    }).join('');

}



// =============================================================================

// PROMOTION CONTROLS (TASK-08 70D governance) — preview + explicit approval flow

// =============================================================================

async function showPromotionPreview() {

    const modelId = (document.getElementById('gov-promo-candidate') || {}).value || '';

    if (!modelId) {

        console.warn('[UI_ERROR] component=Governance action=PROMOTION_PREVIEW missing model_id');

        return;

    }

    const result = await NX.api.get('/api/models/governance/promotion-preview?model_id=' + encodeURIComponent(modelId), { component: 'Governance', action: 'PROMOTION_PREVIEW' });

    const body = document.getElementById('gov-promo-preview');

    if (!body) return;

    if (!result.ok || !result.body || !result.body.preview) {

        body.innerHTML = '<div class=\'text-rose-400\'>' + escHtml(NX.api.msg(result, 'Preview failed')) + '</div>';

        return;

    }

    const p = result.body.preview;

    const g = p.gates || {};

    const v = p.verification || {};

    const champ = p.current_champion || {};

    const cand = p.candidate || {};

    const roll = p.rollback || {};

    const gateRow = function (name, st) {

        const cls = st === 'PASS' ? 'text-emerald-300' : st === 'FAIL' ? 'text-rose-300' : 'text-amber-300';

        return '<div><span class=\'text-textMuted\'>' + name + '</span> <span class=\'' + cls + ' font-black\'>' + escHtml(st || 'UNKNOWN') + '</span></div>';

    };

    body.innerHTML =

        '<div class=\'text-accentGold font-black\'>PROMOTION PREVIEW (read-only)</div>' +

        '<div><span class=\'text-textMuted\'>Champion</span> ' + escHtml(champ.model_id || '?') + ' @ ' + escHtml(champ.version || '?') +

            ' <span class=\'text-textMuted\'>hash</span> ' + escHtml((champ.artifact_hash || '').slice(0, 12) || '?') + '</div>' +

        '<div><span class=\'text-textMuted\'>Candidate</span> ' + escHtml(cand.model_id || '?') + ' @ ' + escHtml(cand.version || '?') +

            ' <span class=\'text-textMuted\'>schema</span> ' + escHtml(cand.schema || '?') + '</div>' +

        '<div><span class=\'text-textMuted\'>Schema</span> champion=' + escHtml((p.schema || {}).champion || '?') + ' candidate=' + escHtml((p.schema || {}).candidate || '?') + '</div>' +

        '<div class=\'mt-1\'>' + gateRow('OOS', g.oos) + gateRow('Robustness', g.robustness) + gateRow('Shadow', g.shadow) + gateRow('Drift', g.drift) + gateRow('Liquidity', g.liquidity) + '</div>' +

        '<div><span class=\'text-textMuted\'>Rollback</span> ' + (roll.available ? 'AVAILABLE -> ' + escHtml(roll.target || '') : 'NONE') + '</div>' +

        '<div><span class=\'text-textMuted\'>Eligible</span> <span class=\'' + (v.eligible ? 'text-emerald-300' : 'text-rose-300') + ' font-black\'>' + (v.eligible ? 'YES' : 'NO') + '</span>' +

            (v.reason ? ' <span class=\'text-textMuted\'>' + escHtml(v.reason) + '</span>' : '') + '</div>' +

        (p.locked ? '<div class=\'text-amber-300\'>WAIT: another promotion is currently in progress (lock held)</div>' : '');

}



async function executePromotionUI() {

    const modelId = (document.getElementById('gov-promo-candidate') || {}).value || '';

    const token = (document.getElementById('gov-promo-token') || {}).value || '';

    if (!modelId || !token) {

        console.warn('[UI_ERROR] component=Governance action=PROMOTION_EXECUTE missing model_id/token');

        return;

    }

    if (!confirm('PROMOTION IS FINAL AND AUDITED.\n\nPromote ' + modelId + ' to Champion?\n\nThis requires explicit operator approval and records an immutable audit event.')) {

        return;

    }

    const actor = prompt('Operator identity (required for the audit trail):') || '';

    if (!actor) {

        console.warn('[UI_ERROR] component=Governance action=PROMOTION_EXECUTE missing actor');

        return;

    }

    const result = await NX.api.post('/api/models/promotion/execute', {

        actor: actor,

        model_id: modelId,

        model_version: '',

        reason: 'operator promotion via governance UI',

        approval_token: token,

        old_champion_model_id: '',

        old_champion_version: '',

        old_champion_hash: '',

        old_champion_schema: ''

    }, { component: 'Governance', action: 'PROMOTION_EXECUTE' });

    const body = document.getElementById('gov-promo-preview');

    if (!result.ok) {

        if (body) body.innerHTML = '<div class=\'text-rose-400\'>PROMOTION BLOCKED: ' + escHtml(NX.api.msg(result, 'blocked')) + '</div>';

        return;

    }

    if (body) body.innerHTML = '<div class=\'text-emerald-300 font-black\'>PROMOTION COMMITTED — audit record ' + escHtml((result.body.promotion || {}).promotion_id || '?') + '</div>';

    loadGovernancePanel();

}



async function freezePromotionsUI() {

    const actor = prompt('Operator identity:') || '';

    if (!actor) return;

    const result = await NX.api.post('/api/models/governance/emergency/freeze', { actor: actor, reason: 'operator freeze from UI' }, { component: 'Governance', action: 'PROMOTION_FREEZE' });

    if (result.ok) loadGovernancePanel();

}



async function unfreezePromotionsUI() {

    const actor = prompt('Operator identity:') || '';

    if (!actor) return;

    const result = await NX.api.post('/api/models/governance/emergency/unfreeze', { actor: actor, reason: 'operator unfreeze from UI' }, { component: 'Governance', action: 'PROMOTION_UNFREEZE' });

    if (result.ok) loadGovernancePanel();

}



async function loadPromotionStatus() {

    const result = await NX.api.get('/api/models/governance/status', { component: 'Governance', action: 'LOAD_STATUS' });

    const el = document.getElementById('gov-promo-freeze');

    if (!el) return;

    if (!result.ok || !result.body || !result.body.available) {

        el.textContent = 'UNAVAILABLE';

        return;

    }

    const promo = result.body.promotion || {};

    if (promo.frozen) {

        el.textContent = 'PROMOTION FROZEN';

        el.className = 'text-[10px] font-black px-2 py-1 rounded uppercase border bg-rose-500/20 text-rose-300 border-rose-500/30';

    } else {

        el.textContent = 'PROMOTIONS ENABLED';

        el.className = 'text-[10px] font-black px-2 py-1 rounded uppercase border bg-emerald-500/20 text-emerald-300 border-emerald-500/30';

    }

}





async function reconcileRegistry() {

    const result = await NX.api.post('/api/models/registry/reconcile', {}, { component: 'Governance', action: 'RECONCILE' });

    if (!result.ok) {

        console.warn('[UI_ERROR] component=Governance action=RECONCILE ' + NX.api.msg(result, 'Reconcile failed'));

        return;

    }

    renderGovernanceRegistry(result.body.registry);

}



function escHtml(s) {

    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

}



async function loadAccountPerformance() {

    try {

        const res = await fetch('/api/account/performance');

        if (!res.ok) return;

        const data = await res.json();

        if (!data.available) return;



        if (data.live) {

            const setText = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };

            if (data.live.balance != null) setText('acc-balance', '$' + Number(data.live.balance).toLocaleString('en-US', { minimumFractionDigits: 2 }));

            if (data.live.equity != null) setText('acc-equity', '$' + Number(data.live.equity).toLocaleString('en-US', { minimumFractionDigits: 2 }));

            if (data.live.floating_pnl != null) setText('acc-floating', acctFmtMoney(data.live.floating_pnl));

            if (data.live.margin_free != null) setText('acc-margin-free', '$' + Number(data.live.margin_free).toLocaleString('en-US', { minimumFractionDigits: 2 }));

            if (data.live.open_positions != null) setText('acc-open-positions', String(data.live.open_positions));

        }

        if (data.drawdown && data.drawdown.has_data && data.drawdown.current_drawdown_pct != null) {

            const el = document.getElementById('acc-drawdown');

            if (el) el.textContent = Number(data.drawdown.current_drawdown_pct).toFixed(2) + '%';

        }

        if (data.totals && data.totals.win_rate != null) {

            const el = document.getElementById('acc-winrate');

            if (el) el.textContent = Number(data.totals.win_rate).toFixed(1) + '%';

        }

    } catch (err) {

        console.error('Account performance load failed', err);

    }

}



// Advanced risk metrics (Sharpe/Sortino/Calmar/SQN/... from accounting core).

// =============================================================================

// PHASE 16b (UX v2): PERFORMANCE INTELLIGENCE ENGINE

// Verve-coded summary lines + deep scenario analysis. Every claim derives from

// real aggregates (a = data.advanced from the accounting core); missing stats

// produce a neutral hint, never a fabricated claim. Numbers are real; the

// prose is the audit trail made readable.

// =============================================================================



function acctNum(v, d) {

    return v == null ? '--' : Number(v).toFixed(d == null ? 2 : d);

}

function acctR(v) {

    return v == null ? '--' : Number(v).toFixed(3) + 'R';

}

function acctPctV(v, d) {

    return v == null ? '--' : Number(v).toFixed(d == null ? 2 : d) + '%';

}

function acctPct1(v) {

    return v == null ? '--' : (Number(v) * 100).toFixed(1) + '%';

}

function acctTone(v) {

    return v == null ? 'text-gray-400' : (v >= 0 ? 'text-emerald-400' : 'text-rose-400');

}

function acctChip(txt, cls) {

    return '<span class="inline-block text-[9px] uppercase tracking-widest font-bold rounded px-1.5 py-0.5 ' + (cls || 'bg-accentCyan/10 text-accentCyan') + '">' + txt + '</span>';

}

function acctBarRow(label, pct, cls, valStr) {

    const w = Math.max(2, Math.min(100, Number(pct) || 0));

    return '<div class="flex items-center gap-2 text-[10px] font-mono py-0.5">' +

        '<span class="w-40 truncate text-textMuted shrink-0">' + label + '</span>' +

        '<div class="flex-1 h-1.5 rounded-full bg-darkBg overflow-hidden">' +

        '<div class="h-full rounded-full ' + cls + '" style="width:' + w + '%"></div></div>' +

        '<span class="w-28 text-right shrink-0 text-gray-300">' + (valStr || '') + '</span></div>';

}



function toggleAccountIntelMode(btn) {

    const deep = document.getElementById('acct-intel-deep');

    if (!deep) return;

    const showing = !deep.classList.contains('hidden');

    deep.classList.toggle('hidden');

    window.__acctIntelDeepVisible = deep.classList.contains('hidden') ? false : true;

    if (btn) {

        btn.innerHTML = showing

            ? '<i class="fa-solid fa-microscope mr-1"></i>Deep Analysis'

            : '<i class="fa-solid fa-compress mr-1"></i>Hide Analysis';

    }

}



function renderAccountIntelTexts(a, data) {

    const box = document.getElementById('acct-intel-texts');

    if (!box) return;

    const lines = [];

    const add = (txt, cls) => lines.push(

        '<div class="flex items-start gap-2 py-0.5">' +

        '<span class="text-accentCyan mt-0.5 text-[10px] w-3 text-center shrink-0">&#9656;</span>' +

        '<span class="' + (cls || 'text-textMuted') + '">' + txt + '</span></div>');

    const denom = (a.win_rate_denominator || 'NONE').toLowerCase();



    // 1. Win-rate & loss-rate reconciliation (the core debug tool).

    if (a.win_rate != null && a.loss_rate_decided != null) {

        const wr = Number(a.win_rate);

        const verdict = wr >= 50 ? 'count edge confirmed'

            : (wr >= 40 ? 'counts are marginal' : 'counts are bleeding');

        const cls = wr >= 50 ? 'text-emerald-400' : (wr >= 40 ? 'text-amber-400' : 'text-rose-400');

        add('Win rate <b class="' + cls + '">' + acctPctV(a.win_rate) + '</b> vs loss rate <b class="text-rose-400">' +

            acctPctV(a.loss_rate_decided) + '</b> (decided, denominator "' + denom + '") &mdash; ' + verdict + '. ' +

            'Scratches included: win-rate-all <b>' + acctPctV(a.win_rate_all) + '</b>, loss-rate-all <b class="text-rose-400">' +

            acctPctV(a.loss_rate_all) + '</b>.', 'text-textMuted');

    }

    if (a.pnl_weighted_win_rate != null) {

        const pw = Number(a.pnl_weighted_win_rate);

        add('PnL-weighted win rate <b class="' + (pw >= 50 ? 'text-emerald-400' : 'text-rose-400') + '">' + acctPctV(pw) + '</b> &mdash; ' +

            'the dollar-weighted counterpart of the trade-count win rate' +

            (a.win_rate != null ? ' (' + (pw > a.win_rate ? 'profits concentrate in winners' : 'wins are small, losses heavy') + ').' : '.'), 'text-textMuted');

    }

    if (a.total_costs != null && a.net_pnl != null) {

        add('Cost drag ' + acctFmtMoney(a.total_costs) + ' (comm + swap) = ' +

            (a.cost_drag_pct != null ? acctPctV(a.cost_drag_pct) : 'n/a') + ' of gross profit; net ' +

            acctFmtMoney(a.net_pnl) + '.', Number(a.total_costs) > 0 ? 'text-amber-400' : 'text-textMuted');

    }

    if (a.stop_loss_share != null) {

        const pct = (a.stop_loss_share * 100).toFixed(1);

        if (a.stop_loss_share >= 0.7) {

            add(pct + '% of losses exited at a protective stop &mdash; stop discipline is doing the closing (' +

                (a.avg_loss_r != null ? 'avg loss ' + acctNum(a.avg_loss_r, 3) + 'R' : 'avg loss R n/a') + ').', 'text-emerald-400');

        } else {

            add('Only ' + pct + '% of losses exited at a stop &mdash; losers may be bleeding out via manual/emergency/strategy exits (' +

                (a.avg_loss_r != null ? 'avg loss <b>' + acctNum(a.avg_loss_r, 3) + 'R</b>' : 'avg loss R n/a') + ').', 'text-amber-400');

        }

    }

    if (a.avg_mae_r != null && a.avg_mfe_r != null) {

        add('Avg adverse excursion ' + acctR(a.avg_mae_r) + ' vs avg favourable excursion ' + acctR(a.avg_mfe_r) + ' &mdash; ' +

            (a.avg_mae_r > a.avg_mfe_r ? 'the book fights the market before it works.' : 'the book generally works before it fights.'), 'text-textMuted');

    }

    if (a.loss_efficiency_pct != null) {

        add('Losers gave back ~' + acctNum(a.loss_efficiency_pct, 1) + '% of their peak favourable excursion before closing red.', 'text-textMuted');

    }

    if (a.expectancy_breakeven_incl != null) {

        const e = Number(a.expectancy_breakeven_incl);

        add('Expectancy incl. breakevens <b class="' + (e >= 0 ? 'text-emerald-400' : 'text-rose-400') + '">' + acctFmtMoney(e) +

            '</b> per trade (' + a.sample_trades + ' closed). ' +

            (e >= 0 ? 'Positive on raw money.' : 'Negative on raw money &mdash; costs + giveback exceed edge.'),

            e >= 0 ? 'text-emerald-400' : 'text-rose-400');

    }

    if (lines.length === 0) {

        add('Not enough closed trades for performance intelligence yet.', 'text-textMuted');

    }

    box.innerHTML = lines.join('');

}



// ============================ DEEP ANALYSIS ================================

// renderAccountDeepAnalysis(a, data) — builds the "Deep Analysis" block.

// Every section derives from real aggregates; thresholds are honest heuristics.

// ============================ SECTION A: VERDICT ============================



function acctVerdict(a) {

    // Overall tone from the two most load-bearing facts: expectancy sign

    // (decided denominator) and stop-loss discipline share.

    if (a.expectancy == null && a.net_pnl == null) {

        return { label: 'NEUTRAL', text: 'Not enough evidence for a verdict yet.', cls: 'text-gray-400', bar: 'bg-gray-500' };

    }

    const exp = a.expectancy != null ? Number(a.expectancy) : (Number(a.net_pnl) / Math.max(1, a.sample_trades));

    const stop = a.stop_loss_share != null ? Number(a.stop_loss_share) : null;

    const wr = a.win_rate != null ? Number(a.win_rate) : 0;

    const lose = exp < 0;

    const undisciplined = stop != null && stop < 0.7;

    if (lose && undisciplined) {

        return { label: 'BLEEDING', text: 'Negative expectancy AND weak stop discipline — the two compounding problems are both live.',

            cls: 'text-rose-400', bar: 'bg-rose-500' };

    }

    if (lose) {

        return { label: 'LOSING EDGE', text: 'Negative expectancy: every decided trade erodes equity on average (costs + giveback included).',

            cls: 'text-rose-400', bar: 'bg-rose-500' };

    }

    if (undisciplined) {

        return { label: 'FRAGILE', text: 'Positive expectancy but losers rarely end at a stop — the edge depends on manual mercy, not the risk system.',

            cls: 'text-amber-400', bar: 'bg-amber-500' };

    }

    if (wr >= 50 && exp > 0) {

        return { label: 'CONFIRMED', text: 'Positive expectancy with a count edge — the classic profile of a system that works as designed.',

            cls: 'text-emerald-400', bar: 'bg-emerald-500' };

    }

    return { label: 'GRINDING', text: 'Positive expectancy despite a low win rate — the payoff profile carries the book.', cls: 'text-sky-400', bar: 'bg-sky-500' };

}



// ==================== SECTION B: EDGE REALITY (builders) ====================



function acctSecEdge(a) {

    const wins = a.win_rate != null ? Number(a.win_rate) : null;

    const loss = a.loss_rate_decided != null ? Number(a.loss_rate_decided) : null;

    const wrAll = a.win_rate_all != null ? Number(a.win_rate_all) : null;

    const lossAll = a.loss_rate_all != null ? Number(a.loss_rate_all) : null;

    const pnlw = a.pnl_weighted_win_rate != null ? Number(a.pnl_weighted_win_rate) : null;

    const rows = [];

    if (wins != null && loss != null) {

        rows.push(acctBarRow('wins (decided)', wins, 'bg-emerald-500', acctPctV(wins)));

        rows.push(acctBarRow('losses (decided)', loss, 'bg-rose-500', acctPctV(loss)));

    }

    if (wrAll != null && lossAll != null) {

        rows.push(acctBarRow('wins (all incl. scratches)', wrAll, 'bg-emerald-500/60', acctPctV(wrAll)));

        rows.push(acctBarRow('losses (all)', lossAll, 'bg-rose-500/60', acctPctV(lossAll)));

    }

    if (pnlw != null) {

        rows.push(acctBarRow('PnL-share from winners', pnlw, 'bg-accentCyan', acctPctV(pnlw)));

    }

    if (!rows.length) {

        return '<div class="text-textMuted italic text-xs">No closed-trade sample for rate mathematics yet.</div>';

    }



    // Expectancy waterfall as bar segments: gross profit vs gross loss vs costs.

    let waterfall = '';

    if (a.gross_profit != null && a.gross_loss != null) {

        const gp = Math.max(0, Number(a.gross_profit));

        const gl = Math.max(0, Number(a.gross_loss));

        const costs = a.total_costs != null ? Math.max(0, Number(a.total_costs)) : 0;

        const tot = (gp + gl + costs) || 1;

        const wGp = Math.round(gp / tot * 100), wGl = Math.round(gl / tot * 100), wC = Math.max(0, 100 - wGp - wGl);

        waterfall = '<div class="mt-2">' +

            '<div class="flex h-2.5 rounded-full overflow-hidden bg-darkBg">' +

            '<div class="bg-emerald-500" style="width:' + wGp + '%" title="gross profit"></div>' +

            '<div class="bg-rose-500" style="width:' + wGl + '%" title="gross loss"></div>' +

            '<div class="bg-amber-400" style="width:' + wC + '%" title="costs"></div></div>' +

            '<div class="flex flex-wrap gap-x-4 gap-y-0.5 mt-1 text-[10px] font-mono text-textMuted">' +

            '<span><span class="text-emerald-400">&#9632;</span> gross profit ' + acctFmtMoney(gp) + '</span>' +

            '<span><span class="text-rose-400">&#9632;</span> gross loss ' + acctFmtMoney(gl) + '</span>' +

            '<span><span class="text-amber-400">&#9632;</span> costs ' + acctFmtMoney(costs) + '</span></div></div>';

    }



    // R-based edge column.

    const rCells = [

        ['Avg R', acctR(a.avg_r), acctTone(a.avg_r)],

        ['Avg Win R (realized)', a.avg_r_multiple != null ? acctNum(a.avg_r_multiple, 3) + 'R' : '--', a.avg_r_multiple != null && a.avg_r_multiple > 0 ? 'text-emerald-400' : 'text-gray-400'],

        ['Avg Loss R (realized)', a.avg_loss_r != null ? acctNum(a.avg_loss_r, 3) + 'R' : '--', a.avg_loss_r != null && a.avg_loss_r < 0 ? 'text-rose-400' : 'text-gray-400'],

        ['R sample coverage', a.r_coverage_ratio != null ? acctPct1(a.r_coverage_ratio) : '--', 'text-gray-300'],

        ['Payoff ratio', acctNum(a.payoff_ratio, 2), acctTone(a.payoff_ratio != null ? Number(a.payoff_ratio) - 1 : null)],

    ];

    const rHtml = '<div class="grid grid-cols-2 gap-2">' +

        rCells.map(c => '<div class="bg-darkBg/50 rounded-md px-2 py-1.5 border border-borderClr/40">' +

            '<span class="text-[9px] uppercase tracking-wider text-textMuted block">' + c[0] + '</span>' +

            '<span class="font-mono font-bold text-xs ' + c[2] + '">' + c[1] + '</span></div>').join('') + '</div>';



    return '<div class="grid grid-cols-1 lg:grid-cols-2 gap-4">' +

        '<div class="space-y-1">' + rows.join('') + waterfall + '</div>' +

        '<div>' + rHtml + '</div></div>';

}

// ============== SECTION C: EXIT DISCIPLINE & LOSS PERSISTENCE ==============



function acctSecExit(a) {

    const stop = a.stop_loss_share;

    const lossR = a.avg_loss_r != null ? Math.abs(Number(a.avg_loss_r)) : null;

    const hold = a.avg_hold_sec;

    const rows = [];



    if (stop != null) {

        const pct = (stop * 100).toFixed(1);

        const ok = stop >= 0.7;

        rows.push('<div class="flex items-center gap-3 text-sm">' +

            '<span class="w-36 shrink-0 text-[10px] uppercase tracking-wider text-textMuted">stop-loss exits</span>' +

            '<div class="flex-1 h-2 rounded-full bg-darkBg overflow-hidden">' +

            '<div class="h-full rounded-full ' + (ok ? 'bg-emerald-500' : 'bg-amber-500') + '" style="width:' + pct + '%"></div></div>' +

            '<span class="font-mono font-black text-xs w-14 text-right ' + (ok ? 'text-emerald-400' : 'text-amber-400') + '">' + pct + '%</span></div>');

        rows.push('<div class="text-[10px] text-textMuted leading-relaxed">' +

            (ok ? 'Stop system is doing the closing. Discipline is structural, not incidental.'

                : 'Two-thirds or more of losers did NOT end at a stop. Losers are bleeding out via manual / emergency / strategy exits.') + '</div>');

        if (lossR != null) {

            const cls = lossR <= 1.0 ? 'text-emerald-400' : (lossR <= 1.5 ? 'text-amber-400' : 'text-rose-400');

            rows.push('<div class="text-xs font-mono ' + cls + '">avg loss <b>' + acctNum(a.avg_loss_r, 3) + 'R</b>' +

                (lossR > 1.0 ? ' &mdash; losses exceed the planned risk unit; the risk plan is not the binding constraint.' : ' &mdash; losses stay inside the planned risk unit.') + '</div>');

        }

    } else {

        rows.push('<div class="text-textMuted italic text-xs">No exit-classification evidence yet.</div>');

    }

    if (hold != null) {

        const h = Math.round(Number(hold));

        rows.push('<div class="text-xs text-textMuted">avg hold <b class="text-gray-200">' + h + 's</b> per trade' +

            (h <= 90 ? ' &mdash; scalper timing profile.' : (h <= 600 ? ' &mdash; intraday swing-leaning.' : ' &mdash; persistent book; watch overnight gaps.')) + '</div>');

    }

    rows.push('<div class="text-xs text-textMuted">avg MAE ' + acctR(a.avg_mae_r) + ' vs avg MFE ' + acctR(a.avg_mfe_r) +

        (a.avg_mae_r != null && a.avg_mfe_r != null ? ' &mdash; ' +

            (Number(a.avg_mae_r) > Number(a.avg_mfe_r) ? 'adverse excursion dominates: entries fight the move.' : 'favourable excursion dominates: entries enjoy follow-through.') : '') + '</div>');

    if (a.loss_efficiency_pct != null) {

        rows.push('<div class="text-xs text-textMuted">losers gave back ~<b class="text-amber-400">' + acctNum(a.loss_efficiency_pct, 1) + '%</b> of peak favourable excursion before closing red' +

            (Number(a.loss_efficiency_pct) > 50 ? ' &mdash; exits are late on the winner side of losers.' : '.') + '</div>');

    }

    if (a.win_mae_capture_pct != null) {

        rows.push('<div class="text-xs text-textMuted">winners kept only ~<b class="text-gray-200">' + acctNum(a.win_mae_capture_pct, 1) + '%</b> of adverse excursion before closing green (100% = perfect stop discipline).</div>');

    }

    return '<div class="space-y-2">' + rows.join('') + '</div>';

}



// ================= SECTION D: PROFIT / LOSS DISTRIBUTION ===================



function acctSecDistribution(a) {

    const winN = a.max_consecutive_wins;

    const lossN = a.max_consecutive_losses;

    const avgWin = a.average_win != null ? Number(a.average_win) : null;

    const avgLoss = a.average_loss != null ? Number(a.average_loss) : null;

    const payoff = a.payoff_ratio != null ? Number(a.payoff_ratio) : null;

    const skew = a.profit_skew;

    const cells = [];

    cells.push(['Avg Win', avgWin != null ? acctFmtMoney(avgWin) : '--', avgWin != null ? 'text-emerald-400' : 'text-gray-400']);

    cells.push(['Avg Loss', avgLoss != null ? acctFmtMoney(avgLoss) : '--', avgLoss != null ? 'text-rose-400' : 'text-gray-400']);

    cells.push(['Payoff Ratio', payoff != null ? acctNum(payoff, 2) : '--', payoff != null && payoff >= 1 ? 'text-emerald-400' : (payoff != null ? 'text-rose-400' : 'text-gray-400')]);

    cells.push(['Best Trade', a.best_trade != null ? acctFmtMoney(a.best_trade) : '--', 'text-emerald-400']);

    cells.push(['Worst Trade', a.worst_trade != null ? acctFmtMoney(a.worst_trade) : '--', 'text-rose-400']);

    cells.push(['Profit Skew', skew != null ? acctNum(skew, 2) : '--', skew != null && skew > 0 ? 'text-emerald-400' : 'text-gray-400']);

    cells.push(['Loss Skew', a.loss_skew != null ? acctNum(a.loss_skew, 2) : '--', a.loss_skew != null && a.loss_skew < 0 ? 'text-rose-400' : 'text-gray-400']);

    cells.push(['Max Win Streak', winN != null ? String(winN) : '--', 'text-emerald-400']);

    cells.push(['Max Loss Streak', lossN != null ? String(lossN) : '--', 'text-rose-400']);



    // pairwise verdict

    let verdict = '';

    if (avgWin != null && avgLoss != null) {

        const ratio = avgWin / Math.abs(avgLoss);

        const met = payoff != null ? ratio / payoff : ratio;

        if (met < 1) {

            verdict = 'The win:loss size ratio is <b class="text-rose-400">below breakeven for this win rate</b> &mdash; winners do not pay for losers at the current hit rate.';

        } else if (met < 1.25) {

            verdict = 'Win:loss size ratio just about pays the hit rate &mdash; but only just; costs can flip it.';

        } else {

            verdict = 'The win:loss size ratio <b class="text-emerald-400">exceeds the breakeven ratio</b> &mdash; payoff structure supports the win rate.';

        }

        if (winN != null && lossN != null && lossN > 4 && lossN > winN * 2) {

            verdict += ' Max loss streak ' + lossN + ' vs win streak ' + winN + ' &mdash; drawdowns cluster harder than recoveries.';

        }

    }

    return '<div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">' +

        cells.map(c => '<div class="bg-darkBg/50 rounded-md px-2 py-1.5 border border-borderClr/40">' +

            '<span class="text-[9px] uppercase tracking-wider text-textMuted block">' + c[0] + '</span>' +

            '<span class="font-mono font-bold text-xs ' + c[2] + '">' + c[1] + '</span></div>').join('') +

        '</div>' + (verdict ? '<div class="mt-2 text-xs text-textMuted">' + verdict + '</div>' : '');

}

// =============== SECTION E: SCENARIO ANALYSIS (5 core + bonus) ==============

// acctScenario*() returns {title, icon, tone, body} or null when the sample

// cannot support the scenario. Never fabricates.



function acctScenarioStopDiscipline(a) {

    if (a.stop_loss_share == null || a.avg_loss_r == null) return null;

    const stop = Number(a.stop_loss_share);

    const lossR = Math.abs(Number(a.avg_loss_r));

    const title = stop >= 0.7 ? 'Stop discipline is doing the closing' : 'Losers are bleeding out';

    const icon = stop >= 0.7 ? 'fa-shield-halved' : 'fa-truck-medical';

    const tone = stop >= 0.7 ? 'emerald' : 'amber';

    let body;

    if (stop >= 0.7) {

        body = '<p>On this sample, <b>' + (stop * 100).toFixed(1) + '% of losses ended at a protective stop</b> and the average loser burns ' +

            acctNum(a.avg_loss_r, 3) + 'R. The exit engine is the binding constraint on losses &mdash; the risk plan is enforced in practice, not just on paper.</p>' +

            '<p class="mt-1">A healthy stop-loss share means the loss distribution is truncated at the planned risk unit; the remaining bleed (if any) comes from strategy/manual exits, not from stops blowing through.</p>' +

            (lossR > 1.0 ? '<p class="mt-1 text-rose-400">Caveat: avg loss of ' + acctNum(a.avg_loss_r, 3) + 'R still exceeds the 1R plan &mdash; slippage, gaps or mid-bar stops are leaking beyond the unit.</p>' : '');

    } else {

        body = '<p>Only <b>' + (stop * 100).toFixed(1) + '% of losers closed at a stop</b>. The other ' + (100 - stop * 100).toFixed(1) +

            '% exited via manual, emergency or strategy paths &mdash; and the average loser still costs ' + acctNum(a.avg_loss_r, 3) + 'R.</p>' +

            '<p class="mt-1">When the stop system closes fewer than ~70% of losers, the tail of the loss distribution is controlled by human reaction time. Fixing exit classification first will show whether the bleed is strategy exits (by design) or mercy exits (by hesitation).</p>';

    }

    return { title: title, icon: icon, tone: tone, body: body };

}



function acctScenarioExcursion(a) {

    if (a.avg_mae_r == null || a.avg_mfe_r == null) return null;

    const mae = Number(a.avg_mae_r), mfe = Number(a.avg_mfe_r);

    const adverse = mae > mfe;

    const title = adverse ? 'The book fights the market before it works' : 'The book works before it fights';

    const icon = adverse ? 'fa-person-falling' : 'fa-person-running';

    const tone = adverse ? 'rose' : 'emerald';

    let body;

    if (adverse) {

        body = '<p>Avg adverse excursion <b>' + acctNum(mae, 3) + 'R</b> vs avg favourable excursion <b>' + acctNum(mfe, 3) + 'R</b>.' +

            ' Trades go underwater by more than they ever go green before the close &mdash; entries are early, counter-trend, or both.</p>' +

            '<p class="mt-1">Every trade that must survive an adverse excursion before it can pay is a trade paying the market for patience. The fix is entry-side: better timing (later entries), or wider stops that let favourable excursion develop without being stopped first.</p>';

    } else {

        body = '<p>Avg favourable excursion <b>' + acctNum(mfe, 3) + 'R</b> exceeds avg adverse excursion <b>' + acctNum(mae, 3) + 'R</b>.' +

            ' Positions generally move toward profit before they move against &mdash; the entry timing is directionally sound.</p>' +

            '<p class="mt-1">When MFE dominates MAE but the book still loses, the loss is not in entry, it is in exit: winners are given back or cut early. Focus the audit on the exit side.</p>';

    }

    return { title: title, icon: icon, tone: tone, body: body };

}



function acctScenarioGiveback(a) {

    if (a.loss_efficiency_pct == null) return null;

    const gb = Number(a.loss_efficiency_pct);

    const title = gb > 50 ? 'Losers give back their peak' : 'Losers cut their winners early';

    const icon = gb > 50 ? 'fa-rotate-left' : 'fa-scissors';

    const tone = gb > 50 ? 'amber' : 'emerald';

    let body;

    if (gb > 50) {

        body = '<p>Losers gave back ~<b>' + acctNum(gb, 1) + '%</b> of their peak favourable excursion before closing red.' +

            ' A loser that was once +2R and closed -0.5R is not a bad entry, it is a bad exit.</p>' +

            '<p class="mt-1">Giveback this large is the signature of trailing stops that are too wide, take-profit levels that are too far, or manual patience that turns winners into losers. Every giveback point is edge that existed and was then surrendered.</p>';

    } else {

        body = '<p>Losers gave back only ~<b>' + acctNum(gb, 1) + '%</b> of their peak favourable excursion.' +

            ' When a loser does go green, the exit system cuts it before it can round-trip.</p>' +

            '<p class="mt-1">This is the healthy pattern: the book takes its losers quickly even when the market briefly agreed with the entry. The remaining problem, if any, is on the winner side (win MAE capture).</p>';

    }

    return { title: title, icon: icon, tone: tone, body: body };

}



function acctScenarioCostDrag(a) {

    if (a.total_costs == null) return null;

    const costs = Number(a.total_costs);

    const drag = a.cost_drag_pct != null ? Number(a.cost_drag_pct) : null;

    const net = a.net_pnl != null ? Number(a.net_pnl) : null;

    if (costs === 0 && drag == null && net == null) return null;

    const title = costs > 0 ? 'Costs are a tax on every trade' : 'Cost drag is flat';

    const icon = costs > 0 ? 'fa-receipt' : 'fa-circle-check';

    const tone = costs > 0 ? 'amber' : 'emerald';

    let body;

    if (costs > 0) {

        body = '<p>Total costs (commission + swap) = <b>' + acctFmtMoney(costs) + '</b>' +

            (drag != null ? ', <b>' + acctPctV(drag) + '</b> of gross profit' : '') + '. Net PnL ' + acctFmtMoney(net) + '.</p>' +

            '<p class="mt-1">Costs are only neutral when the gross edge covers them. With ' + a.sample_trades + ' closed trades the per-trade cost is ' +

            (a.sample_trades ? acctFmtMoney(costs / a.sample_trades) : '--') + ' &mdash; every trade starts that far behind. If the edge per trade is smaller than the cost per trade, no win-rate tuning fixes it: only fewer, better trades do.</p>';

    } else {

        body = '<p>Cost drag is <b>flat</b> &mdash; the broker\u2019s commission/swap stack is not eating the book.</p>' +

            '<p class="mt-1">With costs neutral, any remaining net loss is 100% execution/edge problem, not overhead. That simplifies the diagnosis: it is not the broker, it is the system.</p>';

    }

    return { title: title, icon: icon, tone: tone, body: body };

}



function acctScenarioStreaks(a) {

    if (a.max_consecutive_losses == null) return null;

    const l = Number(a.max_consecutive_losses);

    const w = a.max_consecutive_wins != null ? Number(a.max_consecutive_wins) : 0;

    const title = l > 4 ? 'Loss streaks cluster harder than recoveries' : 'Streak profile is balanced';

    const icon = l > 4 ? 'fa-wave-square' : 'fa-scale-balanced';

    const tone = l > 4 ? 'rose' : 'emerald';

    let body;

    if (l > 4) {

        body = '<p>Max loss streak <b>' + l + '</b> vs max win streak <b>' + w + '</b>.' +

            ' When the market regime turns, the book loses ' + l + ' in a row before a single recovery &mdash; that is the shape of a drawdown.</p>' +

            '<p class="mt-1">A ' + l + '-streak at the current risk per trade is the true tail of the equity curve. If the risk plan sizes for the average loss but the streak sizes for the maximum, the account is under-capitalized for its own regime risk.</p>';

    } else {

        body = '<p>Max loss streak <b>' + l + '</b> vs max win streak <b>' + w + '</b>.' +

            ' Neither side clusters pathologically &mdash; the book alternates, which is what a mean-reversion-ish scalp profile should do.</p>' +

            '<p class="mt-1">Balanced streaks mean variance is not the primary enemy; expectancy per trade is. Fix the edge, not the streaks.</p>';

    }

    return { title: title, icon: icon, tone: tone, body: body };

}

// =========== SECTION E (cont.): BONUS SCENARIOS + COMPOSER =================



function acctScenarioExpectancy(a) {

    if (a.expectancy_breakeven_incl == null) return null;

    const e = Number(a.expectancy_breakeven_incl);

    const decided = a.expectancy != null ? Number(a.expectancy) : null;

    const title = e >= 0 ? 'Breakeven-inclusive expectancy is positive' : 'Breakeven-inclusive expectancy is negative';

    const icon = e >= 0 ? 'fa-seedling' : 'fa-triangle-exclamation';

    const tone = e >= 0 ? 'emerald' : 'rose';

    let body;

    if (e >= 0) {

        body = '<p>Expectancy incl. breakevens is <b>' + acctFmtMoney(e) + '</b> per trade over ' + a.sample_trades + ' closed trades.' +

            (decided != null ? ' The decided-only version is ' + acctFmtMoney(decided) + ' &mdash; scratches dilute it to ' + acctFmtMoney(e) + '.' : '') + '</p>' +

            '<p class="mt-1">Positive raw-money expectancy is the single most load-bearing number in this panel: it means the system currently prices in all costs and still pays. Protect it &mdash; the next step is scaling it without breaking the denominators.</p>';

    } else {

        body = '<p>Every closed trade (including breakevens) costs <b>' + acctFmtMoney(Math.abs(e)) + '</b> on average &mdash; ' +

            a.sample_trades + ' trades × that drag = ' + acctFmtMoney(a.net_pnl || (e * a.sample_trades)) + ' of bleed.</p>' +

            '<p class="mt-1">Negative expectancy with a positive gross profile means the hole is in exit quality (giveback), cost control, or both. Fixing either flips the number; fixing both is the whole game.</p>';

    }

    return { title: title, icon: icon, tone: tone, body: body };

}



function acctScenarioWinnerMae(a) {

    if (a.win_mae_capture_pct == null) return null;

    const cap = Number(a.win_mae_capture_pct);

    const title = cap >= 70 ? 'Winners keep their adverse excursion' : 'Winners bleed adverse excursion before closing green';

    const icon = cap >= 70 ? 'fa-angles-up' : 'fa-arrow-trend-down';

    const tone = cap >= 70 ? 'emerald' : 'rose';

    let body;

    if (cap >= 70) {

        body = '<p>Winner MAE capture is <b>' + acctNum(cap, 1) + '%</b> &mdash; winners held ~' + acctNum(cap, 1) +

            '% of their adverse excursion back before closing green. Entry timing on winners is clean.</p>' +

            '<p class="mt-1">High capture means the entry-to-profit path is short. If the book still loses, the pressure is on the loser side of the distribution, not the winner side.</p>';

    } else {

        body = '<p>Winners took on <b>' + acctNum(cap, 1) + '%</b> of their adverse excursion before green &mdash; even the winners fight the market before they pay.</p>' +

            '<p class="mt-1">Low capture + negative expectancy is the classic early-entry signature: the model enters before confirmation and pays the market for patience on both sides.</p>';

    }

    return { title: title, icon: icon, tone: tone, body: body };

}



function acctScenarioRiskPerTrade(a) {

    if (a.avg_risk_usd == null) return null;

    const r = Number(a.avg_risk_usd);

    const title = r > 0 ? 'Risk per trade is the unit the whole book is measured in' : 'Risk per trade is flat';

    const icon = 'fa-coins';

    const tone = 'sky';

    let body;

    if (r > 0) {

        body = '<p>Avg risk deployed per trade is <b>' + acctFmtMoney(r) + '</b>. Every R-denominated stat in this panel (avg R, avg MAE/MFE, avg loss R) is a multiple of this unit.</p>' +

            '<p class="mt-1">With risk per trade this size, a ' + (a.max_consecutive_losses || '?') + '-loss streak is a ' +

            acctFmtMoney(r * Math.max(1, Number(a.max_consecutive_losses) || 1)) + ' drawdown before recoveries. The risk plan is what converts a bad streak into an account problem &mdash; size it for the streak, not the average.</p>';

    } else {

        body = '<p>No risk basis recovered for R-denominated stats yet (r_sample_count ' + (a.r_sample_count || 0) + ').</p>';

    }

    return { title: title, icon: icon, tone: tone, body: body };

}



function acctScenarioRatios(a) {

    if (a.sharpe_ratio == null && a.sortino_ratio == null && a.calmar_ratio == null && a.sqn == null) return null;

    const sh = a.sharpe_ratio != null ? Number(a.sharpe_ratio) : null;

    const so = a.sortino_ratio != null ? Number(a.sortino_ratio) : null;

    const ca = a.calmar_ratio != null ? Number(a.calmar_ratio) : null;

    const sq = a.sqn != null ? Number(a.sqn) : null;

    const negCount = [sh, so, ca, sq].filter(v => v != null && v < 0).length;

    const title = negCount >= 2 ? 'Risk-adjusted ratios confirm the bleed' : 'Risk-adjusted ratios are mixed';

    const icon = negCount >= 2 ? 'fa-heart-crack' : 'fa-scale-unbalanced';

    const tone = negCount >= 2 ? 'rose' : 'amber';

    const fmtCol = v => v != null ? '<b class="' + (v >= 0 ? 'text-emerald-400' : 'text-rose-400') + '">' + acctNum(v, 2) + '</b>' : '--';

    let body = '<p>Sharpe ' + fmtCol(sh) + ' · Sortino ' + fmtCol(so) + ' · Calmar ' + fmtCol(ca) + ' · SQN ' + fmtCol(sq) + '.</p>';

    if (negCount >= 2) {

        body += '<p class="mt-1">When the risk-adjusted core is negative across multiple lenses, the drawdown is not a blip &mdash; it is the statistical expectation. The equity curve is a fair price for this process as-is.</p>' +

            '<p class="mt-1">No single ratio need be textbook-good; but they all being negative together is the strongest signal in the panel that the current process, at the current size, should not be scaled yet.</p>';

    } else {

        body += '<p class="mt-1">Mixed ratios: some lenses negative, some not &mdash; the risk profile is not uniformly broken, which means targeted fixes (exit discipline, giveback) can land on the negative side without rebuilding the whole book.</p>';

    }

    return { title: title, icon: icon, tone: tone, body: body };

}



function acctScenarioHoldTime(a) {

    if (a.avg_hold_sec == null) return null;

    const h = Math.round(Number(a.avg_hold_sec));

    const vwap = a.net_pnl != null ? Number(a.net_pnl) : null;

    const title = h <= 90 ? 'Scalper timing profile' : (h <= 600 ? 'Intraday swing-leaning profile' : 'Persistent book, watch the overnight');

    const icon = h <= 90 ? 'fa-bolt' : (h <= 600 ? 'fa-hourglass-half' : 'fa-moon');

    const tone = h <= 90 ? 'accent' : (h <= 600 ? 'sky' : 'amber');

    const body = '<p>Avg hold is <b>' + h + 's</b> per trade.' +

        (h <= 90 ? ' A pure scalp profile: ' + (vwap != null && vwap < 0 ? 'the book bleeds on spread-heavy quick exits &mdash; cost per trade matters more than win rate.' : 'timing dominates and costs matter most.') :

            (h <= 600 ? ' Positions ride minutes, not seconds &mdash; giveback management and MFE capture matter more than raw speed.' :

                ' Holds that long at FX scale carry overnight/rollover risk into every prayer &mdash; swap drag (see cost drag) is a permanent headwind.')) + '</p>';

    return { title: title, icon: icon, tone: tone, body: body };

}



function acctScenarioSkewness(a) {

    if (a.profit_skew == null && a.loss_skew == null) return null;

    const ps = a.profit_skew != null ? Number(a.profit_skew) : null;

    const ls = a.loss_skew != null ? Number(a.loss_skew) : null;

    const good = (ps != null && ps > 0) && (ls != null && ls < 0);

    const title = good ? 'Distribution shape favours the book' : 'Distribution shape fights the book';

    const icon = good ? 'fa-chart-simple' : 'fa-chart-pie';

    const tone = good ? 'emerald' : 'rose';

    const body = '<p>Profit skew <b>' + (ps != null ? acctNum(ps, 2) : '--') + '</b>' +

        ' &middot; Loss skew <b>' + (ls != null ? acctNum(ls, 2) : '--') + '</b>. ' +

        (good

            ? 'Positive profit skew (occasional large winners) + negative loss skew (many small losers) is the textbook winning shape.'

            : 'The shape here means wins cluster small and losses come in chunks &mdash; the tail is on the wrong side.') + '</p>';

    return { title: title, icon: icon, tone: tone, body: body };

}



// ---- composer -------------------------------------------------------------



function acctScenarioCard(s) {

    if (!s) return '';

    const toneCls = {

        emerald: ['border-emerald-400/30', 'text-emerald-400'],

        rose: ['border-rose-400/30', 'text-rose-400'],

        amber: ['border-amber-400/30', 'text-amber-400'],

        sky: ['border-sky-400/30', 'text-sky-400'],

        accent: ['border-accentCyan/30', 'text-accentCyan'],

    }[s.tone] || ['border-borderClr/50', 'text-accentCyan'];

    return '<div class="rounded-lg border ' + toneCls[0] + ' bg-darkBg/40 p-3">' +

        '<div class="flex items-center gap-2 mb-1.5">' +

        '<i class="fa-solid ' + s.icon + ' ' + toneCls[1] + '"></i>' +

        '<span class="text-xs font-bold text-gray-100">' + s.title + '</span></div>' +

        '<div class="text-[11px] leading-relaxed text-textMuted">' + s.body + '</div></div>';

}



function renderAccountDeepAnalysis(a, data) {

    const wrap = document.getElementById('acct-intel-deep');

    if (!wrap) return;

    const verdict = acctVerdict(a);

    let html = '';



    // Verdict banner

    html += '<div class="rounded-lg border border-borderClr/60 bg-darkBg/40 p-3 flex items-center gap-3">' +

        '<span class="text-2xl ' + verdict.cls + '"><i class="fa-solid fa-gauge-high"></i></span>' +

        '<div><div class="flex items-center gap-2"><span class="uppercase tracking-widest text-[9px] text-textMuted">verdict</span>' +

        '<span class="text-xs font-black ' + verdict.cls + '">' + verdict.label + '</span></div>' +

        '<div class="text-[11px] text-textMuted">' + verdict.text + '</div></div></div>';



    // Section B: Edge reality

    html += '<div><div class="flex items-center gap-2 mb-2">' +

        '<span class="text-[10px] uppercase tracking-widest text-textMuted/80 font-bold">Edge Reality</span>' +

        '<span class="text-[9px] text-textMuted/50">counts · dollar-weight · R-unit</span></div>' + acctSecEdge(a) + '</div>';



    // Section C: Exit discipline

    html += '<div><div class="flex items-center gap-2 mb-2">' +

        '<span class="text-[10px] uppercase tracking-widest text-textMuted/80 font-bold">Exit Discipline &amp; Loss Persistence</span>' +

        '<span class="text-[9px] text-textMuted/50">stop share · loss R · excursion</span></div>' + acctSecExit(a) + '</div>';



    // Section D: Distribution

    html += '<div><div class="flex items-center gap-2 mb-2">' +

        '<span class="text-[10px] uppercase tracking-widest text-textMuted/80 font-bold">Profit &amp; Loss Distribution</span>' +

        '<span class="text-[9px] text-textMuted/50">sizes · skew · streaks</span></div>' + acctSecDistribution(a) + '</div>';



    // Section E: Scenario cards

    const scenarios = [

        acctScenarioStopDiscipline(a),

        acctScenarioExcursion(a),

        acctScenarioGiveback(a),

        acctScenarioCostDrag(a),

        acctScenarioStreaks(a),

        acctScenarioExpectancy(a),

        acctScenarioWinnerMae(a),

        acctScenarioRiskPerTrade(a),

        acctScenarioRatios(a),

        acctScenarioHoldTime(a),

        acctScenarioSkewness(a),

    ].filter(Boolean);

    if (scenarios.length) {

        html += '<div><div class="flex items-center gap-2 mb-2">' +

            '<span class="text-[10px] uppercase tracking-widest text-textMuted/80 font-bold">Scenario Analysis</span>' +

            '<span class="text-[9px] text-textMuted/50">' + scenarios.length + ' live scenarios · each falsifiable</span></div>' +

            '<div class="grid grid-cols-1 xl:grid-cols-2 gap-2">' + scenarios.map(acctScenarioCard).join('') + '</div></div>';

    }

    wrap.innerHTML = html;

    // Deep Analysis stays collapsed by default (the toggle button reveals it).

    // Re-renders (period switch / auto-refresh) must not force it open.

    if (!window.__acctIntelDeepVisible) {

        wrap.classList.add('hidden');

    }

}

// ===== WIRING: loadAdvancedMetrics v2 + period extras + refresh hook =======



async function loadAdvancedMetrics() {

    try {

        const res = await fetch('/api/account/performance');

        if (!res.ok) return;

        const data = await res.json();

        if (!data.available || !data.advanced) return;

        const a = data.advanced;

        const setText = (id, txt, cls) => {

            const el = document.getElementById(id);

            if (!el) return;

            if (cls) el.className = 'font-mono font-black text-sm ' + cls;

            el.textContent = txt;

        };

        const color = v => (v == null ? 'text-gray-400' : (v >= 0 ? 'text-emerald-400' : 'text-rose-400'));

        const fmt = (v, d = 2) => (v == null ? 'n/a' : Number(v).toFixed(d));

        setText('acct-sharpe', fmt(a.sharpe_ratio), color(a.sharpe_ratio));

        setText('acct-sortino', fmt(a.sortino_ratio), color(a.sortino_ratio));

        setText('acct-calmar', fmt(a.calmar_ratio), color(a.calmar_ratio));

        setText('acct-sqn', fmt(a.sqn), color(a.sqn));

        setText('acct-recovery-factor', fmt(a.recovery_factor), color(a.recovery_factor));

        setText('acct-payoff', fmt(a.payoff_ratio), color(a.payoff_ratio));

        setText('acct-avg-win', a.average_win == null ? 'n/a' : acctFmtMoney(a.average_win), 'text-emerald-400');

        setText('acct-avg-loss', a.average_loss == null ? 'n/a' : acctFmtMoney(a.average_loss), 'text-rose-400');

        setText('acct-win-streak', String(a.max_consecutive_wins ?? 'n/a'), 'text-emerald-400');

        setText('acct-loss-streak', String(a.max_consecutive_losses ?? 'n/a'), 'text-rose-400');

        setText('acct-eq-vol', a.equity_volatility_pct == null ? 'n/a' : fmt(a.equity_volatility_pct) + '%');

        setText('acct-pnl-tstat', fmt(a.profit_standard_error), color(a.profit_standard_error));

        setText('acct-stop-share', a.stop_loss_share == null ? 'n/a' : fmt(a.stop_loss_share * 100, 1) + '%', 'text-gray-200');

        setText('acct-avg-loss-r', fmt(a.avg_loss_r, 3), 'text-rose-400');

        setText('acct-avg-win-r', fmt(a.avg_r_multiple, 3), 'text-emerald-400');

        setText('acct-avg-mae-r', fmt(a.avg_mae_r, 3), 'text-rose-400');

        setText('acct-avg-mfe-r', fmt(a.avg_mfe_r, 3), 'text-emerald-400');

        setText('acct-adv-avg-hold', a.avg_hold_sec == null ? 'n/a' : Math.round(a.avg_hold_sec) + 's', 'text-gray-200');

        setText('acct-avg-risk-usd', a.avg_risk_usd == null ? 'n/a' : acctFmtMoney(a.avg_risk_usd), 'text-gray-200');

        setText('acct-r-coverage', a.r_coverage_ratio == null ? 'n/a' : (a.r_coverage_ratio * 100).toFixed(1) + '%', 'text-gray-200');

        // Period-level extras live in the period payload, not advanced; guard.

        const src = document.getElementById('acct-adv-source');

        if (src) src.textContent = 'source: accounting core · ' + a.sample_trades + ' closed trades';

        renderAccountIntelTexts(a, data);

        renderAccountDeepAnalysis(a, data);

    } catch (err) {

        console.error('Advanced metrics load failed', err);

    }

}



function acctSetIfId(id, txt) {

    const el = document.getElementById(id);

    if (el) el.textContent = txt;

}



// New period fields: gross split bar + expectancy incl. breakevens + breakevens count.

function renderAccountPeriodExtras(p) {

    const gp = p.gross_profit != null ? Math.max(0, Number(p.gross_profit)) : 0;

    const gl = p.gross_loss != null ? Math.max(0, Number(p.gross_loss)) : 0;

    const tot = (gp + gl) || 1;

    acctSetIfId('acct-gross-profit', acctFmtMoney(gp));

    acctSetIfId('acct-gross-loss', acctFmtMoney(gl));

    const split = document.getElementById('acct-hero-split');

    if (split) {

        const wGp = Math.round(gp / tot * 100);

        split.innerHTML = '<div class="bg-emerald-500" style="width:' + wGp + '%"></div>' +

            '<div class="bg-rose-500" style="width:' + (100 - wGp) + '%"></div>';

    }

    acctSetIfId('acct-expectancy-incl', p.expectancy_breakeven_incl != null ? acctFmtMoney(p.expectancy_breakeven_incl) : 'n/a');

    acctSetIfId('acct-breakevens', p.breakeven_count != null ? String(p.breakeven_count) : '--');

    if (p.win_rate_denominator) {

        acctSetIfId('acct-win-denom', 'denominator: ' + p.win_rate_denominator);

        const chip = document.getElementById('acct-period-denom-chip');

        if (chip) {

            chip.textContent = 'denominator: ' + p.win_rate_denominator;

            chip.classList.remove('hidden');

        }

    }

}



async function loadAccountPeriod(kind, btn) {

    window.__currentPeriodKind = kind;

    try {

        const res = await fetch('/api/account/performance/' + kind);

        if (!res.ok) return;

        const data = await res.json();

        if (!data.available || !data.period) return;

        const p = data.period;

        document.getElementById('acct-net-pnl').textContent = acctFmtMoney(p.net_pnl);

        document.getElementById('acct-net-pnl').className = 'font-mono font-black text-sm ' + (p.net_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400');

        document.getElementById('acct-pnl-pct').textContent = acctFmtPct(p.pnl_pct);

        document.getElementById('acct-trades').textContent = String(p.total_trades);

        document.getElementById('acct-winrate-period').textContent = acctFmtPct(p.win_rate);

        document.getElementById('acct-expectancy').textContent = acctFmtMoney(p.expectancy);

        document.getElementById('acct-profit-factor').textContent = acctFmtNum(p.profit_factor, 3);

        document.getElementById('acct-avg-r').textContent = acctFmtNum(p.average_r, 3);

        document.getElementById('acct-max-dd').textContent = acctFmtPct(p.max_drawdown_pct);

        document.getElementById('acct-best-trade').textContent = acctFmtMoney(p.best_trade);

        document.getElementById('acct-worst-trade').textContent = acctFmtMoney(p.worst_trade);

        document.getElementById('acct-avg-hold').textContent = p.average_holding_sec != null ? Math.round(p.average_holding_sec) + 's' : '--';

        document.getElementById('acct-risk-deployed').textContent = acctFmtMoney(p.total_risk_deployed);

        document.getElementById('acct-loss-rate-decided').textContent = acctFmtPct(p.loss_rate_decided);

        document.getElementById('acct-loss-rate-all').textContent = acctFmtPct(p.loss_rate_all);

        document.getElementById('acct-winrate-all').textContent = acctFmtPct(p.win_rate_all);

        document.getElementById('acct-winrate-pnlw').textContent = acctFmtPct(p.pnl_weighted_win_rate);

        document.getElementById('acct-avg-pnl-decided').textContent = acctFmtMoney(p.avg_pnl_per_decided);

        document.getElementById('acct-cost-drag').textContent = (p.cost_drag_pct != null ? acctFmtNum(p.cost_drag_pct, 2) + '%' : '--');

        renderAccountPeriodExtras(p);
        // BUG-134: broker-day + market-state label (UI '1 Day' truth).
        const titleEl = document.getElementById('acct-period-title');
        if (titleEl) {
            const m = data.market || {};
            const serverDay = m.server_day || (p && p.key) || '--';
            titleEl.textContent = (kind === 'DAY' ? 'broker day ' : kind.toLowerCase() + ' ') + serverDay;
        }
        const msEl = document.getElementById('acct-market-state');
        if (msEl && data.market) {
            const m = data.market;
            const st = m.state || 'UNKNOWN';
            msEl.textContent = 'market ' + st + (m.next_open_iso ? ' | opens ' + new Date(m.next_open_iso).toLocaleString() : '');
            msEl.classList.toggle('hidden', st === 'OPEN');
        }

        const wrDeno = p.win_rate_denominator || 'NONE';

        const wrDenoEl = document.getElementById('acct-win-denom');

        if (wrDenoEl) wrDenoEl.textContent = 'denominator: ' + wrDeno;



        document.querySelectorAll('.acct-period-btn').forEach(b => {

            b.className = 'acct-period-btn px-4 py-1.5 rounded-lg text-xs font-bold bg-darkBg text-textMuted border border-borderClr';

        });

        if (btn) btn.className = 'acct-period-btn px-4 py-1.5 rounded-lg text-xs font-bold bg-accentCyan/15 text-accentCyan border border-accentCyan/30';



        loadPeriodSeries(kind);

    } catch (err) {

        console.error('Account period load failed', err);

    }

}



async function loadPeriodSeries(kind) {

    try {

        const res = await fetch('/api/account/performance/' + kind + '/series?count=12');

        if (!res.ok) return;

        const data = await res.json();

        if (!data.available || !data.periods || data.periods.length === 0) return;

        const labels = data.periods.map(p => p.key);

        const net = data.periods.map(p => p.net_pnl);

        acctLineChart('acct-period-chart', 'acct-period-chart-empty', labels, net, '#22d3ee', acctFmtMoney);

    } catch (err) {

        console.error('Period series load failed', err);

    }

}



async function loadAccountCharts() {

    try {

        const res = await fetch('/api/account/equity-curve');

        if (!res.ok) return;

        const data = await res.json();

        if (!data.available) return;



        const curve = data.equity_curve || [];

        const labels = curve.map(c => { const t = new Date(c.timestamp); return (t.getMonth() + 1) + '/' + t.getDate(); });

        acctLineChart('acct-equity-chart', 'acct-equity-empty', labels, curve.map(c => c.equity), '#34d399', v => '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 }));

        acctLineChart('acct-drawdown-chart', 'acct-drawdown-empty', labels, curve.map(c => c.drawdown_pct), '#fb7185', v => Number(v).toFixed(2) + '%');



        const cum = data.cumulative_pnl || [];

        acctLineChart('acct-cumulative-chart', 'acct-cumulative-empty', cum.map(c => { const t = new Date(c.timestamp); return (t.getMonth() + 1) + '/' + t.getDate(); }), cum.map(c => c.cumulative_pnl), '#fbbf24', acctFmtMoney);

    } catch (err) {

        console.error('Account charts load failed', err);

    }

}



async function loadAccountStrategies() {

    try {

        const res = await fetch('/api/account/strategies');

        if (!res.ok) return;

        const data = await res.json();

        const tbody = document.getElementById('acct-strategy-table');

        if (!tbody) return;

        if (!data.available || !data.strategies || data.strategies.length === 0) {

            tbody.innerHTML = '<tr><td colspan="8" class="py-4 text-center text-textMuted italic font-sans">NO STRATEGY EVIDENCE AVAILABLE</td></tr>';

            return;

        }

        tbody.innerHTML = data.strategies.map(s => {

            const lifecycle = s.lifecycle_state || 'DISCOVERED';

            // Distinct, truthful styling per lifecycle. DISCOVERED (observed

            // but unscored family) is informational, never an error.

            const lifeColor = lifecycle === 'ACTIVE' ? 'text-emerald-400' :

                (lifecycle === 'RETIRED' || lifecycle === 'QUARANTINED') ? 'text-rose-400' :

                (lifecycle === 'DISCOVERED') ? 'text-sky-400' : 'text-amber-400';

            return '<tr class="border-b border-borderClr/30">' +

                '<td class="py-2 pl-2 font-mono text-accentCyan">' + s.strategy_id + '</td>' +

                '<td class="py-2">' + s.trade_count + '</td>' +

                '<td class="py-2 ' + (s.net_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400') + '">' + acctFmtMoney(s.net_pnl) + '</td>' +

                '<td class="py-2">' + acctFmtPct(s.win_rate) + '</td>' +

                '<td class="py-2">' + acctFmtNum(s.profit_factor, 3) + '</td>' +

                '<td class="py-2">' + acctFmtNum(s.average_r, 3) + '</td>' +

                '<td class="py-2 ' + lifeColor + '">' + lifecycle + '</td>' +

                '<td class="py-2 pr-2 text-right">' + acctFmtNum(s.confidence, 4) + '</td>' +

                '</tr>';

        }).join('');

    } catch (err) {

        console.error('Account strategies load failed', err);

    }

}



async function loadTradeForensics() {

    const input = document.getElementById('acct-forensic-ticket');

    const out = document.getElementById('acct-forensic-output');

    if (!input || !out) return;

    const ticket = String(input.value || '').trim();

    if (!ticket) { out.textContent = 'Enter a ticket to inspect.'; out.classList.remove('hidden'); return; }

    try {

        const res = await fetch('/api/account/trades/' + encodeURIComponent(ticket));

        if (!res.ok) { out.textContent = 'HTTP ' + res.status; out.classList.remove('hidden'); return; }

        const data = await res.json();

        out.textContent = JSON.stringify(data, null, 2);

        out.classList.remove('hidden');

    } catch (err) {

        out.textContent = 'Inspection failed: ' + err;

        out.classList.remove('hidden');

    }

}



function initAccountIntelligence() {

    loadAccountPerformance();

    loadAdvancedMetrics();

    loadAccountPeriod('DAY', document.querySelector('.acct-period-btn'));

    loadAccountCharts();

    loadAccountStrategies();

    loadClosedTrades();

    loadRiskPlan();

}



// Closed Trading History: authoritative rows from /api/account/trades.

async function loadClosedTrades() {

    const tbody = document.getElementById('trade-history-table');

    if (!tbody) return;

    try {

        const res = await fetch('/api/account/trades?limit=50');

        if (!res.ok) {

            tbody.innerHTML = '<tr><td colspan="7" class="py-6 text-center text-textMuted italic font-sans text-xs">Closed history unavailable (' + res.status + ')</td></tr>';

            return;

        }

        const rows = await res.json();

        const list = Array.isArray(rows) ? rows : (rows.trades || rows.rows || []);

        if (!list || list.length === 0) {

            tbody.innerHTML = '<tr><td colspan="7" class="py-6 text-center text-textMuted italic font-sans text-xs">No historical trades found yet — broker history sync pending.</td></tr>';

            return;

        }

        tbody.innerHTML = list.map(t => {

            const dir = String(t.direction || '--').toUpperCase();

            const dirCls = dir.startsWith('BUY') ? 'text-emerald-400' : (dir.startsWith('SELL') ? 'text-rose-400' : 'text-textMuted');

            const net = (t.net_pnl != null) ? Number(t.net_pnl) : (t.pnl != null ? Number(t.pnl) : null);

            const netCls = net == null ? 'text-textMuted' : (net >= 0 ? 'text-emerald-400' : 'text-rose-400');

            const vol = (t.volume != null) ? Number(t.volume) : null;

            const exitT = t.exit_time || t.close_time || t.closed_at || '';

            const timeStr = exitT ? new Date(exitT).toLocaleString('en-GB', { hour12: false }) : '--';

            const id = t.ticket != null ? t.ticket : (t.position_id != null ? t.position_id : (t.trade_id || '--'));

            return '<tr>' +

                '<td class="py-2 pl-2 font-mono text-accentCyan">' + id + '</td>' +

                '<td class="py-2">' + (t.symbol || '--') + '</td>' +

                '<td class="py-2 ' + dirCls + '">' + dir + '</td>' +

                '<td class="py-2">' + (vol != null ? Number(vol).toFixed(2) : '--') + '</td>' +

                '<td class="py-2 font-mono">' + (t.entry_price != null ? Number(t.entry_price).toFixed(2) + ' → ' : '') + (t.exit_price != null ? Number(t.exit_price).toFixed(2) : '--') + '</td>' +

                '<td class="py-2 font-mono ' + netCls + '">' + (net != null ? acctFmtMoney(net) : '--') + '</td>' +

                '<td class="py-2 pr-2 text-right font-mono text-textMuted">' + timeStr + '</td>' +

                '</tr>';

        }).join('');

    } catch (err) {

        console.error('Closed trades load failed', err);

        tbody.innerHTML = '<tr><td colspan="7" class="py-6 text-center text-textMuted italic font-sans text-xs">Closed history load failed.</td></tr>';

    }

}



// Risk Plan: authoritative numbers from /api/live/accounting (single source

// of truth - the SAME RiskEngine the live engine uses; no JS-side math).

async function loadRiskPlan() {

    try {

        const res = await fetch('/api/live/accounting');

        if (!res.ok) return;

        const data = await res.json();

        const srcEl = document.getElementById('risk-plan-source');

        if (srcEl) srcEl.textContent = data.source || (data.available ? 'RISK_ENGINE' : 'UNAVAILABLE');

        if (!data.available || !data.plan) {

            return;

        }

        const p = data.plan;

        const setTxt = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };

        setTxt('rp-risk-usd', (p.risk_usd != null) ? '$' + Number(p.risk_usd).toFixed(2) : '—');

        setTxt('rp-lot-size', (p.lot_size != null) ? Number(p.lot_size).toFixed(2) : '—');

        setTxt('rp-margin', (p.margin_required != null) ? '$' + Number(p.margin_required).toLocaleString('en-US', { minimumFractionDigits: 2 }) : '—');

        setTxt('rp-exposure', (p.exposure_pct != null) ? Number(p.exposure_pct).toFixed(2) + '%' : '—');

        const note = document.getElementById('rp-note');

        if (note) {

            note.textContent = p.note ? String(p.note) : (p.entry != null ? `entry ${p.entry} · SL ${p.stop_loss} · lots ${p.lot_size} (min ${p.min_lot}, step ${p.lot_step})` : '');

        }

    } catch (err) {

        console.warn('[UI_ERROR] component=Accounting action=LOAD_RISK_PLAN', err);

    }

}

// =============================================================================



// =============================================================================

// PHASE 09B: STRATEGY RESEARCH ENGINE (registry / discovery / validation)

// =============================================================================



async function loadResearchSummary() {

    try {

        const res = await fetch('/api/research/summary');

        if (!res.ok) return;

        const body = await res.json();

        if (!body.available || !body.summary) return;

        const s = body.summary;

        document.getElementById('research-registry-total').textContent = s.total ?? '--';

        const by = s.by_lifecycle || {};

        document.getElementById('research-validated-count').textContent = by.VALIDATED ?? '0';

        document.getElementById('research-rejected-count').textContent =

            (by.REJECTED || 0) + (by.DEGRADED || 0) + (by.RETIRED || 0);

        const w = s.worker || {};

        document.getElementById('research-worker-status').textContent =

            (w.status || '--') + ' · ' + (w.cycle_count || 0) + 'cyc';

        renderResearchOutcomeQuality(s.outcome_quality);

        loadResearchRegistry();

    } catch (e) {

        console.warn('research summary failed', e);

    }

}



function renderResearchOutcomeQuality(q) {

    const box = document.getElementById('research-outcome-quality');

    if (!box) return;

    if (!q || !q.available) {

        box.innerHTML = '<div class="text-textMuted italic">Outcome quality unavailable.</div>';

        return;

    }

    const srcs = q.reconstruction_sources || {};

    const srcDesc = Object.entries(srcs).map(([k, v]) => esc(k) + ': ' + v).join(' · ');

    const zeroCls = q.zero_r_outcomes > 0 ? 'text-accentRed' : 'text-accentGreen';

    box.innerHTML =

        '<div class="grid grid-cols-2 lg:grid-cols-5 gap-2">' +

        '<div><span class="text-textMuted">closed</span> <b class="text-white">' + (q.closed_outcomes ?? '--') + '</b></div>' +

        '<div><span class="text-textMuted">nonzero R</span> <b class="text-accentGreen">' + (q.nonzero_r_outcomes ?? '--') + '</b></div>' +

        '<div><span class="text-textMuted">zero R</span> <b class="' + zeroCls + '">' + (q.zero_r_outcomes ?? '--') + '</b></div>' +

        '<div><span class="text-textMuted">+R</span> <b class="text-accentGreen">' + (q.positive_r_outcomes ?? '--') + '</b></div>' +

        '<div><span class="text-textMuted">-R</span> <b class="text-accentRed">' + (q.negative_r_outcomes ?? '--') + '</b></div>' +

        '</div>' +

        '<div class="mt-1 text-textMuted">sources: ' + srcDesc + '</div>';

}



async function repairResearchOutcomes() {

    const box = document.getElementById('research-repair-result');

    box.innerHTML = '<div class="text-textMuted italic">Repairing zero-R outcomes from broker history (bounded, idempotent)…</div>';

    try {

        const res = await NX.api.post('/api/research/repair-outcomes', {}, { component: 'Research', action: 'REPAIR_OUTCOMES' });

        const body = res.ok ? res.body : { available: false, error: res.error };

        if (!body.available) {

            box.innerHTML = '<div class="text-accentRed italic">Repair unavailable: ' + esc(body.error || '') + '</div>';

            return;

        }

        const r = body.result || {};

        box.innerHTML = '<div class="text-accentGreen">Repair pass complete.</div>' +

            '<div class="mt-1">candidates: ' + (r.candidates ?? 0) +

            ' · repaired: <b class="text-accentGreen">' + (r.repaired ?? 0) + '</b>' +

            ' · unrepaired: ' + (r.unrepaired ?? 0) +

            ' · skipped_no_broker: ' + (r.skipped_no_broker ?? 0) + '</div>' +

            (r.repaired_rows || []).slice(0, 12).map(row =>

                '<div class="text-[10px]">ticket ' + esc(String(row.ticket)) +

                ' · R ' + row.old_r + ' → <b class="text-accentGreen">' + row.new_r + '</b>' +

                ' · pnl ' + row.new_pnl + ' · ' + esc(row.source) + '</div>').join('');

        loadResearchSummary();

    } catch (e) {

        console.warn('research repair failed', e);

        box.innerHTML = '<div class="text-accentRed italic">Repair failed.</div>';

    }

}



// BUG-075: registry `score` may be absent, the literal "null" (historical

// writer defect), '{}', valid JSON, or malformed. Never let any of them crash

// the research panel: decode defensively and emit a visible [UI_ERROR].

function safeScoreObj(v) {

    if (v == null) return null;

    if (typeof v === 'object') return v;

    try {

        const parsed = JSON.parse(v);

        return (parsed && typeof parsed === 'object') ? parsed : null;

    } catch (e) {

        console.error('[UI_ERROR] component=ResearchRegistry action=SCORE_DECODE value=' + String(v).slice(0, 60));

        return null;

    }

}

function safeScore(v) {

    const o = safeScoreObj(v);

    const fs = o && o.final_score;

    return (typeof fs === 'number' && isFinite(fs)) ? fs : '--';

}



async function loadResearchRegistry() {

    try {

        const res = await NX.api.get('/api/research/registry?limit=20', { component: 'ResearchRegistry', action: 'LOAD' });

        if (!res.ok) {

            const box = document.getElementById('research-registry');

            if (box) box.innerHTML = '<div class="text-accentRed italic">Research registry request failed: ' + esc(NX.api.msg(res, 'Registry unavailable')) + '</div>';

            return;

        }

        const body = res.body || {};

        if (!body.available) {

            const box = document.getElementById('research-registry');

            if (box) box.innerHTML = '<div class="text-textMuted italic">Research registry unavailable (available=false).</div>';

            return;

        }

        const box = document.getElementById('research-registry');

        const rows = body.registry || [];

        if (!rows.length) {

            box.innerHTML = '<div class="text-textMuted italic">No strategies in the registry yet. Run discovery first.</div>';

            return;

        }

        box.innerHTML = rows.map(r => {

            const sc = safeScore(r.score);

            const lc = esc(r.lifecycle || '--');

            return '<div class="flex items-center justify-between bg-darkBg/50 rounded px-2 py-1.5 border border-borderClr/40">' +

                '<span class="text-accentCyan font-bold">' + esc(r.strategy_id) + '</span>' +

                '<span class="text-textMuted">v' + esc(r.strategy_version.slice(0, 8)) + '</span>' +

                '<span class="text-textMuted">' + esc(r.feature_schema_id) + '</span>' +

                '<span class="text-accentGreen font-bold">' + esc(lc) + '</span>' +

                '<span class="text-textMuted">score ' + esc(String(sc)) + '</span>' +

                '</div>';

        }).join('');

    } catch (e) {

        console.warn('research registry failed', e);

    }

}



async function scanResearchDiscovery() {

    const box = document.getElementById('research-registry');

    box.innerHTML = '<div class="text-textMuted italic">Discovering candidates from experience ledger…</div>';

    try {

        const res = await NX.api.post('/api/research/discover', {}, { component: 'Research', action: 'DISCOVER' });

        const body = res.ok ? res.body : { available: false, error: res.error };

        if (!body.available) {

            box.innerHTML = '<div class="text-accentRed italic">Discovery unavailable: ' + esc(body.error || '') + '</div>';

            return;

        }

        box.innerHTML = '<div class="text-accentGreen">Dataset ' + esc(body.dataset_id) + ' · ' +

            body.samples + ' samples · ' + (body.candidates || []).length + ' candidates discovered.</div>' +

            (body.candidates || []).slice(0, 10).map(c =>

                '<div class="flex items-center justify-between bg-darkBg/50 rounded px-2 py-1.5 border border-borderClr/40">' +

                '<span class="text-accentCyan font-bold">' + esc(c.strategy_id) + '</span>' +

                '<span class="text-textMuted">' + esc(c.discovery_method) + '</span>' +

                '<span class="text-textMuted">' + (c.discovery_evidence.samples ?? '--') + ' samples</span>' +

                '</div>').join('');

        loadResearchSummary();

    } catch (e) {

        console.warn('research discovery failed', e);

    }

}



async function validateResearchCandidate() {

    const sid = (document.getElementById('research-validate-id').value || '').trim();

    const box = document.getElementById('research-validation-result');

    if (!sid) { box.innerHTML = '<div class="text-accentRed italic">Enter a strategy_id first.</div>'; return; }

    box.innerHTML = '<div class="text-textMuted italic">Running backtest → walk-forward → OOS → robustness → score…</div>';

    try {

        const res = await NX.api.post('/api/research/validate?strategy_id=' + encodeURIComponent(sid), {}, { component: 'Research', action: 'VALIDATE' });

        const body = res.ok ? res.body : { available: false, error: res.error };

        if (!body.available) {

            box.innerHTML = '<div class="text-accentRed italic">Validation unavailable: ' + esc(body.reason || body.error || '') + '</div>';

            return;

        }

        const r = body.result || {};

        box.innerHTML = '<div class="text-accentCyan font-bold">lifecycle: ' + esc(r.lifecycle) + '</div>' +

            '<div>expectancy_r: ' + esc(r.backtest?.expectancy_r ?? '--') + '</div>' +

            '<div>oos_expectancy_r: ' + esc(r.oos?.oos_expectancy_r ?? '--') + ' · oos_status: ' + esc(r.oos?.status ?? '--') + '</div>' +

            '<div>robustness: ' + esc(r.robustness?.status ?? '--') + '</div>' +

            '<div>score: ' + esc(safeScore(r.score)) + ' · verdict: ' + esc((safeScoreObj(r.score) || {}).verdict ?? '--') + '</div>';

        loadResearchSummary();

    } catch (e) {

        console.warn('research validate failed', e);

    }

}



// =============================================================================
// TASK-21: RESEARCH OBSERVABILITY (health / worker / diagnostics / detail)
// =============================================================================

async function loadResearchHealth() {

    try {

        const res = await fetch('/api/research/summary');

        if (!res.ok) return;

        const body = await res.json();

        if (!body.available || !body.summary) return;

        const s = body.summary;

        const by = s.by_lifecycle || {};

        const grid = document.getElementById('research-health-grid');

        if (!grid) return;

        const discovered = by.DISCOVERED ?? 0;

        const running = (by.BACKTESTING ?? 0) + (by.VALIDATING ?? 0) + (by.OOS_TESTING ?? 0) + (by.ROBUSTNESS_TESTING ?? 0);

        const validated = by.VALIDATED ?? 0;

        const rejected = by.REJECTED ?? 0;

        const shadow = by.SHADOW ?? 0;

        const active = by.ACTIVE ?? 0;

        const degraded = by.DEGRADED ?? 0;

        const retired = by.RETIRED ?? 0;

        // RC3 state contract: every bucket MUST exist in CandidateLifecycle
        // (src/nexus_scalp/research/models.py). No QUEUED/BLOCKED/FAILED rows.
        grid.innerHTML =

            '<div class="text-textMuted">Total</div><div class="text-accentCyan font-bold">' + (s.total ?? '--') + '</div>' +

            '<div class="text-textMuted">Discovered</div><div class="text-white">' + discovered + '</div>' +

            '<div class="text-textMuted">Running</div><div class="text-accentYellow font-bold">' + running + '</div>' +

            '<div class="text-textMuted">Validated</div><div class="text-accentGreen font-bold">' + validated + '</div>' +

            '<div class="text-textMuted">Shadow</div><div class="text-white">' + shadow + '</div>' +

            '<div class="text-textMuted">Active</div><div class="text-accentGreen">' + active + '</div>' +

            '<div class="text-textMuted">Rejected</div><div class="text-accentRed font-bold">' + rejected + '</div>' +

            '<div class="text-textMuted">Degraded</div><div class="text-accentRed">' + degraded + '</div>' +

            '<div class="text-textMuted">Retired</div><div class="text-gray-400">' + retired + '</div>';

    } catch (e) {

        console.warn('research health failed', e);

    }

}



async function loadResearchWorker() {

    try {

        const res = await fetch('/api/research/worker');

        if (!res.ok) return;

        const body = await res.json();

        if (!body.available) return;

        const w = body.worker || {};

        const panel = document.getElementById('research-worker-panel');

        if (!panel) return;

        const hb = w.heartbeat || {};

        const healthCls = w.health === 'HEALTHY' ? 'text-accentGreen' : (w.health === 'STUCK' || w.health === 'FAILED' ? 'text-accentRed' : 'text-accentYellow');

        const rt = w.runtime || {};

        panel.innerHTML =

            '<div class="flex justify-between"><span class="text-textMuted">health</span><b class="' + healthCls + '">' + esc(w.health || '--') + '</b></div>' +

            '<div class="flex justify-between"><span class="text-textMuted">state</span><span class="text-white">' + esc(hb.status || rt.status || '--') + '</span></div>' +

            '<div class="flex justify-between"><span class="text-textMuted">cycles</span><span class="text-white">' + (hb.cycle_count ?? rt.cycle_count ?? 0) + '</span></div>' +

            '<div class="flex justify-between"><span class="text-textMuted">last action</span><span class="text-gray-300">' + esc(hb.last_action || '--') + '</span></div>' +

            '<div class="flex justify-between"><span class="text-textMuted">current strategy</span><span class="text-gray-300">' + esc(hb.current_strategy || '--') + '</span></div>' +

            '<div class="flex justify-between"><span class="text-textMuted">current gate</span><span class="text-gray-300">' + esc(hb.current_gate || '--') + '</span></div>' +

            '<div class="flex justify-between"><span class="text-textMuted">queued</span><span class="text-white">' + (hb.queued_jobs ?? 0) + '</span></div>' +

            '<div class="flex justify-between"><span class="text-textMuted">failed</span><span class="text-accentRed">' + (hb.failed_jobs ?? 0) + '</span></div>' +

            '<div class="mt-1 text-textMuted">last error: <span class="text-accentRed">' + esc(hb.last_error || rt.last_error || 'none') + '</span></div>';

    } catch (e) {

        console.warn('research worker failed', e);

    }

}



async function loadResearchDiag() {

    try {

        const res = await fetch('/api/research/diagnostics');

        if (!res.ok) return;

        const body = await res.json();

        if (!body.available) return;

        const d = body;

        const panel = document.getElementById('research-diag-panel');

        if (!panel) return;

        const q = d.queue || {};

        let queueHtml = '';

        const qb = q.queued || {};

        const gateKeys = Object.keys(qb);

        if (gateKeys.length) {

            gateKeys.forEach(function (gk) {

                const st = qb[gk] || {};

                queueHtml += '<div class="flex justify-between"><span class="text-textMuted">' + esc(gk) + '</span><span>' +

                    Object.entries(st).map(function (e) { return esc(e[0]) + ':' + e[1]; }).join(' ') + '</span></div>';

            });

        } else {

            queueHtml = '<div class="text-textMuted">queue empty</div>';

        }

        const hm = d.heatmap || {};

        const bg = hm.by_gate || {};

        let heatHtml = '';

        const hk = Object.keys(bg).slice(0, 5);

        if (hk.length) {

            hk.forEach(function (g) { heatHtml += '<span class="text-accentRed">' + esc(g) + ' ' + bg[g] + '</span>&nbsp; '; });

        } else {

            heatHtml = '<span class="text-textMuted">no gate failures</span>';

        }

        const blocked = d.blocked_gates || [];

        let blockedHtml = blocked.length

            ? blocked.slice(0, 5).map(function (b) { return '<div class="text-accentRed">' + esc(b.gate_type) + ' ' + esc(b.status) + ' — ' + esc(b.failure_reason || '') + '</div>'; }).join('')

            : '<div class="text-textMuted">no blocked/failed gates</div>';

        panel.innerHTML =

            '<div class="mb-1"><span class="text-textMuted">queue</span></div>' + queueHtml +

            '<div class="mt-2 mb-1"><span class="text-textMuted">failures</span></div>' + heatHtml +

            '<div class="mt-2 mb-1"><span class="text-textMuted">blocked gates</span></div>' + blockedHtml;

    } catch (e) {

        console.warn('research diag failed', e);

    }

}



async function loadResearchDetail() {

    const sid = (document.getElementById('research-detail-id').value || '').trim();

    const box = document.getElementById('research-detail-result');

    if (!sid) { box.innerHTML = '<div class="text-accentRed italic">Enter a strategy_id first.</div>'; return; }

    box.innerHTML = '<div class="text-textMuted italic">Tracing strategy lifecycle…</div>';

    try {

        const res = await fetch('/api/research/detail/' + encodeURIComponent(sid));

        if (!res.ok) { box.innerHTML = '<div class="text-accentRed italic">Detail unavailable.</div>'; return; }

        const body = await res.json();

        if (!body.available) { box.innerHTML = '<div class="text-accentRed italic">' + esc(body.reason || 'NOT_IN_REGISTRY') + '</div>'; return; }

        const t = body.detail || {};

        const entry = t.registry || {};

        let html = '<div class="text-accentCyan font-bold mb-1">STRATEGY ' + esc(entry.strategy_id || sid) + '</div>';

        html += '<div class="grid grid-cols-2 gap-x-4 gap-y-0.5 mb-2">' +

            '<div class="text-textMuted">version</div><div class="text-white">' + esc(entry.strategy_version || '--') + '</div>' +

            '<div class="text-textMuted">lifecycle</div><div class="' + (entry.lifecycle === 'VALIDATED' ? 'text-accentGreen' : (entry.lifecycle === 'REJECTED' ? 'text-accentRed' : 'text-accentYellow')) + '">' + esc(entry.lifecycle || '--') + '</div>' +

            '<div class="text-textMuted">discovery</div><div class="text-gray-300">' + esc(entry.discovery_source || '--') + '</div>' +

            '<div class="text-textMuted">window</div><div class="text-gray-300">' + esc(entry.discovery_window || '--') + '</div>' +

            '</div>';

        const br = t.blocked_reason || {};

        if (br.blocked || br.status) {

            html += '<div class="mb-2 p-2 rounded border border-accentRed/30 bg-accentRed/5">' +

                '<div class="text-accentRed font-bold">WHY WAITING: ' + esc(br.current_gate || '--') + ' · ' + esc(br.status || '--') + '</div>' +

                '<div class="text-gray-300">' + esc(br.reason || '') + '</div>' +

                (br.required ? '<div class="text-textMuted">required: ' + esc(br.required) + '</div>' : '') +

                '</div>';

        }

        const gates = t.gates || [];

        if (gates.length) {

            html += '<div class="mb-1 text-textMuted uppercase tracking-widest">Gates</div>';

            gates.forEach(function (g) {

                const st = g.status;

                const cls = st === 'PASSED' ? 'text-accentGreen' : (st === 'FAILED' || st === 'ERROR' || st === 'BLOCKED' ? 'text-accentRed' : (st === 'RUNNING' ? 'text-accentYellow' : 'text-gray-300'));

                html += '<div class="flex justify-between"><span class="text-gray-300">' + esc(g.gate_type) + '</span><span class="' + cls + '">' + esc(st) + '</span>' +

                    (g.duration_ms ? '<span class="text-textMuted">' + Math.round(g.duration_ms) + 'ms</span>' : '') + '</div>';

                if (g.failure_reason) html += '<div class="text-accentRed pl-3">' + esc(g.failure_reason) + '</div>';

                if (g.evidence_id) html += '<div class="text-textMuted pl-3">evidence: ' + esc(g.evidence_id) + '</div>';

            });

        }

        const events = t.events || [];

        if (events.length) {

            html += '<div class="mt-2 mb-1 text-textMuted uppercase tracking-widest">Timeline (' + events.length + ' events)</div>';

            html += '<div class="max-h-40 overflow-y-auto">';

            events.slice(0, 40).forEach(function (e) {

                const when = (e.occurred_at || '').slice(11, 19);

                html += '<div class="text-gray-300"><span class="text-textMuted">' + esc(when) + '</span> <span class="text-accentCyan">' + esc(e.event_type) + '</span> ' + esc(e.message || '') + '</div>';

            });

            html += '</div>';

        }

        const runs = t.runs || [];

        if (runs.length) {

            html += '<div class="mt-2 mb-1 text-textMuted uppercase tracking-widest">Runs</div>';

            runs.slice(0, 5).forEach(function (r) {

                html += '<div class="text-gray-300">' + esc(r.run_id) + ' · ' + esc(r.status || '--') + ' · ' + esc(r.run_outcome || '--') + '</div>';

            });

        }

        const snapshots = t.snapshots || [];

        if (snapshots.length) {

            html += '<div class="mt-2 mb-1 text-textMuted uppercase tracking-widest">Reproducibility Snapshot</div>';

            const sn = snapshots[0];

            html += '<div class="grid grid-cols-2 gap-x-4 gap-y-0.5">' +

                '<div class="text-textMuted">dataset</div><div class="text-gray-300">' + esc(sn.dataset_version || '--') + '</div>' +

                '<div class="text-textMuted">schema</div><div class="text-gray-300">' + esc(sn.feature_schema_version || '--') + '</div>' +

                '<div class="text-textMuted">engine</div><div class="text-gray-300">' + esc(sn.engine_version || '--') + '</div>' +

                '<div class="text-textMuted">research hash</div><div class="text-gray-300">' + esc((sn.research_hash || '--').slice(0, 16)) + '</div>' +

                '</div>';

        }

        const inv = t.invariant || {};

        if (inv.valid !== undefined) {

            html += '<div class="mt-2">invariant: <b class="' + (inv.valid ? 'text-accentGreen' : 'text-accentRed') + '">' + (inv.valid ? 'VALID' : 'BROKEN') + '</b>' +

                (inv.problems && inv.problems.length ? ' <span class="text-accentRed">' + esc(inv.problems.join('; ')) + '</span>' : '') + '</div>';

        }

        box.innerHTML = html;

    } catch (e) {

        console.warn('research detail failed', e);

        box.innerHTML = '<div class="text-accentRed italic">detail failed</div>';

    }

}



async function runResearchPreflight() {

    const sid = (document.getElementById('research-detail-id').value || '').trim();

    const box = document.getElementById('research-detail-result');

    if (!sid) { box.innerHTML = '<div class="text-accentRed italic">Enter a strategy_id first.</div>'; return; }

    box.innerHTML = '<div class="text-textMuted italic">Running validation pre-flight…</div>';

    try {

        const res = await fetch('/api/research/preflight?strategy_id=' + encodeURIComponent(sid));

        if (!res.ok) { box.innerHTML = '<div class="text-accentRed italic">Preflight unavailable.</div>'; return; }

        const body = await res.json();

        if (!body.available) { box.innerHTML = '<div class="text-accentRed italic">Preflight unavailable.</div>'; return; }

        const p = body.preflight || {};

        const cls = p.status === 'PREFLIGHT PASS' ? 'text-accentGreen' : 'text-accentRed';

        let html = '<div class="' + cls + ' font-bold mb-1">' + esc(p.status) + '</div>';

        const c = p.checks || {};

        html += '<div class="grid grid-cols-2 gap-x-4 gap-y-0.5">';

        Object.entries(c).forEach(function (kv) {

            html += '<div class="text-textMuted">' + esc(kv[0]) + '</div><div class="text-gray-300">' + esc(String(kv[1])) + '</div>';

        });

        html += '</div>';

        if (p.blockers && p.blockers.length) {

            html += '<div class="mt-1 text-accentRed">blockers: ' + p.blockers.map(esc).join(', ') + '</div>';

        }

        box.innerHTML = html;

    } catch (e) {

        console.warn('research preflight failed', e);

        box.innerHTML = '<div class="text-accentRed italic">preflight failed</div>';

    }

}



// TASK-21: also load health/worker/diag with the summary refresh.
async function loadResearchSummaryWithObs() {

    await loadResearchSummary();

    await loadResearchHealth();

    await loadResearchWorker();

    await loadResearchDiag();

}

// PHASE 09: TRADE INTELLIGENCE BRAIN (lifecycle / autopsy / behavior / evolution)

// =============================================================================



// =============================================================================
// MARKET RADAR (Intel Hub) - render binding ONLY.
// Renders the backend `radar` object attached to the canonical
// get_system_state() snapshot. We NEVER recompute or derive any trading
// intelligence here; every value is taken verbatim from the backend payload.
// =============================================================================

function renderMarketRadar(radar) {

    const statusEl   = document.getElementById('radar-status');
    const regimeEl   = document.getElementById('radar-regime');
    const typeEl     = document.getElementById('radar-best-type');
    const dirEl      = document.getElementById('radar-direction');
    const qualEl     = document.getElementById('radar-quality');
    const compatEl   = document.getElementById('radar-compatible');
    const countEl    = document.getElementById('radar-count');
    const newsEl     = document.getElementById('radar-news');
    const decisionEl = document.getElementById('radar-decision');
    const updatedEl  = document.getElementById('radar-updated');

    // Empty / missing radar: explicit waiting state - NEVER fake numbers.
    if (!radar || typeof radar !== 'object') {

        if (statusEl)   { statusEl.textContent = 'NO RADAR DATA'; statusEl.className = 'text-[10px] font-black px-2 py-1 rounded border bg-slate-500/10 text-slate-300 border-slate-500/30'; }
        if (regimeEl)   regimeEl.textContent = '-';
        if (typeEl)     typeEl.textContent = '-';
        if (dirEl)      { dirEl.textContent = '-'; dirEl.className = 'text-[10px] font-bold px-2 py-0.5 rounded border bg-slate-500/10 text-slate-300 border-slate-500/30'; }
        if (qualEl)     qualEl.textContent = '-';
        if (compatEl)   compatEl.textContent = '-';
        if (countEl)    countEl.textContent = '-';
        if (newsEl)     { newsEl.textContent = '-'; newsEl.className = 'text-[10px] font-bold px-2 py-0.5 rounded border bg-slate-500/10 text-slate-300 border-slate-500/30'; }
        if (decisionEl) decisionEl.textContent = 'Awaiting radar snapshot...';
        if (updatedEl)  updatedEl.textContent = '-';
        return;

    }

    // Status badge - exact backend `state` value, distinct visual states, no
    // invented terminology. A Radar candidate is NEVER shown as an approved
    // trade (e.g. SETUP_READY + BLOCKED_BY_GUARDIAN_UNSAFE_REGIME stays
    // visibly distinct from ENTRY_APPROVED).
    const state = radar.state || 'NO_SETUP';
    const STATE_STYLE = {
        SETUP_READY: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
        WATCHING:    'bg-cyan-500/10 text-cyan-400 border-cyan-500/30',
        NO_SETUP:    'bg-slate-500/10 text-slate-300 border-slate-500/30',
    };
    if (statusEl) {
        statusEl.textContent = state;
        statusEl.className = 'text-[10px] font-black px-2 py-1 rounded border ' + (STATE_STYLE[state] || STATE_STYLE.NO_SETUP);
    }

    if (regimeEl)   regimeEl.textContent = radar.regime != null ? String(radar.regime) : '-';
    if (countEl)    countEl.textContent = radar.candidate_count != null ? String(radar.candidate_count) : '-';

    const best = radar.best_setup && typeof radar.best_setup === 'object' ? radar.best_setup : null;
    if (typeEl)     typeEl.textContent = best && best.setup_type ? String(best.setup_type) : '-';
    if (qualEl)     qualEl.textContent = best && typeof best.quality === 'number' ? (best.quality * 100).toFixed(1) + '%' : '-';

    // Direction from factors.direction: +1 = BUY, -1 = SELL (only if present).
    let dirText = '-', dirCls = 'bg-slate-500/10 text-slate-300 border-slate-500/30';
    if (best && best.factors && best.factors.direction != null) {
        if (best.factors.direction === 1)       { dirText = 'BUY';  dirCls = 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'; }
        else if (best.factors.direction === -1) { dirText = 'SELL'; dirCls = 'bg-rose-500/10 text-rose-400 border-rose-500/30'; }
    }
    if (dirEl) { dirEl.textContent = dirText; dirEl.className = 'text-[10px] font-bold px-2 py-0.5 rounded border ' + dirCls; }

    if (compatEl) {
        const cs = best && Array.isArray(best.compatible_strategies) ? best.compatible_strategies : [];
        compatEl.textContent = cs.length ? cs.join(', ') : '-';
    }

    // News state + freshness (radar.updated_at is the authoritative timestamp).
    const news = radar.news_state;
    let newsCls = 'bg-slate-500/10 text-slate-300 border-slate-500/30';
    if (news === 'HIGH_IMPACT') newsCls = 'bg-rose-500/10 text-rose-400 border-rose-500/30';
    else if (news === 'MEDIUM_IMPACT') newsCls = 'bg-amber-500/10 text-amber-400 border-amber-500/30';
    else if (news === 'LOW_IMPACT' || news === 'CALM') newsCls = 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30';
    if (newsEl) {
        newsEl.textContent = news != null ? String(news) : '-';
        newsEl.className = 'text-[10px] font-bold px-2 py-0.5 rounded border ' + newsCls;
    }

    // Decision reason - keeps a blocked candidate visibly distinct from approval.
    if (decisionEl) decisionEl.textContent = radar.decision_reason != null ? String(radar.decision_reason) : '-';

    if (updatedEl)  updatedEl.textContent = radar.updated_at != null ? String(radar.updated_at) : '-';

}


function esc(s) {

    return String(s == null ? '' : s)

        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

        .replace(/"/g, '&quot;');

}



async function loadIntelligenceSummary() {

    try {

        const res = await fetch('/api/intelligence/summary');

        if (!res.ok) return;

        const body = await res.json();

        if (!body.available) return;

        document.getElementById('intel-lifecycle-count').textContent = body.lifecycle_events ?? '--';

        document.getElementById('intel-autopsy-count').textContent = body.autopsies ?? '--';

        const w = body.worker || {};

        document.getElementById('intel-worker-status').textContent =

            'worker: ' + (w.status || '--') + ' · cycles: ' + (w.cycle_count || 0);

        if (body.last_suitability) {

            const s = body.last_suitability;

            document.getElementById('intel-suitability').innerHTML =

                '<b class="text-accentCyan">' + esc(s.decision) + '</b> · ' +

                'suitability ' + s.suitability_score + ' · ' + esc(s.reason) +

                ' <span class="text-textMuted">(' + esc(s.strategy_id) + ')</span>';

        }

        // Load evolution candidates whenever the panel is refreshed.

        loadIntelligenceEvolution();

        loadIntelligenceAutopsies();
        renderMarketRadar(liveUiSnapshot && liveUiSnapshot.radar);


    } catch (e) {

        console.warn('intelligence summary failed', e);

    }

}



async function loadIntelligenceTimeline() {

    const ticket = (document.getElementById('intel-ticket-input').value || '').trim();

    if (!ticket) return;

    const box = document.getElementById('intel-timeline');

    box.innerHTML = '<div class="text-textMuted italic">Loading timeline…</div>';

    try {

        const res = await fetch('/api/intelligence/positions/' + encodeURIComponent(ticket) + '/timeline');

        const body = await res.json();

        if (!body.available || !body.events.length) {

            box.innerHTML = '<div class="text-textMuted italic">No lifecycle events for ticket ' + esc(ticket) + '.</div>';

            return;

        }

        box.innerHTML = body.events.map(ev =>

            '<div class="flex items-start justify-between bg-darkBg/50 rounded px-2 py-1.5 border border-borderClr/40">' +

                '<span><span class="text-accentCyan font-bold">' + esc(ev.event_type) + '</span>' +

                ' <span class="text-textMuted">' + esc(ev.detail || '') + '</span></span>' +

                '<span class="text-textMuted whitespace-nowrap ml-2">MFE ' + (ev.performance ? ev.performance.mfe : 0) +

                ' · MAE ' + (ev.performance ? ev.performance.mae : 0) + '</span>' +

            '</div>'

        ).join('');

    } catch (e) {

        box.innerHTML = '<div class="text-textMuted italic">Timeline load failed.</div>';

    }

}



async function loadIntelligenceAutopsies() {

    try {

        const res = await fetch('/api/intelligence/autopsies?limit=8');

        const body = await res.json();

        const box = document.getElementById('intel-autopsies');

        if (!body.available || !body.autopsies.length) {

            box.innerHTML = '<div class="text-textMuted italic">No autopsies available.</div>';

            return;

        }

        box.innerHTML = body.autopsies.map(a =>

            '<div class="bg-darkBg/50 rounded px-2 py-1.5 border border-borderClr/40">' +

                '<div class="flex justify-between"><span class="text-accentCyan font-bold">' + esc(a.quality_verdict || 'UNKNOWN') +

                '</span><span class="text-textMuted">ticket ' + esc(a.ticket) + '</span></div>' +

                '<div class="text-textMuted">' + esc(a.narrative || '') + '</div>' +

            '</div>'

        ).join('');

        loadIntelligenceBehavior();

        loadIntelligenceAnomalies();

    } catch (e) {

        console.warn('autopsies load failed', e);

    }

}



// Behavior detections: real data from /api/intelligence/behavior.

// NO DATA renders an explicit "NO DATA" state - nothing is fabricated.

async function loadIntelligenceBehavior() {

    try {

        const res = await fetch('/api/intelligence/behavior?limit=8');

        const body = await res.json();

        const countEl = document.getElementById('intel-behavior-count');

        const box = document.getElementById('intel-behavior');

        const detections = body.detections || [];

        if (!body.available || detections.length === 0) {

            if (countEl) countEl.textContent = '0';

            if (box) box.innerHTML = '<div class="text-textMuted italic text-[11px]">NO DATA — no behavior detections recorded.</div>';

            return;

        }

        if (countEl) countEl.textContent = detections.length;

        if (box) {

            box.innerHTML = detections.map(b =>

                '<div class="bg-darkBg/50 rounded px-2 py-1.5 border border-borderClr/40">' +

                    '<div class="flex justify-between"><span class="text-accentCyan font-bold">' + esc(b.pattern || b.behavior_type || b.behavior || 'UNKNOWN') +

                    '</span><span class="text-textMuted">' + esc(b.symbol || '') + ' · ' + esc(String(b.detected_at || '').substring(0, 16)) + '</span></div>' +

                    '<div class="text-textMuted">' + esc(b.summary || b.detail || '') + '</div>' +

                '</div>'

            ).join('');

        }

    } catch (e) {

        console.warn('behavior load failed', e);

    }

}



// Anomaly events: evidence-based inconsistencies from /api/intelligence/anomalies.

async function loadIntelligenceAnomalies() {

    try {

        const res = await fetch('/api/intelligence/anomalies?limit=8');

        const body = await res.json();

        const box = document.getElementById('intel-anomalies');

        const anomalies = body.anomalies || [];

        if (!body.available || anomalies.length === 0) {

            if (box) box.innerHTML = '<div class="text-textMuted italic text-[11px]">NO DATA — no anomaly events recorded.</div>';

            return;

        }

        if (box) {

            box.innerHTML = anomalies.map(a => {

                const obs = (a.observation_count > 1) ? ' <span class="text-textMuted">(x' + esc(String(a.observation_count)) + ')</span>' : '';

                const range = (a.first_seen && a.last_seen && a.first_seen !== a.last_seen)

                    ? ' <span class="text-textMuted">' + esc(String(a.first_seen).substring(0, 16) + '..' + String(a.last_seen).substring(0, 16)) + '</span>'

                    : '';

                return '<div class="bg-darkBg/50 rounded px-2 py-1.5 border border-borderClr/40">' +

                    '<div class="flex justify-between"><span class="text-amber-400 font-bold">' + esc(a.anomaly_type || a.category || 'UNKNOWN') + obs +

                    '</span><span class="text-textMuted">' + esc(String(a.severity || '')) + ' · ' + esc(String(a.detected_at || '').substring(0, 16)) + range + '</span></div>' +

                    '<div class="text-textMuted">' + esc((a.evidence && a.evidence.explanation) ? a.evidence.explanation : JSON.stringify(a.evidence || '')) + '</div>' +

                '</div>';

            }).join('');

        }

    } catch (e) {

        console.warn('anomalies load failed', e);

    }

}



async function loadIntelligenceEvolution() {

    try {

        const res = await fetch('/api/intelligence/evolution?limit=8');

        const body = await res.json();

        const box = document.getElementById('intel-evolution');

        if (!body.available || !body.candidates.length) {

            box.innerHTML = '<div class="text-textMuted italic">No evolution candidates. Click "Scan Now" to discover strategy variations.</div>';

            return;

        }

        document.getElementById('intel-evolution-count').textContent = body.candidates.length;

        box.innerHTML = body.candidates.map(c =>

            '<div class="bg-darkBg/50 rounded px-2 py-1.5 border border-borderClr/40">' +

                '<div class="flex justify-between"><span class="text-accentCyan font-bold">' + esc(c.status) +

                '</span><span class="text-textMuted">' + esc(c.candidate_id.slice(0, 14)) + '</span></div>' +

                '<div class="text-textMuted">' + esc(c.hypothesis) + '</div>' +

            '</div>'

        ).join('');

    } catch (e) {

        console.warn('evolution load failed', e);

    }

}



async function scanIntelligenceEvolution() {

    const box = document.getElementById('intel-evolution');

    box.innerHTML = '<div class="text-textMuted italic">Scanning for strategy variations…</div>';

    try {

        const res = await NX.api.post('/api/intelligence/evolution/scan', {}, { component: 'Intelligence', action: 'EVOLUTION_SCAN' });

        const body = res.ok ? res.body : { available: false, error: res.error };

        if (!body.available) {

            box.innerHTML = '<div class="text-textMuted italic">Scan failed.</div>';

            return;

        }

        box.innerHTML = (body.candidates && body.candidates.length)

            ? body.candidates.map(c =>

                '<div class="bg-darkBg/50 rounded px-2 py-1.5 border border-borderClr/40">' +

                    '<span class="text-accentCyan font-bold">' + esc(c.status) + '</span> ' +

                    esc(c.hypothesis) +

                '</div>').join('')

            : '<div class="text-textMuted italic">No new candidates discovered.</div>';

    } catch (e) {

        box.innerHTML = '<div class="text-textMuted italic">Scan failed.</div>';

    }

}



document.addEventListener('DOMContentLoaded', () => {

    // UI source-of-control: execution-mode selector (LIVE/SIMULATION/REPLAY).
    // Every change is persisted via the backend (settings DB) and applied
    // to the engine; the runtime badge always shows REAL connection state.
    const modeSel = document.getElementById('execution-mode-selector');
    if (modeSel && !modeSel.dataset.modeBound) {
        modeSel.dataset.modeBound = '1';
        modeSel.addEventListener('change', async () => {
            const requested = modeSel.value;
            try {
                const res = await NX.api.post('/api/engine/mode', { mode: requested }, { component: 'Engine', action: 'SET_MODE' });
                if (res.ok && res.body && res.body.success) {
                    const rt = res.body.runtime_mode || requested;
                    const badge = document.getElementById('runtime-mode-badge');
                    if (badge) {
                        badge.textContent = rt;
                        const isLive = String(rt).indexOf('LIVE') === 0;
                        const isDegraded = String(rt).indexOf('DISCONNECTED') !== -1 || String(rt).indexOf('BLOCKED') !== -1;
                        badge.className = 'text-[10px] px-2 py-0.5 rounded font-black border ' +
                            (isLive && !isDegraded
                                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                                : isDegraded
                                    ? 'bg-rose-500/10 text-rose-400 border-rose-500/30'
                                    : 'bg-amber-500/10 text-amber-400 border-amber-500/30');
                    }
                    console.log('[ENGINE_UI] mode set to ' + res.body.mode + ' (runtime: ' + rt + ')');
                } else {
                    // Revert to the authoritative server value on failure.
                    console.warn('[UI_ERROR] component=Engine action=SET_MODE ' + NX.api.msg(res, 'Mode change failed.'));
                    if (window.__serverExecutionMode) modeSel.value = window.__serverExecutionMode;
                }
            } catch (err) {
                console.error('[UI_ERROR] component=Engine action=SET_MODE', err);
                if (window.__serverExecutionMode) modeSel.value = window.__serverExecutionMode;
            }
        });
    }
    // Track the authoritative server-reported mode so a failed change can revert.
    window.__serverExecutionMode = modeSel ? modeSel.value : null;

    setTimeout(initAccountIntelligence, 1200);

    setTimeout(loadIntelligenceSummary, 1500);

    // Periodically refresh account intelligence (broker history sync fills in

    // real values after engine start; charts need fresh points to render).

    setInterval(() => {

        loadAccountPerformance();

        loadAdvancedMetrics();

        loadAccountCharts();

        loadClosedTrades();

    }, 30000);

    const observer = new MutationObserver(() => {

        const tab = document.getElementById('tab-account');

        if (tab && !tab.classList.contains('hidden') && !window.__acctIntelLoaded) {

            window.__acctIntelLoaded = true;

            initAccountIntelligence();

        }

        const aiTab = document.getElementById('tab-ai-analysis');

        if (aiTab && !aiTab.classList.contains('hidden') && !window.__aiIntelLoaded) {

            window.__aiIntelLoaded = true;

            loadIntelligenceSummary();

        }

    });

    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });

});



// PHASE 12: NEWS INTELLIGENCE (live feed / state / fetch / analyze)

// =============================================================================
// PRO HOT RELOAD: News engine ENABLE / DISABLE (no restart)
// -----------------------------------------------------------------------------
// Toggle is the AUTHORITATIVE UI for the real backend flag (news.enabled).
// Enabled -> engine constructs worker+gate, badge shows state (NORMAL/STALE/...);
// Disabled -> worker stopped, /api/news/* return available=false, badge OFF.
// Persists via runtime_config (validated, restart-persistent). News can never
// force a trade (bounded gate invariant) even when ON.
// =============================================================================
function syncNewsToggleUI(enabled) {
    const cb = document.getElementById('news-toggle');
    const lbl = document.getElementById('news-toggle-label');
    const st = document.getElementById('news-toggle-state');
    if (cb) cb.checked = !!enabled;
    if (lbl) { lbl.textContent = enabled ? 'NEWS ON' : 'NEWS OFF'; lbl.className = 'text-[10px] font-black ' + (enabled ? 'text-emerald-400' : 'text-slate-400'); }
    if (st) st.textContent = enabled ? 'ENABLED' : 'DISABLED';
}

async function refreshNewsToggleState() {
    try {
        const res = await fetch('/api/news/toggle-state');
        if (!res.ok) return;
        const body = await res.json();
        if (body && typeof body.enabled === 'boolean') syncNewsToggleUI(body.enabled);
    } catch (_e) { /* silent — badge will reflect actual state on next loadNewsState */ }
}

// News Auto Analysis — local deterministic toggle (NO API key / NO endpoint).
// ON: worker auto-analyzes each cycle for more accuracy. OFF: worker still
// ingests, skips auto-analysis. Persists via runtime_config (hot-reload).
function syncNewsAutoUI(enabled) {
    const cb = document.getElementById("news-auto-toggle");
    const lbl = document.getElementById("news-auto-label");
    const st = document.getElementById("news-auto-state");
    if (cb) cb.checked = !!enabled;
    if (lbl) { lbl.textContent = enabled ? "AUTO ON" : "AUTO OFF"; lbl.className = "text-[10px] font-black " + (enabled ? "text-emerald-400" : "text-slate-400"); }
    if (st) st.textContent = enabled ? "AUTO ON" : "AUTO OFF";
}
async function refreshNewsAutoState() {
    try {
        const res = await fetch("/api/news/auto-analysis");
        if (!res.ok) return;
        const body = await res.json();
        if (body && typeof body.enabled === "boolean") syncNewsAutoUI(body.enabled);
    } catch (_e) { /* silent — next loadNewsState refreshes */ }
}
async function toggleNewsAutoAnalysis(enabled) {
    const cb = document.getElementById("news-auto-toggle");
    if (cb) cb.disabled = true;
    setNewsStatus((enabled ? "enabling" : "disabling") + " news auto analysis...", false);
    try {
        const res = await fetch("/api/news/auto-analysis", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: !!enabled }) });
        const body = await res.json().catch(() => ({}));
        if (!res.ok || !body.success) {
            const msg = (body && (body.error || body.reason)) || ("HTTP " + res.status);
            throw new Error(msg);
        }
        syncNewsAutoUI(!!body.enabled);
        setNewsStatus("news auto analysis " + (body.enabled ? "ENABLED" : "DISABLED") + " (v" + (body.runtime_version ?? "?") + ")", false);
    } catch (e) {
        console.error("[UI_ERROR] component=News endpoint=/api/news/auto-analysis action=TOGGLE message=" + (e && e.message));
        setNewsStatus("news auto toggle failed: " + (e && e.message), true);
        await refreshNewsAutoState();
    } finally {
        if (cb) cb.disabled = false;
    }
}

async function toggleNewsEngine(enabled) {
    const cb = document.getElementById('news-toggle');
    if (cb) cb.disabled = true;
    setNewsStatus((enabled ? 'enabling' : 'disabling') + ' news engine...', false);
    try {
        const res = await fetch('/api/news/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !!enabled }) });
        const body = await res.json().catch(() => ({}));
        if (!res.ok || !body.success) {
            const msg = (body && (body.error || body.reason)) || ('HTTP ' + res.status);
            throw new Error(msg);
        }
        syncNewsToggleUI(!!body.enabled);
        setNewsStatus('news engine ' + (body.enabled ? 'ENABLED' : 'DISABLED') + ' (v' + (body.runtime_version ?? '?') + ')', false);
        await loadNewsState();
    } catch (e) {
        console.error('[UI_ERROR] component=News endpoint=/api/news/toggle action=TOGGLE message=' + (e && e.message));
        setNewsStatus('news toggle failed: ' + (e && e.message), true);
        await refreshNewsToggleState();
    } finally {
        if (cb) cb.disabled = false;
    }
}


function setNewsStatus(msg, isError) {

    const el = document.getElementById('news-status-line');

    if (!el) return;

    if (!msg) { el.classList.add('hidden'); return; }

    el.classList.remove('hidden');

    el.textContent = (isError ? '⚠ ' : '') + msg + ' — ' + new Date().toLocaleTimeString();

    el.style.color = isError ? '#f87171' : '#64748b';

}



async function loadNewsState() {
    try {
        refreshNewsToggleState().catch(() => {});
        refreshNewsAutoState().catch(() => {});
        const res = await fetch('/api/news/state');

        if (!res.ok) {

            console.error('[UI_ERROR] component=News endpoint=/api/news/state status=' + res.status);

            throw new Error('HTTP ' + res.status);

        }

        const body = await res.json();

        if (!body.available) {
            document.getElementById('news-state-value').textContent = 'OFF';
            document.getElementById('news-state-badge').textContent = 'OFF';
            setNewsStatus('news engine unavailable (available=false)', true);
            return;
        }
        syncNewsToggleUI(true);

        const state = body.state || 'NORMAL';

        document.getElementById('news-state-value').textContent = state;

        document.getElementById('news-state-badge').textContent = state;

        const badge = document.getElementById('news-nav-state');

        if (badge) { badge.textContent = state; badge.className = 'ml-auto text-[9px] font-black px-1.5 py-0.5 rounded border ' + stateColor(state); }

        document.getElementById('news-xauusd-rel').textContent = (body.xauusd_relevance * 100).toFixed(0) + '%';

        document.getElementById('news-bull').textContent = (body.bullish_score * 100).toFixed(0) + '%';

        document.getElementById('news-bear').textContent = (body.bearish_score * 100).toFixed(0) + '%';

        document.getElementById('news-events').textContent = body.active_event_count ?? 0;
        // Truth hint: when last 100 are all NEUTRAL junk, user sees 0% with no context — surface it
        try {
            const bh = document.getElementById('news-bb-hint');
            if (bh) bh.textContent = (body.bullish_score===0 && body.bearish_score===0 && (body.xauusd_relevance||0)>0.3) ? 'junk NEUTRAL filtered' : '';
            const ah = document.getElementById('news-active-hint');
            if (ah) ah.textContent = (body.active_event_count===0 && (body.xauusd_relevance||0)>0.3) ? 'no high-impact' : '';
        } catch(_){}

        setNewsStatus('state=' + state + ' events=' + (body.active_event_count ?? 0));

        loadNewsFeed();

        loadNewsKeywords();

    } catch (e) {

        console.error('[UI_ERROR] component=News endpoint=/api/news/state action=LOAD message=' + (e && e.message));

        setNewsStatus('news state failed: ' + (e && e.message), true);

        const val = document.getElementById('news-state-value');

        if (val && (val.textContent === '--' || val.textContent === 'OFF')) val.textContent = 'ERR';

    }

}



// search-as-you-type for the keyword dataset filter

let __newsKwSearchTimer = null;

function bindNewsKeywordSearch() {

    const input = document.getElementById('news-kw-search');

    if (!input || input.dataset.bound) return;

    input.dataset.bound = '1';

    input.addEventListener('input', () => {

        clearTimeout(__newsKwSearchTimer);

        __newsKwSearchTimer = setTimeout(loadNewsKeywords, 300);

    });

}



function stateColor(state) {

    const colors = {

        BREAKING: 'bg-rose-500/20 text-rose-300 border-rose-500/40',

        HIGH_IMPACT: 'bg-orange-500/20 text-orange-300 border-orange-500/40',

        CONFLICTED: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/40',

        ELEVATED: 'bg-amber-500/20 text-amber-300 border-amber-500/40',

        STALE: 'bg-slate-500/20 text-slate-300 border-slate-500/40',

        NORMAL: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40',

    };

    return colors[state] || colors.NORMAL;

}



// =============================================================================

// NEWS IMPACT TIMELINE CHART (canvas line chart: bullish/bearish/neutral)

// =============================================================================

let __newsTfSec = 900; // default 15m buckets

let __newsTfHours = 48;



function setNewsTimeframe(bucketSec) {

    __newsTfSec = bucketSec;

    // widen the lookback window as buckets grow so the chart stays useful

    __newsTfHours = bucketSec >= 86400 ? 168 : bucketSec >= 14400 ? 72 : 48;

    document.querySelectorAll('[id^="news-tf-"]').forEach(b => {

        const active = (b.id === 'news-tf-15m' && bucketSec === 900) ||

                       (b.id === 'news-tf-1h' && bucketSec === 3600) ||

                       (b.id === 'news-tf-4h' && bucketSec === 14400) ||

                       (b.id === 'news-tf-1d' && bucketSec === 86400);

        b.className = active

            ? 'px-2.5 py-1 rounded-md bg-white text-slate-900 shadow-sm transition'

            : 'px-2.5 py-1 rounded-md text-slate-400 hover:text-white transition';

    });

    loadNewsTimeline();

}



async function loadNewsTimeline() {
    // If News tab is hidden, defer until visible (canvas has zero size while hidden)
    try {
        const tab = document.getElementById('tab-news');
        if (tab && tab.classList.contains('hidden')) {
            // will be retried on tab switch
            setTimeout(loadNewsTimeline, 800);
            return;
        }
    } catch(_) {}
    try {

        const res = await fetch('/api/news/timeline?bucket_sec=' + __newsTfSec + '&hours_back=' + __newsTfHours);

        if (!res.ok) throw new Error('HTTP ' + res.status);

        const body = await res.json();

        if (!body.available) return;

        drawNewsImpactChart(body.buckets || []);

    } catch (e) {

        console.warn('news timeline failed', e);

    }

}



function drawNewsImpactChart(buckets) {

    const canvas = document.getElementById('newsImpactChart');
    const wrap = canvas ? canvas.parentElement : null;
    const emptyEl = document.getElementById('news-timeline-empty');
    const countEl = document.getElementById('news-timeline-count');
    const bucketsEl = document.getElementById('news-timeline-buckets');
    const windowEl = document.getElementById('news-timeline-window');
    const topEl = document.getElementById('news-timeline-top');
    const dotEl = document.getElementById('news-timeline-dot');
    const tipEl = document.getElementById('news-timeline-tip');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    let rect = canvas.getBoundingClientRect();
    if ((rect.width|0) < 10 || (rect.height|0) < 10) {
        if (wrap) rect = wrap.getBoundingClientRect();
        if ((rect.width|0) < 10) return;
        if ((rect.height|0) < 10) rect = { width: rect.width, height: 220, left: rect.left||0, top: rect.top||0, right:(rect.left||0)+rect.width, bottom:(rect.top||0)+220, x:rect.left||0, y:rect.top||0 };
    }
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const w = rect.width, h = rect.height;
    const g = ctx.createLinearGradient(0,0,0,h);
    g.addColorStop(0, '#0b1320'); g.addColorStop(1, '#060a12');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);



    const annotate = (text) => {
        if (countEl) countEl.textContent = (buckets && buckets.length) ? buckets.length + ' buckets' : 'no data';
        if (bucketsEl) bucketsEl.textContent = (buckets && buckets.length) ? String(buckets.length) : '--';
        if (windowEl) windowEl.textContent = __newsTfSec >= 86400 ? (__newsTfHours + 'h window') : (__newsTfHours + 'h window / ' + (__newsTfSec/60) + 'm buckets');
        if (dotEl) { dotEl.className = 'w-2 h-2 rounded-full ' + ((buckets && buckets.length) ? 'bg-emerald-400 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 'bg-slate-500/40'); }
    };
    if (!buckets || buckets.length < 1) {
        annotate('');
        if (emptyEl) { emptyEl.classList.remove('hidden'); emptyEl.classList.add('flex'); }
        if (topEl) topEl.textContent = '';
        // keep grid so empty state doesn't look broken
        ctx.strokeStyle = 'rgba(148,163,184,0.12)';
        ctx.setLineDash([4,4]);
        ctx.beginPath(); ctx.moveTo(34, h/2); ctx.lineTo(w-8, h/2); ctx.stroke();
        ctx.setLineDash([]);
        return;
    }
    if (emptyEl) { emptyEl.classList.add('hidden'); emptyEl.classList.remove('flex'); }
    annotate(buckets.length + ' buckets');



    const padL = 34, padR = 8, padT = 12, padB = 18;

    const plotW = w - padL - padR, plotH = h - padT - padB;



    // zero-centered y-scale across bullish(+)/bearish(-)/neutral magnitudes

    let maxAbs = 0.05;

    buckets.forEach(b => {

        maxAbs = Math.max(maxAbs, Math.abs(b.bullish || 0), Math.abs(b.bearish || 0), Math.abs(b.neutral || 0));

    });

    const midY = padT + plotH / 2;

    const scale = (plotH / 2 - 6) / maxAbs; // px per unit



    // grid

    ctx.strokeStyle = '#1e293b';

    ctx.lineWidth = 1;

    ctx.beginPath();

    ctx.moveTo(padL, midY); ctx.lineTo(w - padR, midY); // zero line

    ctx.stroke();

    ctx.fillStyle = '#475569';

    ctx.font = '9px monospace';

    ctx.textAlign = 'right';

    ctx.fillText('0', padL - 4, midY + 3);

    ctx.fillText('+' + maxAbs.toFixed(2), padL - 4, padT + 6);

    ctx.fillText('-' + maxAbs.toFixed(2), padL - 4, h - padB + 6);



    const n = buckets.length;

    const step = plotW / Math.max(n - 1, 1);



    // helper: draw a polyline + area

    const line = (key, color, signed) => {

        ctx.beginPath();

        buckets.forEach((b, i) => {

            const x = padL + i * step;

            const y = midY - (signed ? (b[key] || 0) * scale : (b[key] || 0) * scale);

            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);

        });

        ctx.strokeStyle = color;

        ctx.lineWidth = 1.6;

        ctx.stroke();

    };

    const fill = (key, color, signed) => {

        ctx.beginPath();

        buckets.forEach((b, i) => {

            const x = padL + i * step;

            const y = midY - (b[key] || 0) * scale;

            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);

        });

        ctx.lineTo(padL + (n - 1) * step, midY);

        ctx.lineTo(padL, midY);

        ctx.closePath();

        ctx.fillStyle = color;

        ctx.globalAlpha = 0.18;

        ctx.fill();

        ctx.globalAlpha = 1.0;

    };

    // neutral area (light), then bearish (below zero, red), bullish (above zero, green)

    fill('neutral', '#94a3b8');

    fill('bearish', '#ef4444');

    fill('bullish', '#22c55e');

    line('neutral', '#94a3b8');

    line('bearish', '#ef4444');

    line('bullish', '#22c55e');



    // bucket markers + time labels

    ctx.textAlign = 'center';

    ctx.font = '9px monospace';

    buckets.forEach((b, i) => {

        const x = padL + i * step;

        // point marker

        ctx.beginPath();

        ctx.arc(x, midY - (b.bullish || 0) * scale, 2, 0, Math.PI * 2);

        ctx.fillStyle = '#22c55e'; ctx.fill();

        ctx.beginPath();

        ctx.arc(x, midY - (b.bearish || 0) * scale, 2, 0, Math.PI * 2);

        ctx.fillStyle = '#ef4444'; ctx.fill();

        // x labels: every ~4th bucket

        if (n <= 24 || i % Math.ceil(n / 12) === 0) {

            const t = new Date(b.bucket_start);

            ctx.fillStyle = '#475569';

            const lbl = __newsTfSec >= 86400 ? t.toLocaleDateString(undefined, {month:'short', day:'numeric'})

                : t.toLocaleTimeString(undefined, {hour:'2-digit', minute:'2-digit'});

            ctx.fillText(lbl, x, h - 4);

        }

    });



    // footer annotation + tooltip hit areas
    let top = buckets.reduce((a, b) => (Math.abs(b.bullish) + Math.abs(b.bearish) > Math.abs(a.bullish) + Math.abs(a.bearish) ? b : a), buckets[0]);
    if (topEl) {
        const tt = top.top_title ? ('Top: ' + top.top_title.slice(0, 88)) : (top.article_count + ' events');
        const mag = (Math.abs(top.bullish)+Math.abs(top.bearish)+Math.abs(top.neutral||0)).toFixed(3);
        topEl.textContent = tt + ' · impact ' + mag;
        topEl.title = top.top_title || '';
    }
    // hover tooltip
    if (wrap && tipEl) {
        let raf=0;
        const showTip = (b, x, y) => {
            const dt = new Date(b.bucket_start);
            const when = __newsTfSec >= 86400 ? dt.toLocaleDateString() : dt.toLocaleString();
            tipEl.innerHTML = '<div class="font-bold text-white">'+when+'</div><div><span class="text-emerald-300">▲ '+Number(b.bullish||0).toFixed(3)+'</span> &middot; <span class="text-red-300">▼ '+Number(b.bearish||0).toFixed(3)+'</span> &middot; <span class="text-slate-300">● '+Number(b.neutral||0).toFixed(3)+'</span></div><div class="text-slate-400">'+Number(b.article_count||0)+' articles</div>' + (b.top_title?'<div class="text-slate-200 mt-1 line-clamp-2">'+ (b.top_title||'').replace(/</g,'&lt;') +'</div>':'');
            tipEl.style.left = Math.min(w-270, Math.max(8, x+10)) + 'px';
            tipEl.style.top = Math.max(8, y-10) + 'px';
            tipEl.classList.remove('hidden');
        };
        const hideTip = () => tipEl.classList.add('hidden');
        const hit = (evt) => {
            const r = canvas.getBoundingClientRect();
            const mx = evt.clientX - r.left, my = evt.clientY - r.top;
            let best=null, bestDx=1e9;
            buckets.forEach((b,i)=>{
                const x = 34 + i * ( (w-34-8) / Math.max(buckets.length-1,1) );
                const dx = Math.abs(mx - x);
                if (dx < bestDx && dx < 22) { bestDx=dx; best=b; }
            });
            if (!best) return hideTip();
            showTip(best, mx, my);
        };
        canvas.onmousemove = (e)=>{ cancelAnimationFrame(raf); raf=requestAnimationFrame(()=>hit(e)); };
        canvas.onmouseleave = hideTip;
    }
}


async function loadNewsFeed() {

    try {

        const filter = (window.NewsIntel && window.NewsIntel.state) ? window.NewsIntel.state.filter : 'ACTIVE';
        const res = await NX.api.get('/api/news?limit=50&status=' + encodeURIComponent(filter), { component: 'News', action: 'FEED' });

        if (!res.ok) {
            let _nmsg='news feed failed'; try{ if(window.NX&&NX.Forensic&&NX.Forensic.normalizeError){ const _nn=NX.Forensic.normalizeError(res,{component:'News',action:'FEED',endpoint:'/api/news'}); _nmsg='news feed failed: '+_nn.message; } else _nmsg='news feed failed: HTTP '+res.status; }catch(_x){ _nmsg='news feed failed: HTTP '+res.status; }
            setNewsStatus(_nmsg, true);
            return;
        }

        const body = res.body;

        const feed = document.getElementById('news-feed');

        if (!body.available || !body.articles || body.articles.length === 0) {

            feed.innerHTML = '<div class="text-textMuted italic">No news events yet. Click "Fetch News" or wait for the worker.</div>';

            if (window.NewsIntel && NewsIntel.renderStatusCounts) NewsIntel.renderStatusCounts(body.status_counts);
            return;

        }

        feed.innerHTML = body.articles.map(a => {

            const hasAna = !!(a.analysis && (a.analysis.direction || a.analysis.relevance_to_xauusd));

            const dir = hasAna ? (a.analysis.direction || 'NEUTRAL') : 'PENDING';

            const dirColor = dir === 'BULLISH' ? 'text-accentGreen' : dir === 'BEARISH' ? 'text-accentRed' : dir === 'PENDING' ? 'text-slate-500' : 'text-slate-400';

            const imp = hasAna ? Math.round((a.importance_score || 0) * 100) : null;

            const rel = hasAna ? Math.round((a.analysis.relevance_to_xauusd || 0) * 100) + '%' : '--';

            const mech = hasAna && a.analysis.market_mechanism ? a.analysis.market_mechanism : '';

            const impColor = imp == null ? 'bg-slate-600/20 text-slate-400' : imp >= 70 ? 'bg-rose-500/20 text-rose-300' : imp >= 50 ? 'bg-orange-500/20 text-orange-300' : imp >= 30 ? 'bg-amber-500/20 text-amber-300' : 'bg-slate-500/20 text-slate-400';

            const extras = (window.NewsIntel && NewsIntel.articleExtrasHTML) ? NewsIntel.articleExtrasHTML(a) : '';

            return '<div class="bg-darkBg/40 border border-borderClr/40 rounded p-2.5" data-article-id="' + esc(a.article_id) + '">' +

                '<div class="flex justify-between items-center gap-2">' +

                '<span class="font-bold text-white truncate" title="' + esc(a.title) + '">' + esc(a.title) + '</span>' +

                '<span class="text-[9px] ' + dirColor + ' font-black shrink-0 border ' + (dir === 'BULLISH' ? 'border-accentGreen/30' : dir === 'BEARISH' ? 'border-accentRed/30' : 'border-slate-500/30') + ' rounded px-1.5 py-0.5">' + esc(dir) + '</span></div>' +

                '<div class="flex items-center gap-3 mt-1 text-[10px] text-textMuted">' +

                '<span>' + esc(a.source_name || a.source_id || '') + '</span>' +

                '<span class="' + impColor + ' rounded px-1 py-0.5">imp ' + imp + '</span>' +

                '<span class="text-amber-400/80">XAU ' + rel + '</span>' +

                '<span>' + esc(String(a.published_at || '').slice(0, 16)) + '</span></div>' +

                (mech ? '<div class="text-[9px] text-slate-500 mt-0.5 truncate">' + esc(mech) + '</div>' : '') +

                '<div class="flex flex-wrap items-center gap-2 mt-1.5">' + extras + '</div>' +

                '</div>';

        }).join('');

        if (window.NewsIntel && NewsIntel.renderStatusCounts) NewsIntel.renderStatusCounts(body.status_counts);

        setNewsStatus('feed: ' + body.articles.length + ' articles');

        loadNewsTimeline();

    } catch (e) {

        let _nf2='news feed failed'; try{ if(window.NX&&NX.Forensic&&NX.Forensic.normalizeError){ const _nn=NX.Forensic.normalizeError(e,{component:'News',action:'FEED',endpoint:'/api/news'}); _nf2='news feed failed: '+_nn.message; } else if(e&&e.message) _nf2='news feed failed: '+e.message; }catch(_x){ if(e&&e.message) _nf2='news feed failed: '+e.message; }
        setNewsStatus(_nf2, true);

    }

}



async function loadNewsKeywords() {

    try {

        const q = (document.getElementById('news-kw-search')?.value || '').trim();

        const cat = document.getElementById('news-kw-category')?.value || '';

        const res = await fetch('/api/news/keywords?top_n=25&category=' + encodeURIComponent(cat) + '&q=' + encodeURIComponent(q));

        if (!res.ok) throw new Error('HTTP ' + res.status);

        const body = await res.json();

        if (!body.available) {

            const tbl = document.getElementById('news-kw-table');

            if (tbl) tbl.innerHTML = '<div class="text-textMuted italic p-2">Keyword dataset unavailable.</div>';

            return;

        }

        // dataset meta

        const set = body.dataset || {};

        const cov = body.coverage || {};

        document.getElementById('news-kw-total').textContent = set.total_keywords ?? '--';

        document.getElementById('news-kw-articles').textContent = cov.articles_scanned ?? '--';

        document.getElementById('news-kw-mentions').textContent = cov.total_mentions ?? '--';

        document.getElementById('news-kw-active').textContent = cov.active_keywords ?? '--';

        const dir = cov.direction_distribution || {};

        document.getElementById('news-kw-dir').innerHTML =

            '<span class="text-accentGreen">' + (dir.BULLISH ?? 0) + '</span> / ' +

            '<span class="text-accentRed">' + (dir.BEARISH ?? 0) + '</span> / ' +

            '<span class="text-slate-400">' + (dir.NEUTRAL ?? 0) + '</span>';

        // populate category select once

        const sel = document.getElementById('news-kw-category');

        if (sel && sel.options.length <= 1 && set.categories) {

            Object.keys(set.categories).sort().forEach(c => {

                const opt = document.createElement('option');

                opt.value = c;

                opt.textContent = c + ' (' + set.categories[c] + ')';

                sel.appendChild(opt);

            });

        }

        // top keywords table

        const tbl = document.getElementById('news-kw-table');

        const tops = cov.top_keywords || [];

        const kws = body.keywords || [];

        if (top_none(tops)) {

            tbl.innerHTML = '<div class="text-textMuted italic p-2">No keyword hits in the scanned corpus yet. Fetch news to populate.</div>';

            return;

        }

        tbl.innerHTML = '<table class="w-full text-left">' +

            '<thead><tr class="text-textMuted uppercase text-[9px] border-b border-borderClr/40">' +

            '<th class="p-1.5">Keyword</th><th class="p-1.5">Category</th><th class="p-1.5">Bias</th>' +

            '<th class="p-1.5 text-right">Hits</th><th class="p-1.5 text-right">Mentions</th><th class="p-1.5 text-right">Share</th></tr></thead><tbody>' +

            tops.map(k => kwRow(k)).join('') +

            (kws.length > tops.length ? '<tr><td colspan="6" class="p-1.5 text-[9px] text-textMuted italic">' + (kws.length - tops.length) + ' more dataset keywords (filter to browse)…</td></tr>' : '') +

            '</tbody></table>';

    } catch (e) {

        console.error('[UI_ERROR] component=News endpoint=/api/news/keywords action=LOAD message=' + (e && e.message));

        const tbl = document.getElementById('news-kw-table');

        if (tbl) tbl.innerHTML = '<div class="text-accentRed italic p-2">keyword load failed: ' + esc(e && e.message ? String(e.message) : 'unknown') + '</div>';

        setNewsStatus('keyword dataset failed: ' + (e && e.message), true);

    }

}



function top_none(tops) {

    return !tops || tops.length === 0;

}



function kwRow(k) {

    const biasColor = k.direction_bias === 'BULLISH' ? 'text-accentGreen' : k.direction_bias === 'BEARISH' ? 'text-accentRed' : 'text-slate-400';

    return '<tr class="border-b border-borderClr/20 hover:bg-darkBg/30">' +

        '<td class="p-1.5 font-bold text-white">' + esc(k.keyword) + '</td>' +

        '<td class="p-1.5 text-textMuted">' + esc(k.category) + '</td>' +

        '<td class="p-1.5 ' + biasColor + '">' + esc(k.direction_bias || 'NEUTRAL') + '</td>' +

        '<td class="p-1.5 text-right text-accentCyan">' + k.article_hits + '</td>' +

        '<td class="p-1.5 text-right">' + k.mention_count + '</td>' +

        '<td class="p-1.5 text-right">' + Math.round((k.share || 0) * 100) + '%</td></tr>';

}



async function analyzeNewsWithAI(articleId) {
    // Delegated to the News Intelligence module (state machine + safe error
    // handling + toast). Kept as a thin wrapper for backward compatibility.
    if (window.NewsIntel && NewsIntel.analyzeArticle) {
        NewsIntel.analyzeArticle(articleId);
    } else {
        console.warn('news analyze unavailable');
    }
}



async function triggerNewsRefresh() {

    try {

        const res = await NX.api.post('/api/news/refresh', {}, { component: 'News', action: 'REFRESH' });

        const body = res.ok ? res.body : { available: false, error: res.error };

        if (!body || !body.available) {

            console.error('[UI_ERROR] component=News endpoint=/api/news/refresh action=REFRESH available=false');

            setNewsStatus('news refresh failed', true);

            alert('News engine unavailable');

        } else if (body.cooldown) {

            // bandwidth guard: server rejected the re-fetch, show remaining wait

            setNewsStatus('refresh cooldown: ' + body.cooldown + 's (bandwidth guard)');

        } else {

            setNewsStatus('fetch complete: ' + (body.ingested ? body.ingested.new : 0) + ' new items');

        }

        loadNewsState();

    } catch (e) {

        console.warn('news refresh failed', e);

        setNewsStatus('news refresh failed: ' + e.message, true);

    }

}



// auto-load news state on tab open + keep it fresh (auto-reconnect semantics:

// the panel re-polls every 60s so a worker restart / engine start is picked up

// without manual refresh, and silent failures become visible via the status line)

let __newsRefreshTimer = null;

function startNewsAutoRefresh() {

    if (__newsRefreshTimer) return;

    __newsRefreshTimer = setInterval(() => {

        if (document.getElementById('tab-news') && !document.getElementById('tab-news').classList.contains('hidden')) {

            loadNewsState();

            if (window.NewsIntel && NewsIntel.loadAIStatus) NewsIntel.loadAIStatus();

        }

    }, 60000);

}

const __newsTabObserver = new MutationObserver(() => {

    const tab = document.getElementById('tab-news');

    if (tab && !tab.classList.contains('hidden') && !window.__newsIntelLoaded) {

        window.__newsIntelLoaded = true;

        bindNewsKeywordSearch();

        loadNewsState();

        // News Intelligence (0100): initialize AI status + filter + pro controls.
        if (window.NewsIntel) {
            if (NewsIntel.setNewsFilter) NewsIntel.setNewsFilter('ACTIVE');
            if (NewsIntel.loadAIStatus) NewsIntel.loadAIStatus();
        }

        startNewsAutoRefresh();

    }

});

__newsTabObserver.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });

// (stale Phase-16 duplicate renderer removed in UX v2)

// Renders human audit lines with real values only; missing stats produce a

// neutral hint, never a fabricated claim.



// =============================================================================

// 70D SHADOW MODEL PANEL (TASK-05-70D-SHADOW) — real data only

// =============================================================================

async function loadShadow70Panel() {

    const results = await Promise.allSettled([

        NX.api.get('/api/models/shadow70/summary', { component: 'Shadow70', action: 'LOAD_SUMMARY' }),

        NX.api.get('/api/models/shadow70/health', { component: 'Shadow70', action: 'LOAD_HEALTH' }),

        NX.api.get('/api/models/shadow70/disagreements', { component: 'Shadow70', action: 'LOAD_DISAGREEMENTS' })

    ]);

    let summary = null, health = null, disagreements = [];

    if (results[0].status === 'fulfilled' && results[0].value.ok) summary = results[0].value.body;

    if (results[1].status === 'fulfilled' && results[1].value.ok) health = results[1].value.body;

    if (results[2].status === 'fulfilled' && results[2].value.ok) {

        const b = results[2].value.body;

        disagreements = Array.isArray(b.rows) ? b.rows.slice(0, 30) : [];

    }

    renderShadow70(summary, health, disagreements);

}



function renderShadow70(summary, health, disagreements) {

    const st = document.getElementById('s70-status');

    if (st) {

        const rt = (summary && summary.runtime) || {};

        const state = rt.status || 'IDLE';

        st.textContent = state;

        st.className = 'text-[10px] font-black px-2 py-1 rounded uppercase border ' + (

            state === 'READY' ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'

            : state === 'DEGRADED' || state === 'WARNING' ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'

            : state === 'FAILED' || state === 'BLOCKED' ? 'bg-rose-500/20 text-rose-300 border-rose-500/30'

            : 'bg-slate-500/20 text-slate-300 border-slate-500/30');

    }

    const modelBody = document.getElementById('s70-model-body');

    if (modelBody) {

        const rt = (summary && summary.runtime) || {};

        if (rt.model_id) {

            modelBody.innerHTML =

                '<div><span class=\'text-textMuted\'>ID      </span>' + escHtml(rt.model_id) + ' @ ' + escHtml(rt.model_version || '?') + '</div>' +

                '<div><span class=\'text-textMuted\'>Schema  </span>' + escHtml(rt.schema || '?') + ' / ' + (rt.dimension || 0) + 'D</div>' +

                '<div><span class=\'text-textMuted\'>Hash    </span>' + escHtml((rt.artifact_hash || '?').slice(0, 16)) + '</div>' +

                '<div><span class=\'text-textMuted\'>Scaler  </span>' + escHtml((rt.scaler_hash || '?').slice(0, 16)) + '</div>';

        } else {

            modelBody.innerHTML = '<div class=\'text-textMuted italic\'>No validated 70D candidate attached</div>';

        }

    }

    const rtBody = document.getElementById('s70-runtime-body');

    if (rtBody) {

        const rt = (summary && summary.runtime) || {};

        const wk = (summary && summary.worker) || {};

        rtBody.innerHTML =

            '<div>inferences   ' + (rt.observations != null ? rt.observations : '--') + '</div>' +

            '<div>errors       ' + (rt.errors != null ? rt.errors : '--') + '</div>' +

            '<div>dropped      ' + (rt.dropped != null ? rt.dropped : '--') + '</div>' +

            '<div>timeouts     ' + (rt.timeouts != null ? rt.timeouts : '--') + '</div>' +

            '<div>avg latency  ' + (rt.avg_latency_ms != null ? rt.avg_latency_ms.toFixed(3) + ' ms' : '--') + '</div>' +

            '<div>p95 latency  ' + (rt.p95_latency_ms != null ? rt.p95_latency_ms.toFixed(3) + ' ms' : '--') + '</div>' +

            '<div>queue        ' + (wk.queue_size != null ? wk.queue_size + '/' + (wk.max_queue || '?') : '--') + '</div>';

    }

    const diffBody = document.getElementById('s70-diff-body');

    if (diffBody) {

        const rt = (summary && summary.runtime) || {};

        const store = (summary && summary.store) || {};

        const agg = (store.disagreement_counts) || {};

        const total = (store.observations || 0);

        const agree = (store.agreements || 0);

        const agreePct = total > 0 ? (100 * agree / total).toFixed(1) : '--';

        diffBody.innerHTML =

            '<div>observations ' + total + '</div>' +

            '<div>agreement    ' + agreePct + '%</div>' +

            '<div>avg conf Δ   ' + (rt.avg_conf_delta != null ? rt.avg_conf_delta.toFixed(4) : '--') + '</div>' +

            '<div class=\'text-textMuted\'><b>BY CLASS</b></div>' +

            Object.keys(agg).sort().map(function (k) {

                return '<div>' + escHtml(k) + ' = ' + agg[k] + '</div>';

            }).join('');

    }

    const healthBody = document.getElementById('s70-health-body');

    if (healthBody) {

        const fh = (health && health.feature_health) || [];

        if (fh.length) {

            healthBody.innerHTML = fh.map(function (h) {

                const drift = (health.drift && health.drift.severity) || 'NORMAL';

                return '<div class=\'bg-darkBg border border-borderClr/60 rounded p-2\'>' +

                    '<div class=\'text-textMuted\'>' + escHtml(h.name) + '</div>' +

                    '<div>mean ' + h.mean.toFixed(3) + ' std ' + h.std.toFixed(3) + '</div>' +

                    '<div>miss ' + (100 * h.missing_rate).toFixed(1) + '% zero ' + (100 * h.zero_rate).toFixed(1) + '%</div>' +

                    '<div>drift ' + escHtml(drift) + ' · samples ' + h.samples + '</div></div>';

            }).join('');

        } else {

            healthBody.innerHTML = '<div class=\'text-textMuted italic\'>No live feature health yet</div>';

        }

    }

    const disBody = document.getElementById('s70-disagreements-body');

    if (disBody) {

        if (disagreements.length) {

            disBody.innerHTML = disagreements.map(function (r) {

                return '<div class=\'flex justify-between gap-2\'><span>' + escHtml((r.timestamp || '').slice(0, 19)) + '</span>' +

                    '<span>' + escHtml(r.champion_action || '') + ' → ' + escHtml(r.shadow_action || '') + '</span>' +

                    '<span class=\'text-accentGold\'>' + escHtml(r.disagreement || '') + '</span>' +

                    '<span>outcome ' + escHtml(r.outcome || 'PENDING') + '</span></div>';

            }).join('');

        } else {

            disBody.innerHTML = '<div class=\'text-textMuted italic\'>No disagreements recorded</div>';

        }

    }

}



// =====================================================================

// =====================================================================
// FORENSIC INCIDENT CENTER (TASK-12 / FORENSIC INCIDENT CENTER overhaul)
// ---------------------------------------------------------------------
// Production-grade operational console:
//  - SINGLE authoritative incident array (no duplicated counters)
//  - KPIs DERIVED from that array (fixes OPEN/CRITICAL/HIGH/MEDIUM
//    impossible state)
//  - NX.api everywhere => no raw "TypeError: Failed to fetch" in DOM
//  - Loading / Empty / Error / Loaded states are DISTINCT
//  - Agent Mode: real state machine, automatic eligible trace, dedup
//  - Generate Task drawer with review-before-submit
//  - Stop Bot confirmation (type STOP), truthful semantics
// All HTTP goes through NX.api (safe envelope). See forensic_console.js.
// =====================================================================

// Single source of truth for the Forensic Incident Center.
var INC_STATE = {
  incidents: [],     // authoritative full list (data.incidents)
  filter: 'open',    // open | resolved | agent
  loading: false,
  loaded: false,
  error: null,
  requestSeq: 0,     // concurrency guard: latest response wins
  agentMode: false,
  agentProcessed: {}, // incident_id -> agent state (dedup map)
};

var INC_AGENT_OPEN = ['OPEN', 'INVESTIGATING', 'ROOT_CAUSE_IDENTIFIED', 'CONTAINED', 'RECOVERY_READY'];

// Severity -> display class (reuse existing palette).
function incSevClass(sev) {
  switch (NX.Forensic.model.normSeverity(sev)) {
    case 'CRITICAL': return 'text-rose-400 border-rose-500/40 bg-rose-500/10';
    case 'HIGH': return 'text-orange-400 border-orange-500/40 bg-orange-500/10';
    case 'MEDIUM': return 'text-yellow-400 border-yellow-500/40 bg-yellow-500/10';
    case 'LOW': return 'text-sky-400 border-sky-500/40 bg-sky-500/10';
    case 'INFO': return 'text-slate-300 border-slate-500/40 bg-slate-500/10';
    default: return 'text-gray-300 border-borderClr bg-slate-500/10';
  }
}

// Render the derived KPI summary (single source of truth).
function renderIncidentKpis() {
  var k = NX.Forensic.model.deriveKpis(INC_STATE.incidents);
  var set = function (id, v) { var el = document.getElementById(id); if (el) el.textContent = (v == null ? '--' : v); };
  set('inc-summary-open', k.open);
  set('inc-summary-critical', k.critical);
  set('inc-summary-high', k.high);
  set('inc-summary-medium', k.medium);
  // Nav badge reflects the OPEN count only.
  var badge = document.getElementById('incident-nav-badge');
  if (badge) {
    badge.textContent = k.open + ' open';
    badge.classList.remove('hidden');
  }
}

// Worker health hierarchy (spec 33): separate Status / Cycles / Last OK.
function renderWorkerHealth(w) {
  w = w || {};
  var state = w.display_state || (w.state ? (w.state === 'RUNNING' || w.state === 'STARTING' ? 'RUNNING'
    : w.state === 'DEGRADED' ? 'DEGRADED' : w.state === 'FAILED' ? 'FAILED' : 'DISABLED') : 'DISABLED');
  var stateEl = document.getElementById('inc-worker-state');
  if (stateEl) {
    var cls = { RUNNING: 'status-success', DEGRADED: 'status-degraded', FAILED: 'status-error', DISABLED: 'status-inactive' }[state] || 'status-inactive';
    stateEl.className = 'nx-status-pill ' + cls;
    stateEl.innerHTML = '<span class="nx-status-dot"></span>' + esc(state);
  }
  var detail = document.getElementById('inc-worker-detail');
  if (detail) {
    var parts = [];
    if (w.cycle_count != null) parts.push('Cycles: ' + w.cycle_count);
    if (w.last_success) parts.push('Last OK: ' + String(w.last_success).slice(5, 19).replace('T', ' '));
    else parts.push('No success yet');
    if (w.last_failure) parts.push('Last fail: ' + String(w.last_failure).slice(5, 19).replace('T', ' '));
    if (w.queue_size) parts.push('Queue: ' + w.queue_size);
    if (w.incidents_created) parts.push('Created: ' + w.incidents_created);
    if (w.incidents_deduplicated) parts.push('Dedup: ' + w.incidents_deduplicated);
    detail.textContent = parts.length ? parts.join('  ·  ') : 'no worker attached';
  }
}
// Load + render incidents. Single authoritative array; KPIs derived.
// Uses NX.api so errors never leak raw stack text into the DOM.
async function loadIncidents() {
  var listEl = document.getElementById('incident-list');
  if (!listEl) return;

  // Concurrency guard: only the latest response wins.
  var seq = ++INC_STATE.requestSeq;
  INC_STATE.loading = true;
  if (!INC_STATE.loaded) {
    renderIncidentLoading(listEl);
  }

  // Skeleton while we wait (no layout jump).
  renderIncidentLoading(listEl);

  try {
    // Health gives counts + worker state (used for worker health + nav badge).
    var hres = await NX.api.get('/api/diagnostics/health', { component: 'Incidents', action: 'HEALTH' });
    if (seq !== INC_STATE.requestSeq) return; // superseded
    if (!hres || !hres.ok) {
      var hn = NX.Forensic.normalizeError(hres, { component: 'Incidents', action: 'HEALTH', endpoint: '/api/diagnostics/health' });
      NX.Forensic.toast.error(hn.message, { detail: hn.detail });
      renderIncidentError(listEl); INC_STATE.loading = false; return;
    }
    var hdata = hres.body || {};
    renderWorkerHealth(hdata.worker || {});

    // AUTHORITATIVE list: all incidents (not pre-filtered).
    var ires = await NX.api.get('/api/diagnostics/incidents?limit=200', { component: 'Incidents', action: 'LIST' });
    if (seq !== INC_STATE.requestSeq) return;
    if (!ires || !ires.ok) {
      var inerr = NX.Forensic.normalizeError(ires, { component: 'Incidents', action: 'LIST', endpoint: '/api/diagnostics/incidents' });
      NX.Forensic.toast.error(inerr.message, { detail: inerr.detail });
      renderIncidentError(listEl); INC_STATE.loading = false; return;
    }
    var body = ires.body || {};
    if (body.available === false) {
      NX.Forensic.toast.error('Incident service is unavailable.');
      renderIncidentError(listEl, 'The incident service could not be reached.'); INC_STATE.loading = false; return;
    }
    var incidents = Array.isArray(body.incidents) ? body.incidents : [];
    // Normalize + attach any agent state we already track.
    incidents.forEach(function (inc) {
      if (INC_STATE.agentProcessed[inc.incident_id]) {
        inc._agentState = INC_STATE.agentProcessed[inc.incident_id];
      }
    });
    INC_STATE.incidents = incidents;
    INC_STATE.loaded = true;
    INC_STATE.error = null;
    INC_STATE.loading = false;
    renderIncidentKpis();
    renderIncidentList(listEl);

    // Agent Mode: trigger automatic trace for eligible incidents (deduped).
    if (INC_STATE.agentMode) maybeAutoTraceEligible();
  } catch (e) {
    var n = NX.Forensic.normalizeError(e, { component: 'Incidents', action: 'LOAD', endpoint: '/api/diagnostics/incidents' });
    NX.Forensic.toast.error(n.message, { detail: n.detail });
    renderIncidentError(listEl);
    INC_STATE.loading = false;
  }
}

function renderIncidentLoading(listEl) {
  if (!listEl) return;
  var html = '';
  for (var i = 0; i < 3; i++) html += '<div class="nx-skeleton"></div>';
  listEl.innerHTML = html;
}

function renderIncidentError(listEl, msg) {
  if (!listEl) return;
  listEl.innerHTML = '<div class="rounded-lg border border-rose-500/30 bg-rose-500/10 p-4 text-rose-200">' +
    '<p class="text-sm font-semibold">Unable to load incidents.</p>' +
    '<p class="text-[11px] text-rose-300/80 mt-1">' + esc(msg || 'The incident service could not be reached.') + '</p>' +
    '<button onclick="loadIncidents()" class="mt-3 nx-btn-ghost px-3 py-1.5 rounded text-[11px]"><i class="fa-solid fa-rotate mr-1"></i>Retry</button>' +
    '</div>';
}

// Filtered view of the authoritative array (single dataset, no copies).
function getFilteredIncidents() {
  var arr = INC_STATE.incidents;
  if (INC_STATE.filter === 'open') return arr.filter(NX.Forensic.model.isOpen);
  if (INC_STATE.filter === 'resolved') return arr.filter(NX.Forensic.model.isResolved);
  if (INC_STATE.filter === 'agent') return arr.filter(function (i) {
    return NX.Forensic.model.isResolved(i) && (i.resolved_by === 'AGENT' || NX.Forensic.model.normStatus(i.status) === 'RESOLVED_BY_AGENT');
  });
  return arr;
}

function setIncidentFilter(filter, btn) {
  INC_STATE.filter = filter;
  document.querySelectorAll('#incident-filter-tabs .nx-filter-tab').forEach(function (b) {
    var on = b.getAttribute('data-filter') === filter;
    b.classList.toggle('nx-filter-tab-active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  var listEl = document.getElementById('incident-list');
  if (listEl) renderIncidentList(listEl);
}

// Render the list for the current filter (distinct empty/loaded states).
function renderIncidentList(listEl) {
  if (!listEl) return;
  if (!INC_STATE.loaded) return; // loading/error handled elsewhere
  var items = getFilteredIncidents();
  if (!items.length) {
    var label = INC_STATE.filter === 'open' ? 'open' : (INC_STATE.filter === 'agent' ? 'resolved by Agent' : 'resolved');
    listEl.innerHTML = '<div class="rounded-lg border border-borderClr bg-darkBg/40 p-6 text-center">' +
      '<i class="fa-solid fa-circle-check text-emerald-400 text-xl mb-2"></i>' +
      '<p class="text-sm text-white font-semibold">No ' + label + ' incidents</p>' +
      '<p class="text-[11px] text-textMuted mt-1">The forensic worker has not reported any ' + label + ' incidents.</p>' +
      '</div>';
    return;
  }
  listEl.innerHTML = '';
  items.forEach(function (inc) { listEl.appendChild(buildIncidentCard(inc)); });
}

// Compact, hierarchy-clear incident card with row-level actions.
function buildIncidentCard(inc) {
  var card = document.createElement('div');
  var sev = NX.Forensic.model.normSeverity(inc.severity);
  var sevCls = incSevClass(sev);
  card.className = 'border rounded-lg p-3 bg-darkBg/50 hover:border-accentCyan/40 transition';
  card.style.borderColor = 'rgba(148,163,184,.18)';

  var agentState = inc._agentState || (inc.resolved_by === 'AGENT' ? 'RESOLVED' : null);
  var agentPill = agentState ? agentBadgeHtml(agentState) : '';

  var head = document.createElement('div');
  head.className = 'flex items-center justify-between gap-2';
  head.innerHTML =
    '<div class="flex items-center gap-2 min-w-0">' +
      '<span class="font-mono text-xs font-bold text-white truncate">' + esc(inc.incident_id) + '</span>' +
      '<span class="text-[10px] px-1.5 py-0.5 rounded border ' + sevCls + '">' + esc(sev) + '</span>' +
      '<span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-500/10 border border-slate-500/30 text-slate-300">' + esc(inc.category || '—') + '</span>' +
      agentPill +
    '</div>' +
    '<span class="text-[10px] text-textMuted whitespace-nowrap">' + esc((inc.detected_at || inc.first_seen_at || '').toString().slice(0, 19).replace('T', ' ')) + '</span>';

  var body = document.createElement('p');
  body.className = 'text-xs text-gray-300 mt-2';
  body.innerHTML = '<b>' + esc(inc.operation || '') + '</b> @ <code class="text-accentCyan">' + esc(inc.component || '') + '</code>';

  var meta = document.createElement('p');
  meta.className = 'text-[11px] text-textMuted mt-1';
  var impact = inc.impact ? (inc.impact.affected_trades + ' trades / ' + inc.impact.affected_records + ' records') : 'n/a';
  meta.innerHTML = 'root cause: <b>' + esc(inc.root_cause_status || 'UNKNOWN') + '</b> · impact: ' + esc(impact) +
    (inc.repeated_count > 1 ? ' · repeats: ' + inc.repeated_count : '');

  var actions = document.createElement('div');
  actions.className = 'flex items-center gap-2 mt-3 pt-2 border-t border-borderClr/40';
  var traceBtn = document.createElement('button');
  traceBtn.className = 'nx-btn-ghost px-2.5 py-1 rounded text-[11px]';
  traceBtn.innerHTML = '<i class="fa-solid fa-route mr-1"></i>Trace';
  traceBtn.onclick = function (e) { e.stopPropagation(); var inp = document.getElementById('incident-search-input'); if (inp) inp.value = inc.incident_id; searchIncidents(); };

  var taskBtn = document.createElement('button');
  taskBtn.className = 'nx-btn-ghost px-2.5 py-1 rounded text-[11px] text-accentCyan';
  taskBtn.innerHTML = '<i class="fa-solid fa-clipboard-list mr-1"></i>Generate Task';
  taskBtn.title = 'Generate a reviewable task from this incident';
  taskBtn.onclick = function (e) { e.stopPropagation(); openTaskDrawer(inc); };

  var detailBtn = document.createElement('button');
  detailBtn.className = 'nx-btn-ghost px-2.5 py-1 rounded text-[11px] ml-auto';
  detailBtn.innerHTML = 'Details';
  detailBtn.onclick = function (e) { e.stopPropagation(); showIncidentDetail(inc.incident_id); };

  actions.appendChild(traceBtn);
  actions.appendChild(taskBtn);
  actions.appendChild(detailBtn);

  card.appendChild(head);
  card.appendChild(body);
  card.appendChild(meta);
  card.appendChild(actions);
  card.onclick = function () { showIncidentDetail(inc.incident_id); };
  return card;
}

function agentBadgeHtml(state) {
  var b = NX.Forensic.agent.badge(state);
  var cls = 'nx-agent-pill';
  if (b.color === 'success') cls += ' is-success';
  else if (b.color === 'error') cls += ' is-error';
  else if (NX.Forensic.agent.isActive(state)) cls += ' is-processing';
  return '<span class="' + cls + '"><i class="fa-solid fa-robot"></i>' + esc(b.label) + '</span>';
}
// =====================================================================
// AGENT MODE (spec 15-22)
// Real state machine; never fakes backend behavior. Auto-trace only
// fires for ELIGIBLE incidents and is deduplicated by incident_id.
// =====================================================================

function isEligibleForAgent(inc) {
  if (!inc) return false;
  if (!NX.Forensic.model.isOpen(inc)) return false; // only open incidents
  var sev = NX.Forensic.model.normSeverity(inc.severity);
  // Respect severity: CRITICAL/HIGH/MEDIUM eligible; LOW/INFO are queued lightly.
  if (['CRITICAL', 'HIGH', 'MEDIUM'].indexOf(sev) === -1) return false;
  // Respect existing trace state + dedup.
  if (INC_STATE.agentProcessed[inc.incident_id]) return false;
  if (inc._agentState) return false;
  return true;
}

// Auto-trace eligible incidents when Agent Mode is ON. Deduplicated by id.
function maybeAutoTraceEligible() {
  var eligible = INC_STATE.incidents.filter(isEligibleForAgent);
  if (!eligible.length) return;
  // Respect a simple workload cap to avoid a storm on reconnect/refresh.
  var active = Object.keys(INC_STATE.agentProcessed).length;
  var budget = 5;
  eligible.slice(0, Math.max(0, budget - active)).forEach(function (inc) {
    agentTraceIncident(inc.incident_id);
  });
}

async function agentTraceIncident(incidentId) {
  if (INC_STATE.agentProcessed[incidentId]) return; // dedup guard
  INC_STATE.agentProcessed[incidentId] = 'TRACING';
  await refreshAgentPillFor(incidentId);
  try {
    // Use the SAME real trace endpoint the manual Trace uses.
    var res = await NX.api.get('/api/diagnostics/trace?query=' + encodeURIComponent(incidentId), { component: 'Agent', action: 'TRACE' });
    if (!res || !res.ok) {
      INC_STATE.agentProcessed[incidentId] = 'FAILED';
    } else {
      var trace = (res.body && res.body.trace) || {};
      if (trace.missing_link) {
        INC_STATE.agentProcessed[incidentId] = 'ANALYZING'; // partial lineage; keep investigating
      } else {
        // Lineage resolved => generate a proposal (stays as ANALYZING -> TASK_READY).
        INC_STATE.agentProcessed[incidentId] = 'TASK_READY';
      }
    }
  } catch (e) {
    INC_STATE.agentProcessed[incidentId] = 'FAILED';
  }
  await refreshAgentPillFor(incidentId);
}

// Re-render the card (and its pill) for a single incident without a full reload.
async function refreshAgentPillFor(incidentId) {
  var inc = INC_STATE.incidents.find(function (i) { return i.incident_id === incidentId; });
  if (!inc) return;
  inc._agentState = INC_STATE.agentProcessed[incidentId];
  var listEl = document.getElementById('incident-list');
  if (listEl) renderIncidentList(listEl);
}

function toggleAgentMode() {
  INC_STATE.agentMode = !INC_STATE.agentMode;
  var btn = document.getElementById('agent-mode-toggle');
  if (btn) {
    btn.setAttribute('aria-checked', INC_STATE.agentMode ? 'true' : 'false');
    btn.title = INC_STATE.agentMode ? 'Agent Mode: ON — automatic trace/anomaly investigation' : 'Agent Mode: OFF';
  }
  if (INC_STATE.agentMode) {
    NX.Forensic.toast.info('Agent Mode enabled. Eligible open incidents will be traced automatically.');
    if (INC_STATE.loaded) maybeAutoTraceEligible();
  } else {
    NX.Forensic.toast.info('Agent Mode disabled.');
  }
}

// =====================================================================
// TASK GENERATION (spec 23-31) — review-before-submit drawer
// No external provider is wired on the backend yet => truthful
// "pending / not configured" state. Never fabricates success.
// =====================================================================

var TASK_DRAWER_CTX = null;

function openTaskDrawer(inc) {
  TASK_DRAWER_CTX = inc || null;
  var drawer = document.getElementById('task-drawer');
  var backdrop = document.getElementById('task-drawer-backdrop');
  if (!drawer) return;

  // Reset states.
  showOnly('task-loading');
  var note = document.getElementById('task-submit-note');
  if (note) note.classList.add('hidden');
  setTaskSubmitEnabled(false);

  drawer.classList.remove('hidden');
  drawer.classList.add('nx-modal-open');
  if (backdrop) backdrop.classList.remove('hidden');
  drawer._lastFocused = document.activeElement;

  // Simulate analysis then populate from REAL incident evidence (no AI here).
  NX.Forensic.withButtonLock(null, null, function () {});
  setTimeout(function () { populateTaskFromIncident(inc); }, 450);
}

function closeTaskDrawer() {
  var drawer = document.getElementById('task-drawer');
  var backdrop = document.getElementById('task-drawer-backdrop');
  if (drawer) { drawer.classList.add('hidden'); drawer.classList.remove('nx-modal-open'); }
  if (backdrop) backdrop.classList.add('hidden');
  if (drawer && drawer._lastFocused && drawer._lastFocused.focus) {
    try { drawer._lastFocused.focus(); } catch (e) {}
  }
  drawer._lastFocused = null;
}

function showOnly(which) {
  ['task-loading', 'task-form', 'task-error'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) {
      if (id === which) { el.classList.remove('hidden'); if (id === 'task-loading') el.classList.add('flex'); }
      else { el.classList.add('hidden'); el.classList.remove('flex'); }
    }
  });
}

function setTaskSubmitEnabled(on) {
  var b = document.getElementById('task-submit-btn');
  if (!b) return;
  b.disabled = !on;
  b.classList.toggle('opacity-50', !on);
  b.classList.toggle('cursor-not-allowed', !on);
}

// Build the task proposal from the REAL incident record. No fabricated
// timestamps/root causes/confidence. Preserves forensic context.
function populateTaskFromIncident(inc) {
  if (!inc) { showOnly('task-error'); var em = document.getElementById('task-error-msg'); if (em) em.textContent = 'No incident selected.'; return; }
  var sev = NX.Forensic.model.normSeverity(inc.severity);
  var comp = inc.component || 'unknown component';
  var title = 'Investigate ' + (inc.category || 'INCIDENT').toUpperCase() + ' in ' + comp;

  var desc = [];
  desc.push('Incident: ' + inc.incident_id);
  desc.push('Severity: ' + sev + '   Status: ' + (inc.status || 'UNKNOWN'));
  if (inc.first_seen_at) desc.push('First observed: ' + String(inc.first_seen_at).replace('T', ' '));
  if (inc.last_seen_at) desc.push('Last observed: ' + String(inc.last_seen_at).replace('T', ' '));
  if (inc.component) desc.push('Affected component: ' + inc.component);
  if (inc.correlation_id) desc.push('Correlation ID: ' + inc.correlation_id);
  if (inc.operation) desc.push('Operation: ' + inc.operation);
  if (inc.root_cause) desc.push('Root cause: ' + inc.root_cause);
  else if (inc.root_cause_status) desc.push('Root cause status: ' + inc.root_cause_status);
  if (inc.ticket) desc.push('Ticket: ' + inc.ticket);
  if (inc.execution_id) desc.push('Execution ID: ' + inc.execution_id);
  if (inc.request_id) desc.push('Request ID: ' + inc.request_id);
  if (inc.model_id) desc.push('Model ID: ' + inc.model_id);
  var impact = inc.impact ? (inc.impact.affected_trades + ' trades / ' + inc.impact.affected_records + ' records') : null;
  if (impact) desc.push('Observed impact: ' + impact);
  if (inc.symptom) desc.push('Symptom: ' + inc.symptom);
  if (inc.suspected_root_cause) desc.push('Suspected root cause: ' + inc.suspected_root_cause);
  desc.push('Recommended action: investigate the ' + comp + ' subsystem lineage and confirm resolution before closing.');

  setValue('task-title', title);
  setValue('task-severity', sev);
  setValue('task-description', desc.join('\n'));
  setValue('task-tags', [inc.category, sev, 'forensic'].filter(Boolean).join(','));

  showOnly('task-form');
  setTaskSubmitEnabled(true);
}

function setValue(id, v) { var el = document.getElementById(id); if (el) el.value = v == null ? '' : v; }

async function submitGeneratedTask(btn) {
  // Duplicate-submit guard.
  if (btn && btn._busy) return;
  var incident = TASK_DRAWER_CTX;
  var provider = (document.getElementById('task-provider') || {}).value || 'jira';
  var title = (document.getElementById('task-title') || {}).value || '';
  var description = (document.getElementById('task-description') || {}).value || '';
  var severity = (document.getElementById('task-severity') || {}).value || 'MEDIUM';
  var tags = (document.getElementById('task-tags') || {}).value || '';

  if (!title.trim() || !description.trim()) {
    NX.Forensic.toast.warning('Task title and description are required.');
    return;
  }

  var prevHtml = btn ? btn.innerHTML : '';
  NX.Forensic.withButtonLock(btn, 'Creating…', async function () {
    // No backend provider endpoint exists. Truthful handling per spec 48:
    // we do NOT fabricate a successful external ticket.
    var surface = NX.Forensic.taskProvider.surface();
    if (!surface.configured || !surface.submitEndpoint) {
      // Keep the operator's edits; report honest pending state.
      var note = document.getElementById('task-submit-note');
      if (note) note.classList.remove('hidden');
      NX.Forensic.toast.warning('No external task provider is configured. Submission is pending backend wiring (reviewed, not sent).');
      if (btn) btn.innerHTML = prevHtml;
      if (btn) { btn.disabled = false; btn.classList.remove('opacity-50', 'cursor-not-allowed'); }
      return;
    }
    // (Reserved) real submission path would POST to surface.submitEndpoint here.
  });
}
// =====================================================================
// STOP BOT — production safety (spec 7)
// Modal requires typing STOP (case-sensitive). Destructive call only
// fires after confirmation. Truthful: toggling OFF halts the engine
// loop; it does NOT cancel broker-placed pending orders (verify: the
// backend sets engine._running=False only). Modal never claims success
// until the backend confirms.
// =====================================================================

function openStopBotModal() {
  var modal = document.getElementById('stop-bot-modal');
  if (!modal) return;
  var input = document.getElementById('stop-bot-confirm-input');
  var confirmBtn = document.getElementById('stop-bot-confirm-btn');
  if (input) { input.value = ''; input.classList.remove('border-rose-500'); }
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.classList.add('opacity-50', 'cursor-not-allowed'); }
  NX.Forensic.modal.open(modal);
  if (input) setTimeout(function () { try { input.focus(); } catch (e) {} }, 20);
}

function onStopBotInput(e) {
  var input = e && e.target ? e.target : document.getElementById('stop-bot-confirm-input');
  var confirmBtn = document.getElementById('stop-bot-confirm-btn');
  if (!input || !confirmBtn) return;
  var ok = input.value === 'STOP'; // exact, case-sensitive
  confirmBtn.disabled = !ok;
  confirmBtn.classList.toggle('opacity-50', !ok);
  confirmBtn.classList.toggle('cursor-not-allowed', !ok);
}

function closeStopBotModal() {
  var modal = document.getElementById('stop-bot-modal');
  if (modal) NX.Forensic.modal.close(modal);
}

async function confirmStopBot(btn) {
  var input = document.getElementById('stop-bot-confirm-input');
  if (!input || input.value !== 'STOP') {
    NX.Forensic.toast.warning('Type STOP exactly to confirm.');
    return;
  }
  if (btn && btn._busy) return; // duplicate guard
  NX.Forensic.withButtonLock(btn, 'Stopping…', async function () {
    var result = await NX.api.post('/api/engine/toggle', { active: false }, { component: 'Engine', action: 'STOP' });
    if (!result || !result.ok) {
      var n = NX.Forensic.normalizeError(result, { component: 'Engine', action: 'STOP', endpoint: '/api/engine/toggle' });
      NX.Forensic.toast.error('Stop Bot failed: ' + n.message, { detail: n.detail });
      return; // modal stays open; truthful failure
    }
    var body = result.body || {};
    if (body.success) {
      NX.Forensic.toast.success('Bot stopped. The engine loop is halted; open positions are untouched.');
      closeStopBotModal();
    } else {
      NX.Forensic.toast.error('Stop Bot was not confirmed by the server.');
      // modal stays open — no fake success
    }
  });
}

// Replace the old direct toggle with a confirmation-gated flow.
async function toggleEngineRunning() {
  // When currently running (label says "Stop Bot"), require confirmation.
  var btn = document.getElementById('btn-toggle-engine');
  var isStopping = btn && btn.textContent && btn.textContent.indexOf('Stop') !== -1;
  if (isStopping) {
    openStopBotModal();
    return;
  }
  // Starting the bot: direct (non-destructive) action.
  try {
    var result = await NX.api.post('/api/engine/toggle', { active: true }, { component: 'Engine', action: 'START' });
    if (result && result.ok && result.body && result.body.success) {
      NX.Forensic.toast.success('Bot started.');
    } else {
      var n = NX.Forensic.normalizeError(result, { component: 'Engine', action: 'START', endpoint: '/api/engine/toggle' });
      NX.Forensic.toast.error('Start failed: ' + n.message, { detail: n.detail });
    }
  } catch (e) {
    var ne = NX.Forensic.normalizeError(e, { component: 'Engine', action: 'START' });
    NX.Forensic.toast.error(ne.message, { detail: ne.detail });
  }
}

// =====================================================================
// INCIDENT DETAIL / SEARCH / PROBES — route through NX.api + toasts.
// No raw stack traces rendered.
// =====================================================================

async function showIncidentDetail(id) {
  var el = document.getElementById('incident-detail');
  if (!el) return;
  el.classList.remove('hidden');
  el.innerHTML = '<p class="text-xs text-textMuted">Loading…</p>';
  try {
    var res = await NX.api.get('/api/diagnostics/incidents/' + encodeURIComponent(id), { component: 'Incidents', action: 'DETAIL' });
    if (!res || !res.ok) { var n = NX.Forensic.normalizeError(res, { component: 'Incidents', action: 'DETAIL' }); el.innerHTML = '<p class="text-xs text-rose-400">' + esc(n.message) + '</p>'; return; }
    var data = res.body || {};
    if (data.available === false || !data.incident) { el.innerHTML = '<p class="text-xs text-rose-400">Incident not found.</p>'; return; }
    var inc = data.incident;
    var html = '<h3 class="text-sm font-bold text-white">Incident <code>' + esc(inc.incident_id) + '</code>' +
      ' <span class="text-[10px] px-1.5 py-0.5 rounded border ' + incSevClass(inc.severity) + '">' + esc(inc.severity) + '</span>' +
      ' <span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-500/10 border border-slate-500/30">' + esc(inc.status || '') + '</span></h3>';
    html += '<div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">' +
      '<div><span class="text-textMuted">Category:</span> ' + esc(inc.category) + '</div>' +
      '<div><span class="text-textMuted">Component:</span> <code>' + esc(inc.component) + '</code></div>' +
      '<div><span class="text-textMuted">Root cause:</span> ' + esc(inc.root_cause_status || 'UNKNOWN') + '</div>' +
      '<div><span class="text-textMuted">Correlation:</span> <code>' + esc(inc.correlation_id || '—') + '</code></div>' +
      '<div><span class="text-textMuted">First seen:</span> ' + esc((inc.first_seen_at || '').toString().slice(0, 19).replace('T', ' ')) + '</div>' +
      '<div><span class="text-textMuted">Last seen:</span> ' + esc((inc.last_seen_at || '').toString().slice(0, 19).replace('T', ' ')) + '</div>' +
      '<div><span class="text-textMuted">Repeats:</span> ' + (inc.repeated_count || 1) + '</div>' +
      (inc.related_bug_id ? '<div><span class="text-textMuted">BUG:</span> ' + esc(inc.related_bug_id) + '</div>' : '') +
      '</div>';
    if (inc.root_cause) html += '<p class="text-xs text-gray-300 mt-2">' + esc(inc.root_cause) + '</p>';
    var plan = inc.recovery_plan || {};
    if (plan.options && plan.options.length) {
      html += '<h4 class="text-xs font-bold text-accentCyan mt-3">Recovery plan (' + esc(plan.status || 'RECOMMENDED') + ')</h4><ul class="text-[11px] space-y-1 mt-1">';
      plan.options.forEach(function (o) { html += '<li>[' + esc(o.status) + '] ' + esc(o.action) + '</li>'; });
      html += '</ul>';
    }
    if (inc.timeline && inc.timeline.length) {
      html += '<h4 class="text-xs font-bold text-accentCyan mt-3">Timeline</h4><ul class="text-[11px] space-y-1 mt-1 max-h-48 overflow-y-auto">';
      inc.timeline.forEach(function (t) { html += '<li><code>' + esc((t.timestamp || '').slice(11, 19)) + '</code> [' + esc(t.source) + '] ' + esc(t.event_type) + '</li>'; });
      html += '</ul>';
    }
    if (inc.quarantine_entries && inc.quarantine_entries.length) {
      html += '<h4 class="text-xs font-bold text-amber-400 mt-3">Quarantine</h4><ul class="text-[11px] space-y-1 mt-1">';
      inc.quarantine_entries.forEach(function (q) { html += '<li><code>' + esc(q.target_table) + '</code> ' + esc(q.record_key) + ' -> ' + esc(q.status) + ' (' + esc(q.reason) + ')</li>'; });
      html += '</ul>';
    }
    el.innerHTML = html;
  } catch (e) {
    var ne = NX.Forensic.normalizeError(e, { component: 'Incidents', action: 'DETAIL' });
    el.innerHTML = '<p class="text-xs text-rose-400">' + esc(ne.message) + '</p>';
  }
}

async function searchIncidents() {
  var input = document.getElementById('incident-search-input');
  var resEl = document.getElementById('incident-search-results');
  if (!input || !resEl) return;
  var q = input.value.trim();
  resEl.innerHTML = '';
  if (!q) { resEl.innerHTML = '<p class="text-xs text-textMuted">Enter an identifier to trace.</p>'; return; }
  resEl.innerHTML = '<p class="text-xs text-textMuted">Tracing…</p>';
  try {
    var res = await NX.api.get('/api/diagnostics/trace?query=' + encodeURIComponent(q), { component: 'Incidents', action: 'TRACE' });
    if (!res || !res.ok) { var n = NX.Forensic.normalizeError(res, { component: 'Incidents', action: 'TRACE' }); resEl.innerHTML = '<p class="text-xs text-rose-400">' + esc(n.message) + '</p>'; return; }
    var data = res.body || {};
    var tr = data.trace || {};
    if (tr.missing_link) {
      resEl.innerHTML = '<div class="border border-amber-500/30 rounded p-2 bg-darkBg/50 text-[11px]"><b class="text-amber-400">TRACE</b> missing link: <code>' + esc(tr.missing_link) + '</code> — ' + esc(tr.reason || '') + (tr.last_known_node ? ' (last known: ' + esc(String(tr.last_known_node)) + ')' : '') + '</div>';
    } else if (tr.kind === 'incident') {
      var rc = tr.root_cause || {};
      resEl.innerHTML = '<div class="border border-borderClr rounded p-2 bg-darkBg/50 text-[11px] space-y-1">' +
        '<div><b class="text-accentCyan">INCIDENT ' + esc(tr.query) + '</b> — ' + esc((tr.incident || {}).severity || '') + '/' + esc((tr.incident || {}).status || '') + '</div>' +
        '<div>root cause: <b>' + esc(rc.status || 'UNKNOWN') + '</b> — ' + esc(rc.statement || '') + ' (evidence: ' + (rc.evidence_count || 0) + ')</div>' +
        '<div>affected records: ' + ((tr.affected_entities && tr.affected_entities.affected_records) ? tr.affected_entities.affected_records.length : 0) + '</div>' +
        '<div>lineage downstream: ' + ((tr.lineage && tr.lineage.downstream) ? tr.lineage.downstream.length : 0) + ' nodes</div></div>';
    } else {
      var h = '<div class="border border-borderClr rounded p-2 bg-darkBg/50 text-[11px] space-y-1">';
      h += '<div><b class="text-accentCyan">TRACE ' + esc(q) + '</b> (' + esc(tr.kind || 'object') + ')</div>';
      h += '<div>ledger: ' + (tr.ledger ? 'found' : '—') + ' · broker position: ' + (tr.broker_position ? 'found' : '—') + '</div>';
      h += '<div>outcome: ' + (tr.outcome ? 'found' : '—') + ' · experience: ' + (tr.experience ? 'found' : '—') + '</div>';
      h += (tr.model_id ? '<div>model: <code>' + esc(tr.model_id) + '</code></div>' : '');
      h += (tr.research_runs && tr.research_runs.length ? '<div>research runs: ' + tr.research_runs.length + '</div>' : '');
      h += '</div>';
      resEl.innerHTML = h;
    }
  } catch (e) {
    var ne = NX.Forensic.normalizeError(e, { component: 'Incidents', action: 'TRACE' });
    resEl.innerHTML = '<p class="text-xs text-rose-400">' + esc(ne.message) + '</p>';
  }
}

// =====================================================================
// FORENSIC PROBES / AUDIT — truthful toasts, no raw leak (spec 9/11).
// =====================================================================

async function runForensicProbe(kind) {
  var resEl = document.getElementById('forensic-probe-results');
  if (!resEl) return;
  var ticketParam = '';
  try { var ti = document.getElementById('incident-search-input'); if (ti && ti.value.trim() && !String(ti.value.trim()).toUpperCase().startsWith('INC-')) { ticketParam = '&ticket=' + encodeURIComponent(ti.value.trim()); } } catch (e) {}
  resEl.innerHTML = 'Running ' + kind + ' probe…';
  var res = await NX.api.get('/api/diagnostics/forensics?kind=' + encodeURIComponent(kind) + ticketParam, { component: 'Forensics', action: 'PROBE_' + kind.toUpperCase() });
  if (!res || !res.ok) { var n = NX.Forensic.normalizeError(res, { component: 'Forensics', action: 'PROBE' }); NX.Forensic.toast.error(n.message, { detail: n.detail }); resEl.innerHTML = '<p class="text-xs text-rose-400">' + esc(n.message) + '</p>'; return; }
  var d = res.body || {};
  if (d.available === false) { NX.Forensic.toast.warning('Probe unavailable: ' + (d.error || '')); resEl.innerHTML = '<p class="text-xs text-rose-400">Probe failed: ' + esc(d.error || '') + '</p>'; return; }
  NX.Forensic.toast.success((kind === 'timebase' ? 'Timebase' : 'Accounting') + ' probe completed.' + (d.recovery_candidate_count ? ' ' + d.recovery_candidate_count + ' recovery candidate(s).' : ''));
  if (kind === 'timebase') {
    var o = d.measured_offsets_seconds || {};
    var html = '<div class="border border-sky-500/30 rounded p-2 bg-darkBg/50 text-[11px]"><b class="text-sky-400">TIMEBASE</b> ' + esc(d.classification || '') +
      ' · sync lag: <code>' + (d.sync_lag_seconds ?? 'n/a') + 's</code> · data age: <code>' + (d.observed_data_age_seconds ?? 'n/a') + 's</code>' +
      ' · host→db: <code>' + (o.host_to_db ?? 'n/a') + 's</code> · affected: ' + esc((d.affected_subsystems || []).join(', ')) + '</div>';
    var ec = d.event_chain || {};
    if (ec.source_time) { html += '<div class="border border-sky-500/30 rounded p-2 bg-darkBg/50 text-[11px] mt-1 space-y-1"><div><b class="text-sky-400">EVENT CHAIN</b> ' + esc(ec.source_component || '') + ' vs ' + esc(ec.comparison_component || '') + '</div><div>source: <code>' + esc(ec.source_time || '') + '</code> (' + esc(ec.source_timezone || '') + ')</div><div>normalized UTC: <code>' + esc(ec.normalized_utc || '') + '</code> · expected: <code>' + esc(ec.expected_time || '') + '</code></div><div>difference: <code>' + (ec.difference_ms ?? 'n/a') + ' ms</code> · rule: ' + esc(ec.normalization_rule || '') + '</div>' + (ec.normalization_note ? '<div class="text-amber-400">' + esc(ec.normalization_note) + '</div>' : '') + '</div>'; }
    resEl.innerHTML = html;
  } else {
    var cls = d.classification_counts || {}, zcls = d.zero_outcome_classification_counts || {};
    resEl.innerHTML = '<div class="border border-amber-500/30 rounded p-2 bg-darkBg/50 text-[11px]"><b class="text-amber-400">ACCOUNTING AUDIT</b> checked=' + (d.checked_records ?? 0) + ' · classification: ' + esc(JSON.stringify(cls)) + ' · zero-outcomes: ' + esc(JSON.stringify(zcls)) + ' · recovery candidates: ' + (d.recovery_candidate_count ?? 0) + '</div>';
  }
}

async function runIncidentAudit() {
  var resEl = document.getElementById('forensic-probe-results');
  if (!resEl) return;
  resEl.innerHTML = 'Running full forensic audit…';
  var res = await NX.api.post('/api/diagnostics/incidents/reconcile', {}, { component: 'Forensics', action: 'AUDIT' });
  if (!res || !res.ok) { var n = NX.Forensic.normalizeError(res, { component: 'Forensics', action: 'AUDIT' }); NX.Forensic.toast.error(n.message, { detail: n.detail }); resEl.innerHTML = '<p class="text-xs text-rose-400">' + esc(n.message) + '</p>'; return; }
  var d = res.body || {};
  if (d.available === false) { NX.Forensic.toast.warning('Audit unavailable: ' + (d.error || '')); resEl.innerHTML = '<p class="text-xs text-rose-400">Audit failed: ' + esc(d.error || '') + '</p>'; return; }
  NX.Forensic.toast.success('Forensic audit complete: ' + (d.incidents_reconciled ?? 0) + ' incident(s) reconciled.');
  var f = d.findings || {};
  var html = '<div class="border border-emerald-500/30 rounded p-2 bg-darkBg/50 text-[11px]">';
  html += '<b class="text-emerald-400">AUDIT</b> started ' + esc(String(d.audit_started || '').slice(0, 19).replace('T', ' ')) + ' · scope: ' + esc((d.audit_scope || []).join(', ')) + '</div>';
  html += '<div class="text-[11px] mt-1">accounting divergences: ' + ((f.accounting || {}).divergence_count ?? 'n/a') + ' · timebase: ' + esc((f.timebase || {}).divergence || 'n/a') + ' · suspect outcomes: ' + ((f.outcome || {}).zero_realized_outcomes ?? 'n/a') + ' · split families: ' + ((f.split_fill || {}).split_fill_families ?? 'n/a') + '</div>';
  html += '<div class="text-[11px] mt-1">incidents discovered: ' + (d.incidents_discovered ?? 0) + ' · reconciled: ' + (d.incidents_reconciled ?? 0) + '</div>';
  html += '</div>';
  resEl.innerHTML = html;
  loadIncidents();
}

async function exportIncident(kind) {
  var id = '';
  var detailEl = document.getElementById('incident-detail');
  if (detailEl && !detailEl.classList.contains('hidden')) {
    var m = detailEl.innerHTML.match(/Incident <code>([^<]+)<\/code>/);
    if (m) id = m[1];
  }
  if (!id) {
    var res = await NX.api.get('/api/diagnostics/incidents?limit=1', { component: 'Forensics', action: 'EXPORT_LOOKUP' });
    if (res && res.ok && res.body && res.body.incidents && res.body.incidents.length) id = res.body.incidents[0].incident_id;
  }
  if (!id) { NX.Forensic.toast.warning('No incident to export.'); return; }
  var url = '/api/diagnostics/incidents/' + encodeURIComponent(id) + (kind === 'report' ? '/report' : '/zip');
  NX.Forensic.toast.info('Exporting ' + (kind === 'report' ? 'report' : 'evidence ZIP') + ' for ' + id + '…');
  window.open(url, '_blank');
}

// TASK-22: Database Health Panel (spec 17) — real backend data only.
async function loadHealthPanel() {
    const ids = ['dbh-scheduler', 'dbh-next', 'dbh-quarantined', 'dbh-audit'];
    const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    const planEl = document.getElementById('db-health-plan');
    const errEl = document.getElementById('db-health-errors');
    if (errEl) errEl.textContent = '';
    try {
        const res = await fetch('/api/db/hygiene');
        const data = await res.json();
        const runtime = data.runtime || {};
        const q = data.quarantine || {};
        setTxt('dbh-scheduler', runtime.available ? (runtime.worker_mode || 'NOT_STARTED') : 'UNAVAILABLE');
        const nextIn = runtime.next_light_in_sec ?? null;
        setTxt('dbh-next', nextIn === null ? '—' : Math.round(nextIn) + 's');
        setTxt('dbh-quarantined', (q.total ?? 0) + ' item(s)');
        setTxt('dbh-audit', runtime.initial_audit_done ? 'DONE' : 'PENDING');
        if (planEl) {
            const plans = data.plans || {};
            const entries = Object.entries(plans);
            if (!entries.length) { planEl.innerHTML = '<div class="text-textMuted italic">no plan data</div>'; }
            else {
                planEl.innerHTML = entries.map(([db, p]) => {
                    const s = p.plan || {};
                    return '<div class="bg-darkBg/40 border border-borderClr/40 rounded p-2">' +
                        '<span class="text-accentCyan font-bold">' + esc(db) + '</span><br/>' +
                        'tables: ' + (s.tables_scanned ?? 0) +
                        ' · rows: ' + (s.rows_scanned ?? 0) +
                        ' · dups: ' + (s.duplicates_found ?? 0) +
                        ' · orphans: ' + (s.orphans_found ?? 0) +
                        ' · del candidates: ' + (s.delete_candidates ?? 0) +
                        '</div>';
                }).join('');
            }
        }
    } catch (e) {
        setTxt('dbh-scheduler', 'ERROR');
        if (errEl) errEl.textContent = 'DB health load failed: ' + esc(String(e));
    }
}
// ===========================================================================
// STRATEGY FACTORY (2026-08-20) — autonomous strategy evolution control room
// ===========================================================================

// BUG-131b: NX.api returns {ok,status,body,request_id} — the old
// `res.data ?? res` pattern never saw .available (undefined -> every factory
// call reported UNAVAILABLE/UNKNOWN even when the backend succeeded).
function factoryRes(res, fallback) {
    if (res && res.ok && res.body && typeof res.body === 'object') return res.body;
    if (res && res.body && typeof res.body === 'object') return res.body;
    return fallback || { available: false, reason: (res && res.error && res.error.message) || 'UNKNOWN' };
}

// Realtime factory console: every endpoint result (success + error + warning)
// is appended to the on-tab console with a colored row so debugging is easy.
const factoryLogBuffer = [];
function factoryLog(level, text, meta) {
    const line = { ts: new Date().toISOString().substring(11, 19), level: level || 'info', text: String(text || ''), meta: meta || null };
    factoryLogBuffer.push(line);
    if (factoryLogBuffer.length > 300) factoryLogBuffer.shift();
    const el = document.getElementById('factory-console-body');
    if (el) {
        const row = document.createElement('div');
        const pal = { info: 'text-sky-300', ok: 'text-emerald-300', warn: 'text-amber-300', error: 'text-rose-300' };
        const cls = pal[level] || 'text-gray-300';
        row.className = 'text-[10px] font-mono ' + cls + ' whitespace-pre-wrap break-all';
        row.textContent = '[' + line.ts + '] ' + line.text;
        el.appendChild(row);
        while (el.childElementCount > 250) el.removeChild(el.firstElementChild);
        el.scrollTop = el.scrollHeight;
    }
    if (level === 'error') console.warn('[FACTORY] ' + text);
    else console.log('[FACTORY] ' + text);
    updateFactoryConsoleStats();
}

function updateFactoryConsoleStats() {
    const el = document.getElementById('factory-console-stats');
    if (!el) return;
    const ok = factoryLogBuffer.filter(l => l.level === 'ok' || l.level === 'info').length;
    const warn = factoryLogBuffer.filter(l => l.level === 'warn').length;
    const err = factoryLogBuffer.filter(l => l.level === 'error').length;
    el.textContent = 'ok:' + ok + ' warn:' + warn + ' err:' + err;
}

function factoryConsoleClear() {
    factoryLogBuffer.length = 0;
    const el = document.getElementById('factory-console-body');
    if (el) el.innerHTML = '<div class="text-textMuted italic font-sans">Console cleared.</div>';
    updateFactoryConsoleStats();
}

async function loadFactoryStatus() {
    try {
        const res = await NX.api.get('/api/factory/status', { component: 'StrategyFactory', action: 'STATUS' });
        const data = factoryRes(res, { available: false, reason: 'UNKNOWN' });
        if (!data.available) {
            document.getElementById('factory-loop-state').textContent = 'UNAVAILABLE';
            return;
        }
        const loop = data.loop ?? {};
        document.getElementById('factory-loop-state').textContent = loop.state ?? 'STOPPED';
        document.getElementById('factory-current-generation').textContent =
            loop.current_generation || (data.generations && data.generations.length ? data.generations[0].generation_id : '--');

        const usage = data.provider_usage ?? {};
        document.getElementById('factory-llm-cost').textContent = '$' + (usage.estimated_cost_usd ?? 0).toFixed(4);
        document.getElementById('factory-llm-requests').textContent = usage.requests ?? 0;

        // Metrics strip
        const genList = data.generations ?? [];
        const genTotals = genList.reduce((acc, g) => {
            const cfg = typeof g.config === 'string' ? safeParse(g.config) : (g.config || {});
            const s = cfg.summary || {};
            acc.generated += s.generated || 0;
            acc.valid += s.validated || 0;
            acc.rej += s.rejected || 0;
            return acc;
        }, { generated: 0, valid: 0, rej: 0 });
        setText('factory-metric-generations', String(genList.length));
        setText('factory-metric-generated', String(genTotals.generated));
        setText('factory-metric-validated', String(genTotals.valid));
        setText('factory-metric-rejected', String(genTotals.rej));
        setText('factory-metric-requests', String(usage.requests ?? 0));
        setText('factory-metric-provider-errors', String(usage.failures ?? 0));

        // operator stats
        const ops = loop.operator_stats ?? {};
        const opsBox = document.getElementById('factory-operator-stats');
        const opKeys = Object.keys(ops);
        if (opKeys.length) {
            opsBox.innerHTML = opKeys.map(op => {
                const s = ops[op] ?? {};
                return `<div><span class="text-accentCyan">${op}</span> gen=${s.generated ?? 0} surv=${s.survived ?? 0} elite=${s.elite ?? 0}</div>`;
            }).join('');
        } else {
            opsBox.innerHTML = '<div class="text-textMuted italic">No operators yet.</div>';
        }

        // generations list
        const gens = data.generations ?? [];
        const genBox = document.getElementById('factory-generations');
        if (gens.length) {
            genBox.innerHTML = gens.map(g => {
                const cfg = typeof g.config === 'string' ? safeParse(g.config) : (g.config ?? {});
                const summary = cfg.summary;
                const counts = summary
                    ? `v=${summary.validated ?? 0} r=${summary.rejected ?? 0} best=${(summary.best_score ?? 0).toFixed(2)}`
                    : `pop=${g.population_target ?? 0}`;
                return `<div class="flex justify-between"><span class="text-accentCyan">${g.generation_id}</span><span>${g.status} ${counts}</span></div>`;
            }).join('');
        } else {
            genBox.innerHTML = '<div class="text-textMuted italic">No generations yet.</div>';
        }

        await Promise.all([loadFactoryEvents(), loadFactoryFailures(), loadFactoryRanking(), loadFactoryLlmConfig(), loadFactoryBenchmarks()]);
    } catch (err) {
        factoryLog('error', 'loadFactoryStatus: ' + String(err && err.message || err));
        document.getElementById('factory-loop-state').textContent = 'ERROR';
    }
}

function safeParse(text) {
    try { return JSON.parse(text); } catch (e) { return {}; }
}

async function loadFactoryEvents() {
    try {
        const res = await NX.api.get('/api/factory/events?limit=50', { component: 'StrategyFactory', action: 'EVENTS' });
        const data = factoryRes(res, { available: false, reason: 'UNKNOWN' });
        const events = data.events ?? [];
        const box = document.getElementById('factory-events');
        if (!events.length) {
            box.innerHTML = '<div class="text-textMuted italic">No events yet.</div>';
            return;
        }
        box.innerHTML = events.slice(0, 50).map(ev => {
            const ts = (ev.created_at ?? '').replace('T', ' ').slice(5, 19);
            return `<div class="flex justify-between gap-2"><span class="text-textMuted">${ts}</span><span class="text-accentCyan">${ev.event_type}</span><span class="flex-1 truncate">${ev.message ?? ''}</span></div>`;
        }).join('');
    } catch (err) {
        console.warn('factory events failed', err);
    }
}

async function loadFactoryFailures() {
    try {
        const res = await NX.api.get('/api/factory/failures?limit=50', { component: 'StrategyFactory', action: 'FAILURES' });
        const data = factoryRes(res, { available: false, reason: 'UNKNOWN' });
        const failures = data.failures ?? [];
        const box = document.getElementById('factory-failures');
        if (!failures.length) {
            box.innerHTML = '<div class="text-textMuted italic">No failures recorded.</div>';
            return;
        }
        box.innerHTML = failures.slice(0, 50).map(f => {
            const ts = (f.created_at ?? '').replace('T', ' ').slice(5, 19);
            return `<div class="flex justify-between gap-2"><span class="text-textMuted">${ts}</span><span class="text-accentRed">${f.reason}</span><span class="text-xs">${f.candidate_id}</span></div>`;
        }).join('');
    } catch (err) {
        console.warn('factory failures failed', err);
    }
}

async function loadFactoryRanking() {
    try {
        const res = await NX.api.get('/api/factory/ranking?dimension=OVERALL&limit=20', { component: 'StrategyFactory', action: 'RANKING' });
        const data = factoryRes(res, { available: false, reason: 'UNKNOWN' });
        const ranked = data.ranked ?? [];
        const box = document.getElementById('factory-ranking');
        if (!ranked.length) {
            box.innerHTML = '<div class="text-textMuted italic">No ranked strategies yet.</div>';
            return;
        }
        box.innerHTML = ranked.map((r, i) => {
            const score = r.score && typeof r.score === 'string' ? safeParse(r.score) : (r.score ?? {});
            const total = r._dimension_score ?? (score.final_score ?? 0);
            return `<div class="flex justify-between gap-2"><span class="text-accentCyan">#${i + 1}</span><span class="font-mono">${r.strategy_id}</span><span class="text-accentGreen">${typeof total === 'number' ? total.toFixed(3) : total}</span></div>`;
        }).join('');
    } catch (err) {
        console.warn('factory ranking failed', err);
    }
}

async function factoryGenerate() {
    const size = parseInt(document.getElementById('factory-generation-size').value || '400', 10);
    const mode = document.getElementById('factory-mode').value || 'MANUAL';
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = 'Generating…';
    try {
        const res = await NX.api.post('/api/factory/generate', { size, mode }, { component: 'StrategyFactory', action: 'GENERATE' });
        const data = factoryRes(res, { available: false, reason: 'UNKNOWN' });
        if (data.available) {
            factoryLog('ok', 'Generate ' + (data.generation && data.generation.generation_id) +
                ' -> population ' + data.population + ' | structurally valid ' + data.passed +
                ' | rejected ' + data.failed + ' | status ' + (data.status || ''));
            alert(`Generation ${data.generation?.generation_id} created:\nPopulation: ${data.population}\nStructurally valid: ${data.passed}\nRejected: ${data.failed}`);
            await loadFactoryStatus();
        } else {
            factoryLog('error', 'Generate failed: ' + (data.reason ?? 'UNKNOWN'));
            alert('Generation failed: ' + (data.reason ?? 'UNKNOWN'));
        }
    } catch (err) {
        factoryLog('error', 'Generate threw: ' + String(err && err.message || err));
        alert('Generation error — see Factory console below');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Generate';
    }
}

async function factoryLoopStart() {
    try {
        const res = await NX.api.post('/api/factory/loop/start', {}, { component: 'StrategyFactory', action: 'LOOP_START' });
        const data = factoryRes(res, { available: false, reason: 'UNKNOWN' });
        if (data.available) { factoryLog('ok', 'Loop STARTED'); document.getElementById('factory-loop-state').textContent = 'RUNNING'; }
        else factoryLog('error', 'Loop start failed: ' + (data.reason || 'UNKNOWN'));
        await loadFactoryStatus();
    } catch (err) { factoryLog('error', 'Loop start threw: ' + String(err && err.message || err)); }
}

async function factoryLoopPause() {
    try {
        await NX.api.post('/api/factory/loop/pause', {}, { component: 'StrategyFactory', action: 'LOOP_PAUSE' });
        await loadFactoryStatus();
    } catch (err) { console.warn('loop pause failed', err); }
}

async function factoryLoopResume() {
    try {
        await NX.api.post('/api/factory/loop/resume', {}, { component: 'StrategyFactory', action: 'LOOP_RESUME' });
        await loadFactoryStatus();
    } catch (err) { console.warn('loop resume failed', err); }
}

async function factoryLoopStop() {
    try {
        await NX.api.post('/api/factory/loop/stop', {}, { component: 'StrategyFactory', action: 'LOOP_STOP' });
        document.getElementById('factory-loop-state').textContent = 'STOPPED';
        await loadFactoryStatus();
    } catch (err) { console.warn('loop stop failed', err); }
}

// ---------------------------------------------------------------------------
// LLM Provider config (BUG-131): set OpenAI-compatible endpoint + key from UI.
// ---------------------------------------------------------------------------
async function loadFactoryLlmConfig() {
    try {
        const res = await NX.api.get('/api/factory/llm-config', { component: 'StrategyFactory', action: 'LLM_STATUS' });
        const data = factoryRes(res, { available: false, reason: 'UNKNOWN' });
        if (!data.available) {
            setFactoryLlmStatus(false, data.reason || 'UNAVAILABLE');
            return;
        }
        const st = data.status ?? {};
        const prov = data.provider ?? {};
        const baseEl = document.getElementById('factory-llm-baseurl');
        const modelEl = document.getElementById('factory-llm-model');
        const tempEl = document.getElementById('factory-llm-temperature');
        // Never echo a stored key back into the DOM (secret stays in the
        // backend secret store); the field stays blank until the user types.
        if (baseEl && st.base_url) baseEl.value = st.base_url;
        if (modelEl && st.model) modelEl.value = st.model;
        if (tempEl && st.temperature != null) tempEl.value = st.temperature;
        const timeoutEl = document.getElementById('factory-llm-timeout');
        if (timeoutEl && st.request_timeout_sec != null) timeoutEl.value = st.request_timeout_sec;
        const maxreqEl = document.getElementById('factory-llm-maxreq');
        if (maxreqEl && st.max_requests_per_generation != null) maxreqEl.value = st.max_requests_per_generation;
        const cfgReady = Boolean(st.base_url && st.model && st.api_key_set);
        const provReady = Boolean(prov.available);
        setFactoryLlmStatus(cfgReady && provReady,
            (cfgReady ? 'CONFIGURED' : 'NOT CONFIGURED') +
            (provReady ? ' · provider READY' : (cfgReady ? ' · provider not built yet' : ' · key missing')));
        const detail = document.getElementById('factory-llm-detail');
        if (detail) {
            detail.textContent = 'prompt=' + (prov.prompt_version || st.prompt_version || '--') +
                ' · model=' + (prov.model || st.model || '--') +
                ' · base=' + (prov.base_url || st.base_url || '--') +
                ' · usage=' + (prov.usage ? prov.usage.requests + ' req · $' + (prov.usage.estimated_cost_usd ?? 0).toFixed(4) : '0 req') +
                (st.api_key_set ? ' · key: SET (hidden)' : ' · key: NOT SET');
        }
    } catch (err) {
        console.warn('factory llm config load failed', err);
        setFactoryLlmStatus(false, 'ERROR');
    }
}

function setFactoryLlmStatus(ok, text) {
    const el = document.getElementById('factory-llm-status');
    if (!el) return;
    el.textContent = text || (ok ? 'OK' : 'UNKNOWN');
    const okTxt = String(text || '').toUpperCase();
    if (okTxt.includes('READY') || okTxt.includes('CONFIGURED')) {
        el.className = 'text-[10px] font-black px-2 py-1 rounded bg-gradient-to-r from-emerald-500/20 to-teal-500/20 text-emerald-300 border border-emerald-500/40';
    } else if (okTxt.includes('NOT CONFIGURED') || okTxt.includes('KEY MISSING')) {
        el.className = 'text-[10px] font-black px-2 py-1 rounded bg-gradient-to-r from-amber-500/20 to-orange-500/20 text-amber-300 border border-amber-500/40';
    } else {
        el.className = 'text-[10px] font-black px-2 py-1 rounded bg-gradient-to-r from-rose-500/20 to-red-500/20 text-rose-300 border border-rose-500/40';
    }
}

async function saveFactoryLlmConfig() {
    const btn = event.target.closest('button');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    const payload = {
        base_url: document.getElementById('factory-llm-baseurl').value.trim(),
        api_key: document.getElementById('factory-llm-apikey').value.trim(),
        model: document.getElementById('factory-llm-model').value.trim(),
        temperature: parseFloat(document.getElementById('factory-llm-temperature').value || '0.7'),
        request_timeout_sec: parseFloat(document.getElementById('factory-llm-timeout').value || '300'),
        max_requests_per_generation: parseInt(document.getElementById('factory-llm-maxreq').value || '60', 10),
    };
    try {
        const res = await NX.api.post('/api/factory/llm-config', payload, { component: 'StrategyFactory', action: 'LLM_SAVE' });
        const data = factoryRes(res, { available: false, reason: 'UNKNOWN' });
        if (data.available) {
            document.getElementById('factory-llm-apikey').value = '';
            await Promise.all([loadFactoryLlmConfig(), loadFactoryStatus()]);
        } else {
            alert('LLM config save failed: ' + (data.reason ?? 'UNKNOWN'));
        }
    } catch (err) {
        console.warn('factory llm config save failed', err);
        alert('LLM config save error — see console');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Save & Reload'; }
    }
}

async function clearFactoryLlmKey() {
    if (!confirm('Remove the stored LLM API key? The factory falls back to deterministic generation until you set a new key.')) return;
    try {
        const res = await NX.api.post('/api/factory/llm-config', { clear_api_key: true }, { component: 'StrategyFactory', action: 'LLM_CLEAR_KEY' });
        const data = factoryRes(res, { available: false, reason: 'UNKNOWN' });
        if (data.available) {
            await Promise.all([loadFactoryLlmConfig(), loadFactoryStatus()]);
        } else {
            alert('Clear key failed: ' + (data.reason ?? 'UNKNOWN'));
        }
    } catch (err) {
        console.warn('factory llm clear key failed', err);
        alert('Clear key error — see console');
    }
}

// startup: load factory status along with the other panels
setInterval(() => {
    const tab = document.querySelector('#tab-factory');
    if (tab && !tab.classList.contains('hidden')) {
        loadFactoryStatus();
    }
}, 10000);

async function loadFactoryBenchmarks(generationId) {
    try {
        const sel = document.getElementById('factory-benchmark-generation');
        const gid = (generationId !== undefined && generationId !== null) ? generationId : (sel ? sel.value : '');
        const qs = gid ? ('?generation_id=' + encodeURIComponent(gid) + '&limit=50') : '?limit=50';
        const res = await NX.api.get('/api/factory/benchmarks' + qs, { component: 'StrategyFactory', action: 'BENCHMARKS' });
        const data = factoryRes(res, { available: false, reason: 'UNKNOWN' });
        if (!data.available) {
            const box = document.getElementById('factory-benchmarks');
            if (box) box.innerHTML = '<div class="text-amber-300 italic">Benchmarks unavailable: ' + escHtml(data.reason || 'UNKNOWN') + '</div>';
            return;
        }
        const bms = data.benchmarks ?? [];
        if (sel && sel.options.length <= 1) {
            try {
                const st = await NX.api.get('/api/factory/generations?limit=20', { component: 'StrategyFactory', action: 'GENS_FOR_BM' });
                const sd = factoryRes(st, { available: false });
                if (sd.available && sd.generations) {
                    sd.generations.forEach(function(g) {
                        if (!Array.from(sel.options).some(function(o){ return o.value === g.generation_id; })) {
                            var o = document.createElement('option'); o.value = g.generation_id; o.textContent = g.generation_id + ' (' + (g.status || '') + ')'; sel.appendChild(o);
                        }
                    });
                }
            } catch(e) {}
        }
        const elite = bms.filter(function(b){ return b.decision === 'CANDIDATE_ELITE' || b.lifecycle === 'VALIDATED'; }).length;
        const incon = bms.filter(function(b){ return b.decision === 'INCONCLUSIVE_NEEDS_MORE_DATA'; }).length;
        const rej = bms.length - elite - incon;
        var avgCov = 0; if (bms.length) { var s=0,c=0; bms.forEach(function(b){ var pct=(b.coverage && b.coverage.coverage_pct)!=null? b.coverage.coverage_pct : null; if(pct!=null){ s+=Number(pct); c++; }}); if(c) avgCov=s/c; }
        var setBm = function(id, v){ var el=document.getElementById(id); if(el) el.textContent=v; };
        setBm('factory-bm-count', String(bms.length));
        setBm('factory-bm-elite', String(elite));
        setBm('factory-bm-inconclusive', String(incon));
        setBm('factory-bm-rejected', String(rej < 0 ? 0 : rej));
        setBm('factory-bm-coverage', bms.length ? avgCov.toFixed(1) + '%' : '--');
        const box = document.getElementById('factory-benchmarks');
        if (!box) return;
        if (!bms.length) {
            box.innerHTML = '<div class="text-textMuted italic">No benchmarks yet for this generation.</div>';
            return;
        }
        box.innerHTML = bms.slice(0,50).map(function(b){
            var decision = b.decision || b.lifecycle || '--';
            var decCls = decision === 'CANDIDATE_ELITE' ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' : decision === 'INCONCLUSIVE_NEEDS_MORE_DATA' ? 'bg-amber-500/15 text-amber-300 border-amber-500/30' : 'bg-rose-500/10 text-rose-300 border-rose-500/30';
            var cov = b.coverage || {};
            var covTxt = cov.coverage_pct != null ? cov.coverage_pct + '%' : (cov.matched != null ? cov.matched + '/' + (cov.total_ledger_samples ?? '?') : '--');
            var bt = b.backtest || {}; var wf = b.walk_forward || {}; var oos = b.oos || {};
            var score = b.score || {};
            var fs = score.final_score != null ? Number(score.final_score).toFixed(3) : '--';
            var verdict = score.verdict || b.lifecycle || '--';
            var pf = b.primary_failure || (oos.status !== 'PASS' ? 'OOS' : (!wf.passed ? 'WALK_FORWARD' : '--'));
            var expR = bt.expectancy_r != null ? Number(bt.expectancy_r).toFixed(4) : '--';
            var oosE = oos.oos_expectancy_r != null ? Number(oos.oos_expectancy_r).toFixed(4) : '--';
            var flist = Array.isArray(b.dsl_filters) ? b.dsl_filters.slice(0,3).map(function(f){ return escHtml((f.feature||'') + ' ' + (f.op||'') + ' ' + String(f.value ?? '')); }).join(', ') : '';
            return '<div class="bg-darkBg/40 border border-borderClr/40 rounded-lg p-3 hover:border-violet-400/30 transition">'
                + '<div class="flex flex-wrap justify-between gap-2 items-center">'
                + '<span class="font-black text-violet-300">' + escHtml(b.candidate_id || b.benchmark_id || '--') + '</span>'
                + '<span class="text-[10px] font-black px-2 py-0.5 rounded border ' + decCls + '">' + escHtml(decision) + '</span>'
                + '<span class="text-textMuted">family <span class="text-gray-200">' + escHtml(b.family || '--') + '</span></span>'
                + '<span class="text-textMuted">coverage <span class="text-accentCyan">' + escHtml(String(covTxt)) + '</span></span>'
                + '<span class="text-textMuted">score <span class="text-emerald-300">' + escHtml(fs) + '</span> ' + escHtml(verdict) + '</span>'
                + '</div>'
                + '<div class="mt-1.5 grid grid-cols-2 lg:grid-cols-4 gap-2 text-[10px] leading-tight">'
                + '<div><span class="text-textMuted">backtest</span> expR ' + escHtml(expR) + ' pf ' + escHtml(bt.profit_factor != null ? String(bt.profit_factor) : '--') + ' trades ' + escHtml(bt.total_trades != null ? String(bt.total_trades) : '--') + '</div>'
                + '<div><span class="text-textMuted">walk-fwd</span> ' + (wf.passed ? '<span class="text-emerald-400">PASS</span>' : '<span class="text-rose-400">FAIL</span>') + ' ' + escHtml(wf.passes != null ? (wf.passes + '/' + (wf.folds ?? '?')) : '--') + ' rate ' + escHtml(wf.pass_rate != null ? String(wf.pass_rate) : '--') + '</div>'
                + '<div><span class="text-textMuted">OOS</span> ' + escHtml(oos.status || '--') + ' expR ' + escHtml(oosE) + '</div>'
                + '<div><span class="text-textMuted">primary failure</span> <span class="text-rose-300">' + escHtml(pf) + '</span></div>'
                + '</div>'
                + (flist ? '<div class="mt-1 text-[10px] text-textMuted truncate">filters: <span class="text-gray-300">' + flist + '</span></div>' : '')
                + '</div>';
        }).join('');
        if (bms.length) factoryLog('info', 'Benchmarks loaded: ' + bms.length + ' (elite ' + elite + ', inconclusive ' + incon + ')');
    } catch (err) {
        console.warn('factory benchmarks failed', err);
        factoryLog('warn', 'Benchmarks load failed: ' + String(err && err.message || err));
    }
}


// ===========================================================================
// DATABASE MANAGEMENT PANEL (DATABASE PORTABILITY, 2026-08-20)
// Provider status, PostgreSQL config, and the SQLite->PostgreSQL migration
// workflow.  All network calls go through NX.api; passwords are sent once
// to the backend and never stored in DOM/localStorage.
// ===========================================================================

function _dbEl(id) { return document.getElementById(id); }

function dbSet(id, text) { const el = _dbEl(id); if (el) el.textContent = text; }

async function loadDbStatus() {
  try {
    const r = await NX.api.get('/api/db/manage/status', { component: 'DatabaseManagement', action: 'STATUS' });
    const data = r.success ? r : await r.json?.() ?? r;
    if (!data.success) throw new Error('status failed');
    dbSet('db-current-provider', String(data.provider || 'sqlite').toUpperCase());
    dbSet('db-current-status', data.overall || '--');
    const aud = data.domains && data.domains.audit || {};
    dbSet('db-current-name', aud.database || '--');
    dbSet('db-current-server', aud.server || '--');
    dbSet('db-current-schema', String(aud.schema_version ?? '--'));
    dbSet('db-current-health', aud.health || '--');
    dbSet('db-current-latency', aud.latency_ms != null ? aud.latency_ms + ' ms' : '--');
    dbSet('db-nav-state', String(data.provider || 'sqlite').toUpperCase());
    const sel = _dbEl('db-provider-select');
    if (sel) sel.value = data.provider || 'sqlite';
    return data;
  } catch (err) {
    console.warn('db status failed', err);
    dbSet('db-nav-state', 'ERR');
  }
}

function pgPayload(extra) {
  const g = (id) => _dbEl(id) ? _dbEl(id).value : '';
  return Object.assign({
    host: g('pg-host') || 'localhost',
    port: parseInt(g('pg-port') || '5432', 10),
    database: g('pg-database') || 'nse_audit',
    username: g('pg-user') || 'nse_user',
    ssl_mode: g('pg-sslmode') || '',
    password: g('pg-password') || '',
    confirm_password: g('pg-password') || '',
  }, extra || {});
}

async function savePgConfig() {
  try {
    const r = await NX.api.post('/api/db/manage/config', pgPayload(), { component: 'DatabaseManagement', action: 'SAVE_CONFIG' });
    const data = r.success ? r : await r.json?.() ?? r;
    _dbEl('pg-password').value = '';
    dbSet('db-test-result', data.success ? 'Configuration saved (password stored securely).' : 'Save failed: ' + JSON.stringify(data));
    await loadDbStatus();
  } catch (err) { console.warn('save pg config failed', err); dbSet('db-test-result', 'Save failed'); }
}

async function testDbConnection() {
  try {
    dbSet('db-test-result', 'Testing connection...');
    const r = await NX.api.post('/api/db/manage/test-connection', pgPayload(), { component: 'DatabaseManagement', action: 'TEST_CONNECTION' });
    const data = r.success ? r : await r.json?.() ?? r;
    dbSet('db-test-result', data.connected ? 'Connected: ' + (data.database_version || '') : 'Connection failed.');
  } catch (err) { console.warn('test connection failed', err); dbSet('db-test-result', 'Connection failed'); }
}

async function previewDbMigration() {
  try {
    dbSet('db-preview', 'Analyzing source...');
    const r = await NX.api.post('/api/db/manage/preview', pgPayload(), { component: 'DatabaseManagement', action: 'PREVIEW' });
    const data = r.success ? r : await r.json?.() ?? r;
    if (!data.success) { dbSet('db-preview', 'Preview failed.'); return; }
    const p = data.preview || {};
    const lines = [
      'SOURCE: ' + (p.source || 'sqlite') + '  ->  DESTINATION: ' + (p.destination || 'postgresql'),
      'TABLES: ' + p.tables + '   ROWS: ' + p.rows + '   EST. VOLUME: ' + (p.estimated_volume_bytes != null ? Math.round(p.estimated_volume_bytes / 1024 / 1024) + ' MB' : 'n/a'),
      'ISSUES: ' + ((p.issues || []).join('; ') || 'none'),
    ];
    const details = Object.entries(p.table_details || {}).slice(0, 12).map(([t, v]) => t + ' (' + v.rows + ' rows)').join('  ');
    dbSet('db-preview', lines.join('\n') + '\n' + details);
  } catch (err) { console.warn('preview failed', err); dbSet('db-preview', 'Preview failed'); }
}

async function startDbMigration() {
  if (!confirm('Start SQLite -> PostgreSQL migration? This copies all data in streamed batches and does NOT delete the SQLite source. Continue?')) return;
  try {
    dbSet('db-report', 'Migration started...');
    const r = await NX.api.post('/api/db/manage/migrate', pgPayload({ confirm: true }), { component: 'DatabaseManagement', action: 'MIGRATE' });
    const data = r.success ? r : await r.json?.() ?? r;
    if (!data.success) { dbSet('db-report', 'Migration start failed.'); return; }
    // poll progress
    const t0 = Date.now();
    const poll = setInterval(async () => {
      try {
        const pr = await NX.api.get('/api/db/manage/progress', { component: 'DatabaseManagement', action: 'PROGRESS' });
        const pdata = pr.success ? pr : await pr.json?.() ?? pr;
        const rep = pdata.report || {};
        if (pdata.done) {
          clearInterval(poll);
          const lines = [
            'STATUS: ' + (rep.status || '--'),
            'VALIDATION: ' + (rep.validation || '--'),
            'TABLES MIGRATED: ' + (rep.tables_migrated || 0),
            'ROWS MIGRATED: ' + (rep.rows_migrated || 0),
            'ERRORS: ' + ((rep.errors || []).join('; ') || 'none'),
          ];
          dbSet('db-report', lines.join('\n'));
          await loadDbStatus();
        } else {
          dbSet('db-progress-pct', Math.round((pdata.progress || 0) * 100) + '%');
          _dbEl('db-progress-bar').style.width = Math.round((pdata.progress || 0) * 100) + '%';
          dbSet('db-progress-detail', (pdata.current_table || '') + ' ' + (pdata.rows_copied || 0) + ' / ' + (pdata.total_rows || 0) + ' rows');
        }
      } catch (err) { console.warn('migration progress poll failed', err); }
    }, 1500);
  } catch (err) { console.warn('start migration failed', err); dbSet('db-report', 'Migration start failed'); }
}

async function validateDbMigration() {
  try {
    const r = await NX.api.get('/api/db/manage/validate', { component: 'DatabaseManagement', action: 'VALIDATE' });
    const data = r.success ? r : await r.json?.() ?? r;
    dbSet('db-report', 'VALIDATION: ' + (data.validation || 'n/a'));
  } catch (err) { console.warn('validate failed', err); dbSet('db-report', 'Validation failed'); }
}

async function runDbBackup() {
  if (!confirm('Backup the SQLite audit database (WAL-consistent snapshot copy)? Continue?')) return;
  try {
    const r = await NX.api.post('/api/db/manage/backup', {}, { component: 'DatabaseManagement', action: 'BACKUP' });
    const data = r.success ? r : await r.json?.() ?? r;
    dbSet('db-report', data.success ? 'Backup created: ' + (data.backup_path || '') : 'Backup failed.');
  } catch (err) { console.warn('backup failed', err); dbSet('db-report', 'Backup failed'); }
}

async function switchDbProvider() {
  const sel = _dbEl('db-provider-select');
  const provider = sel ? sel.value : 'sqlite';
  if (!confirm('Set the ACTIVE provider to ' + provider.toUpperCase() + '? This takes effect on the next application start. Continue?')) return;
  try {
    const r = await NX.api.post('/api/db/manage/provider', { provider }, { component: 'DatabaseManagement', action: 'SWITCH' });
    const data = r.success ? r : await r.json?.() ?? r;
    dbSet('db-test-result', data.success ? 'Provider set to ' + data.provider.toUpperCase() + ' — restart required.' : 'Switch failed.');
    await loadDbStatus();
  } catch (err) { console.warn('switch provider failed', err); dbSet('db-test-result', 'Switch failed'); }
}

// initialize on load
document.addEventListener('DOMContentLoaded', () => { setTimeout(loadDbStatus, 500); });

// ===========================================================================
// DATABASE EXPLORER + SQL CONSOLE + API KEYS (Hermes-DBConsole 2026-08-20)
// SSMS-style management: ALL database files -> tables -> columns -> rows.
// Provider-abstracted through /api/db/console/* — SQLite now, PostgreSQL
// after the active provider switch. Read-only query console.
// ===========================================================================

const dbC = {
  database: null,
  table: null,
  tables: [],
  offset: 0,
  perPage: 100,
  _lastDatabases: [],
};

function dbEl(id) { return document.getElementById(id); }
function dbSet(id, text) { const el = dbEl(id); if (el) el.textContent = text; }
function dbEsc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function dbFmt(v) {
  if (v == null) return '<span class="text-gray-600">NULL</span>';
  const s = String(v);
  if (typeof v === 'object') return '<span class="text-purple-300">' + dbEsc(JSON.stringify(v)) + '</span>';
  if (typeof v === 'number') return '<span class="text-amber-300">' + dbEsc(s) + '</span>';
  return dbEsc(s);
}

async function dbConsoleRefresh() {
  try {
    const r = await NX.api.get('/api/db/console/databases', { component: 'DatabaseManagement', action: 'CONSOLE_DATABASES' });
    const data = r.body || r;
    if (!data.success) throw new Error(data.error || 'failed');
    dbC.tables = [];
    dbC.table = null;
    dbSet('db-console-dbcount', '(' + data.databases.length + ')');
    dbSet('db-console-breadcrumb', 'No database selected');
    renderDbList(data.databases);
    renderTableChips([]);
    renderGrid(null, []);
    dbSet('db-grid-status', 'Rescanned ' + new Date().toLocaleTimeString());
    dbSet('db-table-count', '');
  } catch (err) {
    console.warn('db console refresh failed', err);
    dbSet('db-console-dbcount', 'ERR');
  }
}

function renderDbList(databases) {
  const wrap = dbEl('db-console-dblist');
  if (!wrap) return;
  const filter = (dbEl('db-console-filter')?.value || '').toLowerCase();
  const list = (databases || []).filter(d => !filter || (d.name + ' ' + (d.database || '')).toLowerCase().includes(filter));
  if (!list.length) { wrap.innerHTML = '<div class="text-[10px] text-gray-600">No databases match.</div>'; return; }
  wrap.innerHTML = list.map(d => {
    const size = d.size_bytes != null ? (d.size_bytes / 1024 / 1024).toFixed(1) + ' MB' : '—';
    const dot = d.status === 'CONNECTED' ? 'text-emerald-400' : (d.status === 'MISSING' ? 'text-gray-600' : 'text-red-400');
    const active = dbC.database === d.name ? 'border-accentCyan bg-accentCyan/10' : 'border-transparent hover:border-borderClr';
    return '<button onclick="dbPickDatabase(\'' + d.name + '\')" class="w-full text-left px-2 py-1.5 rounded border ' + active + ' transition">' +
      '<div class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full ' + dot + '"></span>' +
      '<span class="font-bold text-white text-[11px]">' + dbEsc(d.name) + '</span>' +
      '<span class="text-[9px] text-gray-500 font-mono ml-auto">' + dbEsc(d.provider) + '</span></div>' +
      '<div class="text-[9px] text-gray-500 font-mono pl-3.5">' + dbEsc(d.database || d.server || '') + ' · ' + size + '</div>' +
      '</button>';
  }).join('');
}

function dbConsoleFilter() { renderDbList(dbC._lastDatabases || []); }

async function dbConsoleLoad() {
  try {
    const r = await NX.api.get('/api/db/console/databases', { component: 'DatabaseManagement', action: 'CONSOLE_DATABASES' });
    const data = r.body || r;
    if (!data.success) throw new Error(data.error || 'failed');
    dbC._lastDatabases = data.databases || [];
    dbSet('db-console-dbcount', '(' + data.databases.length + ')');
    renderDbList(dbC._lastDatabases);
    await loadDbApiKeys();
  } catch (err) {
    console.warn('db console load failed', err);
  }
}

async function dbPickDatabase(name) {
  dbC.database = name;
  dbC.table = null;
  dbC.offset = 0;
  dbSet('db-console-breadcrumb', name.toUpperCase());
  renderDbList(dbC._lastDatabases);
  dbSet('db-table-count', 'loading tables…');
  try {
    const r = await NX.api.get('/api/db/console/tables', { database: name, component: 'DatabaseManagement', action: 'CONSOLE_TABLES' });
    const data = r.body || r;
    if (!data.success) throw new Error(data.error || 'failed');
    dbC.tables = data.tables || [];
    dbSet('db-table-count', '(' + dbC.tables.length + ' tables)');
    renderTableChips(dbC.tables);
    renderGrid(null, []);
    dbSet('db-rows-offset', '');
    if (dbC.tables.length) dbPickTable(dbC.tables[0].name);
  } catch (err) {
    console.warn('db tables failed', err);
    dbSet('db-table-count', 'ERR');
  }
}

function renderTableChips(tables) {
  const wrap = dbEl('db-console-tablelist');
  if (!wrap) return;
  wrap.innerHTML = (tables || []).map(t => {
    const rows = t.rows != null ? t.rows.toLocaleString() : '?';
    const active = dbC.table === t.name ? 'bg-accentCyan/20 border-accentCyan text-white' : 'bg-darkBg border-borderClr text-gray-300 hover:border-accentCyan';
    return '<button onclick="dbPickTable(\'' + t.name.replace(/'/g, "\\'") + '\')" class="px-2 py-1 rounded border ' + active + ' transition text-[10px] font-mono" title="' + dbEsc(t.name) + '">' +
      dbEsc(t.name) + ' <span class="text-gray-500">(' + rows + ')</span></button>';
  }).join('') || '<span class="text-[10px] text-gray-600">No tables.</span>';
}

async function dbPickTable(name) {
  dbC.table = name;
  dbC.offset = 0;
  renderTableChips(dbC.tables);
  await dbReloadRows();
}

async function dbReloadRows() {
  if (!dbC.table) return;
  dbC.perPage = parseInt(dbEl('db-rows-perpage')?.value || '100', 10);
  dbSet('db-grid-status', 'loading…');
  try {
    const params = { database: dbC.database, table: dbC.table, limit: dbC.perPage, offset: dbC.offset };
    const r = await NX.api.get('/api/db/console/rows', Object.assign(params, { component: 'DatabaseManagement', action: 'CONSOLE_ROWS' }));
    const data = r.body || r;
    if (!data.success) throw new Error(data.error || 'failed');
    renderGrid(data.columns || [], data.rows || []);
    dbSet('db-grid-status', 'rows ' + (data.offset || 0) + '–' + ((data.offset || 0) + (data.rows || []).length) + ' (' + dbC.perPage + '/page)');
    dbSet('db-rows-offset', 'offset ' + (data.offset || 0));
  } catch (err) {
    console.warn('db rows failed', err);
    dbSet('db-grid-status', 'ERR ' + (err.message || ''));
  }
}

function renderGrid(columns, rows) {
  const head = dbEl('db-grid-head');
  const body = dbEl('db-grid-body');
  if (!head || !body) return;
  if (!columns || !columns.length) {
    head.innerHTML = '';
    body.innerHTML = '<tr><td class="px-3 py-4 text-center text-gray-600">Select a database and a table to browse rows.</td></tr>';
    return;
  }
  head.innerHTML = '<tr>' + columns.map(c => '<th class="px-2 py-1.5 text-left text-[10px] font-bold text-cyan-300 bg-panelBg border-b border-borderClr whitespace-nowrap">' + dbEsc(c) + '</th>').join('') + '</tr>';
  if (!rows || !rows.length) {
    body.innerHTML = '<tr><td colspan="' + columns.length + '" class="px-3 py-4 text-center text-gray-600">No rows.</td></tr>';
    return;
  }
  body.innerHTML = rows.map(row =>
    '<tr class="hover:bg-accentCyan/5">' + columns.map(c =>
      '<td class="px-2 py-1 border-b border-borderClr/40 whitespace-nowrap max-w-xs overflow-hidden text-ellipsis">' + dbFmt(row[c]) + '</td>'
    ).join('') + '</tr>'
  ).join('');
}

function dbRowsPrev() {
  if (dbC.offset <= 0) return;
  dbC.offset = Math.max(0, dbC.offset - dbC.perPage);
  dbReloadRows();
}

function dbRowsNext() {
  dbC.offset += dbC.perPage;
  dbReloadRows();
}

// ---- ready-made SQL buttons (SSMS-style quick tasks) ----
function dbSelectedTable() { return dbC.table || (dbC.tables && dbC.tables[0] && dbC.tables[0].name) || ''; }

async function dbQuickSql(kind) {
  const table = dbSelectedTable();
  if (!table) { dbSet('db-sql-result', 'Select a table first (click it in the explorer).'); return; }
  let sql = '';
  if (kind === 'top100') sql = 'SELECT * FROM "' + table + '" ORDER BY rowid LIMIT 100';
  else if (kind === 'count') sql = 'SELECT COUNT(*) AS row_count FROM "' + table + '"';
  else if (kind === 'recent') sql = 'SELECT * FROM "' + table + '" ORDER BY rowid DESC LIMIT 100';
  else if (kind === 'schema') sql = 'SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=\'' + table + '\'';
  else if (kind === 'integrity') sql = 'PRAGMA integrity_check';
  if (dbEl('db-sql-input')) dbEl('db-sql-input').value = sql;
  await dbRunQuery();
}

async function dbRunQuery() {
  const sql = (dbEl('db-sql-input')?.value || '').trim();
  const database = dbC.database || 'audit';
  if (!sql) { dbSet('db-sql-result', 'Enter a SQL query.'); return; }
  dbSet('db-sql-result', 'Running…');
  try {
    const r = await NX.api.post('/api/db/console/query', { database, sql }, { component: 'DatabaseManagement', action: 'CONSOLE_QUERY' });
    const data = r.body || r;
    if (!data.success) { dbSet('db-sql-result', 'Query rejected: ' + (data.error || '')); return; }
    if (!data.columns || !data.columns.length) {
      dbSet('db-sql-result', 'Done — ' + (data.rows_returned || 0) + ' rows returned.');
      return;
    }
    renderGrid(data.columns, data.rows);
    dbSet('db-grid-status', 'SQL · ' + (data.rows || []).length + ' rows' + (data.truncated ? ' (truncated at ' + data.rows.length + ')' : ''));
    dbSet('db-sql-result', 'SQL OK — ' + (data.rows || []).length + ' rows' + (data.truncated ? ' (result limited to ' + (data.rows || []).length + ' rows)' : '') + ' · ' + new Date().toLocaleTimeString());
  } catch (err) {
    console.warn('db query failed', err);
    dbSet('db-sql-result', 'Query failed: ' + (err.message || ''));
  }
}

// ---- API keys ----
async function loadDbApiKeys() {
  try {
    const r = await NX.api.get('/api/db/console/apikeys', { component: 'DatabaseManagement', action: 'CONSOLE_APIKEYS' });
    const data = r.body || r;
    const wrap = dbEl('db-apikey-list');
    if (!wrap) return;
    if (!data.success) { wrap.innerHTML = '<div class="text-red-400">unavailable</div>'; return; }
    const keys = data.apikeys || [];
    wrap.innerHTML = keys.length
      ? keys.map(k => '<div class="flex items-center gap-1">' +
          '<span class="text-emerald-400">●</span>' + dbEsc(k.masked || k.name) +
          '<span class="text-gray-600">(' + (k.set ? 'set' : 'empty') + ')</span></div>').join('')
      : '<div class="text-gray-600">No API keys stored.</div>';
  } catch (err) {
    console.warn('apikeys failed', err);
  }
}

async function dbApiKeySave() {
  const name = (dbEl('db-apikey-name')?.value || '').trim();
  const value = dbEl('db-apikey-value')?.value || '';
  if (!name) { alert('Enter a key name.'); return; }
  try {
    const r = await NX.api.post('/api/db/console/apikey', { name, value }, { component: 'DatabaseManagement', action: 'CONSOLE_APIKEY_SAVE' });
    const data = r.body || r;
    if (!data.success) { alert('Save failed: ' + (data.error || '')); return; }
    if (dbEl('db-apikey-value')) dbEl('db-apikey-value').value = '';
    await loadDbApiKeys();
  } catch (err) { console.warn('apikey save failed', err); alert('Save failed'); }
}

async function dbApiKeyDelete() {
  const name = (dbEl('db-apikey-name')?.value || '').trim();
  if (!name) { alert('Enter the key name to delete.'); return; }
  if (!confirm('Delete API key "' + name + '"? This cannot be undone.')) return;
  try {
    const r = await NX.api.del('/api/db/console/apikey/' + encodeURIComponent(name), { component: 'DatabaseManagement', action: 'CONSOLE_APIKEY_DELETE' });
    const data = r && (r.body || r);
    if (!data || !data.success) { alert('Delete failed: ' + ((data && data.error) || 'unknown')); return; }
    if (dbEl('db-apikey-name')) dbEl('db-apikey-name').value = '';
    await loadDbApiKeys();
  } catch (err) { console.warn('apikey delete failed', err); alert('Delete failed'); }
}

// load explorer + api keys on startup
document.addEventListener('DOMContentLoaded', () => { setTimeout(dbConsoleLoad, 800); });
// News Auto Analysis — prime toggle state on load (no API key needed)
document.addEventListener('DOMContentLoaded', () => { setTimeout(() => { try { refreshNewsAutoState(); refreshNewsToggleState(); } catch(_e){} }, 900); });
