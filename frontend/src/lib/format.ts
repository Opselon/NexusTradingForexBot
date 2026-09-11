/**
 * Formatting helpers — financial values, timestamps, ages.
 * Pure presentation utilities; no state decisions live here.
 */

export function formatPrice(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function formatMoney(value: number | null | undefined, currency = "$"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  return `${sign}${currency}${abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatPnl(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const s = formatMoney(Math.abs(value));
  return value >= 0 ? `+${s}` : `-${s}`;
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function formatPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

export function formatTime(iso: string | number | null | undefined): string {
  if (iso === null || iso === undefined || iso === "") return "—";
  let d: Date;
  if (typeof iso === "number") d = new Date(iso * (iso > 1e12 ? 1 : 1000));
  else d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleTimeString("en-GB", { hour12: false });
}

export function formatDateTime(iso: string | number | null | undefined): string {
  if (iso === null || iso === undefined || iso === "") return "—";
  let d: Date;
  if (typeof iso === "number") d = new Date(iso * (iso > 1e12 ? 1 : 1000));
  else d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return `${d.toLocaleDateString("en-CA")} ${d.toLocaleTimeString("en-GB", { hour12: false })}`;
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
