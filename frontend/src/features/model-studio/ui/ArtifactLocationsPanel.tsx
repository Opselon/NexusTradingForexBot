/**
 * ArtifactLocationsPanel — resolved on-disk roots for every studio artifact.
 *
 * Port of the legacy Neural Model Studio "Artifact Locator" card
 * (Web/model_studio_ui.js loadStudioArtifactLocations +
 * STUDIO_LOCATION_META), which reads
 *   GET /api/model-studio/artifact-locations
 *
 * Paths are SERVER-DERIVED (resolved from the repo root, never from request
 * input) and read-only: existence flags + file counts only, no contents.
 * The status pill shows "N/M ROOTS PRESENT" — absent roots are not errors,
 * they render as a neutral, non-green card so an operator can see exactly
 * which artifact the studio has not written yet.
 */

import { useQuery } from "@tanstack/react-query";
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { ARTIFACT_LOCATION_META, type ArtifactLocationEntry, type ArtifactLocationKey } from "../model";
import { modelStudioApi } from "../api";

const ORDER: ArtifactLocationKey[] = [
  "datasets",
  "position_datasets",
  "position_datasets_alt",
  "model_checkpoints",
  "training_datasets",
  "registry_database",
];

const ICON: Record<ArtifactLocationKey, string> = {
  datasets: "▤",
  position_datasets: "↯",
  position_datasets_alt: "↯",
  model_checkpoints: "▣",
  training_datasets: "✦",
  registry_database: "▦",
};

/** Show the repo-relative path when available; the UI normalizes Windows
 *  separators the same way the legacy studioDisplayPath() did. */
function displayPath(entry: { relative_path?: string; absolute_path?: string }): string {
  const p = entry.relative_path || entry.absolute_path || "—";
  return p.replace(/\\/g, "/");
}

export function ArtifactLocationsPanel() {
  const q = useQuery({
    queryKey: ["model-studio", "artifact-locations"],
    queryFn: ({ signal }) => modelStudioApi.artifactLocations(signal),
    refetchInterval: 60_000,
    retry: false,
  });

  const locations: Partial<Record<ArtifactLocationKey, ArtifactLocationEntry>> = q.data?.locations ?? {};
  const present = ORDER.filter((k) => locations[k]?.exists).length;
  const total = ORDER.filter((k) => locations[k]).length;
  const allPresent = total > 0 && present === total;

  return (
    <Panel
      title="Artifact locator (on-disk roots)"
      right={
        q.data ? (
          <span className={`badge ${allPresent ? "good" : present === 0 ? "bad" : "warn"}`}>
            {present}/{total} roots present
          </span>
        ) : null
      }
      tight
    >
      {q.isPending ? (
        <Skeleton count={3} />
      ) : q.isError ? (
        <ErrorState
          message={q.error instanceof Error ? q.error.message : "artifact-locations endpoint failed"}
          onRetry={() => void q.refetch()}
        />
      ) : !q.data || total === 0 ? (
        <EmptyState message="Artifact map unavailable." hint="The backend reported no artifact roots." />
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          {q.data.repo_root && (
            <div className="tiny muted" style={{ marginBottom: 2, fontFamily: "var(--mono)" }}>
              repo root: {displayPath({ absolute_path: q.data.repo_root })}
            </div>
          )}
          {ORDER.filter((k) => locations[k]).map((key) => {
            const entry = locations[key]!;
            const meta = ARTIFACT_LOCATION_META[key];
            const ok = Boolean(entry.exists);
            return (
              <div
                key={key}
                className="kv-row"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "6px 10px",
                  borderRadius: 8,
                  border: `1px solid ${ok ? "color-mix(in srgb, var(--green) 30%, transparent)" : "var(--border)"}`,
                  background: ok ? "var(--green-dim)" : "transparent",
                }}
                title={entry.absolute_path}
              >
                <span style={{ width: 18, textAlign: "center", color: ok ? "var(--green)" : "var(--text-faint)" }}>
                  {ICON[key]}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="small">
                    <strong>{meta.label}</strong>{" "}
                    <span className="tiny faint" style={{ fontFamily: "var(--mono)" }}>
                      {meta.hint}
                    </span>
                  </div>
                  <div className="tiny muted" style={{ fontFamily: "var(--mono)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {displayPath(entry)}
                  </div>
                </div>
                <span style={{ textAlign: "right", flexShrink: 0 }}>
                  <span className={`badge ${ok ? "good" : "unknown"}`}>{ok ? "present" : "absent"}</span>
                  {entry.is_dir ? (
                    <span className="tiny faint" style={{ marginLeft: 6 }}>
                      {entry.file_count} files
                    </span>
                  ) : null}
                </span>
              </div>
            );
          })}
          <div className="tiny faint" style={{ marginTop: 4 }}>
            Paths are resolved server-side from the repo root and reported read-only — the UI never sends a path
            back to the backend.
          </div>
        </div>
      )}
    </Panel>
  );
}
