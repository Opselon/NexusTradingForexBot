# Lane 11 — Input-Validation Boundary Audit (wave 20260914)

Scope: every external input boundary of the Nexus Scalp Engine — broker ticks,
gateway wire, paper/replay data, spread/tick values, OHLC+volume candle
ingestion, news payloads, web API request bodies, config/env, CLI options,
persisted-state reload, broker responses, and model feature vectors.
Classification per the MUST-FAIL-CLOSED list (corrupted price, risk calc,
schema mismatch, unknown mode) vs may-degrade.

Repo: `NexusTradingForexBot` @ `9431edd2` (nse/master-active-scalper-wave).
Method: static read + executed probes (`.venv/Scripts/python.exe`, repo root).
VERIFIED markers = observed in an executed command's output, not inferred.

Legend — PRESENT: boundary validated at the entry surface. PARTIAL: validated
for some defect classes, gaps for others (gap named). ABSENT: no validation at
the boundary (defect only if the boundary is on a MUST-FAIL-CLOSED path).
Fail-closed = bad input is rejected/blocked/raised. Fail-open = bad input is
accepted, silently coerced, or the failure path proceeds with defaults.

---

## 1. Direct MT5 tick boundary (`adapters/mt5`)

**Verdict: PARTIAL — fail-closed on structural price, fail-open on non-finite
quotes from a connected terminal.**

Evidence:

* `TickData` domain model validates the price contract at construction:
  `bid/ask gt=0.0`, `volume ge=0.0`, `last ge=0.0`
  (`src/nexus_scalp/domain/models.py:30-33`) plus a `model_validator` negative-
  spread guard `bid > ask → ValueError` (`domain/models.py:44-49`) and UTC
  timestamp coercion (`domain/models.py:36-41`).
  **Gap probe VERIFIED**: pydantic `gt`/`ge` accept infinity —
  `TickData(bid=1.0, ask=inf)` is ACCEPTED and `spread_points`/mid become
  `inf`; `bid=inf, ask=inf` is ACCEPTED with `spread_points = NaN`
  (`inf-inf`); `volume=inf` ACCEPTED. `TickData` fields are plain `float`
  with no `allow_inf_nan=False`/finite validator
  (`domain/models.py:30-33`, `models.py:50-52`). NaN is rejected only as a
  side-effect of the `gt` comparison (`nan > 0` is False) — VERIFIED:
  `bid=NaN → REJECTED`, `bid=inf → ACCEPTED`.
* The MT5 snapshot layer converts raw ticks with `or 0.0` fallbacks and does
  NOT check finiteness: `get_broker_tick` maps `snap.bid = float(getattr(raw,
  "bid", 0.0) or 0.0)` (`adapters/mt5/mt5_adapter.py:539-541`); spread is only
  computed when both sides `> 0` (`mt5_adapter.py:552-553`); staleness flag at
  30 s (`mt5_adapter.py:547-551`). A raw `inf` bid/ask from a malfunctioning
  terminal flows through unchanged (0.0 is the sentinel for missing, which the
  legacy reader then rejects: `mt5_adapter.py:374-380` raises when
  `bid is None or ask is None` — note `0.0 or None` mapping means a literal
  0.0 bid becomes `0.0`, not `None`, and is caught only because `TickData`
  requires `gt=0`).
* `get_last_tick` raises `RuntimeError` on unavailable tick — "never returns
  fake data" (`mt5_adapter.py:374-390`) — then constructs `TickData`
  (structural guards apply). Fail-closed for missing, fail-open for
  non-finite.
* The engine consumes ticks at `application/live/runtime_loop.py:376`
  (`tick = self.om.adapter.get_last_tick(symbol)`) with no extra finiteness
  screen between adapter and `BarAggregator.process_tick`; the aggregator
  computes `price = (bid+ask)/2` and writes it straight into bar OHLC state
  (`market_data/bar_aggregator.py:124`), so an inf/NaN-through-TickData tick
  (the gap above) would poison the forming bar. Mitigations: bid>ask is
  structurally impossible, out-of-order ticks are dropped
  (`bar_aggregator.py:107-121`), duplicate quotes suppressed upstream
  (`runtime_loop.py:408-419`), and any raise in the pipeline feeds the
  hot-path circuit breaker → DEGRADED (`live_engine.py:3137-3175`).
