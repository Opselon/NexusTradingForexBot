'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
function load(extra = {}) {
    const context = { window: {}, ...extra };
    vm.createContext(context);
    const file = path.join(root, 'Web/backtest_report_ui.js');
    if (fs.existsSync(file)) vm.runInContext(fs.readFileSync(file, 'utf8'), context);
    assert.equal(typeof context.window.NSEBacktestReport?.render, 'function', 'standalone renderer is registered');
    return context.window.NSEBacktestReport;
}

test('renders every report section, nested metric labels, real zero and missing values safely', () => {
    const ui = load();
    const html = ui.render({
        schema_version: 1, engine: { name: 'NSE_EMPIRICAL_REPLAY' },
        settings: { symbol: '<img src=x onerror="alert(1)">', costs: { commission_usd: 0 } },
        performance: { net_profit_usd: 0, profit_factor: null, expectancy_r: -0.25 },
        drawdown: { max_drawdown_pct: null }, directions: { buy: { win_rate_pct: 50 } },
        trade_statistics: { count: 0 }, holding_excursion: { holding_seconds: null },
        data_quality: { '<script>': '"&\'' }, limitations: ['Immutable outcomes only'],
    });
    for (const title of ['Engine', 'Settings', 'Performance', 'Drawdown', 'Directions', 'Trade statistics', 'Holding / excursion', 'Data quality', 'Trades', 'Orders', 'Curves', 'Limitations']) {
        assert.ok(html.includes('>' + title + '<'), title);
    }
    assert.match(html, /Net profit \(USD\)[\s\S]*?>0</);
    assert.match(html, /Profit factor[\s\S]*?>N\/A</);
    assert.match(html, /Expectancy \(R\)[\s\S]*?>-0.25</);
    assert.match(html, /Commission \(USD\)/);
    assert.match(html, /Win rate \(%\)/);
    assert.match(html, /&lt;img/);
    assert.match(html, /&lt;script&gt;/i);
    assert.match(html, /&quot;&amp;&#39;/);
    assert.doesNotMatch(html, /<img|<script|NaN|Infinity/);
    assert.match(html, /NSE_EMPIRICAL_REPLAY/);
    assert.match(html, /not an MT5 Strategy Tester/i);
    assert.doesNotMatch(html, /MT5.*(?:passed|success)|real ticks verified|approve live/i);
});

test('trade table includes union of properties and discloses server truncation and unavailable orders', () => {
    const html = load().render({ trades: { entries: [{ id: '<x>', pnl_r: 0 }, { id: 'b', exit_reason: 'SL', extra: { fee_usd: null } }], total: 10, truncated: true }, orders: null,
        economic: { net_profit_usd: 15 }, sized: { net_profit_usd: 22 } });
    assert.match(html, /<table/);
    for (const label of ['Id', 'Pnl (R)', 'Exit reason', 'Extra']) assert.ok(html.includes('>' + label + '<'));
    assert.match(html, /&lt;x&gt;/);
    assert.match(html, /Showing 2/);
    assert.match(html, /truncated/i);
    assert.match(html, /Orders unavailable.*(?:not recorded|not provided)/s);
    assert.match(html, /Economic assumptions \(modeled, not observed\)/i);
    assert.match(html, /Sized.*modeled/i);
});

test('curves use finite real points with separate units and never substitute missing values', () => {
    const ui = load();
    const html = ui.render({ curves: { equity_r: [0, 2, null, -1, Infinity], equity_usd: [100, 103, 99], missing: null } });
    assert.equal((html.match(/<svg /g) || []).length, 2);
    assert.match(html, /Equity \(R\)/);
    assert.match(html, /Equity \(USD\)/);
    assert.doesNotMatch(html, /NaN|Infinity/);
    assert.match(html, /finite points/i);
    const blank = ui.render({ curves: { equity_r: [null, '2', Infinity] } });
    assert.doesNotMatch(blank, /<svg /);
    assert.match(blank, /N\/A/);
});

test('downloadReportJson uses local Blob and trigger without remote calls', () => {
    let clicked = false;
    let appended = false;
    let removed = false;
    let createdUrl = null;
    let createdType = null;
    let downloadedName = null;

    class FakeBlob {
        constructor(parts, options) {
            this.parts = parts;
            this.type = options?.type;
            createdType = this.type;
        }
    }

    const mockWindow = {
        Blob: FakeBlob,
        URL: {
            createObjectURL(blob) {
                createdUrl = 'blob:test-123';
                return createdUrl;
            },
            revokeObjectURL(url) {}
        },
        document: {
            createElement(tag) {
                return {
                    href: '',
                    download: '',
                    click() { clicked = true; downloadedName = this.download; }
                };
            },
            body: {
                appendChild(el) { appended = true; },
                removeChild(el) { removed = true; }
            }
        },
        setTimeout(fn) { fn(); }
    };

    const ui = load({ window: mockWindow, setTimeout: mockWindow.setTimeout });
    const rendered = ui.render({ schema_version: 1 });
    assert.doesNotMatch(rendered, /onclick=|__nse_last_rendered_report/);
    ui.downloadReportJson({ schema_version: 1, test: true }, 'custom-report.json');
    assert.equal(clicked, true);
    assert.equal(appended, true);
    assert.equal(removed, true);
    assert.equal(createdType, 'application/json');
    assert.equal(downloadedName, 'custom-report.json');
});

test('Web/index.html registers backtest_report_ui.js before app.js', () => {
    const indexHtml = fs.readFileSync(path.join(root, 'Web/index.html'), 'utf8');
    const uiScriptIdx = indexHtml.indexOf('backtest_report_ui.js');
    const appScriptIdx = indexHtml.indexOf('app.js');
    assert.ok(uiScriptIdx !== -1, 'backtest_report_ui.js is registered in Web/index.html');
    assert.ok(appScriptIdx !== -1, 'app.js is registered in Web/index.html');
    assert.ok(uiScriptIdx < appScriptIdx, 'backtest_report_ui.js appears BEFORE app.js');
});



function researchContext(result, fail = false, renderer = load()) {
    const box = { innerHTML: '', querySelector: () => null };
    const ctx = { window: { NSEBacktestReport: renderer }, document: { getElementById: id => id === 'research-validate-id' ? { value: 'a&b' } : box },
        NX: { api: { post: async () => { if (fail) throw Error('<offline>'); return { ok: true, body: { available: true, result } }; } } },
        esc: s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
        safeScore: () => 0, safeScoreObj: s => s, loadResearchSummary: () => {}, console: { warn() {} } };
    vm.createContext(ctx);
    const src = fs.readFileSync(path.join(root, 'Web/app.js'), 'utf8');
    const start = src.indexOf('async function validateResearchCandidate()');
    vm.runInContext(src.slice(start, src.indexOf('// =============================================================================', start)), ctx);
    return { ctx, box };
}
test('validation mounts report, retains independent gate statuses, and shows errors instead of pending forever', async () => {
    const { ctx, box } = researchContext({ lifecycle: 'REJECTED', backtest: { report: { performance: { net_profit_usd: 0 } } },
        walk_forward: { passed: false }, oos: { status: 'FAIL' }, robustness: { status: 'NOT_RUN' } });
    await ctx.validateResearchCandidate();
    assert.match(box.innerHTML, /Backtest report/);
    assert.match(box.innerHTML.replace(/<[^>]*>/g, ''), /walk-forward: FAIL/);
    assert.match(box.innerHTML.replace(/<[^>]*>/g, ''), /oos_status: FAIL/);
    assert.match(box.innerHTML.replace(/<[^>]*>/g, ''), /robustness: NOT_RUN/);
    const failed = researchContext({}, true);
    await failed.ctx.validateResearchCandidate();
    assert.match(failed.box.innerHTML, /Validation failed.*&lt;offline&gt;/);
    assert.doesNotMatch(failed.box.innerHTML, /Running|Waiting/);
    const legacy = researchContext({ backtest: { expectancy_r: 0 } }, false, null);
    await legacy.ctx.validateResearchCandidate();
    assert.match(legacy.box.innerHTML.replace(/<[^>]*>/g, ''), /expectancy_r: 0/);
    assert.match(legacy.box.innerHTML, /report unavailable/i);
});
test('factory renders only on first open, retains gates and binds each report download', async () => {
    const ui = load();
    const rendered = [], bound = [];
    const benchmarks = Array.from({length: 50}, (_, i) => ({candidate_id: 'candidate-' + i,
        backtest: {expectancy_r: 0, report: {performance: {net_profit_usd: i}}},
        walk_forward: {passed: false}, oos: {status: 'FAIL'}}));
    const nodes = benchmarks.map((_, i) => {
        const handlers = {}, clicks = [];
        const details = {open: false, addEventListener: (type, fn) => { handlers[type] = fn; }};
        return {innerHTML: '', parentElement: details, closest: () => details,
            getAttribute: () => String(i), handlers, clicks,
            querySelector: () => ({addEventListener: (type, fn) => { clicks.push([type, fn]); }})};
    });
    const box = {innerHTML: '', querySelectorAll: () => nodes};
    const ctx = {window: {NSEBacktestReport: {
        render: report => { rendered.push(report); return ui.render(report); },
        bindDownload: (node, report) => { bound.push(report); ui.bindDownload(node, report); }
    }}, document: {getElementById: id => id === 'factory-benchmarks' ? box : null},
        NX: {api: {get: async () => ({available: true, benchmarks})}},
        factoryRes: r => r, factoryLog() {}, escHtml: ui.escape, console};
    vm.createContext(ctx);
    const src = fs.readFileSync(path.join(root, 'Web/app.js'), 'utf8');
    const start = src.indexOf('async function loadFactoryBenchmarks(');
    vm.runInContext(src.slice(start, src.indexOf('// ===========================================================================', start)), ctx);
    await ctx.loadFactoryBenchmarks();
    assert.equal(rendered.length, 0, '50 closed reports must not render eagerly');
    assert.equal(bound.length, 0);
    assert.equal((box.innerHTML.match(/<details/g) || []).length, 50);
    assert.match(box.innerHTML, /walk-fwd[\s\S]*FAIL/);
    for (const i of [0, 49]) {
        const node = nodes[i];
        assert.equal(typeof node.handlers.toggle, 'function', 'details toggle wired');
        node.handlers.toggle();
        assert.equal(rendered.length, i === 0 ? 0 : 1, 'closed toggle ignored');
        node.parentElement.open = true;
        node.handlers.toggle();
        assert.match(node.innerHTML, /Backtest report/);
        assert.equal(rendered.at(-1), benchmarks[i].backtest.report);
        assert.equal(bound.at(-1), benchmarks[i].backtest.report);
        assert.equal(node.clicks.length, 1, 'download click bound once');
        node.parentElement.open = false;
        node.handlers.toggle();
        node.parentElement.open = true;
        node.handlers.toggle();
        assert.equal(node.clicks.length, 1, 'reopen must not bind twice');
    }
    assert.equal(rendered.length, 2, 'only two opened reports rendered once');
    assert.equal(bound.length, 2);
});

test('report envelopes preserve trade/order metadata and nested curve points without bridging gaps', () => {
    const ui = load();
    const html = ui.render({
        trades: { rows: [{ ticket: 'T1', profit_r: 0 }], total_count: 8, returned_count: 1, truncated: true, note: '<cap>' },
        orders: { available: false, reason: '<not in source>', rows: [] },
        curves: { realized: { equity_r: [{ index: 0, value: 0 }, { index: 1, value: 2 }, { index: 2, value: null }, { index: 3, value: -1 }], unit: 'R', note: '<actual>' } }
    });
    assert.match(html, /T1/);
    assert.match(html, /Showing 1 of 8/);
    assert.match(html, /&lt;cap&gt;/);
    assert.match(html, /&lt;not in source&gt;/);
    assert.match(html, /&lt;actual&gt;/);
    assert.match(html, /<svg /);
    assert.match(html, /<circle /);
    assert.match(html, /stroke="#[a-fA-F0-9]+"/);
});

test('a renderer exception leaves legacy summary intact with escaped warning', async () => {
    const { ctx, box } = researchContext({ backtest: { report: {}, expectancy_r: 1 } }, false, { render: () => { throw Error('<bad report>'); } });
    await ctx.validateResearchCandidate();
    assert.match(box.innerHTML, /&lt;bad report&gt;/);
    assert.match(box.innerHTML.replace(/<[^>]*>/g, ''), /expectancy_r: 1/);
});

test('curve coordinates stay finite with extreme inputs; all envelope metadata stays visible', () => {
 const ui = load();
 const html = ui.render({ trades: { entries: [{ id: 'x' }], limit: 100, custom_field: '<meta>' },
    orders: { available: false, count: null, reason: 'not provided', provenance: '<orders>' },
    curves: { equity_r: [{x: Infinity, value: 1}, {x: 99999, value: 2}], note: '<curve meta>' } });
 assert.match(html, /Custom field/);
 assert.match(html, /&lt;meta&gt;/);
 assert.match(html, /&lt;orders&gt;/);
 assert.match(html, /&lt;curve meta&gt;/);
 assert.doesNotMatch(html, /(?:cx|cy|points)="[^"]*(?:Infinity|NaN)/);
});

