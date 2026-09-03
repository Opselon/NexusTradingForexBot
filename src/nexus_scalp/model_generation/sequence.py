"""Deterministic Sequence Builder (PHASE 13B).

Temporal data contract (spec 5):

    * a model sequence satisfies timestamp_0 < timestamp_1 < ... < timestamp_N
    * every timestep shares the same symbol / timeframe / feature schema /
      news schema
    * NO sequence crosses a symbol boundary, timeframe boundary, or an
      invalid market-data gap (configurable max gap)
    * deterministic ordering

Sequences are built from an already-labeled, chronologically-sorted
dataset frame using ONLY the artifacts' stored sample ordering — no future
information and no cross-boundary contamination.

TASK ARCH-SEQ-UNIFY — SINGLE SOURCE OF TRUTH
--------------------------------------------
The canonical temporal contract VALUES (sequence length, feature dim,
max inter-bar gap, schema id) live ONLY in
``nexus_scalp.model_generation.temporal_contract``. The module-level names
below (SEQUENCE_LENGTH / FEATURE_DIM / MAX_GAP_US / SCHEMA_ID and the
``SequenceContract`` dataclass defaults) are frozen ALIASES re-exported
from temporal_contract — kept for backward compatibility with existing
imports (sequence_training, benchmark, tests). One direction only:
temporal_contract is authoritative; nothing here redefines a value.
"""

from __future__ import annotations

# =============================================================================
# CANONICAL TEMPORAL SEQUENCE CONTRACT (TASK MLFIX-T2 / PHASE 14 UNIFICATION)
# =============================================================================
#
# WHY THIS EXISTS
# ---------------
# The 70D model family has TWO execution paths that MUST consume sequences
# with IDENTICAL semantics: offline training/validation/OOS (datasets built
# by SequenceBuilder) and live inference (LiveEngine._infer_probabilities).
# Before MLFIX-T2 the live path fed (1, 70) single-snapshot tensors -> the
# 2D MLP branch of ScalpNet -> the TCN+MHA temporal path was NEVER trained
# and NEVER served. This contract is the single source of truth both paths
# consume; the invariant is:
#
#     TRAIN INPUT (B, L, 70)  ==  LIVE INPUT (1, L, 70)   (identical
#     window construction, ordering, scaling stage and feature layout)
#
# WHY L = 32 (evidence, not taste)
# --------------------------------
#   1. RECEPTIVE FIELD. ScalpNet v3's causal TCN stack is 3 layers with
#      kernel 3 at dilations 1/2/4 -> causal receptive field
#      1 + (3-1)*(1+2+4) = 15 bars for the conv path; MHA then mixes the
#      whole window and pooling takes h[:, -1, :]. L must be > 15 for the
#      conv stack to see a full field AND leave headroom for attention to
#      relate distant bars: L=32 = 2x RF covers ~2 micro-regime windows.
#   2. LATENCY. The MLFix hot-path note measures ~1-2 ms per forward at
#      L=32 on CPU (267k-param net, single-thread pinned, as live already
#      does) - inside the tick budget. L=64 doubles that for no proven
#      OOS gain.
#   3. EVIDENCE. The F2 sequence harness (MLFix status board: commit
#      ccb7765c, artifacts t70d_seq_v1 / t70d_seq_v2_tuned) trained and
#      validated end-to-end at L=32: VAL accuracy 0.429/0.448, OOS
#      0.377/0.371. It is the ONLY sequence length with a completed
#      train->validate->OOS record on the full-history 70D dataset.
#   4. DATA SPARSITY. Triple-Barrier yields ~5,752 evaluable rows over
#      100k M1 bars; windows at L=32 stride 1 waste the fewest labeled
#      rows to history (32 bars) while L=128 would orphan the head of
#      every fold segment.
# A future L change is a CONTRACT VERSION bump + retrain; never a silent
# serving-time switch.
#
# GAP / BOUNDARY HANDLING
# -----------------------
# A window is VALID only when every timestep is contiguous in time (no
# inter-bar gap > MAX_GAP_US), inside one symbol+timeframe block, and the
# frame's fold purge/embargo boundaries are respected by the caller
# (SequenceCandidateTrainer / walk-forward split helpers). Invalid windows
# are EXCLUDED (valid=False), never padded with fabricated/foreign bars.
# Cold-start on live: if fewer than L post-scaler vectors are buffered the
# engine falls back to the 2D path with an explicit logged warning - the
# fallback is visible, never silent.
# =============================================================================
from dataclasses import dataclass
from datetime import UTC
from typing import Any

