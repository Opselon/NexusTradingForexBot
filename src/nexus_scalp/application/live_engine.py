"""
Live Execution Engine (v6 Enterprise Rewrite)
============================================

Key Improvements
----------------
- Pre-flight validation (config, artifacts, scaler) before LIVE connection.
- Atomic model+scaler bundle loading and swapping under RLock.
- Strict hot-path: no heavy allocations/IO on tick.
- Async retrain worker with backpressure and cancel-safe lifecycle.
- Regime classifier init backward-compatibility.
- Telegram secrets hardening: supports env override; avoids logging tokens.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import threading
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeOrder,
    TradeProposal,
)
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.features.regime_classifier import MarketRegimeClassifier, MarketRegimeState
from nexus_scalp.features.scalp_features import FeatureVector, ScalpFeatureEngine
from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler
from nexus_scalp.market_data.bar_aggregator import BarAggregator
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.observability.logging import configure_logging, get_logger
from nexus_scalp.observability.telegram_notifier import TelegramNotifier
from nexus_scalp.ports.mt5_port import IMT5Port
from nexus_scalp.risk.risk_engine import RiskEngine
from nexus_scalp.signals.policy import SignalPolicy
from nexus_scalp.signals.rule_matrix import RuleMatrixEngine
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

logger = get_logger("nexus_scalp.application.live_engine")


# -----------------------------
# Small supporting structs
# -----------------------------


@dataclass(frozen=True)
class ScalerBundle:
    mean: np.ndarray | None
    std: np.ndarray | None

    def is_ready(self) -> bool:
        return self.mean is not None and self.std is not None

    def transform_50d(self, x_1x50: np.ndarray) -> np.ndarray:
        if not self.is_ready():
            return x_1x50
        # clip to avoid tail explosion
        return np.clip((x_1x50 - self.mean) / self.std, -5.0, 5.0)


@dataclass(frozen=True)
class ModelBundle:
    model: ScalpNet
    scaler: ScalerBundle
    artifact_path: Path


# -----------------------------
# Live Engine
# -----------------------------


class LiveEngine:
    """
    Production Live Orchestrator for XAUUSD scalping.
    """

    FEATURE_DIM: int = 50
    FEATURE_COLS: tuple[str, ...] = tuple(f"feat_{i}" for i in range(50))

    def __init__(
        self,
        config: AppConfig,
        adapter: IMT5Port,
        audit_repo: AuditRepository | None = None,
        force_fresh_model: bool = False,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.audit = audit_repo or AuditRepository()
        self.force_fresh_model = bool(force_fresh_model)

        self._running: bool = False
        self.server_state: Any = None

        # Thread-safe model bundle swaps (model+scaler together)
        self._bundle_lock = threading.RLock()
        self._bundle: ModelBundle | None = None

        # Trading runtime state
        self._symbol_info: SymbolInfo | None = None
        self._peak_equity: float = 0.0
        self._last_balance: float = 0.0
        self._last_active_position_count: int = 0

        self._consecutive_losses: int = 0
        self._survival_mode_active: bool = False

        # Diagnostics & Heartbeat
        self._last_radar_log_time: float = 0.0

        # Buffers / engines
        symbol = config.execution.symbol
        self.aggregator = BarAggregator(symbol=symbol, timeframe_minutes=1)
        self.feature_engine = ScalpFeatureEngine(symbol=symbol)

        # Module 1: Market Regime Engine (init hardening)
        self.regime_classifier = self._init_regime_classifier(symbol=symbol)

        # Telegram (do NOT trust logged token; allow env override)
        bot_token = os.getenv("NEXUS_TELEGRAM_BOT_TOKEN", config.telegram.bot_token)
        admin_id = os.getenv("NEXUS_TELEGRAM_ADMIN_ID", config.telegram.admin_id)

        self.notifier = TelegramNotifier(
            bot_token=bot_token,
            admin_id=admin_id,
            enabled=config.telegram.enabled,
        )

        # Module 1: Rule Matrix Engine
        self.rule_matrix = RuleMatrixEngine(audit_repo=self.audit)

        # Order/risk/policy
        self.signal_policy = SignalPolicy(
            confidence_threshold=config.model.confidence_threshold,
            cooldown_seconds=4.0,
            rule_matrix=self.rule_matrix,
            algo_config=config.algo,
        )
        self.risk_engine = RiskEngine(
            config=config.risk,
            max_margin_usage_pct=config.risk.max_margin_usage_pct,
            max_allowed_lots=config.risk.max_allowed_lots,
        )
        self.order_manager = OrderLifecycleManager(
            adapter=adapter,
            audit_repo=self.audit,
            notifier=self.notifier,
            rule_matrix=self.rule_matrix,
            algo_config=config.algo,
            # Routing every dispatch through the risk engine enforces dynamic clamps
            # and the free-margin pre-check at the execution boundary.
            risk_engine=self.risk_engine,
        )

        # Online training toolchain
        self.trainer = WalkForwardTrainer(
            artifact_save_path=Path(self.config.model.model_artifact_path),
            random_seed=42,
            active_class_boost=2.5,  # can be increased to 3.5 after calibration
        )

        self.online_labeler = TripleBarrierLabeler(
            take_profit_atr_mult=1.1,
            stop_loss_atr_mult=1.0,
            max_holding_bars=15,
            friction_usd=0.35,
            embargo_bars=3,
        )

        self._rolling_feature_records: deque[dict] = deque(maxlen=4000)
        self._retrain_interval_bars: int = 50
        self._bars_since_last_retrain: int = 0
        self._retrain_task: asyncio.Task | None = None
        self._retrain_inflight: bool = False

        # Hedging tracker to avoid spamming multiple limit orders per ticket
        self._hedged_tickets: set[int] = set()

        # Web / UI Synchronization states to act as single source of truth
        self._last_tick: TickData | None = None
        self._last_fv: FeatureVector | None = None
        self._last_regime_state: MarketRegimeState | None = None
        self._last_probs: torch.Tensor | None = None
        self._last_proposal: TradeProposal | None = None

        # Preload model/scaler bundle (pre-flight)
        model_path = Path(self.config.model.model_artifact_path)
        self._bundle = self._load_or_create_bundle(
            model_path=model_path, force_fresh=self.force_fresh_model
        )

    # -------------------------
    # Public lifecycle
    # -------------------------

    def start(self) -> None:
        """
        Synchronous entrypoint.
        """
        configure_logging(log_level="INFO", json_format=False, log_to_file=True)
        logger.info(
            "Initializing Live Engine",
            symbol=self.config.execution.symbol,
            mode=self.config.execution.mode.value,
        )

        try:
            # Pre-flight validation BEFORE connecting to broker
            self._preflight_or_raise()
        except Exception as e:
            logger.critical("Pre-flight validation failed", error=str(e), exc_info=True)
            try:
                self.notifier.notify_error(
                    "Engine Startup Pre-Flight", f"Startup pre-flight failed: {e}"
                )
                self.notifier.shutdown(timeout=2.0)
            except Exception:
                pass
            raise

        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            if os.name != "nt":
                for sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
                    except NotImplementedError:
                        pass

            loop.run_until_complete(self.run_loop())

        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received. Stopping...")
            self._running = False
        except Exception as e:
            logger.critical("Fatal exception in engine run loop", error=str(e), exc_info=True)
            try:
                self.notifier.notify_error(
                    "Engine Run-Loop Fatal", f"Unhandled critical exception: {e}"
                )
            except Exception:
                pass
            raise

        finally:
            try:
                if loop is not None and not loop.is_closed():
                    loop.run_until_complete(self._shutdown_async())
                    loop.close()
            except Exception:
                pass

    async def stop(self) -> None:
        self._running = False

    async def run_loop(self) -> None:
        """
        Main tick ingestion loop.
        """
        if not self.adapter.connect():
            logger.critical("MT5 connect() failed. Engine shutting down.")
            try:
                self.notifier.notify_error(
                    "MT5 Connectivity",
                    "MT5 connect() failed. Engine shutting down.",
                )
            except Exception:
                pass
            return

        self._running = True
        symbol = self.config.execution.symbol

        account = self.adapter.get_account_info()
        self._symbol_info = self.adapter.get_symbol_info(symbol)

        self._restore_peak_equity(account)
        self._notify_startup(account)

        await self._cold_start_warmup(symbol)

        await self._bootstrap_train_if_ready()

        logger.info(
            "LIVE CONNECTED",
            login=getattr(account, "login", 0) if account else 0,
            balance=getattr(account, "balance", 0.0) if account else 0.0,
            equity=getattr(account, "equity", 0.0) if account else 0.0,
            symbol=symbol,
            digits=self._symbol_info.digits if self._symbol_info else 2,
            model_path=str(self.config.model.model_artifact_path),
        )

        import time

        self._last_tick_processed_time = time.time()

        while self._running:
            try:
                # Tick Stagnation Watchdog: If no ticks/bars are processed for > 15 seconds, trigger healthcheck & reconnect.
                current_time = time.time()
                if (current_time - self._last_tick_processed_time) > 15.0:
                    # Avoid spamming reconnects if connected but market is closed (e.g. weekend or holidays)
                    if not self.adapter.is_connected():
                        logger.warning(
                            "[WARNING] Tick stream stalled and MT5 disconnected. Triggering MT5 adapter healthcheck & auto-reconnect"
                        )
                        try:
                            self.adapter.disconnect()
                            await asyncio.sleep(1.0)
                            self.adapter.connect()
                        except Exception as conn_err:
                            logger.error(
                                "Error during auto-reconnect in watchdog",
                                error=str(conn_err),
                                exc_info=True,
                            )
                    else:
                        logger.info("[WATCHDOG] Tick stream quiet. MT5 connection remains active.")
                    self._last_tick_processed_time = time.time()

                live_account = self.adapter.get_account_info()
                tick = self.adapter.get_last_tick(symbol)

                if live_account is None or tick is None:
                    await asyncio.sleep(0.2)
                    continue

                if self._symbol_info is None:
                    self._symbol_info = self.adapter.get_symbol_info(symbol)

                self._process_tick_pipeline(tick=tick, account=live_account)
                self._last_tick_processed_time = time.time()
                await asyncio.sleep(0.05)

            except Exception as e:
                logger.error("Error in live loop", error=str(e), exc_info=True)
                try:
                    self.notifier.notify_error("Real-Time Execution Loop", str(e))
                except Exception:
                    pass
                await asyncio.sleep(1.0)

        await self._shutdown_async()

    async def _shutdown_async(self) -> None:
        # Cancel retrain task safely
        try:
            if self._retrain_task and not self._retrain_task.done():
                self._retrain_task.cancel()
                with contextlib.suppress(Exception):
                    await self._retrain_task
        except Exception:
            pass

        try:
            self.adapter.disconnect()
        except Exception:
            pass

        try:
            self.audit.close()
        except Exception:
            pass

        try:
            self.notifier.notify_shutdown(reason="Engine Stopped")
        except Exception:
            pass

        logger.info("Engine shutdown complete.")

    # -------------------------
    # Preflight
    # -------------------------

    def _preflight_or_raise(self) -> None:
        """
        Validate artifacts and critical configuration before live.
        """
        model_path = Path(self.config.model.model_artifact_path)
        if not model_path.parent.exists():
            raise RuntimeError(f"Model directory missing: {model_path.parent}")

        # Model file can be created on cold start; scaler may not exist (allowed).
        if model_path.exists():
            logger.info("Model artifact present", path=str(model_path))
        else:
            logger.warning("Model artifact not found; will initialize fresh", path=str(model_path))

        # Validate schema contract
        if self.config.model.feature_schema_version != "v1.0":
            logger.warning(
                "Feature schema version unexpected",
                version=self.config.model.feature_schema_version,
            )

        # Telegram hardening: never log token
        if self.config.telegram.enabled and (
            not os.getenv("NEXUS_TELEGRAM_BOT_TOKEN") and not self.config.telegram.bot_token
        ):
            logger.warning("Telegram enabled but token missing (env override recommended)")

    # -------------------------
    # Init helpers
    # -------------------------

    def _init_regime_classifier(self, symbol: str) -> MarketRegimeClassifier:
        """
        Backward-compatible init wrapper.
        Supports both:
            - new classifier args: spread_chop_enter_usd, spread_chop_exit_usd
            - old arg: max_allowed_spread_usd
        """
        # Prefer new API, but keep compatibility with your current callsite semantics.
        try:
            return MarketRegimeClassifier(
                symbol=symbol,
                spread_chop_enter_usd=0.50,
                spread_chop_exit_usd=0.40,
                min_regime_hold_sec=4.0,
                switch_prob_margin=0.10,
            )
        except TypeError:
            # Fallback to legacy signature
            return MarketRegimeClassifier(
                symbol=symbol,
                max_allowed_spread_usd=0.50,
            )

    # -------------------------
    # Model / scaler bundle
    # -------------------------

    def _load_or_create_bundle(self, model_path: Path, force_fresh: bool) -> ModelBundle:
        model = self._load_or_initialize_model_weights(
            model_path=model_path, force_fresh=force_fresh
        )
        scaler = self._load_scaler_artifacts(model_path=model_path)
        return ModelBundle(model=model, scaler=scaler, artifact_path=model_path)

    def _load_or_initialize_model_weights(self, model_path: Path, force_fresh: bool) -> ScalpNet:
        """
        Loads model.pt if present, validates 50D contract, otherwise creates and saves.
        """
        model = ScalpNet(num_features=self.FEATURE_DIM, num_classes=4)
        model.eval()

        if model_path.exists() and not force_fresh:
            state_dict = torch.load(model_path, map_location="cpu")

            expected = model.input_projection.weight.shape
            loaded = state_dict.get("input_projection.weight", torch.empty(0)).shape
            if loaded != expected:
                backup_path = model_path.with_suffix(".pt.corrupt")
                logger.critical(
                    "Checkpoint dimension mismatch; quarantining",
                    expected=str(expected),
                    loaded=str(loaded),
                    backup=str(backup_path),
                )
                try:
                    model_path.rename(backup_path)
                except Exception:
                    pass
                raise RuntimeError(
                    f"Checkpoint dimension mismatch: expected {expected}, got {loaded}"
                )

            model.load_state_dict(state_dict)
            logger.info("Loaded model weights", path=str(model_path))
            return model

        logger.info("Initializing fresh model weights", path=str(model_path))
        self._save_model_weights_atomic(model, model_path)
        return model

    def _load_scaler_artifacts(self, model_path: Path) -> ScalerBundle:
        scaler_path = model_path.with_suffix(".scaler.npz")
        if not scaler_path.exists():
            logger.info("Scaler artifact missing (cold-start acceptable)", path=str(scaler_path))
            return ScalerBundle(mean=None, std=None)

        try:
            data = np.load(scaler_path)
            mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
            std = np.asarray(data["std"], dtype=np.float32).reshape(-1)

            if mean.shape[0] != self.FEATURE_DIM or std.shape[0] != self.FEATURE_DIM:
                raise RuntimeError(
                    f"Scaler dim invalid: mean{mean.shape} std{std.shape} expected ({self.FEATURE_DIM},)"
                )

            logger.info(
                "Loaded scaler artifacts successfully",
                path=str(scaler_path),
                mean_shape=mean.shape,
                std_shape=std.shape,
            )
            return ScalerBundle(mean=mean, std=std)

        except Exception as err:
            logger.warning(
                "Failed to load scaler; fallback to raw features",
                error=str(err),
                path=str(scaler_path),
            )
            return ScalerBundle(mean=None, std=None)

    def _save_model_weights_atomic(self, model: ScalpNet, model_path: Path) -> None:
        """Saves current PyTorch model weights state_dict atomically to disk with thread lock and logging."""
        with self._bundle_lock:
            try:
                model_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = model_path.with_suffix(".pt.tmp")

                # Detach state dict to CPU before saving for HFT thread safety
                cpu_state = {k: v.detach().cpu() for k, v in model.state_dict().items()}
                torch.save(cpu_state, tmp)
                tmp.replace(model_path)

                logger.info(
                    "Saved PyTorch model weights artifact atomically to disk",
                    path=str(model_path),
                    tensor_layers=len(cpu_state),
                )
            except Exception as err:
                logger.error(
                    "Failed to save atomic model weights to disk",
                    error=str(err),
                    path=str(model_path),
                )

    # -------------------------
    # Warmup + bootstrap training
    # -------------------------

    async def _cold_start_warmup(self, symbol: str) -> None:
        logger.info("Cold-start warmup: fetching 1200 M1 bars...")
        hist_bars = (
            self.adapter.get_historical_bars(symbol=symbol, timeframe="M1", count=1200) or []
        )

        for b in hist_bars:
            self.aggregator._completed_bars.append(b)

            completed = self.aggregator.get_completed_bars()
            if len(completed) < 55:
                continue

            bar_time = getattr(b, "timestamp", getattr(b, "time", datetime.now(UTC)))
            synthetic_tick = TickData(
                symbol=symbol,
                timestamp=bar_time,
                bid=b.close,
                ask=b.close + 0.20,
                volume=b.tick_volume,
            )

            # Slice window to last 300 bars for O(1) feature calculation speed
            bars_window = completed[-300:] if len(completed) > 300 else completed
            fv = self.feature_engine.compute_from_bars(bars_window, synthetic_tick)
            x50 = self._validate_50d_tensor(fv.to_tensor_input(), context="cold_start_warmup")
            record = {f"feat_{i}": float(x50[i]) for i in range(self.FEATURE_DIM)}
            record.update(
                close=b.close, high=b.high, low=b.low, open=b.open, spread=0.20, atr_m1=fv.atr_m1
            )
            self._rolling_feature_records.append(record)

        logger.info("Warmup complete", buffer_size=len(self._rolling_feature_records))

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
            for b in completed_bars[-250:]:
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
            logger.info("Cold-start SMC visual overlays successfully bridged to server state!")

    async def _bootstrap_train_if_ready(self) -> None:
        if len(self._rolling_feature_records) < 300:
            return

        logger.info(
            "BOOTSTRAP: initial online fine-tune starting...",
            rows=len(self._rolling_feature_records),
        )
        df_hist = pl.DataFrame(list(self._rolling_feature_records))
        df_labeled = self.online_labeler.label_dataframe(df_hist)

        feature_cols = list(self.FEATURE_COLS)
        logger.info("Training features", feature_cols=feature_cols)

        with self._bundle_lock:
            bundle = self._bundle
        if bundle is None:
            return

        # trainer returns updated model (assumed)
        updated_model = self.trainer.fine_tune_online(
            model=bundle.model,
            recent_df=df_labeled,
            feature_cols=feature_cols,
            epochs=5,
            learning_rate=1e-4,
            max_holding_bars=15,
        )
        updated_model.eval()

        # Reload scaler after training (if trainer writes it)
        scaler = self._load_scaler_artifacts(bundle.artifact_path)

        # Atomic swap bundle
        with self._bundle_lock:
            self._bundle = ModelBundle(
                model=updated_model, scaler=scaler, artifact_path=bundle.artifact_path
            )

        self._run_model_diagnostics_and_summary(df_labeled=df_labeled, feature_cols=feature_cols)

    # -------------------------
    # Hot-path tick pipeline
    # -------------------------

    def _process_tick_pipeline(self, tick: TickData, account: AccountInfo) -> None:
        try:
            # Synchronize the live hot-swapped AlgoConfig on every single tick pulse
            self.signal_policy.algo_config = self.config.algo
            self.order_manager.algo_config = self.config.algo
            self.risk_engine.min_risk_reward_ratio = self.config.algo.min_risk_reward_ratio
            self.risk_engine.min_rr_high_confidence = getattr(
                self.config.algo, "min_rr_high_confidence", 1.2
            )
            self.risk_engine.high_confidence_threshold = getattr(
                self.config.algo, "high_confidence_threshold", 0.70
            )

            is_new_bar = self.aggregator.process_tick(tick)

            # cap bars (O(1) amortized)
            if len(self.aggregator._completed_bars) > 4000:
                self.aggregator._completed_bars = self.aggregator._completed_bars[-4000:]

            completed_bars = self.aggregator.get_completed_bars()
            fv = self.feature_engine.compute_from_bars(
                completed_bars=completed_bars, current_tick=tick
            )

            if is_new_bar and completed_bars:
                self._on_new_bar(tick=tick, fv=fv, last_bar=completed_bars[-1])

            # Regime state (Module 1)
            regime_state: MarketRegimeState = self.regime_classifier.classify_tick(
                current_tick=tick,
                is_macro_news_window=False,
            )

            # Manage open positions
            active_positions = self.order_manager.manage_active_positions(
                symbol=tick.symbol,
                current_tick=tick,
                feature_vector=fv,
                symbol_info=self._symbol_info,
                account=account,
            )
            current_pos_count = len(active_positions)

            # Inference
            probs = self._infer_probabilities(fv=fv)

            # Heartbeat radar logging: On EVERY M1 Bar completion or every 10 seconds of active ticks, force log.
            import time

            current_time = time.time()
            force_log = False
            if is_new_bar or (current_time - self._last_radar_log_time) >= 10.0:
                force_log = True
                self._last_radar_log_time = current_time

            # Policy
            proposal = self.signal_policy.evaluate_probabilities(
                probabilities=probs,
                current_tick=tick,
                feature_vector=fv,
                regime_state=regime_state,
                survival_mode=self._survival_mode_active,
                force_log=force_log,
                order_manager=self.order_manager,
            )
            self.audit.log_signal(proposal)

            # Update synchronization properties for the Web backend
            self._last_tick = tick
            self._last_fv = fv
            self._last_regime_state = regime_state
            self._last_probs = probs
            self._last_proposal = proposal

            # Extract and update real SMC overlays for the live chart canvas
            real_overlays = self.signal_policy.extract_live_chart_overlays(
                completed_bars=completed_bars, atr_val=fv.atr_m1
            )
            if hasattr(self, "server_state") and self.server_state is not None:
                bars_list = []
                for b in completed_bars[-250:]:
                    bars_list.append(
                        {
                            "time": b.timestamp.isoformat(),
                            "open": b.open,
                            "high": b.high,
                            "low": b.low,
                            "close": b.close,
                            "volume": b.tick_volume,
                            "is_complete": True,
                        }
                    )
                forming_bar = self.aggregator.get_current_forming_bar()
                if forming_bar:
                    bars_list.append(
                        {
                            "time": forming_bar.timestamp.isoformat(),
                            "open": forming_bar.open,
                            "high": forming_bar.high,
                            "low": forming_bar.low,
                            "close": forming_bar.close,
                            "volume": forming_bar.tick_volume,
                            "is_complete": False,
                        }
                    )
                self.server_state.update_live_visuals(bars_list, real_overlays)

            policy_decision = proposal
            if policy_decision.action != ActionType.NO_TRADE:
                # ---------------------------------------------------------------
                # AI POSITION REVERSAL: close-then-flip, never stack
                # ---------------------------------------------------------------
                if getattr(policy_decision, "is_ai_reversal", False) or (
                    policy_decision.action == ActionType.CLOSE_POSITION
                    and "AI_REVERSAL_SIGNAL" in (policy_decision.reason_code or "")
                ):
                    reversal_volume = 0.0
                    if self._symbol_info:
                        reversal_volume = self.risk_engine.calculate_volume(
                            entry=policy_decision.proposed_entry,
                            sl=policy_decision.stop_loss,
                            tp=policy_decision.take_profit,
                            account=account,
                            symbol_info=self._symbol_info,
                        )
                        reversal_volume = self.risk_engine.get_clamped_position_size(
                            volume=reversal_volume,
                            account=account,
                            symbol_info=self._symbol_info,
                        )

                    success = self.order_manager.execute_ai_reversal(
                        decision=policy_decision,
                        volume=reversal_volume,
                        current_tick=tick,
                        symbol_info=self._symbol_info,
                    )
                    logger.info(
                        f"[info] AI REVERSAL EXECUTED ticket={policy_decision.ticket} "
                        f"new_action={getattr(policy_decision.reversal_action, 'value', None)} "
                        f"volume={reversal_volume} success={success}"
                    )

                # FOR NEW ENTRY SIGNALS
                elif policy_decision.action in (
                    ActionType.BUY,
                    ActionType.SELL,
                    ActionType.BUY_MARKET,
                    ActionType.SELL_MARKET,
                    ActionType.BUY_LIMIT,
                    ActionType.SELL_LIMIT,
                    ActionType.BUY_STOP,
                    ActionType.SELL_STOP,
                ):
                    if self._symbol_info:
                        dynamic_volume = self.risk_engine.calculate_volume(
                            entry=policy_decision.proposed_entry,
                            sl=policy_decision.stop_loss,
                            tp=policy_decision.take_profit,
                            account=account,
                            symbol_info=self._symbol_info,
                        )
                        # Guarantee that the lot size respects the safety clamp under any mathematical condition
                        dynamic_volume = self.risk_engine.get_clamped_position_size(
                            volume=dynamic_volume,
                            account=account,
                            symbol_info=self._symbol_info,
                        )
                        success = self.order_manager.dispatch_order(policy_decision, dynamic_volume)
                        logger.info(
                            f"[info] DISPATCH ORDER action={policy_decision.action.value} price={policy_decision.proposed_entry} volume={dynamic_volume}"
                        )

                        if success:
                            risk_usd = account.equity * (
                                self.config.risk.risk_per_trade_pct / 100.0
                            )
                            try:
                                mapped_order_type = self.risk_engine._map_action_to_order_type(
                                    policy_decision.action
                                )
                                order_obj = TradeOrder(
                                    order_id=policy_decision.request_id,
                                    symbol=policy_decision.symbol,
                                    order_type=mapped_order_type,
                                    volume=dynamic_volume,
                                    price=policy_decision.proposed_entry,
                                    stop_loss=policy_decision.stop_loss,
                                    take_profit=policy_decision.take_profit,
                                    magic_number=888101,
                                    comment="NSE_HFT_SIZED",
                                )
                                self.notifier.notify_order_opened(
                                    order=order_obj,
                                    risk_usd=risk_usd,
                                    callback=lambda msg_id: (
                                        self.order_manager.register_order_message(
                                            order_obj.order_id, msg_id
                                        )
                                        if msg_id
                                        else None
                                    ),
                                )
                            except Exception:
                                pass
                        else:
                            # Dispatch failed! Clear the price lock immediately so bot is not locked out of trading!
                            self.signal_policy.last_order_price = None
                            self.signal_policy.last_order_time = None
                            self.signal_policy._last_active_direction = None
                            self.signal_policy._last_active_direction_time = None
                            self.signal_policy._last_executed_price = 0.0

                # FOR POSITION LIFECYCLE ACTIONS
                elif policy_decision.action in (
                    ActionType.CLOSE_POSITION,
                    ActionType.PARTIAL_CLOSE,
                    ActionType.MODIFY_SL_TP,
                    ActionType.CANCEL_ORDER,
                ):
                    self.order_manager.execute_lifecycle_action(policy_decision)
                    ticket = getattr(policy_decision, "ticket", 0) or 0
                    logger.info(
                        f"[info] DISPATCH LIFECYCLE ACTION action={policy_decision.action.value} ticket={ticket}"
                    )

            # Evaluate intelligent hedging / counter-position policy
            self._evaluate_hedging_policy(
                active_positions=active_positions,
                tick=tick,
                probs=probs,
                regime_state=regime_state,
                fv=fv,
                account=account,
            )

            # Equity / drawdown tracking + audit
            self._update_survival_state(account=account, current_pos_count=current_pos_count)
            self.audit.log_account_snapshot(account=account, peak_equity=self._peak_equity)
            # Keep the order manager's account snapshot fresh so closed-trade autopsy rows
            # carry accurate balance/equity/drawdown values.
            self.order_manager.update_account_snapshot(
                account=account, peak_equity=self._peak_equity
            )

        except Exception as pipeline_err:
            logger.error(
                "Silent recovery: exception caught in hot-path tick processing pipeline",
                error=str(pipeline_err),
                exc_info=True,
            )

    def _evaluate_hedging_policy(
        self,
        active_positions: list[Position],
        tick: TickData,
        probs: torch.Tensor,
        regime_state: MarketRegimeState | None,
        fv: FeatureVector,
        account: AccountInfo,
    ) -> None:
        """
        Intelligent Hedging / Counter-Position Policy (PyTorch & Regime-Driven).
        """
        import time

        from nexus_scalp.domain.enums import ActionType, OrderType
        from nexus_scalp.domain.models import TradeProposal
        from nexus_scalp.features.regime_classifier import RegimeType

        # Garbage collect closed tickets
        active_tickets = {pos.ticket for pos in active_positions}
        self._hedged_tickets &= active_tickets

        atr = max(self.order_manager._safe_feature_float(fv, "atr_m1", 1.50), 0.50)
        probs_list = probs.squeeze().tolist()
        if not isinstance(probs_list, list):
            probs_list = [probs_list]
        prob_buy = probs_list[1] if len(probs_list) > 1 else 0.0
        prob_sell = probs_list[2] if len(probs_list) > 2 else 0.0

        for pos in active_positions:
            # Only evaluate positions currently in drawdown
            if pos.profit >= 0.0:
                continue

            if pos.ticket in self._hedged_tickets:
                continue

            # Evaluate whether hold score has dropped below threshold or volatility has shifted
            hold_score = self.order_manager._hold_score_tracker.get(pos.ticket, 100)

            hold_score_dropped = hold_score < 50
            volatility_shifted = False
            if regime_state and regime_state.regime_type == RegimeType.VOLATILITY_EXPANSION:
                if hold_score < 75 or pos.profit < -0.50:
                    volatility_shifted = True

            if not (hold_score_dropped or volatility_shifted):
                continue

            # Prevent per-tick log spam if active position capacity is already full
            symbol_positions = [p for p in active_positions if p.symbol == pos.symbol]
            if len(symbol_positions) >= self.config.risk.max_concurrent_positions:
                self._hedged_tickets.add(pos.ticket)
                continue

            logger.info(
                "Hedging trigger met for position in drawdown",
                ticket=pos.ticket,
                hold_score=hold_score,
                volatility_shifted=volatility_shifted,
                pnl=pos.profit,
            )

            # Determine whether to hedge or average using PyTorch model predictions and regime indicators
            if pos.type == OrderType.BUY:
                if prob_buy >= prob_sell or (
                    regime_state and regime_state.regime_type == RegimeType.RANGING_MEAN_REVERSION
                ):
                    is_buy_limit = True
                    target_entry = round(tick.bid - atr * 1.0, 2)
                    stop_loss = round(target_entry - atr * 1.5, 2)
                    take_profit = round(pos.price_open, 2)
                else:
                    is_buy_limit = False
                    target_entry = round(tick.ask + atr * 1.0, 2)
                    stop_loss = round(target_entry + atr * 1.5, 2)
                    take_profit = round(tick.bid - atr * 1.5, 2)
            elif prob_sell >= prob_buy or (
                regime_state and regime_state.regime_type == RegimeType.RANGING_MEAN_REVERSION
            ):
                is_buy_limit = False
                target_entry = round(tick.ask + atr * 1.0, 2)
                stop_loss = round(target_entry + atr * 1.5, 2)
                take_profit = round(pos.price_open, 2)
            else:
                is_buy_limit = True
                target_entry = round(tick.bid - atr * 1.0, 2)
                stop_loss = round(target_entry - atr * 1.5, 2)
                take_profit = round(tick.ask + atr * 1.5, 2)

            action = ActionType.BUY_LIMIT if is_buy_limit else ActionType.SELL_LIMIT

            proposal = TradeProposal(
                request_id=f"hedge_{pos.ticket}_{int(time.time())}",
                symbol=pos.symbol,
                generated_at=tick.timestamp,
                action=action,
                confidence=float(max(prob_buy, prob_sell)),
                proposed_entry=float(target_entry),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                risk_reward_ratio=1.35,
                reason_code=f"HEDGE_TRIGGER_{action.name}_SCORE_{hold_score}",
            )

            if self._symbol_info:
                hedge_order = self.risk_engine.evaluate_proposal(
                    proposal=proposal,
                    account=account,
                    symbol_info=self._symbol_info,
                    active_positions=active_positions,
                    current_tick=tick,
                    regime_state=regime_state,
                    atr=atr,
                )

                if hedge_order:
                    logger.info(
                        "Dispatching intelligent hedging limit order",
                        original_ticket=pos.ticket,
                        action=hedge_order.order_type.value,
                        volume=hedge_order.volume,
                        price=hedge_order.price,
                    )
                    success = self.order_manager.execute_order(hedge_order)
                    if success:
                        self._hedged_tickets.add(pos.ticket)
                        try:
                            self.notifier.notify_info(
                                "Intelligent Hedging Activated",
                                f"Position {pos.ticket} is in drawdown (Hold Score: {hold_score}). "
                                f"Placed hedging order {hedge_order.order_type.value} of {hedge_order.volume} lots at {hedge_order.price}.",
                            )
                        except Exception:
                            pass

    def _on_new_bar(self, tick: TickData, fv, last_bar) -> None:
        x50 = self._validate_50d_tensor(fv.to_tensor_input(), context="new_bar_record")
        rec = {f"feat_{i}": float(x50[i]) for i in range(self.FEATURE_DIM)}
        rec.update(
            close=last_bar.close,
            high=last_bar.high,
            low=last_bar.low,
            open=last_bar.open,
            spread=(tick.ask - tick.bid),
            atr_m1=fv.atr_m1,
        )
        self._rolling_feature_records.append(rec)
        self._bars_since_last_retrain += 1

        if (
            self._bars_since_last_retrain >= self._retrain_interval_bars
            and len(self._rolling_feature_records) >= 300
            and not self._retrain_inflight
        ):
            try:
                loop = asyncio.get_running_loop()
                self._retrain_task = loop.create_task(self._trigger_async_online_fine_tune())
            except RuntimeError:
                pass

    def _infer_probabilities(self, fv) -> torch.Tensor:
        x50 = self._validate_50d_tensor(fv.to_tensor_input(), context="live_inference")
        x_np = np.array(x50, dtype=np.float32).reshape(1, -1)

        with self._bundle_lock:
            bundle = self._bundle
        if bundle is None:
            raise RuntimeError("Model bundle not initialized")

        x_np = bundle.scaler.transform_50d(x_np)
        x = torch.tensor(x_np, dtype=torch.float32)
        x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)

        bundle.model.eval()
        with torch.inference_mode():
            return bundle.model(x)

    # -------------------------
    # Async retraining worker
    # -------------------------

    async def _trigger_async_online_fine_tune(self) -> None:
        if self._retrain_inflight:
            return
        self._retrain_inflight = True
        try:
            logger.info("ASYNC RETRAIN START", buffer_size=len(self._rolling_feature_records))

            df = pl.DataFrame(list(self._rolling_feature_records))
            df_labeled = self.online_labeler.label_dataframe(df)
            feature_cols = list(self.FEATURE_COLS)

            with self._bundle_lock:
                bundle = self._bundle
            if bundle is None:
                return

            # Run training off loop thread
            updated_model = await asyncio.to_thread(
                self.trainer.fine_tune_online,
                bundle.model,
                df_labeled,
                feature_cols,
                3,  # epochs
                1e-4,  # lr
                15,  # max_holding_bars
            )
            updated_model.eval()

            # Refresh scaler + persist weights
            scaler = self._load_scaler_artifacts(bundle.artifact_path)
            self._save_model_weights_atomic(updated_model, bundle.artifact_path)

            with self._bundle_lock:
                self._bundle = ModelBundle(
                    model=updated_model, scaler=scaler, artifact_path=bundle.artifact_path
                )

            self._bars_since_last_retrain = 0
            logger.info("ASYNC RETRAIN SUCCESS")

        except Exception as err:
            logger.error("Async retrain failed", error=str(err), exc_info=True)

        finally:
            self._retrain_inflight = False

    # -------------------------
    # Diagnostics
    # -------------------------

    def _run_model_diagnostics_and_summary(
        self, df_labeled: pl.DataFrame, feature_cols: list[str]
    ) -> None:
        logger.info("=== MODEL DIAGNOSTICS ===")

        with self._bundle_lock:
            bundle = self._bundle
        if bundle is None:
            logger.warning("No model bundle for diagnostics")
            return

        # Test 1: forward pass sanity
        sample_x_np = (
            df_labeled.select(feature_cols).tail(20).to_numpy().astype(np.float32, copy=False)
        )
        sample_x_np = bundle.scaler.transform_50d(sample_x_np)
        x = torch.tensor(sample_x_np, dtype=torch.float32)
        x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)

        with torch.inference_mode():
            p = bundle.model(x)
        test1_pass = not (torch.isnan(p).any() or torch.isinf(p).any())

        # Test 2/3: calibrated class distribution
        test_df = df_labeled.tail(100)
        test_x_np = test_df.select(feature_cols).to_numpy().astype(np.float32, copy=False)
        test_x_np = bundle.scaler.transform_50d(test_x_np)
        tx = torch.tensor(test_x_np, dtype=torch.float32)
        tx = torch.nan_to_num(tx, nan=0.0, posinf=1.0, neginf=-1.0)

        with torch.inference_mode():
            probs = bundle.model(tx).cpu().numpy()

        buy_probs = probs[:, 1]
        sell_probs = probs[:, 2]
        threshold = float(self.config.model.confidence_threshold)

        raw_preds = np.argmax(probs[:, :3], axis=1)
        preds = np.zeros(len(probs), dtype=int)
        for i in range(len(probs)):
            c = raw_preds[i]
            if c == 1 and buy_probs[i] >= threshold:
                preds[i] = 1
            elif c == 2 and sell_probs[i] >= threshold:
                preds[i] = 2
            else:
                preds[i] = 0

        total = len(preds)
        buy_pct = float(np.sum(preds == 1) / max(total, 1) * 100.0)
        sell_pct = float(np.sum(preds == 2) / max(total, 1) * 100.0)
        no_trade_pct = float(np.sum(preds == 0) / max(total, 1) * 100.0)

        test3_pass = buy_pct < 85.0 and sell_pct < 85.0

        logger.info(
            "MODEL SUMMARY",
            test1_tensor_sanity="PASS" if test1_pass else "FAIL",
            class_dist=f"BUY {buy_pct:.1f}% | SELL {sell_pct:.1f}% | NO_TRADE {no_trade_pct:.1f}%",
            threshold=f"{threshold:.2f}",
            status="HEALTHY" if (test1_pass and test3_pass) else "WARNING",
        )
        logger.info("=======================")

    # -------------------------
    # Risk/survival tracking
    # -------------------------

    def _restore_peak_equity(self, account: AccountInfo | None) -> None:
        last_snapshot = self.audit.get_last_account_snapshot()
        if last_snapshot and "peak_equity" in last_snapshot:
            self._peak_equity = float(last_snapshot["peak_equity"])
            logger.info(
                "Restored peak equity from audit DB", peak_equity=f"{self._peak_equity:.2f}"
            )
        elif account:
            self._peak_equity = float(account.equity)

        if account:
            self._last_balance = float(account.balance)

    def _notify_startup(self, account: AccountInfo | None) -> None:
        if not account:
            return
        try:
            self.notifier.notify_startup(
                symbol=self.config.execution.symbol,
                mode=self.config.execution.mode.value,
                balance=account.balance,
                equity=account.equity,
            )
        except Exception:
            pass

    def _update_survival_state(self, account: AccountInfo, current_pos_count: int) -> None:
        # Withdrawal adjustment heuristic retained
        if self._last_balance > 0.0:
            balance_delta = account.balance - self._last_balance
            no_trade_was_closed = current_pos_count >= self._last_active_position_count
            if (
                balance_delta < 0.0
                and no_trade_was_closed
                and abs(balance_delta) > (account.equity * 0.02)
            ):
                self._peak_equity += balance_delta
                logger.info(
                    "Withdrawal detected; adjusted peak equity",
                    peak_equity=f"{self._peak_equity:.2f}",
                )

        self._last_balance = float(account.balance)
        self._last_active_position_count = int(current_pos_count)

        if account.equity > self._peak_equity:
            self._peak_equity = float(account.equity)
            self._consecutive_losses = 0
            if self._survival_mode_active:
                self._survival_mode_active = False
                try:
                    self.notifier.notify_survival_mode_changed(active=False, drawdown_pct=0.0)
                except Exception:
                    pass

        elif account.equity < self._peak_equity and self._peak_equity > 0:
            drawdown_pct = ((self._peak_equity - account.equity) / self._peak_equity) * 100.0
            if (
                drawdown_pct > (self.config.risk.max_account_drawdown_pct * 0.5)
                and not self._survival_mode_active
            ):
                self._survival_mode_active = True
                logger.warning("SURVIVAL MODE ON", drawdown_pct=round(drawdown_pct, 2))
                try:
                    self.notifier.notify_survival_mode_changed(
                        active=True, drawdown_pct=drawdown_pct
                    )
                except Exception:
                    pass

            if drawdown_pct > self.config.risk.max_account_drawdown_pct:
                logger.critical("MAX DRAWDOWN EXCEEDED; HALTING", dd_pct=round(drawdown_pct, 2))
                try:
                    self.notifier.notify_kill_switch_activated(
                        f"Max Drawdown Exceeded ({drawdown_pct:.2f}%)"
                    )
                except Exception:
                    pass
                self._running = False

    # -------------------------
    # 50D contract validation
    # -------------------------

    @classmethod
    def _validate_50d_tensor(cls, features: Sequence[float], context: str) -> list[float]:
        if len(features) != cls.FEATURE_DIM:
            raise RuntimeError(
                f"50D feature contract violation in {context}: expected {cls.FEATURE_DIM}, got {len(features)}"
            )

        out: list[float] = []
        for idx, val in enumerate(features):
            try:
                f = float(val)
            except Exception:
                logger.warning(
                    "Non-numeric feature sanitized", context=context, feature=f"feat_{idx}"
                )
                f = 0.0

            if not np.isfinite(f):
                logger.warning(
                    "Non-finite feature sanitized", context=context, feature=f"feat_{idx}"
                )
                f = 0.0

            out.append(float(np.clip(f, -3.0, 3.0)))
        return out
