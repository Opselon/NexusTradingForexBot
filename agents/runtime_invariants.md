# RUNTIME INVARIANTS — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §8 (see `agents/multi-agent-git-contract.md`).
> These are NON-NEGOTIABLE runtime guarantees. Every agent must explicitly
> consider these invariants when modifying shared runtime code.
> Any intended invariant change requires explicit review + a DEC-XXXX decision record.
> Verified status per agents/skill.md forensic badges (🟢 VERIFIED etc.).

## INV-001 — Tick hot path MUST NOT synchronously query the DB
Live tick hot path (`_process_tick_pipeline`) has zero synchronous DB. All
writes are queued (audit/candle) or cached (news context cache-only, rule
matrix TTL 5s, experience score TTL 30s + ≤1/s refresh budget).
Status: 🟢 VERIFIED (2026-08-18 performance audit).

## INV-002 — Learning subsystem MUST NOT place orders
Experience/learning/research/strategies NEVER hold an adapter or risk
engine — no order authority.

## INV-003 — RiskEngine remains authoritative for risk boundaries
All entry proposals pass through `calculate_dynamic_volume()`; clamped to
free margin (20%) and account tier caps.

## INV-004 — OrderManager remains authoritative for execution
Execution routing, position lifecycle, breakeven lock, profit giveback.
Enforces `HARD_MAX_LOTS = 10.0` and `MAX_TOTAL_EXPOSURE = 1`.

## INV-005 — One canonical execution lineage must NOT become duplicate experiences
Split fills (multiple MT5 fills for one logical position) must not create
duplicate learning experiences (BUG-081). Child fills inherit parent
execution context.

## INV-006 — Duplicate broker events MUST NOT create duplicate outcomes
Idempotent outcome recovery; duplicate orders/fills deduped (no-order-ID
duplicates, BUG-081).

## INV-007 — Historical experience is immutable
Historical experience/outcome rows are never rewritten to make metrics look
better; corrections flow through raw evidence → reconstruction → derived
record → provenance (contract §47).

## INV-008 — Future information MUST NOT enter current feature/decision context
No lookahead: broker rate history INCLUDES the still-forming current minute
— history ingestion must REPLACE + ALIGN (reseed, BUG-058 pattern), never
blind-append. Labeling uses triple-barrier with embargo/purge discipline.

## INV-009 — 50D/60D/350D feature ordering must be schema-controlled
Feature ordering is a schema contract. A dimension change is NEVER a minor
refactor (contract §26).

## INV-010 — Telegram is a read-only consumer of canonical state
Telegram/UI never mutate canonical state. Telegram credentials route ONLY
via `settings_service.set_telegram()` — never live.yaml (BUG-080).

## INV-011 — Broker truth takes precedence over stale local execution state
When reconciling active exposure, broker-authoritative state wins over stale
in-memory session caches (BUG-072/074 pattern).

## INV-013 — Exit classification must carry evidence provenance
Every persisted `exit_mechanism` must be accompanied by its evidence source
(ENGINE_FORCED / BROKER_DEAL_REASON / BROKER_DEAL_COMMENT / SL_GEOMETRY /
TP_GEOMETRY / FALLBACK_HEURISTIC) and confidence. Never present an inferred
exit label as broker-proven (EXIT_CLASSIFICATION v3, BUG-088). The
position-lifecycle timeline must be finalized (POSITION_EXITED) and must
carry the decision identity (trade_id/order_id) on every event (BUG-089).

## INV-012 — UNKNOWN evidence must never be silently promoted
UNKNOWN broker exit reasons stay UNKNOWN instead of assuming MANUAL_CLOSE or
another confident classification (DEC-0021 pattern, BUG-081).

## INV-013 — Model loadability requires the deterministic load gate (TASK-6)
No model is loaded into shadow merely because its file exists: the 10-gate
load gate (artifact/hash/manifest/schema/dimension/scaler/label/validation/
lifecycle) must pass; the exact failing gate is reported (MODEL_LOAD_REJECTED).
Status: VERIFIED (tests/unit/test_model_governance_phase16.py).

## INV-014 — Shadow can never mutate execution state (TASK-6)
The shadow comparison path imports no order manager / risk engine / adapter.
Challenger failure, timeout or invalid probability is FAILURE_ISOLATED and
the Champion prediction path continues unaffected.
Status: VERIFIED (TEST-LG-10/11/12).

## INV-015 — Promotion requires explicit operator approval (TASK-6)
SHADOW -> CHAMPION is an illegal transition; the only legal path is
READY_FOR_REVIEW -> APPROVED -> CHAMPION with an operator actor and
approval token. Rollback restores the previous Champion and preserves
evidence about the failed model. No automatic promotion exists.
Status: VERIFIED (TEST-LG-21..24).

## INV-019 — Liquidity features are causally confirmed and schema-gated (TASK-01-60D-LIQUIDITY)
The 10 liquidity dimensions (scalp_liquidity_v1, indices 50..59) are computed
ONLY from bars closed at/before the decision timestamp; a swing pool is usable
only from its confirmed_at (candidate bar + SWING_CONFIRM_BARS=5); HTF
evidence uses only completed buckets (the forming H1/H4/D1 candle is
EXCLUDED); sweep state requires penetration + rejection/reclaim evidence in a
later closed bar (breakout is never a sweep); every value is finite, clipped
[-3,+3] via one central clip, deterministic (same input -> same output), and
pure (no DB/network). The flag `model.liquidity_features_enabled` (default
false) switches the layer without silently altering schema expectations.
Status: 🟢 VERIFIED (2026-08-19 TASK-01-60D-LIQUIDITY: 60 focused tests incl.
TEST-LIQ-23..28 anti-leakage, TEST-60D-BASE-01 first-50 unchanged).

