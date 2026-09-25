# AI-Analysis (bounded context)

Live model signal + decision explainability: hero live-signal card (action +
radial confidence gauge + entry/SL/TP price ladder with R:R), KPI strip +
charts (action donut, confidence timeline, stage/reason bars), history table
(action chips, inline confidence cells), per-decision gates/evidence/
explanation drawer, indicator wall (gauges/oscillators/MAs/pivots), shadow
70D envelope + deep panel, intel hub (intelligence telemetry + position
timeline — legacy tab-ai-analysis parity).

- Invariants: RESOURCE_NOT_FOUND (no signals yet) and ENGINE_UNAVAILABLE
  (indicators, no synthetic bars) are honest empty states — never zero-fill.
- Confidence normalization has an explicit rule (0..1 vs 0..100 vintage);
  unknown scales render "—", never a guess.
- Charts are presentation-only (ui/vizMath.ts + ui/aaCharts.tsx): backend
  counts/rows/levels only; rows without a parseable timestamp are dropped and
  the drop count is captioned (never hidden); action families are a stated
  prefix classification (BUY*/SELL*/NO_TRADE/CLOSE_POSITION), unknown labels
  render "unknown"; R:R is arithmetic over the recorded levels.
- Read-only feature; no mutations, hence no confirm modals here.
- Evidence: src/nexus_scalp/web/api_v1/{signals,decisions,indicators,shadow}.py;
  behavior reference Web/app.js tab-ai-analysis.
- Shared UI helpers imported from features/research/ui/lane5Kit (lane-5 owned);
  ConfidenceGauge comes from components/viz; feature charts stay local (aa-*).
