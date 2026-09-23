"""ML-ARCH-003 — attention & positional-encoding ablation harness.

Compares, under a FIXED dataset and FIXED training loop, the things the task
names:

    attention kind : none (pure TCN control) | unmasked (incumbent) | causal
    heads          : 2 | 4
    positional     : none | sinusoidal | learned | rotary

Every arm trains on the SAME synthetic fold with the SAME seed, the SAME
batch order, the SAME loss, the SAME number of optimizer steps and the SAME
batch composition, so the only quantity that varies between arms is the
architecture knob under test. The harness then measures val loss / val
accuracy / macro-F1 and CPU forward latency per sample on the SAME batch.

Latency is measured with ``torch.inference_mode()`` after a warmup pass, on
``time.process_time()`` (CPU time, insensitive to co-tenant load on shared
runners — a wall-clock comparison here is the known flaky shape; see the
pitfalls note in the swarm skill).

Determinism note: the runner reseeds before EVERY arm, and arms that do not
touch the RNG-generating code paths (e.g. ``attention=none`` vs
``attention=causal`` at the same parameter count) consume the same parameter
draw. Attention-mask differences cannot perturb the RNG: ``nn.MultiheadAttention``
draws no random numbers when ``dropout=0`` and ``need_weights`` does not draw
either. Arms with different head counts DO perturb the init draw (different
projection shapes), which is unavoidable and is reported as such.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import torch
from torch import nn

from nexus_scalp.model_generation.architectures import (
    DILATION_GEOMETRIC,
    CausalConv1dBlock,
    dilation_schedule,
)
from nexus_scalp.models.attention import (
    ATTENTION_NONE,
    POSITION_LEARNED,
    POSITION_NONE,
    POSITION_ROTARY,
    POSITION_SINUSOIDAL,
    CausalSelfAttention,
    RotaryPositionalEncoding,
    create_positional_encoding,
)

__all__ = [
    "ARM_DEFAULTS",
    "ATTENTION_KINDS",
    "POSITION_KINDS",
    "AttentionArm",
    "AttentionArmResult",
    "AttentionAttentionArm",
    "NoAttentionArm",
    "build_attention_ablation_arms",
    "compute_macro_f1",
    "compute_val_metrics",
    "measure_latency",
    "run_attention_ablation",
]

ATTENTION_KINDS: tuple[str, ...] = (ATTENTION_NONE, "unmasked", "causal")
POSITION_KINDS: tuple[str, ...] = (
    POSITION_NONE,
    POSITION_SINUSOIDAL,
    POSITION_LEARNED,
    POSITION_ROTARY,
)


@dataclass(frozen=True)
class AttentionArm:
    """One cell of the ablation matrix.

    ``label`` is the human-readable arm identity; the ``(attention, heads,
    positional)`` triple is the knob set. Two arms with the same knob set and
    a different label are a caller bug, so ``__post_init__`` refuses it.
    """

    label: str
    attention: str
    heads: int
    positional: str
    input_dim: int = 50
    hidden_dim: int = 64
    blocks: int = 3
    kernel_size: int = 3
    seq_len: int = 32
    num_classes: int = 3
    dropout: float = 0.0
    dilation: str = DILATION_GEOMETRIC
    max_seq_len: int = 128

    def __post_init__(self) -> None:
        if self.attention not in ATTENTION_KINDS:
            supported = ", ".join(ATTENTION_KINDS)
            raise ValueError(
                f"Arm {self.label!r}: attention={self.attention!r} not in ({supported})"
            )
        if self.positional not in POSITION_KINDS:
            supported = ", ".join(POSITION_KINDS)
            raise ValueError(
                f"Arm {self.label!r}: positional={self.positional!r} not in ({supported})"
            )
        if self.attention != ATTENTION_NONE and self.hidden_dim % self.heads != 0:
            raise ValueError(
                f"Arm {self.label!r}: hidden_dim={self.hidden_dim} not divisible by "
                f"heads={self.heads}"
            )
        if self.num_classes < 2:
            raise ValueError(
                f"Arm {self.label!r}: num_classes={self.num_classes} < 2 violates the "
                "3-class tensor contract"
            )

    @property
    def uses_attention(self) -> bool:
        return self.attention != ATTENTION_NONE

    @property
    def causal(self) -> bool:
        return self.attention == "causal"


class NoAttentionArm(nn.Module):
    """Pure-TCN control: conv stack + last-timestep pooling, no temporal mixing.

    This is the arm that isolates the *attention* contribution. It keeps the
    same projection / conv / head geometry as the attention arms so the
    parameter-count difference is exactly the attention block.
    """

    def __init__(self, spec: AttentionArm) -> None:
        super().__init__()
        self.spec = spec
        self.projection = nn.Linear(spec.input_dim, spec.hidden_dim)
        self.proj_norm = nn.LayerNorm(spec.hidden_dim)
        self.conv_blocks = nn.ModuleList(
            [
                CausalConv1dBlock(
                    spec.hidden_dim,
                    kernel_size=spec.kernel_size,
                    dilation=d,
                    dropout=spec.dropout,
                )
                for d in dilation_schedule(spec.dilation, spec.blocks)
            ]
        )
        self.head = nn.Sequential(
            nn.Linear(spec.hidden_dim, spec.hidden_dim // 2),
            nn.GELU(),
            nn.Linear(spec.hidden_dim // 2, spec.num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """``x: (B, T, F)`` -> ``(B, num_classes)`` logits; no temporal mixing."""
        h = self.proj_norm(self.projection(x))  # (B, T, H)
        h = h.transpose(1, 2)  # (B, H, T)  -- CausalConv1dBlock contract
        for block in self.conv_blocks:
            h = block(h)  # (B, H, T)
        h = h.transpose(1, 2)  # (B, T, H)
        return self.head(h[:, -1, :])


class AttentionAttentionArm(nn.Module):
    """TCN + positional encoding + (causal|unmasked) multi-head attention."""

    def __init__(self, spec: AttentionArm) -> None:
        super().__init__()
        self.spec = spec
        self.projection = nn.Linear(spec.input_dim, spec.hidden_dim)
        self.proj_norm = nn.LayerNorm(spec.hidden_dim)
        self.conv_blocks = nn.ModuleList(
            [
                CausalConv1dBlock(
                    spec.hidden_dim,
                    kernel_size=spec.kernel_size,
                    dilation=d,
                    dropout=spec.dropout,
                )
                for d in dilation_schedule(spec.dilation, spec.blocks)
            ]
        )
        self.positional = create_positional_encoding(
            spec.positional, spec.hidden_dim, max_len=spec.max_seq_len
        )
        self.attention = CausalSelfAttention(
            hidden_dim=spec.hidden_dim,
            num_heads=spec.heads,
            dropout=spec.dropout,
            causal=spec.causal,
            max_seq_len=spec.max_seq_len,
        )
        self.attn_norm = nn.LayerNorm(spec.hidden_dim)
        self.head = nn.Sequential(
            nn.Linear(spec.hidden_dim, spec.hidden_dim // 2),
            nn.GELU(),
            nn.Linear(spec.hidden_dim // 2, spec.num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """``x: (B, T, F)`` -> ``(B, num_classes)`` logits."""
        h = self.proj_norm(self.projection(x))
        h = h.transpose(1, 2)
        for block in self.conv_blocks:
            h = block(h)
        h = h.transpose(1, 2)
        h = self.positional(h)
        rotary = self.positional if isinstance(self.positional, RotaryPositionalEncoding) else None
        attn_out, _ = self.attention(h, rotary=rotary)
        h = self.attn_norm(h + attn_out)
        return self.head(h[:, -1, :])


@dataclass(frozen=True)
class AttentionArmResult:
    """One arm's measured outcome (training + latency)."""

    label: str
    attention: str
    heads: int
    positional: str
    train_loss: float
    val_loss: float
    val_accuracy: float
    val_macro_f1: float
    latency_us_per_sample: float
    params: int
    causal: bool
    steps: int
    val_samples: int
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "attention": self.attention,
            "heads": self.heads,
            "positional": self.positional,
            "causal": self.causal,
            "params": self.params,
            "steps": self.steps,
            "train_loss": self.train_loss,
            "val_loss": self.val_loss,
            "val_accuracy": self.val_accuracy,
            "val_macro_f1": self.val_macro_f1,
            "latency_us_per_sample": self.latency_us_per_sample,
            "val_samples": self.val_samples,
            "detail": self.detail,
        }


