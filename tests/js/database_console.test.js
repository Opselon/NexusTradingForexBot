// Pure-logic tests for the /alt/database tab (React feature `features/database`).
// Run with: node tests/js/database_console.test.js
//
// Imports the REAL frontend module `frontend/src/features/database/uiLogic.ts`
// (node 24 strips the TS types; the only imports there are `import type`,
// erased before resolution, so the `@/` alias never has to resolve here).
// The payload fixtures below are VERBATIM live captures from
// GET /api/db/hygiene, GET /api/db/manage/status and GET /api/db/console/
// databases (2026-09-23), so a shape drift on either side fails this test.

const assert = require('assert');

const {
  isReachable,
  pickDefaultDatabase,
  consoleBlocker,
  defaultSqlForProvider,
  pageCaption,
  truncationNote,
  pendingSchema,
  providerHints,
  hygieneWorker,
  hygieneStorage,
  totalStorageBytes,
  hygienePlanRows,
  providerTruth,
  psycopgState,
  ageLabel,
} = require('../../frontend/src/features/database/uiLogic.ts');

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
    console.error('    ', err && err.message ? err.message : err);
  }
}

// ---------------------------------------------------------------------------
// Live /api/db/console/databases capture (postgresql provider, psycopg absent)
// ---------------------------------------------------------------------------
const LIVE_DATABASES = [
  { name: 'audit', provider: 'postgresql', database: 'nse_audit', server: 'localhost:5432', path: '', size_bytes: null, status: 'DRIVER_UNAVAILABLE: psycopg', hint: "Install the driver (`pip install 'nexus[postgres]'`, psycopg[binary]==3.2.*) or switch the active provider back to sqlite on the Manage tab." },
  { name: 'news', provider: 'postgresql', database: 'nse_audit', server: 'localhost:5432', path: '', size_bytes: null, status: 'DISCONNECTED', hint: 'Start the PostgreSQL server at localhost:5432 or switch the active provider back to sqlite on the Manage tab.' },
  { name: 'candle_intel', provider: 'postgresql', database: 'nse_audit', server: 'localhost:5432', path: '', size_bytes: null, status: 'DISCONNECTED' },
  { name: 'settings', provider: 'sqlite', database: 'app_settings.db', server: 'Local', path: 'C:\\Users\\x\\AppData\\Local\\NexusScalpEngine\\databases\\app_settings.db', size_bytes: 196608, status: 'CONNECTED', table_count: null, description: 'application settings / runtime configuration' },
];

// ---------------------------------------------------------------------------
// Live /api/db/hygiene capture (trimmed to the fields the tab renders)
// ---------------------------------------------------------------------------
const LIVE_HYGIENE = {
  status: {
    id: 1, state: 'IDLE', mode: 'AUDIT_ONLY', cycle: 0,
    last_scan: '2026-09-22T21:11:49.204286+00:00',
    last_cleanup: '2026-09-22T21:11:51.756395+00:00',
    last_success: '2026-09-22T21:11:51.756395+00:00',
    last_failure: '',
    execution_mode: 'PAPER',
    apply_deletes: false,
    managed_databases: ['audit', 'news', 'candle_intel'],
    db_sizes: {
      audit: { bytes: 425672704, wal_bytes: 791072 },
      news: { bytes: 241434624, wal_bytes: 107152 },
      'candle_intel': { bytes: 10969088, wal_bytes: 0 },
    },
  },
  plans: {
    audit: { database: 'audit', plan: { database: 'audit', generated_at: '2026-09-22T21:29:59.266641+00:00', tables_scanned: 70, rows_scanned: 0, duplicates_found: 1, exact_duplicates: 0, orphans_found: 3870, retention_candidates: 1, delete_candidates: 0, blocked: 1 } },
    news: { database: 'news', plan: { database: 'news', generated_at: '2026-09-22T21:29:59.967738+00:00', tables_scanned: 22, rows_scanned: 0, duplicates_found: 21501, exact_duplicates: 21498, orphans_found: 59, retention_candidates: 1, delete_candidates: 0, blocked: 21499 } },
    'candle_intel': { database: 'candle_intel', plan: { tables_scanned: 14, duplicates_found: 0, orphans_found: 0, blocked: 0 } },
  },
  quarantine: {},
};

// Live /api/db/manage/status capture
const LIVE_MANAGE = {
  success: true,
  provider: 'postgresql',
  supported_providers: ['sqlite', 'postgresql'],
  overall: 'Warning',
  domains: { audit: { domain: 'audit', provider: 'postgresql', status: 'DISCONNECTED', connected: false } },
  postgres: null,
  password_set: false,
  postgresql_driver_available: false,
  hints: [
    'Active provider is postgresql but psycopg is not installed: run `pip install \'nexus[postgres]\'` or switch back to sqlite.',
    'No PostgreSQL connection configuration is stored — fill in the form below and Save config before migrating.',
    '',
    42,
  ],
};

