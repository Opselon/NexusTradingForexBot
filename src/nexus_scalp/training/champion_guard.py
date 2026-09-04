"""CHAMPION GUARD — training/tests/probes must never write the serving bundle.

P0-2026-09-04 producer fix. The 34x10 production launch passed the canonical
CHAMPION bundle path (artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt)
straight into WalkForwardTrainer.artifact_save_path, so the trainer's atomic
per-file publishes (checkpoint / scaler / meta) targeted the LIVE serving
artifacts. Sidecars were additionally clobbered by unrelated short jobs
(benchmark/retrain json writers), producing the P0 incoherence:

    tensor head [4,32] + metadata 3-class + dataset_id=null

This module is the single structural denial point:

* assert_not_champion_path() raises ChampionPathError BEFORE any training work
  when a save path resolves to a canonical serving bundle.
* resolve_under() / resolve_relative_to() bind relative artifact paths to an
  approved artifact root (no traversal, no symlink escape).

The guard compares REALPATHS, so symlink aliasing of the champion directory is
also rejected. An explicit operator opt-in (allow_champion_save=True) is the
only way to write a canonical variant bundle, and it must be one of the
documented variant paths in CANONICAL_VARIANT_ALLOWLIST.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Canonical serving bundles (the LIVE champion variants). Relative to repo
#: root; resolved with realpath at check time.
CANONICAL_CHAMPION_PATHS: tuple[str, ...] = (
    "artifacts/models/scalp/XAUUSD/50d_main/model.pt",
    "artifacts/models/scalp/XAUUSD/70d_news/model.pt",
    "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
)

#: Documented variant paths an explicit allow_champion_save=True operator run
#: may target (same list today; kept separate so policy can diverge later).
CANONICAL_VARIANT_ALLOWLIST: frozenset[str] = frozenset(CANONICAL_CHAMPION_PATHS)

#: Approved artifact roots relative to the repo root. Any model save/load path
#: used by the producer must resolve under one of these.
APPROVED_ARTIFACT_ROOTS: tuple[str, ...] = (
    "artifacts/models",
    "artifacts/model_generation",
)

_MODEL_PT_TAIL = ("model.pt",)


class ChampionPathError(RuntimeError):
    """Raised when a save/load path would touch a canonical serving bundle."""


def repo_root() -> Path:
    """Repository root inferred from this file (src/nexus_scalp/training/...).

    Falls back to CWD when the layout is unexpected (packaged builds), in
    which case relative paths are resolved against CWD exactly as before.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() or (parent / "setup.py").exists():
            return parent
    return Path.cwd()


def _real(path: Path | str) -> Path:
    return Path(os.path.realpath(Path(path)))


def canonical_champion_realpaths() -> frozenset[Path]:
    root = repo_root()
    return frozenset(_real(root / rel) for rel in CANONICAL_CHAMPION_PATHS)


def is_champion_path(path: Path | str) -> bool:
    """True when ``path`` resolves to a canonical serving bundle file."""
    p = _real(path)
    return p in canonical_champion_realpaths()


def is_canonical_variant_path(path: Path | str) -> bool:
    p = _real(path)
    root = repo_root()
    for rel in CANONICAL_VARIANT_ALLOWLIST:
        if p == _real(root / rel):
            return True
    return False


def assert_not_champion_path(
    path: Path | str,
    *,
    allow_champion_save: bool = False,
    context: str = "",
) -> None:
    """Fail loudly when ``path`` targets a canonical serving bundle.

    allow_champion_save=True permits ONLY the documented canonical variant
    paths (never arbitrary artifacts/models/scalp/** locations). Any other
    path that resolves under the serving tree (including via symlink) is
    rejected regardless of the flag.
    """
    p = _real(path)
    name = p.name
    if name not in _MODEL_PT_TAIL:
        # sidecars (model.scaler.npz / model.meta.json) map to their model.pt
        name = "model.pt" if ".pt" in name else name
    if is_champion_path(p):
        if allow_champion_save and is_canonical_variant_path(p):
            return  # explicit operator opt-in for a documented variant
        raise ChampionPathError(
            f"CHAMPION_GUARD_ABORT{' in ' + context if context else ''}: "
            f"refusing to write canonical serving bundle {p}. Training, tests, "
            f"probes and candidates must target an isolated path under "
            f"artifacts/model_generation/models/. Pass allow_champion_save=True "
            f"only via the governed producer for a documented variant path."
        )


def _approved_roots_real() -> list[Path]:
    root = repo_root()
    return [_real(root / rel) for rel in APPROVED_ARTIFACT_ROOTS]


def resolve_under(path: Path | str) -> Path:
    """Resolve a model-artifact path and enforce the approved-root allow-list.

    Rejects: traversal outside the roots, symlink escapes, and paths that land
    on a canonical serving bundle (champion protection is unconditional for
    loaders; producers additionally route through assert_not_champion_path).
    """
    p = _real(path)
    for root in _approved_roots_real():
        try:
            p.relative_to(root)
        except ValueError:
            continue
        assert_not_champion_path(p, context="resolve_under")
        return p
    raise ChampionPathError(
        f"CHAMPION_GUARD_ABORT: path {p} is outside the approved artifact "
        f"roots {APPROVED_ARTIFACT_ROOTS} (traversal / external model file)."
    )