test('real server trade_statistics cap appears at the trade table with 501 total trades', () => {
 const report = {
   trades: Array.from({length: 500}, (_, i) => ({sample_id: String(i), realized_r: 0})),
   trade_statistics: {total_trades: 501, displayed_trades: 500, truncated: true,
     truncation_note: 'Trade list capped at 500 entries for transport and UI safety <cap>'}
 };
 const table = load().render(report).split('>Trades</h3>')[1].split('</section>')[0];
 assert.match(table, /Showing 500 of 501 trade entries/);
 assert.match(table, /server truncated \/ capped/);
 assert.match(table, /Trade list capped at 500 entries for transport and UI safety &lt;cap&gt;/);
});

test('large curves bound display to 500 with original indices, extrema and counts', () => {
 const points = Array.from({length: 200001}, (_, i) => ({index: i * 3, value: i % 7}));
 points[12345].value = -999;
 points[178901].value = 999;
 const html = load().renderCurves({cumulative_r: points, closed_pnl_usd: points});
 const svgs = [...html.matchAll(/<svg\b[^>]*>([\s\S]*?)<\/svg>/g)];
 assert.equal(svgs.length, 2);
 for (const [, svg] of svgs) {
   const circles = [...svg.matchAll(/<circle\b[^>]*>/g)];
   assert.ok(circles.length <= 500, 'at most 500 circles per curve');
   assert.ok(circles.length > 2);
   for (const index of [0, 12345, 178901, 200000]) {
     assert.match(svg, new RegExp('data-original-index="' + index + '"'));
     assert.match(svg, new RegExp('data-x="' + (index * 3) + '"'));
   }
   const vertices = [...svg.matchAll(/points="([^"]*)"/g)].reduce((n, m) => n + m[1].split(' ').length, 0);
   assert.ok(vertices <= 500, 'polylines also bounded');
 }
 assert.match(html, /Original: 200001 observations/);
 assert.match(html, /Displayed: \d+ of 200001 finite points/);
 assert.match(html, /downsampled/i);
 assert.match(html, /Min: -999.00 Max: 999.00/);
 assert.doesNotMatch(html, /(?:cx|cy|points)="[^"]*(?:Infinity|NaN)/);
 assert.equal(points.length, 200001, 'original download data untouched');
 assert.equal(points[12345].value, -999);
});

