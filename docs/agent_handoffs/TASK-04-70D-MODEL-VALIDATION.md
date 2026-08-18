# TASK-04 Handoff — 70D Model Generation / Fair Benchmark / Challenger Validation

> Agent: Hermes-ModelValidation-04 · TASK-04-70D-MODEL-VALIDATION · 2026-08-19
> Role: 70D Model Generation / Fair Benchmark / Challenger Validation
> Status: **BLOCKED (benchmark execution) — protocol + fairness gates + TEST suite + BUG-101 fix DELIVERED**
> Head at start: `4001e4c` · Branch: `main` · Contract: MASTER MULTI-AGENT CONTRACT v2

---

## 0. MANDATE (from the TASK-4 brief)

Answer ONE scientific question with evidence, controlling everything else:

> After controlling everything else, does the additional 10D Liquidity
> Intelligence contain reliable out-of-sample information beyond the existing
> Base + News 60D architecture?

Valid outcomes: 70D BETTER / EQUIVALENT / WORSE / INCONCLUSIVE / INVALID.
Forbidden: threshold manipulation, auto-promotion, dataset/label/split
inequality between arms, OOS tuning, promotion of a candidate merely because
it trained.

## 1. FIRST FORENSIC GATE (brief §2) — TASK-3 PARITY: NOT DELIVERED

The brief mandates proving `dataset==replay==inference`, `schema==manifest`,
`scaler==manifest`, `70D==expected` BEFORE any training. Verified on the live
tree (2026-08-19 02:45 UTC+0330, HEAD 4001e4c):

| Check | Expected | Actual | Verdict |
| :--- | :--- | :--- | :--- |
| `docs/70D_DATA_CONTRACT.md` | exists | missing | ❌ |
| `docs/agent_handoffs/TASK-03-70D-PARITY.md` | exists | missing | ❌ |
| Parity tests (70D) | exist | none | ❌ |
| `scalp_v3` = 70D | registry | `scalp_v3`=350D; `scalp_v4`=70D appeared mid-session via parallel TASK-02 | ⚠️ (TASK-02 landed, TASK-03 not) |
| 70D dataset artifact | exists | none (only 50D/60D artifacts) | ❌ |
| Liquidity engine | committed + green | UNCOMMITTED WIP, contract tests failing (liq03/liq05/liq11 at bootstrap; 12/50 per BUG-100 note) | ❌ |

**Correct scientific report per brief §2:** `MODEL TRAINING BLOCKED:
FEATURE CONTRACT NOT TRUSTWORTHY`. Benchmark EXECUTION is therefore NOT run —
training on a moving, unverified contract would produce a scientifically
invalid result.

## 2. WHAT TASK-4 DELIVERED (executable now, independent of the blocker)

### 2.1 Fair-Benchmark protocol — `docs/MODEL_BENCHMARK_70D_LIQUIDITY.md`
Full protocol: the A/B/C(+D) matrix definition, the absolute scientific rule,
dataset alignment (sample_id identity), statistical design (purged walk-forward,
effect size, no naive IID test), metrics (accuracy/macro-F1/ECE/Brier/per-class),
governance (CHALLENGER-only, no auto-promotion, Champion freeze), and the exact
continuation steps when TASK-3 lands.

### 2.2 Executable contract suite — `tests/unit/test_70d_model_validation_task4.py`
TEST-70D-MODEL-01..25 (brief §48). **18 passed / 8 skipped** today. The 8 skips
are truthful (70D schema/scaler/artifact-dependent -> TASK-3) — never fake
passes. Gates proven today:
- Fairness METHOD: same sample population / labels / splits / purge-embargo
  between comparison arms (verified on existing 50D vs 60D artifacts: 99,946
  rows each, identical populations within generation).
- Scaler dimensions 50/60 (+70 when it exists); forward-pass geometry
  60D/70D; schema-mismatch rejection; non-finite rejection; Champion hash
  freeze; no auto-promotion (INV-015); OOS split accounting; calibration
  bounds; news/liquidity family separation.

### 2.3 BUG-101 — real reproducibility defect found + fixed
`CandidateTrainer` built the model BEFORE seeding RNG → same experiment in
two fresh processes produced different results (0.3375 vs 0.375). Root cause,
evidence, minimal fix (seed before build, exactly like WalkForwardTrainer),
regression test (fresh-process identity). Details: `agents/bugs.md` BUG-101.
VERIFIED: 0.3 == 0.3 across fresh runs; ruff/mypy clean.

### 2.4 Registries (additive only)
- `agents/taskboard.md`: TASK-04-70D-MODEL-VALIDATION row, status
  WAITING_FOR_AGENT (TASK-03-70D-PARITY) + note.
- `agents/change_control.md`: CHG-0014.
- `agents/runtime_invariants.md`: INV-019 (reproducibility).
- `agents/contracts.md`: FEATURE_SCHEMA_70D row (scalp_v4, consumed).
- `agents/bugs.md`: BUG-101 appended (never rewrote existing sections).