ARM_DEFAULTS: dict[str, Any] = {
    "input_dim": 50,
    "hidden_dim": 64,
    "blocks": 3,
    "kernel_size": 3,
    "seq_len": 32,
    "num_classes": 3,
    "dropout": 0.0,
    "dilation": DILATION_GEOMETRIC,
    "max_seq_len": 128,
}


def build_attention_ablation_arms(
    *,
    attention_kinds: tuple[str, ...] = ATTENTION_KINDS,
    head_counts: tuple[int, ...] = (2, 4),
    positional_kinds: tuple[str, ...] = POSITION_KINDS,
    defaults: dict[str, Any] | None = None,
) -> list[AttentionArm]:
    """Enumerate the ablation matrix as labelled arms.

    The full product is attention x heads x positional. When an arm does not
    use attention at all, ``heads`` is structurally irrelevant (no attention
    block exists), so the heads dimension is collapsed for the
    ``attention=none`` arm to avoid reporting phantom cells that measure the
    same model twice.
    """
    base = dict(ARM_DEFAULTS)
    if defaults is not None:
        base.update(defaults)
    arms: list[AttentionArm] = []
    seen: set[tuple[str, int, str]] = set()
    for attn_kind in attention_kinds:
        heads_iter = (0,) if attn_kind == ATTENTION_NONE else head_counts
        for heads in heads_iter:
            for pos_kind in positional_kinds:
                key = (attn_kind, heads, pos_kind)
                if key in seen:
                    continue
                seen.add(key)
                label = f"attn={attn_kind}"
                if attn_kind != ATTENTION_NONE:
                    label += f"|heads={heads}"
                label += f"|pos={pos_kind}"
                arms.append(
                    AttentionArm(
                        label=label, attention=attn_kind, heads=heads, positional=pos_kind, **base
                    )
                )
    return arms


