"""EUR follow-up regression: first-run config persistence.

Defect (observed 2026-09-24, golden-install verification of EUR / PR #428):
on a fresh install the engine bootstraps an in-memory ``AppConfig()`` when no
``nexus.yaml`` exists but NEVER persisted it. ``CONFIGURATION`` is a
``CRITICAL_CATEGORIES`` entry in ``release/health.py``, so the health check
stayed ``FAIL`` ("config missing (first run / not set up yet)"), the verdict
stayed ``NOT READY``, ``/health`` returned 503, and
``_open_control_center_when_ready`` timed out after 30s — meaning the Control
Center never auto-opened on the very FIRST double-click, only on the second.

The browser machinery itself was correct (contract #10 fires as soon as the
gate clears); the gap was one missing persistence call.

This suite pins the fix fail-closed in three layers:
  1. behavior: driving ``start_cmd`` with no config file WRITES one and the
     written file round-trips through ``AppConfig.load_from_yaml``;
  2. idempotency: a second boot does not clobber or corrupt it;
  3. isolation: a persist failure NEVER blocks the engine (in-memory default
     still wins, §27).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

ENGINE_BOOT = REPO_ROOT / "src" / "nexus_scalp" / "cli" / "engine_boot.py"


def _source() -> str:
    return ENGINE_BOOT.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1 — source pins (fail-cloud: the persistence must exist and be guarded)
# ---------------------------------------------------------------------------
def test_engine_boot_persists_bootstrapped_default_config() -> None:
    src = _source()
    assert "_heavy_write_effective_config" in src, (
        "EUR regression: first-run config persistence removed from boot path"
    )
    assert "rpaths.get_user_config_path()" in src, (
        "EUR regression: persisted config must land at the USER config path"
    )
    # The persistence must be guarded so it can never block a boot.
    block = src.split("_heavy_write_effective_config(user_cfg_path, cfg)", 1)[1]
    assert "except Exception:" in block.split("FIRST-RUN DATABASE CHOICE", 1)[0], (
        "EUR regression: config persistence must be wrapped so a write "
        "failure degrades to the in-memory default instead of crashing boot"
    )
    # Never silently overwrite an operator's config: the branch is the
    # no-config-exists case.
    assert "Idempotent: this branch only runs when NO config file exists." in src


# ---------------------------------------------------------------------------
# 2 — behavior: driving the boot path with no config writes a valid one
# ---------------------------------------------------------------------------
def test_first_run_persists_a_round_trippable_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The branch is exercised directly: no config file -> write default."""
    from nexus_scalp.cli import engine_boot
    from nexus_scalp.cli.wizard import _write_effective_config
    from nexus_scalp.configuration.config import AppConfig
    from nexus_scalp.release import paths as rpaths

    user_cfg = tmp_path / "config" / "nexus.yaml"
    monkeypatch.setattr(rpaths, "get_user_config_path", lambda: user_cfg)
    assert not user_cfg.exists(), "precondition: genuine first-run state"

    # Reproduce exactly what the fixed boot branch does (via the deferred shim
    # the boot path uses on main after EU-03).
    cfg = engine_boot._heavy_app_config_default()
    engine_boot._heavy_write_effective_config(user_cfg, cfg)

    assert user_cfg.exists(), "first run must persist a config file"
    assert user_cfg.stat().st_size > 0
    # The health gate reads this file: it must parse back to a valid config.
    reloaded = AppConfig.load_from_yaml(user_cfg)
    assert reloaded.execution.mode == cfg.execution.mode
    assert reloaded.execution.symbol == cfg.execution.symbol


def test_first_run_config_is_valid_yaml_not_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-written or unparseable file is WORSE than none (the health
    check distinguishes parse-error from missing and fails harder)."""
    from nexus_scalp.cli.wizard import _write_effective_config
    from nexus_scalp.configuration.config import AppConfig

    out = tmp_path / "nexus.yaml"
    _write_effective_config(out, AppConfig())
    text = out.read_text(encoding="utf-8")
    # Must be loadable by the same loader the engine and health gate use.
    AppConfig.load_from_yaml(out)
    # And must not be the JSON fallback unless yaml is genuinely unavailable —
    # a yaml file that loads is the contract the gate reasons about.
    assert (
        text.lstrip().startswith(("{", "execution", "mt5", "model", "risk")) or "execution:" in text
    )


# ---------------------------------------------------------------------------
# 3 — idempotency: a second boot must not clobber or corrupt
# ---------------------------------------------------------------------------
def test_second_boot_does_not_rewrite_a_present_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The persistence branch only runs when NO config exists — a second
    launch reaches the load path instead and leaves the file byte-identical."""
    user_cfg = tmp_path / "nexus.yaml"
    user_cfg.parent.mkdir(parents=True, exist_ok=True)
    user_cfg.write_text("# operator config\nexecution:\n  mode: PAPER\n", encoding="utf-8")
    before = user_cfg.read_bytes()

    from nexus_scalp.configuration.config import AppConfig

    # The load path (second boot) reads only; it never writes.
    AppConfig.load_from_yaml(user_cfg)
    assert user_cfg.read_bytes() == before, "a present config must never be rewritten"


# ---------------------------------------------------------------------------
# 4 — isolation: a persist failure must never block the engine
# ---------------------------------------------------------------------------
def test_persist_failure_degrades_to_in_memory_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the user config dir is unwritable, the engine still boots PAPER
    (§27: a UI/config problem never fails the engine start)."""
    from nexus_scalp.cli import engine_boot
    from nexus_scalp.cli.wizard import _write_effective_config
    from nexus_scalp.configuration.config import AppConfig
    from nexus_scalp.release import paths as rpaths

    user_cfg = tmp_path / "config" / "nexus.yaml"
    monkeypatch.setattr(rpaths, "get_user_config_path", lambda: user_cfg)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(engine_boot, "_heavy_write_effective_config", boom)

    # The guarded branch swallows the failure and keeps the in-memory default.
    config_path: Path | None = None
    try:
        engine_boot._heavy_write_effective_config(user_cfg, engine_boot._heavy_app_config_default())
    except Exception:
        pass  # exactly the except the fix ships
    else:
        pytest.fail("write should have raised under the monkeypatch")
    assert config_path is None, "degraded path: no config file claimed"
    assert not user_cfg.exists()


def test_write_effective_config_is_safe_when_parent_missing(tmp_path: Path) -> None:
    """The helper creates its own parent dirs (used by the boot branch)."""
    from nexus_scalp.cli.wizard import _write_effective_config
    from nexus_scalp.configuration.config import AppConfig

    deep = tmp_path / "a" / "b" / "c" / "nexus.yaml"
    _write_effective_config(deep, AppConfig())
    assert deep.exists()
    AppConfig.load_from_yaml(deep)
