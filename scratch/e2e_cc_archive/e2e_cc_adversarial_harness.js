// Comprehensive Playwright verification & adversarial certification for Command Center Phase 3
const { chromium } = require('playwright');
const fs = require('fs');

const BASE = 'http://127.0.0.1:8081';
const REPORT_PATH = 'tests/e2e_cc_adversarial_result.json';

async function run() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  
  const consoleErrors = [];
  const pageErrors = [];
  page.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
  page.on('pageerror', err => pageErrors.push(err.message));

  const results = { timestamp: new Date().toISOString(), tests: {} };

  // 1. Dashboard / CC Page Load
  await page.goto(BASE + '/command_center.html', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);
  results.tests.pageLoad = {
    title: await page.title(),
    canvasVisible: await page.$eval('canvas', c => c.offsetWidth > 0 && c.offsetHeight > 0)
  };

  // 2. Spatial API + Anti-Clump & Node Count
  const spatialRes = await page.evaluate(async () => {
    const r = await fetch('/api/command-center/spatial');
    const j = await r.json();
    return {
      available: j.available,
      nodeCount: j.nodes ? j.nodes.length : 0,
      zones: j.zones ? Object.keys(j.zones) : []
    };
  });
  results.tests.spatialApi = spatialRes;

  // 3. Execution Safety Adversarial Test (MOST IMPORTANT)
  // Try to make a non-active strategy display LIVE / YES.
  // We query a non-active strategy's execution-safety endpoint directly and verify it returns BLOCKED and can_trade=false.
  const execSafetyTest = await page.evaluate(async () => {
    const fleetRes = await fetch('/api/command-center/fleet?limit=5');
    const fleetJson = await fleetRes.json();
    const rows = fleetJson.rows || [];
    if (!rows.length) return { error: 'No strategies in fleet' };
    
    const nonActiveStrat = rows.find(r => r.lifecycle !== 'ACTIVE') || rows[0];
    const safetyRes = await fetch('/api/command-center/execution-safety/' + nonActiveStrat.strategy_id);
    const safetyJson = await safetyRes.json();
    
    return {
      strategy_id: nonActiveStrat.strategy_id,
      lifecycle: nonActiveStrat.lifecycle,
      eligibility_state: safetyJson.eligibility_state,
      can_trade: safetyJson.can_trade,
      enforced_blocked: safetyJson.eligibility_state === 'BLOCKED' && safetyJson.can_trade === false
    };
  });
  results.tests.executionSafetyAdversarial = execSafetyTest;

  // 4. Time Machine Bounds & Frame Scrubbing Adversarial Test
  const timeMachineTest = await page.evaluate(async () => {
    const boundsRes = await fetch('/api/command-center/timemachine/bounds');
    const boundsJson = await boundsRes.json();
    if (!boundsJson.available) return { available: false };
    
    const earliest = boundsJson.earliest;
    const frameRes = await fetch('/api/command-center/timemachine/frame?at=' + encodeURIComponent(earliest));
    const frameJson = await frameRes.json();
    
    return {
      boundsAvailable: boundsJson.available,
      totalEvents: boundsJson.total_events,
      frameAvailable: frameJson.available,
      frameNodeCount: frameJson.nodes ? frameJson.nodes.length : 0
    };
  });
  results.tests.timeMachineAdversarial = timeMachineTest;

  // 5. Data Adversarial: Malformed / Missing Strategy ID Inspector
  let inspectorAdversarial = { error: 'not run' };
  try {
    inspectorAdversarial = await page.evaluate(async () => {
      const res = await fetch('/api/command-center/inspector/NONEXISTENT_STRATEGY_ID_999');
      let j = null;
      try { j = await res.json(); } catch (e) { j = null; }
      return {
        status: res.status,
        available: j ? j.available : null,
        body: j
      };
    });
  } catch (e) {
    inspectorAdversarial = { error: String(e) };
  }
  results.tests.inspectorAdversarial = inspectorAdversarial;

  results.consoleErrors = consoleErrors;
  results.pageErrors = pageErrors;

  fs.writeFileSync(REPORT_PATH, JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
  await browser.close();
}

run().catch(err => {
  console.error('Test run failed:', err);
  process.exit(1);
});
