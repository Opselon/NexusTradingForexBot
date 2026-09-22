# DEC-0010 — ScalpNet 3-Class Head Contract: legacy 4-logit WAIT retention (Option B)

**Status:** Proposed — awaiting operator decision (ML-ARCH-001)
**Date:** 2026-09-22
**Decided by:** AGENT-ML-ARCH (evidence-gathering only; no code changed)
**Supersedes:** none
**Blocks:** ML-ARCH-002, ML-ARCH-003, ML-EXP-002

---

## 1. Context

The NSE class contract (`src/nexus_scalp/model_lifecycle/model_class_contract.py:50-61`)
declares **3 trained classes** — `NO_TRADE / BUY_MARKET / SELL_MARKET` — while the
ScalpNet output head may be constructed **4-wide**, with logit index 3 (`WAIT`) kept
alive as a legacy "policy bridge" that is masked to `-1e4` before softmax
(`model_class_contract.py:101-121`).

ML-ARCH-001 asks the operator to choose between:

- **Option A** — immediate removal of the 4th logit (hard 3-class everywhere)
- **Option B** — strict sunset with a legacy backwards-compatibility adapter

This record supplies the evidence the decision requires and a **recommendation**.
Per the task's `HUMAN_DECISION_REQUIRED: YES` and its `NON_GOALS`
("Do not delete 4-logit support before operator approval"), **no production code was
modified to produce this record.**

---

## 2. Evidence — gathered live at `origin/main` `eb73440a`

### 2.1 No 4-logit artifact can reach this repository

- **No `.pt` checkpoint is committed to git.** `git ls-tree -r HEAD --name-only`
  returns zero `.pt` paths. Clean-checkout training is impossible by design
  (matches the established "clean git checkout cannot train production models" fact).
- `find artifacts -name "*.pt"` → **0 results**.
- `~/.nexusscalpengine/models/` exists but is **empty** (`ls -la`, 0 entries).
- **No official model has been published.** `official-model-stable/manifest.json`
  has not been rewritten by the publication workflow; clients resolve to
  `OFFICIAL_MODEL_NOT_PUBLISHED`.

**Consequence:** the "UNKNOWN" ML-ARCH-001 flagged — *"whether any production client
still depends on 4-wide checkpoint loading"* — resolves to **no in-repo dependency exists**.
The 4-wide surface is a *compatibility contract*, not a load-bearing artifact.

### 2.2 The live serving path is already 3-class-first, with one legacy gate

`LiveEngine._declared_head_classes_for_path` (`live_engine.py:2448-2465`) reads the
bundle's `model.meta.json` and returns `model_head_classes`/`num_classes` **only when
the value is `3` or `4`**, else falls back to `TRAINED_CLASS_COUNT` (3). So:

- a meta declaring **3** → 3-wide model, **no masking overhead at all**
  (`mask_wait_logit` is a no-op on 3-wide input, `model_class_contract.py:114`);
- a meta declaring **4** → 4-wide model, WAIT masked per-tick
  (`inference.py:306-315`, `masked_softmax`).

That second branch is **dead weight today** — no artifact in this repo, and no
published official bundle, declares 4. The mask runs on every tick for a geometry
that no served bundle uses.

### 2.3 Remaining 4-wide literals in production code — exactly 2

A repo-wide scan (`grep -rn 'num_classes\s*=\s*4' src/`) excluding comments finds:

| Site | Path | Verdict |
|---|---|---|
| 1 | `src/nexus_scalp/smoke/runner.py:731` — `ScalpNet(num_features=50, num_classes=4)` | **Test-only.** Inside `_layer_l2_integration`, a synthetic paper-smoke forward; asserts `logits.shape == (1,4)`. Not a production serving path. |
| 2 | `src/nexus_scalp/web/model_governance_routes.py:515` — `num_classes=4` on a `Shadow70CandidateContract` | **Shadow-only.** Feeds `shadow70` challenger observability, which is explicitly *not* wired into `live_engine`. Its field defaults to 4 (`shadow70/models.py:131`). |

Zero 4-wide literals remain on the **production tick path**. Both survivors are
non-serving (smoke harness, shadow challenger).

### 2.4 The dual-branch cost is real but small

`mask_wait_logit` (`model_class_contract.py:101-121`) runs on **every live forward
pass** via `masked_softmax`. It is a cheap clone + scalar assignment, but it is
unconditional dead work whenever the served head is 3-wide, and
`model_class_contract.py:137-138` / `:161-172` carry 4-wide slices in
`trained_class_probs` / `masked_ce_loss` for the same reason.

