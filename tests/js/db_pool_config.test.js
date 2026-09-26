/**
 * PG-POOL-CONFIG-001 (Lane I) — the diagnostics UI must surface the persisted
 * pool configuration, and a save must carry it to the backend.
 *
 * The defect (P1): `DatabaseConfig.pooling_enabled` had no consumer and
 * `provision_domain` built its PoolLimits from hard-coded call-site literals,
 * so an operator changing the pool size in the DATABASE TAB saved a row that
 * reached nothing.  The fix makes the persisted row the BASE pool limits and
 * gives the pool knobs a full UI surface.
 *
 * Pure-logic tests for the React feature `features/database`, in the same
 * style as database_console.test.js: they import the REAL frontend modules
 * (node strips the TS types; the only imports there are `import type`, erased
 * before resolution, so the `@/` alias never has to resolve here).
 *
 * Run with: node tests/js/db_pool_config.test.js
 */

const assert = require('assert');

const {
  optionsFromStatus,
  baselineOptionsFromManage,
  advancedPayload,
  advancedSpecs,
  optionRowState,
  OPTION_UNAVAILABLE,
} = require('../../frontend/src/features/database/model.ts');

let passed = 0;
let failed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
    console.log('  ok -', name);
  } catch (err) {
    failed++;
    console.error('  FAIL -', name);
    console.error('      ', err && err.message ? err.message : err);
  }
}

/** A /api/db/manage/status payload carrying an operator-set pool config. */
const MANAGE_WITH_POOL = {
  options: {
    command_timeout_sec: 30,
    connect_timeout_sec: 10,
    migrate_on_startup: true,
    pooling_enabled: true,
    pool_min_size: 2,
    pool_max_size: 7,
    pool_idle_timeout_sec: 90,
    pool_max_lifetime_sec: 1800,
    domain: 'audit',
    database: 'nexusdb',
  },
  domains: { audit: { migration_state: 'COMPLETE' } },
};

/** A backend that predates the pool knobs on the row (an older EXE). */
const MANAGE_LEGACY = {
  options: {
    command_timeout_sec: 30,
    connect_timeout_sec: 10,
    migrate_on_startup: true,
    pooling_enabled: true,
    domain: 'audit',
    database: 'nexusdb',
  },
  domains: { audit: { migration_state: 'COMPLETE' } },
};

// ---------------------------------------------------------------- harvest ---

test('optionsFromStatus: a persisted pool config is harvested as numbers', () => {
  const o = optionsFromStatus(MANAGE_WITH_POOL);
  assert.equal(o.live, true, 'the options block is live');
  assert.equal(o.pool_min_size, 2);
  assert.equal(o.pool_max_size, 7);
  assert.equal(o.pool_idle_timeout_sec, 90);
  assert.equal(o.pool_max_lifetime_sec, 1800);
});

test('optionsFromStatus: an UNSET pool knob is null, never zero-filled', () => {
  const o = optionsFromStatus(MANAGE_LEGACY);
  // null is a distinct fact from 0: 0 means open-lazy / never-reap, absent
  // means "the engine default applies". Zero-filling would silently report
  // a pool size the running pool was never given.
  assert.equal(o.pool_min_size, null);
  assert.equal(o.pool_max_size, null);
  assert.equal(o.pool_idle_timeout_sec, null);
  assert.equal(o.pool_max_lifetime_sec, null);
});

test('optionsFromStatus: an older build that reports no options block at all', () => {
  const o = optionsFromStatus({ domains: { audit: {} } });
  assert.equal(o.live, false);
  assert.equal(o.pool_min_size, null);
  assert.equal(o.pooling_enabled, null);
});

// ----------------------------------------------------------------- panel ----

test('optionRowState: an unset pool knob renders UNAVAILABLE, not 0', () => {
  const opts = optionsFromStatus(MANAGE_LEGACY);
  const row = optionRowState(opts, 'pool_min_size');
  assert.equal(row.reported, OPTION_UNAVAILABLE);
  assert.equal(row.unavailable, true);
});

test('optionRowState: a reported pool knob shows its value', () => {
  const opts = optionsFromStatus(MANAGE_WITH_POOL);
  assert.equal(optionRowState(opts, 'pool_max_size').reported, '7');
  assert.equal(optionRowState(opts, 'pool_max_size').unavailable, false);
});

