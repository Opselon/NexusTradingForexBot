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
    "trend_strength",               // feat_46
    "consolidation_ratio",          // feat_47
    "htf_h1_atr_ratio",             // feat_48
    "htf_h4_atr_ratio",             // feat_49
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

// On Startup
window.addEventListener('load', () => {
    initApp();
    startSSE();
    loadConfiguration();
    setInterval(updateHeartbeats, 5000);
});

function initApp() {
    console.log("Nexus Scalp Engine Front-End Booted.");

    let dummyFeatures = FEATURE_NAMES_JS.map((name, idx) => ({
        index: idx,
        name: name,
        value: 0.0
    }));
    lastFeatures = dummyFeatures;
    updateFeaturesGrid(dummyFeatures);

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
    fetch('/api/chart/history')
        .then(res => res.json())
        .then(payload => {
            console.log("OHLC Chart History successfully bootstrapped from API:", payload);
            if (payload.bars && payload.bars.length > 0) {
                candleData = payload.bars;
            }
            if (payload.visual_overlays) {
                visualOverlays = payload.visual_overlays;
            }
            // Auto fit and paint the candles immediately
            autoFitChart();
            drawChart();
        })
        .catch(err => {
            console.warn("Could not bootstrap initial chart history", err);
            drawChart();
        });
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
}

// Server-Sent Events (SSE) Stream Subscriber
function startSSE() {
    if (eventSource) {
        eventSource.close();
    }

    // Connect to server Sent Events streaming endpoint
    eventSource = new EventSource('/api/ticks/stream');

    eventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleIncomingLiveTick(data);
        } catch (err) {
            console.error("Failed to parse SSE payload", err);
        }
    };

    eventSource.onerror = (err) => {
        console.warn("SSE connection interrupted. Reconnecting...", err);
        document.getElementById('system-status-badge').innerHTML = `
            <span class="w-1.5 h-1.5 rounded-full bg-rose-500 mr-1.5"></span>
            DISCONNECTED
        `;
        document.getElementById('system-status-badge').className = "ml-3 text-xs px-2.5 py-0.5 rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/30 flex items-center";
    };
}

