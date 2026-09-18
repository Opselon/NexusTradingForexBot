/**
 * Model Studio UI — Deep Learning & Neural Network Inspection, Prediction & Training.
 * Connects to /api/model-studio/* endpoints for both 50D and 70D models.
 */

let currentStudioDim = 50;
let studioPollTimer = null;

async function loadModelStudioOverview() {
    try {
        const res = await fetch('/api/model-studio/overview');
        if (!res.ok) return;
        const data = await res.json();
        
        document.getElementById('studio-arch').innerText = data.architecture || 'ScalpNet';
        document.getElementById('studio-dim-badge').innerText = (data.effective_dimension || 50) + 'D';
        document.getElementById('studio-source').innerText = data.model_source || 'IN_MEMORY';
        document.getElementById('studio-params').innerText = (data.parameter_count || 0).toLocaleString();
        document.getElementById('studio-weights-hash').innerText = (data.weights_sha256 || '').substring(0, 16);
        document.getElementById('studio-device').innerText = data.device || 'cpu';
        
        const scalerStatus = data.scaler_stats ? data.scaler_stats.status : 'ABSENT';
        const scalerEl = document.getElementById('studio-scaler-status');
        scalerEl.innerText = scalerStatus;
        scalerEl.className = scalerStatus === 'READY' ? 'text-emerald-400 font-bold' : 'text-amber-400 font-bold';
        
        loadModelStudioDatasets();
    } catch (e) {
        console.error('Failed to load studio overview:', e);
    }
}

async function loadModelStudioDatasets() {
    try {
        const res = await fetch('/api/model-studio/datasets');
        if (!res.ok) return;
        const data = await res.json();
        const sel = document.getElementById('studio-dataset-select');
        const posSel = document.getElementById('studio-position-dataset-select');
        if (sel) sel.innerHTML = '';
        if (posSel) posSel.innerHTML = '';
        
        if (!data.datasets || data.datasets.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.innerText = 'No local datasets found (synthetic will be used)';
            if (sel) sel.appendChild(opt);
            if (posSel) posSel.appendChild(opt.cloneNode(true));
            return;
        }
        
        data.datasets.forEach(ds => {
            const opt = document.createElement('option');
            opt.value = ds.path;
            opt.innerText = `${ds.name} (${ds.size_display})`;
            if (sel) sel.appendChild(opt);
            if (posSel) posSel.appendChild(opt.cloneNode(true));
        });
    } catch (e) {
        console.error('Failed to load datasets:', e);
    }
}

function setStudioDimension(dim) {
    currentStudioDim = dim;
    document.getElementById('studio-btn-50d').className = dim === 50 
        ? 'px-4 py-1.5 rounded-lg text-xs font-bold bg-accentCyan text-black transition' 
        : 'px-4 py-1.5 rounded-lg text-xs font-bold bg-surfaceLight/50 text-gray-300 hover:text-white transition';
    document.getElementById('studio-btn-70d').className = dim === 70 
        ? 'px-4 py-1.5 rounded-lg text-xs font-bold bg-accentCyan text-black transition' 
        : 'px-4 py-1.5 rounded-lg text-xs font-bold bg-surfaceLight/50 text-gray-300 hover:text-white transition';
        
    const panel70 = document.getElementById('studio-70d-assembly-panel');
    if (dim === 70) {
        panel70.classList.remove('hidden');
    } else {
        panel70.classList.add('hidden');
    }
}

async function fetchLive70dComponents() {
    const btn = document.getElementById('btn-fetch-70d');
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Fetching...';
    try {
        const res = await fetch('/api/model-studio/fetch-70d');
        const data = await res.json();
        
        document.getElementById('studio-70d-hash').innerText = data.schema_hash ? data.schema_hash.substring(0, 12) : '--';
        const contractBadge = document.getElementById('studio-70d-contract-badge');
        if (data.contract_valid) {
            contractBadge.innerText = 'VALID CONTRACT';
            contractBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
        } else {
            contractBadge.innerText = 'CONTRACT INVALID';
            contractBadge.className = 'px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30';
        }
        
        const listEl = document.getElementById('studio-70d-slots-list');
        listEl.innerHTML = '';
        
        data.slots.forEach(slot => {
            const div = document.createElement('div');
            const famColor = slot.family === 'BASE' ? 'text-sky-400' : slot.family === 'NEWS' ? 'text-amber-400' : 'text-emerald-400';
            div.className = 'flex items-center justify-between text-[11px] font-mono py-1 px-2 rounded bg-darkBg/60 border border-borderClr/40';
            div.innerHTML = `
                <span class="text-gray-500 w-6">${slot.index}</span>
                <span class="font-bold ${famColor} w-20">[${slot.family}]</span>
                <span class="text-gray-300 flex-1 truncate">${slot.name}</span>
                <span class="text-white font-bold">${slot.value.toFixed(4)}</span>
            `;
            listEl.appendChild(div);
        });
    } catch (e) {
        console.error('Failed to fetch 70D components:', e);
    } finally {
        btn.innerHTML = '<i class="fa-solid fa-arrows-rotate mr-1"></i> Fetch Live 70D';
    }
}