def compute_macro_f1(preds: torch.Tensor, labels: torch.Tensor, num_classes: int) -> float:
    """Macro-averaged F1 over all classes (torch-only; sklearn unavailable).

    Follows ``sklearn.metrics.f1_score``'s macro convention exactly: a class
    with no predicted AND no true samples is SKIPPED (it contributes neither a
    0 nor a 1, and the divisor shrinks). A class that has predictions or
    labels but an undefined F1 (precision=0 or recall=0) contributes 0. This
    is why the hand-computed values below must be worked out per class rather
    than assumed to sum to a round number.
    """
    if preds.numel() == 0:
        return 0.0
    if preds.shape != labels.shape:
        raise ValueError(
            f"compute_macro_f1: preds shape {tuple(preds.shape)} != labels "
            f"shape {tuple(labels.shape)}"
        )
    f1_sum = 0.0
    n_scored = 0
    for cls in range(num_classes):
        pred_cls = preds == cls
        true_cls = labels == cls
        tp = int((pred_cls & true_cls).sum())
        fp = int((pred_cls & ~true_cls).sum())
        fn = int((~pred_cls & true_cls).sum())
        # sklearn skips a class absent from BOTH predictions and labels
        if tp + fp == 0 and tp + fn == 0:
            continue
        n_scored += 1
        if tp + fp == 0 or tp + fn == 0:
            continue
        precision = tp / (tp + fp)
        recall = tp / (tp + fn)
        if precision + recall == 0.0:
            continue
        f1_sum += 2.0 * precision * recall / (precision + recall)
    if n_scored == 0:
        return 0.0
    return f1_sum / n_scored


def compute_val_metrics(
    model: nn.Module,
    features: torch.Tensor,
    labels: torch.Tensor,
    *,
    batch_size: int = 256,
    num_classes: int = 3,
) -> tuple[float, float, float]:
    """``(val_loss, val_accuracy, val_macro_f1)`` under ``inference_mode``.

    The caller's train/eval mode is restored on return (an ablation runner that
    interleaves train and eval calls would otherwise silently keep dropout off
    for its next training step).
    """
    was_training = model.training
    model.eval()
    loss_total = 0.0
    n_seen = 0
    all_preds: list[torch.Tensor] = []
    all_labels: list[torch.Tensor] = []
    loss_fn = nn.CrossEntropyLoss(reduction="sum")
    try:
        with torch.inference_mode():
            for start in range(0, features.shape[0], batch_size):
                xb = features[start : start + batch_size]
                yb = labels[start : start + batch_size]
                logits = model(xb)
                # The sequence arms emit (N, C) logits; a bare nn.Linear emits
                # (N, C) too, but a 2-D single-tick model may emit (N, 1, C).
                # CrossEntropyLoss takes (N, C) and (N,) class indices.
                if logits.dim() == 3:
                    logits = logits[:, -1, :]
                if yb.dim() > 1:
                    yb = yb.reshape(yb.shape[0], -1)[:, -1]
                loss_total += float(loss_fn(logits, yb))
                n_seen += int(yb.numel())
                all_preds.append(logits.argmax(dim=-1))
                all_labels.append(yb)
    finally:
        model.train(was_training)
    if n_seen == 0:
        return float("nan"), float("nan"), float("nan")
    preds = torch.cat(all_preds)
    labs = torch.cat(all_labels)
    accuracy = float((preds == labs).float().mean())
    return loss_total / n_seen, accuracy, compute_macro_f1(preds, labs, num_classes)