## Registry notes
- `agents/bugs.md` BUG-NNN entries provide the forensic evidence for each invariant.
- New invariants: append with INV-NNN and reference the evidence.

## INV-013 — GitHub Releases is the ONLY packed-update source
Installed end-user builds update ONLY from a published GitHub release
artifact (with digest + manifest); source archives (main.zip, source
tarballs) are never a production payload. GitHub unavailability yields a
truthful NO_UPDATE/error status — never a fabricated "latest == current".

## INV-014 — An update never disrupts LIVE trading without explicit authorization
`nexus update` BLOCKS while the engine is LIVE (UPDATE_BLOCKED_WHILE_LIVE)
unless the user invokes the documented --force maintenance quiesce flow.
The update itself never liquidates positions; user credentials and
databases are never touched by a normal update.

## INV-015 — 60D (scalp_v2) features are causally produced and schema-gated
The 10 extra 60D dimensions (features/schema_augment.py) are computed ONLY
from completed bars + the decision tick, with documented defaults for
missing data (never NaN/Inf), and are fixed-order via the schema registry.
News is an OPTIONAL additional input (news_enabled), never forced into the
60D base; the causal snapshot rule (events at-or-before sample time) is not
overridden to fabricate signal.
Status: 🟢 VERIFIED (2026-08-18 TASK-5: compute_60d_extras + dataset build on
real M5; news postdating the dataset produced zero vectors, recorded as
NEWS_INCONCLUSIVE_NO_OVERLAP — never a fabricated signal).

## INV-017 — Database hygiene is non-destructive by default
The hygiene worker defaults to AUDIT_ONLY; only operator-explicit
SAFE_CLEAN may apply pre-approved safe classes with confidence 1.0;
AGGRESSIVE_CLEAN requires separate activation. Financial/broker truth,
migration history, model provenance, research evidence are NEVER
auto-deleted. Every destructive batch is archived-before-delete,
journaled, budget-bounded, and verified after each batch
(DATABASE_HYGIENE v1, BUG-099). Never runs on the tick hot path;
BUSY databases are DEFER-ed, never forced.

## INV-016 — Candidate training can never promote or touch the Champion
Model training writes candidate ids only; FAILED/non-finite/exploding runs
are terminal FAILED states; no automatic promotion path exists; the live
engine loads the Champion from the operator-owned artifact path.
Status: 🟢 VERIFIED (2026-08-18 TASK-5 experiment: 4 candidate cells,
all REJECTED, Champion hash unchanged).

## INV-013 — Database evolution is migration-controlled (TASK-10)
Schema changes (tables/columns/indexes) MUST be declared as versioned,
checksummed migrations in `src/nexus_scalp/database/registry.py` and applied
only through `DatabaseMigrationEngine` (startup gate, `nexus db`, updater).
Never add DDL directly to bootstrap SQL outside migration control. Migrations
are additive-first, idempotent, WAL-safe-backup-backed; destructive changes
require operator review; downgrades are blocked; the engine never deletes a
database file and never rewrites raw financial/news/research/model truth.

## INV-018 — 70D shadow observations are observability-only (TASK-05-70D-SHADOW)
The 70D shadow runtime (shadow/shadow70) MAY read live market state, build the
70D vector (50D canonical + 10 news + 10 liquidity when the producer exists),
infer with the shadow candidate and persist observations/research rows. It
MUST NOT influence execution, policy, RiskEngine, OrderManager, broker
interactions, live confidence thresholds or the Champion. Shadow observations
are SIMULATED research telemetry, never accounting outcomes (no experience
ledger writes, no hypothetical PnL in accounting). A shadow fault is isolated:
it never crashes or blocks the tick path (INV-001 intact). Persistence is
queued/batched — no synchronous DB on the tick path. Only a validated
candidate (VALIDATED_CANDIDATE status + verified manifest/hash/schema/
dimension/scaler) may enter Shadow; otherwise the runtime reports
NO_VALIDATED_CANDIDATE and stays idle.


## INV-019 — Model-generation training must be reproducible from seed (TASK-4, BUG-101)
Candidate/benchmark training seeds torch+NumPy RNG BEFORE constructing the
model (weight init must be deterministic across fresh processes). Any trainer
that builds the model before seeding violates the fair-benchmark/reproducibility
contract (TASK-4 brief §39). WalkForwardTrainer and CandidateTrainer comply
post-BUG-101.
Status: 🟢 VERIFIED (2026-08-19 TASK-04: fresh-process smoke identical 0.3 == 0.3).

## INV-020 — Liquidity Intelligence toggle is information-only and hot-reloadable (TASK-02-70D-INTEGRATION)
`model.liquidity_features_enabled` (SettingsService key, HOT_RESTRICTED)
controls ONLY feature/status information generation (70D liquidity block at
60..69). Enabling/disabling it NEVER changes orders, SL/TP, RiskEngine,
execution mode, account state, news state, or the active model. The runtime
applies it without engine restart; persistence goes through SettingsService
(never live.yaml direct writes). Liquidity availability is independent of
news availability in both directions.
Status: 🟢 VERIFIED (2026-08-19 TASK-02: 99 liquidity tests + API smoke).