async function runStudioInference() {
    const btn = document.getElementById('btn-run-inference');
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Evaluating...';
    
    const noise = parseFloat(document.getElementById('studio-noise-input').value) || 0.0;
    const threshold = parseFloat(document.getElementById('studio-threshold-input').value) || 0.35;
    const useLive = document.getElementById('studio-use-live-toggle').checked;
    
    const payload = {
        dimension: currentStudioDim,
        use_live_features: useLive,
        fetch_live_70d: currentStudioDim === 70,
        perturbation_sigma: noise,
        simulate_policy_threshold: threshold,
        inspect_layers: true,
        compute_saliency: true
    };
    
    try {
        const res = await fetch('/api/model-studio/predict', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        
        // Probabilities
        const probs = data.probabilities;
        document.getElementById('studio-prob-notrade').style.width = (probs.no_trade * 100) + '%';
        document.getElementById('studio-val-notrade').innerText = (probs.no_trade * 100).toFixed(1) + '%';
        
        document.getElementById('studio-prob-buy').style.width = (probs.buy * 100) + '%';
        document.getElementById('studio-val-buy').innerText = (probs.buy * 100).toFixed(1) + '%';
        
        document.getElementById('studio-prob-sell').style.width = (probs.sell * 100) + '%';
        document.getElementById('studio-val-sell').innerText = (probs.sell * 100).toFixed(1) + '%';
        
        // Decision badge
        const dec = data.policy_simulation.final_action;
        const decBadge = document.getElementById('studio-decision-badge');
        decBadge.innerText = dec;
        if (dec === 'BUY_MARKET') {
            decBadge.className = 'text-xl font-black text-emerald-400 bg-emerald-500/10 px-3 py-1 rounded-lg border border-emerald-500/30';
        } else if (dec === 'SELL_MARKET') {
            decBadge.className = 'text-xl font-black text-rose-400 bg-rose-500/10 px-3 py-1 rounded-lg border border-rose-500/30';
        } else {
            decBadge.className = 'text-xl font-black text-amber-400 bg-amber-500/10 px-3 py-1 rounded-lg border border-amber-500/30';
        }
        
        // Confidence & Uncertainty
        document.getElementById('studio-confidence-val').innerText = (data.confidence * 100).toFixed(1) + '%';
        document.getElementById('studio-margin-val').innerText = (data.confidence_margin * 100).toFixed(1) + '%';
        document.getElementById('studio-entropy-val').innerText = data.shannon_entropy_bits.toFixed(4) + ' bits';
        
        // Latency
        document.getElementById('studio-latency-val').innerText = data.latency_ms.total_e2e.toFixed(1) + ' ms';
        
        // Numerical & OOD
        const numVal = data.numerical_validation;
        document.getElementById('studio-num-valid').innerText = numVal.valid ? 'VALID (Sum=1.0)' : 'INVALID';
        document.getElementById('studio-num-valid').className = numVal.valid ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold';
        
        const ood = data.ood_metrics;
        const oodEl = document.getElementById('studio-ood-badge');
        oodEl.innerText = ood.is_out_of_distribution ? `OOD DETECTED (z=${ood.max_z_score})` : `NORMAL (z=${ood.max_z_score})`;
        oodEl.className = ood.is_out_of_distribution ? 'text-rose-400 font-bold' : 'text-emerald-400 font-bold';
        
        // Layer Activations
        const layerBody = document.getElementById('studio-layer-body');
        layerBody.innerHTML = '';
        if (data.layer_inspection) {
            data.layer_inspection.forEach(layer => {
                const tr = document.createElement('tr');
                tr.className = 'border-b border-borderClr/30 hover:bg-surfaceLight/20 text-[11px] font-mono';
                tr.innerHTML = `
                    <td class="py-1 px-2 text-white font-semibold">${layer.layer}</td>
                    <td class="py-1 px-2 text-accentCyan">${layer.type}</td>
                    <td class="py-1 px-2 text-gray-400">${layer.shape.join('x')}</td>
                    <td class="py-1 px-2 text-emerald-300">${layer.l2_norm}</td>
                    <td class="py-1 px-2 text-gray-300">${layer.mean}</td>
                    <td class="py-1 px-2 text-gray-300">${layer.std}</td>
                    <td class="py-1 px-2 text-amber-300">${(layer.zero_fraction * 100).toFixed(1)}%</td>
                `;
                layerBody.appendChild(tr);
            });
        }
        
        // Saliency Drivers
        const posDriversEl = document.getElementById('studio-saliency-pos');
        posDriversEl.innerHTML = '';
        if (data.saliency && data.saliency.top_positive_drivers) {
            data.saliency.top_positive_drivers.forEach(d => {
                const badge = document.createElement('span');
                badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
                badge.innerText = `Feat[${d.index}]: +${d.gradient.toFixed(4)}`;
                posDriversEl.appendChild(badge);
            });
        }
        
        const negDriversEl = document.getElementById('studio-saliency-neg');
        negDriversEl.innerHTML = '';
        if (data.saliency && data.saliency.top_negative_drivers) {
            data.saliency.top_negative_drivers.forEach(d => {
                const badge = document.createElement('span');
                badge.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-rose-500/20 text-rose-400 border border-rose-500/30';
                badge.innerText = `Feat[${d.index}]: ${d.gradient.toFixed(4)}`;
                negDriversEl.appendChild(badge);
            });
        }
    } catch (e) {
        console.error('Inference error:', e);
    } finally {
        btn.innerHTML = '<i class="fa-solid fa-bolt mr-1"></i> Run Neural Inference';
    }
}

async function runStudioStressTest() {
    const btn = document.getElementById('btn-stress-test');
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Running Stress Suite...';
    try {
        const res = await fetch('/api/model-studio/stress-test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dimension: currentStudioDim })
        });
        const data = await res.json();
        
        const tableBody = document.getElementById('studio-stress-body');
        tableBody.innerHTML = '';
        data.results.forEach(r => {
            const tr = document.createElement('tr');
            tr.className = 'border-b border-borderClr/30 text-[11px] font-mono';
            const passStyle = r.passed ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold';
            tr.innerHTML = `
                <td class="py-1 px-2 text-white">${r.test}</td>
                <td class="py-1 px-2 ${passStyle}">${r.passed ? 'PASS' : 'FAIL'}</td>
                <td class="py-1 px-2 text-gray-400">${r.detail}</td>
            `;
            tableBody.appendChild(tr);
        });
        document.getElementById('studio-stress-results-card').classList.remove('hidden');
    } catch (e) {
        console.error('Stress test error:', e);
    } finally {
        btn.innerHTML = '<i class="fa-solid fa-shield-halved mr-1"></i> Run Adversarial Stress Suite';
    }
}

