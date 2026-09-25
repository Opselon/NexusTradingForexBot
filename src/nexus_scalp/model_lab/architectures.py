"""MODEL LAB — teacher, student and distilled architectures.

TEACHER (TEACHER_TCN_ATTN): TCN_ATTENTION_V1 with widened capacity + a
    3-layer attention stack. Research-only: slower, high information
    capacity. Built on the repository's verified causal blocks
    (CausalConv1dBlock) — never a lab reimplementation of causality.

STUDENT (STUDENT_MLP): compact MLP, strictly lower parameter count than the
    teacher, single-timestep (contextual) input, production-latency target.

DISTILLATION: KL(teacher_softmax(T) || student_softmax(T)) * w
              + CE(hard labels) * (1 - w), computed over TRAIN split
              outputs. The teacher runs under inference_mode; the student
              learns from (teacher soft targets + hard labels) — never from
              teacher TRAIN-time activations of the val/OOS splits.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import nn

from nexus_scalp.model_lab.registry import ExperimentSpec


class TeacherTCNAttention(nn.Module):
    """High-capacity causal TCN + multi-layer attention teacher (3-class)."""

    def __init__(
        self,
        input_dim: int,
        num_classes: int = 3,
        hidden_dim: int = 96,
        blocks: int = 4,
        heads: int = 4,
        attn_layers: int = 2,
        dropout: float = 0.15,
        window: int = 16,
    ) -> None:
        super().__init__()
        from nexus_scalp.model_generation.architectures import CausalConv1dBlock

        self.window = window
        self.num_classes = num_classes
        self.proj = nn.Linear(input_dim, hidden_dim)
        self.blocks = nn.ModuleList()
        for b in range(blocks):
            self.blocks.append(
                CausalConv1dBlock(hidden_dim, kernel_size=3, dilation=2**b, dropout=dropout)
            )
        layer = nn.TransformerEncoderLayer(
            d_model=hidden_dim,
            nhead=heads,
            dim_feedforward=hidden_dim * 2,
            dropout=dropout,
            batch_first=True,
            norm_first=True,
        )
        self.attn = nn.TransformerEncoder(layer, num_layers=attn_layers)
        self.norm = nn.LayerNorm(hidden_dim)
        self.head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, T, F) for temporal; (B, F) promotes to T=1.
        if x.dim() == 2:
            x = x.unsqueeze(1)
        h = self.proj(x)
        for blk in self.blocks:
            t = h.transpose(1, 2)
            h = h + blk(t).transpose(1, 2)
        h = self.attn(h)
        h = self.norm(h[:, -1, :])  # last-timestep pooling (causal)
        return self.head(h)


class StudentMLP(nn.Module):
    """Compact single-timestep student (3-class)."""

    def __init__(
        self, input_dim: int, num_classes: int = 3, hidden_dim: int = 48, dropout: float = 0.10
    ) -> None:
        super().__init__()
        self.num_classes = num_classes
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() == 3:  # accept windowed input, use last step
            x = x[:, -1, :]
        return self.net(x)


def build_model(spec: ExperimentSpec) -> nn.Module:
    """Lab model construction from an explicit ExperimentSpec."""
    torch.manual_seed(spec.seed)
    if spec.model_family == "STUDENT_MLP":
        return StudentMLP(spec.input_dimension, spec.num_classes, hidden_dim=48)
    if spec.model_family == "TEACHER_TCN_ATTN":
        return TeacherTCNAttention(
            spec.input_dimension,
            spec.num_classes,
            window=max(2, spec.sequence_length),
        )
    if spec.model_family in ("LEGACY_SCALPNET_V1", "MLP_V2", "TCN_ATTENTION_V1"):
        # Repository architectures through the verified factory (same
        # construction path as governance-audited candidates).
        from nexus_scalp.model_generation.model_factory import ModelFactory

        return ModelFactory().build(
            architecture=spec.model_family,
            num_classes=spec.num_classes,
            parameters={"input_dim": spec.input_dimension},
        )
    raise ValueError(f"unknown lab model_family: {spec.model_family}")


def distillation_loss(
    student_logits: torch.Tensor,
    hard_labels: torch.Tensor,
    teacher_logits: torch.Tensor,
    temperature: float,
    weight: float,
) -> torch.Tensor:
    """KL soft loss + CE hard loss, both at temperature T (Hinton et al.)."""
    T = max(float(temperature), 1e-6)
    soft_logp = F.log_softmax(student_logits / T, dim=-1)
    soft_p = F.softmax(teacher_logits / T, dim=-1)
    kd = F.kl_div(soft_logp, soft_p, reduction="batchmean") * (T * T)
    ce = F.cross_entropy(student_logits, hard_labels)
    return weight * kd + (1.0 - weight) * ce
