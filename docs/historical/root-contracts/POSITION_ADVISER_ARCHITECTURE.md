# Position Adviser — Architecture & State Map (Phase 0)

Scope: `src/nexus_scalp/position_adviser/`, `src/nexus_scalp/web/position_adviser_routes.py`,
`frontend/src/features/position-adviser/`, the decide-system seam in
`execution/order_manager.py`, and the persistence layers the adviser uses.

Evidence base: origin/main tip `77eb21b1`, live process on `nse-review-main`
(branch `review/main` @ `b08acc4c`, web at 127.0.0.1:8089), live PostgreSQL
`nexusdb`, live SQLite settings DB, the on-disk artifacts, and exercised API calls.

---

## 1. What actually exists (verified, not assumed)

### Backend — present and working

| Component | Location | State |
|---|---|---|
| Feature contract (12D) | `position_adviser/features.py` | Frozen order; leakage guard; train/serve parity docstring |
| Model head | `position_adviser/trainer.py` `PositionAdviserNet` | MLP 12→64→ReLU→32→ReLU→3 |
| Trainer + OOS protocol | `position_adviser/trainer.py` `train_position_adviser` | Chronological split, purge/embargo, OOS never fitted |
| Runtime service | `position_adviser/service.py` `PositionAdviserService` | Thread-safe, fail-closed, atomic load |
| Package integrity gate | `service.py` `verify_package_integrity` | sha256 pins for weights+scaler+feature schema |
| Decide-system seam | `position_adviser/integration.py` | Train/serve parity table; snapshot contract |
| Hot-path wiring | `execution/order_manager.py:3468-3519` | Applied AFTER giveback override, only lowers score |
| API routes | `web/position_adviser_routes.py` | status/models/train/load/unload/activate/config/advisories/checks/datasets/auto-tune |
| React page | `frontend/src/features/position-adviser/ui/PositionAdviserPage.tsx` | 1057 lines, hero + ladder + counters + training + auto-tune + models + feed |
| Legacy Web/ page | `Web/position_adviser_ui.js` + `/position_adviser_ui.js` route | second console (dual-console law) |

### API contract — verified against the LIVE server (curl, cookie-auth)

```
POST /api/position-adviser/load
  {"weights_path": "artifacts/position_adviser/pos_adviser_1790034969.pt",
   "scaler_path":  "artifacts/position_adviser/pos_adviser_1790034969.scaler.npz",
   "model_id":     "pos_adviser_1790034969"}

  <- {"status":"OK","model_id":"pos_adviser_1790034969",
      "weights_sha256":"c4f663f2ba0ac2996a6c2458bf8b82d45b2308f0c32bf62b922fd5d7ba538046",
      "integrity":"verified",
      "source_dataset_hash":"580cb177abdab45987cb65236e81f800",
      "feature_dim":12,
      "loaded_at":"2026-09-27T23:45:13.700416+00:00",
      "message":"adviser model loaded; activation still DISABLED until set"}
```

So the LOAD path is real, backend-backed, and verified. The premise
"Loaded model = NONE / no dedicated LOAD workflow" is **false at HEAD** —
it described a snapshot from before the model was loaded in that process.

### Feature contract (verified — the "12D" claim is TRUE)

```
idx  name                source                       meaning
 0  unrealized_pnl_r     integration  net R (price-friction)/R
 1  current_r_net        integration  same value (generator parity)
 2  current_return       integration  price_delta / entry
 3  distance_to_stop_r   integration  |price-sl| / R
 4  distance_to_target_r integration  |tp-price| / R
 5  position_age_bars    integration  holding_duration_sec / 60
 6  atr                  decide loop  live ATR
 7  spread               decide loop  live observed spread
 8  estimated_slippage   config       canonical execution assumption
 9  model_probability    entry state  entry confidence
10  model_confidence     entry state  entry confidence
11  signal_age           entry state  signal_age_bars (== pos age)
```

`ADVISER_FEATURE_DIM = 12` is the model's real input width; `net.0.weight`
is `(64, 12)`; the head is `(3, 32)`. Confirmed by loading the checkpoint.

### Dataset / labels (verified on `pos_ds_0729ed3ffda6fa39.parquet`)