test('curve previews disclose even unsampled counts and preserve missing gaps after sampling', () => {
 const ui = load();
 const small = ui.renderCurves({r: [0, null, 2, 3]});
 assert.match(small, /Original: 4 observations \| Displayed: 3 of 3 finite points/);
 assert.match(small, /<title>Observation 2 \| X: 2 \| Value: 2<\/title>/);
 const points = Array.from({length: 100001}, (_, i) => i);
 points[50000] = null;
 const html = ui.renderCurves({r: points});
 assert.equal((html.match(/<polyline/g) || []).length, 2, 'no line bridges a missing point');
 assert.match(html, /Original: 100001 observations \| Displayed: \d+ of 100000 finite points/);
 assert.doesNotMatch(html, /data-original-index="50000"/);
});

test('top-level trade cap metadata is honored and large sized arrays have explicit omission count', () => {
 const ui = load();
 const html = ui.render({ trades: [{id:'a'}], trades_total: 1234, trades_truncated: true,
   sized: { per_trade: Array.from({length: 2000}, (_, i) => i) } });
 assert.match(html, /Showing 1 of 1234/);
 assert.match(html, /truncated/);
 assert.match(html, /1900 entries omitted/);
 assert.ok(html.length < 30000);
 const core = ui.render({ trades: [{id:'a'}], trade_statistics: { total_trades: 501, truncated: true } });
 assert.match(core, /Showing 1 of 501/);
});
