
// =============================================================================
// Layer-2 ML: Position Decision Adviser (keep/close/reduce decide-system hook)
// =============================================================================

const ADVISER_BASE = '/api/position-adviser';

/** Parse a comma-separated number list; returns [] on any malformed entry. */
function adviserParseNums(raw, cast) {
    return String(raw || '')
        .split(',')
        .map(function (p) { return p.trim(); })
        .filter(function (p) { return p.length > 0; })
        .map(function (p) { return cast(p); });
}

function adviserShowError(msg) {
    const el = document.getElementById('adviser-error');
    if (!el) return;
    el.textContent = String(msg || 'unknown error');
    el.classList.remove('hidden');
}

function adviserHideError() {
    const el = document.getElementById('adviser-error');
    if (el) el.classList.add('hidden');
}

/** Refresh the adviser status counters + activation badge + model list. */
async function refreshAdviserStatus() {
    try {
        const res = await fetch(ADVISER_BASE + '/status');
        if (!res.ok) return;
        const s = await res.json();
        const badge = document.getElementById('adviser-activation-badge');
        if (badge) {
            badge.textContent = s.activation || 'DISABLED';
            badge.className = 'text-xs font-bold uppercase ' + (
                s.activation === 'LIVE' ? 'text-emerald-400' :
                s.activation === 'PAPER' ? 'text-amber-400' : 'text-slate-400');
        }
        const mdl = document.getElementById('adviser-stat-model');
        if (mdl) mdl.textContent = s.model_id || 'NONE';
        const setStat = function (id, v) {
            const el = document.getElementById(id);
            if (el) el.textContent = String(v);
        };
        setStat('adviser-stat-applied', s.applied_count != null ? s.applied_count : 0);
        setStat('adviser-stat-evaluated', s.evaluated_count != null ? s.evaluated_count : 0);
        setStat('adviser-stat-refused', s.refused_count != null ? s.refused_count : 0);
        if (s.last_error) adviserShowError('last adviser error: ' + s.last_error);
    } catch (e) {
        console.error('adviser status failed:', e);
    }
}

/** Populate the position-dataset selector (M1/M5 generator output). */
async function loadAdviserDatasets() {
    try {
        const res = await fetch(ADVISER_BASE + '/datasets');
        if (!res.ok) return;
        const data = await res.json();
        const sel = document.getElementById('adviser-dataset-select');
        if (!sel) return;
        sel.innerHTML = '';
        const list = data.datasets || [];
        if (list.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.innerText = 'No position datasets — generate one above';
            sel.appendChild(opt);
            return;
        }
        list.forEach(function (d) {
            const opt = document.createElement('option');
            opt.value = d.path;
            opt.innerText = d.name + (d.timeframe ? ' (' + d.timeframe + ')' : '');
            sel.appendChild(opt);
        });
    } catch (e) {
        console.error('adviser datasets failed:', e);
    }
}

/** List trained adviser checkpoints with their honest OOS metrics. */
async function loadAdviserModels() {
    try {
        const res = await fetch(ADVISER_BASE + '/models');
        if (!res.ok) return;
        const data = await res.json();
        const box = document.getElementById('adviser-models-list');
        if (!box) return;
        box.innerHTML = '';
        const models = data.models || [];
        if (models.length === 0) {
            box.innerHTML = '<div class="text-slate-600">No adviser checkpoints yet — train or auto-tune above.</div>';
            return;
        }
        models.forEach(function (m) {
            const active = m.model_id === data.active_adviser_id;
            const acc = m.oos_accuracy != null ? m.oos_accuracy.toFixed(4) : '--';
            const div = document.createElement('div');
            div.className = 'flex flex-wrap items-center gap-2 rounded border border-borderClr px-2.5 py-1.5 bg-darkBg/40';
            div.innerHTML =
                '<span class="font-mono text-white">' + studioAttr(m.model_id) + '</span>' +
                (active ? '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-cyan-500/20 text-cyan-300">LOADED</span>' : '') +
                '<span class="text-slate-500">OOS acc ' + acc + '</span>' +
                '<span class="text-slate-600">train/oos ' + (m.train_rows != null ? m.train_rows : '--') + '/' + (m.oos_rows != null ? m.oos_rows : '--') + '</span>' +
                '<button class="ml-auto px-2 py-1 rounded border border-borderClr text-slate-300 hover:border-cyan-600 font-bold"' +
                ' onclick="loadAdviserModel(\'' + studioAttr(m.model_id) + '\')">' +
                (m.has_scaler ? 'Load' : 'No scaler') + '</button>';
            box.appendChild(div);
        });
    } catch (e) {
        console.error('adviser models failed:', e);
    }
}

/** Load a trained checkpoint into live memory (activation stays DISABLED). */
async function loadAdviserModel(modelId) {
    try {
        const mres = await fetch(ADVISER_BASE + '/models');
        const m = (mres.ok ? (await mres.json()).models || [] : []).find(function (x) { return x.model_id === modelId; });
        if (!m) { adviserShowError('model not found: ' + modelId); return; }
        const res = await fetch(ADVISER_BASE + '/load', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ weights_path: m.weights_path, scaler_path: m.scaler_path, model_id: m.model_id })
        });
        const data = await res.json();
        if (!res.ok) { adviserShowError(data.detail || JSON.stringify(data)); return; }
        adviserHideError();
        await refreshAdviserStatus();
        await loadAdviserModels();
        if (window.NX && window.NX.toast) window.NX.toast(data.message || 'adviser loaded', 'ok');
    } catch (e) {
        adviserShowError(e.message);
    }
}

