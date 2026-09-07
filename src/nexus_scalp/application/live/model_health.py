"""ModelHealth — collapse detection and collapsed-model recovery.

P1 seam L12 (god-file decomposition, extraction 12 of the live-engine wave).
The model-health pair leaves ``application/live_engine.py`` verbatim
(behavior-preserving extraction):

    _detect_model_collapse        : evidence-based collapse detection on the
                                    live bundle (degenerate output/prior drift)
    _reinitialize_collapsed_model : fail-safe rebuild of the serving bundle
                                    from the declared artifact contract
                                    (BUG-141 guards + effective-col retrain
                                    path), atomic weight save + rebind.

State ownership: ``_bundle`` / ``_bundle_lock`` stay at the composition root;
methods are invoked UNBOUND with the engine as the state surface (harness
contracts preserved; the BUG-182B effective-cols contract follows the code).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import polars as pl
import torch

from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.observability.logging import get_logger

if TYPE_CHECKING:  # import-cycle breaker: ModelBundle lives on the engine
    from nexus_scalp.application.live_engine import ModelBundle

logger = get_logger("nexus_scalp.application.live.model_health")


class ModelHealth:
    """Collapse detection + recovery (composition root: LiveEngine)."""

    def __init__(self, om: Any) -> None:
        self.om = om

    def detect_model_collapse(
        self, df_labeled: pl.DataFrame, feature_cols: list[str]
    ) -> dict[str, float] | None:
        """
        Runs the model over a recent sample and returns the class distribution.

        Returns None when no bundle is available. The caller decides whether the
        distribution indicates a mono-class collapse and how to react.
        """
        with self._bundle_lock:
            bundle = self._bundle
        if bundle is None:
            return None
        try:
            test_df = df_labeled.tail(100)
            test_x_np = test_df.select(feature_cols).to_numpy().astype(np.float32, copy=False)
            test_x_np = bundle.scaler.transform(test_x_np)
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
            total = max(len(preds), 1)
            return {
                "buy_pct": float(np.sum(preds == 1) / total * 100.0),
                "sell_pct": float(np.sum(preds == 2) / total * 100.0),
                "no_trade_pct": float(np.sum(preds == 0) / total * 100.0),
            }
        except Exception as e:
            logger.error("[MODEL] collapse detection failed (isolated)", error=str(e))
            return None

    def reinitialize_collapsed_model(self) -> bool:
        """
        Detects a mono-class prediction collapse (>= 85% on a single active class)
        and re-initializes the live model with fresh weights.

        Previously a collapsed baseline (e.g. 100% SELL) was kept serving live
        ticks: the fine-tuning quality gate rejected every update and rolled back
        to the SAME collapsed baseline, so the engine never escaped the bad state.
        Re-initialization is atomic under `_bundle_lock` and only touches the model
        weights - the experience ledger and strategy memory are untouched.
        """
        try:
            # Build a small sample from the rolling feature buffer.
            if len(self._rolling_feature_records) < 32:
                return False
            df = pl.DataFrame(list(self._rolling_feature_records))
            # BUG-182B: artifact-driven columns (see _trigger_async_online_fine_tune).
            feature_cols = list(self.effective_feature_cols)
            dist = self._detect_model_collapse(df, feature_cols)
            if dist is None:
                return False
            buy_pct = dist["buy_pct"]
            sell_pct = dist["sell_pct"]
            # A healthy model must not be dominated by a single active class.
            collapsed = buy_pct >= 85.0 or sell_pct >= 85.0
            if not collapsed:
                return False

            logger.warning(
                "[MODEL] MONO_CLASS_COLLAPSE_DETECTED - re-initializing weights",
                buy_pct=round(buy_pct, 1),
                sell_pct=round(sell_pct, 1),
                no_trade_pct=round(dist["no_trade_pct"], 1),
            )
            model_path = Path(self.config.model.model_artifact_path)
            fresh = ScalpNet(
                num_features=self._declared_contract_dim_for_path(model_path) or self.FEATURE_DIM,
                # BUG-243: declared head, not hardcoded 4.
                num_classes=self._declared_head_classes_for_path(
                    model_path.with_suffix(".meta.json")
                ),
            )
            fresh.eval()
            with self._bundle_lock:
                self._bundle = ModelBundle(
                    model=fresh,
                    scaler=self._bundle.scaler if self._bundle else None,
                    artifact_path=model_path,
                )
            # BUG-185: the fresh model was seeded at the PATH-declared
            # contract width - rebind the trainer to it.
            self._rebind_trainer_to_bundle()
            saved = self._save_model_weights_atomic(fresh, model_path)
            if saved:
                # P1 ARTIFACT TRUST: rebind integrity sidecars to the NEW
                # digest so the fresh pair stays verifiable (same contract as
                # the fine-tune persist path). A refresh failure keeps the
                # in-memory fresh bundle serving but flags loudly: the NEXT
                # cold load will fail closed rather than serve unverified.
                if not self._refresh_artifact_integrity_metadata(model_path):
                    logger.error(
                        "[MODEL] event=COLLAPSE_RECOVERY_SIDECAR_REFRESH_FAILED "
                        "next_cold_load_will_fail_closed artifact=%s",
                        model_path.name,
                    )
            self._register_active_model(model_path=model_path, replaced=True)
            logger.warning("[MODEL] COLLAPSE_RECOVERY_COMPLETE - fresh weights serving live ticks")
            return True
        except Exception as e:
            logger.error("[MODEL] collapse recovery failed (isolated)", error=str(e))
            return False
