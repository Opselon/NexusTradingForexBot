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
import { useI18n } from "@/stores/i18nStore";
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


/** Display label per artifact root — literal t() keys (dynamic keys are banned). */
function ArtifactLabel({ k }: { k: ArtifactLocationKey }) {
  const t = useI18n((s) => s.t);
  switch (k) {
    case "datasets":
      return <>{t("model-studio.artifacts.datasets", "Market Datasets")}</>;
    case "position_datasets":
      return <>{t("model-studio.artifacts.position_datasets", "Position Datasets")}</>;
    case "position_datasets_alt":
      return (
        <>{t("model-studio.artifacts.position_datasets_alt", "Position Datasets (data/positions)")}</>
      );
    case "model_checkpoints":
      return <>{t("model-studio.artifacts.model_checkpoints", "Model Checkpoints")}</>;
    case "training_datasets":
      return <>{t("model-studio.artifacts.training_datasets", "Training Datasets")}</>;
    case "registry_database":
      return <>{t("model-studio.artifacts.registry_database", "SQLite Registry")}</>;
    default:
      return null;
  }
}

export function ArtifactLocationsPanel() {
  const t = useI18n((s) => s.t);
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
      title={t("model-studio.artifacts.title", "Artifact locator (on-disk roots)")}
      right={
        q.data ? (
          <span className={`badge ${allPresent ? "good" : present === 0 ? "bad" : "warn"}`}>
            {t("model-studio.artifacts.roots_present", "{p}/{n} roots present", {
              p: present,
              n: total,
            })}
          </span>
        ) : null
      }
      tight
    >
      {q.isPending ? (
        <Skeleton count={3} />
      ) : q.isError ? (
        <ErrorState
          message={
            q.error instanceof Error
              ? q.error.message
              : t("model-studio.artifacts.endpoint_failed", "artifact-locations endpoint failed")
          }
          onRetry={() => void q.refetch()}
        />
      ) : !q.data || total === 0 ? (
        <EmptyState
          message={t("model-studio.artifacts.empty_msg", "Artifact map unavailable.")}
          hint={t("model-studio.artifacts.empty_hint", "The backend reported no artifact roots.")}
        />
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          {q.data.repo_root && (
            <div className="tiny muted" style={{ marginBottom: 2, fontFamily: "var(--mono)" }}>
              {t("model-studio.artifacts.repo_root", "repo root:")}{" "}
              <span dir="ltr">{displayPath({ absolute_path: q.data.repo_root })}</span>
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
                    <strong>
                      <ArtifactLabel k={key} />
                    </strong>{" "}
                    <span className="tiny faint" dir="ltr" style={{ fontFamily: "var(--mono)" }}>
                      {meta.hint}
                    </span>
                  </div>
                  <div
                    className="tiny muted"
                    dir="ltr"
                    style={{ fontFamily: "var(--mono)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                  >
                    {displayPath(entry)}
                  </div>
                </div>
                <span style={{ textAlign: "end", flexShrink: 0 }}>
                  <span className={`badge ${ok ? "good" : "unknown"}`}>{ok
                      ? t("model-studio.artifacts.present", "present")
                      : t("model-studio.artifacts.absent", "absent")}</span>
                  {entry.is_dir ? (
                    <span className="tiny faint" style={{ marginInlineStart: 6 }}>
                      {t("model-studio.artifacts.files", "{n} files", { n: entry.file_count })}
                    </span>
                  ) : null}
                </span>
              </div>
            );
          })}
          <div className="tiny faint" style={{ marginTop: 4 }}>
            {t(
              "model-studio.artifacts.footer",
              "Paths are resolved server-side from the repo root and reported read-only — the UI never sends a path back to the backend.",
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}