// Live /api/db/status capture
const LIVE_STATUS = {
  available: true,
  databases: {
    audit: { schema_version: 7, expected_version: 9, migration_state: 'DB_MIGRATION_PENDING', pending_count: 2, integrity: 'ok', tamper_detected: false },
    news: { schema_version: 2, expected_version: 2, migration_state: 'DB_MIGRATION_NOT_REQUIRED', pending_count: 0, integrity: 'ok', tamper_detected: false },
    candle_intel: { schema_version: 2, expected_version: 2, migration_state: 'DB_MIGRATION_NOT_REQUIRED', pending_count: 0, integrity: 'ok', tamper_detected: true },
  },
};

// ---------------------------------------------------------------------------
// pickDefaultDatabase — the 2026-09-23 regression
// ---------------------------------------------------------------------------
test('pickDefaultDatabase: prefers the first REACHABLE database, not list[0]', () => {
  assert.equal(pickDefaultDatabase(LIVE_DATABASES), 'settings');
});

test('pickDefaultDatabase: returns the first entry when nothing is reachable', () => {
  const allDown = LIVE_DATABASES.map((d) => ({ ...d, status: 'DISCONNECTED' }));
  assert.equal(pickDefaultDatabase(allDown), 'audit');
});

test('pickDefaultDatabase: first reachable wins over an unreachable prefix', () => {
  const list = [
    { name: 'a', status: 'DISCONNECTED' },
    { name: 'b', status: 'CONNECTED' },
    { name: 'c', status: 'CONNECTED' },
  ];
  assert.equal(pickDefaultDatabase(list), 'b');
});

test('pickDefaultDatabase: empty / undefined list is null, never a crash', () => {
  assert.equal(pickDefaultDatabase([]), null);
  assert.equal(pickDefaultDatabase(undefined), null);
  assert.equal(pickDefaultDatabase(null), null);
});

test('isReachable: only the exact CONNECTED status counts', () => {
  assert.equal(isReachable(LIVE_DATABASES[3]), true);
  assert.equal(isReachable(LIVE_DATABASES[0]), false);
  assert.equal(isReachable(LIVE_DATABASES[1]), false);
  assert.equal(isReachable(undefined), false);
  assert.equal(isReachable(null), false);
});

// ---------------------------------------------------------------------------
// consoleBlocker — honest blocker with the backend's own hint
// ---------------------------------------------------------------------------
test('consoleBlocker: null for a reachable database', () => {
  assert.equal(consoleBlocker(LIVE_DATABASES[3]), null);
  assert.equal(consoleBlocker(null), null);
});

test('consoleBlocker: names the target, status and passes the hint through', () => {
  const b = consoleBlocker(LIVE_DATABASES[0]);
  assert.ok(b, 'expected a blocker for the unreachable audit database');
  assert.ok(b.message.includes('audit'), b.message);
  assert.ok(b.message.toLowerCase().includes('driver unavailable'), b.message);
  assert.ok(b.message.includes('nse_audit'), b.message);
  assert.ok(b.hint && b.hint.includes('nexus[postgres]'), 'hint must survive to the UI');
});

test('consoleBlocker: a database with no hint still explains itself', () => {
  const b = consoleBlocker(LIVE_DATABASES[2]);
  assert.ok(b, 'expected a blocker');
  assert.ok(b.message.includes('candle_intel'));
  assert.equal(b.hint, undefined);
});

// ---------------------------------------------------------------------------
// Dialect-aware starter SQL + captions
// ---------------------------------------------------------------------------
test('defaultSqlForProvider: information_schema for postgres, sqlite_master otherwise', () => {
  assert.ok(defaultSqlForProvider('postgresql').includes('information_schema'));
  assert.ok(defaultSqlForProvider('sqlite').includes('sqlite_master'));
  assert.ok(defaultSqlForProvider(undefined).includes('sqlite_master'));
  assert.ok(defaultSqlForProvider(null).includes('sqlite_master'));
  assert.ok(defaultSqlForProvider('POSTGRESQL').includes('information_schema'));
});

test('pageCaption: 1-based inclusive range, honest empty page', () => {
  assert.equal(pageCaption(0, 100), 'rows 1–100');
  assert.equal(pageCaption(100, 37), 'rows 101–137');
  assert.equal(pageCaption(200, 0), 'empty page (offset 200)');
});

