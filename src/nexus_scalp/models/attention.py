"""Temporal attention & positional encoding building blocks (ML-ARCH-003).

CONTEXT
-------
``TCNAttentionV1`` (``model_generation/architectures.py``) and the 3D path of
``ScalpNet`` (``models/scalp_net.py``) both call ``nn.MultiheadAttention``
**without an attention mask**. For a model whose only output is read at the
LAST timestep of the decision window, an unmasked self-attention layer is not
lookahead in the sense that produces a *wrong live prediction* (position T-1 is
allowed to attend to 0..T-1, all of which are causal history of the decision
point), but it is lookahead in the strict per-position sense: position ``t``
mixes information from ``t+1..T-1``. That breaks the ``dY_t/dX_{t+k} == 0``
contract this repository proves for its conv stacks, and it breaks it
*differently for every sequence length* — the intermediate sequence states are
not a function of the past alone, so a model trained/persisted with one
``max_seq_len`` cannot be sliced down at inference time without silently
changing every intermediate representation.

This module supplies what both call sites lack:

* ``CausalSelfAttention`` — multi-head attention with an explicit triangular
  causal mask, exposing ``causal`` and ``mask`` knobs so an ablation can hold
  everything else fixed and flip ONLY the mask (the exact comparison the
  ML-ARCH-003 task demands).
* ``PositionalEncoding`` variants — ``sinusoidal`` (exact backward-compatible
  formula), ``learned`` (the parameterised embedding ``TCNAttentionV1`` uses
  today), ``rotary`` (RoPE, applied to Q/K so attention scores become relative
  functions of position) and ``none``.

All four encoders share one public surface (``create_positional_encoding`` +
the ``POSITIONAL_ENCODINGS`` registry) so the ablation runner can compare them
under identical training.

NONE of these layers is wired into live serving by this task (NON_GOALS: "Do
not deploy unverified attention architectures to production live serving").
They are research/ablation components that the model-factory can request by
parameter; the default behaviour of every existing call site is unchanged.
"""

from __future__ import annotations

from typing import Final, cast

import torch
from torch import nn

__all__ = [
    "ATTENTION_NONE",
    "POSITIONAL_ENCODINGS",
    "POSITION_LEARNED",
    "POSITION_NONE",
    "POSITION_ROTARY",
    "POSITION_SINUSOIDAL",
    "CausalSelfAttention",
    "LearnedPositionalEncoding",
    "NonePositionalEncoding",
    "RotaryPositionalEncoding",
    "SinusoidalPositionalEncoding",
    "apply_rotary",
    "create_positional_encoding",
    "make_causal_mask",
    "rotate_half",
]

# ---------------------------------------------------------------------------
# Attention variants
# ---------------------------------------------------------------------------

ATTENTION_NONE: Final[str] = "none"
"""Attention-kind selector: no temporal-mixing layer at all (pure TCN control)."""


def _log_base(base: float) -> torch.Tensor:
    """``log(base)`` as a tensor (``torch.log`` has no float overload)."""
    return torch.log(torch.tensor(base, dtype=torch.float32))


# ---------------------------------------------------------------------------
# Attention variants
# ---------------------------------------------------------------------------

POSITION_NONE: Final[str] = "none"
POSITION_SINUSOIDAL: Final[str] = "sinusoidal"
POSITION_LEARNED: Final[str] = "learned"
POSITION_ROTARY: Final[str] = "rotary"