async function runStudioBenchmark() {
    const btn = document.getElementById('btn-benchmark');
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Benchmarking 100 passes...';
    try {
        const res = await fetch('/api/model-studio/benchmark', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dimension: currentStudioDim, iterations: 100 })
        });
        const data = await res.json();
        
        document.getElementById('bench-p50').innerText = data.latency_p50_ms.toFixed(3) + ' ms';
        document.getElementById('bench-p90').innerText = data.latency_p90_ms.toFixed(3) + ' ms';
        document.getElementById('bench-p99').innerText = data.latency_p99_ms.toFixed(3) + ' ms';
        document.getElementById('bench-throughput').innerText = data.throughput_inferences_per_sec.toFixed(0) + ' inf/s';
        document.getElementById('studio-bench-results-card').classList.remove('hidden');
    } catch (e) {
        console.error('Benchmark error:', e);
    } finally {
        btn.innerHTML = '<i class="fa-solid fa-stopwatch mr-1"></i> Benchmark Latency';
    }
}

async function startStudioTraining() {
    const btn = document.getElementById('btn-start-train');
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Dispathing...';
    
    const dataset = document.getElementById('studio-dataset-select').value;
    const epochs = parseInt(document.getElementById('studio-epochs-input').value) || 3;
    const lr = parseFloat(document.getElementById('studio-lr-input').value) || 0.0005;
    const batch = parseInt(document.getElementById('studio-batch-input').value) || 256;
    
    try {
        const res = await fetch('/api/model-studio/train', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                dataset_path: dataset,
                dimension: currentStudioDim,
                epochs: epochs,
                batch_size: batch,
                learning_rate: lr
            })
        });
        const data = await res.json();
        
        document.getElementById('studio-train-progress-box').classList.remove('hidden');
        document.getElementById('train-status-msg').innerText = data.message;
        
        if (studioPollTimer) clearInterval(studioPollTimer);
        studioPollTimer = setInterval(pollStudioTraining, 1500);
    } catch (e) {
        console.error('Training dispatch error:', e);
    } finally {
        btn.innerHTML = '<i class="fa-solid fa-play mr-1"></i> Start Model Training';
    }
}

