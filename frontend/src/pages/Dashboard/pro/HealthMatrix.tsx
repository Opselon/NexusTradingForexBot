/**
 * HealthMatrix — subsystem health as a compact tile grid.
 *
 * Same source as the legacy key/value list: `snapshot.health.subsystems` +
 * `overall` + `live_freshness.overall`. Status WORDS and their badge levels
 * come from the existing StatusBadge primitive — this component only re-packs
 * them into a matrix and adds the last-check timestamp. Nothing is filtered,
 * renamed (beyond underscore → space), or invented. Layout is inline-styled
 * so this lane adds no shared CSS.
 */

import type { EngineSnapshot } from "@/types/domain";
import { StatusBadge } from "@/components/primitives";
import { formatTime } from "@/lib/format";

export function HealthMatrix({ snapshot }: { snapshot: EngineSnapshot }) {
  const health = snapshot.health;
  const cells: Array<{ name: string; status: string | null | undefined }> = [
    ...Object.entries(health.subsystems).map(([name, st]) => ({ name, status: st ?? null })),
    { name: "overall", status: health.overall },
    { name: "freshness", status: snapshot.live_freshness?.overall ?? null },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div
        className="health-grid"
        style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 8 }}
      >
        {cells.map((c) => (
          <div
            key={c.name}
            title={`${c.name}: ${c.status ?? "UNKNOWN"}`}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 8,
              padding: "8px 10px",
              border: "1px solid var(--border)",
              borderRadius: 8,
              background: "var(--bg-inset)",
              minWidth: 0,
            }}
          >
            <span className="tiny muted" style={{ textTransform: "uppercase", letterSpacing: "0.08em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {c.name.replace(/_/g, " ")}
            </span>
            <StatusBadge status={c.status} />
          </div>
        ))}
      </div>
      <div className="timestamp-note">checked {formatTime(health.checked_at)}</div>
    </div>
  );
}