```
rows 3115 · train 2134 · val 496 · oos 459 · purge 26
labels (all):   CLOSE 2121 · KEEP 926 · REDUCE 68
labels (OOS):   CLOSE  329 · KEEP 110 · REDUCE 20
majority baseline (top TRUE OOS label / OOS n) = 0.71678
unique position_id 358 of 3115 rows (one row per bar of a position)
```

Label logic (position_replay.py:1006-1024) is real future-outcome logic:
`continuation_val = best_future_r - cur_r_net`, KEEP/CLOSE/REDUCE thresholds.
No label column appears in `ADVISER_FEATURE_ORDER` (assert_no_label_leakage → []).
Purge + embargo + horizon-truncation quarantine at both interior boundaries
AND the dataset tail (F2).

### Live state right now

```
activation: PAPER (set via API; the "DISABLED" in the report was the pre-load state)
model: pos_adviser_1790034969, integrity verified, sha256 c4f663f2…, dataset 580cb177…
applied 0 · evaluated 0 · refused 0 · stale refused 0 · last_error ""
open positions: [] (broker has none) → no advisories yet (correct, not a bug)
safety: HALTED / trading REFUSED (drawdown 66.95% > 5.00%) — untouched
```

---

## 2. What is MISSING (the real defect list)

### D1 — NO tensor inspector (mission §11/§12/§33) — HIGH

There is no `/api/position-adviser/tensor/*` endpoint and no Tensor Inspector
panel in the React page. `grep -rn "tensor" frontend/src/features/position-adviser/`
returns nothing. The operator cannot see raw vs normalized features, NaN/Inf
counts, shape, dtype, or device. This is the largest spec'd feature absent.

### D2 — NO settings persistence (mission §5/§38) — HIGH

`grep -rn "position_adviser" src/nexus_scalp/settings/` → nothing. The
adviser's activation, model_id, config, stale gate, and auto-load preference
are **in-memory only**. A process restart loses all of it (verified: the route
module holds a module-level `_SERVICE` singleton with no rehydration). SQLite
settings DB exists (`SettingsDatabase.set/get`, table `application_settings`)
and is the mandated home; nothing writes adviser keys to it.

### D3 — NO PostgreSQL history (mission §6/§39) — HIGH

`nexusdb` has 125 tables; **zero** are adviser-owned (`*advis*` → none).
Training runs, OOS metrics, load/activation events, advisories, and lineage
are not persisted anywhere durable. The infrastructure is present and is the
documented pattern (`TrainingRunStore` + `queue_write`/`query_rows` +
`build_upsert_sql` over the audit domain, both providers) — the adviser just
never wires into it.

### D4 — NO live-decision diagnostic / decision-trace endpoint (mission §34) — MED

`GET /advisories` returns the ring of past advisories, but there is no
"current position → features → tensor → probabilities → decision" trace. The
feed item carries `p_keep`/`snapshot_id`/`snapshot_age_ms` — good evidence —
but no per-stage timestamps and no raw/normalized vector.

### D5 — Rollback / previous-model handle absent (mission §27) — MED

`load()` replaces `_state._model` with no retention of the prior model. A
failed hot-swap leaves no automatic restore path (the service does fail
closed — a rejected candidate never evicts the incumbent — but there is no
explicit `previous_model` handle or `rollback()` API).

### D6 — Many-model training is sequential (mission §22/§23/§44) — MED

`route_auto_tune` runs trials in a single loop, one `train_position_adviser`
call each; every trial re-reads the parquet and re-preprocesses (verified in
trainer: `_load_frame` per call). No shared dataset load, no bounded worker
pool, no resource detection. max_trials is capped at 60 by the DTO.

### D7 — Restart-required / hot-swap contract not surfaced (mission §3/§4) — MED

`load()` has no `restart_required` field. A schema-incompatible artifact is
rejected outright (correct), so restart is never genuinely required today;
the contract should state this explicitly rather than leave it undefined.

### D8 — Action policy only emits 3 actions; SL/TP not wired (mission §17/§18) — BY DESIGN, needs documenting

`ADVISER_ACTIONS = (KEEP, CLOSE, REDUCE)`. The ONLY execution channel is the
bounded negative `hold_score_adjustment` — the decide system's own exit logic
interprets the lowered score. There is deliberately no ADJUST_SL/ADJUST_TP/
direct CLOSE path from the adviser, because the repo's authority model
(INV-004) routes every broker mutation through `OrderManager` and the adviser
must never bypass it. See §5 for the decision.

