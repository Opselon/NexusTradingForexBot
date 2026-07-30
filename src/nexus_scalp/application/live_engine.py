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
import contextlib
import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import threading
from typing import Deque, Dict, List, Optional, Sequence, Tuple

import numpy as np
import polars as pl
import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import AccountInfo, SymbolInfo, TickData, TradeOrder
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.features.regime_classifier import MarketRegimeClassifier, MarketRegimeState
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler
from nexus_scalp.market_data.bar_aggregator import BarAggregator
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.observability.logging import configure_logging, get_logger
from nexus_scalp.observability.telegram_notifier import TelegramNotifier
from nexus_scalp.ports.mt5_port import IMT5Port
from nexus_scalp.risk.risk_engine import RiskEngine
from nexus_scalp.signals.policy import SignalPolicy
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

logger = get_logger("nexus_scalp.application.live_engine")


# -----------------------------
# Small supporting structs
# -----------------------------

@dataclass(frozen=True)
class ScalerBundle:
    mean: Optional[np.ndarray]
    std: Optional[np.ndarray]

    def is_ready(self) -> bool:
        return self.mean is not None and self.std is not None

    def transform_40d(self, x_1x40: np.ndarray) -> np.ndarray:
        if not self.is_ready():
            return x_1x40
        # clip to avoid tail explosion
        return np.clip((x_1x40 - self.mean) / self.std, -5.0, 5.0)


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

    FEATURE_DIM: int = 40
    FEATURE_COLS: Tuple[str, ...] = tuple(f"feat_{i}" for i in range(40))

    def __init__(
        self,
        config: AppConfig,
        adapter: IMT5Port,
        audit_repo: Optional[AuditRepository] = None,
        force_fresh_model: bool = False,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.audit = audit_repo or AuditRepository()
        self.force_fresh_model = bool(force_fresh_model)

        self._running: bool = False

        # Thread-safe model bundle swaps (model+scaler together)
        self._bundle_lock = threading.RLock()
        self._bundle: Optional[ModelBundle] = None

        # Trading runtime state
        self._symbol_info: Optional[SymbolInfo] = None
        self._peak_equity: float = 0.0
        self._last_balance: float = 0.0
        self._last_active_position_count: int = 0

        self._consecutive_losses: int = 0
        self._survival_mode_active: bool = False

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

        # Order/risk/policy
        self.signal_policy = SignalPolicy(
            confidence_threshold=config.model.confidence_threshold,
            cooldown_seconds=4.0,
        )
        self.risk_engine = RiskEngine(
            config=config.risk,
            max_margin_usage_pct=config.risk.max_margin_usage_pct,
            max_allowed_lots=config.risk.max_allowed_lots,
        )
        self.order_manager = OrderLifecycleManager(adapter=adapter, audit_repo=self.audit)

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

        self._rolling_feature_records: Deque[dict] = deque(maxlen=1000)
        self._retrain_interval_bars: int = 50
        self._bars_since_last_retrain: int = 0
        self._retrain_task: Optional[asyncio.Task] = None
        self._retrain_inflight: bool = False

        # Preload model/scaler bundle (pre-flight)
        model_path = Path(self.config.model.model_artifact_path)
        self._bundle = self._load_or_create_bundle(model_path=model_path, force_fresh=self.force_fresh_model)

    # -------------------------
    # Public lifecycle
    # -------------------------

    def start(self) -> None:
        """
        Synchronous entrypoint.
        """
        configure_logging(log_level="INFO", json_format=False, log_to_file=True)
        logger.info("Initializing Live Engine", symbol=self.config.execution.symbol, mode=self.config.execution.mode.value)

        # Pre-flight validation BEFORE connecting to broker
        self._preflight_or_raise()

        loop: Optional[asyncio.AbstractEventLoop] = None
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

        while self._running:
            try:
                live_account = self.adapter.get_account_info()
                tick = self.adapter.get_last_tick(symbol)

                if live_account is None or tick is None:
                    logger.warning("Transient MT5 drop (None account/tick). Retrying...")
                    await asyncio.sleep(0.2)
                    continue

                if self._symbol_info is None:
                    self._symbol_info = self.adapter.get_symbol_info(symbol)

                self._process_tick_pipeline(tick=tick, account=live_account)
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
            logger.warning("Feature schema version unexpected", version=self.config.model.feature_schema_version)

        # Telegram hardening: never log token
        if self.config.telegram.enabled and (not os.getenv("NEXUS_TELEGRAM_BOT_TOKEN") and not self.config.telegram.bot_token):
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
                spread_chop_enter_usd=0.35,
                spread_chop_exit_usd=0.30,
                min_regime_hold_sec=7.0,
                switch_prob_margin=0.12,
            )
        except TypeError:
            # Fallback to legacy signature
            return MarketRegimeClassifier(
                symbol=symbol,
                max_allowed_spread_usd=0.35,
            )

    # -------------------------
    # Model / scaler bundle
    # -------------------------

    def _load_or_create_bundle(self, model_path: Path, force_fresh: bool) -> ModelBundle:
        model = self._load_or_initialize_model_weights(model_path=model_path, force_fresh=force_fresh)
        scaler = self._load_scaler_artifacts(model_path=model_path)
        return ModelBundle(model=model, scaler=scaler, artifact_path=model_path)

    def _load_or_initialize_model_weights(self, model_path: Path, force_fresh: bool) -> ScalpNet:
        """
        Loads model.pt if present, validates 40D contract, otherwise creates and saves.
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
                raise RuntimeError(f"Checkpoint dimension mismatch: expected {expected}, got {loaded}")

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

            logger.info("Loaded scaler artifacts successfully", path=str(scaler_path), mean_shape=mean.shape, std_shape=std.shape)
            return ScalerBundle(mean=mean, std=std)

        except Exception as err:
            logger.warning("Failed to load scaler; fallback to raw features", error=str(err), path=str(scaler_path))
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
                logger.error("Failed to save atomic model weights to disk", error=str(err), path=str(model_path))

    # -------------------------
    # Warmup + bootstrap training
    # -------------------------

    async def _cold_start_warmup(self, symbol: str) -> None:
        logger.info("Cold-start warmup: fetching 2000 M1 bars...")
        hist_bars = self.adapter.get_historical_bars(symbol=symbol, timeframe="M1", count=2000) or []

        for b in hist_bars:
            self.aggregator._completed_bars.append(b)

            completed = self.aggregator.get_completed_bars()
            if len(completed) < 55:
                continue

            bar_time = getattr(b, "timestamp", getattr(b, "time", datetime.now(timezone.utc)))
            synthetic_tick = TickData(
                symbol=symbol,
                timestamp=bar_time,
                bid=b.close,
                ask=b.close + 0.20,
                volume=b.tick_volume,
            )

            fv = self.feature_engine.compute_from_bars(completed, synthetic_tick)
            x40 = self._validate_40d_tensor(fv.to_tensor_input(), context="cold_start_warmup")
            record = {f"feat_{i}": float(x40[i]) for i in range(self.FEATURE_DIM)}
            record.update(
                close=b.close, high=b.high, low=b.low, open=b.open,
                spread=0.20, atr_m1=fv.atr_m1
            )
            self._rolling_feature_records.append(record)

        logger.info("Warmup complete", buffer_size=len(self._rolling_feature_records))

    async def _bootstrap_train_if_ready(self) -> None:
        if len(self._rolling_feature_records) < 300:
            return

        logger.info("BOOTSTRAP: initial online fine-tune starting...", rows=len(self._rolling_feature_records))
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
            self._bundle = ModelBundle(model=updated_model, scaler=scaler, artifact_path=bundle.artifact_path)

        self._run_model_diagnostics_and_summary(df_labeled=df_labeled, feature_cols=feature_cols)

    # -------------------------
    # Hot-path tick pipeline
    # -------------------------

    def _process_tick_pipeline(self, tick: TickData, account: AccountInfo) -> None:
        is_new_bar = self.aggregator.process_tick(tick)

        # cap bars (O(1) amortized)
        if len(self.aggregator._completed_bars) > 200:
            self.aggregator._completed_bars = self.aggregator._completed_bars[-200:]

        completed_bars = self.aggregator.get_completed_bars()
        fv = self.feature_engine.compute_from_bars(completed_bars=completed_bars, current_tick=tick)

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
        )
        current_pos_count = len(active_positions)

        # Inference
        probs = self._infer_probabilities(fv=fv)

        # Policy
        proposal = self.signal_policy.evaluate_probabilities(
            probabilities=probs,
            current_tick=tick,
            feature_vector=fv,
            regime_state=regime_state,
            survival_mode=self._survival_mode_active,
        )
        self.audit.log_signal(proposal)

        # Risk + order build
        order: Optional[TradeOrder] = None
        if proposal.action in (
            ActionType.BUY_MARKET, ActionType.SELL_MARKET,
            ActionType.BUY_LIMIT, ActionType.SELL_LIMIT,
            ActionType.BUY_STOP, ActionType.SELL_STOP
        ) and self._symbol_info:
            order = self.risk_engine.evaluate_proposal(
                proposal=proposal,
                account=account,
                symbol_info=self._symbol_info,
                active_positions=active_positions,
                current_tick=tick,
                regime_state=regime_state,
            )

        if order is not None:
            logger.info("DISPATCH ORDER", action=order.order_type.value, volume=order.volume, price=order.price)
            success = self.order_manager.execute_order(order)
            if success:
                risk_usd = account.equity * (self.config.risk.risk_per_trade_pct / 100.0)
                try:
                    self.notifier.notify_order_opened(order=order, risk_usd=risk_usd)
                except Exception:
                    pass

        # Equity / drawdown tracking + audit
        self._update_survival_state(account=account, current_pos_count=current_pos_count)
        self.audit.log_account_snapshot(account=account, peak_equity=self._peak_equity)

    def _on_new_bar(self, tick: TickData, fv, last_bar) -> None:
        x40 = self._validate_40d_tensor(fv.to_tensor_input(), context="new_bar_record")
        rec = {f"feat_{i}": float(x40[i]) for i in range(self.FEATURE_DIM)}
        rec.update(
            close=last_bar.close, high=last_bar.high, low=last_bar.low, open=last_bar.open,
            spread=(tick.ask - tick.bid), atr_m1=fv.atr_m1
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
        x40 = self._validate_40d_tensor(fv.to_tensor_input(), context="live_inference")
        x_np = np.array(x40, dtype=np.float32).reshape(1, -1)

        with self._bundle_lock:
            bundle = self._bundle
        if bundle is None:
            raise RuntimeError("Model bundle not initialized")

        x_np = bundle.scaler.transform_40d(x_np)
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
                3,     # epochs
                1e-4,  # lr
                15,    # max_holding_bars
            )
            updated_model.eval()

            # Refresh scaler + persist weights
            scaler = self._load_scaler_artifacts(bundle.artifact_path)
            self._save_model_weights_atomic(updated_model, bundle.artifact_path)

            with self._bundle_lock:
                self._bundle = ModelBundle(model=updated_model, scaler=scaler, artifact_path=bundle.artifact_path)

            self._bars_since_last_retrain = 0
            logger.info("ASYNC RETRAIN SUCCESS")

        except Exception as err:
            logger.error("Async retrain failed", error=str(err), exc_info=True)

        finally:
            self._retrain_inflight = False

    # -------------------------
    # Diagnostics
    # -------------------------

    def _run_model_diagnostics_and_summary(self, df_labeled: pl.DataFrame, feature_cols: List[str]) -> None:
        logger.info("=== MODEL DIAGNOSTICS ===")

        with self._bundle_lock:
            bundle = self._bundle
        if bundle is None:
            logger.warning("No model bundle for diagnostics")
            return

        # Test 1: forward pass sanity
        sample_x_np = df_labeled.select(feature_cols).tail(20).to_numpy().astype(np.float32, copy=False)
        sample_x_np = bundle.scaler.transform_40d(sample_x_np)
        x = torch.tensor(sample_x_np, dtype=torch.float32)
        x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)

        with torch.inference_mode():
            p = bundle.model(x)
        test1_pass = not (torch.isnan(p).any() or torch.isinf(p).any())

        # Test 2/3: calibrated class distribution
        test_df = df_labeled.tail(100)
        test_x_np = test_df.select(feature_cols).to_numpy().astype(np.float32, copy=False)
        test_x_np = bundle.scaler.transform_40d(test_x_np)
        tx = torch.tensor(test_x_np, dtype=torch.float32)
        tx = torch.nan_to_num(tx, nan=0.0, posinf=1.0, neginf=-1.0)

        with torch.inference_mode():
            probs = bundle.model(tx).cpu().numpy()

        buy_probs = probs[:, 1]
        sell_probs = probs[:, 2]
        threshold = float(self.config.model.confidence_threshold)

        preds = np.zeros(len(probs), dtype=int)
        preds[(buy_probs >= threshold) & (buy_probs > sell_probs)] = 1
        preds[(sell_probs >= threshold) & (sell_probs > buy_probs)] = 2

        total = len(preds)
        buy_pct = float(np.sum(preds == 1) / max(total, 1) * 100.0)
        sell_pct = float(np.sum(preds == 2) / max(total, 1) * 100.0)
        no_trade_pct = float(np.sum(preds == 0) / max(total, 1) * 100.0)

        test3_pass = (buy_pct < 85.0 and sell_pct < 85.0)

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

    def _restore_peak_equity(self, account: Optional[AccountInfo]) -> None:
        last_snapshot = self.audit.get_last_account_snapshot()
        if last_snapshot and "peak_equity" in last_snapshot:
            self._peak_equity = float(last_snapshot["peak_equity"])
            logger.info("Restored peak equity from audit DB", peak_equity=f"{self._peak_equity:.2f}")
        elif account:
            self._peak_equity = float(account.equity)

        if account:
            self._last_balance = float(account.balance)

    def _notify_startup(self, account: Optional[AccountInfo]) -> None:
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
            no_trade_was_closed = (current_pos_count >= self._last_active_position_count)
            if balance_delta < 0.0 and no_trade_was_closed and abs(balance_delta) > (account.equity * 0.02):
                self._peak_equity += balance_delta
                logger.info("Withdrawal detected; adjusted peak equity", peak_equity=f"{self._peak_equity:.2f}")

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
            if drawdown_pct > (self.config.risk.max_account_drawdown_pct * 0.5) and not self._survival_mode_active:
                self._survival_mode_active = True
                logger.warning("SURVIVAL MODE ON", drawdown_pct=round(drawdown_pct, 2))
                try:
                    self.notifier.notify_survival_mode_changed(active=True, drawdown_pct=drawdown_pct)
                except Exception:
                    pass

            if drawdown_pct > self.config.risk.max_account_drawdown_pct:
                logger.critical("MAX DRAWDOWN EXCEEDED; HALTING", dd_pct=round(drawdown_pct, 2))
                try:
                    self.notifier.notify_kill_switch_activated(f"Max Drawdown Exceeded ({drawdown_pct:.2f}%)")
                except Exception:
                    pass
                self._running = False

    # -------------------------
    # 40D contract validation
    # -------------------------

    @classmethod
    def _validate_40d_tensor(cls, features: Sequence[float], context: str) -> List[float]:
        if len(features) != cls.FEATURE_DIM:
            raise RuntimeError(f"40D feature contract violation in {context}: expected {cls.FEATURE_DIM}, got {len(features)}")

        out: List[float] = []
        for idx, val in enumerate(features):
            try:
                f = float(val)
            except Exception:
                logger.warning("Non-numeric feature sanitized", context=context, feature=f"feat_{idx}")
                f = 0.0

            if not np.isfinite(f):
                logger.warning("Non-finite feature sanitized", context=context, feature=f"feat_{idx}")
                f = 0.0

            out.append(float(np.clip(f, -3.0, 3.0)))
        return out