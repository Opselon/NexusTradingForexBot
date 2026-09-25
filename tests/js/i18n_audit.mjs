/**
 * i18n-complete wave — automated i18n audit tool (rules §13, §14, §15, §58).
 *
 * Parses the TS i18n surface directly (this product has no JSON bundles:
 * message tables live in <scope>/i18n.ts as `MESSAGES: FeatureMessages = {...}`)
 * and reports:
 *   - MISSING KEYS:      a t("key", ...) call site whose key exists in NO
 *                        scope and NO chrome dict
 *   - EXTRA KEYS:        a scoped key no call site uses (informational only,
 *                        never fails CI — dead weight, not a defect)
 *   - LOCALE PARITY:     fa/de/es/ar coverage % of scoped keys (§14)
 *   - INTERPOLATION:     every locale of a key must use the exact same {var}
 *                        set (§15: missing / extra / renamed variables)
 *   - DUPLICATE KEYS:    same key defined in two scopes (ambiguous lookup)
 *   - MALFORMED:         empty translation, or stray angle-bracket template
 *                        marker (§12/§57: '<English>' placeholders must not ship)
 *
 * Chrome DICTS (frontend/src/lib/i18n.ts legacy ux. nav. meta. tables) are
 * treated as pre-covered for the call-site existence check — their per-locale
 * parity is enforced by tests/js/frontend_i18n_parity.test.js already.
 *
 * Exit 1 on MISSING / INTERPOLATION / DUPLICATE / MALFORMED; else 0.
 *
 * Usage: node tests/js/i18n_audit.mjs [--root <repo>] [--json <out.json>]
 */
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { basename, join, relative, resolve } from "node:path";

// ------------------------------------------------------------------ args
const argv = process.argv.slice(2);
const flag = (name) => (argv.includes(name) ? argv[argv.indexOf(name) + 1] : null);
const ROOT = resolve(flag("--root") || process.cwd());
const JSON_OUT = flag("--json");

// ------------------------------------------------------------ file walk
function listFiles(dir, out = []) {
  let names;
  try {
    names = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of names) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name === "node_modules" || name === "dist" || name === ".git") continue;
      listFiles(p, out);
    } else if (/\.(ts|tsx)$/.test(name)) {
      out.push(p);
    }
  }
  return out;
}

// --------------------------------------------------- MESSAGES body parser
/** Balanced-brace slice of the `MESSAGES ... = {` object, strings respected. */
function messagesBody(source) {
  const idx = source.search(/MESSAGES\s*(?::[^=]+)?=/);
  if (idx < 0) return null;
  const brace = source.indexOf("{", idx);
  if (brace < 0) return null;
  let depth = 0;
  let inStr = false;
  let quote = "";
  for (let i = brace; i < source.length; i++) {
    const c = source[i];
    if (inStr) {
      if (c === "\\") i++;
      else if (c === quote) inStr = false;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") {
      inStr = true;
      quote = c;
      continue;
    }
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) return source.slice(brace, i + 1);
    }
  }
  return null;
}

const LOCALES = ["fa", "de", "es", "ar"];

/**
 * Tokenize one `MESSAGES` object body. Entries look like
 *   "key.name": { fa: "…{var}…", de: '…', es: "…", ar: "…" },
 * locale keys may be quoted or bare; values may contain {var} braces and
 * escaped quotes — none of which break this scanner.
 */
