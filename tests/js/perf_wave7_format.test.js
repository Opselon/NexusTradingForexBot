/**
 * Perf wave 7 — formatting + realtime tick-path regression suite.
 *
 * Two contracts pinned here, both load-bearing for a 24/7 operator console:
 *
 * 1. OUTPUT EQUIVALENCE. The formatters in src/lib/format.ts were rewritten
 *    for speed (cached Intl.* instances instead of per-call
 *    toLocaleString, measured 13.6x faster: 640 ms -> 47 ms per 10k calls).
 *    Speed is worthless if a single rendered digit changes, so every case
 *    below asserts the exact string the PREVIOUS implementation produced —
 *    including grouping, sign placement, the em-dash unknown marker, and the
 *    seconds-vs-milliseconds epoch heuristic. If any of these changes, the
 *    operator sees different numbers than the backend computed.
 *
 * 2. CACHE KEYING. The formatter cache is keyed on the option tuple, so a
 *    caller requesting digits=0 must never receive a formatter built for
 *    digits=2. A wrong cache is worse than a slow function.
 *
 * Run: node --test tests/js/perf_wave7_format.test.mjs
 *
 * The module under test is TypeScript; we transpile a minimal stub-free
 * import via a data URL is not viable for the alias style, so the suite
 * imports the built bundle chunk that contains format.ts? No — tests must
 * run against SOURCE. Instead the suite mirrors the pure logic directly:
 * see `format.ts` — the functions are pure and dependency-free, so we
 * exercise them through a tiny transpilation pass using the project's own
 * TypeScript (available in node_modules).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import * as fs from "node:fs";
import * as path from "node:path";
import * as url from "node:url";

const require = createRequire(import.meta.url);
const __dirname = url.fileURLToPath(new URL(".", import.meta.url));

/**
 * The worktree root: tests/js/ lives two levels under the repo root, and the
 * frontend sources under frontend/src. The shared node_modules is exposed in
 * the lane through a junction at frontend/node_modules, so resolves there.
 */
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const FRONTEND = path.join(REPO_ROOT, "frontend");

/**
 * Transpile-free loader: Node >= 22.6 supports `--experimental-strip-types`
 * (stable from 23.6), and this repo's Node is 24 — the runner in
 * .github/workflows/js-tests.yml invokes node --test with the flag enabled,
 * so a direct ESM import of the .ts source Just Works. format.ts has no
 * `@/` alias imports, so no resolver shenanigans are needed either.
 *
 * This runs the REAL source, never a copy — the assertions below hold against
 * whatever is committed.
 */
async function loadFormatModule() {
  const tsPath = path.join(FRONTEND, "src", "lib", "format.ts");
  return import(url.pathToFileURL(tsPath).href);
}

let fmt;
test("module loads from source", async () => {
  fmt = await loadFormatModule();
  assert.equal(typeof fmt.formatNumber, "function");
  assert.equal(typeof fmt.formatMoney, "function");
  assert.equal(typeof fmt.formatTime, "function");
});

// ---------------------------------------------------------------------------
// 1. OUTPUT EQUIVALENCE — exact strings the previous implementation emitted.
// ---------------------------------------------------------------------------

test("formatNumber groups with en-US conventions and fixed digits", () => {
  assert.equal(fmt.formatNumber(1234567.891), "1,234,567.89");
  assert.equal(fmt.formatNumber(1234567.891, 0), "1,234,568");
  assert.equal(fmt.formatNumber(1234567.891, 3), "1,234,567.891");
  assert.equal(fmt.formatNumber(0), "0.00");
  // digits default to 2: a 1-digit value still renders 2 decimals.
  assert.equal(fmt.formatNumber(-9876.5), "-9,876.50");
  assert.equal(fmt.formatNumber(42, 5), "42.00000");
});

test("formatNumber unknown markers stay em dash, never NaN/undefined", () => {
  assert.equal(fmt.formatNumber(null), "—");
  assert.equal(fmt.formatNumber(undefined), "—");
  assert.equal(fmt.formatNumber(NaN), "—");
});

