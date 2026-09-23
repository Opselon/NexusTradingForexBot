/**
 * PURPOSE:  Storefront view-model helpers — pack covers, facet derivation,
 *           podium rank words. Pure, derived only from loaded payloads.
 * OWNER:    uiux-w6-marketplace
 * CONSUMES: ../types (MktPack, MktSeed), ../model (lifecycleLevel)
 * PROVIDES: packMonogram, packCoverTone, facetCounts, rankWord, seedCardMeta
 * INVARIANTS: no invented values — a monogram is just initials of a name that
 *           exists; facet chips are counts of fields actually present; rank
 *           words come from the array position the backend ordering gave us.
 * EXTEND:   add a pure helper; keep every function total over undefined.
 */

import type { MktPack, MktSeed } from "../types";

/** Initials of the pack name — falls back to the id prefix, never blank. */
export function packMonogram(pack: MktPack): string {
  const src = pack.name.trim() || pack.id;
  const words = src.split(/[\s_-]+/).filter((w) => w.length > 0);
  if (words.length === 0) return "?";
  if (words.length === 1) return (words[0] ?? "").slice(0, 2).toUpperCase();
  const a = words[0]?.[0] ?? "";
  const b = words[1]?.[0] ?? "";
  return (a + b).toUpperCase() || "?";
}

/** Cover hue from the family string — token palette only (no hex added). */
export function packCoverTone(family: string | undefined): string {
  const f = (family ?? "").toUpperCase();
  if (f.includes("ICT") || f.includes("PRICE")) return "accent";
  if (f.includes("MEAN") || f.includes("REVERSION")) return "green";
  if (f.includes("MOMENT") || f.includes("BREAK")) return "amber";
  if (f.includes("ICHIMOKU") || f.includes("HYBRID")) return "violet";
  return "accent";
}

export interface FacetCount {
  /** The verbatim backend value (family/lifecycle string). */
  value: string;
  /** How many loaded rows carry it. */
  count: number;
}

/**
 * Facet chips for a seed list. Derived ONLY from rows actually loaded —
 * a family/lifecycle that is not in the page never gets a chip.
 */
export function facetCounts(rows: MktSeed[], key: "family" | "lifecycle"): FacetCount[] {
  const map = new Map<string, number>();
  for (const r of rows) {
    const v = r[key];
    if (typeof v !== "string" || v.trim() === "") continue;
    map.set(v, (map.get(v) ?? 0) + 1);
  }
  return Array.from(map, ([value, count]) => ({ value, count })).sort((a, b) =>
    a.count === b.count ? a.value.localeCompare(b.value) : b.count - a.count,
  );
}

/** Rank word from the podium position (0-based). Derived label, by position. */
export function rankWord(index: number): string {
  if (index === 0) return "rank 1 · highest total";
  if (index === 1) return "rank 2";
  if (index === 2) return "rank 3";
  return `rank ${index + 1}`;
}

/** Metadata pairs for a seed card — only fields that exist on the row. */
export function seedCardMeta(seed: MktSeed): Array<{ label: string; text: string }> {
  const out: Array<{ label: string; text: string }> = [];
  if (typeof seed.family === "string" && seed.family) out.push({ label: "family", text: seed.family });
  if (typeof seed.risk_profile === "string" && seed.risk_profile) out.push({ label: "risk", text: seed.risk_profile });
  if (typeof seed.pack_id === "string" && seed.pack_id) out.push({ label: "pack", text: seed.pack_id });
  if (typeof seed.author === "string" && seed.author) out.push({ label: "author", text: seed.author });
  if (typeof seed.version === "string" && seed.version) out.push({ label: "v", text: seed.version });
  if (typeof seed.license === "string" && seed.license) out.push({ label: "license", text: seed.license });
  return out;
}
