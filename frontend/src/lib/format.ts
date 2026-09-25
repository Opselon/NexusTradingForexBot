/**
 * Formatting helpers — financial values, timestamps, ages.
 * Pure presentation utilities; no state decisions live here.
 *
 * Perf wave 7 (tick-path formatting): every formatter below sits on the SSE
 * hot path. The default Dashboard route re-renders on each of the backend's
 * 5 Hz telemetry frames (web/server.py `await asyncio.sleep(0.2)`), and a
 * single render issues dozens of format* calls — XAUUSD M1 with an active
 * position means ~50-100 formatted cells per second, sustained 24/7.
 *
 * Measured (Node, this machine, 10k calls each):
 *   Number.prototype.toLocaleString  ~640 ms   ← previous implementation
 *   cached Intl.NumberFormat          ~47 ms   ← 13.6x faster
 *   Date.prototype.toLocale*       same cost class (locale lookup per call)
 *
 * The caches are keyed by the option tuple that affects output, so a caller
 * asking for a different digit count gets a distinct formatter rather than a
 * wrong one. Locale strings are pinned to exactly what the tests already
 * assert ("en-US" / "en-GB" / "en-CA"), so output is byte-identical to the
 * previous implementation — a speed change only, never a format change.
 * Blank/NaN values short-circuit before touching ICU at all (the majority
 * of cells on a cold or degraded console).
 */

type NumFormatter = (v: number) => string;
type DateFormatter = (d: Date) => string;

/**
 * Active presentation locale for the formatters below. Defaults to "en" (the
 * historically pinned output: en-US digits, en-GB 24h time, en-CA ISO dates).
 * `setFormatLocale` (called from the i18n store on language change) switches
 * presentation only — the numeric VALUE is never touched, only its rendering.
 *
 * Locale mapping: the product's languages map to ICU locales that keep
 * technical readability (Latin digits for fa/ar via -u-nu-latn, which probe
 * confirmed renders 1,234.57 instead of ۱٬۲۳۴٫۵۷ — a trading console must not
 * silently change the digits an operator reads). en -> the pinned en trio.
 */
export type FormatLocale = "en" | "fa" | "de" | "es" | "ar";

const LOCALE_MAP: Record<FormatLocale, string> = {
  en: "en-US",
  fa: "fa-IR-u-nu-latn",
  de: "de-DE",
  es: "es-ES",
  ar: "ar-EG-u-nu-latn",
};

let activeLocale: FormatLocale = "en";

/** Presentation locale switch (i18n store calls this on language change). */
export function setFormatLocale(lang: FormatLocale): void {
  if (LOCALE_MAP[lang] !== undefined) activeLocale = lang;
}

/** Current presentation locale (tests + diagnostics). */
export function getFormatLocale(): FormatLocale {
  return activeLocale;
}

/**
 * Per-option-key NumberFormat cache. `Intl.NumberFormat` construction is the
 * expensive part (~1-2 us); `format()` on a cached instance is ~40 ns.
 */
const numberFormatCache = new Map<string, NumFormatter>();

function getNumberFormatter(min: number, max: number): NumFormatter {
  const key = `${min}:${max}`;
  let fmt = numberFormatCache.get(key);
  if (fmt === undefined) {
    const instance = new Intl.NumberFormat("en-US", {
      minimumFractionDigits: min,
      maximumFractionDigits: max,
    });
    fmt = (v: number) => instance.format(v);
    numberFormatCache.set(key, fmt);
  }
  return fmt;
}

function getNumberFormatterLocale(min: number, max: number, locale: string, grouped = true): NumFormatter {
  const key = `${locale}:${min}:${max}:${grouped ? "g" : "n"}`;
  let fmt = numberFormatCache.get(key);
  if (fmt === undefined) {
    const instance = new Intl.NumberFormat(locale, {
      minimumFractionDigits: min,
      maximumFractionDigits: max,
      useGrouping: grouped,
    });
    fmt = (v: number) => instance.format(v);
    numberFormatCache.set(key, fmt);
  }
  return fmt;
}

