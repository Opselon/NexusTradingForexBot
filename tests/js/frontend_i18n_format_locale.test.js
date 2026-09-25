/**
 * i18n-complete wave — locale-aware formatting layer (frontend/src/lib/format.ts).
 *
 * Contracts pinned here:
 * 1. DEFAULT (no locale set) is byte-identical to the classic pinned
 *    en output — the perf_wave7 suite owns that assertion; this file proves
 *    the Locale variants agree with the classic functions ON EN, so call
 *    sites can migrate without changing any en rendering.
 * 2. setFormatLocale switches PRESENTATION only: same numeric value, locale
 *    separators/digits. fa/ar must render LATIN digits (nu-latn mapping) —
 *    a trading console must not silently swap digit scripts in mono tables.
 * 3. Switching back to en restores byte-identical output (no cache cross-talk:
 *    formatter caches are keyed by locale + options).
 *
 * Run: node --test tests/js/frontend_i18n_format_locale.test.js
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const url = require("node:url");

const FRONTEND = path.resolve(__dirname, "..", "..", "frontend");
const fmtUrl = url.pathToFileURL(path.join(FRONTEND, "src", "lib", "format.ts")).href;

/** Node >= 22.6 strips types on .ts imports (the repo's node is 24; CI passes
 *  the same flag). Loaded through dynamic import so this file stays CJS. */
let fmt;
test.before(async () => {
  fmt = await import(fmtUrl);
});

test("en default: Locale variants match the classic pinned functions exactly", async () => {
  assert.equal(fmt.getFormatLocale(), "en");
  assert.equal(fmt.formatNumberLocale(1234.5678, 2), fmt.formatNumber(1234.5678, 2));
  assert.equal(fmt.formatNumberLocale(1234.5678, 0), fmt.formatNumber(1234.5678, 0));
  assert.equal(fmt.formatPriceLocale(4012.35, 2), fmt.formatPrice(4012.35, 2));
  assert.equal(fmt.formatMoneyLocale(1234.5), fmt.formatMoney(1234.5));
  assert.equal(fmt.formatPnlLocale(-2500.25), fmt.formatPnl(-2500.25));
  assert.equal(fmt.formatPctLocale(99.95, 2), fmt.formatPct(99.95, 2));
  assert.equal(
    fmt.formatDateTimeLocale("2026-09-23T14:05:06.000Z"),
    fmt.formatDateTime("2026-09-23T14:05:06.000Z"),
  );
  assert.equal(fmt.formatDateTimeLocale(null), "—");
  assert.equal(fmt.formatTimeLocale("garbage"), "garbage");
});

test("de-DE: locale decimal/grouping marks, same value", async () => {
  fmt.setFormatLocale("de");
  assert.equal(fmt.formatNumberLocale(1234.5678, 2), "1.234,57");
  assert.equal(fmt.formatPriceLocale(4012.35, 2), "4012,35");
  assert.equal(fmt.formatPctLocale(35.4, 2), "35,40%");
  assert.match(fmt.formatDateTimeLocale("2026-09-23T14:05:06.000Z"), /\d{2}\.\d{2}\.\d{4}/);
  // blank handling unchanged in every locale
  assert.equal(fmt.formatNumberLocale(null, 2), "—");
  assert.equal(fmt.formatPriceLocale(undefined, 2), "—");
});

test("fa/ar use Latin digits (nu-latn) — no digit-script swap in tables", async () => {
  const persianOrArabicDigits = /[۰-۹٠-٩]/;
  fmt.setFormatLocale("fa");
  const fa = fmt.formatNumberLocale(1234.5678, 2);
  assert.equal(persianOrArabicDigits.test(fa), false, `fa rendered non-Latin digits: ${fa}`);
  fmt.setFormatLocale("ar");
  const ar = fmt.formatNumberLocale(1234.5678, 2);
  assert.equal(persianOrArabicDigits.test(ar), false, `ar rendered non-Latin digits: ${ar}`);
  // grouping still locale-shaped (both latn mappings group with commas)
  assert.match(fa, /1,234\.57/);
  assert.match(ar, /1,234\.57/);
});

test("switch back to en restores byte-identical classic output (cache keyed by locale)", async () => {
  fmt.setFormatLocale("de");
  assert.notEqual(fmt.formatNumberLocale(1234.5678, 2), fmt.formatNumber(1234.5678, 2));
  fmt.setFormatLocale("en");
  assert.equal(fmt.formatNumberLocale(1234.5678, 2), fmt.formatNumber(1234.5678, 2));
  assert.equal(
    fmt.formatDateTimeLocale("2026-09-23T14:05:06.000Z"),
    fmt.formatDateTime("2026-09-23T14:05:06.000Z"),
  );
});

test("unknown locale string is ignored (fail-safe, stays on current locale)", async () => {
  fmt.setFormatLocale("en");
  fmt.setFormatLocale("xx-NOPE");
  assert.equal(fmt.getFormatLocale(), "en");
  assert.equal(fmt.formatNumberLocale(15, 0), "15");
});