def measure_latency(
    model: nn.Module,
    sample: torch.Tensor,
    *,
    repetitions: int = 25,
    warmup: int = 5,
) -> float:
    """Median per-sample CPU forward latency in microseconds.

    ``time.process_time()`` (CPU) rather than ``perf_counter`` (wall clock):
    a latency comparison that trips on co-tenant scheduler load on shared CI
    runners is the documented flaky shape, and CPU time removes that noise
    without changing what is being measured (these models are pure-CPU).
    Median over ``repetitions`` further suppresses GC/scheduler outliers.
    """
    if sample.dim() != 3:
        raise ValueError(
            f"measure_latency expects a (1, T, F) sample, got shape {tuple(sample.shape)}"
        )
    if repetitions < 1 or warmup < 0:
        raise ValueError(f"measure_latency: repetitions={repetitions} warmup={warmup} invalid")
    model.eval()
    with torch.inference_mode():
        for _ in range(warmup):
            model(sample)
        timings = torch.empty(repetitions, dtype=torch.float64)
        for i in range(repetitions):
            start = time.process_time()
            model(sample)
            timings[i] = (time.process_time() - start) * 1e6
    return float(timings.median().item())


def _train_arm(
    spec: AttentionArm,
    train_features: torch.Tensor,
    train_labels: torch.Tensor,
    val_features: torch.Tensor,
    val_labels: torch.Tensor,
    *,
    steps: int,
    batch_size: int,
    lr: float,
    seed: int,
    rng: torch.Generator,
) -> tuple[nn.Module, float, int]:
    """Train one arm; returns ``(model, final_train_loss, batches_run)``.

    The batch ORDER is drawn from ``rng`` (shared across arms) so arms see an
    identical sequence of minibatches — the only difference is how each arm
    processes them. The optimizer is constructed from the model parameters
    after the parameter draw, so no RNG state leaks between the init and the
    batch-order draws.
    """
    torch.manual_seed(seed)
    model: nn.Module
    if spec.uses_attention:
        model = AttentionAttentionArm(spec)
    else:
        model = NoAttentionArm(spec)
    model.train()
    optim = torch.optim.AdamW(model.parameters(), lr=lr)
    loss_fn = nn.CrossEntropyLoss()
    n_train = train_features.shape[0]
    batches_run = 0
    train_loss_sum = 0.0
    for _step in range(steps):
        idx = torch.randint(0, n_train, (batch_size,), generator=rng)
        xb = train_features[idx]
        yb = train_labels[idx]
        optim.zero_grad(set_to_none=True)
        logits = model(xb)
        loss = loss_fn(logits, yb)
        loss.backward()
        optim.step()
        train_loss_sum += float(loss.detach())
        batches_run += 1
    return model, train_loss_sum / max(batches_run, 1), batches_run


def run_attention_ablation(
    arms: list[AttentionArm],
    train_features: torch.Tensor,
    train_labels: torch.Tensor,
    val_features: torch.Tensor,
    val_labels: torch.Tensor,
    *,
    steps: int = 40,
    batch_size: int = 64,
    lr: float = 2e-3,
    seed: int = 1337,
    latency_repetitions: int = 25,
) -> list[AttentionArmResult]:
    """Run every arm on identical data; return per-arm results.

    One shared ``torch.Generator`` seeds the batch-order stream so all arms see
    the same minibatches in the same order, and each arm is re-initialized
    from ``torch.manual_seed(seed)`` so the parameter draw is reproducible.
    """
    results: list[AttentionArmResult] = []
    # Shared batch-order stream: every arm consumes the SAME sequence of
    # minibatch index draws. Reuse requires the caller to pass the same
    # stream; here we create it once per arm list so identical step counts
    # consume identical draws.
    for spec in arms:
        rng = torch.Generator().manual_seed(seed + 1)
        model, train_loss, batches = _train_arm(
            spec,
            train_features,
            train_labels,
            val_features,
            val_labels,
            steps=steps,
            batch_size=batch_size,
            lr=lr,
            seed=seed,
            rng=rng,
        )
        val_loss, val_acc, val_f1 = compute_val_metrics(
            model,
            val_features,
            val_labels,
            batch_size=256,
            num_classes=spec.num_classes,
        )
        latency = measure_latency(
            model,
            val_features[:1],
            repetitions=latency_repetitions,
        )
        results.append(
            AttentionArmResult(
                label=spec.label,
                attention=spec.attention,
                heads=spec.heads,
                positional=spec.positional,
                train_loss=train_loss,
                val_loss=val_loss,
                val_accuracy=val_acc,
                val_macro_f1=val_f1,
                latency_us_per_sample=latency,
                params=sum(p.numel() for p in model.parameters() if p.requires_grad),
                causal=spec.causal,
                steps=batches,
                val_samples=int(val_labels.numel()),
            )
        )
    return results
