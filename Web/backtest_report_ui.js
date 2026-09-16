/* Read-only empirical report UI. Imported values are always escaped. */
(function (global) {
    'use strict';

    function escape(value) {
        return String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function label(key) {
        const units = { usd: 'USD', r: 'R', pct: '%', seconds: 'seconds', ms: 'ms', lots: 'lots' };
        let words = String(key).replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ');
        const parts = words.split(' ');
        const unit = units[parts[parts.length - 1]?.toLowerCase()];
        if (unit) parts.pop();
        words = parts.join(' ');
        return escape(words.charAt(0).toUpperCase() + words.slice(1) + (unit ? ' (' + unit + ')' : ''));
    }

    function formatPrimitive(data) {
        if (data == null || data === '' || (typeof data === 'number' && !Number.isFinite(data))) return 'N/A';
        return escape(data);
    }

    function value(data, depth = 0) {
        if (data == null || data === '' || (typeof data === 'number' && !Number.isFinite(data))) return 'N/A';
        if (depth > 6) return '<span class="text-textMuted">[nested data omitted]</span>';

        if (Array.isArray(data)) {
            if (!data.length) return 'N/A';
            const MAX_ARRAY_ITEMS = 100;
            const visible = data.slice(0, MAX_ARRAY_ITEMS);
            const omitted = data.length - MAX_ARRAY_ITEMS;
            let listHtml = '<ul class="space-y-1 pl-3 list-disc">' + visible.map(item => '<li>' + value(item, depth + 1) + '</li>').join('');
            if (omitted > 0) {
                listHtml += '<li class="text-textMuted italic font-mono text-[11px] list-none pt-1">[' + omitted + ' entries omitted from visual table; see downloadable JSON for full array]</li>';
            }
            listHtml += '</ul>';
            return listHtml;
        }

        if (typeof data === 'object') {
            const keys = Object.keys(data);
            return keys.length ? '<dl class="space-y-1">' + keys.map(key => '<div class="border-b border-borderClr/40 py-1"><dt class="text-textMuted">' + label(key) + '</dt><dd class="text-gray-200 break-words">' + value(data[key], depth + 1) + '</dd></div>').join('') + '</dl>' : 'N/A';
        }

        return escape(data);
    }

    function section(title, content, extraClass = '') {
        return '<section class="bg-darkBg/60 border border-borderClr rounded-lg p-3 min-w-0 ' + extraClass + '">' +
            '<h3 class="font-bold text-accentCyan mb-2 text-xs tracking-wide uppercase">' + escape(title) + '</h3>' +
            content + '</section>';
    }

    function extractEntries(source) {
        if (!source) return [];
        if (Array.isArray(source)) return source;
        if (Array.isArray(source.entries)) return source.entries;
        if (Array.isArray(source.rows)) return source.rows;
        if (Array.isArray(source.items)) return source.items;
        if (Array.isArray(source.trades)) return source.trades;
        if (Array.isArray(source.orders)) return source.orders;
        if (Array.isArray(source.data)) return source.data;
        return [];
    }

    function metadata(source, excluded) {
        if (!source || Array.isArray(source) || typeof source !== 'object') return '';
        const data = Object.fromEntries(Object.entries(source).filter(([key]) => !excluded.includes(key)));
        return Object.keys(data).length ? value(data) : '';
    }

    function renderTrades(trades, extra = {}) {
        if (!trades) return '<p class="text-textMuted italic">No trade entries recorded.</p>';
        const entries = extractEntries(trades);
        const total = typeof extra.total === 'number' ? extra.total : (typeof trades.total_count === 'number' ? trades.total_count : (typeof trades.total === 'number' ? trades.total : entries.length));
        const isTruncated = Boolean(extra.truncated || trades.truncated || (total > entries.length));
        const noteText = extra.note || trades.note || trades.truncation_reason || trades.message;
        const meta = metadata(trades, ['entries', 'rows', 'items', 'trades', 'orders', 'data']);

        let note = meta;
        if (entries.length || total > 0) {
            note += '<div class="text-[11px] text-textMuted mb-2">' +
                'Showing ' + entries.length + (total !== entries.length ? ' of ' + total : '') + ' trade entries' +
                (isTruncated ? ' <span class="text-amber-400 font-semibold">(server truncated / capped)</span>' : '') +
                (noteText ? ' <span class="text-gray-300">[' + escape(noteText) + ']</span>' : '') +
                '.</div>';
        }

        if (!entries.length) {
            return note + '<p class="text-textMuted italic">No trade entries recorded' + (total > 0 ? ' (' + total + ' total trades truncated by server)' : '') + '.</p>';
        }

        // Gather union of all properties across all entries
        const propSet = new Set();
        entries.forEach(e => {
            if (e && typeof e === 'object') {
                Object.keys(e).forEach(k => propSet.add(k));
            }
        });
        const props = Array.from(propSet);

        const thead = '<thead><tr class="border-b border-borderClr bg-darkBg/80 text-textMuted text-left">' +
            props.map(p => '<th class="py-1 px-2 font-medium whitespace-nowrap">' + label(p) + '</th>').join('') +
            '</tr></thead>';

        const tbody = '<tbody class="divide-y divide-borderClr/30">' + entries.map(entry => {
            return '<tr class="hover:bg-white/5 transition">' + props.map(p => {
                const val = entry ? entry[p] : undefined;
                return '<td class="py-1 px-2 whitespace-nowrap font-mono text-[11px]">' + value(val) + '</td>';
            }).join('') + '</tr>';
        }).join('') + '</tbody>';

        return note + '<div class="overflow-x-auto max-h-72 border border-borderClr/40 rounded"><table class="w-full text-xs">' + thead + tbody + '</table></div>';
    }

    function renderOrders(orders) {
        const entries = extractEntries(orders);
        const reason = (orders && (orders.reason || orders.explanation || orders.message)) ||
            'Order records are not provided in this empirical outcome replay; order execution must not be inferred from trades.';

        if (!orders || orders.available === false || !entries.length) {
            return '<div class="p-2.5 rounded bg-amber-500/10 border border-amber-500/20 text-amber-200 text-[11px]">' +
                '<strong>Orders unavailable:</strong> ' + escape(reason) + '</div>' +
                metadata(orders, ['entries', 'rows', 'items', 'orders', 'data']);
        }

        return renderTrades({
            entries,
            total: orders.total_count ?? orders.total ?? entries.length,
            truncated: orders.truncated,
            note: orders.note
        });
    }

    function flattenCurves(obj, prefix = '') {
        const result = [];
        if (!obj || typeof obj !== 'object') return result;

        for (const [key, val] of Object.entries(obj)) {
            const currentKey = prefix ? prefix + ' ' + key : key;
            if (Array.isArray(val)) {
                result.push({ key: currentKey, points: val, unit: obj.unit, note: obj.note });
            } else if (val && typeof val === 'object') {
                if (Array.isArray(val.points) || Array.isArray(val.data) || Array.isArray(val.values) || Array.isArray(val.series)) {
                    result.push({
                        key: currentKey,
                        points: val.points || val.data || val.values || val.series,
                        unit: val.unit || obj.unit,
                        note: val.note || obj.note
                    });
                } else {
                    const nested = flattenCurves(val, currentKey);
                    if (nested.length) {
                        result.push(...nested);
                    }
                }
            }
        }
        return result;
    }

    function renderCurves(curves) {
        if (!curves || typeof curves !== 'object') return 'N/A';
        const curveList = flattenCurves(curves);
        if (!curveList.length) return value(curves);
        const meta = metadata(curves, Object.keys(curves).filter(k => typeof curves[k] === 'object' && curves[k] !== null));

        let renderedAny = false;
        const parts = curveList.map(({ key, points, unit, note }) => {
            if (!Array.isArray(points)) return null;

            // Retain source positions and gap IDs before visual-only sampling.
            const finitePoints = [];
            let gap = 0;
            let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
            points.forEach((p, idx) => {
                const y = typeof p === 'number' ? p : (p && typeof p === 'object' ? (p.value ?? p.y ?? p.val ?? p.v) : null);
                const rawX = p && typeof p === 'object' ? (p.index ?? p.x ?? p.i ?? p.t) : idx;
                const x = typeof rawX === 'number' && Number.isFinite(rawX) ? rawX : idx;
                if (typeof y !== 'number' || !Number.isFinite(y)) { gap++; return; }
                finitePoints.push({ x, y, originalIndex: idx, gap });
                if (x < minX) minX = x;
                if (x > maxX) maxX = x;
                if (y < minY) minY = y;
                if (y > maxY) maxY = y;
            });
            if (finitePoints.length < 2) return null;
            renderedAny = true;

            // 249 bucket min/max pairs + both endpoints: at most 500.
            // Every source point participates; even a one-observation spike survives.
            let displayed = finitePoints;
            if (finitePoints.length > 500) {
                displayed = [finitePoints[0]];
                const buckets = 249;
                for (let b = 0; b < buckets; b++) {
                    const start = 1 + Math.floor(b * (finitePoints.length - 2) / buckets);
                    const end = 1 + Math.floor((b + 1) * (finitePoints.length - 2) / buckets);
                    let low = start, high = start;
                    for (let i = start + 1; i < end; i++) {
                        if (finitePoints[i].y < finitePoints[low].y) low = i;
                        if (finitePoints[i].y > finitePoints[high].y) high = i;
                    }
                    displayed.push(finitePoints[Math.min(low, high)]);
                    if (low !== high) displayed.push(finitePoints[Math.max(low, high)]);
                }
                displayed.push(finitePoints[finitePoints.length - 1]);
            }

            // Normalize first to avoid overflow for finite +/- Number.MAX_VALUE.
            const xScale = Math.max(Math.abs(minX), Math.abs(maxX), 1);
            const yScale = Math.max(Math.abs(minY), Math.abs(maxY), 1);
            const rangeX = maxX / xScale - minX / xScale || 1;
            const rangeY = maxY / yScale - minY / yScale || 1;

            const width = 360;
            const height = 80;
            const padding = 10;
            const w = width - padding * 2;
            const h = height - padding * 2;

            // Build segments without bridging null/missing gaps
            const segments = [];
            let currentSegment = [];
            displayed.forEach((pt, idx) => {
                const normX = (pt.x / xScale - minX / xScale) / rangeX;
                const normY = (pt.y / yScale - minY / yScale) / rangeY;
                const cx = padding + normX * w;
                const cy = padding + h - normY * h;
                if (idx > 0 && pt.gap !== displayed[idx - 1].gap) {
                    if (currentSegment.length) {
                        segments.push(currentSegment);
                    }
                    currentSegment = [];
                }
                currentSegment.push({ cx, cy, pt });
            });
            if (currentSegment.length) {
                segments.push(currentSegment);
            }

            const polylinesHtml = segments.map(seg => {
                const pts = seg.map(p => p.cx.toFixed(1) + ',' + p.cy.toFixed(1)).join(' ');
                return '<polyline points="' + pts + '" stroke="#38bdf8" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>';
            }).join('');

            const circlesHtml = displayed.map(item => {
                const normX = (item.x / xScale - minX / xScale) / rangeX;
                const normY = (item.y / yScale - minY / yScale) / rangeY;
                const cx = padding + normX * w;
                const cy = padding + h - normY * h;
                return '<circle cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) + '" r="2" fill="#38bdf8" data-original-index="' + item.originalIndex + '" data-x="' + item.x + '"><title>Observation ' + item.originalIndex + ' | X: ' + item.x + ' | Value: ' + item.y + '</title></circle>';
            }).join('');

            const unitBadge = unit ? ' <span class="text-accentCyan uppercase font-mono">(' + escape(unit) + ')</span>' : '';
            const noteHtml = note ? ' <div class="text-[10px] text-textMuted mt-0.5">' + escape(note) + '</div>' : '';

            const isDownsampled = displayed.length < finitePoints.length;
            const countDisclosure = 'Original: ' + points.length + ' observations | Displayed: ' + displayed.length + ' of ' + finitePoints.length + ' finite points' +
                (isDownsampled ? ' (downsampled; bucket extrema retained, full series in JSON)' : '');

            return '<div class="bg-darkBg border border-borderClr/40 rounded p-2.5 mb-2">' +
                '<div class="flex justify-between items-baseline mb-1 text-[11px]">' +
                '<div><span class="font-bold text-gray-200">' + label(key) + '</span>' + unitBadge + noteHtml + '</div>' +
                '<div class="text-textMuted font-mono text-[10px] text-right">' +
                countDisclosure + ' | Min: ' + minY.toFixed(2) + ' Max: ' + maxY.toFixed(2) +
                '</div>' +
                '</div>' +
                '<svg viewBox="0 0 ' + width + ' ' + height + '" class="w-full h-16" preserveAspectRatio="none">' +
                polylinesHtml + circlesHtml +
                '</svg>' +
                '</div>';
        }).filter(Boolean);

        if (!renderedAny || !parts.length) {
            return '<p class="text-textMuted italic">No curves with sufficient finite points recorded (N/A).</p>';
        }
        return meta + '<p class="text-textMuted text-[10px] mb-2">X: original observation index (zero-based unless supplied). Hover points for source index/value. Separate scales by series; missing gaps are not interpolated.</p><div class="space-y-2">' + parts.join('') + '</div>';
    }

    function downloadReportJson(report, filename) {
        if (!report) return;
        const json = JSON.stringify(report, null, 2);
        const BlobCtor = (global && global.Blob) || (typeof Blob !== 'undefined' ? Blob : null);
        if (!BlobCtor) throw new Error('Blob constructor unavailable');
        const blob = new BlobCtor([json], { type: 'application/json' });
        const URLObj = (global && global.URL) || (typeof URL !== 'undefined' ? URL : null);
        if (!URLObj || typeof URLObj.createObjectURL !== 'function') throw new Error('URL.createObjectURL unavailable');
        const url = URLObj.createObjectURL(blob);
        const doc = (global && global.document) || (typeof document !== 'undefined' ? document : null);
        if (!doc) throw new Error('document unavailable');
        const a = doc.createElement('a');
        a.href = url;
        a.download = filename || ('backtest-report-' + (report.schema_version || 'v1') + '.json');
        doc.body.appendChild(a);
        a.click();
        doc.body.removeChild(a);
        const setTimeoutFn = (global && global.setTimeout) || (typeof setTimeout !== 'undefined' ? setTimeout : null);
        if (setTimeoutFn) {
            setTimeoutFn(() => URLObj.revokeObjectURL(url), 1000);
        }
    }

    function render(report) {
        if (!report || typeof report !== 'object' || Array.isArray(report)) {
            return '<p class="text-textMuted italic">Detailed report unavailable (legacy result).</p>';
        }

        const sectionsHtml = [];

        // Engine & Settings
        sectionsHtml.push(section('Engine', value(report.engine)));
        sectionsHtml.push(section('Settings', value(report.settings)));

        // Economic assumptions vs Sized if present
        if (report.economic && typeof report.economic === 'object') {
            sectionsHtml.push(section('Economic assumptions (modeled, not observed)', value(report.economic)));
        }
        if (report.sized && typeof report.sized === 'object') {
            sectionsHtml.push(section('Sized (modeled volume / risk-sized performance)', value(report.sized)));
        }

        // Performance & Drawdown
        sectionsHtml.push(section('Performance', value(report.performance)));
        sectionsHtml.push(section('Drawdown', value(report.drawdown)));

        // Directions & Trade Statistics
        sectionsHtml.push(section('Directions', value(report.directions)));
        sectionsHtml.push(section('Trade statistics', value(report.trade_statistics)));

        // Holding / Excursion & Data quality
        sectionsHtml.push(section('Holding / excursion', value(report.holding_excursion)));
        sectionsHtml.push(section('Data quality', value(report.data_quality)));

        // Trades & Orders
        sectionsHtml.push(section('Trades', renderTrades(report.trades, {
            total: report.trades_total ?? report.total_trades ?? report.trade_statistics?.total_trades,
            truncated: report.trades_truncated ?? report.trade_statistics?.truncated,
            note: report.trades_note ?? report.trade_statistics?.truncation_note
        }), 'md:col-span-2'));
        sectionsHtml.push(section('Orders', renderOrders(report.orders)));

        // Curves & Limitations
        sectionsHtml.push(section('Curves', renderCurves(report.curves), 'md:col-span-2'));
        sectionsHtml.push(section('Limitations', value(report.limitations)));

        const downloadBtn = '<button type="button" data-backtest-download class="px-2.5 py-1 text-[11px] font-semibold bg-accentCyan/10 text-accentCyan border border-accentCyan/30 rounded hover:bg-accentCyan/20 transition cursor-pointer">' +
            'Download JSON</button>';

        return '<div class="space-y-4 text-xs text-gray-300 font-sans">' +
            '<header class="flex flex-wrap items-center justify-between gap-2 border-b border-borderClr/60 pb-3">' +
            '<div>' +
            '<div class="flex items-center gap-2">' +
            '<h2 class="font-bold text-base text-white">Backtest report</h2>' +
            '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/15 text-amber-300 border border-amber-500/30">NSE_EMPIRICAL_REPLAY</span>' +
            '</div>' +
            '<p class="text-textMuted text-[11px] mt-0.5">Replay of immutable recorded trade outcomes; not an MT5 Strategy Tester run or real-tick simulation. Research evidence only, not live approval.</p>' +
            '</div>' +
            '<div class="flex items-center gap-2">' +
            '<span class="text-[11px] text-textMuted">Schema: ' + formatPrimitive(report.schema_version) + '</span>' +
            downloadBtn +
            '</div>' +
            '</header>' +
            '<div class="grid grid-cols-1 md:grid-cols-2 gap-3">' +
            sectionsHtml.join('') +
            '</div>' +
            '</div>';
    }

    function bindDownload(container, report) {
        if (!container || !container.querySelector) return;
        const button = container.querySelector('[data-backtest-download]');
        if (!button) return;
        button.addEventListener('click', () => {
            try {
                downloadReportJson(report);
            } catch (error) {
                button.textContent = 'JSON download unavailable';
            }
        });
    }

    global.NSEBacktestReport = {
        render,
        downloadReportJson,
        bindDownload,
        escape,
        label,
        renderTrades,
        renderOrders,
        renderCurves
    };
}(typeof window !== 'undefined' ? window : globalThis));
