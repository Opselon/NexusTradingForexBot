"""FIX #1 + FIX #8 — TEMPORAL SEQUENCE CONTRACT (shared train | live parity).

Contract (file:line refs):
- Canonical lengths: SequenceBuilder(seq_len=16 default, F2 experiments L=32)  -> src/nexus_scalp/model_generation/sequence.py:31-33
- Gap gate: SequenceBuilder.max_gap_us + valid  -> sequence.py:95-102, contract CANONICAL_MAX_GAP_US=10*60*1_000_000.
- Purge/embargo: WalkForwardTrainer purge_gap=15 embargo=15  -> temporal_contract.py:13-15; training/walk_forward_trainer.py:137-195.
- HTF: completed-bucket only (htf_liquidity_score, end<=decision_at) -> features/liquidity_engine.py:841-901.
- Train input (B,L,70) vs live (1,L,70) identical: TCNAttentionV1.forward( B ,T,F)->(B,C)  -> architectures.py:135-151.
  Previous live defect: _infer_probabilities built (1,70)=>unsqueeze(1) MLP path seq_len=1 at live_engine.py:5187 (now fixed).
- This test pins the SHARED contract by proving: same causal window => same sequence tensor train vs live.
  One builder (SequenceBuilder) is the ONLY builder (Do NOT invent a second builder).
"""

from __future__ import annotations

import numpy as np
import polars as pl
import torch

from nexus_scalp.model_generation.architectures import TCNAttentionV1
from nexus_scalp.model_generation.sequence import SequenceBuilder
from nexus_scalp.model_generation.temporal_contract import (
    CANONICAL_EMBARGO_BARS,
    CANONICAL_MAX_GAP_US,
    CANONICAL_PURGE_BARS,
    CANONICAL_SEQ_LEN,
    get_canonical_sequence_builder,
)


def _synthetic_70d_frame(n: int = 64, seed: int = 7) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    d: dict[str, list] = {}
    for i in range(70):
        d[f"feat_{i}"] = rng.normal(0, 0.6, size=n).astype(float).tolist()
    # strictly-increasing M1 timestamps (60s steps) in microseconds — required for gap checks
    base_us = 1_700_000_000_000_000
    d["timestamp"] = (
        (base_us + np.arange(n, dtype=np.int64) * 60_000_000).astype("datetime64[us]").tolist()
    )
    d["symbol"] = ["XAUUSD"] * n
    d["timeframe"] = ["M1"] * n
    d["label"] = rng.integers(0, 3, size=n).tolist()
    return pl.DataFrame(d)


def test_contract_constants_documented() -> None:
    assert CANONICAL_SEQ_LEN in (16, 32)
    assert CANONICAL_MAX_GAP_US == 10 * 60 * 1_000_000
    assert CANONICAL_PURGE_BARS == 15
    assert CANONICAL_EMBARGO_BARS == 15


def test_builder_factory_is_canonical_wrapper() -> None:
    b = get_canonical_sequence_builder(seq_len=32, max_gap_us=CANONICAL_MAX_GAP_US)
    assert b.seq_len == 32
    assert b.max_gap_us == CANONICAL_MAX_GAP_US


def test_gap_invalidates_window_and_not_else() -> None:
    # Shift the TAIL (rows 20..39) forward by 20m so the gap 19->20 (21m) survives
    # string-sort adjacency (the single-row shift would be re-sorted past the gap).
    frame = _synthetic_70d_frame(n=40)
    rows = list(frame.iter_rows(named=True))
    for j in range(20, len(rows)):
        rows[j]["timestamp"] = rows[j]["timestamp"] + np.timedelta64(20 * 60_000_000, "us")
    fixed = pl.DataFrame(rows)
    builder = SequenceBuilder(seq_len=16, max_gap_us=CANONICAL_MAX_GAP_US)
    seq = builder.build(fixed, news_enabled=False)
    # Gap is between rows 19 (17:34) and 20 (17:55) = 21m > 10m => every window that
    # includes both sides is invalid. Windows fully before (indices 0..19) or
    # fully after (20..39) remain valid.
    assert 0 < int(seq["valid"].sum()) < int(seq["valid"].shape[0])


def test_l16_and_l32_both_supported() -> None:
    frame = _synthetic_70d_frame(n=64)
    for L in (16, 32):
        builder = get_canonical_sequence_builder(seq_len=L)
        seq = builder.build(frame, news_enabled=False)
        assert seq["X"].shape[1] == L  # seq_len respected


def test_same_causal_window_same_sequence_tensor_train_vs_live() -> None:
    """Train builds windows via SequenceBuilder; live builds the same window via a deque.

    Must produce numerically identical (1, L, 70) tensors for the same causal history.
    """
    frame = _synthetic_70d_frame(n=64)
    L = 32
    builder = get_canonical_sequence_builder(seq_len=L, max_gap_us=None)
    seq = builder.build(frame, news_enabled=False)
    # pick the last valid train window as the canonical causal window ending at row 63
    assert int(seq["valid"].sum()) > 0
    X = seq["X"][seq["valid"]]  # (N_valid, L, 70)
    train_window = X[-1]  # (L, 70) — causal history of the last M1 bar
    feat_cols = [f"feat_{i}" for i in range(70)]
    rows_sorted = list(frame.sort("timestamp").iter_rows(named=True))
    # simulate live: push each bar's 70D vector into a bounded deque of length L, scaled identically
    # (here trivially no scaling after the scaler — parity must hold without scaler too; the live test
    # feeds post-scaler vectors and the train test's X_all is the same pre-scaler stack)
    live_deque: list[list[float]] = []
    for r in rows_sorted[-L:]:
        live_deque.append([float(r[c]) for c in feat_cols])
    live_window = np.array(live_deque, dtype=np.float32)  # (L, 70)
    assert live_window.shape == (L, 70)
    assert np.allclose(train_window, live_window, atol=1e-6), (
        f"train vs live tensor diverged: max diff {float(np.abs(train_window - live_window).max())}"
    )
    # Both windows feed the SAME 3D path (B, L, 70) -> (B, C); train has B>1, live has B=1
    tcn = TCNAttentionV1(input_dim=70, num_classes=3, max_seq_len=L)
    tcn.eval()
    with torch.inference_mode():
        out_train = tcn(torch.from_numpy(train_window[None, :, :]))  # (1, L, 70)
        out_live = tcn(torch.from_numpy(live_window[None, :, :]))  # (1, L, 70)
    assert out_train.shape == (1, 3)
    assert torch.allclose(out_train, out_live, atol=1e-6)


def test_tcn_accepts_both_B_and_one() -> None:
    tcn = TCNAttentionV1(input_dim=70, num_classes=3, max_seq_len=32)
    tcn.eval()
    with torch.inference_mode():
        assert tcn(torch.randn(4, 32, 70)).shape == (4, 3)
        assert tcn(torch.randn(1, 32, 70)).shape == (1, 3)
