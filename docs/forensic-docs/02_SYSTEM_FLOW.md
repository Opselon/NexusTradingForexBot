# 02 — System Flow

## The tick lifecycle (hot path)

```
Broker tick (UTC-normalized)
  → LiveEngine._process_tick_pipeline
      1. sync runtime config snapshot        (UI save reflects next tick)
      2. BarAggregator.process_tick          (new M1 bar? → 4000-bar cap)
      3. ScalpFeatureEngine 50D vector       (causal, finite, [-3,+3])
      4. _on_new_bar side-effects            (candle intel, retrain cadence)
      5. MarketRegimeClassifier              (10 regimes + hysteresis)
      6. OrderManager.manage_active_positions (positions FIRST; probs+regime)
      7. warmup gate?                        (HTF_WARMUP_INCOMPLETE NO_TRADE)
      8. inference (reused, once/tick)       (scaler → torch num_threads=1
                                              → softmax)
      9. SignalPolicy                        (guardian → dedup → confidence
                                              → spread/RR → hysteresis →
                                              rule matrix → SMC → proposal)
     10. Experience gate                     (down-rank only, TTL-cached)
     11. Intelligence gate                   (WARN/PENALIZE/REJECT only)
     12. News gate                           (±5%/−10% bounded, no-op on failure)
     13. audit.log_signal                    (dedup-keyed row, queued)
     14. shadow recording                    (challenger, same vector)
     15. shadow70 observation                (70D hook, isolated)
     16. chart overlays + server_state       (900-bar window)
     17. dispatch: AI-reversal (close-then-flip) OR
         entry (risk sizing → clamp → setup snapshot → dispatch)
     18. liquidity governor (new-bar cadence, info-only)
```

## Signal → Risk → Execution pipeline

```
ScalpNet probs (4 classes)
  → SignalPolicy gates   →  TradeProposal (EXEC-…-trace-id stamped)
  → RiskEngine.evaluate_proposal
      kill switch → exposure → spread → RR → stops-level → drawdown/conf
      → dynamic volume (8-step) → margin verify → Almgren-Chriss impact
  → TradeOrder (magic 888101, NSE_HFT_SIZED)
  → OrderLifecycleManager.dispatch_order
      MAX_TOTAL_EXPOSURE=1 → HARD_MAX_LOTS clamp → entry-context staged
      → broker call → audit.log_order
  → position lifecycle (every tick):
      protect (BE lock / trailing / giveback) + adaptive recovery +
      arbitration (5-level, HOLD never overrides protective EXIT)
  → close (verified) → ledger autopsy + experience outcome +
      lifecycle finalize + Telegram canonical close
```

## ML pipeline lifecycle (training side)

```
Experience ledger / history
  → datasets (schema_v2 60D/70D builders; causal frames; verify gates)
  → triple-barrier labels (cost-aware, purged, MAE guard)
  → walk-forward (34 folds, purge+embargo, fresh model per fold, OOS metrics)
  → final training → artifact-first Model Factory (manifests, hashes)
  → validation gates (macro-F1/balanced-acc/ECE/evidence floors,
      grad-norm, collapse) → benchmark MATRIX 8 cells
  → champion/challenger governance (load gates, promotion approval,
      shadow comparison, rollback) → live hot-swap under bundle_lock
  → online fine-tune (300-record rolling buffer, quality-gated,
      atomic swap)
```

## Data lifecycle / persistence

```
Ticks → bars → features → proposals → orders → positions
  → audit.db (WAL): audit_signals (dedup-keyed), audit_orders, ledger
      (autopsy rows), experiences+outcomes (immutable, idempotency_key),
      snapshots (throttled), research/intelligence/hygiene tables
  → news.db (articles, consensus, impacts)
  → candle_intel.db (close-quality decisions)
  → artifacts/: model artifacts (manifests), archives (sha256 .jsonl),
      golden baselines
  → accounting core reads ONLY these stores → REST/UI/Telegram
```