import numpy as np
import polars as pl

# ---------------------------------------------------------------------------
# TASK ARCH-SEQ-UNIFY: these are ALIASES of the canonical contract defined in
# temporal_contract.py (the single source of truth). No local literals —
# change the value there, and every consumer of the sequence contract
# (SequenceBuilder defaults, SequenceContract, SequenceCandidateTrainer,
# live rebind) follows. Kept under the historic names for backward compat.
# ---------------------------------------------------------------------------
from nexus_scalp.model_generation.temporal_contract import (
    CANONICAL_MAX_GAP_US as MAX_GAP_US,  # canonical max inter-bar gap (us)
)
from nexus_scalp.model_generation.temporal_contract import (
    CANONICAL_SCHEMA_ID as SCHEMA_ID,  # canonical 70D feature schema id
)
from nexus_scalp.model_generation.temporal_contract import (
    CANONICAL_SEQ_LEN as SEQUENCE_LENGTH,  # canonical sequence length L
)
from nexus_scalp.model_generation.temporal_contract import (
    FEATURE_DIM,  # canonical 70D feature width (defined in temporal_contract)
)

__all__ = [
    "FEATURE_DIM",
    "MAX_GAP_US",
    "SCHEMA_ID",
    "SEQUENCE_CONTRACT",
    "SEQUENCE_LENGTH",
    "SequenceBuilder",
    "SequenceContract",
]


@dataclass(frozen=True)
class SequenceContract:
    """Immutable temporal-contract descriptor (v1).

    A model artifact trained through SequenceCandidateTrainer stamps
    ``trained_mode="sequence"``, ``seq_len`` and ``feature_dimension``
    (via build_metadata / meta.json). The live loader asserts the serving
    tensor it builds matches these EXACTLY; any mismatch is a loud
    fallback to the 2D path, never a silent reinterpretation.

    Field defaults are ALIASES of temporal_contract.CANONICAL_* — do not
    add local literals here.
    """

    sequence_length: int = SEQUENCE_LENGTH
    feature_dim: int = FEATURE_DIM
    max_gap_us: int | None = MAX_GAP_US
    schema_id: str = SCHEMA_ID
    trained_mode: str = "sequence"
    contract_version: str = "1"

    def __post_init__(self) -> None:
        if self.sequence_length < 2:
            raise ValueError("SequenceContract: sequence_length must be >= 2")
        if self.feature_dim <= 0:
            raise ValueError("SequenceContract: feature_dim must be > 0")
        if self.trained_mode not in ("sequence", "2d"):
            raise ValueError(f"SequenceContract: unknown trained_mode {self.trained_mode!r}")

    def describe(self) -> str:
        return (
            f"temporal-sequence-contract v{self.contract_version}: "
            f"L={self.sequence_length} C={self.feature_dim} "
            f"max_gap_us={self.max_gap_us} schema={self.schema_id} "
            f"mode={self.trained_mode}"
        )


#: Singleton instance both dataset generation and live serving import.
SEQUENCE_CONTRACT: SequenceContract = SequenceContract()


