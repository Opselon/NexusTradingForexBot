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

## INV-012 — UNKNOWN evidence must never be silently promoted
UNKNOWN broker exit reasons stay UNKNOWN instead of assuming MANUAL_CLOSE or
another confident classification (DEC-0021 pattern, BUG-081).

## Registry notes
- `agents/bugs.md` BUG-NNN entries provide the forensic evidence for each invariant.
- New invariants: append with INV-NNN and reference the evidence.
