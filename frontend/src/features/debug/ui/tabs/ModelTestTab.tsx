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
import { useI18n } from "@/stores/i18nStore";
import { ResultStrip } from "@/features/config/ui/kit";
import { useDebugFeaturesQuery, useModelTest } from "../../hooks";
import { vectorSpec } from "../../model";

export function ModelTestTab() {
  const t = useI18n((s) => s.t);
  const featuresQuery = useDebugFeaturesQuery(true);
  // memoized on the single field it reads: a stable object identity so the
  // contract, the invalid-cell memo and the submit gate don't churn per render
  const spec = useMemo(() => vectorSpec(featuresQuery.data?.feature_count ?? null), [featuresQuery.data?.feature_count]);
  const cells = useMemo(() => {
    const rows = featuresQuery.data?.features ?? [];
    return rows.map((r) => (r.value === null || r.value === undefined ? "0" : String(r.value)));
  }, [featuresQuery.data]);
  const [useLive, setUseLive] = useState(true);
  const [localCells, setLocalCells] = useState<string[]>([]);
  const run = useModelTest(t);

  const source = cells;
  // memoized per (mode, contract cells, edited cells): the live-vector view
  // is a fixed placeholder list and must not be rebuilt on every render
  const shown = useMemo(() => (useLive ? cells.map(() => "—") : localCells), [useLive, cells, localCells]);

  const startCustom = () => {
    setLocalCells(source.length > 0 ? [...source] : []);
    setUseLive(false);
  };

  const submit = async () => {
    await run.mutateAsync({ spec, cells: localCells, useLive });
  };

  // invalid-cell set: recomputed only when the mode, the contract or the
  // edited cells change — same rule, same result, no per-render Set churn
  const badIdx = useMemo(() => {
    const invalid = new Set<number>();
    if (!useLive && spec) {
      localCells.forEach((c, i) => {
        const n = Number(c);
        if (c.trim() === "" || !Number.isFinite(n) || n < spec.min || n > spec.max) invalid.add(i);
      });
    }
    return invalid;
  }, [useLive, spec, localCells]);

  return (
    <Panel
      title={t("debug.model.title", "Model instant test (/api/debug/model-test)")}
      accent
      right={<span className="timestamp-note">{spec ? t("debug.model.contract", "contract: {n} values ∈ [{min}, {max}]", { n: spec.dimension, min: spec.min, max: spec.max }) : t("debug.model.contract_missing", "contract not loaded — send blocked")}</span>}
    >
      <div className="dbg-sec">
        <div className="l3-toolbar">
          <span className="timestamp-note">{t("debug.model.feature_source", "feature source")}</span>
          <span className="segmented">
            <button className={useLive ? "active" : ""} onClick={() => setUseLive(true)}>
              {t("debug.model.live_vector", "live vector")}
            </button>
            <button className={!useLive ? "active" : ""} onClick={startCustom}>
              {t("debug.model.custom_vector", "custom vector")}
            </button>
          </span>
          <button className="btn small" onClick={() => void featuresQuery.refetch()} disabled={featuresQuery.isFetching}>
            {t("debug.model.reload_contract", "reload contract")}
          </button>
          <span className="timestamp-note">
            {featuresQuery.data ? t("debug.model.last_vector", "last vector @ {at} · {state}", { at: featuresQuery.data.timestamp_utc ?? "—", state: featuresQuery.data.is_stale ? t("debug.model.stale", "STALE") : t("debug.model.fresh", "fresh") }) : t("debug.model.no_features", "no features read yet")}
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
                  })} inputMode="decimal" aria-label={t("debug.model.feature_aria", "feature {i}", { i })} />
                </label>
              ))}
              {shown.length === 0 && <div className="l3-note warn">{t("debug.model.needs_contract", "custom mode needs the live contract first — press \"reload contract\".")}</div>}
            </div>
            {badIdx.size > 0 && <div className="l3-note bad">{t("debug.model.cells_invalid", "{n} cell(s) invalid — non-finite or out of bounds. The POST is blocked until they pass.", { n: badIdx.size })}</div>}
          </>
        )}
        <div className="l3-toolbar" style={{ justifyContent: "flex-end" }}>
          <button
            className="btn primary dbg-run"
            disabled={run.isPending || (!useLive && (!spec || localCells.length === 0 || badIdx.size > 0))}
            onClick={() => void submit()}
          >
            {run.isPending ? t("debug.model.inferring", "inferring…") : useLive ? t("debug.model.run_live", "Run on LIVE vector") : t("debug.model.run_custom", "Run on custom vector")}
          </button>
        </div>
        <ResultStrip result={run.isPending ? { running: true, lastResult: null, lastMessage: null } : run.data ? { running: false, lastResult: run.data.ok, lastMessage: run.data.message } : null} />
        {run.data?.ok && run.data.result && (
          <div className="dbg-verdict-wrap">
            <div className="l3-verdict dbg-verdict">
              <MetricCard
                label={t("debug.model.verdict", "verdict")}
                value={run.data.result.predicted_label ?? "—"}
                tone={run.data.result.predicted_label === "BUY_MARKET" ? "pos" : run.data.result.predicted_label === "SELL_MARKET" ? "neg" : "dim"}
                sub={t("debug.model.sub_class", "class {i}", { i: run.data.result.predicted_class_index ?? "?" })}
              />
              <MetricCard label={t("debug.model.confidence", "confidence")} value={((run.data.result.confidence ?? 0) * 100).toFixed(1) + "%"} sub={t("debug.model.sub_argmax", "argmax over {n} heads", { n: (run.data.result.probabilities ?? []).length })} />
              <MetricCard label={t("debug.model.e2e", "e2e")} value={`${run.data.result.latency_ms ?? "—"} ms`} sub={t("debug.model.sub_model", "model {ms} ms", { ms: String(run.data.result.model_forward_ms ?? "—") })} />
              <MetricCard label={t("debug.model.source", "source")} value={run.data.result.model_source ?? "—"} sub={t("debug.model.sub_source", "features: {src} · sanitized {n}", { src: run.data.result.feature_source ?? "—", n: String(run.data.result.sanitized_inputs ?? 0) })} />
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
                {t("debug.model.latency_breakdown_label", "latency breakdown:")} {Object.entries(run.data.result.latency_breakdown).slice(0, 10).map(([k, v]) => `${k}=${String(v)}`).join(" · ")}
              </div>
            )}
            <div className="tiny faint">{t("debug.model.evaluated_at", "evaluated_at {at}", { at: String(run.data.result.evaluated_at ?? "—") })}</div>
          </div>
        )}
      </div>
    </Panel>
  );
}