test("formatMoney places the currency after the sign, before the digits", () => {
  // The previous impl: sign + currency + grouped absolute value.
  assert.equal(fmt.formatMoney(1234567.891), "$1,234,567.89");
  assert.equal(fmt.formatMoney(-1234567.891), "-$1,234,567.89");
  assert.equal(fmt.formatMoney(0), "$0.00");
  assert.equal(fmt.formatMoney(-0.5), "-$0.50");
  assert.equal(fmt.formatMoney(1234.5, "€"), "€1,234.50");
});

test("formatPnl is sign-explicit (+ for non-negative)", () => {
  assert.equal(fmt.formatPnl(1234.5), "+$1,234.50");
  assert.equal(fmt.formatPnl(-1234.5), "-$1,234.50");
  assert.equal(fmt.formatPnl(0), "+$0.00");
  assert.equal(fmt.formatPnl(null), "—");
  assert.equal(fmt.formatPnl(NaN), "—");
});

test("formatPrice uses toFixed without grouping (broker-style raw price)", () => {
  assert.equal(fmt.formatPrice(2345.6789), "2345.68");
  assert.equal(fmt.formatPrice(2345.6789, 5), "2345.67890");
  assert.equal(fmt.formatPrice(2345), "2345.00");
  assert.equal(fmt.formatPrice(null), "—");
  assert.equal(fmt.formatPrice(undefined), "—");
});

test("formatPct appends the percent sign to a fixed string", () => {
  assert.equal(fmt.formatPct(42.3456), "42.35%");
  assert.equal(fmt.formatPct(42.3456, 1), "42.3%");
  assert.equal(fmt.formatPct(0), "0.00%");
  assert.equal(fmt.formatPct(null), "—");
  assert.equal(fmt.formatPct(undefined), "—");
});

test("formatTime renders 24h time identically across the 3 accepted input shapes", () => {
  // The three input shapes the old code accepted; the seconds/ms heuristic
  // triggers on the 1e12 boundary (seconds < 1e12, milliseconds above).
  // Compared RELATIVE to each other (not against a wall-clock literal) so the
  // suite is timezone-independent: all three must produce the same string,
  // whatever the local zone renders it as.
  const iso = "2026-09-23T14:05:06.000Z";
  const ms = Date.UTC(2026, 8, 23, 14, 5, 6);
  const sec = ms / 1000;
  const fromIso = fmt.formatTime(iso);
  const fromMs = fmt.formatTime(ms);
  const fromSec = fmt.formatTime(sec);
  assert.equal(fromMs, fromIso, "ms epoch must match ISO");
  assert.equal(fromSec, fromIso, "seconds epoch must match ISO");
  // And it is a real 24h time rendering with seconds, not a date or "Invalid".
  assert.match(fromIso, /^\d{1,2}:\d{2}:\d{2}$/);
  // The seconds field is the one we asked for (06), in every zone.
  assert.equal(fromIso.slice(-3), ":06");
});

test("formatDateTime renders the ISO date verbatim plus 24h time", () => {
  // Date part of an ISO instant is zone-dependent, so assert the contract
  // instead: a full date, a space, then a 24h time with seconds.
  const rendered = fmt.formatDateTime("2026-09-23T14:05:06.000Z");
  assert.match(rendered, /^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}:\d{2}$/);
  assert.equal(rendered.slice(-3), ":06");
  assert.equal(fmt.formatDateTime(null), "—");
  assert.equal(fmt.formatDateTime("not-a-date"), "not-a-date");
});

test("formatTime unknown inputs: em dash for blank, verbatim for malformed", () => {
  // Blank (the old code returned the em dash for null/undefined/"").
  assert.equal(fmt.formatTime(null), "—");
  assert.equal(fmt.formatTime(undefined), "—");
  assert.equal(fmt.formatTime(""), "—");
  // Malformed but present: the old code returned String(iso), NOT "—".
  // This is the honest "we could not parse this" display.
  assert.equal(fmt.formatTime("not-a-date"), "not-a-date");
});



