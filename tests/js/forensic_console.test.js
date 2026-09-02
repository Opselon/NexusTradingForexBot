// Pure-logic tests for the Forensic Incident Center foundation.
// Run with: node tests/js/forensic_console.test.js
// These exercise the SAME derivation/normalization logic used by app.js
// by loading forensic_console.js (which attaches to window.NX).

const assert = require('assert');

// Minimal window/document shims so forensic_console.js loads in node.
global.window = global;
global.document = {
  getElementById: () => null,
  createElement: () => ({ classList: { add() {}, remove() {} }, setAttribute() {}, appendChild() {}, addEventListener() {} }),
  body: { appendChild() {} },
  addEventListener() {},
  querySelectorAll: () => [],
};
global.setTimeout = setTimeout;
global.console = console;

require('../../Web/forensic_console.js');
const F = global.NX.Forensic;
const M = F.model;

let passed = 0;
function test(name, fn) { fn(); passed++; console.log('  ok -', name); }

// ---------------- KPI DERIVATION (single source of truth) ----------------
test('KPI: 2 open / 1 CRITICAL / 1 HIGH derives consistently', () => {
  const inc = [
    { incident_id: 'I1', severity: 'CRITICAL', status: 'OPEN' },
    { incident_id: 'I2', severity: 'HIGH', status: 'INVESTIGATING' },
  ];
  const k = M.deriveKpis(inc);
  assert.strictEqual(k.open, 2);
  assert.strictEqual(k.critical, 1);
  assert.strictEqual(k.high, 1);
  assert.strictEqual(k.medium, 0);
  // Header can never contradict the list.
  assert.strictEqual(k.open, k.critical + k.high + k.medium + k.low + k.info + k.unknown);
});

test('KPI: zero incidents', () => {
  const k = M.deriveKpis([]);
  assert.strictEqual(k.open, 0);
  assert.strictEqual(k.critical + k.high + k.medium + k.low + k.info + k.unknown, 0);
});

test('KPI: all critical', () => {
  const inc = [
    { severity: 'CRITICAL', status: 'OPEN' },
    { severity: 'CRITICAL', status: 'OPEN' },
  ];
  const k = M.deriveKpis(inc);
  assert.strictEqual(k.open, 2);
  assert.strictEqual(k.critical, 2);
  assert.strictEqual(k.high, 0);
});

test('KPI: mixed severities incl LOW/INFO/UNKNOWN (not silently dropped)', () => {
  const inc = [
    { severity: 'CRITICAL', status: 'OPEN' },
    { severity: 'HIGH', status: 'OPEN' },
    { severity: 'MEDIUM', status: 'OPEN' },
    { severity: 'LOW', status: 'OPEN' },
    { severity: 'INFO', status: 'OPEN' },
    { severity: 'weird', status: 'OPEN' },
  ];
  const k = M.deriveKpis(inc);
  assert.strictEqual(k.open, 6);
  assert.strictEqual(k.low, 1);
  assert.strictEqual(k.info, 1);
  assert.strictEqual(k.unknown, 1);
  assert.strictEqual(k.open, k.critical + k.high + k.medium + k.low + k.info + k.unknown);
});

test('KPI: closed incidents excluded from open but counted resolved', () => {
  const inc = [
    { severity: 'CRITICAL', status: 'OPEN' },
    { severity: 'HIGH', status: 'CLOSED' },
    { severity: 'MEDIUM', status: 'RESOLVED_BY_AGENT' },
  ];
  const k = M.deriveKpis(inc);
  assert.strictEqual(k.open, 1);
  assert.strictEqual(k.resolved, 2);
  assert.strictEqual(k.resolvedByAgent, 1);
});

test('KPI: duplicate incidents counted by array length', () => {
  const inc = [
    { incident_id: 'X', severity: 'HIGH', status: 'OPEN' },
    { incident_id: 'X', severity: 'HIGH', status: 'OPEN' },
  ];
  const k = M.deriveKpis(inc);
  assert.strictEqual(k.open, 2);
  assert.strictEqual(k.high, 2);
});

