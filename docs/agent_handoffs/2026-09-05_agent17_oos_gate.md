# AGENT 17 — OOS GATE / TEMPORAL HOLDOUT / FORWARD TEST — FINAL HANDOFF (2026-09-05)

Agent: Agent 17 (Nexus-Main orchestrated)
Role: Research Validation / OOS Gate / Temporal Holdout / Forward-Test Forensics
Task: TASK-AGENT17-OOS-GATE (CHG-0063)
Branch: main (origin/main)
Starting HEAD: d0a9b6d4 (at mission start; swarm advanced it repeatedly mid-mission)
Ending HEAD (my last push): d8ed39ad (390e153d + d8ed39ad are MINE; later commits are other agents')

## VERDICT

VERIFIED WITH RESIDUAL RISKS.

The OOS hard gate chain (split_temporal -> OOSGate -> scoring -> registry ->
governance) is proven sound by adversarial execution, with ONE confirmed
defect (BUG-245, empty context-contract silent widening) FIXED and
regression-tested. Model-lane floors (macro-F1 > 0.34, bacc > 0.34,
ECE <= 0.15, N >= 100) verified at exact runtime semantics. MT5 UTC
semantics verified against the adapter's documented broker-timebase
contract (BUG-188 convention). Real bounded OOS + forward-test freeze
executed and repeatable.

## ROOT CAUSES

RC-1 (FIXED, BUG-245, HIGH): OOSGate.evaluate empty-contract silent
widening — direct callers could PASS on the wrong population. Commit
390e153d.

RC-2 (ADVISORY, NOT A DEFECT): min-evidence (N>=100) is NOT enforced inside
OOSGate itself; it IS enforced end-to-end on the production path
(pipeline -> compute_strategy_score verdict INCONCLUSIVE -> REJECTED, and
ValidationFactory min_evidence gate on the model lane). Only direct
gate-level callers see un-floored sample counts; documented, no code
change (defense-in-depth option left for the owner).

RC-3 (FOREIGN, ROUTED): src/nexus_scalp/research/streaming_replay.py
currently fails import with "TypeError: Cannot overwrite attribute
__setattr__ in class _PendingLimit" — a duplicated
@dataclass(frozen=True, slots=True) decorator from the parallel
Agent-18/Agent-14 lanes (CHG-0062/0061 working tree). NOT fixed by me
(foreign seam); blocks only direct in-process imports of
ForwardTestExperiment — the freeze behavior itself is covered green by
tests/unit/test_forward_test_freeze.py (9 tests).

## WHAT WAS VERIFIED (EXECUTABLE EVIDENCE, NOT DOCS)

1. Temporal split (splitting.py): determinism, disjointness, strict
   train<val<oos chronology, purge of boundary-crossing horizons,
   embargo on val and OOS boundaries. 12/12 checks.
2. OOSGate: zero-OOS FAIL, negative-OOS FAIL, degradation ceiling,
   NaN-R exclusion, valid-scope PASS preserved. Empty-contract FAIL
   after fix (red-before/green-after repro).
3. ValidationFactory (model lane): strict >0.34 macro-F1, strict >0.34
   bacc, ECE floor 0.15 (inclusive <=), min_evidence 100 (99 FAIL,
   100/101 pass the N-gate). No internal split — caller owns the OOS
   slice (correct design; NOT a substitute for the research OOSGate).
4. Registry: _is_stronger blocks REJECTED<->VALIDATED peer truth
   rewrites; invariant_check demands OOS PASS for VALIDATED.
5. Governance: verify_candidate fail-closed on missing evidence
   (INSUFFICIENT_EVIDENCE, never green), smoke quarantine blocks even
   smoke+production_eligible=True, NaN expectancy FAILs gate_oos, no
   production caller passes force=True.
6. Preprocessing isolation: WalkForwardTrainer fits the scaler on the
   fold-train slice only (X_train_raw); appending 60 future bars
   changes ZERO of 70x346 historical feature values
   (schema_v2.compute_70d_frame, real XAUUSD_M5 parquet).
7. MT5 semantics: broker-epoch (server GMT+3) -> UTC conversion
   verified; copy_ticks_range input window shifted +180min per BUG-188
   (symmetric with output conversion); copy_rates_range surface probed;
   dataset fingerprint deterministic + one-byte-sensitive; dataset
   read path is offline/cache-only (no MetaTrader5 import).
8. REAL execution: bounded OOS over 600 real M1 bars — OOS gate ran,
   metrics computed only from the temporal OOS slice, two runs
   bit-identical (status, oos_samples, oos_expectancy_r).
9. Forward test: cutoff equality is STRICT (events at timestamp ==
   cutoff excluded; naive datetimes coerced to UTC); freeze capture
   persists frozen model/scaler bytes + identity digest; freeze
   verified AFTER run (FREEZE_DRIFTED on tamper, suite-pinned);
   repeatability of freeze digest + run (test_forward_test_freeze
   9/9 GREEN).

## FILES CHANGED (mine)

- src/nexus_scalp/research/oos.py — BUG-245 fix (fail-closed
  CONTEXT_CONTRACT_EMPTY_POPULATION on empty contract match).
- tests/unit/test_agent17_oos_hard_gate.py — NEW, 9 regression tests.
- agents/bugs.md — BUG-245 row appended.
- agents/taskboard.md — mission row + completion row.
- agents/change_control.md — CHG-0063 entry.
- docs/agent_handoffs/2026-09-05_agent17_oos_gate.md — this handoff.

## COMMITS

- 390e153d — OOSGate empty-context silent-widening fix + regression suite.
- d8ed39ad — ruff format follow-up (B905 zip strict).
(Registry commits ad179530/372d4469 were pushed earlier from main and
survived as ancestors; current main contains them.)

## QUALITY GATE

- ruff check: clean on both files.
- ruff format: clean after d8ed39ad.
- py_compile: clean.
- Focused suites: 68 passed (agent17 + phase09b + purge_defaults_bug183
  + forward_test_freeze + bug233 short-circuit).
- Full beforePush NOT run this session (swarm was landing parallel
  foreign-WIP commits every few minutes; running the full gate would
  have graded their in-flight work, not mine). Scope-isolated gates all
  green as listed.

## KNOWN RISKS / UNRESOLVED

1. streaming_replay.py import crash (FOREIGN — owner must drop the
   duplicated decorator; routed via taskboard + this handoff).
2. Model-lane "oos_*" naming in ValidationFactory can mislead future
   agents into believing it performs a temporal split — it does not;
   the real temporal OOS is the research-lane OOSGate. Documented here;
   optional rename later (shared-surface change, needs owner review).
3. min-evidence floor at the OOSGate level (defense-in-depth) left to
   the owner; production path already enforces N>=100 end-to-end.
4. OOS-floor thresholds live in two lanes (research expectancy-R vs
   model-lane class floors). Both verified; keep them distinct.

## EXACT NEXT-AGENT INSTRUCTIONS

1. Owner of streaming_replay.py (Agent 18/14 lane): remove the
   duplicated @dataclass(frozen=True, slots=True) line above
   _PendingLimit; run tests/unit/test_forward_test_freeze.py +
   test_streaming_replay*.py.
2. Next OOS work MUST reuse OOSGate + split_temporal (never re-derive
   splits) and must treat ValidationFactory as the model-quality lane,
   not a holdout mechanism.
3. If a candidate's context contract matches 0 samples, the gate now
   FAILs by design — do not "fix" this by widening the population;
   widen the DECLARED contract instead.

— Agent 17, 2026-09-05