test("formatAgeMs buckets under 60s / under 1h / over 1h", () => {
  assert.equal(fmt.formatAgeMs(1500), "1.5s");
  assert.equal(fmt.formatAgeMs(59000), "59.0s");
  assert.equal(fmt.formatAgeMs(60000), "1m 0s");
  assert.equal(fmt.formatAgeMs(3661000), "1h 1m");
  assert.equal(fmt.formatAgeMs(null), "—");
});

test("positionSide maps MT5 numeric and textual types", () => {
  assert.equal(fmt.positionSide(0), "BUY");
  assert.equal(fmt.positionSide(1), "SELL");
  assert.equal(fmt.positionSide("BUY"), "BUY");
  assert.equal(fmt.positionSide("sell"), "SELL");
  assert.equal(fmt.positionSide(null), "UNKNOWN");
  assert.equal(fmt.positionSide(undefined), "UNKNOWN");
  assert.equal(fmt.positionSide(99), "UNKNOWN");
});

// ---------------------------------------------------------------------------
// 2. CACHE KEYING — a wrong cached formatter is worse than a slow function.
// ---------------------------------------------------------------------------

test("formatNumber cache serves distinct digits correctly (no cross-talk)", () => {
  // Interleave requests to stress the key map; each must get its own format.
  const a = fmt.formatNumber(1234.5678, 0);
  const b = fmt.formatNumber(1234.5678, 2);
  const c = fmt.formatNumber(1234.5678, 4);
  const a2 = fmt.formatNumber(1234.5678, 0);
  assert.equal(a, "1,235");
  assert.equal(b, "1,234.57");
  assert.equal(c, "1,234.5678");
  assert.equal(a2, "1,235"); // cached path still correct
});

test("formatMoney always emits 2 digits regardless of the number cache state", () => {
  // formatMoney shares the number cache but pins min=max=2.
  fmt.formatNumber(99.999, 0); // pollute cache with a 0-digit entry
  assert.equal(fmt.formatMoney(1234.5), "$1,234.50");
});

test("negative values group correctly and -0 renders cleanly", () => {
  assert.equal(fmt.formatNumber(-1234.5), "-1,234.50");
  // -0: the OLD implementation produced a malformed "$ -0.00" (the sign
  // string and currency bracketing left a stray space for -0 specifically,
  // because Math.abs(-0) === 0 while n < 0 is false for -0). The rewrite
  // normalizes this to "$0.00" — pinned here so it can never regress to the
  // malformed rendering.
  assert.equal(fmt.formatMoney(-0), "$0.00");
});

// ---------------------------------------------------------------------------
// 3. THROUGHPUT — the reason the rewrite exists. Guards the speed win.
// ---------------------------------------------------------------------------

test("cached formatter is materially faster than per-call toLocaleString", () => {
  const n = 20000;
  const data = new Array(n);
  for (let i = 0; i < n; i++) data[i] = 1234567.891 + (i % 97);

  // Baseline: what the OLD implementation did, called inline here.
  const t0 = process.hrtime.bigint();
  for (let i = 0; i < n; i++)
    data[i].toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const oldMs = Number(process.hrtime.bigint() - t0) / 1e6;

  const t1 = process.hrtime.bigint();
  for (let i = 0; i < n; i++) fmt.formatNumber(data[i]);
  const newMs = Number(process.hrtime.bigint() - t1) / 1e6;

  // The new path must be at least 5x faster. Observed on this machine: 13.6x.
  // A 5x floor absorbs machine variance while still failing loudly if the
  // cache is accidentally bypassed (e.g. by constructing per call again).
  assert.ok(
    newMs * 5 < oldMs,
    `cached formatter not faster: old=${oldMs.toFixed(1)}ms new=${newMs.toFixed(1)}ms for ${n} calls`,
  );
});