function parseEntries(body) {
  const entries = {};
  let p = 0;
  const ws = () => {
    // whitespace + line/block comments (comment text can contain ':' and
    // quotes — it must be skipped whole, never tokenized)
    for (;;) {
      while (p < body.length && /\s/.test(body[p])) p++;
      if (body[p] === "/" && body[p + 1] === "/") {
        while (p < body.length && body[p] !== "\n") p++;
        continue;
      }
      if (body[p] === "/" && body[p + 1] === "*") {
        const end = body.indexOf("*/", p + 2);
        p = end < 0 ? body.length : end + 2;
        continue;
      }
      break;
    }
  };
  const readString = () => {
    const q = body[p];
    p++;
    let out = "";
    while (p < body.length && body[p] !== q) {
      if (body[p] === "\\") {
        out += body[p + 1];
        p += 2;
      } else {
        out += body[p];
        p++;
      }
    }
    p++;
    return out;
  };
  const skipValue = () => {
    // non-string value: step past nested structures to the next , or }
    let depth = 0;
    while (p < body.length) {
      const c = body[p];
      if (c === '"' || c === "'") {
        readString();
        continue;
      }
      if (c === "{") depth++;
      if (c === "}") {
        if (depth === 0) return;
        depth--;
      }
      if (c === "," && depth === 0) return;
      p++;
    }
  };
  const token = () => {
    ws();
    if (body[p] === '"') return readString();
    const m = /^[\w$]+/.exec(body.slice(p));
    if (!m) return null;
    p += m[0].length;
    return m[0];
  };

  p = body.indexOf("{") + 1; // skip outer {
  while (p < body.length) {
    ws();
    if (body[p] === "}" ) break;
    if (body[p] === "," || body[p] === ";") {
      p++;
      continue;
    }
    const key = token();
    if (key === null) {
      p++;
      continue;
    }
    ws();
    if (body[p] !== ":") {
      p++; // stray token outside a comment — bounded progress, keep scanning
      continue;
    }
    p++;
    ws();
    if (body[p] !== "{") {
      skipValue(); // scalar entry (chrome-dict shape) — not a locale table
      continue;
    }
    p++;
    const vals = {};
    while (p < body.length && body[p] !== "}") {
      ws();
      if (body[p] === ",") {
        p++;
        continue;
      }
      const loc = token();
      if (loc === null) {
        p++;
        continue;
      }
      ws();
      if (body[p] !== ":") {
        continue;
      }
      p++;
      ws();
      if (body[p] !== '"' && body[p] !== "'" && body[p] !== "`") {
        skipValue();
        continue;
      }
      const value = readString();
      if (LOCALES.includes(loc) || loc === "en") vals[loc] = value;
      ws();
      if (body[p] === ",") p++;
    }
    p++; // closing }
    entries[key] = vals;
  }
  return entries;
}

/** {var} placeholders in a message. */
function varsOf(s) {
  const out = new Set();
  for (const m of String(s).matchAll(/\{(\w+)\}/g)) out.add(m[1]);
  return out;
}

// ------------------------------------------------------------------ scan
const srcDir = join(ROOT, "frontend", "src");
const files = listFiles(srcDir);

// 1. scoped message tables (every <scope>/i18n.ts exporting MESSAGES)
const scopes = [];
for (const f of files) {
  if (basename(f) !== "i18n.ts" || f === join(srcDir, "lib", "i18n.ts")) continue;
  const body = messagesBody(readFileSync(f, "utf8"));
  if (!body) continue;
  const entries = parseEntries(body);
  if (Object.keys(entries).length) scopes.push({ path: f, entries });
}

// 2. chrome keys from lib/i18n.ts (pre-covered: parity gate owns their locales)
const chromeKeys = new Set();
try {
  const chromeSrc = readFileSync(join(srcDir, "lib", "i18n.ts"), "utf8");
  for (const m of chromeSrc.matchAll(/"([a-z][a-z0-9_]*(?:\.[A-Za-z0-9_-]+)+)"\s*:/g)) {
    chromeKeys.add(m[1]);
  }
} catch {
  /* lib/i18n.ts missing — chrome set stays empty */
}

