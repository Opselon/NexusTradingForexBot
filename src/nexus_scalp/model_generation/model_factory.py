"""Model Factory (PHASE 13, spec 19 / 20 / 21 / 22).

Architecture registry — configuration-driven model construction.

The FIRST benchmark MUST include LEGACY_SCALPNET_V1 through the NEW
artifact/dataset pipeline. Architecture novelty is NOT evidence of
superiority; the baseline is the control group.
"""

from __future__ import annotations

from typing import Any

import torch
from torch import nn

from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.model_generation.models import ModelArchitecture
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.model_factory")

#: ScalpNet legacy head: NO_TRADE, BUY, SELL, WAIT. The migration contract is
#: 3-class neural; the legacy 4th output is a POLICY bridge (WAIT), not a
#: training label. Strategy: validate against the declared label_schema.
LEGACY_HEAD_CLASSES = 4
CONTRACT_3CLASS = 3


class SimpleMLP(nn.Module):
    """Minimal MLP baseline for comparison experiments (MLP_V2)."""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 128,
        layers: int = 3,
        dropout: float = 0.15,
        num_classes: int = 3,
    ) -> None:
        super().__init__()
        mods: list[nn.Module] = []
        dims = [input_dim] + [hidden_dim] * layers
        for i in range(len(dims) - 1):
            mods.append(nn.Linear(dims[i], dims[i + 1]))
            mods.append(nn.GELU())
            mods.append(nn.Dropout(dropout))
        mods.append(nn.Linear(hidden_dim, num_classes))
        self.net = nn.Sequential(*mods)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class ModelFactory:
    """Builds models from a declared architecture id + parameters.

    ``num_classes`` is derived from the label schema contract (3 by default);
    the legacy ScalpNet accepts 4 outputs only for the LEGACY baseline path,
    where the 4th output is a policy WAIT bridge with an explicit contract
    note.
    """

    def __init__(self, feature_schema_id: str = "scalp_v1") -> None:
        self.feature_schema = FEATURE_SCHEMAS.resolve(feature_schema_id)

    def build(
        self,
        architecture: str,
        num_classes: int = CONTRACT_3CLASS,
        parameters: dict[str, Any] | None = None,
    ) -> nn.Module:
        params = parameters or {}
        input_dim = int(params.get("input_dim", self.feature_schema.dimension))

        if architecture == ModelArchitecture.LEGACY_SCALPNET_V1.value:
            # Legacy baseline: raw ScalpNet. num_classes defaults to its own
            # 4-head (NO_TRADE/BUY/SELL/WAIT-policy-bridge). When the caller
            # demands the 3-class contract we still build 4 logits and the
            # runtime maps class 3 -> WAIT policy state (never a label).
            head = int(params.get("num_classes", LEGACY_HEAD_CLASSES))
            if num_classes == CONTRACT_3CLASS:
                head = LEGACY_HEAD_CLASSES  # preserve legacy geometry
            return ScalpNet(
                num_features=input_dim,
                num_classes=head,
                hidden_dim=int(params.get("hidden_dim", 128)),
                num_heads=int(params.get("num_heads", 4)),
                dropout_rate=float(params.get("dropout_rate", 0.25)),
            )

        if architecture == ModelArchitecture.MLP_V2.value:
            return SimpleMLP(
                input_dim=input_dim,
                hidden_dim=int(params.get("hidden_dim", 128)),
                layers=int(params.get("layers", 3)),
                dropout=float(params.get("dropout", 0.15)),
                num_classes=num_classes,
            )

        if architecture in (
            ModelArchitecture.TCN_V2.value,
            ModelArchitecture.TCN_ATTENTION_V1.value,
            ModelArchitecture.TRANSFORMER_V1.value,
        ):
            # Registered future architectures: construction is supported via
            # the same config-driven pattern. Only architectures proven by
            # benchmark evidence earn Challenger status.
            raise NotImplementedError(
                f"Architecture {architecture} registered but not yet implemented — "
                "benchmark LEGACY_SCALPNET_V1 first (spec 19/20)."
            )

        raise ValueError(f"Unknown architecture: {architecture}")

    def build_from_experiment(self, experiment: dict[str, Any]) -> nn.Module:
        """Builds the model for an experiment config dict."""
        return self.build(
            architecture=str(
                experiment.get("architecture", ModelArchitecture.LEGACY_SCALPNET_V1.value)
            ),
            num_classes=int(experiment.get("class_count", CONTRACT_3CLASS)),
            parameters=experiment.get("architecture_parameters", {}),
        )


def infer_feature_dim(parameters: dict[str, Any], schema_id: str = "scalp_v1") -> int:
    return int(parameters.get("input_dim") or FEATURE_SCHEMAS.resolve(schema_id).dimension)