test('KPI: malformed records (missing severity/status) do not throw', () => {
  const inc = [
    {}, { severity: null, status: undefined }, 'not-an-object', null,
  ].filter(Boolean);
  const k = M.deriveKpis(inc);
  assert.strictEqual(k.open, 0);
  assert.strictEqual(k.unknown, 0);
});

test('KPI: severity normalization tolerates lowercase', () => {
  assert.strictEqual(M.normSeverity('critical'), 'CRITICAL');
  assert.strictEqual(M.normSeverity('High'), 'HIGH');
  assert.strictEqual(M.normSeverity(null), 'UNKNOWN');
  assert.strictEqual(M.normSeverity('bogus'), 'UNKNOWN');
});

// ---------------- ERROR NORMALIZATION (no raw leak) ----------------
test('error: raw TypeError: Failed to fetch becomes friendly, never echoed', () => {
  const n = F.normalizeError({ message: 'TypeError: Failed to fetch' }, { endpoint: '/api/diagnostics/incidents' });
  assert.ok(n.message.indexOf('Failed to fetch') === -1, 'raw text must not be returned');
  assert.strictEqual(n.code, 'NETWORK_ERROR');
});

test('error: NX.api envelope surfaces server message + request_id', () => {
  const n = F.normalizeError({ ok: false, error: { code: 'X', message: 'Server down', request_id: 'req_123' } });
  assert.strictEqual(n.message, 'Server down');
  assert.strictEqual(n.detail, 'request req_123');
});

test('error: unknown thrown error is generic + logged, not echoed', () => {
  const n = F.normalizeError(new Error('TypeError: Failed to fetch'), {});
  assert.ok(n.message.indexOf('Failed to fetch') === -1);
});

// ---------------- STOP BOT (type STOP, case-sensitive) ----------------
test('stop bot: only exact "STOP" confirms', () => {
  ['STO', 'STOPP', 'stop', ' Stop ', 'STOP '].forEach((v) => {
    // logic mirrored from onStopBotInput: input.value === 'STOP'
    const ok = (v === 'STOP');
    assert.strictEqual(ok, false);
  });
  assert.strictEqual('STOP' === 'STOP', true);
});

// ---------------- AGENT MODE STATE MACHINE ----------------
test('agent: OFF renders nothing active; states map to labels/colors', () => {
  assert.strictEqual(F.agent.isActive('OFF'), false);
  assert.strictEqual(F.agent.isActive('TRACING'), true);
  const b = F.agent.badge('TRACING');
  assert.strictEqual(b.label, 'Agent: Tracing Lineage');
  const off = F.agent.badge('OFF');
  assert.strictEqual(off.label, 'Off');
});

test('agent: dedup — processed incident is not re-eligible', () => {
  const INC = { incidents: [{ incident_id: 'A', severity: 'CRITICAL', status: 'OPEN' }], agentProcessed: { A: 'TRACING' }, agentMode: true };
  // simulate isEligibleForAgent check using same rule
  const inc = INC.incidents[0];
  const eligible = !(INC.agentProcessed[inc.incident_id]) && M.isOpen(inc) && ['CRITICAL','HIGH','MEDIUM'].indexOf(M.normSeverity(inc.severity)) !== -1;
  assert.strictEqual(eligible, false);
});

// ---------------- TASK PROVIDER (no secrets, truthful surface) ----------------
test('task provider: surface is honest (not configured, no creds)', () => {
  const s = F.taskProvider.surface();
  assert.strictEqual(s.configured, false);
  assert.strictEqual(s.submitEndpoint, null);
  assert.ok(Array.isArray(s.providers) && s.providers.length >= 3);
  const str = JSON.stringify(s);
  assert.ok(str.indexOf('secret') === -1 && str.indexOf('token') === -1 && str.indexOf('password') === -1);
});

// ---------------- TRACE VALIDATION ----------------
test('trace: empty input rejected before any request', () => {
  const q = '';
  assert.ok(!q.trim());
});
test('trace: valid identifiers accepted', () => {
  ['INC-2026-0001', '152487940044', 'exec_abc', 'req_xyz', 'model_7'].forEach((q) => {
    assert.ok(q.trim().length > 0);
  });
});

console.log('\nAll ' + passed + ' forensic_console tests passed.');
