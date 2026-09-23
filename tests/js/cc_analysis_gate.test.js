"use strict";
/**
 * CI bridge for js-tests.yml: that workflow globs tests/js/*.test.js and runs
 * each file with plain `node $t` — ESM `.mjs` suites never reach CI (the
 * existing pro_risk_viz.test.mjs has the same gap). This CJS wrapper spawns
 * the real suite (ESM + Node 24 type-stripping + resolve hook) so the
 * command-center analysis contract is gated, WITHOUT editing the workflow
 * glob (repo-infra change requires explicit approval).
 */
const { spawnSync } = require("node:child_process");
const path = require("node:path");

const target = path.join(__dirname, "cc_analysis.test.mjs");
const r = spawnSync(process.execPath, ["--test", target], { stdio: "inherit" });
if (r.error) {
  console.error(`cc_analysis bridge failed to spawn: ${r.error.message}`);
  process.exit(1);
}
process.exit(r.status === null ? 1 : r.status);