* Spread gate consumption: `signals/policy.py:735,755-756,785` guard with
  `math.isfinite(spread_tp_ratio)` before comparing (`policy.py:905-911`), and
  non-finite inputs are checked at `policy.py:562,2595-2610`. A non-finite
  spread cannot silently pass the ratio gates; the entry itself still depends
  on `current_spread > 0.0` semantics — an `inf` spread fails the finite test
  and blocks (fail-closed at consumer level, not at boundary level).

**Classification**: corrupted-price boundary — MUST FAIL CLOSED. The
structural half (bid/ask > 0, bid ≤ ask, negative spread) is fail-closed.
The non-finite half (±inf) is **fail-open at the TickData boundary**
(GAP L11-1) with partial fail-closed compensation in downstream consumers
(finiteness tests in policy, aggregator contract tests MD-1..MD-11).
Suggested one-line fix: `bid/ask/last/volume: float = Field(..., allow_inf_nan=False, gt=...)`
or an `after`-validator `math.isfinite` sweep on `TickData`.

## 2. Gateway — server side (`gateway/server.py`)

**Verdict: PARTIAL — transport auth fail-closed; payload validation fail-open
with silent defaults (order-path).**

* Auth chain: body-size cap 64 KiB (`server.py:50,358-360`), HMAC-SHA256 over
  `timestamp + raw body` with `compare_digest` (`server.py:100-104`), ±300 s
  timestamp skew (`server.py:51,370-378`), api-key equality (`server.py:380`),
  all 401 fail-closed. Well-known default secrets refused for LIVE-allowed
  gateways (AUDIT-B2, `server.py:57-98`) — fail-closed `RuntimeError`.
  Unknown actions → `_json_failed(f"unknown action {action}")` (`server.py:395`)
  — fail-closed for unknown mode-like input.
* Payload surface: every handler parses `payload` as a raw `dict` with
  **defaults**, never a Pydantic model:
  `volume=float(payload.get("volume") or 0.01)`, `price=float(payload.get("price")
  or 0.0)`, `stop_loss=... or 0.0` (`server.py:330-337`, `296-303`,
  `272-288`, `345-349`). A missing or `null` volume becomes 0.01 lots; a
  missing price becomes 0.0 (then the MT5-side `_validate_pending_request`
  rejects structurally for PENDING orders — `mt5_adapter.py:1954-2062` — but
  the MARKET path `execute_market_order` performs NO structural validation at
  all: it forwards the dict to `mt5.order_send` (`mt5_adapter.py:1167-1235`)).
  Order type strings are fail-closed (`OrderType(...)` else `_json_failed
  "unknown order_type"` — `server.py:293-297,276-280,331-335`).
  Negative volume, NaN price (`"price": NaN` is valid JSON) and absurd prices
  are not range-checked server-side → broker-side retcode is the only gate
  (fail-open to broker, mitigated by MT5 itself rejecting invalid floats).
* GET_LAST_TICK response passes `snap.bid/ask` through `float()` without
  finiteness checks (`server.py:225-246`); the client re-validates via
  `TickData` construction (§3) — structural only, same inf gap.
* Error hygiene: exception text never echoed to the client (CodeQL
  stack-trace-exposure fix, `server.py:414-431`) — good.

## 3. Gateway — client side (`adapters/mt5/remote_gateway.py`)

**Verdict: PARTIAL — typed-model reconstruction fail-closed on structure,
crash-on-garbage fail-open-into-error.**

* Every response is rebuilt through validated domain models: `AccountInfo`
  (`remote_gateway.py:95-107`), `SymbolInfo` (`109-125`), `TickData`
  (`127-146`), `BarData` per row (`148-177`), `Position` (`179-200`).
  `SymbolInfo`/`AccountInfo` carry `gt/ge` constraints
  (`domain/models.py:65-77,90-93`) so a malicious/broken server cannot deliver
  negative equity, zero point, volume_max ≤ 0 etc. without a `ValidationError`
  — fail-closed on structure.
* Missing keys raise `KeyError`/`TypeError` out of `res["data"]`
  (`remote_gateway.py:94`, `112`, `131`) — the call fails loudly (no silent
  default), which is fail-closed but untyped (not `GatewayProtocolError`);
  non-"SUCCESS" statuses raise in `_sync_ping` (`remote_gateway.py:228-233`)
  and trade paths map FAILED/exception to `False`/`UNKNOWN` tri-state
  (`write_market_order` semantics preserved client-side,
  `remote_gateway.py:241-281`).
