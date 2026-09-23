/**
 * Regression: the research PLAYBOOK must track the backend it documents.
 *
 * These tests re-read the Python sources (evidence.py, models.py,
 * discovery.py, scoring.py, robustness.py, splitting.py) and fail if the
 * frontend handbook's verbatim constants/order drift from them. The
 * handbook leaves are plain .ts modules whose cross-module links are
 * `import type` only, so Node 24's type stripping can load them directly
 * (same pattern as pro_risk_viz.test.mjs).
 *
 * Run: node tests/js/research_handbook.test.js  (CI: Frontend JS Unit Tests)
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { registerHooks } = require("node:module");

/**
 * The handbook is authored bundler-style (extensionless specifiers, a
 * `gates/` directory import) for Vite/tsc. Register a sync resolve hook so
 * Node's ESM loader can load the same files during this test: try the bare
 * specifier, then `<spec>.ts`, then `<spec>/index.ts`.
 */
registerHooks({
  resolve(specifier, context, nextResolve) {
    try {
      return nextResolve(specifier, context);
    } catch (err) {
      const relative = specifier.startsWith("./") || specifier.startsWith("../");
      const bareFileish = !/\.[cm]?[jt]sx?$/.test(specifier);
      if (relative && bareFileish) {
        for (const cand of [specifier + ".ts", specifier + "/index.ts"]) {
          try {
            return nextResolve(cand, context);
          } catch {
            /* try next candidate */
          }
        }
      }
      throw err;
    }
  },
});

const ROOT = path.resolve(__dirname, "..", "..");
const HANDBOOK = path.join(ROOT, "frontend", "src", "features", "research", "handbook");

const load = (rel) => import(pathToFileURL(path.join(HANDBOOK, rel)).href);
const readPy = (rel) => fs.readFileSync(path.join(ROOT, "src", "nexus_scalp", "research", rel), "utf8");

/** Extract a `NAME ... = ( … )` / `frozenset({ … })` definition block (up to the
 * first blank line) and pull its GateType.<MEMBER> references in source order. */
function pyMemberBlock(py, header) {
  const idx = py.search(header); // header is a line-anchored RegExp
  assert.ok(idx >= 0, `${header} not found`);
  // evidence.py may be CRLF: split at the first blank line, either ending.
  const def = py.slice(idx).split(/\r?\n\r?\n/)[0];
  return [...def.matchAll(/GateType\.([A-Z_]+)/g)].map((m) => m[1]);
}

test("gate chain order matches evidence.py::GATE_CHAIN verbatim", async () => {
  const gates = await load(path.join("gates", "index.ts"));
  const pyChain = pyMemberBlock(readPy("evidence.py"), /^GATE_CHAIN/m);
  assert.equal(pyChain.length, 6, "expected a six-gate chain in evidence.py");
  assert.deepEqual([...gates.GATE_CHAIN], pyChain, "frontend GATE_CHAIN drifted from evidence.py");
  assert.equal(gates.GATE_CHAIN.length, 6, "expected a six-gate chain");
});

test("terminal statuses and failure classes match evidence.py", async () => {
  const gates = await load(path.join("gates", "index.ts"));
  const py = readPy("evidence.py");

  assert.ok(py.includes("_TERMINAL_GATE"), "evidence.py lost _TERMINAL_GATE");
  for (const s of gates.TERMINAL_GATE_STATUSES) {
    assert.ok(py.includes(`"${s}"`) || py.includes(`GateStatus.${s}`), `terminal status ${s} missing from evidence.py`);
  }
  const classBlock = py.match(/class FailureClass[\s\S]*?StrEnum\)?\s*:\s*([\s\S]*?)$/);
  for (const c of gates.FAILURE_CLASSES) {
    assert.ok(py.includes(`"${c}"`), `failure class ${c} missing from evidence.py`);
  }
  void classBlock;
});

test("required-gates set matches REQUIRED_GATES_FOR_VALIDATION", async () => {
  const gates = await load(path.join("gates", "index.ts"));
  const py = readPy("evidence.py");
  const pyReq = pyMemberBlock(py, /^REQUIRED_GATES_FOR_VALIDATION/m);
  assert.equal(pyReq.length, 5, "expected five required gates");
  // frozenset literal — semantics is set membership, not source order.
  assert.deepEqual([...gates.REQUIRED_FOR_VALIDATED].sort(), pyReq.sort());
});

