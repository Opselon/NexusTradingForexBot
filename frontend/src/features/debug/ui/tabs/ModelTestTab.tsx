/**
 * Model test tab — POST /api/debug/model-test: validated instant inference.
 *
 * The vector contract (dimension + bounds) comes from the live
 * /api/debug/features read — never hardcoded — so a schema change upstream
 * flows straight through. Invalid cells are blocked before the POST; the
 * backend's own refusal (e.g. a 422 dimension mismatch) is shown verbatim.
 */

import { useMemo, useState } from "react";
import { MetricCard, Panel } from "@/components/primitives";
import { ResultStrip } from "@/features/config/ui/kit";
import { useDebugFeaturesQuery, useModelTest } from "../../hooks";
import { vectorSpec } from "../../model";

export function ModelTestTab() {
  const featuresQuery = useDebugFeaturesQuery(true);
  const spec = vectorSpec(featuresQuery.data?.feature_count ?? null);
  const cells = useMemo(() => {
    const rows = featuresQuery.data?.features ?? [];
    return rows.map((r) => (r.value === null || r.value === undefined ? "0" : String(r.value)));
  }, [featuresQuery.data]);
  const [useLive, setUseLive] = useState(true);
  const [localCells, setLocalCells] = useState<string[]>([]);
  const run = useModelTest();

  const source = cells;
  const shown = useLive ? cells.map(() => "—") : localCells;

  const startCustom = () => {
    setLocalCells(source.length > 0 ? [...source] : []);
    setUseLive(false);
  };

  const submit = async () => {
    await run.mutateAsync({ spec, cells: localCells, useLive });
  };

  const badIdx = new Set<number>();
  if (!useLive && spec) {
    localCells.forEach((c, i) => {
      const n = Number(c);
      if (c.trim() === "" || !Number.isFinite(n) || n < spec.min || n > spec.max) badIdx.add(i);
    });
  }

  return (
    <Panel
      title="Model instant test (/api/debug/model-test)"
      accent
      right={<span className="timestamp-note">{spec ? `contract: ${spec.dimension} values ∈ [${spec.min}, ${spec.max}]` : "contract not loaded — send blocked"}</span>}
    >
      <div className="dbg-sec">
        <div className="l3-toolbar">
          <span className="timestamp-note">feature source</span>
          <span className="segmented">
            <button className={useLive ? "active" : ""} onClick={() => setUseLive(true)}>
              live vector
            </button>
            <button className={!useLive ? "active" : ""} onClick={startCustom}>
              custom vector
            </button>
          </span>
          <button className="btn small" onClick={() => void featuresQuery.refetch()} disabled={featuresQuery.isFetching}>
            reload contract
          </button>
          <span className="timestamp-note">
            {featuresQuery.data ? `last vector @ ${featuresQuery.data.timestamp_utc ?? "—"} · ${featuresQuery.data.is_stale ? "STALE" : "fresh"}` : "no features read yet"}
          </span>
        </div>
        {!useLive && (
          <>
            <div className="l3-vector-grid l3-scroll dbg-vector" style={{ marginBottom: 10 }}>
              {shown.map((c, i) => (
                <label className={`l3-vector-cell ${badIdx.has(i) ? "invalid" : ""}`} key={i}>
                  <span className="ix">{i}</span>
                  <input className="input" value={c} onChange={(e) => setLocalCells((prev) => {
                    const next = [...prev];
                    next[i] = e.target.value;
                    return next;
                  })} inputMode="decimal" aria-label={`feature ${i}`} />
                </label>
              ))}
              {shown.length === 0 && <div className="l3-note warn">custom mode needs the live contract first — press "reload contract".</div>}
            </div>
            {badIdx.size > 0 && <div className="l3-note bad">{badIdx.size} cell(s) invalid — non-finite or out of bounds. The POST is blocked until they pass.</div>}
          </>
        )}
        <div className="l3-toolbar" style={{ justifyContent: "flex-end" }}>
          <button
            className="btn primary dbg-run"
            disabled={run.isPending || (!useLive && (!spec || localCells.length === 0 || badIdx.size > 0))}
            onClick={() => void submit()}
          >
            {run.isPending ? "inferring…" : useLive ? "Run on LIVE vector" : "Run on custom vector"}
          </button>
        </div>
        <ResultStrip result={run.isPending ? { running: true, lastResult: null, lastMessage: null } : run.data ? { running: false, lastResult: run.data.ok, lastMessage: run.data.message } : null} />
        {run.data?.ok && run.data.result && (
          <div className="dbg-verdict-wrap">
            <div className="l3-verdict dbg-verdict">
              <MetricCard
                label="verdict"
                value={run.data.result.predicted_label ?? "—"}
                tone={run.data.result.predicted_label === "BUY_MARKET" ? "pos" : run.data.result.predicted_label === "SELL_MARKET" ? "neg" : "dim"}
                sub={`class ${run.data.result.predicted_class_index ?? "?"}`}
              />
              <MetricCard label="confidence" value={((run.data.result.confidence ?? 0) * 100).toFixed(1) + "%"} sub={`argmax over ${(run.data.result.probabilities ?? []).length} heads`} />
              <MetricCard label="e2e" value={`${run.data.result.latency_ms ?? "—"} ms`} sub={`model ${String(run.data.result.model_forward_ms ?? "—")} ms`} />
              <MetricCard label="source" value={run.data.result.model_source ?? "—"} sub={`features: ${run.data.result.feature_source ?? "—"} · sanitized ${String(run.data.result.sanitized_inputs ?? 0)}`} />
            </div>
            <div style={{ marginTop: 8 }}>
              <div className="probbar">
                {(
                  [
                    ["NO_TRADE", run.data.result.ai_no_trade ?? null, "flat"],
                    ["BUY", run.data.result.ai_buy ?? null, "buy"],
                    ["SELL", run.data.result.ai_sell ?? null, "sell"],
                    ["WAIT", run.data.result.ai_wait ?? null, "flat"],
                  ] as Array<[string, number | null, "buy" | "sell" | "flat"]>
                ).map(([label, value, tone]) => (
                  <div className="row" key={label}>
                    <span className="lab">{label}</span>
                    <span className="track">
                      <i className={tone} style={{ width: `${(value ?? 0) * 100}%` }} />
                    </span>
                    <span className="val">{value === null ? "—" : `${(value * 100).toFixed(1)}%`}</span>
                  </div>
                ))}
              </div>
            </div>
            {run.data.result.latency_breakdown && (
              <div className="tiny faint" style={{ marginTop: 8 }}>
                latency breakdown: {Object.entries(run.data.result.latency_breakdown).slice(0, 10).map(([k, v]) => `${k}=${String(v)}`).join(" · ")}
              </div>
            )}
            <div className="tiny faint">evaluated_at {String(run.data.result.evaluated_at ?? "—")}</div>
          </div>
        )}
      </div>
    </Panel>
  );
}