### 2.5 Blast radius of Option A (measured, not guessed)

`grep -rn 'num_classes\s*=\s*4' tests/` hits **66 occurrences across 29 test files**.
Most are *intentional probes* — e.g. `test_bug243_bundle_class_head_mint.py` asserts
the mint honors a meta-declared 4-head; `test_promotion_rejects_degenerate_model.py`
and `test_bug225_untrained_champion_canary.py` construct 4-wide degenerates to prove
gates reject them. Removing the 4-wide constructor capability would **delete the
negative-test coverage that currently proves the serving path is safe**, and would
break the `allow_legacy_4` integrity probes (`model_lifecycle/integrity.py:33-38`).

---

## 3. Recommendation — Option B (strict sunset, legacy adapter retained)

Reasoning, in priority order:

1. **Zero in-repo 4-wide artifacts** (§2.1) means removal buys no real safety — the
   risk Option A protects against does not exist in a repo with no committed weights.
2. **Option A destroys proven negative tests** (§2.5). The 4-wide probes are the
   evidence that gates reject incoherent bundles. Deleting the capability deletes
   the coverage. Option B keeps them as regression fences *and* makes them assert
   the deprecation warning.
3. **The operator's stated priority is client install + full automation**, and the
   official-model publication path is **not yet live**. Retaining legacy-4 read
   compatibility keeps any future operator-supplied 4-wide bundle loadable instead
   of hard-failing at a client site — the cheaper failure mode while publication
   is unbuilt.
4. The only production-path saving from Option A is eliminating one
   `mask_wait_logit` no-op per tick — negligible vs. the ~60ms forward it sits inside
   (`inference.py:295-298`), and achievable under Option B by routing 3-wide
   meta bundles straight to the 3-class constructor (already the default at
   `live_engine.py:2463-2464`).

### 3.1 What Option B means concretely (for the operator's yes/no)

- **3-class stays the SSoT for all new training** — already true
  (`TRAINED_CLASS_COUNT = 3`, fresh mints default to 3).
- **4-wide remains constructible and loadable, but emits a `DeprecationWarning`** at
  construction (`scalp_net.py:113-138`) and at legacy artifact load
  (`integrity.py` `allow_legacy_4=True` path).
- **The 3 non-critical 4-wide literals stay as compatibility probes**, with tests
  asserting the warning fires and masking still neutralizes WAIT.
- **Sunset is *declared*, not executed**: a `SHADOW_WAIT_LOGIT_SUNSET` marker in
  `model_class_contract.py` with a target version, so a future operator can flip it.

### 3.2 Expected implementation surface (post-approval)

- `src/nexus_scalp/models/scalp_net.py` — `DeprecationWarning` on `num_classes=4`
- `src/nexus_scalp/model_lifecycle/integrity.py` — warning on `allow_legacy_4=True`
- `tests/unit/test_model_class_contract.py` — assert warning + masking invariant
- **No change to `live_engine.py`, `inference.py`, or any serving path.**

---

## 4. Operator decision required

```
Option A — remove the 4th logit entirely (breaks 29 test files' negative coverage)
Option B — strict sunset + deprecation warnings   ← RECOMMENDED by AGENT-ML-ARCH
Option C — reject sunset; retain current masking indefinitely
```

Acceptance criteria from ML-ARCH-001 that **only the operator can satisfy**:
`ACCEPTANCE_CRITERIA #1 — Operator decision recorded.` This record is that vehicle;
criteria #2 (fresh checkpoints emit `(B,3)`) and #3 (legacy via adapter) are
**already satisfied today** (§2.2) and need no further work under Option B.

---

## 5. References

- Task: `docs/ml-system/tasks/ML-ARCH-001.md`
- Contract: `src/nexus_scalp/model_lifecycle/model_class_contract.py:50-172`
- Serving head resolution: `src/nexus_scalp/application/live_engine.py:2448-2465`
- Tick-path mask: `src/nexus_scalp/application/live/inference.py:306-315`
- Integrity probes: `src/nexus_scalp/model_lifecycle/integrity.py:33-38`
- Blocks: `ML-ARCH-002` (TCN dilation), `ML-ARCH-003` (attention ablation),
  `ML-EXP-002` (architectural ablation harness)
