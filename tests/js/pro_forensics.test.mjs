/**
 * pro_forensics.test.mjs — node:test suite for the audit/forensics math layer
 * used by the pro console (frontend/src/lib/forensicsMath.ts) plus a static
 * safety scan of the components that consume it.
 *
 * Run from the repo root:
 *   node --test tests/js/pro_forensics.test.mjs
 * (Node >= 22.6 strips the TS types from the imported .ts module at load time;
 * the Node version is printed at the top of the run output.)
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import {
  buildCsv,
  escapeCsvCell,
  eventGaps,
  normalizeBucket,
  parseTimestampMs,
  payloadSummary,
  severityMatrix,
  timelinePoints,
  UNKNOWN,
} from "../../frontend/src/lib/forensicsMath.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(here, "..", "..", rel), "utf8");

console.log(`node ${process.version}`);

const HOUR = 3600_000;
const MIN = 60_000;

// ---------------------------------------------------------------------------
// parseTimestampMs
// ---------------------------------------------------------------------------

test("parseTimestampMs: valid ISO string -> epoch ms", () => {
  assert.equal(parseTimestampMs("2026-09-13T04:00:00Z"), Date.UTC(2026, 8, 13, 4, 0, 0));
});

test("parseTimestampMs: numeric epochs (seconds vs milliseconds)", () => {
  assert.equal(parseTimestampMs(1_700_000_000_000), 1_700_000_000_000);
  assert.equal(parseTimestampMs(1_700_000_000), 1_700_000_000_000);
  assert.equal(parseTimestampMs("1700000000"), 1_700_000_000_000);
});

test("parseTimestampMs: epoch 0 is valid and distinguishable from garbage", () => {
  assert.equal(parseTimestampMs("1970-01-01T00:00:00Z"), 0);
  assert.equal(parseTimestampMs(0), 0);
});

test("parseTimestampMs: garbage never becomes a guessed timestamp", () => {
  for (const bad of [null, undefined, "", "   ", "not-a-date", "2026-13-45T99:99:99Z", NaN, Infinity, -Infinity, true, false, {}, [], () => 1, "NaN"]) {
    assert.equal(parseTimestampMs(bad), null, `expected null for ${String(bad)}`);
  }
});

// ---------------------------------------------------------------------------
// severityMatrix
// ---------------------------------------------------------------------------

test("severityMatrix: counts a normal inventory and keeps totals consistent", () => {
  const m = severityMatrix([
    { severity: "CRITICAL", status: "OPEN" },
    { severity: "CRITICAL", status: "OPEN" },
    { severity: "CRITICAL", status: "RESOLVED" },
    { severity: "HIGH", status: "OPEN" },
    { severity: "LOW", status: "INVESTIGATING" },
  ]);
  assert.equal(m.counts.CRITICAL.OPEN, 2);
  assert.equal(m.counts.CRITICAL.RESOLVED, 1);
  assert.equal(m.counts.HIGH.OPEN, 1);
  assert.equal(m.counts.LOW.INVESTIGATING, 1);
  assert.equal(m.counts.LOW.OPEN, 0, "every cell must exist, zero-filled");
  assert.equal(m.total, 5);
  assert.equal(Object.values(m.severityTotals).reduce((a, b) => a + b, 0), m.total);
  assert.equal(Object.values(m.statusTotals).reduce((a, b) => a + b, 0), m.total);
});

test("severityMatrix: canonical row/column order with unknown last", () => {
  const m = severityMatrix([
    { severity: "LOW", status: "RESOLVED" },
    { severity: "CRITICAL", status: "OPEN" },
    { severity: "MEDIUM", status: "OPEN" },
    { severity: "CRITICAL" },
  ]);
  assert.deepEqual(m.severities, ["CRITICAL", "MEDIUM", "LOW"]);
  assert.deepEqual(m.statuses, ["OPEN", "RESOLVED", UNKNOWN]);
  assert.equal(m.counts.CRITICAL[UNKNOWN], 1, "status-less row buckets to unknown");
  const withUnknownSev = severityMatrix([{ severity: null, status: "OPEN" }, { severity: "HIGH", status: "OPEN" }]);
  assert.deepEqual(withUnknownSev.severities, ["HIGH", UNKNOWN], "unknown severity sorts last");
});

test("severityMatrix: missing / blank / weird fields land in the unknown bucket, never dropped", () => {
  const m = severityMatrix([
    {}, // neither field
    { severity: null, status: undefined },
    { severity: "   ", status: "\t" }, // blank after trim
    { severity: "WEIRD_CODE", status: "OPEN" },
  ]);
  assert.equal(m.total, 4, "a row with no fields still counts once");
  assert.equal(m.counts[UNKNOWN][UNKNOWN], 3);
  assert.equal(m.counts.WEIRD_CODE.OPEN, 1);
  assert.equal(m.severityTotals[UNKNOWN], 3);
});

test("severityMatrix: normalization is trim + upper-case only (never infers)", () => {
  assert.equal(normalizeBucket(" critical "), "CRITICAL");
  assert.equal(normalizeBucket(""), UNKNOWN);
  assert.equal(normalizeBucket("null"), "NULL", 'the literal string "null" is not the absent value');
  assert.equal(normalizeBucket("0"), "0");
  assert.equal(normalizeBucket(Number.NaN), UNKNOWN);
  assert.equal(normalizeBucket({ severity: "HIGH" }), UNKNOWN);
});

test("severityMatrix: hostile inventory input cannot throw", () => {
  const m = severityMatrix([null, undefined, 42, "CRITICAL", [], { severity: { nested: 1 }, status: ["x"] }]);
  assert.equal(m.total, 1, "only the object row counts");
  assert.equal(m.skipped, 5);
  assert.equal(m.counts[UNKNOWN][UNKNOWN], 1);
  assert.equal(severityMatrix(null).total, 0);
  assert.equal(severityMatrix(undefined).total, 0);
  assert.equal(severityMatrix("not-an-array").total, 0);
  assert.equal(severityMatrix([]).severities.length, 0);
});

// ---------------------------------------------------------------------------
// eventGaps
// ---------------------------------------------------------------------------

test("eventGaps: reports the quiet period between consecutive events", () => {
  const rows = [
    { created_at: "2026-09-13T04:00:00Z" },
    { created_at: "2026-09-13T04:00:05Z" },
    { created_at: "2026-09-13T05:30:00Z" }, // 90m after the previous
    { created_at: "2026-09-13T05:30:10Z" },
  ];
  const gaps = eventGaps(rows, 60);
  assert.equal(gaps.length, 1);
  assert.equal(gaps[0].index, 2);
  assert.equal(gaps[0].previousIndex, 1);
  assert.equal(gaps[0].deltaSec, 89 * 60 + 55, "04:00:05 -> 05:30:00 = 5395s");
});

test("eventGaps: newest-first tails (backend order) report gaps too", () => {
  const rows = [
    { created_at: "2026-09-13T06:00:00Z" },
    { created_at: "2026-09-13T04:00:00Z" }, // 2h earlier: descending order
  ];
  const gaps = eventGaps(rows, 60);
  assert.equal(gaps.length, 1);
  assert.equal(gaps[0].index, 1);
  assert.ok(gaps[0].deltaSec > 7000);
});

test("eventGaps: bad timestamps are skipped, gap measured across the hole", () => {
  const rows = [
    { created_at: "2026-09-13T04:00:00Z" },
    { created_at: "garbage" },
    { created_at: null },
    { created_at: "2026-09-13T04:10:00Z" },
  ];
  const gaps = eventGaps(rows, 60);
  assert.equal(gaps.length, 1);
  assert.equal(gaps[0].index, 3);
  assert.equal(gaps[0].previousIndex, 0, "skip is bridged, not guessed away");
  assert.equal(gaps[0].deltaSec, 600);
});

test("eventGaps: NaN timestamps do not poison the walk", () => {
  const rows = [
    { created_at: NaN },
    { created_at: "2026-09-13T04:00:00Z" },
    { created_at: NaN },
    { created_at: "2026-09-13T05:00:00Z" },
  ];
  const gaps = eventGaps(rows, 60);
  assert.equal(gaps.length, 1);
  assert.equal(gaps[0].deltaSec, HOUR / 1000);
});

test("eventGaps: invalid threshold returns [] instead of throwing or reporting everything", () => {
  const rows = [{ created_at: "2026-09-13T04:00:00Z" }, { created_at: "2026-09-13T09:00:00Z" }];
  assert.deepEqual(eventGaps(rows, NaN), []);
  assert.deepEqual(eventGaps(rows, 0), []);
  assert.deepEqual(eventGaps(rows, -5), []);
  assert.deepEqual(eventGaps(rows, Infinity), []);
  assert.equal(eventGaps(null, 60).length, 0);
  assert.equal(eventGaps(undefined, 60).length, 0);
});

test("eventGaps: non-object rows are skipped, not crash-prone", () => {
  const rows = [null, 7, { created_at: "2026-09-13T04:00:00Z" }, { created_at: "2026-09-13T08:00:00Z" }];
  const gaps = eventGaps(rows, 60);
  assert.equal(gaps.length, 1);
  assert.equal(gaps[0].index, 3);
  assert.equal(gaps[0].previousIndex, 2);
});

// ---------------------------------------------------------------------------
// payloadSummary
// ---------------------------------------------------------------------------

test("payloadSummary: nested structures render one line, depth-limited", () => {
  const payload = {
    a: 1,
    b: { c: { d: { e: "deep" } } },
    list: [1, 2, 3],
    flag: true,
    none: null,
  };
  const s = payloadSummary(payload, 200, 2);
  assert.ok(!s.includes("\n"), `single line expected, got: ${s}`);
  assert.ok(s.includes("a: 1"));
  assert.ok(s.includes("[1, 2, 3]"));
  assert.ok(s.includes("flag: true"));
  assert.ok(s.includes("none: null"));
  assert.ok(s.includes("{…1 keys"), `depth-2 container must collapse: ${s}`);
  assert.ok(s.length <= 200);
});

test("payloadSummary: a raw JSON-string payload stays quoted text (callers parse first)", () => {
  const s = payloadSummary('{"event":"FILL","qty":0.5}', 200, 3);
  assert.ok(s.startsWith('"') && s.endsWith('"'), `string input must render as a quoted string: ${s}`);
  assert.ok(s.includes('\\"event\\"'), `inner quotes must be escaped: ${s}`);
  const parsed = payloadSummary(JSON.parse('{"event":"FILL","qty":0.5}'), 200, 3);
  assert.ok(parsed.includes('event: "FILL"'), parsed);
  assert.ok(parsed.includes("qty: 0.5"), parsed);
});

test("payloadSummary: truncation marker and hard length cap", () => {
  const s = payloadSummary({ note: "x".repeat(500) }, 40, 3);
  assert.equal(s.length, 40);
  assert.ok(s.endsWith("…"));
  assert.equal(payloadSummary("hello", 1), "…");
  assert.equal(payloadSummary(null, 20), "null");
  assert.equal(payloadSummary(undefined, 20), "undefined");
});

test("payloadSummary: control characters are flattened; markup stays inert literal text", () => {
  const s = payloadSummary({ evil: '<script>alert("x")</script>\n\t<img src=x onerror=alert(1)>' }, 400, 3);
  assert.ok(!/[\n\r\t]/.test(s), "no raw control characters survive");
  assert.ok(s.includes("<script>alert"), "markup is preserved verbatim as data (React escapes at render)");
  assert.ok(s.includes("onerror=alert(1)"), "event-handler payload is inert text");
  assert.ok(s.includes('\\"x\\"'), "inner quotes escaped, never a raw structural quote");
  assert.ok(s.length <= 400);
});

test("payloadSummary: circular structures do not hang or throw", () => {
  const a = { name: "a" };
  a.self = a;
  const s = payloadSummary(a, 200, 5);
  assert.ok(s.includes("[circular]"), s);
});

test("payloadSummary: NaN/Infinity stay visible instead of turning into null", () => {
  assert.ok(payloadSummary({ v: Number.NaN }, 100).includes("NaN"));
  assert.ok(payloadSummary({ v: Infinity }, 100).includes("Infinity"));
  assert.ok(payloadSummary({ v: -Infinity }, 100).includes("-Infinity"));
});

test("payloadSummary: oversized containers report how much was elided", () => {
  const big = Array.from({ length: 300 }, (_, i) => i);
  const s = payloadSummary(big, 10_000, 2);
  assert.ok(s.includes("…250 more"), "child cap must be stated, not silently dropped");
  assert.ok(s.length < JSON.stringify(big).length);
});

// ---------------------------------------------------------------------------
// timelinePoints
// ---------------------------------------------------------------------------

test("timelinePoints: relative offsets, span and max gap", () => {
  const rows = [
    { created_at: "2026-09-13T04:00:00Z" },
    { created_at: "2026-09-13T04:00:30Z" },
    { created_at: "2026-09-13T04:05:30Z" },
    { created_at: "2026-09-13T04:06:00Z" },
  ];
  const t = timelinePoints(rows);
  assert.equal(t.points.length, 4);
  assert.equal(t.points[0].offsetMs, 0);
  assert.equal(t.points[1].offsetMs, 30_000);
  assert.equal(t.points[3].offsetMs, 6 * MIN);
  assert.equal(t.spanMs, 6 * MIN);
  assert.equal(t.maxGapMs, 5 * MIN);
  assert.deepEqual(t.maxGapPair, [1, 2]);
  assert.equal(t.skipped, 0);
});

test("timelinePoints: bad rows are counted as skipped and excluded", () => {
  const rows = [
    { created_at: "2026-09-13T04:00:00Z" },
    { created_at: "yesterday-ish" },
    { created_at: null },
    { created_at: "2026-09-13T04:02:00Z" },
  ];
  const t = timelinePoints(rows);
  assert.equal(t.points.length, 2);
  assert.equal(t.skipped, 2);
  assert.equal(t.examined, 4);
  assert.equal(t.maxGapMs, 2 * MIN);
  assert.ok(t.points.every((p) => Number.isFinite(p.offsetMs) && p.offsetMs >= 0));
});

test("timelinePoints: empty / garbage input yields a safe zeroed result", () => {
  for (const input of [[], null, undefined, [null, 5, "x"]]) {
    const t = timelinePoints(input);
    assert.deepEqual(t.points, []);
    assert.equal(t.spanMs, 0);
    assert.equal(t.maxGapMs, 0);
    assert.equal(t.maxGapPair, null);
  }
  assert.equal(timelinePoints([null, 5, "x"]).skipped, 3);
});

test("timelinePoints: single point has zero span and no gap", () => {
  const t = timelinePoints([{ created_at: "2026-09-13T04:00:00Z" }]);
  assert.equal(t.points.length, 1);
  assert.equal(t.points[0].offsetMs, 0);
  assert.equal(t.spanMs, 0);
  assert.equal(t.maxGapMs, 0);
  assert.equal(t.maxGapPair, null);
});

// ---------------------------------------------------------------------------
// escapeCsvCell / buildCsv (expected values verified against the real
// implementation output — see the test run log)
// ---------------------------------------------------------------------------

test("escapeCsvCell: always quotes and doubles internal quotes (RFC-4180)", () => {
  assert.equal(escapeCsvCell('He said "hi"'), '"He said ""hi"""');
  assert.equal(escapeCsvCell('a,"b"'), '"a,""b"""');
  assert.equal(escapeCsvCell("plain"), '"plain"');
});

test("escapeCsvCell: CR/LF and control characters cannot forge a row break", () => {
  assert.equal(escapeCsvCell("line1\r\nline2"), '"line1line2"');
  assert.equal(escapeCsvCell("embedded\nnewline"), '"embeddednewline"');
  assert.equal(escapeCsvCell("unicode\u2028separator"), '"unicodeseparator"');
});

test("escapeCsvCell: formula-leading values are neutralized with a leading apostrophe", () => {
  assert.equal(escapeCsvCell('=HYPERLINK("http://evil","click")'), "\"'=HYPERLINK(\"\"http://evil\"\",\"\"click\"\")\"");
  assert.equal(escapeCsvCell("+CMD|/C calc"), "\"\'+CMD|/C calc\"");
  assert.equal(escapeCsvCell("-1.5"), "\"'-1.5\"");
  assert.equal(escapeCsvCell("@SUM(A1)"), "\"'@SUM(A1)\"");
  assert.equal(escapeCsvCell("\t=cmd|calc"), "\"'=cmd|calc\""); // tab stripped, '=' then leads
});

test("escapeCsvCell: numbers, nulls and structured values behave", () => {
  assert.equal(escapeCsvCell(1.5), '"1.5"');
  assert.equal(escapeCsvCell(0), '"0"');
  assert.equal(escapeCsvCell(-1.5), '"\'-1.5"'); // leading '-' is a formula trigger
  assert.equal(escapeCsvCell(Number.NaN), '""');
  assert.equal(escapeCsvCell(Infinity), '""');
  assert.equal(escapeCsvCell(null), '""');
  assert.equal(escapeCsvCell(undefined), '""');
  assert.equal(escapeCsvCell(true), '"TRUE"');
  assert.equal(escapeCsvCell(["a", "b"]), '"[""a"",""b""]"'); // JSON-encoded, then RFC-4180 quoted
});

test("buildCsv: header + rows, CRLF separators, trailing newline, quoting everywhere", () => {
  const csv = buildCsv(
    ["Ticket", "Note"],
    [[12345, '=cmd|"/C calc"!A0'], [9876, 'has "quotes", comma\nand newline']],
  );
  assert.equal(
    csv,
    '"Ticket","Note"\r\n"12345","\'=cmd|""/C calc""!A0"\r\n"9876","has ""quotes"", commaand newline"\r\n',
  );
  assert.equal(csv.split("\r\n").length, 4, "header + 2 records + trailing empty split piece");
});

/** Minimal RFC-4180 record splitter: quotes-aware, CRLF-aware. */
function parseCsvRecords(text) {
  const records = [];
  let field = "";
  let record = [];
  let inQuotes = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"') inQuotes = true;
    else if (ch === ",") {
      record.push(field);
      field = "";
    } else if (ch === "\r" && text[i + 1] === "\n") {
      record.push(field);
      records.push(record);
      record = [];
      field = "";
      i += 1;
    } else field += ch;
  }
  if (field !== "" || record.length > 0) {
    record.push(field);
    records.push(record);
  }
  return records;
}