async function pollStudioTraining() {
    try {
        const res = await fetch('/api/model-studio/train/progress');
        const data = await res.json();
        const p = data.progress;
        
        const pct = p.epochs > 0 ? Math.round((p.epoch / p.epochs) * 100) : 0;
        document.getElementById('train-prog-bar').style.width = pct + '%';
        document.getElementById('train-epoch-text').innerText = `Epoch ${p.epoch} / ${p.epochs} (${pct}%)`;
        document.getElementById('train-loss-text').innerText = `Loss: ${p.loss.toFixed(4)} | Val Loss: ${p.val_loss.toFixed(4)}`;
        
        if (p.status === 'DONE' || p.status === 'FAILED') {
            clearInterval(studioPollTimer);
        }
    } catch (e) {
        console.error('Polling error:', e);
    }
}

async function downloadStudioDataset() {
    const btn = document.getElementById('btn-download-dataset');
    const symbol = document.getElementById('studio-download-symbol').value || 'XAUUSD';
    const timeframe = document.getElementById('studio-download-timeframe').value || 'M5';
    const bars = parseInt(document.getElementById('studio-download-bars').value, 10) || 10000;
    const source = document.getElementById('studio-download-source').value || 'synthetic';
    
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Downloading...';
    try {
        const res = await fetch('/api/model-studio/datasets/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol: symbol,
                timeframe: timeframe,
                bars: bars,
                source: source
            })
        });
        const data = await res.json();
        if (!res.ok) {
            alert('Download failed: ' + (data.detail || JSON.stringify(data)));
            return;
        }
        
        const fb = document.getElementById('studio-download-feedback');
        fb.classList.remove('hidden');
        document.getElementById('download-status-msg').innerText = data.message;
        document.getElementById('download-size-badge').innerText = `${data.rows.toLocaleString()} bars | ${data.size_display}`;
        document.getElementById('download-detail-text').innerText = `Saved: ${data.dataset_path} | Throughput: ${Math.round(data.throughput_bars_sec)} bars/s | Elapsed: ${data.elapsed_sec.toFixed(2)}s`;
        
        await loadModelStudioDatasets();
    } catch (e) {
        console.error('Dataset download error:', e);
        alert('Download error: ' + e.message);
    } finally {
        btn.innerHTML = '<i class="fa-solid fa-download mr-2"></i> Download Dataset';
    }
}