test("lifecycle states match models.py::CandidateLifecycle", async () => {
  const lc = await load("lifecycle.ts");
  const py = readPy("models.py");
  const start = py.indexOf("class CandidateLifecycle");
  assert.ok(start >= 0, "CandidateLifecycle enum not found in models.py");
  const nextClass = py.indexOf("\nclass ", start + 10);
  const block = py.slice(start, nextClass === -1 ? undefined : nextClass);
  // enum members only: 4-space indent, quoted value (docstring lines never match).
  const pyStates = [...block.matchAll(/^\s{4}([A-Z_]+)\s*=\s*"([A-Z_]+)"/gm)].map((x) => x[1]);
  assert.deepEqual([...lc.STATE_IDS], pyStates, "frontend STATE_IDS drifted from models.py");
  assert.equal(lc.STATE_IDS.length, 16, "expected 16 lifecycle states");
});

test("trade-eligible + terminal sets match lifecycle.py", async () => {
  const lc = await load("lifecycle.ts");
  const py = readPy("lifecycle.py");
  assert.ok(py.includes("VALIDATED") && py.includes("SHADOW") && py.includes("ACTIVE"));
  // approve_for_live's refusal text names the two legal sources.
  assert.match(py, /SHADOW/, "lifecycle.py no longer names SHADOW");
  assert.ok(py.includes("_TRANSITIONS"), "lifecycle.py lost _TRANSITIONS");
  for (const t of lc.TERMINAL_STATES) {
    assert.ok(py.includes(`"${t}"`) || py.includes(`${t}`), `terminal state ${t} missing`);
  }
  assert.deepEqual([...lc.TRADE_ELIGIBLE], ["VALIDATED", "SHADOW", "ACTIVE"]);
});

test("discovery floors match discovery.py", async () => {
  const d = await load("discovery.ts");
  const py = readPy("discovery.py");
  const num = (name) => {
    const m = py.match(new RegExp(`${name}[^=\\n]*=\\s*([0-9.]+)`));
    assert.ok(m, `${name} not found in discovery.py`);
    return Number(m[1]);
  };
  assert.equal(d.MIN_FAMILY_SAMPLES, num("MIN_FAMILY_SAMPLES"));
  assert.equal(d.MIN_DISCOVERY_EXPECTANCY_R, num("MIN_DISCOVERY_EXPECTANCY_R"));
  assert.equal(d.SMALL_SAMPLE_FLOOR, num("SMALL_SAMPLE_FLOOR"));
  const models = readPy("models.py");
  const m20 = models.match(/MIN_EVIDENCE_SAMPLES[^=\n]*=\s*([0-9.]+)/);
  assert.ok(m20, "MIN_EVIDENCE_SAMPLES not found in models.py");
  assert.equal(d.MIN_EVIDENCE_SAMPLES, Number(m20[1]));
});