* Same `inf` gap as §1: `TickData(bid=float(data["bid"]))` accepts `inf` from
  the wire (VERIFIED in §1 probe).
* Client default secrets `default_local_key`/`default_local_secret` in the
  ctor (`remote_gateway.py:44-48`) — server refuses them for LIVE (§2), so the
  pair is fail-closed only when the server enforces AUDIT-B2.

## 4. Paper adapter data (`adapters/paper`)

**Verdict: mostly PRESENT (fail-closed by contract); persisted-state reload is
the PARTIAL (see §10).**

* REPLAY wiring is fail-closed by design and VERIFIED in code: an attached
  `replay_source` that isn't a REPLAY mode source raises at `__init__`
  (`paper_adapter.py:140-142`); `build_paper_adapter` refuses to serve a
  synthetic walk to an operator who asked for REPLAY at interactive boot
  (`on_replay_unavailable="raise"`, `paper_data.py:105-131`), and the
  re-alignment degradation path stamps `degraded_reason` into
  `replay_provenance` loudly — never silent (`paper_data.py:112-131`).
  Exhausted replay freezes on the last historical tick and raises
  `ReplayDataUnavailableError` when there is no tick — never a synthetic
  fallback (`paper_adapter.py:899-908`).
* Replay records build `TickData` (guards apply) — `paper_adapter.py:858-866`;
  raw CSV rows parse `float(row["open"]...)` (`replay_source.py:136-141`) —
  a non-numeric cell raises `ValueError`/`KeyError` inside `_load` which is
  wrapped: "corrupt data is DETECTED and REJECTED, never served"
  (`replay_source.py:115`); zero-row file raises (`replay_source.py:145`).
  No finiteness check on parsed CSV values (a CSV cell of `inf`/`nan` text
  parses to float inf/nan) → then `TickData` rejects NaN/0 but **accepts
  `inf`** (§1 gap re-surfaces on the paper path, PAPER-only blast radius).
* Synthetic quotes: spread band from canonical artifact costs, degenerate band
  raises (`paper_adapter.py:235-268`); stress spread scale env clamped
  `max(0.1, min(s,10.0))` with non-numeric → 1.0 default
  (`paper_adapter.py:269-283`) — bounded coercion, may-degrade class.
* `reconcile_accounting` asserts equity == balance + unrealized (`paper_adapter.py:513-537`)
  — internal consistency gate.

## 5. OHLC + volume candle ingestion (candles, bars, candle_intelligence)

**Verdict: PARTIAL — rich validator exists but is only advisory on the MT5
history read; bar models are unvalidated.**

* `validate_ohlc_bars` (adapters/mt5/providers.py:861-914) checks ascending
  unique timestamps, finite OHLC, high/low geometry, negative volume. It is
  called ONLY from `get_rate_history` and ONLY logs a warning — "malformed
  bars are NOT mutated - callers decide rejection" (`providers.py:866`) and
  the caller does not reject: `mt5_adapter.py:744-756` logs `invalid>0` and
  returns the bars anyway. **GAP L11-2 (corrupted price, fail-open):** invalid
  broker history (NaN OHLC, high<low, duplicate/descending timestamps) flows
  into every consumer of `get_rate_history`.
* `get_historical_bars` (`mt5_adapter.py:905-937`) drops bars only for `None`
  fields, not for NaN/inf/inverted geometry; `BarData` is a plain pydantic
  model with NO constraints (`market_data/bar_aggregator.py:18-33`).
  **PROBE VERIFIED**: `BarData(open=nan, high=inf, low=-1.0, tick_volume=-7)`
  constructs cleanly, and `BarAggregator.reseed([NaN bar])` ACCEPTS it and
  seeds the forming bar with `open=nan` — a NaN close becomes the live bar
  seed (`reseed` filters only duplicates/uncompleted/non-ascending,
  `bar_aggregator.py:199-217`). The reseed path is fed by
  `live_engine._resync_from_broker` → `adapter.get_historical_bars`
  (`live_engine.py:2763-2767`): corrupted broker history therefore poisons the
  canonical bar series, features, and liquidity warmup. The only thing between
  this and corrupted-price execution is the HTF/freshness gates, not any OHLC
  integrity check.
