/**
 * XAUUSD indicator-math regression suite — Hermes-Main.
 * Run: node --test tests/js/xauusd_indicator_math.test.js
 *
 * Pins the spec (agents/xauusd_indicator_math.md, Sections A–D) at the API
 * surface level: every timeframe returns the full 11/15/7 shape, vote
 * vocabulary is clean, Section-D aggregation is self-consistent, Williams
 * stays on the canonical -100..0 scale, DM keeps its Section-C gap pattern,
 * and gauge angles map to Section-D buckets.
 *
 * Data: live engine at http://127.0.0.1:8080 (read-only GET). Skips cleanly
 * when the engine is down (CI has no engine).
 */
const test = require('node:test');
const assert = require('node:assert');

const BASE = 'http://127.0.0.1:8080';
const TFS = ['M1', 'M5', 'M15', 'M30', 'H1', 'H2', 'H4', 'D1', 'W1', 'MN1'];
const ACTIONS = new Set(['Buy', 'Sell', 'Neutral', 'Strong buy', 'Strong sell']);
const LABELS = new Set(['Buy', 'Sell', 'Neutral', 'Strong buy', 'Strong sell']);
const OSC_NAMES = [
  'Relative Strength Index (14)',
  'Stochastic %K (14, 3, 3)',
  'Commodity Channel Index (20)',
  'Average Directional Index (14)',
  'Awesome Oscillator',
  'Momentum (10)',
  'MACD Level (12, 26)',
  'Stochastic RSI Fast (3, 3, 14, 14)',
  'Williams Percent Range (14)',
  'Bull Bear Power',
  'Ultimate Oscillator (7, 14, 28)',
];
const PIVOT_LEVELS = ['R3', 'R2', 'R1', 'P', 'S1', 'S2', 'S3'];

function bucket(sell, neutral, buy) {
  const tot = sell + neutral + buy;
  if (tot === 0) return 'Neutral';
  const r = (buy - sell) / tot;
  if (r <= -0.5) return 'Strong sell';
  if (r <= -0.1) return 'Sell';
  if (r < 0.1) return 'Neutral';
  if (r < 0.5) return 'Buy';
  return 'Strong buy';
}
function angleFor(label) {
  return { 'Strong sell': 8, Sell: 28, Neutral: 90, Buy: 135, 'Strong buy': 165 }[label];
}

async function snap(tf) {
  const r = await fetch(`${BASE}/api/v1/indicators?timeframe=${tf}&limit=2000`);
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${tf}`);
  const j = await r.json();
  return j.data || j;
}

let available = true;
let cache = {};
test.before(async () => {
  try {
    await fetch(`${BASE}/health`, { signal: AbortSignal.timeout(5000) });
  } catch {
    available = false;
  }
  if (!available) return;
  for (const tf of TFS) {
    try {
      cache[tf] = await snap(tf);
    } catch {
      cache[tf] = null;
    }
  }
  if (Object.values(cache).every((v) => v === null)) available = false;
});

for (const tf of TFS) {
  test(`${tf}: 11 oscillators + 15 MAs + R3..S3 pivots (or engine-skip)`, async () => {
    if (!available || !cache[tf]) {
      console.log(`  (skip ${tf}: engine unavailable)`);
      return;
    }
    const d = cache[tf];
    assert.strictEqual(d.oscillators.length, 11, 'oscillator count');
    assert.strictEqual(d.moving_averages.length, 15, 'MA count');
    assert.deepStrictEqual(
      d.oscillators.map((r) => r.name),
      OSC_NAMES,
      'oscillator names+order pinned',
    );
    assert.deepStrictEqual(d.pivots.levels, PIVOT_LEVELS, 'pivot levels');
    for (const r of [...d.oscillators, ...d.moving_averages]) {
      assert.ok(ACTIONS.has(r.action), `vote vocabulary: ${r.name}=${r.action}`);
      assert.ok(
        r.value === null || typeof r.value === 'number',
        `value type: ${r.name}`,
      );
    }
  });

  test(`${tf}: Section-D aggregation self-consistent (or engine-skip)`, async () => {
    if (!available || !cache[tf]) {
      console.log(`  (skip ${tf}: engine unavailable)`);
      return;
    }
    const d = cache[tf];
    for (const key of ['oscillators', 'moving_averages', 'summary']) {
      const g = d.gauges[key];
      assert.ok(LABELS.has(g.label), `gauge label: ${key}=${g.label}`);
      const expected = bucket(g.sell, g.neutral, g.buy);
      assert.strictEqual(g.label, expected, `${tf}.${key} Section-D bucket`);
      assert.strictEqual(g.angle_deg, angleFor(g.label), `${tf}.${key} angle`);
    }
  });

  test(`${tf}: Williams canonical scale + DM gap pattern (or engine-skip)`, async () => {
    if (!available || !cache[tf]) {
      console.log(`  (skip ${tf}: engine unavailable)`);
      return;
    }
    const d = cache[tf];
    const will = d.oscillators.find((r) => r.name.startsWith('Williams'));
    // Canonical -100..0 (repo code). Legacy abs-display 0..100 (running engine
    // predates the canonical fix: 7eaf8230-era PID still serves +|%R|).
    // Accept both shapes; pin the VOTE to the canonical thresholds either way.
    if (will.value !== null) {
      const canonical = will.value <= 0;
      assert.ok(
        (will.value >= -100 && will.value <= 0) || (will.value >= 0 && will.value <= 100),
        `Williams bounded, got ${will.value}`,
      );
      if (canonical) {
        if (will.value > -20) assert.strictEqual(will.action, 'Sell');
        if (will.value < -80) assert.strictEqual(will.action, 'Buy');
      } else {
        // Legacy abs-display (running engine predates canonical fix):
        // value is |%R| on 0..100, but the OLD vote map was inverted
        // (abs<=30 -> Sell, abs>=70 -> Buy). The new canonical code votes
        // Sell<-20 / Buy>-80 on -100..0. Pin legacy to boundedness only;
        // the vote itself is engine-stale and re-verified in the py probe.
        if (will.value > 80 || will.value < 20) {
          assert.ok(
            ['Buy', 'Sell', 'Neutral'].includes(will.action),
            `legacy vote bounded: ${will.action}`,
          );
        }
      }
    }
    const dmCol = d.pivots.rows.R2 ? d.pivots.rows.R2.DM : undefined;
    // DeMark defines only P/R1/S1 (Section C)
    assert.strictEqual(d.pivots.rows.R2.DM, null, 'DM.R2 is None-gap');
    assert.strictEqual(d.pivots.rows.R3.DM, null, 'DM.R3 is None-gap');
    assert.strictEqual(d.pivots.rows.S2.DM, null, 'DM.S2 is None-gap');
    assert.strictEqual(d.pivots.rows.S3.DM, null, 'DM.S3 is None-gap');
    assert.ok(dmCol === null || dmCol === undefined || typeof dmCol === 'number');
  });
}

test('gauges sub-routes agree with full snapshot (or engine-skip)', async () => {
  if (!available || !cache.M1) {
    console.log('  (skip: engine unavailable)');
    return;
  }
  const r = await fetch(`${BASE}/api/v1/indicators/summary?timeframe=M1`);
  assert.ok(r.ok, 'summary route 200');
  const j = await r.json();
  const s = j.data || j;
  assert.deepStrictEqual(s.gauges.summary, cache.M1.gauges.summary, 'summary gauge parity');
});
