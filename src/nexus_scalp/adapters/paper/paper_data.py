"""Paper data-mode factory (BUG-266: wire the shipped REPLAY source to boot).

ROLE IN THE ARCHITECTURE (audit K2, 2026-09-07):
``PaperMT5Adapter`` gained an explicit REPLAY market-data mode in a5f38467
(P0 phase 4) — real historical chronology via ``ReplayTickSource`` so
paper-derived experience carries real-market statistics instead of a
synthetic AR(1) walk around a hardcoded seed. But the wiring was never
built: every production construction site passed no ``replay_source``, so
BOTH PAPER entrypoints (``nexus start`` and the double-click launcher) —
and both mode-boundary re-alignments (BUG-212 boot align, BUG-148
hot-swap) — silently kept SYNTHETIC mode forever. K2's fix existed and
was dead.

This module is the SINGLE decision point every PAPER construction must
route through:

* ``SYNTHETIC`` (default, unchanged): AR(1) walk, CI determinism.
* ``REPLAY``: a ``ReplayTickSource`` over the dataset cache (preferred) or
  the committed real M1 bar export, attached to the adapter explicitly.

Integrity contract (mission 5C, preserved here):
* REPLAY is fail-closed at the interactive boot surface
  (``on_replay_unavailable="raise"``): an operator who asked for real
  replay data is refused loudly rather than served a synthetic world that
  masquerades as evidence.
* The two mode-boundary RE-ALIGNMENT paths use
  ``on_replay_unavailable="synthetic"`` — a PAPER re-alignment that fails
  must still land on the SIMULATION adapter (BUG-212's production failure
  was a PAPER badge over a REAL broker; the execution boundary outranks
  the data-truth boundary). The degradation is NEVER silent: a loud
  structured WARNING plus an explicit ``degraded_reason`` stamped into the
  adapter's ``replay_provenance``, so the session can never be attributed
  to real-market data.
* ``allow_replay=False`` (SHADOW): shadow evidence must observe the REAL
  feed contract, so replay data is never attached there; behavior is
  exactly what it was before BUG-266 (SYNTHETIC simulation adapter).

The data mode is BOOT-TIME ONLY by design: swapping the market-data
substrate mid-session crosses the BUG-232 cross-mode invalidation contract
(state derived from the old stream), so a change requires a restart / a
real mode switch, not a hot config edit. ``AppConfig.paper_data`` is
therefore deliberately absent from the runtime-config snapshot sections.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.adapters.paper.replay_source import (
    ReplayDataUnavailableError,
    ReplayTickSource,
)
from nexus_scalp.observability.logging import get_logger

if TYPE_CHECKING:
    from nexus_scalp.configuration.config import PaperDataConfig

logger = get_logger("nexus_scalp.adapters.paper.data")

#: How a REPLAY request reacts when the historical source is unusable.
ON_REPLAY_UNAVAILABLE_RAISE = "raise"
ON_REPLAY_UNAVAILABLE_SYNTHETIC = "synthetic"


def build_paper_adapter(
    *,
    symbol: str,
    initial_balance: float = 10000.0,
    paper_data: PaperDataConfig | None = None,
    allow_replay: bool = True,
    on_replay_unavailable: str = ON_REPLAY_UNAVAILABLE_RAISE,
) -> PaperMT5Adapter:
    """Construct the PAPER adapter in the configured market-data mode.

    Args:
        symbol: active execution symbol (BUG-232: conventions must match).
        initial_balance: starting simulated balance for re-alignment paths.
        paper_data: ``AppConfig.paper_data`` block (None = SYNTHETIC default).
        allow_replay: False disables REPLAY regardless of config (SHADOW).
        on_replay_unavailable: ``"raise"`` (interactive boot, fail-closed) or
            ``"synthetic"`` (boundary re-alignment, degrade loudly).

    Returns:
        A PaperMT5Adapter whose ``market_data_mode``/``replay_provenance``
        state the truth of what it is serving.
    """
    mode = "SYNTHETIC"
    if paper_data is not None and allow_replay:
        mode = str(getattr(paper_data, "mode", "SYNTHETIC") or "SYNTHETIC").upper()

    if mode != "REPLAY":
        if (
            paper_data is not None
            and allow_replay is False
            and _configured_mode(paper_data) == "REPLAY"
        ):
            logger.warning(
                "[PAPER] event=REPLAY_NOT_ALLOWED_FOR_MODE reason=SHADOW_keeps_real_feed "
                "serving=SYNTHETIC — REPLAY is a PAPER-only data mode (BUG-266)"
            )
        return PaperMT5Adapter(initial_balance=initial_balance, symbol=symbol)

    if allow_replay is False:  # pragma: no cover - defensive double gate
        return PaperMT5Adapter(initial_balance=initial_balance, symbol=symbol)

    try:
        source = ReplayTickSource(
            symbol=symbol,
            dataset_id=str(getattr(paper_data, "dataset_id", "") or ""),
            allow_raw_fallback=bool(getattr(paper_data, "allow_raw_fallback", True)),
            raw_bars_path=str(getattr(paper_data, "raw_bars_path", "") or "data/raw/XAUUSD_M1.csv"),
        )
    except ReplayDataUnavailableError as exc:
        if on_replay_unavailable != ON_REPLAY_UNAVAILABLE_SYNTHETIC:
            # Fail-closed: never serve a synthetic walk to an operator who
            # explicitly asked for real replay data.
            raise
        logger.error(
            "[PAPER] event=REPLAY_UNAVAILABLE_FALLBACK_SYNTHETIC error=%r — execution "
            "boundary (PAPER simulation) kept, data-truth boundary DEGRADED; this "
            "session is SYNTHETIC and must never be attributed to real-market replay",
            str(exc),
        )
        adapter = PaperMT5Adapter(initial_balance=initial_balance, symbol=symbol)
        prov: dict[str, Any] = dict(getattr(adapter, "replay_provenance", {}) or {})
        prov["requested_mode"] = "REPLAY"
        prov["degraded_reason"] = f"REPLAY_UNAVAILABLE: {exc}"
        adapter.replay_provenance = prov
        return adapter

    adapter = PaperMT5Adapter(
        initial_balance=initial_balance,
        symbol=symbol,
        replay_source=source,
    )
    logger.info(
        "[PAPER] event=REPLAY_WIRED_AT_BOOT symbol=%s source=%s records=%s",
        symbol,
        adapter.replay_provenance.get("source"),
        adapter.replay_provenance.get("record_count"),
    )
    return adapter


def _configured_mode(paper_data: Any) -> str:
    return str(getattr(paper_data, "mode", "SYNTHETIC") or "SYNTHETIC").upper()
