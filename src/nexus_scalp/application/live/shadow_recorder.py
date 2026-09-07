"""ShadowRecorder — champion/challenger + 70D shadow observation recording.

P1 seam L1 (god-file decomposition, extraction 1 of the live-engine wave):
the shadow-recording responsibility leaves ``application/live_engine.py``
verbatim (behavior-preserving extraction). The recorder reads the engine's
attribute surface through ``self.om`` so the composition root keeps owning
all state — this module owns the RECORDING LOGIC only.

Moved methods (verbatim bodies, ``self`` -> ``self.om``):
    record_shadow_decision      : 50D Champion/Challenger parallel decision
    record_shadow70_observation : BUG-105 70D observability hook (INV-018)

Failure-isolation contract (spec 17): any recorder fault is logged and
swallowed — a shadow fault must NEVER affect production execution.
"""

from __future__ import annotations

import contextlib
from typing import Any

import numpy as np

from nexus_scalp.domain.models import TickData, TradeProposal
from nexus_scalp.features.regime_classifier import MarketRegimeState
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.application.live.shadow_recorder")


class ShadowRecorder:
    """Shadow decision + 70D observation recorder (composition: engine)."""

    def __init__(self, om: Any) -> None:
        # The composition root (LiveEngine). All state stays there; this
        # object only carries the recording logic.
        self.om = om

    def record_shadow_decision(
        self,
        tick: TickData,
        fv: Any,
        regime_state: MarketRegimeState,
        proposal: TradeProposal,
    ) -> None:
        """
        Records one parallel Champion/Challenger decision on the SAME live
        feature vector (spec 3 / 4). Bounded + failure-isolated: a Challenger
        fault must never affect production execution (spec 17).
        """
        if self.om._shadow_challenger is None and self.om._governance_shadow is None:
            return
        try:
            engine = self.om.shadow_engine
            # CHG-0046 D1: the shadow compares against the model that
            # ACTUALLY served this tick — the loaded bundle's authoritative
            # contract (effective_*), never the class bootstrap constants
            # (which lag at scalp_v1/50D while a 70D bundle serves).
            live_schema_id = str(self.om.effective_feature_schema_id)
            live_dim = int(self.om.effective_feature_dim)
            x50 = fv.to_tensor_input() if hasattr(fv, "to_tensor_input") else [0.0] * live_dim
            # CHG-0046 D5: deterministic full-vector fingerprint (the salted
            # 5-element python hash() was irreproducible across processes and
            # insensitive to 90% of the vector — same-input proof impossible).
            from nexus_scalp.shadow.compat import vector_fingerprint

            feature_hash = vector_fingerprint(x50)
            regime_str = getattr(getattr(regime_state, "regime", None), "value", "UNKNOWN")
            if isinstance(regime_str, str) is False and regime_str is not None:
                regime_str = str(regime_str)
            news_ctx: Any = None
            if self.om._news_enabled and self.om.news_engine is not None:
                try:
                    news_ctx = self.om.news_engine.current_context()
                except Exception:
                    news_ctx = None
            champion_action = (
                proposal.action.value if hasattr(proposal.action, "value") else str(proposal.action)
            )
            champ_probs = [
                float(v)
                for v in (self.om._last_probs.tolist() if self.om._last_probs is not None else [])
            ]
            champ_ref_dict: dict[str, Any] = {
                "model_id": self.om.champion_manager.model_id,
                "model_version": self.om.champion_manager.model_version,
                # CHG-0046 D1: bundle-authoritative identity, not class
                # bootstrap constants (which say scalp_v1/50D while the
                # loaded artifact serves scalp_v3/70D).
                "feature_schema_id": live_schema_id,
                "feature_dimension": live_dim,
            }
            with contextlib.suppress(Exception):
                champ = self.om.champion_manager.champion_or_none()
                if champ is not None:
                    champ_ref_dict["model_id"] = champ.model_id
                    champ_ref_dict["model_version"] = champ.model_version
                    champ_ref_dict["artifact_hash"] = champ.artifact_hash
            if self.om._governance_shadow is not None and engine.active_run_id:
                # TASK-6: compute the 10 REAL scalp_v2 extras from the same
                # causal bar window the Champion used (features/schema_augment,
                # TASK-5 contract). A 60D Challenger must never receive
                # zero-filled extras (INV-009 / no-silent-pad rule).
                extras_60d = None
                try:
                    from nexus_scalp.features.schema_augment import compute_60d_extras

                    bars = self.om.aggregator.get_completed_bars()
                    if bars and len(bars) >= 5:
                        opens = np.asarray([float(b.open) for b in bars[-60:]], dtype=np.float32)
                        highs = np.asarray([float(b.high) for b in bars[-60:]], dtype=np.float32)
                        lows = np.asarray([float(b.low) for b in bars[-60:]], dtype=np.float32)
                        closes = np.asarray([float(b.close) for b in bars[-60:]], dtype=np.float32)
                        vols = np.asarray(
                            [float(getattr(b, "tick_volume", 0.0) or 0.0) for b in bars[-60:]],
                            dtype=np.float32,
                        )
                        extras_60d = compute_60d_extras(
                            opens=opens,
                            highs=highs,
                            lows=lows,
                            closes=closes,
                            volumes=vols,
                        )
                except Exception as e60:
                    logger.debug("[MODEL_SHADOW] 60D extras unavailable (isolated)", error=str(e60))
                self.om._governance_shadow.compare(
                    champion_vector=x50,
                    reference_vector=self.om._governance_reference_vector,
                    news_context=(news_ctx.model_dump() if news_ctx is not None else None),
                    champion_ref=champ_ref_dict,
                    champion_action=champion_action,
                    champion_confidence=float(getattr(proposal, "confidence", 0.0)),
                    champion_probabilities=champ_probs,
                    timestamp=tick.timestamp,
                    symbol=tick.symbol,
                    timeframe="M1",
                    regime=regime_str,
                    session=getattr(proposal, "session", "") or "ALL",
                    run_id=engine.active_run_id,
                    decision_id=getattr(proposal, "request_id", ""),
                    champion_latency_ms=float(self.om._last_inference_latency_ms or 0.0),
                    feature_context_id=feature_hash,
                    extras_60d=extras_60d,
                )
            if self.om._shadow_challenger is not None:
                from nexus_scalp.shadow.models import ShadowModelRef

                champ_ref = ShadowModelRef(
                    model_id=champ_ref_dict.get("model_id", ""),
                    model_version=champ_ref_dict.get("model_version", ""),
                    feature_schema_id=live_schema_id,
                    feature_dimension=live_dim,
                    artifact_hash=champ_ref_dict.get("artifact_hash", ""),
                    is_champion=True,
                )
                engine.set_champion_ref(champ_ref)
                engine.record_shadow_decision(
                    timestamp=tick.timestamp,
                    symbol=tick.symbol,
                    timeframe="M1",
                    feature_hash=feature_hash,
                    feature_schema_id=live_schema_id,
                    feature_dimension=live_dim,
                    regime=regime_str,
                    session=getattr(proposal, "session", "") or "ALL",
                    configuration_version=str(
                        getattr(self.om.config.model, "feature_schema_version", "")
                    ),
                    champion_ref=champ_ref,
                    champion_action=champion_action,
                    champion_confidence=float(getattr(proposal, "confidence", 0.0)),
                    champion_probabilities=champ_probs,
                    champion_strategy_id="",
                    decision_id=getattr(proposal, "request_id", ""),
                    feature_vector=x50,
                    # CHG-0046 D3: capture BOTH sides' risk geometry at record
                    # time. Champion geometry = the real proposal the policy
                    # emitted; shadow geometry is filled by the engine from
                    # the challenger action (side-neutral ATR geometry below
                    # once RiskEngine-level sizing is mirrored — the shadow
                    # NEVER consults RiskEngine itself).
                    champion_entry=float(proposal.proposed_entry),
                    champion_sl=float(proposal.stop_loss),
                    champion_tp=float(proposal.take_profit),
                    spread_usd=float(tick.spread_points),
                )
        except Exception as e:
            # Shadow is observability only: a failure here NEVER disturbs live.
            logger.error("[SHADOW] event=RECORD_FAILURE (isolated)", error=str(e))

    def record_shadow70_observation(
        self,
        tick: TickData,
        fv: Any,
        proposal: TradeProposal,
    ) -> None:
        """BUG-105: 70D shadow observation (observability ONLY, INV-018).

        Runs on EVERY tick (independent of the 50D shadow/Challenger gate —
        the previous placement inside _record_shadow_decision's except block
        made it dead code on the happy path). Builds the canonical 70D vector
        (BASE 0..49 from the live 50D features, NEWS 50..59 from the same
        news context the Champion consumed, LIQUIDITY 60..69 from the
        liquidity producer) and records a SIMULATED observation. Fully
        failure-isolated: any fault logs and returns; the Champion path is
        never disturbed.
        """
        rt70 = getattr(self.om, "_shadow70_runtime", None)
        if (
            rt70 is None
            or rt70.state.value != "READY"
            or not getattr(self.om, "_shadow70_enabled", False)
        ):
            return
        try:
            from nexus_scalp.features.liquidity_runtime import (
                build_70d_vector,
            )
            from nexus_scalp.shadow.shadow70.liq_provider import build_liquidity_10

            # CHG-0046 D1b: the 70D observation inherits the bundle's
            # AUTHORITATIVE base width, not the hard-coded 50 — a 0-filled
            # fallback must match the ACTUAL base block the champion used.
            _base_dim = int(self.om.effective_feature_dim) - 20
            base50 = [0.0] * max(1, _base_dim)
            if fv is not None:
                v = fv.to_tensor_input() if hasattr(fv, "to_tensor_input") else None
                if v is not None and len(v) == _base_dim:
                    base50 = list(v)
            feature_hash = getattr(fv, "feature_hash", "") or ""
            regime_str = getattr(getattr(self.om, "_last_regime_state", None), "regime", None)
            regime_str = getattr(regime_str, "value", "UNKNOWN") or "UNKNOWN"

            # news vector from the same context the Champion saw
            news10 = [0.0] * 10
            news_ctx: Any = None
            if self.om._news_enabled and self.om.news_engine is not None:
                try:
                    news_ctx = self.om.news_engine.current_context()
                except Exception:
                    news_ctx = None
            if news_ctx is not None:
                try:
                    from nexus_scalp.governance.alignment import (
                        vectorize_news_context,
                    )
                    from nexus_scalp.shadow.shadow70.news_provider import (
                        build_news_10,
                    )

                    news10, _ = build_news_10(vectorize_news_context(news_ctx))
                except Exception:
                    news10 = [0.0] * 10

            # CHG-0046 D8: record the governor's CAUSAL state alongside the
            # snapshot. The champion consumes a governor snapshot ONLY when
            # causal_state == VALID (else inference is blocked); the shadow
            # accepts a fresh-but-invalid snapshot and labels it. The
            # liquidity_state column now carries that truth so an operator
            # can distinguish a like-for-like comparison from an
            # INPUT_MISMATCH (the champion saw no liquidity at all).
            liquidity_calc_version = ""
            liquidity_causal_state = ""
            liq10 = [0.0] * 10
            gov = getattr(self.om, "liquidity_governor", None)
            if gov is not None:
                try:
                    liquidity_causal_state = str(
                        gov.causal_state() if callable(getattr(gov, "causal_state", None)) else ""
                    )
                except Exception:
                    liquidity_causal_state = ""
            try:
                liq10, liquidity_calc_version = build_liquidity_10(self, tick)
            except Exception:
                liq10, liquidity_calc_version = [0.0] * 10, ""

            # canonical schema identity for THIS observation (the old hook
            # passed "" which silently skipped schema verification)
            from nexus_scalp.features.schema_contract import feature_schema_hash

            schema_hash = feature_schema_hash()

            vector70 = build_70d_vector(base50, family_10=news10, liquidity_10=liq10)

            champion_action = (
                proposal.action.value if hasattr(proposal.action, "value") else str(proposal.action)
            )
            champ_probs = [
                float(v)
                for v in (self.om._last_probs.tolist() if self.om._last_probs is not None else [])
            ]
            obs = rt70.observe(
                vector70=vector70,
                champion_action=champion_action,
                champion_probabilities=champ_probs,
                champion_confidence=float(getattr(proposal, "confidence", 0.0)),
                snapshot_id=feature_hash or f"snap_{tick.timestamp.isoformat()}",
                timestamp=tick.timestamp,
                symbol=tick.symbol,
                timeframe="M1",
                regime=regime_str,
                session=getattr(proposal, "session", "") or "ALL",
                news_context=(news_ctx.model_dump() if news_ctx is not None else None),
                news_state=str(getattr(news_ctx, "state", "") or "")
                if isinstance(news_ctx, object)
                else "",
                # CHG-0046 D8: truthful liquidity provenance — the governor's
                # causal state + how the 10 values were produced. An
                # INVALID/stale state means the CHAMPION would have blocked
                # inference this tick; the shadow row is labeled, not silent.
                liquidity_state=liquidity_causal_state
                or ("unavailable" if liquidity_calc_version == "unavailable" else "UNKNOWN"),
                liquidity_calculation_version=liquidity_calc_version,
                liquidity_features_10=liq10,
                base_feature_hash=feature_hash,
                feature_schema_hash=schema_hash,
                sample_source="LIVE",
                decision_id=getattr(proposal, "request_id", ""),
            )
            hm = getattr(self.om, "_shadow70_health", None)
            if hm is not None and obs.valid:
                hm.update(vector70, stale=False)
            dm = getattr(self.om, "_shadow70_drift", None)
            if dm is not None and obs.valid:
                dm.update(vector70)
            wk = getattr(self.om, "_shadow70_worker", None)
            if wk is not None:
                if not getattr(self.om, "_shadow70_worker_started", False):
                    wk.start()
                    self.om._shadow70_worker_started = True
                if not wk.enqueue(obs):
                    pass  # backpressure already telemetried by the worker
        except Exception as e70:
            logger.error("[SHADOW70] hook failed (isolated, Champion unaffected)", error=str(e70))
