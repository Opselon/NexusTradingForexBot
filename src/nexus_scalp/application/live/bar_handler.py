"""BarHandler — completed-bar (M1 close) processing for the live engine.

P1 seam L5 (god-file decomposition, extraction 5 of the live-engine wave):
``_on_new_bar`` leaves ``application/live_engine.py`` verbatim
(behavior-preserving extraction). Responsibilities moved: BUG-061 candle-intel
hook, Market Radar setup detection (BUG-138), MSLIE perception vector,
rolling-retrain buffer update (BUG-185 fallback width), and the
config-gated online fine-tune trigger (Phase 6/10 learning loop).

State ownership: rolling buffers / counters / radar state stay at the
composition root (LiveEngine), reached through ``self.om``; this module
owns the BAR-PROCESSING LOGIC only. Failure-isolated per stage.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.candle_intelligence import RegimeState
from nexus_scalp.domain.models import TickData
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.application.live.bar_handler")


class BarHandler:
    """Completed-bar processing (composition root: LiveEngine)."""

    def __init__(self, om: Any) -> None:
        self.om = om

    def on_new_bar(self, tick: TickData, fv, last_bar) -> None:
        # BUG-061: candle-close gate — feed the completed bar into the local
        # candle-intelligence subsystem and capture its decision (entry/hold/
        # fast-exit bias). Failure is isolated; never disturbs the tick path.
        ci = getattr(self, "candle_intel", None)
        if ci is not None:
            try:
                regime_name = getattr(self.om._last_regime_state, "regime_type", None)
                regime_name = getattr(regime_name, "value", "UNKNOWN") if regime_name else "UNKNOWN"
                regime_state = RegimeState(
                    symbol=tick.symbol,
                    timeframe="M1",
                    timestamp=last_bar.timestamp,
                    regime=str(regime_name),
                    atr=float(getattr(fv, "atr_m1", 0.0) or 0.0),
                    spread=float(max(0.0, tick.ask - tick.bid)),
                )
                out = ci.ingest_bar(
                    symbol=tick.symbol,
                    timeframe="M1",
                    timestamp=last_bar.timestamp,
                    open_=float(last_bar.open),
                    high=float(last_bar.high),
                    low=float(last_bar.low),
                    close=float(last_bar.close),
                    volume=float(getattr(last_bar, "tick_volume", 0.0) or 0.0),
                    is_complete=True,
                    regime_state=regime_state,
                    holding_position=bool(self.om.order_manager._position_states),
                )
                self.om._last_candle_decision = out.to_dict() if out else None
            except Exception as ci_err:
                logger.error("[CANDLE_INTEL] bar feed failed (isolated)", error=str(ci_err))

        # ---------------------------------------------------------------------
        # Build the canonical 50D feature record for THIS bar (always available,
        # independent of MSLIE). Used by the Market Radar detector below and the
        # rolling retrain buffer. NOTE: rec must be defined BEFORE the radar block
        # (BUG-139: prior nesting inside the mslie_engine conditional left `rec`
        # unbound when mslie_engine was None -> BAR_DETECT_FAILED).
        # BUG-185 PART-3: the canonical per-bar retrain record is built by the
        # shared builder — full scalp_v3 geometry when a 70D champion serves
        # (Base 0..49 | News 50..59 | Liquidity 60..69), 50D base otherwise.
        # The builder REFUSES (None) when the real liquidity snapshot is not
        # VALID — never zero-fills — and its width guard turns any residual
        # contract split into a structured FEATURE_CONTRACT_MISMATCH (SKIP),
        # not a raw IndexError.
        rec = self.om._build_retrain_record(
            base50=fv.to_tensor_input(),
            fv=fv,
            bar=last_bar,
            spread=(tick.ask - tick.bid),
            context="new_bar_record",
        )
        if rec is None:
            # 70D record refused (liquidity not VALID yet): keep the legacy
            # 50D observability record so the radar/UI keep working; it is
            # simply NOT appended to the retrain buffer by this path (the
            # width guard below keeps starvation loud).
            rec = {
                f"feat_{i}": float(v)
                for i, v in enumerate(
                    self.om._validate_50d_tensor(
                        fv.to_tensor_input(), context="new_bar_record_fallback_50d"
                    )
                )
            }
            rec.update(
                close=last_bar.close,
                high=last_bar.high,
                low=last_bar.low,
                open=last_bar.open,
                spread=(tick.ask - tick.bid),
                atr_m1=fv.atr_m1,
            )
        if self.om._governance_reference_vector is None:
            _ref = self.om._validate_50d_tensor(
                fv.to_tensor_input(), context="governance_reference"
            )
            self.om._governance_reference_vector = [float(v) for v in _ref]

        # ---------------------------------------------------------------------
        # Market Radar (Hunter SetupDetector) - live, bar-close cadence (BUG-138).
        # Runs on the SAME completed-bar feature record as the sample-maker uses,
        # but here for the LIVE path. Pure + causal; failure-isolated. Stores the
        # ranked setup list as _last_market_radar for the Intel Hub / Web Panel.
        try:
            radar_rec = rec
            if "feat_0" not in radar_rec:
                radar_rec = (
                    self.om._rolling_feature_records[-1]
                    if self.om._rolling_feature_records
                    else None
                )
            if radar_rec is not None:
                detected = self.om.setup_detector.detect(radar_rec, timestamp=last_bar.timestamp)
                ranked = sorted(detected, key=lambda s: s.quality, reverse=True)
                best = ranked[0] if ranked else None
                _regime_val = (
                    getattr(getattr(self.om._last_regime_state, "regime_type", None), "value", None)
                    or "UNKNOWN"
                )
                _news_state_val = None
                try:
                    if getattr(self, "news_engine", None) is not None:
                        _nc = self.om.news_engine.current_context()
                        if _nc is not None:
                            _ns = getattr(_nc, "state", None)
                            _news_state_val = getattr(_ns, "value", None) or str(_ns)
                except Exception:
                    _news_state_val = None
                self.om._last_market_radar = {
                    "symbol": tick.symbol,
                    "timestamp": last_bar.timestamp.isoformat(),
                    "bar_timestamp": last_bar.timestamp.isoformat(),
                    "regime": str(_regime_val),
                    "candidate_count": len(ranked),
                    "best_setup": best.to_contract() if best else None,
                    "setups": [s.to_contract() for s in ranked[:5]],
                    "state": (
                        "SETUP_READY"
                        if best and best.quality >= self.om.setup_detector.min_quality
                        else ("WATCHING" if ranked else "NO_SETUP")
                    ),
                    "news_state": _news_state_val,
                    "decision_reason": self.om._last_proposal.reason_code
                    if getattr(self, "_last_proposal", None)
                    else None,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
        except Exception as radar_err:
            logger.warning("[RADAR] event=BAR_DETECT_FAILED error=%s", radar_err)

        # ---------------------------------------------------------------------
        # MSLIE: market perception on the bar-close cadence (pure numpy, no
        # I/O, no DB — INV-001). The engine produces the structured
        # MarketIntelligenceFeatureVectorV1 for the debug UI / AI models.
        # Failure is isolated: perception can never disturb the tick path.
        # =====================================================================
        ms = getattr(self, "mslie_engine", None)
        if ms is not None:
            try:
                completed_bars = self.om.aggregator.get_completed_bars()
                if completed_bars:
                    vector = ms.analyze_market(
                        completed_bars,
                        decision_at=last_bar.timestamp,
                        mid_price=float(tick.bid),
                        atr=float(getattr(fv, "atr_m1", 0.0) or 0.0),
                    )
                    self.om._last_mslie_vector = vector
            except Exception as ms_err:
                logger.warning(
                    "[MSLIE] event=BAR_FEED_FAILED error=%s (isolated; trading unaffected)",
                    ms_err,
                )

        self.om._rolling_feature_records.append(rec)
        self.om._bars_since_last_retrain += 1

        # BUG-169: width guard for the online fine-tune path. The buffer
        # records carry the 50D tensor (feat_0..feat_49, class contract);
        # feeding them to a 70-input model head crashed with
        # "mat1 and mat2 shapes cannot be multiplied (10x50 and 70x128)" on
        # EVERY retrain window while the 70D champion was loaded (60
        # failures on 2026-08-31) and each crash burned a scaler-save
        # attempt against the artifact the engine holds (WinError 5).
        # Gate: fine-tune only when the trainer's bound width matches the
        # actual record width; the __init__ rebind covers the 70D case
        # via FEATURE_COLS on the effective contract.
        # BUG-185: a record width that disagrees with the rebound trainer
        # width is a CONTRACT SPLIT (buffer built 50D vs trainer bound 70D),
        # not a routine case - surface it loudly once per hour instead of
        # silently starving the 70D online-learning loop.
        if len(rec) - 6 != self.om.trainer.num_features or getattr(
            self, "_online_train_disabled", False
        ):
            if self.om._bars_since_last_retrain >= self.om._retrain_interval_bars and (
                not getattr(self, "_online_train_width_warn_at", 0.0)
                or time.time() - self.om._online_train_width_warn_at >= 3600.0
            ):
                self.om._online_train_width_warn_at = time.time()
                # BUG-185: CRITICAL, not WARNING - this split starves the
                # online-learning loop for the loaded contract entirely.
                logger.critical(
                    "[ONLINE_TRAIN] SKIPPED width-contract split "
                    "record_width=%s trainer_width=%s (BUG-169 guard, BUG-185 "
                    "record-contract violation - buffer builder did not follow "
                    "the loaded bundle contract)",
                    len(rec) - 6,
                    self.om.trainer.num_features,
                )
            return

        # AGENT-8 BUG-243 (runtime forensics 2026-09-05): defensive row-width
        # filter immediately before the DataFrame materialization boundary.
        # The width guard above is check-then-use: the buffer is APPEND-ONLY
        # and a restart with a DIFFERENT champion width (50D <-> 70D hot-swap)
        # leaves mixed-width rows in the deque. polars unions heterogeneous
        # dicts BY NAME, materializing the missing columns as None (proven:
        # probe -> feat_50..feat_69 nulls), and neither _validate_training_frame
        # (labels only) nor _filter_trainable_rows (label_evaluated/is_purged
        # only) inspects feature nulls. The trainer's nan_to_num then silently
        # trains on zero-fabricated rows - the exact "invalid record becomes
        # zero-fill" class the record-builder invariant forbids, entering via
        # the DATAFRAME boundary instead of the builder. Guard = keep only
        # rows whose feat_* width matches the bound trainer contract.
        if self.om._rolling_feature_records:
            # PERF (TASK-PERF-A16-HOTPATH): the BUG-243 filter used to rescan
            # the full buffer deque (up to 4000 records x ~56 keys, measured
            # ~23.5ms) on EVERY completed bar. The buffer is APPEND-ONLY, so a
            # width anomaly can enter through exactly two doors: (a) the record
            # appended by THIS bar (checked in O(1) below), or (b) a trainer
            # width change since the previous bar (hot-swap epoch boundary).
            # On either trigger we fall back to the ORIGINAL full scan + filter
            # so the BUG-243 repair semantics are preserved verbatim.
            _rec_feat_width = sum(1 for k in rec if str(k).startswith("feat_"))
            _expected = int(self.om.trainer.num_features)
            _prev_expected = getattr(self, "_a16_last_trainer_width", None)
            self._a16_last_trainer_width = _expected
            # (c) FIRST bar handled by this handler instance: the buffer may
            # already hold mixed-width rows (restart mid-epoch, live hot-swap
            # by another surface) - the original per-bar scan would have caught
            # that immediately, so the first pass must too. After the first
            # pass the buffer is width-clean and only doors (a)/(b) can
            # re-introduce a mix.
            _first_bar = _prev_expected is None
            _epoch_changed = _prev_expected is not None and _prev_expected != _expected
            if _rec_feat_width != _expected or _epoch_changed or _first_bar:
                _widths = {
                    sum(1 for k in r if str(k).startswith("feat_"))
                    for r in self.om._rolling_feature_records
                }
                if len(_widths) > 1:
                    _before = len(self.om._rolling_feature_records)
                    self.om._rolling_feature_records = deque(
                        (
                            r
                            for r in self.om._rolling_feature_records
                            if sum(1 for k in r if str(k).startswith("feat_")) == _expected
                        ),
                        maxlen=_before,
                    )
                    logger.warning(
                        "[ONLINE_TRAIN] event=BUFFER_WIDTH_FILTER dropped=%s kept=%s "
                        "expected_width=%s widths_seen=%s (mixed-width rows would "
                        "have become None->0.0 fabrications in the training frame)",
                        _before - len(self.om._rolling_feature_records),
                        len(self.om._rolling_feature_records),
                        _expected,
                        sorted(_widths),
                    )

        if (
            self.om._bars_since_last_retrain
            and len(self.om._rolling_feature_records) >= 300
            and not self.om._retrain_inflight
        ):
            # LEARNING-LOOP (Phase 10): the online fine-tune rewrites the LIVE
            # serving artifact outside the governed promotion transaction. It
            # is config-gated (config.learning.online_finetune.enabled) and
            # DISABLED by default — the dispatch below simply never fires
            # while disabled. One throttled INFO per retrain window (not a
            # per-bar warning; the disabled state is the supported default).
            if not self.om._online_finetune_enabled:
                if self.om._bars_since_last_retrain >= self.om._retrain_interval_bars and (
                    not getattr(self, "_online_ft_disabled_log_at", 0.0)
                    or time.time() - self.om._online_ft_disabled_log_at >= 3600.0
                ):
                    self.om._online_ft_disabled_log_at = time.time()
                    logger.info(
                        "[ONLINE_TRAIN] event=DISABLED_BY_CONFIG "
                        "(champion artifact immutable between governed promotions; "
                        "enable via learning.online_finetune.enabled after DEC-0006)"
                    )
                return
            try:
                loop = asyncio.get_running_loop()
                self.om._retrain_task = loop.create_task(self.om._trigger_async_online_fine_tune())
            except RuntimeError:
                pass