/** Train by hand on the selected position dataset. */
async function trainPositionAdviser() {
    const btn = document.getElementById('btn-adviser-train');
    const sel = document.getElementById('adviser-dataset-select');
    const msg = document.getElementById('adviser-train-msg');
    const ds = sel ? sel.value : '';
    const epochs = parseInt(document.getElementById('adviser-train-epochs').value, 10) || 12;
    if (!ds) { adviserShowError('select a position dataset first'); return; }
    btn.disabled = true;
    if (msg) msg.textContent = 'training…';
    try {
        const res = await fetch(ADVISER_BASE + '/train', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dataset_path: ds, epochs: epochs })
        });
        const data = await res.json();
        if (!res.ok) { adviserShowError(data.detail || JSON.stringify(data)); if (msg) msg.textContent = ''; return; }
        adviserHideError();
        if (msg) msg.textContent = data.message || 'training complete';
        await loadAdviserModels();
    } catch (e) {
        adviserShowError(e.message);
        if (msg) msg.textContent = '';
    } finally {
        btn.disabled = false;
    }
}

/** AUTO MODE: bounded grid sweep; keeps the lowest OOS-loss model and loads it. */
async function autoTunePositionAdviser() {
    const btn = document.getElementById('btn-adviser-autotune');
    const sel = document.getElementById('adviser-dataset-select');
    const msg = document.getElementById('adviser-tune-msg');
    const ds = sel ? sel.value : '';
    if (!ds) { adviserShowError('select a position dataset first'); return; }

    const epochs = parseInt(document.getElementById('adviser-tune-epochs').value, 10) || 12;
    const maxTrials = parseInt(document.getElementById('adviser-tune-max').value, 10) || 6;
    const lrs = adviserParseNums(document.getElementById('adviser-tune-lrs').value, Number);
    const bss = adviserParseNums(document.getElementById('adviser-tune-bs').value, function (v) { return parseInt(v, 10); });
    const seedsRaw = document.getElementById('adviser-tune-seeds');
    const seeds = adviserParseNums(seedsRaw ? seedsRaw.value : '42,1337', function (v) { return parseInt(v, 10); });
    if (lrs.length === 0 || bss.length === 0 || seeds.length === 0) {
        adviserShowError('auto-tune needs at least one learning rate, batch size and seed');
        return;
    }

    btn.disabled = true;
    if (msg) msg.textContent = 'sweeping ' + maxTrials + ' trials… (this trains real models — wait)';
    try {
        const res = await fetch(ADVISER_BASE + '/auto-tune', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                dataset_path: ds, epochs: epochs, max_trials: maxTrials,
                learning_rates: lrs, batch_sizes: bss, seeds: seeds, auto_load: true
            })
        });
        const data = await res.json();
        if (!res.ok) { adviserShowError(data.detail || JSON.stringify(data)); if (msg) msg.textContent = ''; return; }
        adviserHideError();
        const baseline = data.majority_baseline_accuracy;
        const verdict = baseline != null
            ? (data.beats_majority_baseline ? 'beats' : 'does NOT beat') + ' majority baseline ' + Number(baseline).toFixed(4)
            : '';
        if (msg) msg.textContent = (data.message || 'auto-tune complete') + (verdict ? ' — ' + verdict : '');
        await loadAdviserModels();
        await refreshAdviserStatus();
        if (window.NX && window.NX.toast) window.NX.toast(data.message || 'auto-tune complete', 'ok');
    } catch (e) {
        adviserShowError(e.message);
        if (msg) msg.textContent = '';
    } finally {
        btn.disabled = false;
    }
}

/** Move along the activation ladder (DISABLED -> PAPER -> LIVE). */
async function setAdviserActivation(target) {
    try {
        let checks = null;
        if (target === 'LIVE') {
            // LIVE demands real broker/position checks first; run them here so
            // the operator sees the evidence, and the server re-verifies anyway.
            checks = await runAdviserChecks();
            if (!checks) {
                adviserShowError('LIVE refused: prerequisite checks did not pass. See the check list.');
                return;
            }
        }
        const res = await fetch(ADVISER_BASE + '/activate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(checks ? { activation: target, checks: checks } : { activation: target })
        });
        const data = await res.json();
        if (!res.ok) { adviserShowError(data.detail || JSON.stringify(data)); return; }
        adviserHideError();
        await refreshAdviserStatus();
    } catch (e) {
        adviserShowError(e.message);
    }
}

/** Run the REAL broker/position prerequisite checks. Returns them on success. */
async function runAdviserChecks() {
    const box = document.getElementById('adviser-checks-list');
    try {
        const res = await fetch(ADVISER_BASE + '/checks/run', { method: 'POST' });
        const data = await res.json();
        if (!res.ok) { adviserShowError(data.detail || JSON.stringify(data)); return null; }
        const checks = data.checks || [];
        if (box) {
            box.innerHTML = checks.map(function (c) {
                return '<div class="flex items-start gap-2"><span class="' +
                    (c.passed ? 'text-emerald-400' : 'text-rose-400') + '">' +
                    (c.passed ? '✓' : '✗') + '</span><span class="font-mono text-slate-300">' +
                    studioAttr(String(c.name)) + '</span><span class="text-slate-500"> — ' +
                    studioAttr(String(c.detail || '')) + '</span></div>';
            }).join('');
        }
        return data.all_passed ? checks : null;
    } catch (e) {
        adviserShowError(e.message);
        return null;
    }
}
