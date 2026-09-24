/**
 * i18n-complete wave — user-entered numeric parsing (frontend/src/lib/numberInput.ts).
 *
 * §22 contract: decimal-comma locales must never produce NaN/garbage on the
 * order path, and no input form may silently reinterpret a value.
 */
import { strict as nodeAssert } from "node:assert";
import { test } from "node:test";

// numberInput.ts is dependency-free -> safe to import under node strip-types.
import { parseUserNumber, parseUserInt } from "../../frontend/src/lib/numberInput.ts";

test("parseUserNumber: en decimal dot passes through unchanged", () => {
  nodeAssert.equal(parseUserNumber("4012.35"), 4012.35);
  nodeAssert.equal(parseUserNumber("0.35"), 0.35);
  nodeAssert.equal(parseUserNumber("-2.5"), -2.5);
  nodeAssert.equal(parseUserNumber("0"), 0);
});

test("parseUserNumber: decimal COMMA (fa/de/es input) parses instead of NaN", () => {
  nodeAssert.equal(parseUserNumber("4012,35"), 4012.35);
  nodeAssert.equal(parseUserNumber("0,35"), 0.35);
  nodeAssert.equal(parseUserNumber("-2,5"), -2.5);
  // The exact defect this helper exists for: Number("4012,35") was NaN.
  nodeAssert.ok(Number.isNaN(Number("4012,35")));
  nodeAssert.equal(parseUserNumber("4012,35").valueOf(), 4012.35);
});

test("parseUserNumber: grouping spaces of any flavour are stripped", () => {
  nodeAssert.equal(parseUserNumber("4 012,35"), 4012.35);
  nodeAssert.equal(parseUserNumber("4\u00A0012.35"), 4012.35);
  nodeAssert.equal(parseUserNumber("4\u202F012,35"), 4012.35);
});

test("parseUserNumber: both separators -> rightmost is the decimal mark", () => {
  nodeAssert.equal(parseUserNumber("4,012.35"), 4012.35); // en grouping + decimal
  nodeAssert.equal(parseUserNumber("4.012,35"), 4012.35); // de grouping + decimal
  nodeAssert.equal(parseUserNumber("1.234,56"), 1234.56);
  nodeAssert.equal(parseUserNumber("1,234.56"), 1234.56);
});

test("parseUserNumber: empty and unparseable return NaN (never a silent 0)", () => {
  nodeAssert.ok(Number.isNaN(parseUserNumber("")));
  nodeAssert.ok(Number.isNaN(parseUserNumber("   ")));
  nodeAssert.ok(Number.isNaN(parseUserNumber("abc")));
  nodeAssert.ok(Number.isNaN(parseUserNumber("40a12")));
  nodeAssert.ok(Number.isNaN(parseUserNumber("1.2.3"))); // two dots, no comma
  // NaN is what the OLD order path forwarded: JSON-serialized it as null.
  nodeAssert.equal(JSON.stringify({ stop_loss: Number("4012,35") }), '{"stop_loss":null}');
});

test("parseUserInt: integer fields round instead of truncating at a comma", () => {
  nodeAssert.equal(parseUserInt("12"), 12);
  nodeAssert.equal(parseUserInt("12,7"), 13);
  nodeAssert.equal(parseUserInt("1.234,5"), 1235);
  nodeAssert.equal(parseUserInt("1,234"), 1); // documented residual: comma-only 3-digit grouping
  nodeAssert.ok(Number.isNaN(parseUserInt("")));
});
