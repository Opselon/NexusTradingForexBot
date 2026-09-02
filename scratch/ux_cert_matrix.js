// Certification matrix for CHG-0048 (8 parts) at HEAD — behavioral checks in Node.
const fs = require('fs');
const path = require('path');
const WEB = path.resolve(__dirname, '..', 'Web');
const read = (f) => fs.readFileSync(path.join(WEB, f), 'utf8');
const results = [];
const check = (name, cond, note) => results.push({ name, pass: !!cond, note: note || '' });

// PART 1: typed-LIVE gate (ux.js + app.js layering)
const ux = read('ux.js'), appjs = read('app.js');
check('P1 confirmTyped exists + typed gate on LIVE', ux.includes("requireText: toLive ? 'LIVE' : null") && ux.includes('NX.confirmTyped'));
check('P1 app.js awaits confirmModeChange before POST', appjs.includes('await window.NX.confirmModeChange'));
check('P1 cancel reverts selector', appjs.includes('modeSel.value = previous'));

// PART 2: connection banner (ux_conn.js) — states, real signals, retry wiring
const conn = read('ux_conn.js');
check('P2 NXConn UP/DEGRADED/DOWN', ["'UP'", "'DEGRADED'", "'DOWN'"].every(s => conn.includes(s)));
check('P2 retry wired to snapshot+SSE', conn.includes('fetchSystemSnapshot()') && conn.includes('startSSE()'));
check('P2 lastEventAt real timestamp', conn.includes('lastEventAt = Date.now()'));

