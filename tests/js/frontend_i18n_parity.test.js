/**
 * Frontend i18n parity gate — i18n wave 2026-09.
 *
 * Asserts the React console (frontend/src) can render fully in EVERY
 * supported language (fa/de/es/ar; en = identity fallback):
 *   1. every LITERALLY-CALLED t("key", "fallback") key has fa+de+es+ar
 *      entries (in <scope>/i18n.ts messages or the legacy chrome DICTS),
 *   2. chrome literals (ux./nav./common.) and derived nav.feature.<route>
 *      keys (from features/<slug>/index.ts routes) are covered too,
 *   3. {placeholder} sets match the English source in every translation
 *      (a dropped {s} silently corrupts the sentence),
 *   4. every <scope>/i18n.ts message entry is well-formed (all 4 langs),
 *   5. dynamic t() keys and dead message entries are reported (warn only).
 *
 * Pure Node (CJS, stdlib only) — runs in CI's tests/js/*.test.js glob and
 * locally: node tests/js/frontend_i18n_parity.test.js
 */
"use strict";
const fs = require("fs");
const path = require("path");

const SRC = path.join(__dirname, "..", "..", "frontend", "src");
const LANGS = ["fa", "de", "es", "ar"];
const failures = [];
const warnings = [];

function walk(dir, out) {
  out = out || [];
  if (!fs.existsSync(SRC)) {
    failures.push(`frontend/src not found at ${SRC}`);
    return out;
  }
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    const st = fs.statSync(full);
    if (st.isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(name)) out.push(full);
  }
  return out;
}

function unquote(js) {
  try { return JSON.parse(js); } catch (_) { return js.slice(1, -1); }
}

function placeholders(s) {
  const m = s.match(/\{(\w+)\}/g) || [];
  return new Set(m.map((x) => x.slice(1, -1)));
}

const files = walk(SRC);

