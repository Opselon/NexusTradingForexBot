# SEC-CAPITAL + DATA-BROKER Security Audit — 2026-09-04

**Scope:** `src/nexus_scalp/**` — trading safety: deserialization, hot-swap/promotion, path traversal, auth, silent failures, degraded-data trading.
**Method:** `grep torch.load`, `weights_only`, `hot_swap|champion|promot`, `except`, `artifact_path`, `auth|Depends|JWT`, `DEGRADED|stale|BLOCKED`, manual review of hot paths.

---

## 1. Model Deserialization (`torch.load` / `weights_only`) — **FAIL**

| Verdict | File:Line | Evidence |
|---------|-----------|----------|
| **FAIL** | `src/nexus_scalp/application/live_engine.py:2684` | `probe = torch.load(model_path, map_location="cpu")` — no `weights_only`, arbitrary pickle execution before dim check |
| **FAIL** | `src/nexus_scalp/application/live_engine.py:2727` | same — second probe path |
| **FAIL** | `src/nexus_scalp/application/live_engine.py:2755` | `state_dict = torch.load(model_path, map_location="cpu")` in `_load_or_initialize_model_weights` — **production load path with no `weights_only=True`** |
| **FAIL** | `src/nexus_scalp/governance/load_gate.py:66` | `state = torch.load(path, map_location="cpu", weights_only=False)` — explicit opt-out in the gate meant to *validate* |
| **FAIL** | `src/nexus_scalp/model_generation/runtime.py:110` | `weights_only=False` |
| **FAIL** | `src/nexus_scalp/model_lifecycle/integrity.py:269,314,426` | 3× `weights_only=False` |
| **FAIL** | `src/nexus_scalp/forensics/checks_features.py:421` | `weights_only=False` |
| **FAIL** | `src/nexus_scalp/shadow/challenger.py:127` | `weights_only=False` |
| **FAIL** | `src/nexus_scalp/web/model_governance_routes.py:582` | `state = torch.load(path, map_location="cpu", weights_only=False)` inside `_make_infer` closure (shadow70 attach) |
| **FAIL** | `src/nexus_scalp/research/streaming_replay.py:135` | `probe = torch.load(p, map_location="cpu")` — inherits default `weights_only=False` on affected torch versions |
| PASS | `src/nexus_scalp/model_lab/baseline.py:50`, `lab_runner.py:49`, `release/diagnostics.py:70`, `release/health.py:366,465,640`, `release/runtime_snapshot.py:141` | correctly use `weights_only=True` |

**Risk:** Any attacker (or compromised artifact store) who can write a `.pt` file reachable via `model_artifact_path` gets code execution in the trading process. Dimension mismatch quarantine (live_engine.py:2758-2773) runs *after* deserialization — too late.

**Fix:** Migrate all production loads to `weights_only=True` (state dicts are pure tensors). For legitimate non-tensor artifacts, pin an allow-list of safe globals or load via `np.load`/`safetensors`.

---

## 2. Hot-Swap / Champion Promotion — **FAIL**

| Verdict | File:Line | Evidence |
|---------|-----------|----------|
| **FAIL** | `src/nexus_scalp/application/live_engine.py:1496-1506` | `async def hot_swap_model(self, new_artifact_path: str)` → `new_path = Path(new_artifact_path)` → `if not new_path.exists():` — no allow-list, no `resolve().is_relative_to(artifacts_root)` |
| **FAIL** | `src/nexus_scalp/web/diagnostics_state_routes.py:1626-1639` | `async def model_hot_swap(payload)` reads `payload.get("model_artifact_path")` raw, `await engine.hot_swap_model(artifact)` — **zero auth**, no `Depends`, no role check |
| **FAIL** | `src/nexus_scalp/web/server.py:423-426` | `CORSMiddleware(allow_origins=["*"], allow_credentials=False)` — any origin can call `model_hot_swap` |
| PASS | `src/nexus_scalp/web/model_governance_routes.py:1245-1293` | `execute_promotion` actually gates on `actor+model_id+approval_token`, checks `promotion_frozen`, takes cross-process lock, re-verifies champion hash — no auto-promotion path (correctly `PROMOTION_BLOCKED` on missing fields) |
| PASS | `src/nexus_scalp/application/live_engine.py:1497-1587` | hot-swap is atomic: validates new bundle first, warms up under isolation, swaps under `_bundle_lock`, never replaces healthy model on failure |

**Risk:** Unauthenticated web caller can hot-swap any model file on disk directly into the live trading loop, bypassing the audited governance/promotion path (which is correctly locked). Path traversal compounds this (see §3).

---

## 3. Artifact Path Traversal — **FAIL**

| Verdict | File:Line | Evidence |
|---------|-----------|----------|
| **FAIL** | `src/nexus_scalp/application/live_engine.py:1505` + `web/diagnostics_state_routes.py:1636` | User-supplied string becomes filesystem path with no sanitization, no `resolve()`+`is_relative_to()`, no symlink check, no existence outside `artifacts/` rejection. `Path("/tmp/evil.pt")` passes the `exists()` check and is `torch.load`'d. |

**Fix:** Canonicalize: `p = Path(new_artifact_path).resolve(); if not p.is_relative_to(ARTIFACTS_ROOT.resolve()) or p.is_symlink(): reject`. Apply both at web boundary and at `hot_swap_model` entry.

---

## 4. Auth on Model-Swap Endpoints — **FAIL (hot-swap) / PASS (governance promotion)**