* Live tick → bar path (MD-1..MD-11 wave): symbol identity ValueError
  fail-closed (`bar_aggregator.py:88-93`), out-of-order drop
  (`bar_aggregator.py:107-121`), reseed clock rebase (`bar_aggregator.py:233`),
  future-stamp handled at freshness layer (STALE, fail-closed —
  `live_freshness.py:53-68` MD-8), loop-level dedup guard fixed to read
  engine state (`runtime_loop.py:408-419` MD-7). These are integrity guards
  against replays/foreign symbols, not value-range guards (§1's inf gap is
  reachable here: `price=(bid+ask)/2` unchecked, `bar_aggregator.py:124`).
* Candle-intelligence DB writes ARE validated: `record_candle` rejects
  non-finite OHLC (`candle_intelligence/store_writes.py:67-68`) and coerces
  non-finite volume/fields via `_safe_float` (`store_writes.py:30-35`).
  Fail-closed for that store (may-degrade: it silently returns `False`).

## 6. Spread / tick values (secondary boundary summary)

**Verdict: PRESENT (consumer-side fail-closed), ABSENT (source-side finite guard).**

* Broker spread computed only when bid&ask > 0 (`mt5_adapter.py:552-553`).
* Policy spread gates require finite ratios (`signals/policy.py:735-786,905-911`);
  `isfinite` rejections fall to NO_TRADE paths.
* Paper spread bounded by canonical band + clamped scale (§4).
* The one source-side gap is `TickData` inf acceptance (§1); every consumer
  gate that compares spread against thresholds behaves fail-closed for
  `inf` (fails `isfinite` or exceeds limits), so the composite risk is a
  poisoned bar mid (§5) more than a bad spread gate.

## 7. News payloads (`news/`)

**Verdict: PARTIAL — typed models validated at write boundary; ingestion degrades
invalid items by skip/now-fallback (may-degrade class).**

* `NewsArticle` and friends are constrained pydantic models: scores
  `ge=0,le=1`, `poll_interval_sec ge=10`, timestamps `field_validator`
  (`news/models.py:119-224,131-133,207,214`).
* RSS/Atom fetch: unparseable dates fall back to `_utc_now()`
  (`news/sources/base.py:63-72,193,313`) — a published-at-less story becomes
  "just published" (freshness inflation, may-degrade for news, which only
  boosts/penalizes confidence within bounded caps).
* Ingest: item without a title is skipped (`fetcher.py:258-261`); duplicates
  and tombstones short-circuit (`fetcher.py:266-283`); DB write still passes
  raw dicts to `insert_article` rather than `NewsArticle.model_validate` —
  the model guards are enforced by column CHECKs/the analyzer path, not at
  ingest. Non-typed payload fields (source config `dict[str, Any]`) are
  operator-controlled YAML, not external input.
* News gate on missing/stale context returns IGNORE — "no fake influence"
  (`news/gate.py:110-116`); confidence deltas bounded by
  `max_confidence_boost/penalty` (`news/config.py:55-59`). Fail-closed for
  influence, degrade for ingestion. Acceptable per may-degrade.

## 8. Web API request bodies (`web/`)

**Verdict: PARTIAL — the read-only v1 surface is strict; the debug/operator
surface consumes raw `dict[str, Any]` bodies.**

* `api_v1` query params bounded (`Query(default=100, ge=1, le=500)` —
  `api_v1/market.py:85`); `ModeProposal` is a typed body with explicit
  transition whitelist (`api_v1/runtime.py:81-83,100-112`) — unknown mode
  string → `errors` + 422, **fail-closed for unknown mode** (and the endpoint
  validates only, never applies).
* The mutating surface — `POST /api/runtime-config/apply` — accepts
  `payload: dict[str, Any]` (`web/diagnostics_state_routes.py:1666-1687`),
  NOT a Pydantic model. The defense is server-side: every key must be a known
  flat key and pass `_VALIDATORS` (`configuration/runtime_config.py:753-760`
  `unknown configuration key` → whole-batch reject, atomic;
  `validate_field` `runtime_config.py:660-668`), plus cross-field
  constraints (`runtime_config.py:956-969` risk_per_trade ≤ max_drawdown) and
  `_coerce` type conversion with `isinstance(v, bool)` anti-pattern guards
  (AGENT-18 fixes, `runtime_config.py:626-651`). This is effectively a
  fail-closed validator registry — good — but the router itself has no
  schema/type wall (a `dict`-typed body accepts any JSON object; malformed
  values are rejected at validation, so the boundary behavior is fail-closed
  despite the untyped signature).
