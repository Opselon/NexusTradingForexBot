---
title: Capability Matrix
description: Every major capability with its evidence-graded status and where to verify it.
lang: en
---

# Capability Matrix

Legend: ✅ Certified · 🟢 Implemented · 🟡 Experimental · 🔵 Research · 🚧 In
progress · 📌 Planned · ⚪ Not verified

| Capability | Status | Evidence (repository) | Notes |
| :--- | :--- | :--- | :--- |
| Causal 50D feature engine (`scalp_v1`) | ✅ Certified | `features/scalp_features.py`, schema registry, golden/parity tests | live contract; NaN/Inf deterministic fallback |
| 70D canonical contract (`scalp_v3`) | 🟢 Implemented (research) | `features/schema_contract.py` SSoT, hash, inference validator (10 rejection codes) | candidate-only; negative OOS evidence so far |
| Liquidity intelligence (10D block) | 🟡 Experimental | `features/liquidity_engine.py`, INV-019 causality rules | research series; governed by `liquidity_features_enabled` |
| News intelligence 10D + bounded gate | 🟡 Experimental | `news/`, gate bounds boost ≤0.05 / penalty ≤0.10 | opt-in, disabled by default; can never force a trade |
| ScalpNet model (TCN + self-attention) | 🟢 Implemented | `models/scalp_net.py`, checkpoint rollback tests | 4-logit head (NO_TRADE/BUY/SELL/WAIT) |
| Artifact-first Model Factory | 🟢 Implemented | `model_generation/` (datasets/experiments/models with manifests) | inference needs no DB; ScalpNet = legacy baseline |
| Walk-forward + OOS gate | ✅ Certified | `training/walk_forward_trainer.py`, `research/oos.py`, BUG-183 purge/embargo regression tests | OOS failure ⇒ REJECTED |
| Deterministic friction-aware backtests | 🟢 Implemented | `research/backtest.py` | spread/slippage/latency stress |
| Replay (bit-exact vs dataset) | 🟢 Implemented | replay parity suites + anti-leakage tests | live = replay = training semantics |
| Streaming replay / forward tests | 🟢 Implemented | `research/streaming_replay.py`, `research/forward_test.py` (CHG-0035) | frozen-capture, no `order_send` (test-enforced) |
| Counterfactual engine (NO_TRADE walk) | 🔵 Research | `research/counterfactual.py` (CHG-0041; 2095 decisions walked) | evidence stratification only |
| Shadow runtime (zero order authority) | ✅ Certified | `shadow/`, `simulated=True` contracts, 60+ tests | live feed, no orders |
| Champion/Challenger governance | 🟢 Implemented | `governance/` (14-gate verify, promotion transaction, rollback preview) | strictly operator-gated |
| Risk engine (Kelly sizing, clamps) | ✅ Certified | `risk/risk_engine.py` + execution clamp suites | margin ≤20%, HARD_MAX_LOTS=10 |
| Execution (60-scenario router, 11 states) | 🟢 Implemented | `execution/order_manager.py` + golden extractions | circuit breaker → SAFE_MODE |
| Accounting ledger (SQLite WAL) | 🟢 Implemented | `accounting/`, `artifacts/audit.db` | immutable history; metrics without evidence = `n/a` |
| Experience / autopsy intelligence | 🟢 Implemented | `experience/`, behavior detectors (13), anomaly events | MANAGED_LOSS ≠ broken strategy |
| Incident response & forensics | 🟢 Implemented | `incidents/`, `forensics/` (deploy gate, health engine) | diagnostic-only; never mutates trading |
| Observability (structured logs) | 🟢 Implemented | severity-split logs, redaction, correlation IDs | OBS-001…016 gap ledger tracked |
| Control Center UI (FastAPI SPA) | 🟢 Implemented | `Web/`, SSE/WS APIs, debug console | buildless vanilla JS; no Node runtime |
| Docker packaging | 🟢 Implemented | `docker-compose.yml`, `/health` readiness gate | PAPER default; SQLite canonical |
| Windows installer + update/rollback | ✅ Certified | `installer/`, release.yml artifacts + verify suites | per-user, no admin; user data preserved |
| Provider health gate (LLM/AI services) | ✅ Certified | `strategies/factory/provider_gate.py` (CHG-0034/0039 live smoke) | auto-disable on permanent errors; secrets redacted |
| 70D temporal features (`scalp_v4_temporal_candidate`) | 🔵 Research | `features/temporal.py` (22D candidate) | never ACTIVE/CHAMPION without governance |
| MSLIE market-structure intelligence | 🔵 Research | `mslie/` (advisory contract) | perception layer, no order authority |
| Multi-broker support | 📌 Planned | roadmap | MT5-only today |
| Published performance claims | ⚪ Not verified | — | deliberately none; evidence is published instead |

Anything not listed here should be assumed 🟡/🔵/📌 rather than assumed
production-ready. Status disputes are resolved by the repository evidence
links above, not by this page.
