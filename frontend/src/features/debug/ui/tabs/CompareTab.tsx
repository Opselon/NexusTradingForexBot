/**
 * Compare tab — two stored snapshots: server diff (/api/debug/compare) plus
 * the key-level client diff of the raw payloads.
 *
 * BACKEND CONTRACT (verified against the live route): a SUCCESSFUL compare
 * payload has NO `available` key at all — only the two failure paths
 * (NO_SNAPSHOT_STORE / SNAPSHOT_NOT_FOUND) send `available: false` with a
 * reason. The renderer therefore checks `available === false` explicitly;
 * testing truthiness would mislabel every real diff as "unavailable".
 *
 * Feature deltas are sortable (index / name / |Δ|); null/missing values sink.
 */

import { useMemo, useState } from "react";
import { EmptyState, Skeleton, StatusBadge, Panel } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { MonoValue } from "@/features/config/ui/kit";
import type { CompareResult, DebugState } from "../../api";
import { useCompareQuery, useSnapshotDetail, useSnapshotsQuery } from "../../hooks";
import { diffCounts, diffSnapshotSections, snapshotSections } from "../../model";
import { SortTh, sortRows, useSortState, type SortApi } from "../sorting";

type DeltaKey = "index" | "name" | "delta";

export function CompareTab({ a, b, setA, setB }: { a: string | null; b: string | null; setA: (v: string | null) => void; setB: (v: string | null) => void }) {
  const t = useI18n((s) => s.t);
  const list = useSnapshotsQuery(true);
  const serverDiff = useCompareQuery(a, b);
  const snapA = useSnapshotDetail(a);
  const snapB = useSnapshotDetail(b);
  const [section, setSection] = useState<string>("features");
  const deltaApi = useSortState<DeltaKey>({ key: "index", dir: "asc" });

  const clientRows = useMemo(
    () => diffSnapshotSections((snapA.data as DebugState | undefined) ?? null, (snapB.data as DebugState | undefined) ?? null, section, 300, t),
    [snapA.data, snapB.data, section],
  );
  // Reduce / map / set-union chains over the already-memoized rows and the
  // two snapshot payloads — deps are exactly the data each derivation reads.
  const counts = useMemo(() => diffCounts(clientRows), [clientRows]);
  const ids = useMemo(() => (list.data?.snapshots ?? []).map((s) => String(s.snapshot_id ?? "")), [list.data?.snapshots]);
  const sectionNames = useMemo(
    () =>
      Array.from(
        new Set([...snapshotSections((snapA.data as DebugState | undefined) ?? null), ...snapshotSections((snapB.data as DebugState | undefined) ?? null)]),
      ),
    [snapA.data, snapB.data],
  );

  return (
    <Panel
      title={t("debug.compare.title", "Snapshot compare")}
      accent
      right={
        <span className="timestamp-note">
          {serverDiff.dataUpdatedAt ? t("debug.compare.fetched_at", "server diff fetched {at} · ", { at: new Date(serverDiff.dataUpdatedAt).toLocaleTimeString() }) : ""}{t("debug.compare.right", "server diff + key-level client diff")}
        </span>
      }
    >
      <div className="l3-toolbar dbg-compare-pick">
        <span className="lab timestamp-note">A</span>
        <select className="select" value={a ?? ""} onChange={(e) => setA(e.target.value || null)} aria-label={t("debug.compare.aria_a", "snapshot A")}>
          <option value="">{t("debug.compare.pick", "— pick —")}</option>
          {ids.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
        <span className="dbg-compare-arrow" aria-hidden="true">
          ⇄
        </span>
        <span className="lab timestamp-note">B</span>
        <select className="select" value={b ?? ""} onChange={(e) => setB(e.target.value || null)} aria-label={t("debug.compare.aria_b", "snapshot B")}>
          <option value="">{t("debug.compare.pick", "— pick —")}</option>
          {ids.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </div>
      {!a || !b ? (
        <EmptyState
          message={t("debug.compare.empty", "Pick two snapshots (or use A=/B= in the Snapshots tab).")}
          hint={t("debug.compare.empty_hint", "Capture snapshots first — the ring fills as /api/debug/state is polled.")}
        />
      ) : (
        <>
          {serverDiff.isPending ? (
            <Skeleton count={2} />
          ) : serverDiff.data && serverDiff.data.available === false ? (
            <div className="l3-note warn">{t("debug.compare.server_diff", "server diff: {reason}", { reason: String(serverDiff.data.reason ?? t("debug.compare.unavailable", "unavailable")) })}</div>
          ) : serverDiff.data ? (
            <CompareSummary diff={serverDiff.data} deltaApi={deltaApi} />
          ) : null}
          <div className="section-title" style={{ marginTop: 10 }}>
            {t("debug.compare.section_label", "key-level diff · section")}{" "}
            <select className="select" style={{ display: "inline-block", marginInlineStart: 6 }} value={section} onChange={(e) => setSection(e.target.value)} aria-label={t("debug.compare.section_aria", "diff section")}>
              {sectionNames.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <span className="inline-badge">
              {" "}
              · <span className="badge good">+{counts.added}</span> <span className="badge bad">−{counts.removed}</span> <span className="badge warn">~{counts.changed}</span>
            </span>
          </div>
          <div tabIndex={0} className="l3-scroll dbg-diff-scroll">
            {clientRows.length === 0 ? (
              <EmptyState message={t("debug.compare.no_keys", "No key differences in this section.")} />
            ) : (
              <div className="l3-diff dbg-diff">
                {clientRows.slice(0, 200).map((r) => (
                  <div className={`l3-diff-row dbg-diff-row ${r.kind}`} key={r.path}>
                    <span className="kind" aria-hidden="true">
                      {r.kind === "added" ? "+" : r.kind === "removed" ? "−" : "~"}
                    </span>
                    <span className="l3-diff-key" title={r.path}>
                      {r.path.split(".").slice(-1)[0]}
                    </span>
                    <span className="old l3-cell">{r.a}</span>
                    <span className="arrow" aria-hidden="true">
                      →
                    </span>
                    <span className="new l3-cell">{r.b}</span>
                    <span>
                      <StatusBadge status={r.kind === "added" ? "ACTIVE" : r.kind === "removed" ? "STOPPED" : "STALE"} label={r.path} />
                    </span>
                  </div>
                ))}
              </div>
            )}
            {clientRows.length >= 200 && <div className="tiny faint">{t("debug.compare.capped", "showing first 200 changed keys (capped client-side).")}</div>}
          </div>
          {(snapA.isError || snapB.isError) && (
            <div className="l3-note bad">{t("debug.compare.read_fail", "one of the snapshot reads failed — client diff shown only for what could be read.")}</div>
          )}
        </>
      )}
    </Panel>
  );
}

function CompareSummary({ diff, deltaApi }: { diff: CompareResult; deltaApi: SortApi<DeltaKey> }) {
  const t = useI18n((s) => s.t);
  const featureDiffs = diff.feature_diffs ?? [];
  const sections: Array<[string, Record<string, { t0: unknown; t1: unknown }> | undefined]> = [
    ["model", diff.model],
    ["confidence", diff.confidence],
    ["regime", diff.regime],
    ["liquidity", diff.liquidity],
    ["news", diff.news],
    ["policy", diff.policy],
    ["risk", diff.risk],
  ];
  const sortedDeltas = useMemo(
    () =>
      sortRows(
        featureDiffs,
        (f) => (deltaApi.sort.key === "index" ? f.index : deltaApi.sort.key === "name" ? (f.name ?? null) : Math.abs(f.delta)),
        deltaApi.sort.dir,
        deltaApi.sort.key === "index" || deltaApi.sort.key === "delta" ? "number" : "string",
      ),
    [featureDiffs, deltaApi.sort],
  );

  return (
    <div>
      <div className="l3-toolbar timestamp-note dbg-ab">
        <span className="ab-a">{String(diff.a_id)}</span> @ {String(diff.a_timestamp)} <span className="dbg-compare-arrow">⇄</span> <span className="ab-b">{String(diff.b_id)}</span> @{" "}
        {String(diff.b_timestamp)}
      </div>
      {featureDiffs.length > 0 && (
        <>
          <div className="section-title">
            {t("debug.compare.deltas", "feature deltas ({n})", { n: featureDiffs.length })} · {t("debug.compare.sorted_by", "sorted by {key} {dir}", { key: deltaApi.sort.key ?? "", dir: deltaApi.sort.dir })}
          </div>
          <div tabIndex={0} className="l3-scroll sm dbg-table-wrap">
            <table className="data-table dbg-table">
              <thead>
                <tr>
                  <SortTh<DeltaKey> label={t("debug.compare.th_idx", "idx")} col="index" api={deltaApi} num />
                  <SortTh<DeltaKey> label={t("debug.compare.th_name", "name")} col="name" api={deltaApi} />
                  <th className="num plain">A</th>
                  <th className="num plain">B</th>
                  <SortTh<DeltaKey> label={t("debug.compare.th_delta", "Δ")} col="delta" api={deltaApi} num title={t("debug.compare.delta_title", "absolute magnitude sorts; sign shown in the cell")} />
                </tr>
              </thead>
              <tbody>
                {sortedDeltas.slice(0, 100).map((f) => (
                  <tr key={f.index}>
                    <td>{f.index}</td>
                    <td>{f.name ?? "—"}</td>
                    <td className="num">{f.t0.toFixed(4)}</td>
                    <td className="num">{f.t1.toFixed(4)}</td>
                    <td className={`num ${f.delta > 0 ? "pnl-pos" : "pnl-neg"}`}>
                      {f.delta > 0 ? "+" : ""}
                      {f.delta.toFixed(4)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {sections.map(([name, block]) => {
        const entries = Object.entries(block ?? {});
        if (entries.length === 0) return null;
        return (
          <div key={name} style={{ marginTop: 8 }}>
            <div className="section-title">{t("debug.compare.changes", "{name} changes", { name })}</div>
            <table className="data-table dbg-table">
              <thead>
                <tr>
                  <th className="plain">{t("debug.compare.th_key", "key")}</th>
                  <th className="plain">A</th>
                  <th className="plain">B</th>
                </tr>
              </thead>
              <tbody>
                {entries.map(([k, v]) => (
                  <tr key={k}>
                    <td className="inline-mono">{k}</td>
                    <td>
                      <MonoValue value={v.t0} />
                    </td>
                    <td>
                      <MonoValue value={v.t1} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
      {featureDiffs.length === 0 && sections.every(([, bl]) => !bl || Object.keys(bl).length === 0) && (
        <EmptyState message={t("debug.compare.no_material", "Server diff reports no material changes between the two snapshots.")} />
      )}
    </div>
  );
}
