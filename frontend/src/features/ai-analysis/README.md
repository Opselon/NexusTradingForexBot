# AI-Analysis (bounded context)

Live model signal + decision explainability: latest/history signals,
decision stats, NO_TRADE reason distribution, per-decision gates/evidence/
explanation drawers, indicator wall (gauges/oscillators/MAs/pivots), shadow
70D disagreements + drift.

- Invariants: RESOURCE_NOT_FOUND (no signals yet) and ENGINE_UNAVAILABLE
  (indicators, no synthetic bars) are honest empty states — never zero-fill.
- Confidence normalization has an explicit rule (0..1 vs 0..100 vintage);
  unknown scales render "—", never a guess.
- Read-only feature; no mutations, hence no confirm modals here.
- Evidence: src/nexus_scalp/web/api_v1/{signals,decisions,indicators,shadow}.py;
  behavior reference Web/app.js tab-ai-analysis.
- Shared UI helpers imported from features/research/ui/lane5Kit (lane-5 owned).