// 3. call-site keys + their English fallback literal (for existence AND the
//    §57 verbatim-copy check: a fa/de/es/ar value equal to the en fallback
//    of a phrase (>=15 chars, contains a space) is untranslated, not done)
const callSiteKeys = new Set();
const callSiteFallback = new Map();
for (const f of files) {
  if (basename(f) === "i18n.ts") continue; // definitions, not call sites
  const src = readFileSync(f, "utf8");
  for (const m of src.matchAll(/\bt\(\s*(["'])([^"'\\]+)\1/g)) callSiteKeys.add(m[2]);
  for (const m of src.matchAll(/\bt\(\s*(["'])[^"'\\]+\1\s*,\s*(["'])((?:[^"'\\]|\\.)*)\2/g)) {
    if (!callSiteFallback.has(m[3])) callSiteFallback.set(m[3], m[1] === m[2] ? m[3] : null);
  }
}

const report = { missing: [], extra: [], parity: {}, interpolation: [], duplicate: [], malformed: [], untranslated_copy: [] };
const keyToScope = new Map();

for (const { path: p, entries } of scopes) {
  const rel = relative(ROOT, p);
  for (const [key, vals] of Object.entries(entries)) {
    if (keyToScope.has(key)) report.duplicate.push({ key, scopes: [keyToScope.get(key), rel] });
    else keyToScope.set(key, rel);

    if (!callSiteKeys.has(key)) report.extra.push({ key, scope: rel });

    const present = LOCALES.filter((l) => typeof vals[l] === "string");
    for (const l of LOCALES) {
      if (!present.includes(l)) {
        report.missing.push({ key, locale: l, scope: rel });
        report.parity[l] = (report.parity[l] || 0) + 1;
      }
    }

    if (present.length) {
      const ref = varsOf(vals[present[0]]);
      for (const l of present) {
        const v = varsOf(vals[l]);
        if (v.size !== ref.size || [...v].some((x) => !ref.has(x))) {
          report.interpolation.push({ key, locale: l, expected: [...ref], got: [...v], scope: rel });
        }
      }
    }

    for (const l of present) {
      const s = vals[l];
      if (s.trim() === "") report.malformed.push({ key, locale: l, reason: "empty", scope: rel });
      // whole-value placeholder only: fa: "<English source string>" shipping
      // as the message. Inline id-format prose like STRAT-<hash …> (with
      // translated bracket content) is legitimate and must NOT be flagged.
      if (/^\s*<[A-Za-z][^<>]*>\s*$/.test(s)) {
        report.malformed.push({ key, locale: l, reason: "placeholder is the whole value", scope: rel });
      }
      // §57 fake completeness: locale value is a verbatim copy of the English
      // call-site fallback for a real phrase -> it was never translated.
      const fb = callSiteFallback.get(key);
      if (fb && fb.length >= 15 && fb.includes(" ") && s === fb) {
        report.untranslated_copy.push({ key, locale: l, fallback: fb, scope: rel });
      }
    }
  }
}

// 4. call-site keys that exist NOWHERE
for (const k of callSiteKeys) {
  if (!keyToScope.has(k) && !chromeKeys.has(k)) {
    report.missing.push({ key: k, locale: "ALL", scope: "call-site (no scope, no chrome dict)" });
  }
}

// ---------------------------------------------------------------- report
const totalScoped = keyToScope.size;
const parityPct = {};
for (const l of LOCALES) {
  const miss = report.parity[l] || 0;
  parityPct[l] = totalScoped ? Math.round(((totalScoped - miss) / totalScoped) * 100) : 100;
}

const out = [];
out.push("=== NSE i18n audit ===");
out.push(`scopes parsed: ${scopes.length}   scoped keys: ${totalScoped}   call-site keys: ${callSiteKeys.size}   chrome keys: ${chromeKeys.size}`);
out.push("");
out.push("LOCALE PARITY (scoped keys)");
for (const l of LOCALES) out.push(`  ${l}: ${parityPct[l]}%`);
out.push("");
const sect = (title, arr, fmt) => {
  out.push(`${title}: ${arr.length}`);
  for (const e of arr.slice(0, 40)) out.push("  " + fmt(e));
  if (arr.length > 40) out.push(`  ... +${arr.length - 40} more`);
  out.push("");
};
sect("MISSING KEYS", report.missing, (e) => `${e.key} [${e.locale}] @ ${e.scope}`);
sect("EXTRA KEYS (unused, CI-safe)", report.extra, (e) => `${e.key} @ ${e.scope}`);
sect("INTERPOLATION MISMATCH", report.interpolation, (e) => `${e.key} [${e.locale}] expected {${e.expected.join(",")}} got {${e.got.join(",")}}`);
sect("DUPLICATE KEYS", report.duplicate, (e) => `${e.key} in ${e.scopes.join(" + ")}`);
sect("MALFORMED", report.malformed, (e) => `${e.key} [${e.locale}] ${e.reason} @ ${e.scope}`);
sect("UNTRANSLATED COPIES (verbatim en fallback)", report.untranslated_copy, (e) => `${e.key} [${e.locale}] "${e.fallback.slice(0, 60)}" @ ${e.scope}`);

const hard =
  report.missing.length +
  report.interpolation.length +
  report.duplicate.length +
  report.malformed.length +
  report.untranslated_copy.length;
out.push(`HARD FAILURES: ${hard}`);
out.push(hard === 0 ? "PASS i18n audit" : "FAIL i18n audit");
console.log(out.join("\n"));
if (JSON_OUT) writeFileSync(JSON_OUT, JSON.stringify({ ...report, parityPct, totalScoped }, null, 2));
process.exit(hard === 0 ? 0 : 1);