// Handle Incoming Live Market Tick & State Updates
function handleIncomingLiveTick(payload) {
    if (uiPaused) return; // Prevent updates if user paused the visualizer

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
        document.getElementById('btn-toggle-engine').innerHTML = `<i class="fa-solid fa-circle-play"></i> <span>Start Bot</span>`;
        document.getElementById('btn-toggle-engine').className = "flex-1 bg-emerald-500 hover:bg-emerald-600 text-white font-bold py-1.5 px-3 rounded text-xs transition shadow-md shadow-emerald-500/10 flex items-center justify-center space-x-1";
    }

    // Top Header Stats
    document.getElementById('quick-symbol').textContent = payload.symbol;
    document.getElementById('quick-bid-ask').textContent = `${payload.bid.toFixed(2)} / ${payload.ask.toFixed(2)}`;
    document.getElementById('quick-regime').textContent = payload.regime;
    document.getElementById('execution-mode-selector').value = payload.execution_mode;

    // Monitoring Panel
    document.getElementById('monitor-bid').textContent = payload.bid.toFixed(2);
    document.getElementById('monitor-ask').textContent = payload.ask.toFixed(2);
    document.getElementById('monitor-spread').textContent = `${payload.spread} pts`;

    // Display raw realized volatility or ATR depending on source
    const volVal = payload.atr;
    document.getElementById('monitor-atr').textContent = (volVal < 0.1) ? volVal.toFixed(6) : volVal.toFixed(2);
    document.getElementById('monitor-regime').textContent = payload.regime;

    // AI Prediction Card
    document.getElementById('ai-decision-badge').textContent = payload.ai_decision;
    document.getElementById('ai-confidence').textContent = `Conf: ${(payload.ai_confidence * 100).toFixed(2)}%`;
    if (payload.ai_reason) {
        document.getElementById('ai-reason-text').textContent = `"${payload.ai_reason}"`;
    }

    // Softmax probabilities
    const pNoTrade = (payload.probs.no_trade * 100).toFixed(1);
    const pBuy = (payload.probs.buy * 100).toFixed(1);
    const pSell = (payload.probs.sell * 100).toFixed(1);

    document.getElementById('prob-no-trade').textContent = `${pNoTrade}%`;
    document.getElementById('prob-no-trade-bar').style.width = `${pNoTrade}%`;
    document.getElementById('prob-buy').textContent = `${pBuy}%`;
    document.getElementById('prob-buy-bar').style.width = `${pBuy}%`;
    document.getElementById('prob-sell').textContent = `${pSell}%`;
    document.getElementById('prob-sell-bar').style.width = `${pSell}%`;

    // Account Section
    document.getElementById('acc-balance').textContent = `$${payload.account.balance.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
    document.getElementById('acc-equity').textContent = `$${payload.account.equity.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
    document.getElementById('acc-floating').textContent = `${payload.account.floating >= 0 ? '+' : ''}$${payload.account.floating.toFixed(2)}`;
    document.getElementById('acc-floating').className = `text-lg font-black font-mono ${payload.account.floating >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;
    document.getElementById('acc-drawdown').textContent = `${payload.account.drawdown.toFixed(2)}%`;
    document.getElementById('acc-winrate').textContent = `${payload.account.win_rate.toFixed(1)}%`;

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
    }

    // Populate active positions table
    populatePositionsTable(payload.positions);

    // Populate AI Analysis Category
    updateFeaturesGrid(payload.features);

    // Populate Prediction Outcomes
    if (payload.predictions && payload.predictions.length > 0) {
        predictions = payload.predictions;
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

    // Handle retina display scaling
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

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

            let color = 'rgba(0, 230, 118, 0.25)'; // Green Box for Bullish OB/FVG
            let label = rect.type;

            if (rect.type === 'BEARISH_ORDER_BLOCK' || rect.type === 'BEARISH_FVG') {
                color = 'rgba(255, 23, 68, 0.25)'; // Red Box for Bearish OB/FVG
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

            // Display zone type & AI Confidence % inside the box
            ctx.fillStyle = 'rgba(226, 232, 240, 0.9)';
            ctx.font = 'bold 9px sans-serif';
            ctx.textAlign = 'left';
            const textY = Math.min(Math.max(yHigh + 12, 12), h - 22);
            ctx.fillText(`${label} (AI Zone Confidence: ${(rect.ai_confidence * 100).toFixed(0)}%)`, xStart + 8, textY);
        });
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
                <span>Time: ${new Date(c.time).toLocaleTimeString()}</span>
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
        const res = await fetch('/api/positions/modify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticket, stop_loss: sl, take_profit: tp })
        });
        const result = await res.json();
        if (result.success) {
            console.log("SL/TP bracket modification successfully executed.");
            closeModifyModal();
        } else {
            alert(`Execution failed: ${result.message}`);
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
        const res = await fetch('/api/positions/close', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticket })
        });
        const result = await res.json();
        if (result.success) {
            console.log(`Live Position #${ticket} closed successfully.`);
        } else {
            alert(`Execution failed: ${result.message}`);
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

// Dynamic Grid populate for 50D AI features
function updateFeaturesGrid(features) {
    if (features) {
        lastFeatures = features;
    } else {
        features = lastFeatures;
    }

    const grid = document.getElementById('features-grid');
    if (!grid || !features || features.length === 0) return;

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
        const colorClass = val >= 1.0 ? 'text-emerald-400' : (val <= -1.0 ? 'text-rose-400' : 'text-accentCyan');
        return `
            <div class="bg-darkBg/40 border border-borderClr/60 p-3 rounded-lg flex flex-col justify-between hover:border-borderClr transition shadow-sm">
                <span class="text-[10px] text-textMuted font-bold uppercase truncate tracking-wide">${feat.name}</span>
                <div class="flex items-baseline justify-between mt-1.5">
                    <span class="text-sm font-mono font-black ${colorClass}">${val.toFixed(4)}</span>
                    <span class="text-[9px] text-textMuted font-semibold">Dim ${feat.index}</span>
                </div>
            </div>
        `;
    }).join('');
}