* Other raw-dict bodies: `db_console.console_query` — SQL console with
  prefix whitelist + banned-keyword scan + row cap (`db_console.py:410-450`,
  fail-closed read-only); `marketplace.InstallBody/EnableBody/RepairBody`
  typed but permissive (`mode: str` unchecked at the router; the service
  rejects unknown enable-mode strings DENIED — `marketplace/service.py:255-266`
  **fail-closed for unknown mode**, VERIFIED by the explicit `ValueError`
  branch).
* API auth: token middleware fail-closed (`web/auth.py:1-26,201-202` —
  unresolvable token raises, LIVE mode mandatory auth).

## 9. Config / env (`configuration/`)

**Verdict: PARTIAL (strong) — bootstrap YAML + env + runtime store all
range-validated; the flagged `le=100` upper bound is present-but-permissive,
bounded in practice by the cross-field rule.**

* Bootstrap `AppConfig` (pydantic-settings, `NSE_` env prefix, nested `__`,
  `configuration/config.py:222-227`): `RiskConfig` has explicit ranges —
  `risk_per_trade_pct gt=0 le=100`, `max_account_drawdown_pct gt=0 le=100`,
  `max_margin_usage_pct gt=0 le=100`, `max_concurrent_positions ge=1`,
  `max_spread_points ge=0`, `max_allowed_lots gt=0`
  (`config.py:47-57`); `AlgoConfig` fully bounded (`config.py:100-160`);
  `ModelConfig.confidence_threshold ge=0 le=1` (`config.py:88`).
  **PROBE VERIFIED**: `risk_per_trade_pct` accepts exactly `100.0`
  (= risk the entire account per trade), rejects 100.1/0/-1/NaN/inf; unknown
  enum mode strings rejected (`mode="WEIRD"`, `mode="live"` lowercase →
  ValidationError — ExecutionMode is an uppercase StrEnum, `domain/enums.py:11-26`),
  so **unknown mode at config load is fail-closed**; extra nested keys are
  dropped (pydantic default `extra=ignore`, VERIFIED: `bogus_key` not in
  `model_fields_set`).
  **GAP L11-3 (Agent-15 carry-over, confirmed)**: `le=100` is a *legal but
  catastrophic* value at the boundary; the runtime-store cross-field validator
  (`runtime_config.py:956-969`) only caps it relative to
  `max_account_drawdown_pct` (itself ≤100), so `risk_per_trade_pct=100` +
  `max_account_drawdown_pct=100` passes both layers. Risk-calc class ⇒ should
  be a much lower sane ceiling (e.g. le=5) or an operator warning.
  Runtime-side mirror of the same bound: `"risk.risk_per_trade_pct":
  0.0 < v <= 100.0` (`runtime_config.py:604`).
* Mode fail-open exception: persisted `execution.mode` read from the settings
  DB is applied ONLY if it is a known enum member, else silently ignored —
  `live_engine.py:868-873` (`if persisted_mode in {m.value for m in
  ExecutionMode}`) — the fallback is the YAML/CLI default (PAPER). That is
  fail-safe-directional (unknown mode → conservative default) but SILENT; the
  `align_adapter_to_boot_mode` unknown-mode branch logs a warning and makes
  NO change (`live_engine.py:4176-4182`) — degrades loudly, does not trade on
  an unknown mode. `runtime_config` `execution.mode` validator whitelists
  `LIVE|PAPER|SHADOW|SIMULATION|NO_OP` (`runtime_config.py:609-612`).
  Classification: acceptable (unknown mode never executes; PAPER default).
