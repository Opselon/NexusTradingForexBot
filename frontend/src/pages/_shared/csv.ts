/**
 * Client-side CSV export for operator tables (lane 4).
 *
 * The backend exposes no CSV route (verified against shared/ROUTES.txt), so
 * the export is built in the browser from the rows the backend already sent:
 * a download button never re-queries, never re-orders, and never adds values
 * the API did not return. Null renders as an empty cell — not "0", not "—".
 */

export type CsvValue = string | number | boolean | null | undefined;

/** RFC-4180-ish quoting: wrap when the cell contains , " or newline. */
function quote(value: CsvValue): string {
  if (value === null || value === undefined) return "";
  const s = typeof value === "string" ? value : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export interface CsvSpec {
  filename: string;
  headers: string[];
  rows: CsvValue[][];
}

/** Trigger a one-file CSV download built synchronously from given rows. */
export function downloadCsv({ filename, headers, rows }: CsvSpec): void {
  const lines = [headers, ...rows].map((cols) => cols.map(quote).join(",")).join("\r\n");
  // BOM so Excel opens UTF-8 titles (news articles carry non-ASCII).
  const blob = new Blob(["\uFEFF" + lines], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next tick — Safari needs the object URL to outlive click().
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** File stamp for export names (local time, ISO-ish, path-safe). */
export function stampForFilename(d = new Date()): string {
  return d.toISOString().replace(/[:.]/g, "-").slice(0, 19);
}
