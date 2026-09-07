"""WarmupService — HTF warmup evaluation and cold-start bootstrap.

P1 seam L10 (god-file decomposition, extraction 10 of the live-engine wave).
The warmup cluster leaves ``application/live_engine.py`` verbatim
(behavior-preserving extraction):

    evaluate_warmup_readiness : H1/H4 bar-count gates + feature validation
                                -> warmup_state transitions (READY/BLOCKED)
    _cold_start_warmup        : seeds completed bars into the feature engine,
                                warms the liquidity governor, builds the
                                rolling retrain buffer records, triggers the
                                fine-tune path when warm

State ownership: ``warmup_state`` / ``_warmup_attempt`` / thresholds stay at
the composition root (LiveEngine) — they are cross-service coordination
state read by the tick pipeline and runtime loop; this module owns the
WARMUP LOGIC. Methods are invoked UNBOUND with the engine as the state
surface (``WarmupService._x(engine, ...)``), preserving the established
harness/stand-in test contract (test_runtime_70d_warmup drives the real
methods on ``LiveEngine.__new__`` stand-ins).
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.domain.models import TickData
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.application.live.warmup")


class WarmupService:
    """HTF warmup evaluation + cold-start bootstrap (composition root)."""

    def __init__(self, om: Any) -> None:
        self.om = om


    def evaluate_warmup_readiness(self, symbol: str, h1_bars: list, h4_bars: list) -> bool:
        """
        Evaluates HTF bar counts and feature vector validation state to determine if warmup is complete.
        """
        h1_status = "READY" if len(h1_bars) >= self.H1_REQUIRED_BARS else "INSUFFICIENT"
        logger.info(
            f"[WARMUP] H1\nrequired_bars={self.H1_REQUIRED_BARS}\navailable_bars={len(h1_bars)}\nstatus={h1_status}"
        )
    
        h4_status = "READY" if len(h4_bars) >= self.H4_REQUIRED_BARS else "INSUFFICIENT"
        logger.info(
            f"[WARMUP] H4\nrequired_bars={self.H4_REQUIRED_BARS}\navailable_bars={len(h4_bars)}\nstatus={h4_status}"
        )
    
        completed_bars = self.aggregator.get_completed_bars()
        htf_fallbacks = 0
        valid_count = 0
        fallback_count = 0
        invalid_count = 0
    
        if completed_bars:
            last_b = completed_bars[-1]
            last_tick = TickData(
                symbol=symbol,
                timestamp=getattr(last_b, "timestamp", datetime.now(UTC)),
                bid=last_b.close,
                ask=last_b.close + 0.20,
                volume=last_b.tick_volume,
            )
            sample_fv = self.feature_engine.compute_from_bars(completed_bars, last_tick)
    
            # 0.0 is the documented HTF cold-start fallback value (not a real
            # reading); counting fallbacks here quantifies warmup progress.
            if sample_fv.htf_h4_trend == 0.0:
                htf_fallbacks += 1
                logger.warning(
                    "[FEATURE_FALLBACK]\ntimeframe=H4\nfeature=htf_h4_trend\nreason=INSUFFICIENT_H4_BARS\nsource=adapter.get_historical_bars\nfallback=0.0\nwarmup_state="
                    + self.warmup_state
                )
            if sample_fv.htf_h1_momentum == 0.0:
                htf_fallbacks += 1
                logger.warning(
                    "[FEATURE_FALLBACK]\ntimeframe=H1\nfeature=htf_h1_momentum\nreason=INSUFFICIENT_H1_BARS\nsource=adapter.get_historical_bars\nfallback=0.0\nwarmup_state="
                    + self.warmup_state
                )
    
            x50 = sample_fv.to_tensor_input()
            for val in x50:
                if math.isnan(val) or math.isinf(val):
                    invalid_count += 1
                elif val == 0.0:
                    fallback_count += 1
                else:
                    valid_count += 1
    
        is_ready = (h1_status == "READY") and (h4_status == "READY") and (htf_fallbacks == 0)
    
        if not is_ready:
            missing_h1 = max(0, self.H1_REQUIRED_BARS - len(h1_bars))
            missing_h4 = max(0, self.H4_REQUIRED_BARS - len(h4_bars))
            missing_tf = "H1" if missing_h1 > 0 else "H4"
            missing_cnt = missing_h1 if missing_h1 > 0 else missing_h4
            req_cnt = self.H1_REQUIRED_BARS if missing_h1 > 0 else self.H4_REQUIRED_BARS
            avail_cnt = len(h1_bars) if missing_h1 > 0 else len(h4_bars)
    
            logger.info(
                f"[WARMUP] WAITING\ntimeframe={missing_tf}\nrequired={req_cnt}\navailable={avail_cnt}\nmissing={missing_cnt}\nattempt={self._warmup_attempt}"
            )
            logger.info(
                f"[FEATURE_STATUS]\nbase_features=50\nmodel_input_features={self.effective_feature_dim}\nfeature_schema={self.effective_feature_schema_id}\nvalid={valid_count}\nbase_fallbacks={fallback_count}\ninvalid={invalid_count}\nhtf_fallbacks={htf_fallbacks}\nstatus=NOT_READY"
            )
            self.warmup_state = "SAFE_NOT_READY"
            self._inference_enabled = False
            logger.error("[WARMUP] FAILED\nreason=INSUFFICIENT_HTF_HISTORY\nstate=SAFE_NOT_READY")
            logger.warning("[INFERENCE] BLOCKED\nreason=HTF_WARMUP_INCOMPLETE")
        else:
            self.warmup_state = "READY"
            self._inference_enabled = True
            logger.info(
                f"[FEATURE_STATUS]\nbase_features=50\nmodel_input_features={self.effective_feature_dim}\nfeature_schema={self.effective_feature_schema_id}\nvalid={valid_count}\nbase_fallbacks={fallback_count}\ninvalid={invalid_count}\nhtf_fallbacks={htf_fallbacks}\nstatus=READY"
            )
            # STATE-SEMANTICS (C-002, 2026-09-02): the htf_fallbacks counter
            # is the HTF (H1/H4) fallback count ONLY. It was previously
            # mislabeled fallback_features=N, contradicting
            # [FEATURE_STATUS] base_fallbacks=17 (BUG-070-5 class).
            logger.info(
                f"[WARMUP] COMPLETE\nsymbol={symbol}\nH1={len(h1_bars)}/{self.H1_REQUIRED_BARS}\nH4={len(h4_bars)}/{self.H4_REQUIRED_BARS}\nhtf_fallbacks={htf_fallbacks}\nbase_fallbacks={fallback_count}\nstatus=READY"
            )
            logger.info("[INFERENCE] ENABLED\nreason=HTF_WARMUP_COMPLETE")
    
        return is_ready


    async def cold_start_warmup(self, symbol: str) -> None:
        self._warmup_attempt += 1
        logger.info(f"[WARMUP] START\nsymbol={symbol}\nrequired_timeframes=[H1,H4]")
    
        # Non-blocking async fetch of HTF historical bars
        h1_bars = (
            await asyncio.to_thread(
                self.adapter.get_historical_bars, symbol, "H1", self.H1_REQUIRED_BARS
            )
            or []
        )
        h4_bars = (
            await asyncio.to_thread(
                self.adapter.get_historical_bars, symbol, "H4", self.H4_REQUIRED_BARS
            )
            or []
        )
    
        # Fetch 20000 M1 bars (~14 days) to populate full M1..MN1 aggregations.
        # 20000 covers SMA200 on M1 and gives H1/H4/D1/W1 real resampled history
        # so the technicals card shows values (not Neutral-gaps) on every TF.
        hist_m1_bars = (
            await asyncio.to_thread(self.adapter.get_historical_bars, symbol, "M1", 20000) or []
        )
    
        # RESYNC (BUG-054): reseed the aggregator with the broker-authoritative
        # M1 history instead of blind-appending. After 5-6h downtime the first
        # live tick must CONTINUE the broker's current minute, not mint a
        # duplicate stale bar with the same timestamp.
        last_seeded = self.aggregator.reseed(hist_m1_bars)
        completed_init = self.aggregator.get_completed_bars()
        if completed_init:
            self._warm_liquidity_from_bars(completed_init, atr=1.5)
    
        completed = self.aggregator.get_completed_bars()
        if len(completed) >= 55:
            last_300 = completed[-300:] if len(completed) > 300 else completed
            for i in range(54, len(last_300)):
                window = last_300[: i + 1]
                b = last_300[i]
                bar_time = getattr(b, "timestamp", getattr(b, "time", datetime.now(UTC)))
                synthetic_tick = TickData(
                    symbol=symbol,
                    timestamp=bar_time,
                    bid=b.close,
                    ask=b.close + 0.20,
                    volume=b.tick_volume,
                )
                fv = self.feature_engine.compute_from_bars(window, synthetic_tick)
                # BUG-185 PART-3: 70D champion => the record carries the full
                # canonical scalp_v3 geometry (Base|News|Liquidity) via the
                # shared builder; the builder REFUSES (returns None) when the
                # real liquidity snapshot is not yet VALID instead of indexing
                # a 50-element base over a 70-wide range (IndexError class).
                record = self._build_retrain_record(
                    base50=fv.to_tensor_input(),
                    fv=fv,
                    bar=b,
                    spread=0.20,
                    context="cold_start_warmup",
                )
                if record is None:
                    continue
                self._rolling_feature_records.append(record)
    
        self.evaluate_warmup_readiness(symbol, h1_bars, h4_bars)
    
        # Immediately extract and update real SMC overlays to prevent cold-start blank canvas in MT5 mode
        completed_bars = self.aggregator.get_completed_bars()
        if completed_bars and hasattr(self, "server_state") and self.server_state is not None:
            raw_atr = (
                self._rolling_feature_records[-1]["atr_m1"]
                if self._rolling_feature_records
                else 1.5
            )
            real_overlays = self.signal_policy.extract_live_chart_overlays(
                completed_bars=completed_bars, atr_val=raw_atr
            )
            bars_list = []
            for b in completed_bars[-900:]:
                bars_list.append(
                    {
                        "time": b.timestamp.isoformat()
                        if hasattr(b.timestamp, "isoformat")
                        else str(b.timestamp),
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "volume": b.tick_volume,
                        "is_complete": True,
                    }
                )
            self.server_state.update_live_visuals(bars_list, real_overlays)
            logger.info(
                "Cold-start SMC visual overlays successfully bridged to server state!",
                bars=len(bars_list),
                last_seeded=last_seeded.timestamp.isoformat() if last_seeded else None,
            )