test("score weights match scoring.py (sum to 1)", async () => {
  const s = await load("scoring.ts");
  const py = readPy("scoring.py");
  const wm = py.match(/weights\s*=\s*\{([\s\S]*?)\}/);
  assert.ok(wm, "weights dict not found in scoring.py");
  const pyWeights = {};
  for (const [, k, v] of wm[1].matchAll(/["']?([a-z_]+)["']?\s*:\s*([0-9.]+)/g)) pyWeights[k] = Number(v);
  for (const [k, v] of Object.entries(s.SCORE_WEIGHTS)) {
    assert.ok(k in pyWeights, `weight ${k} missing from scoring.py`);
    assert.equal(v, pyWeights[k], `weight ${k} drifted`);
  }
  const sum = Object.values(s.SCORE_WEIGHTS).reduce((a, b) => a + b, 0);
  assert.ok(Math.abs(sum - 1) < 1e-9, `weights sum to ${sum}, expected 1.0`);
  const mid = py.match(/_SAMPLE_MID[^=\n]*=\s*([0-9.]+)/);
  const steep = py.match(/_SAMPLE_STEEPNESS[^=\n]*=\s*([0-9.]+)/);
  assert.ok(mid && steep, "sample-logistic constants missing from scoring.py");
  assert.equal(s.SAMPLE_MID, Number(mid[1]));
  assert.equal(s.SAMPLE_STEEPNESS, Number(steep[1]));
});

test("economics + stress constants match their sources", async () => {
  const e = await load("economics.ts");
  const wf = await load(path.join("gates", "walkForward.ts"));
  const oosMod = await load(path.join("gates", "oos.ts"));
  const s = await load("scoring.ts");
  const robust = readPy("robustness.py");
  const oosSrc = readPy("oos.py");
  const split = readPy("splitting.py");
  const walkSrc = readPy("walkforward.py");
  const grab = (src, name, file) => {
    const m = src.match(new RegExp(`${name}[^=\\n]*=\\s*([0-9.]+)`));
    assert.ok(m, `${name} not found in ${file}`);
    return Number(m[1]);
  };
  // each constant is pinned against the file that ENFORCES it
  assert.equal(e.MAX_ACCEPTABLE_DEGRADATION_R, grab(robust, "MAX_ACCEPTABLE_DEGRADATION_R", "robustness.py"));
  assert.equal(e.MIN_ECONOMIC_OOS_EXPECTANCY_R, grab(oosSrc, "MIN_ECONOMIC_OOS_EXPECTANCY_R", "oos.py"));
  assert.equal(e.MAX_OOS_DEGRADATION, grab(oosSrc, "MAX_OOS_DEGRADATION", "oos.py"));
  assert.equal(oosMod.MIN_OOS_EXPECTANCY_R, grab(oosSrc, "MIN_OOS_EXPECTANCY_R", "oos.py"));
  assert.equal(e.DEFAULT_PURGE_SECONDS, grab(split, "DEFAULT_PURGE_SECONDS", "splitting.py"));
  assert.equal(e.DEFAULT_EMBARGO_SECONDS, grab(split, "DEFAULT_EMBARGO_SECONDS", "splitting.py"));
  assert.equal(wf.MIN_FOLD_EXPECTANCY_R, grab(walkSrc, "MIN_FOLD_EXPECTANCY_R", "walkforward.py"));
  assert.equal(wf.MIN_PASS_FRACTION, grab(walkSrc, "MIN_PASS_FRACTION", "walkforward.py"));
  assert.equal(s.DSR_CONFIDENCE_FLOOR, grab(readPy("scoring.py"), "DSR_CONFIDENCE_FLOOR", "scoring.py"));
  for (const scenario of ["spread_plus_1", "spread_plus_2", "slippage_plus_1", "slippage_plus_2", "latency_plus_50ms", "latency_plus_150ms"]) {
    assert.ok(robust.includes(scenario), `stress scenario ${scenario} missing from robustness.py`);
  }
  // the DOCS must cite the right file too — a right number under a wrong
  // ref is still a doc bug that would send an operator to the wrong place.
  // NB: `e` is the module namespace — the entry object lives on it.
  const entry = e.economicsEntry;
  assert.ok(entry && entry.params, "economics.ts must export economicsEntry with params");
  const refOf = (name) => entry.params.find((p) => p.name === name)?.ref ?? "";
  assert.match(refOf("MAX_ACCEPTABLE_DEGRADATION_R"), /robustness\.py/);
  assert.match(refOf("MIN_ECONOMIC_OOS_EXPECTANCY_R"), /oos\.py/);
  assert.match(refOf("MAX_OOS_DEGRADATION"), /oos\.py/);
  assert.match(refOf("DEFAULT_PURGE_SECONDS"), /splitting\.py/);
  assert.match(refOf("DEFAULT_EMBARGO_SECONDS"), /splitting\.py/);
});

test("handbook ids unique + every chain gate/state has an entry", async () => {
  const idx = await load("index.ts");
  const ids = idx.HANDBOOK_ENTRIES.map((e) => e.id);
  assert.equal(new Set(ids).size, ids.length, "duplicate handbook ids");
  assert.ok(ids.length >= 30, `expected >= 30 entries, got ${ids.length}`);
  for (const gate of idx.GATE_CHAIN) assert.ok(ids.includes(`gate/${gate}`), `no entry for gate ${gate}`);
  for (const st of idx.STATE_IDS) assert.ok(ids.includes(`state/${st}`), `no entry for state ${st}`);
  for (const id of [
    "topic/pipeline", "topic/gates", "topic/lifecycle", "topic/discovery", "topic/scoring",
    "topic/economics", "topic/evidence", "topic/operations", "topic/uiguide", "topic/faq",
    "topic/registry", "topic/experiments", "topic/glossary", "topic/health",
  ]) {
    assert.ok(ids.includes(id), `missing topic ${id}`);
  }
  // every entry carries prose + a source citation (docs, not vibes).
  for (const e of idx.HANDBOOK_ENTRIES) {
    assert.ok(e.sections.length > 0, `${e.id} has no sections`);
    assert.ok(e.source.includes("src/"), `${e.id} lacks a backend source citation`);
    for (const s of e.sections) {
      assert.ok(s.body.length > 0, `${e.id}/${s.heading} has empty body`);
      for (const p of s.body) assert.ok(p.length > 40, `${e.id}/${s.heading}: paragraph too thin (likely placeholder)`);
    }
  }
});

test("search semantics: empty query shows all, AND terms narrow", async () => {
  const idx = await load("index.ts");
  assert.equal(idx.countEntries(""), idx.HANDBOOK_ENTRIES.length);
  const purge = idx.queryHandbook("purge embargo");
  assert.ok(purge.length >= 1, "expected purge+embargo to hit economics entry");
  const nonsense = idx.queryHandbook("zzzz-not-a-real-term");
  assert.equal(nonsense.length, 0);
  assert.ok(idx.countEntries("walk forward folds") >= 1, "multi-term search should match walk-forward entry");
});