| Endpoint | Verdict | Evidence |
|----------|---------|----------|
| `POST /api/models/hot_swap` (`diagnostics_state_routes.py:1626`) | **FAIL** | No `Depends(get_current_user)`, no JWT/Bearer, no API key check. |
| `POST /api/models/promotion/execute` (`model_governance_routes.py:1245`) | **PASS** (weak) | Requires `approval_token` string; server re-verifies under lock. **Note:** token is *presence-checked* only — no signature/enforcement that token was issued to `actor` or is single-use/revoked. Recommend binding token to `actor+model_id+expiry` with HMAC. |

Global auth search (`grep Depends|HTTPBearer|OAuth2|JWT` over `src/nexus_scalp/web/`) returns **zero** auth dependencies — entire web API is unauthenticated (local-only assumption not documented/enforced).

---

## 5. Silent Exception Swallowing — **PASS (with notes)**

| Layer | Verdict | Evidence |
|-------|---------|----------|
| `audit_repository.py` (~25× `except Exception: pass`) | **PASS** | Explicitly documented at line 54: idempotent schema migrations — expected to ignore `already exists`. Isolated to DDL, not data path. |
| `adapters/mt5/providers.py:376-383,418,565` | **PASS** | Narrowly scoped to datetime parsing fallbacks; returns `None`/`UNAVAILABLE` — not trusted downstream. |
| `adapters/mt5/mt5_adapter.py:262-273,1241` | **PASS** | Version probing only (`pass` keeps boot alive). |
| `mt5/providers.py:563-566` | **PASS** | `tick_freshness_ms` computation wrapped, sets `None` on failure — stale detection degrades safely. |
| `application/live_engine.py:2688-2689,2714` | **FAIL (minor)** | `except Exception: pass` in `_expected_num_features_for_artifact` silently falls back to class `FEATURE_DIM`, masking corrupt/tampered artifacts. Should log warning. |
| `execution/order_manager.py` / `broker_history_sync.py` | **PASS** | Broad `except Exception` blocks log (`logger.error/debug`) or propagate — not silent. |

No bare `except:` found. No `except: pass` that silently hides trade-critical failures in broker data layer.

---

## 6. Trading on Degraded/Invalid Data — **PASS**

| Verdict | File:Line | Evidence |
|---------|-----------|----------|
| **PASS** | `src/nexus_scalp/application/live_engine.py:4073-4090` | `live_freshness_gate()` — **pure downgrade**: frozen inference → `BLOCKED_BY_STALE` / `NO_TRADE`, never upgrades to BUY/SELL, never fabricates confidence. Only production touchpoint of freshness model. |
| **PASS** | `live_engine.py:5097,5184,5202,5326` | `DEGRADED->BLOCKED` transition enforced; `_fresh_blocked` path emits `BLOCKED_BY_STALE` reason code for UI separation. |
| **PASS** | `live_engine.py:2631,2647` + `adapters/mt5/providers.py:150-151` + `mt5_adapter.py:513-514` | `tick_freshness_ms` + `tick_stale` (30s) + `tick_stale_after_sec=120` feed market calendar; stale state increments `_stale_state_detected_total` and triggers `STALE_STATE_REUSED` warning. |
| **PASS** | `adapters/mt5/diagnostics.py:214,259` + `adapters/mt5/providers.py:74-88` | `DEGRADED` state enum + `UNAVAILABLE` source tri-state — downstream never treats fallback estimate as `BROKER_NATIVE`. |
| **PASS** | `web/server.py:6057-6058` | Mode `DEGRADED` when LIVE config but MT5 disconnected — UI is truthful, not executable. |
| NOTE | `execution/order_manager.py` | Does not independently re-check freshness; trusts live_engine proposal. Acceptable because gate runs *before* dispatch (live_engine:4084), but recommend defense-in-depth check in order_manager before `mt5.order_send`. |

**AVAILABLE → DEGRADED → BLOCKED chain is correctly gated: DEGRADED ticks do NOT become trades; BLOCKED is terminal downgrade to NO_TRADE.**

---

## Summary Grades

| Area | Grade | One-liner |
|------|-------|-----------|
| torch.load / weights_only | **FAIL** | 9 production loads use `weights_only=False` or bare `torch.load` (RCE before validation) |
| Hot-swap integrity | **PASS** (atomic, validated) | Validates+warms new bundle under lock — correct, minus path/auth |
| Path traversal | **FAIL** | Arbitrary filesystem path accepted at web boundary and engine |
| Auth on model swap | **FAIL** | `model_hot_swap` is unauthenticated; CORS `*`; promotion token is presence-only |
| Silent exception swallowing | **PASS** | Migrations only; broker/execution layers log or degrade to UNAVAILABLE |
| Degraded-data trading | **PASS** | `live_freshness_gate` downgrades to BLOCKED/NO_TRADE; DEGRADED never trades |

## Required Fixes (priority)

1. **BLOCKED:** Set `weights_only=True` on all `torch.load` in live/governance/forensics/shadow/research integrity paths; add Bandit rule `B614`.
2. **BLOCKED:** Gate `hot_swap_model` to `ARTIFACTS_ROOT` with `resolve().is_relative_to()` + symlink reject; same at `diagnostics_state_routes.model_hot_swap`.
3. **BLOCKED:** Add auth to `model_hot_swap` (`Depends(require_operator)` or remove endpoint; promotion flow is the only approved mutation). Restrict CORS `allow_origins` from `*` to local UI origin, or require token.
4. **FAIL:** HMAC-bind `approval_token` to `(actor, model_id, expiry)` and enforce single-use.
5. **FAIL:** Log `except` fallback in `_expected_num_features_for_artifact` at `warning` instead of `pass`.