def make_causal_mask(seq_len: int, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
    """Upper-triangular additive attention mask, shape ``(seq_len, seq_len)``.

    Returned as an *additive* float mask with ``-inf`` above the diagonal, the
    shape ``nn.MultiheadAttention`` expects for ``attn_mask``. Position ``t``
    may attend to positions ``<= t`` only.

    ``bool`` masks are rejected elsewhere in this stack (they invert the
    convention: ``True`` means *masked*, which silently permits exactly the
    future taps this module exists to prevent), so this always returns float.
    """
    mask = torch.full((seq_len, seq_len), float("-inf"), device=device, dtype=dtype)
    return torch.triu(mask, diagonal=1)


def rotate_half(x: torch.Tensor) -> torch.Tensor:
    """Split ``x`` in half along the last dim and rotate: ``[-b, a]``.

    Standard RoPE primitive: for ``x = [a, b]`` the rotation by angle theta is
    ``[a*cos - b*sin, a*sin + b*cos]``, which is the product ``x *
    rotate_pairs``. Keeping the concat-of-rotated-halves form makes the rotary
    embedding composable with any Q/K projection that is a plain ``nn.Linear``.
    """
    half = x.shape[-1] // 2
    x1, x2 = x[..., :half], x[..., half:]
    return torch.cat((-x2, x1), dim=-1)


def apply_rotary(
    x: torch.Tensor,
    cos: torch.Tensor,
    sin: torch.Tensor,
) -> torch.Tensor:
    """Apply RoPE to a ``(B, T, H*D)`` or ``(T, H*D)`` tensor.

    ``cos``/``sin`` carry the per-position frequencies, shape broadcastable to
    ``x`` (typically ``(1, T, H*D)``). The same tensor must be applied to Q and
    K: RoPE makes the attention score a function of the *relative* offset
    between query and key, so position information enters only through the
    score and never as an additive bias on the value path.
    """
    return x * cos + rotate_half(x) * sin


class RotaryPositionalEncoding(nn.Module):
    """Rotary Positional Embedding (Su et al., RoPE) — inverse-frequency form.

    Precomputes ``cos``/``sin`` for ``max_len`` positions and applies them to
    query/key tensors. Because RoPE rotates Q and K *inside* the attention
    score, no positional signal is added to the residual stream — the value
    path stays position-free, unlike additive sinusoidal encodings whose
    positional bias is then carried into the output of every downstream layer.

    The frequency vector uses the inverse-geometric base 10000 over pairs of
    hidden dimensions (the same ``theta`` convention as the existing
    ``SinusoidalPositionalEncoding`` in ``models/scalp_net.py``, so the two
    encoders are comparable at matched frequency schedules).
    """

    def __init__(self, hidden_dim: int, max_len: int = 512, base: float = 10000.0) -> None:
        super().__init__()
        if hidden_dim % 2 != 0:
            raise ValueError(
                f"RotaryPositionalEncoding requires an even hidden_dim, got {hidden_dim}"
            )
        self.hidden_dim = hidden_dim
        self.max_len = max_len
        self.base = base

        positions = torch.arange(max_len, dtype=torch.float32).unsqueeze(1)
        # (hidden_dim // 2,) inverse frequencies: theta_i = base ** (-2i / d)
        inv_freq = torch.exp(
            torch.arange(0, hidden_dim, 2, dtype=torch.float32) * (-_log_base(base) / hidden_dim)
        )
        freqs = positions * inv_freq  # (max_len, hidden_dim // 2)
        # Duplicate across halves so cos/sin broadcast onto the full width
        # (matches rotate_half's concat layout).
        emb = torch.cat((freqs, freqs), dim=-1)  # (max_len, hidden_dim)
        self.register_buffer("cos_cached", emb.cos().unsqueeze(0), persistent=False)
        self.register_buffer("sin_cached", emb.sin().unsqueeze(0), persistent=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """No-op pass-through; rotary is applied to Q/K only (see ``apply_rotary``).

        Present so this encoder satisfies the shared ``PositionalEncoding``
        interface used by the ablation runner: callers that add the encoding to
        the stream get an unchanged stream under the ``rotary`` kind, and the
        attention layer is responsible for rotating Q/K. This split is the
        whole point of the comparison — additive vs rotary is a *different
        injection point*, not a different function on the same input.
        """
        return x

    def cos_sin(self, seq_len: int, device: torch.device) -> tuple[torch.Tensor, torch.Tensor]:
        """``(cos, sin)`` for the first ``seq_len`` positions, moved to ``device``."""
        seq_len = min(seq_len, self.max_len)
        # register_buffer is typed as Tensor | Module at the stub level; the
        # buffers are tensors by construction (see __init__).
        cos = cast(torch.Tensor, self.cos_cached)[:, :seq_len, :].to(device=device)
        sin = cast(torch.Tensor, self.sin_cached)[:, :seq_len, :].to(device=device)
        return cos, sin


class SinusoidalPositionalEncoding(nn.Module):
    """Deterministic sinusoidal positional encoding.

    Reproduces the exact formula of ``models/scalp_net.py``'s
    ``SinusoidalPositionalEncoding`` (sin on even indices, cos on odd, the
    ``10000`` base) so the ``sinusoidal`` ablation arm is the incumbent
    mechanism and not a near-copy of it. Parameter-free and extrapolates to any
    sequence length below ``max_len``.
    """

    def __init__(self, hidden_dim: int, max_len: int = 512) -> None:
        super().__init__()
        pe = torch.zeros(max_len, hidden_dim)
        position = torch.arange(0, max_len, dtype=torch.float32).unsqueeze(1)
        div_term = torch.exp(
            torch.arange(0, hidden_dim, 2, dtype=torch.float32) * (-_log_base(10000.0) / hidden_dim)
        )
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe.unsqueeze(0), persistent=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """``x: (B, T, H)`` -> ``(B, T, H)`` with positional encoding added."""
        seq_len = x.size(1)
        pe_tensor = cast(torch.Tensor, self.pe)
        return x + pe_tensor[:, :seq_len, :]


class LearnedPositionalEncoding(nn.Module):
    """Learned per-position embedding.

    This is the mechanism ``TCNAttentionV1`` ships today (an
    ``nn.Parameter(1, max_seq_len, hidden_dim)`` added before attention). It
    cannot extrapolate beyond ``max_len`` and is the arm to beat for the
    ``learned`` vs ``sinusoidal`` vs ``rotary`` comparison, since it is the
    incumbent and the only one whose positional signal is trained from zero.
    """

    def __init__(self, hidden_dim: int, max_len: int = 512) -> None:
        super().__init__()
        self.max_len = max_len
        self.embedding = nn.Parameter(torch.zeros(1, max_len, hidden_dim))
        nn.init.normal_(self.embedding, std=0.02)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """``x: (B, T, H)`` -> ``(B, T, H)``; refuses to index past ``max_len``."""
        seq_len = x.size(1)
        if seq_len > self.max_len:
            raise IndexError(
                f"LearnedPositionalEncoding: sequence length {seq_len} exceeds "
                f"max_len={self.max_len}; the learned table does not extrapolate."
            )
        return x + self.embedding[:, :seq_len, :]


class NonePositionalEncoding(nn.Module):
    """Identity encoder — the ``none`` arm of the positional ablation.

    Removing position information entirely is the control that isolates its
    contribution: if ``none`` matches ``sinusoidal``/``learned``/``rotary``
    within noise on validation, positional encoding carries no signal at this
    sequence length and the parameter budget is better spent elsewhere.
    """

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Pass-through."""
        return x


POSITIONAL_ENCODINGS: Final[dict[str, type[nn.Module]]] = {
    POSITION_NONE: NonePositionalEncoding,
    POSITION_SINUSOIDAL: SinusoidalPositionalEncoding,
    POSITION_LEARNED: LearnedPositionalEncoding,
    POSITION_ROTARY: RotaryPositionalEncoding,
}


def create_positional_encoding(kind: str, hidden_dim: int, max_len: int = 512) -> nn.Module:
    """Registry constructor for a positional-encoding kind.

    Raises ``ValueError`` on an unknown kind so a typo in a factory parameter
    fails loudly instead of silently falling back to no positional encoding
    (which would corrupt an ablation arm's identity).
    """
    cls = POSITIONAL_ENCODINGS.get(kind)
    if cls is None:
        supported = ", ".join(sorted(POSITIONAL_ENCODINGS))
        raise ValueError(f"Unknown positional encoding kind {kind!r}; supported: {supported}")
    if kind == POSITION_ROTARY:
        return cls(hidden_dim=hidden_dim, max_len=max_len)
    if kind == POSITION_NONE:
        return cls()
    return cls(hidden_dim=hidden_dim, max_len=max_len)


class CausalSelfAttention(nn.Module):
    """Multi-head self-attention with an explicit triangular causal mask.

    Differences from a bare ``nn.MultiheadAttention``:

    * The mask is built once and *always* applied when ``causal=True`` — the
      causality invariant is a construction property of the layer, not a
      caller obligation. This is the class of bug ML-ARCH-003 exists to close:
      ``TCNAttentionV1`` forwards ``h`` to attention with no mask argument at
      all (``architectures.py:273``).
    * ``causal=False`` reproduces the incumbent (unmasked) behaviour exactly,
      so the ablation can flip ONLY the mask and attribute the delta to it.
    * Rotary support: when a ``RotaryPositionalEncoding`` is supplied, Q/K are
      rotated before the attention scores are computed.

    Batch-first throughout (``(B, T, H)``), matching both existing call sites.
    """

    def __init__(
        self,
        hidden_dim: int,
        num_heads: int,
        dropout: float = 0.0,
        causal: bool = True,
        max_seq_len: int = 512,
    ) -> None:
        super().__init__()
        if hidden_dim % num_heads != 0:
            raise ValueError(f"hidden_dim {hidden_dim} must be divisible by num_heads {num_heads}")
        self.hidden_dim = hidden_dim
        self.num_heads = num_heads
        self.head_dim = hidden_dim // num_heads
        self.causal = causal
        self.max_seq_len = max_seq_len

        self.attn = nn.MultiheadAttention(
            embed_dim=hidden_dim,
            num_heads=num_heads,
            dropout=dropout,
            batch_first=True,
        )
        # Cached mask for the most recently seen sequence length. Rebuilding a
        # (T, T) float mask every forward call is O(T^2) allocation on the
        # inference hot path; the mask depends only on T, so it is safe to
        # reuse across batches.
        self._mask_cache: dict[int, torch.Tensor] = {}

    def _causal_mask(self, seq_len: int, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
        cached = self._mask_cache.get(seq_len)
        if cached is not None and cached.device == device and cached.dtype == dtype:
            return cached
        mask = make_causal_mask(seq_len, device, dtype)
        self._mask_cache[seq_len] = mask
        return mask

    def forward(
        self,
        x: torch.Tensor,
        rotary: RotaryPositionalEncoding | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """``x: (B, T, H)`` -> ``(out, attn_weights)``.

        ``attn_weights`` are returned (unlike the incumbent's
        ``need_weights=False``) because the causality test inspects them: the
        upper-triangular mass must be exactly zero.
        """
        query = key = value = x
        if rotary is not None:
            cos, sin = rotary.cos_sin(x.size(1), x.device)
            # nn.MultiheadAttention packs (B, T, E); rotate in the same layout.
            query = apply_rotary(query, cos, sin)
            key = apply_rotary(key, cos, sin)
        mask: torch.Tensor | None = None
        if self.causal:
            mask = self._causal_mask(x.size(1), x.device, x.dtype)
        out, weights = self.attn(query, key, value, attn_mask=mask, need_weights=True)
        return out, weights
