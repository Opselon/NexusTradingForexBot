# DEEP-OPT L4 — the evidence vault must not mirror the gate outcome

The RESCAN after L1/L2/L3. This is the same defect class as the #568 shadow
mirror, in a second store: a derived representation silently inherited the
complete payload of its source.

## What was measured (not estimated)

Read-only probe against the live ledger (`artifacts/audit.db`, 436.7 MB):

| metric | value |
|---|---|
| `research_evidence.content` total | **17,173,678 B** across 18,316 rows |
| rows whose content is semantic-identical to `research_gates.result` | **18,316 of 18,316 (100%)** |
| rows whose content genuinely differs from the gate outcome | **0** |
| rows with no resolvable gate | **0** |
| `research_gates.result` total (the canonical copy) | 18,988,698 B |
| duplicated bytes | **17,173,678 B (100% of the evidence column)** |

Comparison method: joined `research_evidence` to `research_gates` on `gate_id`
and compared the two JSON documents after canonicalization
(`json.dumps(..., sort_keys=True, separators=(",", ":"))`). Raw byte
comparison reported 0 identical because the two writers serialize with
different key orderings — the duplication is **semantic**, not byte-wise, which
is exactly why it survived four prior audits that grepped for literal
duplicates.

Per gate type, the same 100% duplication holds: BACKTEST 4,315, WALK_FORWARD
4,313, OOS 4,313, ROBUSTNESS 4,225, SCORING 1,150.

The `strategy_registry.backtest` column holds a **third** copy of the backtest
document (8.33 MB). It is not identical to the gate outcome (1,064 of 4,107
registry rows match semantically; the rest carry a different mutation of the
same document), so it is out of scope for this fix and recorded below.

## Root cause — why the copy existed

`StrategyValidationPipeline._run_gates` (src/nexus_scalp/research/pipeline.py)
settled each gate by passing the **same dict object** to both parameters:

```python
obs.finish_gate(
    gate.gate_id,
    status=GateStatus.PASSED,
    result=bt_data,  # <- canonical outcome
    evidence=EvidenceArtifact.create(  # <- derives content from bt_data
        sid,
        run_id,
        EvidenceKind.BACKTEST_RESULT,
        bt_data,  # <- the SAME dict
        gate_id=gate.gate_id,
    ),
)
```

`finish_gate` stores `result` into `research_gates.result` **and** calls
`store_evidence`, which serialized `artifact.content` — the same document —
into `research_evidence.content`. This is contract Part 5 rule 8 verbatim
(*"do not automatically store ... duplicated model payloads"*).

It was never a storage decision, and it was never needed by a consumer:

* `research_evidence` is an **immutable identity + provenance vault**
  (evidence_id, kind, content_hash, dataset_version, lineage);
* the **only** readers of the body are `/api/research/evidence` and
  `/api/research/trace`, both of which serve it to the debug console, where
  the exact same document is already reachable through the gate row
  (`/api/research/gates` → `result`), joined one hop away on `gate_id`;
* no model training, no replay, no analytics, no SSE feed and no operator
  script reads `research_evidence.content`. (Verified by trace, see below.)

Producer path traced in full (§3):

```
BacktestEngine.run()
  -> bt.model_dump(mode="json")          [pipeline.py:443]
  -> bt_data + context_contract_hash
  -> finish_gate(result=bt_data, evidence=EvidenceArtifact.create(..., bt_data))
     -> _UPDATE_GATE_RESULT_SQL          -> research_gates.result      [CANONICAL]
     -> store_evidence()
        -> _INSERT_EVIDENCE_SQL          -> research_evidence.content  [DUPLICATE]
  -> registry.upsert(entry.backtest=bt)  -> strategy_registry.backtest [3rd copy]
  -> archive.move_to_archive()           -> research_evidence_archive  [4th copy]
```

Consumer path traced in full (§4) — "nobody needs the second copy" is proven by
code, not asserted:

| consumer | reads | needs the duplicated body? |
|---|---|---|
| `/api/research/evidence` (debug_research_routes) | `obs.list_evidence` → `.content` | no — the same document is served by `/api/research/gates` (`result`) one join away |
| `/api/research/trace` | `obs.get_evidence` → `.content` | no — same document, same join |
| `event_projection.evidence_completeness` | `strategy_registry.{backtest,walkforward,oos,robustness,score}` | no — reads the REGISTRY, never the evidence body |
| registry invariant check (`registry.py:249`) | `entry.backtest` | no — reads the registry copy |
| `evidence.content_hash` integrity checks | the hash, not the body | no — the hash is preserved by this change |
| model training / replay / analytics | — | **no consumer found** |