class SequenceBuilder:
    """Builds fixed-length causal sequences from a labeled dataset frame."""

    def __init__(
        self,
        seq_len: int | None = None,
        max_gap_us: int | str | None = "contract",
    ) -> None:
        # ``seq_len=None`` / ``max_gap_us="contract"`` (sentinel) = take the
        # CANONICAL contract values (L=32, gap=10min). An explicit int wins
        # (benchmark matrix builds L=8/16 ablations); an explicit None for
        # max_gap_us means "no gap check" (legacy ablation semantics).
        if seq_len is None:
            seq_len = SEQUENCE_CONTRACT.sequence_length
        if max_gap_us == "contract":
            max_gap_us = SEQUENCE_CONTRACT.max_gap_us
        self.seq_len = max(2, int(seq_len))
        #: max allowed inter-bar gap in microseconds; None = no gap check
        self.max_gap_us = max_gap_us  # type: ignore[assignment]

    def build(
        self,
        frame: pl.DataFrame,
        *,
        label_col: str = "label",
        timestamp_col: str = "timestamp",
        symbol_col: str = "symbol",
        timeframe_col: str = "timeframe",
        news_enabled: bool = True,
    ) -> dict[str, np.ndarray]:
        """Returns {X: (N, seq_len, F), y: (N,), valid: (N,) bool}.

        Only rows where a FULL causal history of `seq_len` exists within the
        same symbol/timeframe boundary and within the max gap become
        sequence samples (valid=True). Rows at the start of a boundary are
        excluded — never padded with foreign/borrowed data.

        ``news_enabled=False`` EXCLUDES news_* columns from the sequence
        feature vector (news OFF ablation removes NewsContext entirely).
        """
        if frame is None or frame.is_empty():
            return {
                "X": np.zeros((0, self.seq_len, 0), dtype=np.float32),
                "y": np.zeros(0, dtype=np.int64),
                "valid": np.zeros(0, dtype=bool),
            }

        # FRAME-ORDER columns (matches the 2D path + DatasetFactory output;
        # lexicographic sort would silently reorder feat_10 before feat_2)
        feat_cols = [c for c in frame.columns if c.startswith("feat_")]
        news_cols = [
            c for c in frame.columns if c.startswith("news_") and c != "news_context_schema_id"
        ]
        if not news_enabled:
            news_cols = []
        input_cols = feat_cols + news_cols
        if not feat_cols:
            raise ValueError("SequenceBuilder: no feat_* columns in frame")

        rows = [r for r in frame.iter_rows(named=True)]
        # chronological order within the frame (already sorted by the
        # dataset factory; re-sort defensively)
        rows.sort(key=lambda r: str(r.get(timestamp_col, "")))

        X_list: list[np.ndarray] = []
        y_list: list[int] = []
        valid_list: list[bool] = []

        i = self.seq_len - 1
        while i < len(rows):
            window = rows[i - self.seq_len + 1 : i + 1]
            symbol = str(window[-1].get(symbol_col, ""))
            timeframe = str(window[-1].get(timeframe_col, ""))
            boundary_ok = all(
                str(r.get(symbol_col, "")) == symbol and str(r.get(timeframe_col, "")) == timeframe
                for r in window
            )
            gap_ok = True
            if self.max_gap_us is not None:
                ts_prev = _ts_us(window[0].get(timestamp_col))
                for r in window[1:]:
                    ts_cur = _ts_us(r.get(timestamp_col))
                    if ts_cur - ts_prev > self.max_gap_us:
                        gap_ok = False
                        break
                    ts_prev = ts_cur

            vec = np.array(
                [[float(r.get(c, 0.0)) for c in input_cols] for r in window],
                dtype=np.float32,
            )
            X_list.append(vec)
            y_list.append(int(window[-1].get(label_col, 0)))
            valid_list.append(boundary_ok and gap_ok)
            i += 1

        if not X_list:
            return {
                "X": np.zeros((0, self.seq_len, len(input_cols)), dtype=np.float32),
                "y": np.zeros(0, dtype=np.int64),
                "valid": np.zeros(0, dtype=bool),
            }
        return {
            "X": np.stack(X_list),
            "y": np.array(y_list, dtype=np.int64),
            "valid": np.array(valid_list, dtype=bool),
        }


_EPOCH = None  # module-level epoch anchor, built lazily below


def _ts_us(value: Any) -> int:
    """Parses a timestamp cell to epoch microseconds.

    Windows-safe: ``datetime(1970,1,1).timestamp()`` raises OSError [Errno 22]
    on Windows for naive datetimes at/before the epoch (OS localtime
    dependence), so epoch math is done against an explicit anchor instead —
    correct for naive AND aware datetimes on every platform. A naive datetime
    is interpreted as UTC (deterministic; gap checking only ever subtracts
    two timestamps from the same frame, so the shared convention cancels).
    """
    global _EPOCH  # noqa: PLW0603
    if _EPOCH is None:
        from datetime import datetime

        _EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
    if value is None:
        return 0
    if hasattr(value, "timestamp"):  # datetime-like
        try:
            # Windows-safe: works for naive AND aware datetimes, any year.
            return int((value - _EPOCH).total_seconds() * 1_000_000)
        except TypeError:
            # naive datetime vs aware anchor — re-anchor in the same (naive) frame
            from datetime import datetime as _dt

            return int((value - _dt(1970, 1, 1)).total_seconds() * 1_000_000)
        except (OverflowError, OSError, ValueError):
            return 0
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        try:
            return int((dt - _EPOCH).total_seconds() * 1_000_000)
        except TypeError:
            return int((dt - datetime(1970, 1, 1)).total_seconds() * 1_000_000)
    except (OverflowError, OSError, ValueError):
        return 0
