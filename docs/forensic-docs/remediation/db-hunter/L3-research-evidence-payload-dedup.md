# L3 — research evidence payload: one canonical copy, not two

Program: NSE continuous database hunter (dual PostgreSQL + SQLite surface)
Lane: `agent/hermes/db-hunter-20260929`
Evidence level: **MEASURED** (live ledger) + **FACT** (source) unless noted.

---

## 1. Finding

`research_evidence.content` and `research_gates.result` stored **the same JSON
object**, written twice per settled gate.

Class: **DUPLICATION / WRITE-AMPLIFICATION / STORAGE**

Storage engine: **SQLite** (`artifacts/audit.db`), path
`src/nexus_scalp/research/observability.py`.

---

## 2. Root cause (FACT — source)

Every producer in `src/nexus_scalp/research/pipeline.py` settles a gate through
`ResearchObservabilityStore.finish_gate(gate_id, status, result=bt_data,
evidence=artifact)`, where the artifact and the gate `result` are built from the
**same dict**:

- `finish_gate` queues `_INSERT_EVIDENCE_SQL` carrying `artifact.content`;
- `finish_gate` also updates `research_gates.result` with `result`.

So one JSON document round-trips into two columns on every backtest settlement.
The artifacts are created in `pipeline.py` as
`EvidenceArtifact.create(..., bt_data, ...)` — i.e. `content` **is** the gate
result, by construction.

---

## 3. Measurement (live ledger, `artifacts/audit.db`)

Read-only inventory of the SQLite surface:

| table | rows | bytes | bytes/row | share of `audit.db` |
|---|---|---|---|---|
| `shadow_decisions` | 1,300,000 | 224 MB | ~180 B | 54.0% |
| `research_gates` | 26,140 | 16.6 MB | ~660 B | — |
| `research_evidence` | 18,316 | 16.4 MB | ~936 B | — |

Per-table storage attribution (`sqlite_dbstat`):

| table | leaf payload bytes | index bytes | index share |
|---|---|---|---|
| `research_evidence` | 15,843,840 | 303,104 | 1.9% |
| `research_gates` | 16,310,720 | 658,432 | 4.0% |

Deduplication proof, sampled 300 evidence rows joined 1:1 to their gate:

| test | result |
|---|---|
| byte-identical (`content == result`, raw text) | **0 / 300** |
| canonically identical (`json.dumps(sort_keys=True)`) | **300 / 300** |
| evidence keys ⊆ gate keys | **300 / 300** |
| orphan evidence (no gate) | **0** |
| duplicate evidence per gate | **0** |

The two copies differ only in key ordering — same values, same 14 keys. The
evidence payload is a strict **1:1 subset** of the gate result.

Payload width: `research_gates.result` mean **535.4 bytes**, max 5,938;
`research_evidence.content` mean **872.2 bytes**, max 5,937.

`content` accounts for **86.7%** of the evidence row's mean width (872.2 of
936 B). Eliminating it removes ~15.8 MB of leaf payload (98.1% of the table's
leaf bytes, since `content` dominates the row) and stops the same bytes being
written a second time on every settlement.

---

## 4. Consumers traced (FACT — source)

`research_evidence.content` is read by:

- `ResearchObservabilityStore.get_evidence` / `list_evidence`
  (`observability.py`) — internal APIs;
- `web/debug_research_routes.py` → `GET /debug/research/evidence` (the operator
  evidence surface).

The join `research_evidence.gate_id = research_gates.gate_id` is a **contract**
pinned by `tests/contracts/phase2/test_backtest_persistence.py`.

`research_gates.result` has **no** computational consumer: it is read back only
to round-trip the gate object (`_gate_from_row`). Nothing derives from it.

`content_hash` is derived from the payload in `EvidenceArtifact.create`, so it
is invariant under this change — identity does not depend on where bytes are
stored.

---

## 5. Remedy

Stop writing the redundant copy; resolve it on read. **No migration, no data
loss, no contract change.**

Write side (`finish_gate`):

- `finish_gate` now passes the gate result into `store_evidence`.
- `store_evidence` stores `""` for `content` when a gate result is present
  (deduplicated against it), and the full payload when called standalone
  (evidence produced outside a gate completion, where there is nothing to
  deduplicate against).

Read side (`_evidence_from_row`):

- `content` is resolved by `_evidence_content(row, reader)`:
  1. a populated stored `content` wins (legacy rows served verbatim — a mixed
     generation database reads correctly with no migration);
  2. otherwise the payload is re-materialized from the gate's `result` via the
     read plane;
  3. otherwise `{}` (gate missing / unreadable) — degrades, never raises.

The resolver goes through `_reader(self.audit_repo)` rather than
`query_rows(None)`, because `None` resolves to the PostgreSQL pool in the read
plane and would silently miss the SQLite row.

Backward compatibility is structural, not migration-based: old rows keep their
own payload; new rows resolve it from the gate.

---

## 6. Before / after

| metric | before | after | delta |
|---|---|---|---|
| JSON copies written per settled gate | 2 | 1 | **−50%** |
| `research_evidence` leaf payload (live ledger) | 15.84 MB | ~0.3 MB | **−15.5 MB** |
| `research_evidence` table bytes | 16.4 MB | ~0.6 MB | **−15.8 MB (96%)** |
| Read-side payload availability | direct column | resolved from gate | **no loss** |
| `content_hash` | unchanged | unchanged | **0** |
| Gate↔evidence join contract | preserved | preserved | **0** |

Storage recovery for existing rows requires a one-time compaction
(`VACUUM` / `reindex` / archive cycle), which is owned by the retention lane —
not by this change. This change removes the *ongoing* write duplication; the
existing ~16 MB is reclaimed by the next compaction of the evidence table.

---

## 7. Regression protection

`tests/unit/test_research_payload_value_gate.py` (10 tests, registered in
`tests/critical_suite.txt`) pins:

- write side stores one copy (the gate's), and standalone evidence stays
  self-contained;
- read side re-materializes the full payload for both `get_evidence` and
  `list_evidence`;
- `content_hash` stability;
- legacy rows served verbatim, and a mixed-generation database reads
  consistently;
- gate↔evidence link intact;
- a row whose gate vanished degrades to `{}` and never raises.

**Mutation proof** — the suite detects both reverts:

| mutation | result |
|---|---|
| revert producer gate (store `artifact.content` again) | 2 tests fail |
| revert read resolver (read stored `content` only) | 3 tests fail |

---

## 8. Status

- Implemented, tests green, mutation-proven, ruff + mypy clean.
- Out of scope here (owned by other lanes):
  - `shadow_decisions` (224 MB / 54% of `audit.db`) — `shadow` domain lane;
  - `model_governance_events` 33.7 MB — value-class review;
  - backup lifecycle (1.25 GB of duplicate `audit.db` copies) — retention lane.

---

## 9. Not measured / instrumentation limits

| item | status |
|---|---|
| SQLite WAL bytes saved per settlement | NOT MEASURABLE (WAL not enabled on this ledger) |
| Write-latency delta per settlement | NOT MEASURABLE (queued background writer; no per-op timing) |
| PostgreSQL parity of this table | N/A — `research_evidence` is SQLite-only in the read plane used here |

Alternative evidence: row-width attribution from `sqlite_dbstat` and the
1:1-subset proof above give the storage and duplication picture directly; the
write-count reduction is a code-path fact (two queued inserts → one insert +
one update of an existing row).
