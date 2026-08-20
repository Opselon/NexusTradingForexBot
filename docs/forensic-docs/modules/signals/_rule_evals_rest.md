# src/nexus_scalp/signals/_rule_evals_rest.py

- **PURPOSE:** The "rest of the rules" — the 17+ entry/exit/risk evaluators
  that don't fit the HFT or SMC families: HitAndRunExit, ZeroDrawdownTrail,
  TimeDecayChopExit, AtrExpansionRatchet, HedgeOnAiFlip, LondonNyKillzoneOnly,
  AsianRangeFakeout, NewsSpikeFade, DeadZoneBlocker, EndOfHourSqueeze,
  ConsecutiveLossFreeze, DailyTargetLock, AiMacroAlignment,
  TurboConfidenceMultiplier and friends.
- **ARCHITECTURE LAYER:** Signals (rule implementations).
- **RESPONSIBILITY:** Encode the behavioral playbook as discrete veto/trigger
  rules:
  - Exit-family: HitAndRun (take quick wins), ZeroDrawdownTrail (trail to
    breakeven lock), TimeDecayChop (exit chop after time decay),
    AtrExpansionRatchet (ratchet stops on ATR expansion), HedgeOnAiFlip
    (defensive stance when the model flips direction).
  - Session/entry-family: LondonNyKillzoneOnly (trade only in killzones),
    AsianRangeFakeout (fade Asian-range breaks), NewsSpikeFade (fade
    post-news spikes), DeadZoneBlocker, EndOfHourSqueeze, FvgSniperFill,
    JudasSwingFade, LiquiditySweepConfirm, OrderBlockTapReserve,
    WickAbsorptionPlay (in the SMC module).
  - Meta-family: ConsecutiveLossFreeze (halt after N losses — survival),
    DailyTargetLock (lock after daily target hit — profit discipline),
    AiMacroAlignment (require model+macro agreement),
    TurboConfidenceMultiplier (scale confidence in strong confluence).
- **DEPENDENCIES:** ctx types, `_pos`/`_holding_duration` helpers, result
  types, logging.
- **CONNECTS TO:** `_rule_engine` registry + policy gates; the
  ConsecutiveLossFreeze and DailyTargetLock rules implement the survival/
  profit-discipline kill-switch semantics surfaced in Telegram alerts.
- **KEY CONCEPTS:** Each rule is a small deterministic state machine over
  context (position PnL, holding time, spread, session, news state, model
  probs); some rules BLOCK (veto), some TRIGGER (demand action, e.g. exit) —
  the stage gate decides which statuses act.
- **EDGE CASES & PITFALLS:** Holding-duration logic derives from the TICK
  TIMESTAMP (never host clock — the repo-wide lesson); rules must not
  re-implement position math that OrderManager owns — they READ context
  only.