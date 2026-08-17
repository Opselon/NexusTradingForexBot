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
    if (tabId === 'tab-rules') {
        loadRules();
    }
    if (tabId === 'tab-ai-analysis') {
        loadIntelligenceSummary();
    }
    if (tabId === 'tab-research') {
        loadResearchSummary();
    }
    if (tabId === 'tab-news') {
        loadNewsState();
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
        anomalyToggle.addEventListener('change', () => renderDebugFeatures(debugFeatureCache));
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
        loadDebugFeatures(),
        loadDebugIpcTelemetry()
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
    if (state === 'active') {
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

    // Retain the latest payload for AI VIEW per-candle snapshots.
    lastAiSnapshot = payload;

    // Update Connection State badge
    const badge = document.getElementById('system-status-badge');
    if (payload.engine_running) {
        badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse mr-1.5"></span> ACTIVE`;
        badge.className = "ml-3 text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 flex items-center";
        document.getElementById('btn-toggle-engine').innerHTML = `<i class="fa-solid fa-circle-stop"></i> <span>Stop Bot</span>`;
        document.getElementById('btn-toggle-engine').className = "flex-1 bg-rose-500 hover:bg-rose-600 text-white font-bold py-1.5 px-3 rounded text-xs transition shadow-md shadow-rose-500/10 flex items-center justify-center space-x-1";
    } else {
        badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-amber-400 mr-1.5"></span> PAUSED`;
        badge.className = "ml-3 text-xs px-2.5 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/30 flex items-center";
        const btn = document.getElementById('btn-toggle-engine');
        if (btn) {
            btn.innerHTML = `<i class="fa-solid fa-circle-play"></i> <span>Start Bot</span>`;
            btn.className = "flex-1 bg-emerald-500 hover:bg-emerald-600 text-white font-bold py-1.5 px-3 rounded text-xs transition shadow-md shadow-emerald-500/10 flex items-center justify-center space-x-1";
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
        setTxt('quick-bid-ask', `${payload.bid.toFixed(2)} / ${payload.ask.toFixed(2)}`);
    }
    if (payload.regime != null) setTxt('quick-regime', payload.regime);
    if (payload.execution_mode != null) {
        const sel = document.getElementById('execution-mode-selector');
        if (sel) sel.value = payload.execution_mode;
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
    const lastUpdate = document.getElementById('header-last-update');
    if (lastUpdate) {
        lastUpdate.textContent = lastSnapshotAt
            ? Math.round((Date.now() - lastSnapshotAt) / 1000) + 's ago'
            : '—';
    }

    // Monitoring Panel
    if (payload.bid != null) setTxt('monitor-bid', payload.bid.toFixed(2));
    if (payload.ask != null) setTxt('monitor-ask', payload.ask.toFixed(2));
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
        // Inference latency (real measured ms when available)
        if (model.latency_ms != null) {
            setTxt('model-inference-time', `${Number(model.latency_ms).toFixed(2)}ms`);
        }
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
        grid.innerHTML = '<div class="col-span-3 text-center text-textMuted italic py-8 text-xs">Awaiting live 50D feature stream from engine…</div>';
        return;
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
            document.getElementById('cfg-telegram-token').value = selectedConfig.telegram.bot_token || '';
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
            alert("Dynamic Algorithm thresholds successfully updated & hot-swapped!");
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
            bot_token: document.getElementById('cfg-telegram-token').value,
            admin_id: document.getElementById('cfg-telegram-admin').value
        },
        mt5: selectedConfig.mt5
    };

    try {
        const result = await NX.api.post('/api/config', updated, { component: 'Config', action: 'SAVE_CONFIG' });
        if (result.ok && result.body.success) {
            alert("Configuration successfully saved and dynamically hot-reloaded into engine!");
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
                bot_token: document.getElementById('cfg-telegram-token').value,
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
            if (statusEl) statusEl.textContent = '\u2705 Test message sent!';
        } else if (result.status === 0 || (result.error && result.error.code === 'NETWORK_ERROR')) {
            if (statusEl) statusEl.textContent = '\u274c Server unreachable \u2014 engine not running?';
        } else {
            if (statusEl) statusEl.textContent = '\u274c ' + NX.api.msg(result, 'send failed');
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
    } catch (err) {}
}

// Fetch all rules from database and render the UI panel dynamically
async function loadRules() {
    try {
        const res = await fetch('/api/rules');
        const rules = await res.json();

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
        console.error("Failed to load rules", err);
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

async function loadAccountPeriod(kind, btn) {
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
            const lifecycle = s.lifecycle_state || 'UNKNOWN';
            const lifeColor = lifecycle === 'ACTIVE' ? 'text-emerald-400' : (lifecycle === 'RETIRED' || lifecycle === 'QUARANTINED') ? 'text-rose-400' : 'text-amber-400';
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

async function loadResearchRegistry() {
    try {
        const res = await fetch('/api/research/registry?limit=20');
        if (!res.ok) return;
        const body = await res.json();
        if (!body.available) return;
        const box = document.getElementById('research-registry');
        const rows = body.registry || [];
        if (!rows.length) {
            box.innerHTML = '<div class="text-textMuted italic">No strategies in the registry yet. Run discovery first.</div>';
            return;
        }
        box.innerHTML = rows.map(r => {
            const sc = r.score ? (JSON.parse(r.score).final_score ?? '--') : '--';
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
            '<div>score: ' + esc(r.score?.final_score ?? '--') + ' · verdict: ' + esc(r.score?.verdict ?? '--') + '</div>';
        loadResearchSummary();
    } catch (e) {
        console.warn('research validate failed', e);
    }
}

// PHASE 09: TRADE INTELLIGENCE BRAIN (lifecycle / autopsy / behavior / evolution)
// =============================================================================

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
                    '<div class="flex justify-between"><span class="text-accentCyan font-bold">' + esc(b.behavior_type || b.behavior || 'UNKNOWN') +
                    '</span><span class="text-textMuted">' + esc(b.symbol || '') + ' · ' + esc(String(b.detected_at || '').substring(0, 16)) + '</span></div>' +
                    '<div class="text-textMuted">' + esc(b.summary || b.detail || '') + '</div>' +
                '</div>'
            ).join('');
        }
    } catch (e) {
        console.warn('behavior load failed', e);
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
    setTimeout(initAccountIntelligence, 1200);
    setTimeout(loadIntelligenceSummary, 1500);
    // Periodically refresh account intelligence (broker history sync fills in
    // real values after engine start; charts need fresh points to render).
    setInterval(() => {
        loadAccountPerformance();
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

async function loadNewsState() {
    try {
        const res = await fetch('/api/news/state');
        if (!res.ok) return;
        const body = await res.json();
        if (!body.available) {
            document.getElementById('news-state-value').textContent = 'OFF';
            document.getElementById('news-state-badge').textContent = 'OFF';
            return;
        }
        const state = body.state || 'NORMAL';
        document.getElementById('news-state-value').textContent = state;
        document.getElementById('news-state-badge').textContent = state;
        const badge = document.getElementById('news-nav-state');
        if (badge) { badge.textContent = state; badge.className = 'ml-auto text-[9px] font-black px-1.5 py-0.5 rounded border ' + stateColor(state); }
        document.getElementById('news-xauusd-rel').textContent = (body.xauusd_relevance * 100).toFixed(0) + '%';
        document.getElementById('news-bull').textContent = (body.bullish_score * 100).toFixed(0) + '%';
        document.getElementById('news-bear').textContent = (body.bearish_score * 100).toFixed(0) + '%';
        document.getElementById('news-events').textContent = body.active_event_count ?? 0;
        loadNewsFeed();
    } catch (e) {
        console.warn('news state failed', e);
    }
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

async function loadNewsFeed() {
    try {
        const res = await fetch('/api/news?limit=20');
        if (!res.ok) return;
        const body = await res.json();
        const feed = document.getElementById('news-feed');
        if (!body.available || !body.articles || body.articles.length === 0) {
            feed.innerHTML = '<div class="text-textMuted italic">No news events yet. Click "Fetch News" or wait for the worker.</div>';
            return;
        }
        feed.innerHTML = body.articles.map(a => {
            const dir = a.analysis ? (a.analysis.direction || 'NEUTRAL') : 'NEUTRAL';
            const dirColor = dir === 'BULLISH' ? 'text-accentGreen' : dir === 'BEARISH' ? 'text-accentRed' : 'text-slate-400';
            const imp = Math.round((a.importance_score || 0) * 100);
            const rel = a.analysis ? Math.round((a.analysis.relevance_to_xauusd || 0) * 100) + '%' : '--';
            return '<div class="bg-darkBg/40 border border-borderClr/40 rounded p-2.5">' +
                '<div class="flex justify-between items-center gap-2">' +
                '<span class="font-bold text-white truncate">' + esc(a.title) + '</span>' +
                '<span class="text-[9px] ' + dirColor + ' font-black shrink-0">' + esc(dir) + '</span></div>' +
                '<div class="flex items-center gap-3 mt-1 text-[10px] text-textMuted">' +
                '<span>' + esc(a.source_name || a.source_id || '') + '</span>' +
                '<span>imp ' + imp + '</span>' +
                '<span>XAU ' + rel + '</span>' +
                '<span>' + esc(String(a.published_at || '').slice(0, 16)) + '</span></div>' +
                '<div class="flex gap-2 mt-1">' +
                '<button onclick="analyzeNewsWithAI(\'' + a.article_id + '\')" class="text-[9px] bg-accentCyan/10 text-accentCyan border border-accentCyan/30 rounded px-2 py-0.5 hover:bg-accentCyan/20">Analyze with AI</button>' +
                '</div></div>';
        }).join('');
    } catch (e) {
        console.warn('news feed failed', e);
    }
}

async function analyzeNewsWithAI(articleId) {
    try {
        const res = await NX.api.post('/api/news/analyze/' + articleId, {}, { component: 'News', action: 'ANALYZE' });
        const body = res.ok ? res.body : { available: false, error: res.error };
        alert(body && body.ok ? 'Analysis queued (status: ' + (body.status || 'QUEUED') + ')' : 'Analysis request failed');
        setTimeout(loadNewsFeed, 1500);
    } catch (e) {
        console.warn('news analyze failed', e);
    }
}

async function triggerNewsRefresh() {
    try {
        const res = await NX.api.post('/api/news/refresh', {}, { component: 'News', action: 'REFRESH' });
        const body = res.ok ? res.body : { available: false, error: res.error };
        alert(body && body.available ? 'News fetch complete: ' + JSON.stringify(body.ingested || {}) : 'News engine unavailable');
        loadNewsState();
    } catch (e) {
        console.warn('news refresh failed', e);
    }
}

// auto-load news state on tab open
const __newsTabObserver = new MutationObserver(() => {
    const tab = document.getElementById('tab-news');
    if (tab && !tab.classList.contains('hidden') && !window.__newsIntelLoaded) {
        window.__newsIntelLoaded = true;
        loadNewsState();
    }
});
__newsTabObserver.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
