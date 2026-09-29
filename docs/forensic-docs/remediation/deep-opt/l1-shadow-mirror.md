# DEEP-OPT L1 — retire the shadow whole-record JSON mirror

## What was measured (not estimated)

Read-only probe against the live ledger (`artifacts/audit.db`, 435.6 MB):

| metric | value |
|---|---|
| `shadow_decisions` size | 227.2 MB of which `payload` = **108.3 MB (48%)** |
| rows | 55,342 |
| `payload` bytes/row | 1,956 (min 1,947 / max 1,949 observed in the newest block) |
| mirror bytes that duplicate an existing column | **46.6%** (byte-identical values) |
| retired-mirror bytes still on disk | reported by `pending_mirror_bytes()`; reclaimed by `compact_database()` |

The mirror was `json.dumps(decision.model_dump(mode="json"), default=str)` —
the whole record serialized next to the flattened columns that already held it.

## Root cause

`ShadowStore.save_decision` (src/nexus_scalp/shadow/store.py) wrote the record
twice. It was never a storage decision: `model_dump()` was the shortest way to
persist "everything", and the columns were populated for query shape. Contract
Part 5 rule 8 names this exactly — *"do not automatically store ... duplicated
model payloads"* — and rule 41 (update amplification) is the second cost: the
outcome resolver rewrites the row, so every resolution re-lived the mirror.

## The change (fix the producer, rule 5 / 90)

`save_decision` no longer serializes the record. The only mirror content that
had no column is migrated rather than mirrored:

| mirror key | destination | measured bytes/row |
|---|---|---|
| `champion_probabilities` | TEXT JSON column | ~89 |
| `challenger_probabilities` | TEXT JSON column | ~91 |
| `champion_strategy_id` | TEXT column | ~28 |
| `challenger_strategy_id` | TEXT column | ~30 |
| `hypothetical_risk_pct` | REAL column | ~30 |
| `hypothetical_volume` | REAL column | ~28 |
| `hypothetical_entry` | REAL column | ~27 |
| `hypothetical_exit` | REAL column | ~26 |

Everything else in the mirror (`champion`/`challenger` model identity,
`shared_input` feature/market identity, all 18 scalar outcome/geometry fields)
is byte-identical to a populated column or to the `shadow_runs` row, so nothing
is lost by leaving it behind.

**Latent loss closed:** `apply_to_record_fields` computed
`hypothetical_entry`/`hypothetical_exit` and `apply_resolved_outcome` DROPPED
them (the source comment admitted "have no DB columns"). They are now persisted
by the resolver — a real addition to the realized-R contract, not a subtraction.

## Expected effect

| metric | before | after |
|---|---|---|
| mirror bytes written per decision | ~1,956 | **0** |
| replacement columns per decision | — | ~350 B |
| steady-state per-row cost | ~4,106 B | **~2,150 B (~48% smaller)** |
| bytes reclaimable on the existing 55,342 rows | — | ~108 MB of a 227 MB table |
| outcome-resolution UPDATE payload | full row rewrite incl. mirror | 16 scalar columns |

## Rollback and compatibility (rule 86)

* `ensure_schema` backfills the new columns from the retired mirror, once per
  row (guard: mirror present AND probabilities still empty), then reports the
  reclaimable bytes. A row that does not parse is skipped, never dropped.
* `read_decision_row` is the **retained legacy reader**: it returns the vector,
  strategy and hypothetical fields from whichever source the row carries, with
  the column winning. An un-compacted database keeps reading correctly.
* The retired column is **retired, not dropped**. A SQLite `DROP COLUMN`
  rewrites every row — write amplification the contract forbids (rule 40/41).
  `compact_database(db_path)` does the reclaim explicitly, measured and
  idempotent; it is never called automatically.
* Identical artifact written to `src/nexus_scalp/shadow/schema.py` so SQLite and
  PostgreSQL keep ONE schema (dual-path law, rule 51).

## Verification actually executed

```
tests/unit/shadow/test_shadow_minimal_representation.py ............ 10 passed
tests/unit/test_shadow_phase11.py
tests/unit/test_shadow_hardening_chg0046.py
tests/unit/test_live_shadow_outcome_resolution.py
tests/unit/shadow/ ................................................. 87 passed
ruff check + ruff format --check ................................... clean
mypy src/nexus_scalp/shadow/store.py ............................... clean
```

The arity battery is the test that earned its keep: it asserts the INSERT
statement's placeholder count equals the column list length, and it **failed
locally first** with `Incorrect number of bindings supplied. The current
statement uses 45, and there are 52 supplied` — the columns are assembled by
`build_upsert_sql` at import, so editing one side without the other is a runtime
failure on the live tick path, not an import error. Fixed before push.

## Not measured (no fabrication)

* actual file-size delta on the production ledger after `compact_database()` —
  requires running the VACUUM against the live 435 MB file, which is an
  operator action, not a lane action;
* PostgreSQL bloat delta — no dedicated PG instance in this worktree.

## Scale

Row count does not change the per-row cost, so the saving is linear in rows and
was verified at 1/3-row fixtures locally. The 10M-row point is stated as a
linear projection, not a measurement, per the no-fabrication rule.
