/**
 * Pro ops-chrome math + wiring-contract regression suite.
 *
 * Run:  node tests/js/pro_ops_chrome.test.mjs
 *   or: node --test tests/js/pro_ops_chrome.test.mjs
 *
 * Imports the REAL frontend module frontend/src/lib/opsChromeMath.ts (Node 24
 * strips the erasable TS types; the module has zero runtime imports, so no
 * `@/` alias resolution is needed). Pins:
 *   - computeTrend never invents a direction without a PROVIDED prev,
 *   - flashKeyDiffers is a strict equality check (the QuoteTape flash contract:
 *     only a parent-advanced state_version key may flash the tape),
 *   - formatTickAge renders UNKNOWN for missing ages (never "0.0s" freshness),
 *   - watermarkWord precedence: feed-down beats stale; both false → no overlay,
 *   - parseNumericField validates FORMAT only — it never encodes trading
 *     semantics, and "" is distinguishable from garbage.
 * Plus static wiring assertions on the new component sources (single mutation
 * path through the api layer, no fetch, flash-key contract documented).
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  ageSecToMs,
  computeTrend,
  flashKeyDiffers,
  formatTickAge,
  gapExceedsBudget,
  legacyMutationVerdict,
  parseNumericField,
  trendDelta,
  watermarkWord,
} from "../../frontend/src/lib/opsChromeMath.ts";

const here = dirname(fileURLToPath(import.meta.url));

/** Strip comments before scanning a source for forbidden runtime calls. */
function codeOnly(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

// ---------------------------------------------------------------------------
// computeTrend — arrow only from PROVIDED prev+cur
// ---------------------------------------------------------------------------

test("computeTrend: strict comparison of two provided readings", () => {
  assert.equal(computeTrend(10, 5), "up");
  assert.equal(computeTrend(5, 10), "down");
  assert.equal(computeTrend(7, 7), "flat");
});

test("SAFETY: no prev (or no cur) is neutral — never a guessed direction", () => {
  const missing = [null, undefined, NaN, Infinity];
  for (const m of missing) {
    assert.equal(computeTrend(100, m), "neutral", `prev=${String(m)} must not produce a trend`);
    assert.equal(computeTrend(m, 100), "neutral", `cur=${String(m)} must not produce a trend`);
  }
  assert.equal(computeTrend(null, null), "neutral");
});

test("trendDelta: null when either side missing (never 0)", () => {
  assert.equal(trendDelta(10.5, 10), 0.5);
  assert.equal(trendDelta(9, 10), -1);
  assert.equal(trendDelta(10, null), null);
  assert.equal(trendDelta(undefined, 10), null);
  assert.equal(trendDelta(NaN, 10), null);
});

// ---------------------------------------------------------------------------
// flashKeyDiffers — the QuoteTape honesty gate
// ---------------------------------------------------------------------------

test("flashKeyDiffers: strict equality across versions", () => {
  assert.equal(flashKeyDiffers(42, 41), true);
  assert.equal(flashKeyDiffers(42, 42), false);
  assert.equal(flashKeyDiffers(41, null), true);
  assert.equal(flashKeyDiffers(null, null), false);
  // strict: a stringified version is NOT the number version
  assert.equal(flashKeyDiffers("42", 42), true);
});

test("SAFETY: a reconnect replaying the SAME version key never flashes", () => {
  const lastAccepted = 100;
  // transport blip -> parent keeps the key (state_version did not advance)
  assert.equal(flashKeyDiffers(lastAccepted, lastAccepted), false);
  // a newer DATA frame -> parent advances the key -> flash allowed
  assert.equal(flashKeyDiffers(101, lastAccepted), true);
});

// ---------------------------------------------------------------------------
// formatTickAge / ageSecToMs — UNKNOWN never renders as fresh
// ---------------------------------------------------------------------------

test("formatTickAge: humanized seconds/minutes/hours", () => {
  assert.equal(formatTickAge(0), "0.0s");
  assert.equal(formatTickAge(12_345), "12.3s");
  assert.equal(formatTickAge(135_000), "2m 15s");
  assert.equal(formatTickAge(3_720_000), "1h 2m");
  assert.equal(formatTickAge(-5_000), "0.0s", "clock skew clamps at zero, never negative");
});

test("SAFETY: formatTickAge(null/undefined/NaN) is the UNKNOWN dash, not 0.0s", () => {
  assert.equal(formatTickAge(null), "—");
  assert.equal(formatTickAge(undefined), "—");
  assert.equal(formatTickAge(NaN), "—");
});

test("ageSecToMs: backend seconds to client ms; missing stays null", () => {
  assert.equal(ageSecToMs(2.5), 2500);
  assert.equal(ageSecToMs(-1), 0);
  assert.equal(ageSecToMs(null), null);
  assert.equal(ageSecToMs(undefined), null);
  assert.equal(ageSecToMs(NaN), null);
});

// ---------------------------------------------------------------------------
// watermarkWord — parent truth flags only
// ---------------------------------------------------------------------------

test("watermarkWord: feed-down outranks stale; both false hides the overlay", () => {
  assert.equal(watermarkWord({ stale: false, feedDown: false }), null);
  assert.equal(watermarkWord({ stale: true, feedDown: false }), "STALE");
  assert.equal(watermarkWord({ stale: false, feedDown: true }), "LIVE FEED DOWN");
  assert.equal(watermarkWord({ stale: true, feedDown: true }), "LIVE FEED DOWN");
});

// ---------------------------------------------------------------------------
// parseNumericField — FORMAT-only validation
// ---------------------------------------------------------------------------

test("parseNumericField: well-formed decimals parse", () => {
  assert.deepEqual(parseNumericField("2341.55"), { kind: "ok", value: 2341.55 });
  assert.deepEqual(parseNumericField(" 2341 "), { kind: "ok", value: 2341 });
  assert.deepEqual(parseNumericField(".5"), { kind: "ok", value: 0.5 });
  assert.deepEqual(parseNumericField("1e3"), { kind: "ok", value: 1000 });
  assert.deepEqual(parseNumericField("-2.25"), { kind: "ok", value: -2.25 });
});

test("parseNumericField: empty is its OWN kind (editor must not send a value)", () => {
  assert.deepEqual(parseNumericField(""), { kind: "empty" });
  assert.deepEqual(parseNumericField("   "), { kind: "empty" });
});

test("parseNumericField: garbage is invalid — and NOT coerced to 0/NaN", () => {
  for (const junk of ["abc", "1,5", "1.2.3", "12 34", "0x1f", "Infinity", "NaN", "1e", "5..", "--5", "1e999"]) {
    const r = parseNumericField(junk);
    assert.equal(r.kind, "invalid", `"${junk}" must be invalid, got ${JSON.stringify(r)}`);
  }
});

// ---------------------------------------------------------------------------
// legacyMutationVerdict — the backend payload decides, never HTTP 200 alone
// ---------------------------------------------------------------------------

test("legacyMutationVerdict: success:true accepts, success:false refuses", () => {
  assert.deepEqual(legacyMutationVerdict({ success: true }), { ok: true, message: null });
  assert.deepEqual(legacyMutationVerdict({ success: false }), { ok: false, message: null });
  assert.equal(legacyMutationVerdict({ success: true, message: "done" }).message, "done");
});

test("SAFETY: a legacy body WITHOUT a success key is never an acceptance", () => {
  for (const body of [{}, { message: "x" }, null, undefined, "truthy"]) {
    assert.equal(legacyMutationVerdict(body).ok, false, JSON.stringify(body));
  }
});

test("gapExceedsBudget: warn threshold only; null/unknown gap never warns", () => {
  assert.equal(gapExceedsBudget(12.3, 10), true);
  assert.equal(gapExceedsBudget(9.9, 10), false);
  assert.equal(gapExceedsBudget(null, 10), false);
  assert.equal(gapExceedsBudget(NaN, 10), false);
  assert.equal(gapExceedsBudget(5, 0), false, "no budget configured -> no warning");
});

// ---------------------------------------------------------------------------
// Static wiring contracts on the new component sources
// ---------------------------------------------------------------------------

test("OpsChrome.tsx documents the parent-owned flash-key contract and never fetches", () => {
  const src = readFileSync(join(here, "../../frontend/src/components/pro/OpsChrome.tsx"), "utf8");
  assert.match(src, /state_version/);
  assert.match(src, /flashKey/);
  assert.match(src, /reconnect/, "the comment must warn that a reconnect cannot fake a move");
  assert.doesNotMatch(codeOnly(src), /\bfetch\s*\(/, "no fetch outside the api layer");
  assert.doesNotMatch(codeOnly(src), /useUiStore|\bpushToast\b/, "chrome never forks a toast bus");
});

test("SlTpEditor.tsx mutates ONLY through tradingApi (the sanctioned path)", () => {
  const src = readFileSync(join(here, "../../frontend/src/components/pro/SlTpEditor.tsx"), "utf8");
  assert.match(src, /from "@\/api\/tradingApi"/);
  assert.match(src, /tradingApi\.modifyPosition/);
  // reuses the SAME toast bus (uiStore.pushToast — the store the shared hook
  // writes) instead of forking a parallel one, and reads the verdict from the
  // backend body via the pinned legacy rule, never via HTTP status alone.
  assert.match(codeOnly(src), /useUiStore/);
  assert.match(codeOnly(src), /legacyMutationVerdict/);
  assert.doesNotMatch(codeOnly(src), /\bfetch\s*\(/, "no fetch outside the api layer");
  // no optimistic local write of new SL/TP values into any store/query cache
  assert.doesNotMatch(codeOnly(src), /setQueryData/, "display updates only via refetch, never local write");
});

test("opsChromeMath.ts stays erasable-TS importable by node (no enums/namespaces)", () => {
  const src = readFileSync(join(here, "../../frontend/src/lib/opsChromeMath.ts"), "utf8");
  assert.doesNotMatch(src, /^\s*(export\s+)?(enum|namespace|declare)\s/m);
  assert.doesNotMatch(src, /constructor\s*\(\s*(private|public|protected|readonly)\b/, "no parameter properties");
});