// Update Past Signal Outcomes & Accuracy Tracking
function updatePredictionsTable() {
    const tbody = document.getElementById('prediction-vs-movement-table');
    if (!tbody) return;

    if (predictions.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" class="py-4 text-center text-textMuted italic font-sans">No recent AI prediction outcomes evaluated yet.</td>
            </tr>
        `;
        return;
    }

    let trueCount = 0;
    let falseCount = 0;

    tbody.innerHTML = predictions.map(p => {
        const isTrue = p.outcome === 'TRUE_POSITIVE' || p.outcome === 'TRUE_NEGATIVE';
        if (isTrue) trueCount++; else falseCount++;

        return `
            <tr class="hover:bg-darkBg/10">
                <td class="py-2 text-textMuted">${p.time}</td>
                <td class="py-2 text-white font-bold">${p.action}</td>
                <td class="py-2 text-accentCyan">${(p.confidence * 100).toFixed(1)}%</td>
                <td class="py-2 ${p.actual_delta >= 0 ? 'text-emerald-400' : 'text-rose-400'}">${p.actual_delta >= 0 ? '+' : ''}${p.actual_delta.toFixed(2)}</td>
                <td class="py-2 text-right">
                    <span class="px-1.5 py-0.5 rounded text-[10px] font-extrabold ${isTrue ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}">
                        ${p.outcome}
                    </span>
                </td>
            </tr>
        `;
    }).join('');

    // Update Accuracy Statistics Box
    document.getElementById('acc-true-signals').textContent = trueCount;
    document.getElementById('acc-false-signals').textContent = falseCount;
    const total = trueCount + falseCount;
    const accPct = total > 0 ? (trueCount / total * 100) : 0;
    document.getElementById('acc-bar').style.width = `${accPct}%`;
}

// Simulate Interactive Tick Injection
async function injectSimTick(type) {
    try {
        const res = await fetch('/api/simulation/tick', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type })
        });
        const result = await res.json();
        console.log(`Simulation tick of type ${type} successfully dispatched to engine pipeline.`);
        document.getElementById('sim-status').textContent = "Dispatched";
        setTimeout(() => document.getElementById('sim-status').textContent = "Ready", 1000);
    } catch (err) {
        console.error("Simulation dispatch failure", err);
    }
}

// Toggle Historical Replay status
async function toggleReplay() {
    const isReplaying = document.getElementById('btn-replay-play').textContent.includes("Stop");
    const speed = parseInt(document.getElementById('replay-speed').value);

    try {
        const res = await fetch('/api/replay/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active: !isReplaying, speed })
        });
        const result = await res.json();
        if (result.success) {
            if (!isReplaying) {
                document.getElementById('btn-replay-play').innerHTML = `<i class="fa-solid fa-stop"></i> <span>Stop Replay</span>`;
                document.getElementById('btn-replay-play').className = "bg-rose-500 hover:bg-rose-600 text-white font-bold py-1.5 px-4 rounded text-xs transition flex items-center justify-center space-x-1";
            } else {
                document.getElementById('btn-replay-play').innerHTML = `<i class="fa-solid fa-play"></i> <span>Start Replay</span>`;
                document.getElementById('btn-replay-play').className = "bg-accentCyan hover:bg-cyan-500 text-black font-bold py-1.5 px-4 rounded text-xs transition flex items-center justify-center space-x-1";
            }
        }
    } catch (err) {
        console.error("Replay API call failed", err);
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

        document.getElementById('cfg-telegram-enabled').checked = selectedConfig.telegram.enabled;
        document.getElementById('cfg-telegram-token').value = selectedConfig.telegram.bot_token;
        document.getElementById('cfg-telegram-admin').value = selectedConfig.telegram.admin_id;

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
        const res = await fetch('/api/algo/config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updated)
        });
        const result = await res.json();
        if (result.success) {
            alert("Dynamic Algorithm thresholds successfully updated & hot-swapped!");
        } else {
            alert(`Failed to save algorithm thresholds: ${result.message}`);
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
        const res = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updated)
        });
        const result = await res.json();
        if (result.success) {
            alert("Configuration successfully saved and dynamically hot-reloaded into engine!");
        } else {
            alert(`Failed to save: ${result.message}`);
        }
    } catch (err) {
        console.error("Failed to save config", err);
    }
}

// Control switch: start/stop bot thread
async function toggleEngineRunning() {
    const isStopping = document.getElementById('btn-toggle-engine').textContent.includes("Stop");

    try {
        const res = await fetch('/api/engine/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active: !isStopping })
        });
        const result = await res.json();
        if (result.success) {
            console.log("Engine running state successfully toggled.");
        }
    } catch (err) {
        console.error("Failed to toggle engine state", err);
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
        const res = await fetch('/api/rules/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                rule_name: ruleId,
                is_enabled: isEnabled,
                parameters: null
            })
        });
        const result = await res.json();
        if (result.success) {
            console.log(`Rule ${ruleId} has been successfully toggled to ${isEnabled}.`);
        } else {
            alert("Failed to toggle rule state.");
        }
    } catch (err) {
        console.error("Failed to toggle rule", err);
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
