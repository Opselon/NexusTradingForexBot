/**
 * i18n-complete wave — language resolution, persistence and direction (§09-§11, §24).
 *
 * Contracts pinned here:
 * 1. detectLang() precedence: saved localStorage preference wins over
 *    navigator.language (the selector must override browser detection §10).
 * 2. Unknown/safe-default: an unparseable saved value or an unsupported
 *    browser language never breaks the UI — it resolves to en.
 * 3. persistLang writes localStorage["nexus.ui.lang"] (§11).
 * 4. A persisted preference is honored by a later detectLang() (refresh/restart
 *    §63/§64) — this is the persistence round-trip, not a re-import trick.
 * 5. isRtl marks fa/ar RTL and en/de/es LTR (§23) — direction is derived from
 *    the language, never a second hand-toggled state.
 * 6. translate falls back to the literal when a key is absent (§12 — never
 *    undefined/blank/missing.key).
 *
 * The module resolves localStorage/navigator lazily at CALL time, so a fake
 * localStorage installed on globalThis before each call is honored. The full
 * API (detectLang/persistLang/isRtl/LANGUAGES) is only exported when the
 * browser globals exist — install them once before import.
 *
 * Run: node --test tests/js/frontend_i18n_lang.test.js
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const url = require("node:url");

const FRONTEND = path.resolve(__dirname, "..", "..", "frontend");
const i18nUrl = url.pathToFileURL(path.join(FRONTEND, "src", "lib", "i18n.ts")).href;

// --- Browser-global shim -------------------------------------------------
// Node 22+ ships a REAL navigator whose `language` is read-only, so override
// it with an own configurable property. localStorage does not exist at all in
// Node, so install one (values kept in a module-level map).
let navLang = undefined;
let mem = {};
const store = {
  getItem: (k) => (Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null),
  setItem: (k, v) => {
    mem[k] = String(v);
  },
  removeItem: (k) => {
    delete mem[k];
  },
  clear: () => {
    mem = {};
  },
};
if (typeof globalThis.localStorage === "undefined") globalThis.localStorage = store;
// The module reads window.localStorage; browsers have window === globalThis.
if (typeof globalThis.window === "undefined") globalThis.window = globalThis;
Object.defineProperty(globalThis.navigator, "language", {
  configurable: true,
  get: () => navLang,
});
Object.defineProperty(globalThis.navigator, "languages", {
  configurable: true,
  get: () => (navLang ? [navLang] : []),
});

const api = () => import(i18nUrl);

const setNav = (lang) => {
  navLang = lang;
};

test("detectLang: saved preference beats navigator.language (§10)", async () => {
  const m = await api();
  globalThis.localStorage.clear();
  globalThis.localStorage.setItem("nexus.ui.lang", "fa");
  setNav("de-DE");
  assert.equal(m.detectLang(), "fa", "saved fa must win over browser de-DE");
});

test("detectLang: browser language used when nothing saved", async () => {
  const m = await api();
  globalThis.localStorage.clear();
  setNav("de-DE");
  assert.equal(m.detectLang(), "de");
});

test("detectLang: unsupported browser language falls back to en", async () => {
  const m = await api();
  globalThis.localStorage.clear();
  setNav("ja-JP");
  assert.equal(m.detectLang(), "en", "unsupported locale must not break the UI");
});

test("detectLang: garbage saved value falls back safely to en", async () => {
  const m = await api();
  globalThis.localStorage.clear();
  globalThis.localStorage.setItem("nexus.ui.lang", "not-a-locale");
  setNav(undefined);
  assert.equal(m.detectLang(), "en");
});

test("persistLang writes the shared preference key (§11)", async () => {
  const m = await api();
  globalThis.localStorage.clear();
  m.persistLang("ar");
  assert.equal(globalThis.localStorage.getItem("nexus.ui.lang"), "ar");
});

test("persisted preference is honored by a later detectLang (§63 refresh/restart)", async () => {
  const m = await api();
  globalThis.localStorage.clear();
  m.persistLang("fa");
  // A reload re-runs only detectLang() — storage is what survives.
  setNav("en-US");
  assert.equal(m.detectLang(), "fa");
});

test("isRtl: fa/ar are RTL, en/de/es are LTR (§23)", async () => {
  const m = await api();
  assert.equal(m.isRtl("fa"), true);
  assert.equal(m.isRtl("ar"), true);
  assert.equal(m.isRtl("en"), false);
  assert.equal(m.isRtl("de"), false);
  assert.equal(m.isRtl("es"), false);
});

test("translate: fallback string used when a key is absent (§12 — never undefined/blank)", async () => {
  const m = await api();
  assert.equal(m.translate("fa", "no.such.key.ever", "Safe English fallback"), "Safe English fallback");
});