/* ---------- 1. used keys: literal t("key", "fallback") calls ---------- */
const used = new Map(); // key -> fallback (string) | "" when unknown
const dynamic = [];
const T_CALL = /\bt\(\s*(["'])([a-z][a-z0-9_]*(?:\.[A-Za-z0-9_-]+)+)\1\s*,\s*(["'])((?:[^"'\\]|\\.)*)\3/g;
const T_ANY = /\bt\(\s*(["'`$a-zA-Z_])/g;

/* ---------- 2. chrome literals (ux./nav./common. string values) -------- */
const CHROME = /(["'])((?:ux|nav|common)\.[A-Za-z0-9_.-]+)\1/g;

/* ---------- 3. derived nav.feature.<slug> from feature routes ---------- */
const expectedFeatureKeys = [];
const registry = path.join(SRC, "features");
if (fs.existsSync(registry)) {
  for (const slug of fs.readdirSync(registry)) {
    const idx = path.join(registry, slug, "index.ts");
    if (!fs.existsSync(idx)) continue;
    const txt = fs.readFileSync(idx, "utf8");
    const r = txt.match(/route:\s*"([^"]+)"/);
    if (r) expectedFeatureKeys.push(`nav.feature.${r[1].replace(/^\//, "") || "home"}`);
  }
}

for (const f of files) {
  // strip full-line comments: doc-header examples (t("scope.x.y", ...)) and
  // doc prose mentioning chrome keys are not call sites.
  const txt = fs.readFileSync(f, "utf8")
    .split("\n")
    .filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l))
    .join("\n");
  const rel = path.relative(SRC, f).replace(/\\/g, "/");
  if (rel === "lib/i18n.ts") continue; // parsed separately below

  let m;
  T_CALL.lastIndex = 0;
  while ((m = T_CALL.exec(txt)) !== null) {
    if (!used.has(m[2])) used.set(m[2], unquote(`"${m[4]}"`));
  }
  // dynamic t( first-arg (not a string literal) — report, don't fail
  T_ANY.lastIndex = 0;
  while ((m = T_ANY.exec(txt)) !== null) {
    const q = m[1];
    if (q === '"' || q === "'") continue; // literal handled above (or malformed key)
    const line = txt.slice(0, m.index).split("\n").length;
    dynamic.push(`${rel}:${line}`);
  }
  CHROME.lastIndex = 0;
  while ((m = CHROME.exec(txt)) !== null) {
    if (!used.has(m[2])) used.set(m[2], "");
  }
}
for (const k of expectedFeatureKeys) if (!used.has(k)) used.set(k, "");

/* ---------- 4. parse <scope>/i18n.ts message modules ------------------- */
const messages = new Map(); // key -> {fa,de,es,ar,en?}
const ENTRY =
  /"([A-Za-z0-9_.-]+)"\s*:\s*\{\s*fa:\s*("(?:[^"\\]|\\.)*")\s*,\s*de:\s*("(?:[^"\\]|\\.)*")\s*,\s*es:\s*("(?:[^"\\]|\\.)*")\s*,\s*ar:\s*("(?:[^"\\]|\\.)*")\s*(?:,\s*en:\s*("(?:[^"\\]|\\.)*"))?\s*\}/g;
const ENTRY_START = /"([A-Za-z0-9_.-]+)"\s*:\s*\{/g;
const messageFiles = files.filter((f) => /i18n\.ts$/.test(f) && !/[\\/]lib[\\/]i18n\.ts$/.test(f));
let scopeCount = 0;
for (const f of messageFiles) {
  scopeCount++;
  const txt = fs.readFileSync(f, "utf8");
  const rel = path.relative(SRC, f).replace(/\\/g, "/");
  const matchedKeys = new Set();
  let m;
  ENTRY.lastIndex = 0;
  while ((m = ENTRY.exec(txt)) !== null) {
    matchedKeys.add(m[1]);
    const key = m[1];
    if (messages.has(key)) warnings.push(`duplicate message key across scopes: ${key} (${rel})`);
    const entry = { fa: unquote(m[2]), de: unquote(m[3]), es: unquote(m[4]), ar: unquote(m[5]) };
    if (m[6]) entry.en = unquote(m[6]);
    messages.set(key, entry);
  }
  // any entry-start the ENTRY regex did not consume = malformed entry
  ENTRY_START.lastIndex = 0;
  while ((m = ENTRY_START.exec(txt)) !== null) {
    if (m[1] !== "MESSAGES" && !matchedKeys.has(m[1])) {
      const line = txt.slice(0, m.index).split("\n").length;
      failures.push(`malformed message entry "${m[1]}" in ${rel}:${line} — required single entry shape: "key": { fa: "...", de: "...", es: "...", ar: "..." }`);
    }
  }
}

/* ---------- 5. parse legacy chrome DICTS in lib/i18n.ts ---------------- */
const dicts = { fa: {}, de: {}, es: {}, ar: {} };
{
  const txt = fs.readFileSync(path.join(SRC, "lib", "i18n.ts"), "utf8");
  let lang = null;
  for (const line of txt.split("\n")) {
    const open = line.match(/^  (fa|de|es|ar):\s*\{/);
    if (open) { lang = open[1]; continue; }
    if (lang && /^  \},?\s*$/.test(line)) { lang = null; continue; }
    if (lang) {
      const e = line.match(/^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)",?\s*$/);
      if (e) dicts[lang][e[1]] = unquote(`"${e[2]}"`);
    }
  }
}

/* ---------- 6. coverage: every used key x every language --------------- */
const missing = [];
for (const [key] of used) {
  for (const lang of LANGS) {
    const inDict = Object.prototype.hasOwnProperty.call(dicts[lang], key);
    const inMsg = messages.has(key) && typeof messages[key][lang] === "string";
    if (!inDict && !inMsg) missing.push(`${key} [${lang}]`);
  }
}
if (missing.length) {
  failures.push(
    `MISSING translations for ${missing.length} key/lang pairs (add to your scope's i18n.ts or lib/i18n.ts chrome dict):\n    ` +
      missing.slice(0, 400).join("\n    ") +
      (missing.length > 60 ? `\n    ... and ${missing.length - 400} more` : ""),
  );
}

/* ---------- 7. placeholder parity vs English source ------------------- */
let phChecked = 0;
for (const [key, entry] of messages) {
  const enSource = entry.en !== undefined ? entry.en : used.get(key);
  if (enSource === undefined || enSource === "") continue;
  const want = placeholders(enSource);
  for (const lang of LANGS) {
    const val = entry[lang];
    if (typeof val !== "string") continue;
    phChecked++;
    const got = placeholders(val);
    const missingPh = [...want].filter((x) => !got.has(x));
    const extraPh = [...got].filter((x) => !want.has(x));
    if (missingPh.length || extraPh.length) {
      failures.push(
        `placeholder mismatch ${key} [${lang}]: missing={${missingPh.join(",")}} extra={${extraPh.join(",")}} (en: "${enSource}")`,
      );
    }
  }
  // verbatim-untranslated heuristic (multi-word only; single words like "System" are legit)
  for (const lang of LANGS) {
    const val = entry[lang];
    if (typeof val === "string" && val === enSource && /\s/.test(val) && val.length > 12) {
      warnings.push(`possibly untranslated ${key} [${lang}]: "${val}"`);
    }
  }
}
// chrome (DICTS-only) keys with literal fallbacks also get placeholder checks
for (const [key, fb] of used) {
  if (messages.has(key) || fb === "") continue;
  const want = placeholders(fb);
  for (const lang of LANGS) {
    const val = dicts[lang][key];
    if (typeof val !== "string") continue;
    phChecked++;
    const got = placeholders(val);
    const missingPh = [...want].filter((x) => !got.has(x));
    const extraPh = [...got].filter((x) => !want.has(x));
    if (missingPh.length || extraPh.length) {
      failures.push(
        `placeholder mismatch ${key} [${lang}]: missing={${missingPh.join(",")}} extra={${extraPh.join(",")}} (en: "${fb}")`,
      );
    }
  }
}

/* ---------- 8. dead message entries (never referenced literally) ------- */
const dead = [...messages.keys()].filter((k) => !used.has(k));
if (dead.length) warnings.push(`dead message entries (no literal t() call): ${dead.join(", ")}`);

/* ---------- report ------------------------------------------------------ */
console.log(`frontend i18n parity: ${files.length} src files, ${scopeCount} message scopes,`);
console.log(`  ${messages.size} message keys + chrome dicts (fa:${Object.keys(dicts.fa).length} de:${Object.keys(dicts.de).length} es:${Object.keys(dicts.es).length} ar:${Object.keys(dicts.ar).length})`);
console.log(`  ${used.size} used keys, ${phChecked} placeholder checks, ${expectedFeatureKeys.length} derived nav.feature keys`);
if (dynamic.length) console.log(`  NOTE ${dynamic.length} dynamic t() calls (keys must exist via labelKey conventions): ${dynamic.slice(0, 8).join(", ")}${dynamic.length > 8 ? ", ..." : ""}`);
for (const w of warnings) console.log(`  [WARN] ${w}`);
if (failures.length) {
  console.log(`\nFAIL frontend i18n parity (${failures.length} problems):`);
  for (const f of failures) console.log(`  [FAIL] ${f}`);
  process.exit(1);
}
console.log("PASS frontend i18n parity");
