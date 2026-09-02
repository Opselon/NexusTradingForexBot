# src/nexus_scalp/features/features70.py

- **PURPOSE:** The 70D *assembly/validation* helpers (`assemble_70d`,
  `news_10d_from_context`, `news_context_hash_10`, `clamp_neutral_family`)
  that turn the three family vectors (50D base, 10D news, 10D liquidity)
  into ONE validated 70D vector / `Feature70Snapshot`, with schema-hash
  and per-family provenance. The runtime counterpart of schema_contract.
- **ARCHITECTURE LAYER:** Features (contract enforcement at the assembly
  boundary).
- **RESPONSIBILITY:** (a) `Feature70Snapshot` — frozen snapshot with
  `vector` (the assembled 70D), `as_dict()`, `validate()`, `schema_hash()`;
  (b) `assemble_70d` — the strict concatenation with dimension checks and
  family-state handling; (c) `news_10d_from_context` — exact 10-field
  selection from the canonical 12-field news context dict (fields 0..8 +
  news_state idx 10, same non-blind selection as schema_contract);
  (d) `news_context_hash_10` — content hash over the 10 selected fields
  (change detection across train/live); (e) `clamp_neutral_family` — clamp
  a family block toward its neutral values when the family is unavailable
  (e.g. liquidity disabled) — the sanctioned "neutral, not zero" degradation.
- **DEPENDENCIES:** `schema_contract` (names/hash/geometry),
  `features.liquidity_engine` vector, news context schema, pydantic/dataclass
  machinery, logging.
- **CONNECTS TO:** `runtime70` (uses these to build snapshots), shadow70
  runtime, live 70D inference path, dataset builders (train-time assembly
  must match runtime assembly EXACTLY — parity invariant), tests
  (test_70d_parity_task3, test_schema_70d_reconciliation).
- **KEY CONCEPTS:**
  - Strictness: `assemble_70d` validates each family block's arity before
    concatenation — a 51-wide base or 9-wide news block raises, it never
    truncates/pads.
  - The news selection mirrors schema_contract EXACTLY (indices 0..8 + 10):
    two implementations of the same rule, both guarded by the schema-hash
    equality assertion — drift anywhere is caught.
  - `clamp_neutral_family` is the honest degradation path: an unavailable
    family becomes its neutral vector (e.g. liquidity 10D neutral values),
    NOT zeros — a zero family block would read as "extreme bearish/no
    liquidity at all" to the model. Which values are "neutral" is explicit
    per family.
- **EDGE CASES & PITFALLS:** The snapshot `validate()` must be called before
  ANY consumer uses the vector (Debug Hub shows NOT_EXPOSED when the stage
  wasn't computed — never fake 0); hash consistency between train and live
  is THE 70D parity gate — any hash mismatch is a contract break, not a
  warning.