test('advancedSpecs: the pool knobs are part of the panel, with backend bounds', () => {
  const keys = advancedSpecs().map((s) => s.key);
  assert.ok(keys.includes('pool_min_size'), 'pool_min_size missing from the panel');
  assert.ok(keys.includes('pool_max_size'), 'pool_max_size missing from the panel');
  assert.ok(keys.includes('pool_idle_timeout_sec'), 'pool_idle_timeout_sec missing');
  assert.ok(keys.includes('pool_max_lifetime_sec'), 'pool_max_lifetime_sec missing');

  const byKey = Object.fromEntries(advancedSpecs().map((s) => [s.key, s]));
  // Bounds mirror settings/provider_options.py exactly (contract §3.3).
  assert.deepEqual([byKey.pool_min_size.min, byKey.pool_min_size.max], [0, 64]);
  assert.deepEqual([byKey.pool_max_size.min, byKey.pool_max_size.max], [1, 128]);
  assert.deepEqual([byKey.pool_idle_timeout_sec.min, byKey.pool_idle_timeout_sec.max], [0, 86400]);
  assert.deepEqual([byKey.pool_max_lifetime_sec.min, byKey.pool_max_lifetime_sec.max], [0, 604800]);
  assert.equal(byKey.pool_min_size.kind, 'integer');
});

// ----------------------------------------------------------------- form -----

test('baselineOptionsFromManage: a persisted pool config seeds the form', () => {
  const base = baselineOptionsFromManage(MANAGE_WITH_POOL);
  assert.equal(base.pool_min_size, '2');
  assert.equal(base.pool_max_size, '7');
  assert.equal(base.pool_idle_timeout_sec, '90');
  assert.equal(base.pool_max_lifetime_sec, '1800');
});

test('baselineOptionsFromManage: an unset pool knob seeds EMPTY, never 0', () => {
  const base = baselineOptionsFromManage(MANAGE_LEGACY);
  assert.equal(base.pool_min_size, '');
  assert.equal(base.pool_max_size, '');
  // A 0 here would be sent to the backend as "open the pool lazily" — a real
  // setting the operator never chose.
  assert.equal(base.pool_idle_timeout_sec, '');
  assert.equal(base.pool_max_lifetime_sec, '');
});

// ----------------------------------------------------------------- save -----

test('advancedPayload: an edited pool size is carried to the backend', () => {
  const payload = advancedPayload({
    command_timeout_sec: 30,
    connect_timeout_sec: 10,
    migrate_on_startup: true,
    pooling_enabled: true,
    pool_min_size: 3,
    pool_max_size: 12,
  });
  assert.equal(payload.pool_min_size, 3);
  assert.equal(payload.pool_max_size, 12);
});

test('advancedPayload: an UNTOUCHED pool knob is omitted, never sent as 0', () => {
  // Blank means "leave the stored value alone". Sending a 0 would reset the
  // pool to open-lazy / never-reap on a save the operator thought was a no-op
  // for that control.
  const payload = advancedPayload({
    command_timeout_sec: 30,
    pool_min_size: '',
    pool_max_size: '',
  });
  assert.ok(!('pool_min_size' in payload), 'a blank pool_min_size must not be sent');
  assert.ok(!('pool_max_size' in payload), 'a blank pool_max_size must not be sent');
});

test('advancedPayload: an explicit 0 IS sent — it is a real setting', () => {
  const payload = advancedPayload({ pool_min_size: 0, pool_idle_timeout_sec: 0 });
  assert.equal(payload.pool_min_size, 0, '0 = open the pool lazily');
  assert.equal(payload.pool_idle_timeout_sec, 0, '0 = never reap idle connections');
});

test('advancedPayload: out-of-range pool sizes are refused before any POST', () => {
  // The panel's own specs bound these; the payload is the last line of
  // defence, and the backend rejects them too (bounds above).
  assert.throws(() => advancedPayload({ pool_min_size: 999 }), /must be/);
  assert.throws(() => advancedPayload({ pool_max_size: -1 }), /must be/);
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