test('truncationNote: only the backend flag produces a note, with its cap', () => {
  assert.equal(truncationNote(false, 500), '');
  assert.equal(truncationNote(undefined, 500), '');
  assert.equal(truncationNote(true, 500), ' · truncated at the 500-row cap');
  assert.equal(truncationNote(true, undefined), ' · truncated at the row cap');
});

// ---------------------------------------------------------------------------
// pendingSchema / providerHints
// ---------------------------------------------------------------------------
test('pendingSchema: sums pending, lists the domains behind and tampered', () => {
  const p = pendingSchema(LIVE_STATUS);
  assert.equal(p.pending, 2);
  assert.deepEqual(p.behind, ['audit']);
  assert.deepEqual(p.tampered, ['candle_intel']);
});

test('pendingSchema: nothing reported stays null (never 0 pretending to be data)', () => {
  const p = pendingSchema(undefined);
  assert.equal(p.pending, null);
  assert.deepEqual(p.behind, []);
  assert.deepEqual(p.tampered, []);
  const empty = pendingSchema({ available: true, databases: {} });
  assert.equal(empty.pending, 0, 'a real empty payload reports a real 0');
});

test('providerHints: keeps strings, drops blanks and non-strings', () => {
  const hints = providerHints(LIVE_MANAGE);
  assert.equal(hints.length, 2);
  assert.ok(hints[0].includes('psycopg is not installed'));
  assert.ok(hints[1].includes('No PostgreSQL connection configuration'));
  assert.deepEqual(providerHints(undefined), []);
  assert.deepEqual(providerHints({ success: true }), []);
});

// ---------------------------------------------------------------------------
// Hygiene derivations
// ---------------------------------------------------------------------------
test('hygieneWorker: exposes state/mode/cycle/last-success verbatim', () => {
  const w = hygieneWorker(LIVE_HYGIENE);
  assert.equal(w.state, 'IDLE');
  assert.equal(w.mode, 'AUDIT_ONLY');
  assert.equal(w.executionMode, 'PAPER');
  assert.equal(w.cycle, 0);
  assert.equal(w.lastSuccess, '2026-09-22T21:11:51.756395+00:00');
  assert.equal(w.lastFailure, '');
  assert.deepEqual(w.managed, ['audit', 'news', 'candle_intel']);
  assert.equal(hygieneWorker(undefined), null);
  assert.equal(hygieneWorker({}), null);
});

test('hygieneStorage: rows sorted largest-first with WAL alongside', () => {
  const rows = hygieneStorage(LIVE_HYGIENE);
  assert.deepEqual(rows.map((r) => r.database), ['audit', 'news', 'candle_intel']);
  assert.equal(rows[0].bytes, 425672704);
  assert.equal(rows[0].walBytes, 791072);
  assert.equal(rows[2].bytes, 10969088);
  assert.deepEqual(hygieneStorage(undefined), []);
});

test('totalStorageBytes: sums every reported database, null when nothing reported', () => {
  assert.equal(totalStorageBytes(LIVE_HYGIENE), 425672704 + 241434624 + 10969088);
  assert.equal(totalStorageBytes(undefined), null);
  assert.equal(totalStorageBytes({ status: {} }), null);
});

test('hygienePlanRows: unwraps {db: {plan: {...}}} into flat rows', () => {
  const rows = hygienePlanRows(LIVE_HYGIENE);
  assert.equal(rows.length, 3);
  const news = rows.find((r) => r.database === 'news');
  assert.equal(news.tablesScanned, 22);
  assert.equal(news.duplicates, 21501);
  assert.equal(news.exactDuplicates, 21498);
  assert.equal(news.orphans, 59);
  assert.equal(news.blocked, 21499);
  assert.equal(news.generatedAt, '2026-09-22T21:29:59.967738+00:00');
  const ci = rows.find((r) => r.database === 'candle_intel');
  assert.equal(ci.tablesScanned, 14);
  assert.equal(ci.duplicates, 0, 'a real 0 stays a real 0');
  assert.equal(ci.orphans, 0);
  // fields the payload does not carry stay null — never invented zeros
  assert.equal(ci.generatedAt, '');
  assert.deepEqual(hygienePlanRows(undefined), []);
});

