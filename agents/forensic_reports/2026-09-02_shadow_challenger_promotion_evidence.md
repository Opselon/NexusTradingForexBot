# SHADOW CHALLENGER VALIDATION / PROMOTION-EVIDENCE REPORT (CHG-0047)

Date: 2026-09-02 · Agent: Hermes-Main · Scope: Shadow subsystem only
Companion artifact: `artifacts/forensics/shadow_challenger_evidence_*.json`
(reproducibility pair; see Fingerprints below)

## 1. Pipeline proven end-to-end

```
VALIDATED CHALLENGER (70d_news, registry lifecycle=CHALLENGER)
  → SHADOW ATTACH        (shadow70 load gate semantics; Champion untouched)
  → IDENTICAL INPUTS     (same frozen M1 bar window through two deterministic
                          StreamingReplayEngine sessions; pairs joined on
                          timestamp+decision_index, INPUT_MISMATCH rejected)
  → CHAMPION vs CHALLENGER INFERENCE (model argmax level + policy level)
  → PAIRED EXECUTION OUTCOMES (side-aware resolver: BUY@ASK/exit-BID,
                          SELL@BID/exit-ASK, flat=0.0, walk-end honest R)
  → ΔR / AGREEMENT / REGIME / SESSION BREAKDOWN (persisted per pair)
  → EVIDENCE ARTIFACT    (artifacts/forensics/, self-identifying)
  → PROMOTION-READINESS VERDICT (INSUFFICIENT_EVIDENCE | SUPPORTED | REJECTED)
```

## 2. Candidate/challenger selection (steer §1 — smallest legal set)

| Role | Model | Artifact | SHA-256 (32) | Lifecycle | Width |
|---|---|---|---|---|---|
| CHAMPION (untouched) | primary_scalp_scalp_v3_70d v1.0 | 70d_liquidity/model.pt | 763a25f61fe6b7d35da79fc3d2432b5f | CHAMPION (registry id 3546/3741) | 70 |
| CHALLENGER | scalp_70d_news_scalp_v3_70d v1.0.0 | 70d_news/model.pt | 2b98f333cf1e3f77c485f6e1d0dacde7 | CHALLENGER (registry id 3103/3104) | 70 |

Both bundles load STRICT (ScalpNet state_dict, BUG-125 width probe). Both
share the canonical scalp_v3 contract (hash 235b8fccc96b7e0e) and scaler
e82079b4885b47685353aa7a67bdc007. Weights differ (max |Δinput_projection| =
0.2365) — a genuine different model, not a relabeled twin.

## 3. Fingerprints (exact, steer §4)

- feature schema: scalp_v3 / 70D / schema_hash 235b8fccc96b7e0e
- dataset: data/raw/XAUUSD_M1.parquet, last 400 clean-spread M1 bars
  (0 < spread < 50), deterministic window
  - shadow fingerprint:  002758b6b369e694541601090fde0ffc
  - research fingerprint: 60286f8bafecd67ad2a41fcabebf6b62
- policy: frozen SignalPolicy(confidence_threshold=0.20),
  engine config fingerprints per side embedded in the artifact
- replay ledger hashes: champion 39e6be952f75…, challenger a02e072089a5…
- git revision at generation: cd8e732 · configuration: SHADOW_EVIDENCE_V2

## 4. Paired evidence (400 bars → 346 decision pairs)

| Metric | Value |
|---|---:|
| pairs_total | 346 |
| pairs_invalid (INPUT_MISMATCH) | 0 |
| pairs_resolved | 346 |
| pairs_unresolved | 0 |
| MODEL-level argmax disagreements | 259 (74.9%): CHAMPION_NO_TRADE_SHADOW_BUYS 220 · CHAMPION_NO_TRADE_SHADOW_SELLS 32 · BUY_VS_SELL 7 · AGREEMENT 87 |
| POLICY-level action disagreements | 0 (302 flat · 21 BUY · 23 SELL — identical actions both sides) |
| mean champion R | −0.010178 |
| mean challenger R | −0.010178 |
| mean ΔR / median ΔR | 0.0 / 0.0 |
| mean champion MAE R | −0.291498 |
| confidence Δ (model argmax) | mean 0.004418 |

Interpretation (honest): the two models genuinely disagree at the MODEL level
(74.9% argmax divergence) but the frozen policy maps both onto the same
actions on this window, so every pair's outcome is identical (ΔR=0). This is
a REAL, measured result — the correct shadow verdict is that the challenger
is behaviorally indistinguishable FROM THE POLICY's perspective on this
window while differing materially in raw model confidence.

## 5. Promotion-readiness verdict

```
VERDICT: INSUFFICIENT_EVIDENCE
reason: paired delta within noise band (mean 0.0000R, median 0.0000R)
```

No superiority is claimed or inferable. Auto-promotion: none (no promotion
call site touched). The evidence artifact records everything a future
promotion decision needs: identity, fingerprints, per-pair rows, regime and
session breakdowns.

## 6. Steer §7 proofs (all encoded in tests/unit/test_shadow_replay_evidence_chg0047.py)

1. BUY/SELL not mirror-fabricated — `test_buy_vs_sell_not_mirror_fabricated`:
   BUY r=−0.7/1.2, SELL r=+0.3/1.0 on the same path; c.r ≠ −s.r ✅
2. Invalid rows excluded — `test_invalid_pairs_counted_but_never_scored` ✅
3. Unresolved ≠ zero — `test_unresolved_pairs_excluded_from_mean_delta` +
   `test_directional_trade_without_geometry_is_unresolved` (r=None) ✅
4. Stale artifact replacement invalidates run identity —
   `test_artifact_replacement_changes_dataset_fingerprint` + CHG-0046 D11
   ARTIFACT_REPLACED finalize guard ✅
5. Dataset change ⇒ fingerprint change —
   `test_dataset_content_change_changes_fingerprint` ✅
6. Challenger identity change ⇒ run identity change —
   `test_challenger_identity_change_changes_config_fingerprint` ✅
7. Reproducibility — two independent full pipeline runs produced byte-equal
   evidence (modulo generation timestamp): `reproducible: True` ✅
8. Acceptance matrix (steer §8): invalid vector → invalid+excluded;
   unresolved fill → NOT_RECORDED; artifact replacement → identity change;
   challenger mismatch → identity change. All fail safely ✅

## 7. Defects found during this task

- BUG-216 (Shadow-owned, FIXED part 3/4): circular import replay↔_replay_pair
  discovered on first pipeline run; constants single-sourced in replay.py,
  `_replay_*` import them, replay re-exports after definition.
- No foreign defects encountered in Shadow-owned paths; none ledgered as
  handoff this task.

## 8. Limitations / next evidence steps

- 400-bar window + bar-mode synthetic spread = conservative evidence; a
  tick-mode run over a CHG-0041 tick dataset with a materially different
  challenger is the natural next increment.
- A challenger that diverges at POLICY level (not just model level) is
  required for non-zero ΔR evidence; current candidate agrees at policy
  level by construction of the frozen policy gates.