async function inspectStudioFeatures() {
    const btn = document.getElementById('btn-inspect-features');
    const sel = document.getElementById('studio-dataset-select');
    const datasetPath = sel ? sel.value : '';
    
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Inspecting...';
    try {
        const res = await fetch('/api/model-studio/datasets/inspect-features', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                dataset_path: datasetPath,
                dimension: currentStudioDim,
                max_rows: 500
            })
        });
        const data = await res.json();
        if (!res.ok) {
            alert('Feature inspection failed: ' + (data.detail || JSON.stringify(data)));
            return;
        }
        
        document.getElementById('feat-stat-total').innerText = data.total_features;
        document.getElementById('feat-stat-healthy').innerText = data.healthy_features;
        document.getElementById('feat-stat-clamped').innerText = data.clamped_features;
        document.getElementById('feat-stat-scaler').innerText = data.scaler_ready ? 'READY (Z-Score)' : 'NO';
        
        const badgesContainer = document.getElementById('studio-features-summary-badges');
        badgesContainer.innerHTML = `
            <span class="px-2 py-0.5 rounded text-[10px] bg-blue-500/20 text-blue-300 border border-blue-500/30">${data.dimension}D Schema</span>
            <span class="px-2 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">${data.rows_processed} Rows Processed</span>
        `;
        
        const tbody = document.getElementById('studio-features-table-body');
        tbody.innerHTML = '';
        data.features.slice(0, 30).forEach(f => {
            const tr = document.createElement('tr');
            tr.className = 'border-b border-borderClr/30 hover:bg-surfaceLight/10';
            const statusStyle = f.status === 'HEALTHY' ? 'text-emerald-400 font-bold' : 'text-amber-400 font-bold';
            const famStyle = f.family === 'BASE' ? 'text-cyan-400' : f.family === 'NEWS' ? 'text-amber-300' : 'text-purple-400';
            tr.innerHTML = `
                <td class="py-1 px-2 text-textMuted">${f.index}</td>
                <td class="py-1 px-2 ${famStyle}">${f.family}</td>
                <td class="py-1 px-2 text-white font-semibold">${f.name}</td>
                <td class="py-1 px-2 text-right text-gray-300">${f.raw_mean.toFixed(3)}</td>
                <td class="py-1 px-2 text-right text-gray-300">${f.raw_std.toFixed(3)}</td>
                <td class="py-1 px-2 text-right text-emerald-300">${f.normalized_sample.toFixed(3)}</td>
                <td class="py-1 px-2 text-center ${statusStyle}">${f.status}</td>
            `;
            tbody.appendChild(tr);
        });
        
        document.getElementById('studio-features-inspection-container').classList.remove('hidden');
    } catch (e) {
        console.error('Feature inspection error:', e);
        alert('Feature inspection error: ' + e.message);
    } finally {
        btn.innerHTML = '<i class="fa-solid fa-magnifying-glass-chart mr-2"></i> Inspect & Normalize Features (50D / 70D)';
    }
}

async function generatePositionDataset() {
    const btn = document.getElementById('btn-generate-position-dataset');
    const sel = document.getElementById('studio-position-dataset-select');
    const datasetPath = sel ? sel.value : '';
    const maxHolding = parseInt(document.getElementById('studio-position-holding').value, 10) || 30;
    const targetAtr = parseFloat(document.getElementById('studio-position-atr').value) || 2.0;
    const friction = parseFloat(document.getElementById('studio-position-friction').value) || 0.25;
    
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Simulating & Generating...';
    try {
        const res = await fetch('/api/model-studio/position-dataset/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source_dataset_path: datasetPath,
                dimension: currentStudioDim,
                max_holding_bars: maxHolding,
                target_atr_multiplier: targetAtr,
                friction_pips: friction
            })
        });
        const data = await res.json();
        if (!res.ok) {
            alert('Position dataset generation failed: ' + (data.detail || JSON.stringify(data)));
            return;
        }
        
        document.getElementById('pos-stat-samples').innerText = data.total_samples.toLocaleString();
        document.getElementById('pos-stat-trades').innerText = data.simulated_trades.toLocaleString();
        document.getElementById('pos-stat-cont-val').innerText = (data.mean_continuation_value >= 0 ? '+' : '') + data.mean_continuation_value.toFixed(3) + ' R';
        document.getElementById('pos-stat-hold').innerText = data.mean_holding_bars.toFixed(1) + ' bars';
        document.getElementById('pos-checksum-badge').innerText = (data.sha256 || '').substring(0, 16);
        
        const dist = data.actions_distribution || {};
        const labelsContainer = document.getElementById('pos-labels-badge');
        labelsContainer.innerHTML = `
            <span class="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">KEEP: ${dist.KEEP || 0}</span>
            <span class="px-2 py-0.5 rounded bg-rose-500/20 text-rose-400 border border-rose-500/30">CLOSE: ${dist.CLOSE || 0}</span>
            <span class="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">REDUCE: ${dist.REDUCE || 0}</span>
        `;
        
        const pathEl = document.getElementById('pos-path-display');
        pathEl.innerText = `Saved: ${data.dataset_path}`;
        
        document.getElementById('studio-position-results-card').classList.remove('hidden');
        await loadModelStudioDatasets();
    } catch (e) {
        console.error('Position dataset generation error:', e);
        alert('Position dataset error: ' + e.message);
    } finally {
        btn.innerHTML = '<i class="fa-solid fa-gears mr-2"></i> Generate Position Dataset';
    }
}

// Auto-initialize when tab is selected
window.addEventListener('DOMContentLoaded', () => {
    const origSwitch = window.switchTab;
    if (typeof origSwitch === 'function') {
        window.switchTab = function(tabId, btn) {
            origSwitch(tabId, btn);
            if (tabId === 'tab-model-studio') {
                loadModelStudioOverview();
            }
        };
    }
});
