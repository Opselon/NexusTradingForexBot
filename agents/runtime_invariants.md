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

## INV-016 — Candidate training can never promote or touch the Champion
Model training writes candidate ids only; FAILED/non-finite/exploding runs
are terminal FAILED states; no automatic promotion path exists; the live
engine loads the Champion from the operator-owned artifact path.
Status: 🟢 VERIFIED (2026-08-18 TASK-5 experiment: 4 candidate cells,
all REJECTED, Champion hash unchanged).
