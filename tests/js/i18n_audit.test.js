/**
 * CI wrapper for the i18n audit gate (§58/§59).
 *
 * js-tests.yml runs `node tests/js/*.test.js` directly (not `node --test`), so
 * this file adopts that contract: import the audit tool, assert the hard
 * failure classes are empty, exit 0/1. It also registers a node:test entry so
 * `node --test` (local runs) reports a named assertion per gate.
 *
 * HARD (fail CI): MISSING, INTERPOLATION, DUPLICATE, MALFORMED.
 * SOFT (report only): EXTRA (dead keys) — noise risk is too high to fail.
 */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");

function runAudit() {
  const r = spawnSync(process.execPath, [path.join(here, "i18n_audit.mjs"), "--root", root], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
  return { stdout: r.stdout || "", code: r.status, error: r.error };
}

test("i18n audit gate: no MISSING / INTERPOLATION / DUPLICATE / MALFORMED", () => {
  const { stdout, code, error } = runAudit();
  if (error) throw new Error(`audit tool could not run: ${error.message}`);

  const missing = (stdout.match(/^  - errors?\.[^\n]*/gm) || []).length;
  const hard = /^HARD FAILURES: 0$/m.test(stdout);
  assert.ok(hard, `i18n audit reported HARD failures:\n${stdout.split("\n").filter((l) => /^(MISSING|INTERPOLATION|DUPLICATE|MALFORMED|HARD)/.test(l)).join("\n")}`);
  assert.equal(missing, 0, "expected zero missing keys");
  assert.equal(code, 0, `audit tool exited ${code}`);
});

test("i18n audit gate: fa/de/es/ar locale parity is 100%", () => {
  const { stdout } = runAudit();
  for (const l of ["fa", "de", "es", "ar"]) {
    assert.ok(new RegExp(`^  ${l}: 100%$`, "m").test(stdout), `locale ${l} is not at 100%:\n${stdout}`);
  }
});

// CI entry point (js-tests.yml: `node tests/js/*.test.js`, no --test flag).
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { stdout, code, error } = runAudit();
  if (error) {
    console.error(`i18n audit gate: tool could not run — ${error.message}`);
    process.exit(1);
  }
  const failing = stdout
    .split("\n")
    .filter((l) => /^(MISSING KEYS|INTERPOLATION MISMATCH|DUPLICATE KEYS|MALFORMED): [1-9]/.test(l));
  if (code !== 0 || failing.length) {
    console.error(stdout);
    console.error(`i18n audit gate FAILED: ${failing.join("; ") || "tool exit " + code}`);
    process.exit(1);
  }
  console.error("i18n audit gate: PASS (" + (stdout.match(/^scopes parsed: (\d+)/m)?.[1] || "?") + " scopes)");
  process.exit(0);
}