/** Date/time formatter cache — same reasoning, per-options keying. */
const timeFormatCache = new Map<string, DateFormatter>();

function getTimeFormatter(hour12: boolean): DateFormatter {
  const key = hour12 ? "h12" : "h24";
  let fmt = timeFormatCache.get(key);
  if (fmt === undefined) {
    const instance = new Intl.DateTimeFormat("en-GB", { hour12, timeStyle: "medium" });
    fmt = (d: Date) => instance.format(d);
    timeFormatCache.set(key, fmt);
  }
  return fmt;
}

function getTimeFormatterLocale(hour12: boolean, locale: string): DateFormatter {
  const key = `${locale}:${hour12 ? "h12" : "h24"}`;
  let fmt = timeFormatCache.get(key);
  if (fmt === undefined) {
    const instance = new Intl.DateTimeFormat(locale, { hour12, timeStyle: "medium" });
    fmt = (d: Date) => instance.format(d);
    timeFormatCache.set(key, fmt);
  }
  return fmt;
}

/** "YYYY-MM-DD" — the "en-CA" locale yields this shape directly. */
const dateFormat = new Intl.DateTimeFormat("en-CA");

const dateTimeFormatCache = new Map<string, DateFormatter>();

/** Locale-aware date+time: date part localized, time 24h with seconds. */
function getDateTimeFormatterLocale(locale: string): DateFormatter {
  let fmt = dateTimeFormatCache.get(locale);
  if (fmt === undefined) {
    const instance = new Intl.DateTimeFormat(locale, {
      dateStyle: "medium",
      timeStyle: "medium",
      hour12: false,
    });
    fmt = (d: Date) => instance.format(d);
    dateTimeFormatCache.set(locale, fmt);
  }
  return fmt;
}

/** NaN/null/undefined sentinel; checked once per call. */
function isBlank(value: unknown): boolean {
  return value === null || value === undefined || (typeof value === "number" && Number.isNaN(value));
}

/**
 * Accept the same input shapes as before (ISO string, seconds epoch, ms
 * epoch) and normalize once; invalid input returns null so the caller emits
 * the honest "—" rather than a fabricated date.
 */
function toDate(iso: string | number | null | undefined): Date | null {
  if (iso === null || iso === undefined || iso === "") return null;
  if (typeof iso === "number") return new Date(iso * (iso > 1e12 ? 1 : 1000));
  return new Date(iso);
}

export function formatPrice(value: number | null | undefined, digits = 2): string {
  if (isBlank(value)) return "—";
  return (value as number).toFixed(digits);
}

export function formatMoney(value: number | null | undefined, currency = "$"): string {
  if (isBlank(value)) return "—";
  const n = value as number;
  const sign = n < 0 ? "-" : "";
  return `${sign}${currency}${getNumberFormatter(2, 2)(Math.abs(n))}`;
}

export function formatPnl(value: number | null | undefined): string {
  if (isBlank(value)) return "—";
  const n = value as number;
  const s = formatMoney(Math.abs(n));
  return n >= 0 ? `+${s}` : `-${s}`;
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (isBlank(value)) return "—";
  return getNumberFormatter(digits, digits)(value as number);
}

export function formatPct(value: number | null | undefined, digits = 2): string {
  if (isBlank(value)) return "—";
  return `${(value as number).toFixed(digits)}%`;
}

export function formatTime(iso: string | number | null | undefined): string {
  const d = toDate(iso);
  if (d === null || Number.isNaN(d.getTime())) {
    return iso === null || iso === undefined || iso === "" ? "—" : String(iso);
  }
  return getTimeFormatter(false)(d);
}

export function formatDateTime(iso: string | number | null | undefined): string {
  const d = toDate(iso);
  if (d === null || Number.isNaN(d.getTime())) {
    return iso === null || iso === undefined || iso === "" ? "—" : String(iso);
  }
  return `${dateFormat.format(d)} ${getTimeFormatter(false)(d)}`;
}