## 3. CHAMPION SAFETY — PROVEN UNCHANGED

```text
artifact SHA-256  f0f70efb1b55855beb96ae807d81b44db07ae4d0fcff1da2965ea0a408f1d88b
scaler   SHA-256  811554e5286ea3104a9f759ccce611fb62a9994856d08b2dad82aeb6b99424e1
```
Both match `docs/task5_champion_baseline.json` (captured 2026-08-18) — re-verified
on-disk 2026-08-19. Live contract still `scalp_v1`/50D
(TEST-70D-MODEL-14/14b green). No promotion attempted; INV-015 honored.

## 4. PARALLEL-AGENT INTERACTION (observed, respected)

- Working tree is shared LIVE workspace: parallel agents (70D series TASK-02/03/05/08,
  TASK-07-70D-RESEARCH, hygiene/migrations) concurrently modified
  `features/schema.py` (scalp_v4 added), `governance/*`, `web/*`, registries.
- Their uncommitted files were NEVER touched (incl. `governance/verify.py`,
  `test_model_governance_phase16.py` TestGovernance70 — 3 failing tests there are
  THEIR in-flight work, untouched).
- My changes are isolated: `training.py` (12-line seed-order fix), new test file,
  new docs, additive registry rows.

## 5. EXACT NEXT-AGENT INSTRUCTIONS (TASK-5 — 70D SHADOW RUNTIME)

When you take over (TASK-5: 70D Shadow runtime / drift / champion-safe deployment):

1. Re-verify the chain state FIRST: `git log --oneline -10`, `git status --short`,
   then `docs/70D_DATA_CONTRACT.md` + `docs/agent_handoffs/TASK-03-70D-PARITY.md`
   MUST exist and its parity suite MUST be green.
2. If TASK-3 still hasn't landed: register SHADOW_BLOCKED_FEATURE_CONTRACT in the
   taskboard, do NOT create a fake candidate, implement only the observability
   infrastructure against a deterministic fixture (exactly what BUG-100's
   shadow70/ runtime already did) and finalize with NO_VALIDATED_CANDIDATE.
3. If TASK-3 + TASK-4 have landed (this handoff's continuation):
   a. Run `tests/unit/test_70d_model_validation_task4.py` — expect 0 fails.
   b. Execute the continuation in `docs/MODEL_BENCHMARK_70D_LIQUIDITY.md` §10
      (A/B/C(+D) benchmark with identical budgets/seeds/splits; dataset quality
      audit; liquidity distribution/redundancy audit; walk-forward fold-by-fold;
      OOS; robustness; calibration; ablation; news×liquidity interaction).
   c. Register the decision via `ValidationFactory` + research registry — one
      of STRONG/WEAK POSITIVE, NEUTRAL, NEGATIVE, INCONCLUSIVE, INVALID (never
      convert INCONCLUSIVE to SUCCESS). Candidate becomes CHALLENGER only if it
      passes every gate; NO auto-promotion; Champion stays untouched (hash
      freeze doc).
   d. The Shadow runtime then consumes the CHALLENGER (if any) — strict
      observer; nothing flows back into execution/risk (INV-002/014/018).
4. Commit discipline: agent-label commit (`Hermes-*: <imperative>`), body with
   Agent/Role/Scope/Why/Implementation/Verification/Risk/Handoff, regression
   tests in the same commit, full Telegram report in Persian with SHA+files+
   push result+remote verification.
5. If any parity gate fails at ANY point: stop, record BUG-NNN with evidence,
   and report `FEATURE CONTRACT NOT TRUSTWORTHY` rather than proceeding.

## 6. FILES CHANGED (this task)

| File | Change |
| :--- | :--- |
| `src/nexus_scalp/model_generation/training.py` | BUG-101: seed BEFORE model build (12 lines) |
| `tests/unit/test_70d_model_validation_task4.py` | NEW — TEST-70D-MODEL-01..25 |
| `docs/MODEL_BENCHMARK_70D_LIQUIDITY.md` | NEW — fair benchmark protocol + blocker evidence |
| `docs/agent_handoffs/TASK-04-70D-MODEL-VALIDATION.md` | NEW — this handoff |
| `agents/bugs.md` | BUG-101 appended |
| `agents/taskboard.md` | TASK-04 row + note |
| `agents/change_control.md` | CHG-0014 |
| `agents/runtime_invariants.md` | INV-019 |
| `agents/contracts.md` | FEATURE_SCHEMA_70D row |

## 7. RISKS / UNFINISHED

- UNFINISHED (by design): the actual A/B/C training — blocked on TASK-03 parity.
- REMAINING when unblocked: dataset quality audit, liquidity distribution/
  redundancy audit, walk-forward fold-by-fold, OOS, robustness, calibration,
  ablation, news×liquidity interaction, research registry row, final verdict
  table in MODEL_BENCHMARK_70D_LIQUIDITY.md §31, full `beforePush.ps1`.
- Risk: parallel agents continue editing shared files (registries, schema.py);
  re-check tails before each registry write (contract §4).