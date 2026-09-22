/**
 * ModelStressBenchPanel — adversarial stress battery + latency benchmark.
 *
 * Robustness verdicts across extreme volatility, missing features, and
 * outlier contamination; plus a 100-pass P50/P90/P99 latency profile.
 *
 * Stateless presentation component; owned state lives in ModelStudioPage.
 */

import { Panel } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import type { BenchmarkResponse, StressTestResultRow } from "../model";

interface ModelStressBenchPanelProps {
  dimension: number;
  stressBusy: boolean;
  stressResults: StressTestResultRow[];
  onRunStress: () => void;
  benchBusy: boolean;
  benchStats: BenchmarkResponse | null;
  onRunBenchmark: () => void;
}

export function ModelStressBenchPanel({
  dimension,
  stressBusy,
  stressResults,
  onRunStress,
  benchBusy,
  benchStats,
  onRunBenchmark,
}: ModelStressBenchPanelProps) {
  const t = useI18n((s) => s.t);
  const passed = stressResults.filter((r) => r.passed).length;
  const allPassed = stressResults.length > 0 && passed === stressResults.length;

  return (
    <div className="ms-grid-half">
      {/* Adversarial stress battery */}
      <Panel
        title={t("model-studio.stress.battery_title", "Adversarial Stress Battery")}
        subtitle={t(
          "model-studio.stress.battery_subtitle",
          "Extreme volatility, missing-feature, and outlier contamination survival.",
        )}
        accent
        right={
          stressResults.length > 0 ? (
            <span className={`badge ${allPassed ? "good" : "warn"}`}>
              {t("model-studio.stress.passed_badge", "{p} / {n} PASSED", {
                p: passed,
                n: stressResults.length,
              })}
            </span>
          ) : null
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button onClick={onRunStress} disabled={stressBusy} className="ms-btn-action ms-btn-primary">
              {stressBusy
                ? t("model-studio.stress.run_busy", "⏳ Running Battery…")
                : t("model-studio.stress.run_idle", "☣ Run Stress Battery")}
            </button>
            <span className="tiny faint inline-mono">
              {t("model-studio.contract.tensor", "{d}D tensor contract", { d: dimension })}
            </span>
          </div>

          {stressResults.length === 0 ? (
            <div className="tiny faint" style={{ textAlign: "center", padding: "32px 0" }}>
              {t(
                "model-studio.stress.empty",
                "No stress results yet — run the battery to audit the champion under adversarial conditions.",
              )}
            </div>
          ) : (
            <div className="table-wrap" style={{ maxHeight: 380 }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t("model-studio.stress.th_test", "Adversarial Test")}</th>
                    <th style={{ textAlign: "center" }}>{t("model-studio.verdict.verdict", "Verdict")}</th>
                    <th>{t("model-studio.verdict.detail", "Diagnostic Detail")}</th>
                  </tr>
                </thead>
                <tbody>
                  {stressResults.map((r, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 600, color: "var(--text)" }}>{r.test}</td>
                      <td style={{ textAlign: "center" }}>
                        <span className={`badge ${r.passed ? "good" : "bad"}`}>
                          {r.passed
                            ? t("model-studio.verdict.pass", "PASS")
                            : t("model-studio.verdict.fail", "FAIL")}
                        </span>
                      </td>
                      <td className="tiny" style={{ color: "var(--text-dim)" }}>{r.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Panel>

      {/* Latency benchmark */}
      <Panel
        title={t("model-studio.stress.bench_title", "Inference Latency Benchmark")}
        subtitle={t(
          "model-studio.stress.bench_subtitle",
          "100-pass forward-pass profile — percentile latency and sustained throughput.",
        )}
        accent
        right={
          benchStats ? (
            <span className={`badge ${benchStats.sla_passed ? "good" : "warn"}`}>
              {benchStats.sla_passed
                ? t("model-studio.stress.sla_pass", "SLA PASS")
                : t("model-studio.stress.sla_breach", "SLA BREACH")}
            </span>
          ) : null
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button onClick={onRunBenchmark} disabled={benchBusy} className="ms-btn-action ms-btn-success">
              {benchBusy
                ? t("model-studio.stress.bench_busy", "⏳ Benchmarking…")
                : t("model-studio.stress.bench_idle", "⏱ Run 100-Pass Benchmark")}
            </button>
            <span className="tiny faint inline-mono">
              {t("model-studio.contract.tensor", "{d}D tensor contract", { d: dimension })}
            </span>
          </div>

          {!benchStats ? (
            <div className="tiny faint" style={{ textAlign: "center", padding: "32px 0" }}>
              {t(
                "model-studio.stress.empty_bench",
                "No benchmark profile yet — run 100 passes to measure P50 / P90 / P99 latency headroom.",
              )}
            </div>
          ) : (
            <>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
                  gap: 10,
                }}
              >
                {[
                  {
                    l: t("model-studio.stress.stat_p50", "P50 Latency"),
                    v: benchStats.latency_p50_ms.toFixed(3) + " ms",
                    c: "var(--green)",
                  },
                  {
                    l: t("model-studio.stress.stat_p90", "P90 Latency"),
                    v: benchStats.latency_p90_ms.toFixed(3) + " ms",
                    c: "var(--accent-strong)",
                  },
                  {
                    l: t("model-studio.stress.stat_p99", "P99 Latency"),
                    v: benchStats.latency_p99_ms.toFixed(3) + " ms",
                    c: "var(--amber)",
                  },
                  {
                    l: t("model-studio.stress.stat_throughput", "Throughput"),
                    v: benchStats.throughput_inferences_per_sec.toLocaleString() + " /s",
                    c: "var(--violet)",
                  },
                ].map((m) => (
                  <div
                    key={m.l}
                    style={{
                      padding: 10,
                      borderRadius: 8,
                      background: "var(--bg-inset)",
                      border: "1px solid var(--border)",
                    }}
                  >
                    <div className="tiny faint uppercase" style={{ fontWeight: 700, letterSpacing: "0.08em" }}>
                      {m.l}
                    </div>
                    <div className="inline-mono small" style={{ color: m.c, fontWeight: 800, marginTop: 2 }}>
                      {m.v}
                    </div>
                  </div>
                ))}
              </div>

              {/* Percentile ladder visualization */}
              <div>
                <div className="tiny uppercase font-bold" style={{ color: "var(--accent-strong)", marginBottom: 8 }}>
                  {t("model-studio.stress.ladder_title", "Percentile Ladder")}
                </div>
                {(() => {
                  const max = Math.max(
                    benchStats.latency_p50_ms,
                    benchStats.latency_p90_ms,
                    benchStats.latency_p99_ms,
                    1e-9,
                  );
                  const bars = [
                    { l: "P50", v: benchStats.latency_p50_ms, c: "var(--green)" },
                    { l: "P90", v: benchStats.latency_p90_ms, c: "var(--accent-strong)" },
                    { l: "P99", v: benchStats.latency_p99_ms, c: "var(--amber)" },
                  ];
                  return (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {bars.map((b) => (
                        <div key={b.l} className="ms-prob-row">
                          <div className="ms-prob-meta">
                            <span style={{ color: b.c }}>{b.l}</span>
                            <span style={{ color: "var(--text-dim)" }}>{b.v.toFixed(3)} ms</span>
                          </div>
                          <div className="ms-prob-track">
                            <div
                              className="ms-prob-fill"
                              style={{
                                width: `${Math.min(100, (b.v / max) * 100)}%`,
                                background: b.c,
                                boxShadow: `0 0 10px ${b.c}55`,
                              }}
                            />
                          </div>
                        </div>
                      ))}
                    </div>
                  );
                })()}
              </div>

              <div className="tiny faint">
                {t(
                  "model-studio.stress.footer",
                  "{n} iterations at {d}D — tail latency at P99 must stay inside the engine's signal validity window or the SLA gate fails.",
                  { n: benchStats.iterations, d: benchStats.dimension },
                )}
              </div>
            </>
          )}
        </div>
      </Panel>
    </div>
  );
}
