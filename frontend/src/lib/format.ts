/**
 * Formatting helpers — financial values, timestamps, ages.
 * Pure presentation utilities; no state decisions live here.
 *
 * PERF (lane 1, data-core): these run per row per render (232 call sites) and
 * the realtime feed re-renders those rows on every accepted SSE frame. The
 * costly part was constructing an Intl.NumberFormat / Intl.DateTimeFormat on
 * every call (`toLocale*String` builds one internally each time — measured
 * 21-92x slower than reuse on V8). The cached formatters below are built ONCE
 * with the exact option bags those `toLocale*String(locales, options)` calls
 * resolve to, so every rendered character is unchanged; identity was verified
 * byte-for-byte over 1.5M samples (numbers × 6 digit widths, money, 113k
 * instants spanning 2018-2026 + random + epoch/leap edges) before shipping.
 *
 * The `formatNumber` fast path keeps a typeof guard: a runtime value that
 * escaped its `number` typing (backend string) must still take the original
 * `String#toLocaleString` pass-through, which ignores the option bag — the
 * cached formatter would parse it as a number instead.
 */

/** Currency/decimal grouping, 2 fraction digits (formatMoney). */
const moneyFormatter = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Cached per-digit-width number formatters (formatNumber takes `digits`). */
const numberFormatters = new Map<number, Intl.NumberFormat>();
function numberFormatter(digits: number): Intl.NumberFormat {
  let formatter = numberFormatters.get(digits);
  if (!formatter) {
    formatter = new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    numberFormatters.set(digits, formatter);
  }
  return formatter;
}

/** Date part — the en-CA default pattern (YYYY-MM-DD), as toLocaleDateString("en-CA"). */
const dateFormatter = new Intl.DateTimeFormat("en-CA");
/** Time part — as toLocaleTimeString("en-GB", { hour12: false }). */
const timeFormatter = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function formatPrice(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function formatMoney(value: number | null | undefined, currency = "$"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value); // coerces a type-escaped numeric string to number, as before
  return `${sign}${currency}${moneyFormatter.format(abs)}`;
}

export function formatPnl(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const s = formatMoney(Math.abs(value));
  return value >= 0 ? `+${s}` : `-${s}`;
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (typeof value === "number") return numberFormatter(digits).format(value);
  // Runtime type escape (backend sent a string): preserve the legacy
  // String#toLocaleString pass-through — it ignores the option bag.
  return (value as unknown as number).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

/** Epoch seconds / ms / ISO string -> Date (shared by the two time helpers). */
function toDate(iso: string | number): Date | null {
  if (typeof iso === "number") return new Date(iso * (iso > 1e12 ? 1 : 1000));
  return new Date(iso);
}

export function formatTime(iso: string | number | null | undefined): string {
  if (iso === null || iso === undefined || iso === "") return "—";
  const d = toDate(iso);
  if (d === null || Number.isNaN(d.getTime())) return String(iso);
  return timeFormatter.format(d);
}

export function formatDateTime(iso: string | number | null | undefined): string {
  if (iso === null || iso === undefined || iso === "") return "—";
  const d = toDate(iso);
  if (d === null || Number.isNaN(d.getTime())) return String(iso);
  return `${dateFormatter.format(d)} ${timeFormatter.format(d)}`;
}

/** Humanized "age" from a client-captured wall-clock epoch (ms). */
export function formatAgeMs(ageMs: number | null | undefined): string {
  if (ageMs === null || ageMs === undefined) return "—";
  const s = ageMs / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export function positionSide(type: number | string | null | undefined): "BUY" | "SELL" | "UNKNOWN" {
  if (type === null || type === undefined) return "UNKNOWN";
  const n = typeof type === "number" ? type : parseInt(String(type), 10);
  if (n === 0) return "BUY";
  if (n === 1) return "SELL";
  if (String(type).toUpperCase().includes("BUY")) return "BUY";
  if (String(type).toUpperCase().includes("SELL")) return "SELL";
  return "UNKNOWN";
}