// ---------------------------------------------------------------------------
// Provider truth — the 2026-09-23 complaint: "shows postgres, data is sqlite"
// ---------------------------------------------------------------------------
const MEASURED_TRUTH = {
  configured: 'postgresql',
  effective: 'sqlite',
  mismatch: true,
  pg_reachable: false,
  pg_target: 'localhost:5432/nse_audit',
  pg_error: 'PostgreSQL at localhost:5432/nse_audit did not answer the connection probe.',
  evidence: [
    { name: 'audit', file: 'audit.db', path: 'C:/x/artifacts/audit.db', bytes: 425725952, age_seconds: 37, mtime_utc: '2026-09-23T01:42:06+00:00', active: true },
    { name: 'candle_intel', file: 'candle_intel.db', path: 'C:/x/artifacts/candle_intel.db', bytes: 10969088, age_seconds: 1382400, mtime_utc: '2026-09-07T05:10:05+00:00', active: false },
  ],
  note: 'Configured provider is postgresql but localhost:5432/nse_audit did not answer; local SQLite files were written in the last 10 minutes (see evidence) — the data on this tab comes from sqlite.',
  measured_at: '2026-09-23T01:50:00+00:00',
};

test('providerTruth: measured payload is rendered verbatim, source=backend', () => {
  const t = providerTruth({ provider: 'postgresql', provider_truth: MEASURED_TRUTH });
  assert.ok(t, 'expected a truth object');
  assert.equal(t.source, 'backend');
  assert.equal(t.configured, 'postgresql');
  assert.equal(t.effective, 'sqlite');
  assert.equal(t.mismatch, true);
  assert.equal(t.pgTarget, 'localhost:5432/nse_audit');
  assert.ok(t.note.includes('did not answer'), t.note);
  assert.equal(t.evidence.length, 2);
  assert.equal(t.evidence[0].active, true);
  assert.equal(t.evidence[1].active, false);
});

test('providerTruth: null/undefined payload is null, never a crash', () => {
  assert.equal(providerTruth(null), null);
  assert.equal(providerTruth(undefined), null);
});

test('providerTruth: OLD backend + 0 connected domains -> effective sqlite (derived)', () => {
  const manage = {
    provider: 'postgresql',
    domains: {
      audit: { connected: false, status: 'DISCONNECTED' },
      news: { connected: false },
      candle_intel: { connected: false },
    },
  };
  const t = providerTruth(manage);
  assert.equal(t.source, 'derived', 'without provider_truth the claim must label itself');
  assert.equal(t.mismatch, true);
  assert.equal(t.effective, 'sqlite');
  assert.equal(t.configured, 'postgresql');
  assert.ok(t.note.includes('0 of 3'), t.note);
  assert.ok(t.note.includes('local SQLite'), t.note);
  assert.deepEqual(t.evidence, [], 'derived claims carry no file evidence');
});

test('providerTruth: partial connectivity is reported as mixed, not as postgres', () => {
  const manage = { provider: 'postgresql', domains: { a: { connected: true }, b: { connected: false } } };
  const t = providerTruth(manage);
  assert.equal(t.mismatch, true);
  assert.equal(t.effective, 'mixed');
  assert.ok(t.note.includes('only 1 of 2'), t.note);
});

test('providerTruth: all domains connected -> postgres, no mismatch', () => {
  const manage = { provider: 'postgresql', domains: { a: { connected: true }, b: { connected: true } } };
  const t = providerTruth(manage);
  assert.equal(t.effective, 'postgresql');
  assert.equal(t.mismatch, false);
  assert.equal(t.source, 'derived');
});

test('providerTruth: configured sqlite is not a mismatch', () => {
  const t = providerTruth({ provider: 'sqlite', domains: {} });
  assert.equal(t.effective, 'sqlite');
  assert.equal(t.mismatch, false);
});

test('providerTruth: postgresql with no domain rows at all stays unknown, not sqlite', () => {
  const t = providerTruth({ provider: 'postgresql' });
  assert.equal(t.effective, 'unknown');
  assert.equal(t.mismatch, false, 'no data means no claim in either direction');
});

test('psycopgState: tri-state — the field must never default to "installed"', () => {
  assert.deepEqual(psycopgState(true), { label: 'installed', tone: 'good', sub: 'required while the provider is postgresql' });
  assert.equal(psycopgState(false).label, 'missing');
  assert.equal(psycopgState(false).tone, 'bad');
  assert.ok(psycopgState(false).sub.includes('psycopg absent'));
  assert.equal(psycopgState(undefined).label, 'not reported');
  assert.equal(psycopgState(undefined).tone, 'neutral');
  assert.equal(psycopgState(null).label, 'not reported');
});

test('ageLabel: evidence recency reads as a human sentence', () => {
  assert.equal(ageLabel(3), 'just now');
  assert.equal(ageLabel(47), '47s ago');
  assert.equal(ageLabel(90), '2m ago');
  assert.equal(ageLabel(7200), '2h ago');
  assert.equal(ageLabel(200000), '2d ago');
  assert.equal(ageLabel(undefined), 'age unknown');
  assert.equal(ageLabel(-5), 'age unknown');
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
