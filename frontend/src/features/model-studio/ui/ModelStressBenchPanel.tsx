/**
 * ModelStressBenchPanel — adversarial stress battery + latency benchmark.
 *
 * Robustness verdicts across extreme volatility, missing features, and
 * outlier contamination; plus a 100-pass P50/P90/P99 latency profile.
 *
 * Stateless presentation component; owned state lives in ModelStudioPage.
 */

import { Panel } from "@/components/primitives";
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
  const passed = stressResults.filter((r) => r.passed).length;
  const allPassed = stressResults.length > 0 && passed === stressResults.length;

  return (
    <div className="ms-grid-half">
      {/* Adversarial stress battery */}
      <Panel
        title="Adversarial Stress Battery"
        subtitle="Extreme volatility, missing-feature, and outlier contamination survival."
        accent
        right={
          stressResults.length > 0 ? (
            <span className={`badge ${allPassed ? "good" : "warn"}`}>
              {passed} / {stressResults.length} PASSED
            </span>
          ) : null
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button onClick={onRunStress} disabled={stressBusy} className="ms-btn-action ms-btn-primary">
              {stressBusy ? "⏳ Running Battery…" : "☣ Run Stress Battery"}
            </button>
            <span className="tiny faint inline-mono">{dimension}D tensor contract</span>
          </div>

          {stressResults.length === 0 ? (
            <div className="tiny faint" style={{ textAlign: "center", padding: "32px 0" }}>
              No stress results yet — run the battery to audit the champion under adversarial conditions.
            </div>
          ) : (
            <div className="table-wrap" style={{ maxHeight: 380 }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Adversarial Test</th>
                    <th scope="col" style={{ textAlign: "center" }}>Verdict</th>
                    <th scope="col">Diagnostic Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {stressResults.map((r, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 600, color: "var(--text)" }}>{r.test}</td>
                      <td style={{ textAlign: "center" }}>
                        <span className={`badge ${r.passed ? "good" : "bad"}`}>
                          {r.passed ? "PASS" : "FAIL"}
                        </span>
                      </td>
                      <td className="tiny tx-dim" >{r.detail}</td>
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
        title="Inference Latency Benchmark"
        subtitle="100-pass forward-pass profile — percentile latency and sustained throughput."
        accent
        right={
          benchStats ? (
            <span className={`badge ${benchStats.sla_passed ? "good" : "warn"}`}>
              {benchStats.sla_passed ? "SLA PASS" : "SLA BREACH"}
            </span>
          ) : null
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button onClick={onRunBenchmark} disabled={benchBusy} className="ms-btn-action ms-btn-success">
              {benchBusy ? "⏳ Benchmarking…" : "⏱ Run 100-Pass Benchmark"}
            </button>
            <span className="tiny faint inline-mono">{dimension}D tensor contract</span>
          </div>

          {!benchStats ? (
            <div className="tiny faint" style={{ textAlign: "center", padding: "32px 0" }}>
              No benchmark profile yet — run 100 passes to measure P50 / P90 / P99 latency headroom.
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
                  { l: "P50 Latency", v: benchStats.latency_p50_ms.toFixed(3) + " ms", c: "var(--green)" },
                  { l: "P90 Latency", v: benchStats.latency_p90_ms.toFixed(3) + " ms", c: "var(--accent-strong)" },
                  { l: "P99 Latency", v: benchStats.latency_p99_ms.toFixed(3) + " ms", c: "var(--amber)" },
                  {
                    l: "Throughput",
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
                  Percentile Ladder
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
                            <span className="tx-dim" >{b.v.toFixed(3)} ms</span>
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
                {benchStats.iterations} iterations at {benchStats.dimension}D — tail latency at P99 must stay
                inside the engine's signal validity window or the SLA gate fails.
              </div>
            </>
          )}
        </div>
      </Panel>
    </div>
  );
}
