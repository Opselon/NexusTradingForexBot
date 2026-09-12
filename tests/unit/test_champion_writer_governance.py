"""PHASE 13 regression: canonical champion writes require governed authorization.

Pins the writer-governance invariant that failed historically (BUG-257):
every in-repo producer path that could historically write the canonical
champion must either (a) refuse, or (b) require the explicit governed opt-in.
"""

from pathlib import Path

import pytest

from nexus_scalp.training.champion_guard import (
    CANONICAL_CHAMPION_PATHS,
    ChampionPathError,
    assert_not_champion_path,
    is_champion_path,
    resolve_under,
)

CANON = Path("artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt")


def test_canonical_path_is_recognized() -> None:
    assert is_champion_path(CANON)


def test_training_default_write_is_refused() -> None:
    """WalkForwardTrainer default/isolated save paths must never resolve to champion."""
    for p in (
        CANON,
        Path("artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt.tmp"),
        Path("artifacts/models/scalp/XAUUSD/70d_liquidity/bundle/model.pt"),
    ):
        with pytest.raises(ChampionPathError):
            assert_not_champion_path(p, context="regression: unauthorized write")


def test_ungoverned_optin_outside_allowlist_is_refused() -> None:
    """allow_champion_save=True is only valid for the DOCUMENTED variant paths."""
    rogue = Path("artifacts/models/scalp/XAUUSD/70d_liquidity/rogue/model.pt")
    with pytest.raises(ChampionPathError):
        assert_not_champion_path(rogue, allow_champion_save=True, context="regression: rogue")


def test_documented_variant_optin_is_permitted() -> None:
    """The explicit governed opt-in path (three_model output_dir='champion:...')."""
    assert_not_champion_path(CANON, allow_champion_save=True, context="governed opt-in")


def test_external_paths_are_rejected_by_resolve_under() -> None:
    with pytest.raises(ChampionPathError):
        resolve_under(Path("C:/Windows/Temp/evil_model.pt"))
    with pytest.raises(ChampionPathError):
        resolve_under(Path("../../outside/model.pt"))


def test_canonical_allowlist_is_exact() -> None:
    assert set(CANONICAL_CHAMPION_PATHS) == {
        "artifacts/models/scalp/XAUUSD/50d_main/model.pt",
        "artifacts/models/scalp/XAUUSD/70d_news/model.pt",
        "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
    }
