/**
 * Safe user-entered NUMBER parsing (i18n rule §22).
 *
 * Users in different locales type decimals differently: "4012.35" (en),
 * "4012,35" (fa/de/es). The raw `Number("4012,35")` is NaN — which silently
 * serialized to JSON null on the order path — so every user-typed numeric
 * field must parse through here instead of Number()/parseFloat() directly.
 *
 * DELIBERATE POLICY (never guess thousands):
 *  - whitespace / NNBSP grouping spaces are stripped ("4 012,35" -> 4012.35)
 *  - BOTH separators present  -> the RIGHTMOST one is the decimal mark
 *    ("4,012.35" -> 4012.35, "4.012,35" -> 4012.35)
 *  - COMMA only               -> decimal comma ("4012,35" -> 4012.35)
 *  - DOT only                 -> decimal dot ("4012.35" -> 4012.35)
 *  - empty / unparseable      -> NaN (call site decides: validation error,
 *    or its own explicit empty-string rule like SL/TP "" -> remove level)
 *
 * Rationale for comma-means-decimal: price/percent entry boxes conventionally
 * take raw digits without en thousands commas, while fa/de/es users MUST be
 * able to type a decimal comma; rejecting or misreading their input is the
 * worse failure. Call sites must still validate ranges and surface a
 * localized message on NaN — never forward NaN/undefined downstream.
 *
 * PRESENTATION ONLY: this affects only what the user typed this session; it
 * never rewrites stored, loaded, or API-provided values.
 */

/** Parse a user-typed numeric string under the policy above. NaN when empty/invalid. */
export function parseUserNumber(raw: string): number {
  let s = raw.trim().replace(/[\s\u00A0\u202F]/g, "");
  if (s === "") return NaN;
  const lastDot = s.lastIndexOf(".");
  const lastComma = s.lastIndexOf(",");
  if (lastDot >= 0 && lastComma >= 0) {
    // Both present: rightmost separator is the decimal mark; drop the other.
    if (lastDot > lastComma) s = s.replace(/,/g, "");
    else s = s.replace(/\./g, "").replace(",", ".");
  } else if (lastComma >= 0) {
    s = s.replace(",", "."); // comma-only -> decimal comma
  }
  const n = Number(s);
  return Number.isFinite(n) ? n : NaN;
}

/**
 * Parse a user-typed INTEGER (epochs, trials, seeds, sequence numbers).
 * Same separator policy, then round: "12" -> 12, "12,7" -> 13, "1.234,5" -> 1235.
 */
export function parseUserInt(raw: string): number {
  const n = parseUserNumber(raw);
  return Number.isFinite(n) ? Math.round(n) : NaN;
}