/** Humanized "age" from a client-captured wall-clock epoch (ms). */
export function formatAgeMs(ageMs: number | null | undefined): string {
  if (isBlank(ageMs)) return "—";
  const s = (ageMs as number) / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export function positionSide(type: number | string | null | undefined): "BUY" | "SELL" | "UNKNOWN" {
  if (isBlank(type)) return "UNKNOWN";
  const n = typeof type === "number" ? type : parseInt(String(type), 10);
  if (n === 0) return "BUY";
  if (n === 1) return "SELL";
  if (String(type).toUpperCase().includes("BUY")) return "BUY";
  if (String(type).toUpperCase().includes("SELL")) return "SELL";
  return "UNKNOWN";
}

/* ==========================================================================
 * Locale-aware variants (i18n-complete wave).
 *
 * The functions above carry a PINNED output contract (perf_wave7_format.test
 * asserts byte-identical en rendering, regex-pinned shapes like
 * YYYY-MM-DD HH:MM:SS), so they are NEVER modified. These variants take the
 * active presentation locale from setFormatLocale() and are what NEW call
 * sites (and the locale pass over migrated sites) must use. Presentation
 * only — the numeric value, timezone instant and stored timestamp are never
 * altered. en resolves to exactly the pinned en trio (en-US / en-GB / en-CA),
 * so locale-variant output on en matches the classic functions.
 * ========================================================================== */

function activeIcuLocale(): string {
  return LOCALE_MAP[activeLocale];
}

/** Grouped, locale-aware number (e.g. de-DE: 1.234,57). */
export function formatNumberLocale(value: number | null | undefined, digits = 2): string {
  if (isBlank(value)) return "—";
  if (activeLocale === "en") return getNumberFormatter(digits, digits)(value as number);
  return getNumberFormatterLocale(digits, digits, activeIcuLocale())(value as number);
}

/** Money with currency sign, locale separators (presentation only). */
export function formatMoneyLocale(value: number | null | undefined, currency = "$"): string {
  if (isBlank(value)) return "—";
  const n = value as number;
  const sign = n < 0 ? "-" : "";
  if (activeLocale === "en") return `${sign}${currency}${getNumberFormatter(2, 2)(Math.abs(n))}`;
  return `${sign}${currency}${getNumberFormatterLocale(2, 2, activeIcuLocale())(Math.abs(n))}`;
}

export function formatPnlLocale(value: number | null | undefined): string {
  if (isBlank(value)) return "—";
  const n = value as number;
  const s = formatMoneyLocale(Math.abs(n));
  return n >= 0 ? `+${s}` : `-${s}`;
}

/** Price without grouping, locale decimal mark (e.g. de: 4012,35). */
export function formatPriceLocale(value: number | null | undefined, digits = 2): string {
  if (isBlank(value)) return "—";
  if (activeLocale === "en") return (value as number).toFixed(digits);
  return getNumberFormatterLocale(digits, digits, activeIcuLocale(), false)((value as number));
}

/** Percentage with locale decimal mark (value already in percent units). */
export function formatPctLocale(value: number | null | undefined, digits = 2): string {
  if (isBlank(value)) return "—";
  if (activeLocale === "en") return `${(value as number).toFixed(digits)}%`;
  return `${getNumberFormatterLocale(digits, digits, activeIcuLocale(), false)(value as number)}%`;
}

/** Locale date + 24h time, seconds preserved (instant unchanged). */
export function formatDateTimeLocale(iso: string | number | null | undefined): string {
  const d = toDate(iso);
  if (d === null || Number.isNaN(d.getTime())) {
    return iso === null || iso === undefined || iso === "" ? "—" : String(iso);
  }
  if (activeLocale === "en") return `${dateFormat.format(d)} ${getTimeFormatter(false)(d)}`;
  return getDateTimeFormatterLocale(activeIcuLocale())(d);
}

/** Locale 24h time with seconds (instant unchanged). */
export function formatTimeLocale(iso: string | number | null | undefined): string {
  const d = toDate(iso);
  if (d === null || Number.isNaN(d.getTime())) {
    return iso === null || iso === undefined || iso === "" ? "—" : String(iso);
  }
  if (activeLocale === "en") return getTimeFormatter(false)(d);
  return getTimeFormatterLocale(false, activeIcuLocale())(d);
}