---

## 3. UI → API → backend → DB contract map

```
React PositionAdviserPage
  └─ positionAdviserApi (features/position-adviser/api.ts)
      ├─ GET  /api/position-adviser/status      → svc.status() [in-memory]
      ├─ GET  /api/position-adviser/models      → artifact dir scan + .meta.json
      ├─ GET  /api/position-adviser/datasets    → model-studio _dataset_candidates
      ├─ GET  /api/position-adviser/advisories  → in-memory ring (max 200)
      ├─ POST /api/position-adviser/train       → train_position_adviser
      ├─ POST /api/position-adviser/load        → svc.load() [atomic, gated]
      ├─ POST /api/position-adviser/unload      → svc.unload()
      ├─ POST /api/position-adviser/activate    → svc.set_activation()
      ├─ POST /api/position-adviser/config      → svc.config (bounds-enforced)
      ├─ POST /api/position-adviser/checks/run  → real probes (model loaded, engine online)
      └─ POST /api/position-adviser/auto-tune   → bounded grid sweep
                                                  (NO persistence anywhere)
```

Decide path (tick hot loop, `order_manager._evaluate_hold_score`):

```
position state (broker truth)
  → build_position_state_for_adviser()      [integration.py; stamps snapshot_observed_at + snapshot_id]
    → service.evaluate(ticket, state)       [snapshot freshness gate → throttle → features → scaler → model → softmax → bounded adj]
      → apply_advisory_to_hold_score()      [clamps to [0, -max_pen]; DISABLED = byte-identical]
        → hold_score → existing exit logic  [the ONLY channel; adviser never calls the broker]
```

---

## 4. Persistence plan (mission §5 + §6)

**SQLite** (`application_settings`, via `SettingsDatabase`) — adviser settings:
`position_adviser.enabled`, `position_adviser.activation`,
`position_adviser.active_model_id`, `position_adviser.config` (JSON: thresholds,
stale gate, artifact dir), `position_adviser.auto_load`. Source `WEB_UI`,
versioned, audited into `settings_audit` — exactly the existing convention.

**PostgreSQL** (`nexusdb`, audit domain, via `queue_write`/`query_rows` +
`build_upsert_sql`, both providers) — new tables:
`pa_training_runs`, `pa_model_registry`, `pa_load_events`, `pa_advisories`.
DDL lives in `position_adviser/schema.py`, registered through the audit
domain's provisioning so a SQLite box still works.

---

## 5. Action policy (mission §17/§18 — explicit representation)

The adviser's action domain is `KEEP / CLOSE / REDUCE`. The execution channel
is the bounded hold-score adjustment, applied AFTER the decide system's own
scoring and the profit-giveback safety override — so:

- **KEEP** → adjustment 0.0 (never rewards; by construction).
- **CLOSE** → strongest negative adjustment (scales with probability advantage).
- **REDUCE** → at most half the penalty.

`ADJUST_SL` / `ADJUST_TP` are **intentionally not** adviser actions. The
repo's authority model (INV-004, BUG-242) routes every broker mutation
through `OrderManager.modify_position_manual` / `close_position_manual`, and
the adviser must never become a second execution path. An `ADJUST_TP` action
would also extend exposure duration — the opposite of the "never extend"
invariant the service is documented to hold. If an operator wants SL/TP
tightening, the honest path is the existing operator surface (which already
exists at `/api/positions/modify`), not this adviser. This decision is
recorded rather than silently omitted, and the UI will state it.

---

## 6. Verification harness

- Backend: `PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/unit/test_position_adviser*.py`
- Live API: `curl -b <cookiejar> http://127.0.0.1:8089/api/position-adviser/...`
  (cookie bootstrap: GET `/` first — the HttpOnly `nse_web_auth` cookie is the
  credential; the `.env` `NSE_WEB_AUTH_TOKEN` is a masked placeholder).
- PG: `postgresql://nse_user:nse_password_dev@localhost:5432/nexusdb`
  (NOT the `postgres` superuser — that account rejects the connection).
- Frontend: build in the worktree, serve via `NEXUS_ALT_UI_DIR`, Chrome MCP
  against `/alt/position-adviser`.