* Canonical execution-costs JSON: missing file → hard raise ("refusing to fall
  back to per-module hardcoded costs"), malformed → `model_validate` raise
  (`configuration/execution_costs.py:190-207`) — fail-closed.
* Env int reader for broker offset: invalid/out-of-range ignored with a single
  warning, default kept (`adapters/mt5/providers.py:50-88`) — may-degrade,
  bounded, correct direction for a timezone knob (default = documented).

## 10. Persisted-state reload

**Verdict: MIXED — safety-state and config reload are fail-closed; paper-state
JSON is fail-OPEN on partial corruption; experience/order-intent reloads drop
bad records (acceptable).**

* Risk safety state: `resolve_boot_decision` — HALTED/KILL_SWITCH never
  auto-cleared, ANY unknown row state → `trading_allowed=False`
  "UNKNOWN_PERSISTED_STATE — fail closed" (`risk/runtime_safety.py:179-206`).
  MUST-FAIL-CLOSED (unknown mode) — PRESENT.
* Runtime config: rehydrate = full revalidation through
  `build_runtime_configuration`; any error → keep bootstrap snapshot + warn
  (`configuration/runtime_config.py:1049-1078`) — fail-closed (schema
  mismatch ⇒ whole batch rejected, known-good base survives).
* DB startup migration gate: never raises, surfaces `DB_MIGRATION_FAILED`
  state; `assert_ready` raises `MigrationError` (`database/gate.py:31-117`)
  — boot gate fail-closed.
* Model artifact reload: manifest/meta coherence + schema-identity + registry
  binding all raise `ArtifactIntegrityError`/`RuntimeError` on mismatch; a
  corrupt scaler sidecar is flagged `corrupt` and the inference path refuses
  to serve (`model_bundle_store.py:90-131,234-273,303-396`;
  `application/live/inference.py:225-239` — VERIFIED by code path:
  "Scaler sidecar corrupt ... refusing to serve the model with raw unscaled
  features" → caller degrades to `probs=None`).
* **Paper adapter state (`paper_state.json`) — GAP L11-4 (fail-open):**
  `_load_state` applies fields sequentially inside one `try`
  (`adapters/paper/paper_adapter.py:360-415`); a field-by-field mutation
  pattern means corruption BEFORE the failing line is already applied.
  **PROBE VERIFIED**: with `balance=-99999.0, equity=NaN, _ticket_counter=
  "notanint"`, `_load_state()` returned `False` (the int() raise) yet the
  adapter was left with `balance=-99999.0` and `equity=nan` APPLIED (tickets
  unchanged 100001, positions dropped). `connect()` calls `_load_state()` and
  **ignores its return value** (`paper_adapter.py:428-435`), so the adapter
  trades on the half-applied corrupted state. There is no numeric validation
  (neg balance/NaN equity pass `float()`); `Position.model_validate` failures
  drop rows silently (`except Exception: continue`, `paper_adapter.py:393-397`)
  — silently losing open-position state is itself fail-open for a simulation
  book. Blast radius: PAPER/SHADOW only (no live capital), but paper
  experience is training evidence — corrupt-equity sessions poison learned
  data. MUST-FAIL-CLOSED applies strictly to live-critical state; classify
  as **high-priority should-fail-closed**: load into a temp object, apply
  atomically only on full validation, and finite-check balance/equity.
* Order-intent JSONL reload: bad line → `continue` ("corrupt line never
  breaks recovery", `execution/order_write.py:142-158`) — a dropped PENDING
  intent means an ambiguous write may go unreconciled after restart
  (fail-open-ish), but the dispatch layer re-verifies against broker truth
  (reconciliation + BUG-256 halt gate), and dataclass `OrderIntent(**json)`
  raises TypeError→caught on missing keys. May-degrade with a noted blind spot.
* Experience ledger reload: invalid payloads skipped + logged
  (`experience/ledger.py:468-501`), causality-inverted outcomes rejected
  (`ledger.py:485-493`). May-degrade (evidence-only store), acceptable.

## 11. Broker responses (order send / calc / history)

**Verdict: PRESENT — typed tri-state with UNKNOWN ≠ FAILED.**

* `write_market_order` / `write_pending_order` expose SUCCESS/REJECTED/UNKNOWN
  with idempotency fingerprint evidence and never blind-retry
  (`mt5_adapter.py:1220-1330`); ambiguous non-DONE retcodes are resolved
  against live positions before any retry (`mt5_adapter.py:1200-1216`);
  retcode maps pinned by module-level asserts (`mt5_adapter.py:123-128`).
* Pending requests pre-validated against broker truth: volume min/max/step,
  tick alignment, stops/freeze level, SL/TP geometry — `(False, reasons)` fail
  closed (`mt5_adapter.py:1954-2062`), repair loop bounded.
* Snapshot reads carry `available/error_state` and legacy readers raise rather
  than fabricate (`mt5_adapter.py:334-390`; `providers.py:171-266`
  `as_error`). `order_calc_*_snapshot` return typed BrokerCalcSnapshot
  (`mt5_adapter.py:822-905`).
* Broker epoch conversion NaN/Inf-guarded (`providers.py:129-146`,
  `providers.py:440-470` normalize_utc rejects non-finite).
* Residual: finiteness of `price`/`volume` fields inside snapshots
  (`_float_attr` `providers.py:525-535`) is type-only — same inf caveat;
  PositionSnapshot values feed `Position`-shaped domain guards downstream.

## 12. Model feature vectors (live inference + features/)

**Verdict: PRESENT — the strongest boundary in the repo; one deliberate
sanitize-and-clip exception on the 50D fallback lane.**

* `InferenceValidator` chain SCHEMA→DIM→ORDER→HASH→FINITE→BOUNDS(-3..3)→
  NEWS/LIQ availability→FRESHNESS→SCALER with explicit rejection codes and
  "NEVER silently repairs, pads, truncates or substitutes"
  (`features/inference_validator.py:1-20,112-249`; RejectionCode enum
  `:26-37`). Wired into the canonical 70D live hook
  (`features/runtime70.py:117-127`) incl. `compatible_model_schema`
  PASS/BLOCK/UNKNOWN gate (`inference_validator.py:283-320`;
  `runtime70.py:181-196` — non-PASS ⇒ ok=False, no inference).
* 70D live assembly: `validate_70d_vector` raises `SchemaContractError` on
  dim/non-finite/out-of-range/hash (`features/schema_contract.py:220-253`);
  `build_live_feature_vector` STALE/INVALID liquidity → raise → inference
  blocked, incident telemetry + degrade to `probs=None`
  (`application/live/inference.py:135-160,192-215`) — MUST-FAIL-CLOSED
  (schema mismatch) PRESENT.
* BUG-253 stale-liquidity gate: inference served only behind `_liq_ok`
  (`application/live/tick_pipeline.py:489,574`), probs=None → policy
  PROBS_UNAVAILABLE_DEGRADED NO_TRADE (`signals/policy.py:187`,
  `live_engine.py:3333`).
* Corrupt scaler sidecar → refuse raw unscaled serving (§10 model artifact
  bullet; `inference.py:225-239`).
* **Exception to "never sanitizes" (GAP L11-5, deliberate):**
  `_validate_50d_tensor` maps non-numeric → 0.0 (warn), non-finite → 0.0
  (warn), and CLIPS to [-3,+3] (`application/live_engine.py:4586-4617`);
  post-scaler tensor gets `torch.nan_to_num(x, nan=0.0, posinf=1.0,
  neginf=-1.0)` (`application/live/inference.py:272`). A garbage feature
  therefore becomes a *plausible* feature instead of a blocked inference on
  the 50D champion lane (the production champion path for base50). The 70D
  lane is strict (validate_70d_vector raises). Direction: 50D sanitize is
  fail-open-by-design (availability bias) — documented in the file header as
  "sanitizes", but it sits on a MUST-FAIL-CLOSED-adjacent path (corrupted
  model input → risk calc). Recommend at minimum an incident counter on
  sanitize events (clip path is silent besides one log line per feature).
* Model-vs-runtime compat table also gates the governance/UI model swap
  (`features/liquidity_runtime.py:290-345` — BLOCK/UNKNOWN never serve).

## 13. CLI options (`cli/`)

**Verdict: PARTIAL — mode/symbol bounded; numeric options mostly unbounded.**

* `nexus start --mode`: whitelist `MODE_ALIASES` else EXIT_USAGE —
  fail-closed unknown mode (`cli/engine_boot.py:81-91`); LIVE requires
  interactive confirm or explicit `-y` with `--json`
  (`engine_boot.py:71-73`); config default NEVER live (`config.py:37`,
  `runtime_config.py` ExecutionSnapshot.mode="PAPER" AGENT-18 note
  `:96-105`).
* Setup wizard: unknown mode prompt → forced PAPER (fail-safe silent downgrade,
  `cli/wizard.py:149-151`); symbol free-text uppercased with empty→XAUUSD
  (`wizard.py:163-168`) — an arbitrary symbol string is persisted; engine-side
  whitelist `enabled_symbols=("XAUUSD",)` (`config.py:44-47`,
  `runtime_config.py:104`) is the effective fail-closed gate.
* Numeric options: `--page-size min=1 max=200` (`cli/api_commands.py:127,189-190`)
  shows typer `min/max` is used where applied, but the majority of the 224
  `typer.Option` sites carry no `min=`/`max=` (e.g. `--port` unbounded
  `engine_boot.py:66` — OS bind error is the only gate; risk release
  `--actor/--note` free text `risk_commands.py:138-141`, benign). Port and
  file paths are operator-scope, not capital-scope ⇒ may-degrade, acceptable
  except where a numeric feeds risk sizing (none found: no CLI option
  plumbs a volume/risk number directly — sizing comes from validated
  runtime config).

---

## Gap summary (ranked)

| # | Gap | Boundary | Class | Direction |
|---|-----|----------|-------|-----------|
| L11-1 | `TickData`/`BarData` accept `±inf` (pydantic `gt/ge` pass inf); spread/mid become inf/NaN | MT5 tick, gateway tick, paper replay | corrupted price — MUST FAIL CLOSED | fail-open at boundary; mostly fail-closed downstream |
| L11-2 | `validate_ohlc_bars` report is log-only; `get_historical_bars` + `BarAggregator.reseed` accept NaN/inf/inverted OHLC bars (probe: NaN forming-bar seed) | broker history → canonical bar series | corrupted price — MUST FAIL CLOSED | fail-open |
| L11-3 | `RiskConfig.risk_per_trade_pct le=100` (+ cross-field only vs drawdown ≤100) — 100% per-trade risk legal at both config layers (Agent-15 carry-over, confirmed live at 9431edd2) | config/env + runtime store | risk calc — MUST FAIL CLOSED (sane ceiling) | permissive bound |
| L11-4 | `paper_state.json` `_load_state` applies fields before failing (probe: balance=-99999, equity=NaN applied despite return False); `connect()` ignores the False; silent position drop | persisted state reload (PAPER/SHADOW) | partial-state corruption; may-degrade class (no live capital) but poisons training evidence | fail-open |
| L11-5 | `_validate_50d_tensor` sanitize-to-0 + clip[-3,3] and `nan_to_num` turn broken 50D features into plausible inputs (no failure counter) | model feature vectors (50D champion lane) | schema mismatch/corrupted input — boundary is MUST-CLOSED; this is a documented deliberate exception | fail-open-by-design |
| L11-6 | Gateway server order payloads default silently (`volume or 0.01`, `price or 0.0`); no finite/range checks before `execute_market_order` (no structural validation on the market path, unlike pending) | gateway wire → broker | execution boundary; broker retcode is the backstop | fail-open to broker |
| L11-7 | `api/runtime-config/apply` + several debug routes accept `dict[str, Any]` bodies (no Pydantic wall; protection lives in `_VALIDATORS`) | web API | mitigated (whole-batch reject) | fail-closed-in-effect |
| L11-8 | News `published_at` unparseable → now() (freshness inflation); ingest bypasses `NewsArticle` model | news payload | may-degrade | acceptable |
| L11-9 | Corrupt line in order-intent JSONL skipped — a lost PENDING intent can leave an ambiguous write unreconciled until position truth sweep | persisted state | may-degrade w/ blind spot | acceptable, monitor |

## Already-closed highlights (VERIFIED in code, pinned by tests)

Unknown persisted safety state → halt (fail-closed, MUST list ✓); config
unknown-mode/unknown-key/NaN → reject (`ExecutionMode` enum, `_VALIDATORS`,
whole-batch atomic reject) ✓; 70D feature contract (dim/hash/finite/bounds/
freshness/scaler) → raise + NO_TRADE ✓; BUG-253 stale-liquidity gate ✓;
MD-1..MD-11 bar-integrity contracts (symbol identity raise, out-of-order drop,
future-stamp→STALE, dedup guard) ✓; gateway HMAC+skew+size caps with LIVE-
default-secret refusal ✓; update manifest schema-version strict ✓; broker
UNKNOWN≠FAILED tri-state writes + `_validate_pending_request` ✓; web auth
fail-closed ✓; execution-costs artifact missing ⇒ hard refusal ✓.

Probes: 4 boundary probes executed with `./.venv/Scripts/python.exe` from repo
root (TickData/BarData inf/NaN acceptance; paper `_load_state` partial-apply;
AppConfig mode/risk boundaries) — outputs marked VERIFIED above; probe
scripts were kept outside the repo tree (`%TEMP%`), zero repo writes besides
this report.