## The change — fix the producer (rule 5 / 90 / 55)

`ResearchObservabilityStore.store_evidence` no longer serializes the artifact
body. The evidence row stores identity + provenance + the content hash, and the
body is **derived on read from the canonical gate outcome** through one indexed
lookup on `gate_id` (`idx_gates_strategy` / the PK), never a scan.

* LOSSLESS: `get_evidence` / `list_evidence` return the same dict they returned
  before, including the full `content`, because the derivation resolves it from
  the gate.
* The artifact's own `content_hash` is kept as the **integrity witness**: if the
  gate outcome ever disagrees with the hash recorded when the evidence was
  created, the reader logs the divergence instead of silently returning either
  copy (observable + fail-safe, §9).
* The sentinel `__derived_from_gate_result__` is JSON-inert — it can never
  collide with a genuine evidence body, which is always a serialized object.
* Reads go through the provider read plane (`_ProviderRead`), so the derived
  read works on SQLite **and** PostgreSQL (PG-RESEARCH-READ-001). No raw
  sqlite3 path was introduced.

## Rollback and compatibility (rule 86)

* `research_evidence.content` is **retired, not dropped**. A SQLite `DROP
  COLUMN` rewrites every row — write amplification the contract forbids
  (rule 40/41). The reclaim is explicit, measured and idempotent.
* Legacy rows carrying a real body are returned **untouched** —
  `_evidence_from_row` only derives when it sees the sentinel, so an
  un-converted database reads exactly as before.
* `scripts/audit/deep_opt_l4_evidence_body.py` converts the existing 18,316
  rows: keyset cursor on the integer PK (never OFFSET), bounded by
  `--max-rows`, dry-run capable, idempotent and `--revert` capable. It only
  converts rows whose stored body is semantic-identical to the gate outcome;
  anything that genuinely differs is LEFT ALONE and reported (0 such rows on
  the live ledger).
* The archive table keeps its own copy until an operator moves it; nothing is
  deleted automatically.

## Verification actually executed

```
tests/contracts/phase2/test_deep_opt_l4_evidence_body.py ... 6 passed
tests/contracts/phase2/ (full suite incl. PG throwaway server) . 171 passed
tests/unit/test_research_observability_phase21.py ............. included above
backfill --dry-run --max-rows 0 against the LIVE ledger .... 18,316/18,316 duplicate
mutation control: restoring the old mirror ................ 2 tests FAIL
```

The battery is mutation-proven: re-introducing `_json(artifact.content)` in
`store_evidence` fails
`test_evidence_row_carries_the_derivation_sentinel` and
`test_duplicate_event_retry_creates_one_body`. It also covers the large-payload
case (a 900-point equity curve produces exactly ONE stored copy), the legacy
read path (a pre-fix row with no `gate_id` keeps its body verbatim) and the
retry path (re-settling a gate three times writes the sentinel once).

PostgreSQL was exercised by the phase2 contract suite's throwaway server
(`Phase2PgEnv`), which writes only to a scratch database on port 55433 — never
the operator's cluster.

## Expected effect

| metric | before | after |
|---|---|---|
| evidence bytes written per settled gate | ~936 avg (up to 192,714) | **0** (a 33-byte sentinel) |
| duplicated bytes on the existing 18,316 rows | 17,173,678 | **0** |
| read cost per evidence row | 1 row read | 1 row read + 1 indexed gate lookup |
| readers that must change | — | none (API contract unchanged) |

## NOT measured (no fabrication)

* the VACUUM file-size delta on the live 436.7 MB ledger — the reclaim script
  is an operator action, and the file does not shrink until VACUUM runs;
* PostgreSQL bloat/VACUUM delta — no dedicated PG instance holds this table;
* WAL bytes per batch — SQLite WAL growth is not observable from a read-only
  connection;
* the 10M-row scale point — per-row cost is constant (one indexed lookup), so
  the saving is linear in rows, stated as a projection, not a measurement.

## Next redundant representation (§63 — the RESCAN continues)

`strategy_registry.backtest` is a **third** copy of the same backtest document
(8.33 MB, avg 2,026 B, max 192,654 B), written by `registry.upsert` from the
same `bt` object. Unlike the evidence copy it is NOT identical to the gate
outcome (1,064 of 4,107 rows match semantically), so it needs its own
producer/consumer trace before changing: `event_projection` and the registry
invariant check both read the registry copy as canonical, and the debug console
serves it directly. Recorded here, not fixed here.