test("buildCsv: a hostile round-trip parses back to exactly the cells we sent", () => {
  const hostile = [
    ["=1+1", "+cmd", "-2+3", "@SUM(1+1)"],
    ['quote"inside', "comma,inside", "cr\rinside", "lf\ninside"],
    ["tab\there", "unicode\u2028here", null, undefined],
  ];
  const csv = buildCsv(["a", "b", "c", "d"], hostile);
  const records = parseCsvRecords(csv);
  assert.equal(records.length, hostile.length + 1, "header + 3 data records, no forged rows");
  assert.deepEqual(records[0], ["a", "b", "c", "d"]);
  for (const row of records.slice(1)) assert.equal(row.length, 4, "no embedded comma escaped its quoting");
  // formula payloads come back inert (leading apostrophe) and control chars are gone
  assert.deepEqual(hostile[0].map((_, c) => records[1][c]), ["'=1+1", "'+cmd", "'-2+3", "'@SUM(1+1)"]);
  assert.deepEqual(records[2], ['quote"inside', "comma,inside", "crinside", "lfinside"]);
  assert.deepEqual(records[3], ["tabhere", "unicodehere", "", ""]);
  assert.equal(csv.includes("\n\n"), false);
});

test("buildCsv: garbage container input degrades instead of throwing", () => {
  assert.equal(buildCsv(["a"], null), '"a"\r\n');
  assert.equal(buildCsv(null, [[1]]), '\r\n"1"\r\n'); // empty header line, then the row
});

// ---------------------------------------------------------------------------
// Static safety scan of the component layer (no DOM, no React runtime needed)
// ---------------------------------------------------------------------------

test("AuditTools.tsx contains no innerHTML / dangerouslySetInnerHTML escape hatch", () => {
  const src = read("frontend/src/components/pro/AuditTools.tsx");
  assert.equal(/dangerouslySetInnerHTML/.test(src), false);
  assert.equal(/\.innerHTML/.test(src), false);
  assert.equal(/document\.write/.test(src), false);
  assert.equal(/eval\(/.test(src), false);
  assert.equal(/new Function\(/.test(src), false);
});

test("forensicsMath.ts stays free of I/O, DOM and network dependencies", () => {
  const src = read("frontend/src/lib/forensicsMath.ts");
  assert.equal(/^\s*import\s|require\(/m.test(src), false, "must stay dependency-free for node:test import");
  assert.equal(/\bfetch\(|XMLHttpRequest|localStorage|document\.|window\./.test(src), false);
});