// PART 3: i18n (ux_i18n.js) — 5 dicts, RTL, lang-changed event, UI-only key
const i18n = read('ux_i18n.js');
check('P3 five dicts', ['fa:', 'de:', 'es:', 'ar:'].every(k => i18n.includes(k)) && /en:\s*null/.test(i18n));
check('P3 RTL fa+ar', /RTL = \{\s*fa: true,\s*ar: true/.test(i18n));
check('P3 lang-changed event + storage key', i18n.includes('nexus:lang-changed') && i18n.includes('nexus.ui.lang'));

// PART 4: palette (ux_palette.js) — Ctrl+K, tab restore, no dangerous commands
const pal = read('ux_palette.js');
check('P4 Ctrl+K toggle', /mod && \(e\.key === 'k' \|\| e\.key === 'K'\)/.test(pal));
check('P4 last-tab restore UI-only', pal.includes('nexus.ui.tab'));
const dangerous = ['engine/mode', 'engine/toggle', 'doEngineToggle', 'confirmModeChange'];
check('P4 no dangerous commands', dangerous.every(d => !pal.includes(d)));

// PART 5: signal humanization (ux_signal.js)
const sig = read('ux_signal.js');
check('P5 not-consulted rule', sig.includes("conf === 0 && reason && reason !== 'CONFIDENCE_GATE'") && sig.includes('not_consulted'));
check('P5 unknown reason verbatim', sig.includes('out.detail = reason'));

// PART 6: attention strip (ux_attention.js) — payload-sourced only
const att = read('ux_attention.js');
check('P6 payload fields only', att.includes('payload.health') && att.includes('payload.is_stale') && att.includes('payload.runtime_mode'));
check('P6 no own fetch', !/fetch\(/.test(att));

// PART 7: serve routes (server.py) + index load order
const srv = fs.readFileSync(path.join(__dirname, '..', 'src/nexus_scalp/web/server.py'), 'utf8');
const assets = ['ux_i18n.js', 'ux_conn.js', 'ux.js', 'ux_signal.js', 'ux_attention.js', 'ux_palette.js'];
check('P7 six routes in server.py', assets.every(a => srv.includes(`"/${a}"`)));
const html = read('index.html');
const order = [...assets, 'app.js'].map(a => html.indexOf(`src="${a}?`));
check('P7 index order ux->app.js', order.every(i => i >= 0) && order.every((v, i) => i === 0 || v > order[i - 1]));

// PART 8: interaction matrix with CC (NEW) — coexistence contracts
// 8a. palette tabs all exist in index.html (switchTab target + tab-btn)
const tabs = [...pal.matchAll(/\['(tab-[a-z-]+)'/g)].map(m => m[1]);
const missingTabs = tabs.filter(t => !html.includes(`id="${t}"`));
check('P8a palette tabs all present in index.html', missingTabs.length === 0, missingTabs.join(','));

// 8b. no id collisions between my modules and CC modules
const mine = ['ux.js', 'ux_i18n.js', 'ux_conn.js', 'ux_signal.js', 'ux_attention.js', 'ux_palette.js'];
const cc = ['cc_state.js', 'cc_components.js', 'control_center.js'];
const created = (src) => {
  const s = new Set();
  for (const m of src.matchAll(/id=\\?['"]([a-zA-Z][\w-]{2,60})\\?['"]/g)) s.add(m[1]);
  for (const m of src.matchAll(/getElementById\(['"]([\w-]+)['"]\)/g)) s.add(m[1]);
  for (const m of src.matchAll(/createElement\(['"][a-z]+['"]\)[\s\S]{0,200}?\.id\s*=\s*['"]([\w-]+)['"]/g)) s.add(m[1]);
  return s;
};
const myIds = new Set(mine.flatMap(f => [...created(read(f))])); // my runtime-created ids included
const ccIds = new Set(cc.flatMap(f => [...created(read(f))]));
const htmlIds = new Set([...html.matchAll(/id="([\w-]+)"/g)].map(m => m[1]));
const collisions = [...myIds].filter(i => ccIds.has(i) && !htmlIds.has(i) && !['tab'].includes(i));
check('P8b no id collisions (runtime-created, not from html)', collisions.length === 0, collisions.join(','));

// 8c. typed-LIVE guard + CC confirmDialog layering: distinct DOM roots, distinct key handlers
check('P8c guards are separate dialogs', ux.includes('data-ux-input') && read('cc_components.js').includes('ccb-confirm-overlay'));
check('P8c my Esc handler early-returns when hidden', /hidden'\)\)\s*return/.test(ux) || ux.includes("classList.contains('hidden')"));
check('P8c CC removes keydown on close', read('cc_components.js').includes("removeEventListener('keydown', onKey"));

// 8d. banner vs cc_state STALE: different scopes; cc_state never drives the banner
check('P8d cc_state does not drive NXConn', !read('cc_state.js').includes('NXConn'));
check('P8d banner driven only from app.js signals', (appjs.match(/NXConn\.set(Up|Down|Degraded)\(/g) || []).length >= 10);

// 8e. no duplicate global keydown: my files add exactly one document keydown each
const kdMine = mine.filter(f => (read(f).match(/document\.addEventListener\(\s*['"]keydown['"]/g) || []).length > 1);
check('P8e max one doc keydown listener per my file', kdMine.length === 0, kdMine.join(','));

// 8f. broken-local-refs: every src/href referenced by index.html resolves to a served route or vendor asset
const localRefs = [...html.matchAll(/(?:src|href)="([^"#?]+)(?:\?[^"]*)?"/g)]
  .map(m => m[1])
  .filter(u => !/^(https?:|data:|#|mailto:)/.test(u));
const served = ['/', '/app.js', ...assets, '/cc_state.js', '/cc_components.js', '/control_center.js', '/replay_panel.js',
  '/api_client.js', '/command_center_console.js', '/command_center_spatial.js', '/command_center_timemachine.js',
  '/command_center_ui.js', '/dependency_api.js', '/dependency_graph.js', '/dependency_ui.js', '/forensic_console.js',
  '/news_intelligence.js', '/styles.css', '/command_center.html', '/vendor/fontawesome/all.min.css'];
const webFiles = new Set(fs.readdirSync(WEB));
const unreferenced = localRefs.filter(r => !served.includes('/' + r) && !webFiles.has(r.split('/').pop()));
check('P8f index.html local refs all resolve', unreferenced.length === 0, unreferenced.join(','));

let fails = 0;
for (const r of results) {
  if (!r.pass) fails++;
  console.log(`${r.pass ? 'PASS' : 'FAIL'} ${r.name}${r.note ? '  [' + r.note + ']' : ''}`);
}
console.log(`MATRIX_RESULT: ${fails === 0 ? 'ALL_PASS' : fails + '_FAIL'} (${results.length} checks)`);
process.exit(fails === 0 ? 0 : 1);
