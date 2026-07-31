// app.js
// Front-End Engine for Nexus Scalp Engine (NSE) Control Center

let eventSource = null;
let currentTab = 'tab-monitoring';
let currentFeatureCategory = 'volatility';
let candleData = []; // [{time, open, high, low, close, volume, is_complete}]
let predictions = []; // [{time, action, confidence, actual_delta, outcome}]
let selectedConfig = {};

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

    // Render initial empty candle chart
    drawChart();
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
function selectFeatureCategory(category) {
    document.querySelectorAll('.category-btn').forEach(btn => btn.classList.remove('active'));
    event.currentTarget.classList.add('active');
    currentFeatureCategory = category;

    const titles = {
        'volatility': 'Volatility & Microstructure Features (Log Scale)',
        'candlestick': 'Candlestick Anatomy & Patterns',
        'patterns': 'Structure & Swing Patterns (Distance metrics)',
        'sessions': 'Market Sessions Time Lags',
        'ict': 'ICT Smart Money Concepts (FVG / Order Block)',
        'ichimoku': 'Ichimoku Kinko Hyo (Cloud conformance)'
    };
    document.getElementById('feature-category-title').textContent = titles[category];
    // This will force re-render from cached/incoming values
}

// Dynamic Grid populate for 40D AI features
function updateFeaturesGrid(features) {
    const grid = document.getElementById('features-grid');
    if (!grid) return;

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
    } else {
        activeList = features.slice(34, 40);
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
