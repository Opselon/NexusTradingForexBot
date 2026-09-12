/**
 * safeHtml.ts — the ONLY sanctioned URL guard for the alt console (SEC-2/3).
 *
 * Policy (mirrored by the source gate in
 * tests/unit/test_alt_ui_xss_hygiene.py):
 *   - Never assign to innerHTML/outerHTML, never use dangerouslySetInnerHTML,
 *     document.write, eval, new Function or insertAdjacentHTML. Text reaches
 *     the DOM only through React-managed text nodes, which escape by
 *     construction.
 *   - Every href/src/window.open/location sink whose value comes from data
 *     (API payloads, SSE snapshots, query strings, props) must first pass
 *     through one of the guards below. The gate whitelists only a static
 *     string literal or "#"; a variable counts as safe only when its
 *     assignment in the same file calls one of these guards.
 *
 * The export names `safeExternalUrl`, `safeInternalPath` and
 * `isSafeGuardedUrl` are load-bearing: the pytest sink scan whitelists them by
 * name, so renaming them turns every guarded link into a gate failure.
 *
 * This module is deliberately *erasable TypeScript* (Node >= 22.6 type
 * stripping: annotations only — no enums, parameter properties or namespaces)
 * so the Python gate can execute it directly with the local node and pin its
 * behaviour against a payload battery.
 */

/** ASCII whitespace + C0/C1 controls plus NBSP: browser-tolerated noise. */
const CONTROL_CHARS = /[\u0000-\u0020\u007f-\u009f\u00a0]/g;

/**
 * Schemes that execute script or smuggle markup when placed in a URL
 * attribute. Matched case-insensitively after control-character removal.
 */
const SCRIPTABLE_SCHEME =
  /^(?:javascript|data|vbscript|livescript|mocha|view-source|blob|filesystem|file):/i;

/** True when the raw value contains any control or whitespace character. */
function hasControlChars(value: string): boolean {
  return /[\u0000-\u0020\u007f-\u009f\u00a0]/.test(value);
}

/** Browsers ignore control characters inside a URL scheme, so we must too. */
function deobfuscate(value: string): string {
  return value.replace(CONTROL_CHARS, "");
}

/**
 * Absolutise and vet an OUTBOUND external URL.
 *
 * @returns a normalised `https:` URL string, or `null` when the input cannot
 *   be trusted (fail closed — callers render inert text instead of a link).
 *
 * Rejects: anything that is not an absolute URL, every protocol other than
 * `https:` (so plain `http:`, and the scriptable schemes `javascript:`,
 * `data:`, `vbscript:` and friends, including control-character obfuscations
 * such as `java\u0000script:`), and non-string input.
 *
 * Usage:
 *   const href = safeExternalUrl(row.evidence_url);
 *   {href ? <a href={href} rel="noopener noreferrer">evidence</a>
 *         : <span>{row.evidence_ref}</span>}
 */
export function safeExternalUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const candidate = deobfuscate(value).trim();
  if (candidate === "") return null;
  if (SCRIPTABLE_SCHEME.test(candidate)) return null;
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    return null; // relative, malformed or plain text: never an external link
  }
  if (parsed.protocol !== "https:") return null;
  if (SCRIPTABLE_SCHEME.test(deobfuscate(parsed.href))) return null;
  // WHATWG normalisation re-encodes credentials/host/path, so the returned
  // string cannot carry the raw attacker bytes into the attribute.
  return parsed.href;
}

/**
 * Vet an IN-APP path for `<a href>`, a router link or an asset `src` that must
 * stay on this origin.
 *
 * @returns the identical path string when safe, otherwise `null`
 *   (fail closed — never a sanitised guess of an unsafe value).
 *
 * Rules, in order:
 *   - non-strings and empty strings are rejected;
 *   - any control character or whitespace anywhere is rejected (so
 *     `java\nscript:` cannot survive by naive prefix tests — we do not strip
 *     inside a path, because a path has no business containing them);
 *   - a scriptable scheme is rejected case-insensitively: `javascript:`,
 *     `data:`, `vbscript:` and the siblings listed in SCRIPTABLE_SCHEME;
 *   - it must begin with EXACTLY one `/`: `//host/x` is protocol-relative and
 *     leaves the origin, `host/x` is relative-document noise;
 *   - backslashes are rejected: some browsers fold `\` into `/`, turning
 *     `/\evil.example` into an off-origin navigation.
 *
 * Usage:
 *   const to = safeInternalPath(payload.redirect) ?? "/";
 */
export function safeInternalPath(value: unknown): string | null {
  if (typeof value !== "string" || value === "") return null;
  if (hasControlChars(value)) return null;
  if (SCRIPTABLE_SCHEME.test(value)) return null;
  if (SCRIPTABLE_SCHEME.test(deobfuscate(value))) return null;
  if (!value.startsWith("/")) return null;
  if (value.startsWith("//")) return null;
  if (value.includes("\\")) return null;
  return value;
}

/**
 * Predicate for generic link renderers: does one of the guards accept this
 * value (external `https:` or same-origin path)? Anything else — including a
 * bare `blob:` or `data:` — fails closed.
 */
export function isSafeGuardedUrl(value: unknown): boolean {
  return safeExternalUrl(value) !== null || safeInternalPath(value) !== null;
}
