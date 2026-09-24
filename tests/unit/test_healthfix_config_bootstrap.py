"""NSE-HEALTHFIX-001 lane B — first-run config bootstrap honesty.

The health probe returned CONFIGURATION FAIL/NOT_INITIALIZED because
``%LOCALAPPDATA%/NexusScalpEngine/config/nexus.yaml`` was absent, yet the engine
booted fine through the ``configs/base.yaml`` fallback (engine_boot.py:382-388).
The gap is not "health checks the wrong path" — it is that the repair path, the
one place allowed to create the user config, reported a silent OK for a config
that was present-but-unloadable and never proved a freshly bootstrapped config
actually loads.

These tests pin the honest contract (contract §4):
  * template present -> the user config is created, and a second run() is a
    no-op (idempotent);
  * an EXISTING user config is preserved across run(recreate_dirs=False) —
    byte-for-byte;
  * an existing INVALID config reports FAILED with a fix suggestion, never a
    silent OK, and is NOT overwritten;
  * run() never deletes or overwrites user data;
  * doctor --fix reaches RepairEngine for a CONFIGURATION failure (the path the
    operator is told to walk); this lane owns the contract, not doctor.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus_scalp.release import paths as rpaths
from nexus_scalp.release import repair as rrepair

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "configs" / "base.yaml"


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point EVERY user-config path lookup at a tmp dir.

    ``paths.get_user_config_path`` is resolved at call time (not import time),
    so monkeypatching the module function is enough; repair.py calls it through
    the module, never through a cached attribute.
    """
    user_cfg = tmp_path / "NexusScalpEngine" / "config" / "nexus.yaml"
    monkeypatch.setattr(rpaths, "get_user_config_path", lambda: user_cfg)
    # The other _ensure_* steps resolve real per-user dirs; keep them inside the
    # tmp workspace too so a run() never touches the operator's machine state.
    monkeypatch.setattr(rpaths, "app_data_root", lambda: tmp_path / "NexusScalpEngine")
    return user_cfg


def _engine(tmp_path: Path) -> rrepair.RepairEngine:
    # template_config defaults to <workspace>/configs/base.yaml; a tmp workspace
    # has none, so anchor the template to the REAL packaged default. The
    # template itself is a read-only shipped artifact (contract: lanes must not
    # change the packaged default model path) — we only READ it here.
    return rrepair.RepairEngine(workspace=tmp_path, data_root=tmp_path, template_config=TEMPLATE)


# ---------------------------------------------------------------------------
# 1. First-run bootstrap: the template is copied and it actually loads
# ---------------------------------------------------------------------------
def test_first_run_creates_loadable_config(tmp_path: Path) -> None:
    assert TEMPLATE.exists(), "packaged template configs/base.yaml must ship in the tree"
    eng = _engine(tmp_path)
    res = eng.repair_configuration()

    assert res.action == "config"
    assert res.status == "OK", res.detail
    user_cfg = rpaths.get_user_config_path()
    assert user_cfg.exists()
    # The created config is loadable through the SAME loader the engine and
    # check_configuration use — a bootstrap that hands the operator an
    # unloadable config could never report OK.
    from nexus_scalp.configuration.config import AppConfig

    cfg = AppConfig.load_from_yaml(user_cfg)
    assert cfg.execution.mode.value != "LIVE", "repair template must never default to LIVE"


def test_template_absent_is_skipped_not_failed(tmp_path: Path) -> None:
    """No packaged template: honest SKIPPED, never a crash, never a fabricated OK."""
    eng = rrepair.RepairEngine(
        workspace=tmp_path, data_root=tmp_path, template_config=tmp_path / "nope.yaml"
    )
    res = eng.repair_configuration()
    assert res.status == "SKIPPED"
    assert not rpaths.get_user_config_path().exists()


# ---------------------------------------------------------------------------
# 2. Idempotency: a second run() does not touch the file
# ---------------------------------------------------------------------------
def test_run_is_idempotent_second_run_preserves_bytes(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    first = eng.run(with_news=False)
    cfg_result = next(r for r in first if r.action == "config")
    assert cfg_result.status == "OK", cfg_result.detail

    user_cfg = rpaths.get_user_config_path()
    assert user_cfg.exists()
    digest1 = user_cfg.read_bytes()

    second = eng.run(with_news=False)
    cfg_result2 = next(r for r in second if r.action == "config")
    assert cfg_result2.status == "OK", cfg_result2.detail
    assert user_cfg.read_bytes() == digest1, "idempotent run must not rewrite the config"


# ---------------------------------------------------------------------------
# 3. An existing user config is NEVER overwritten by a plain run()
# ---------------------------------------------------------------------------
def test_existing_config_preserved_across_run(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    assert eng.repair_configuration().status == "OK"

    user_cfg = rpaths.get_user_config_path()
    operator_text = "execution:\n  mode: PAPER\n  symbol: EURUSD\n"
    user_cfg.write_text(operator_text, encoding="utf-8")
    before = user_cfg.read_bytes()

    # Plain repair (no --recreate-config): preserves, and validates.
    res = next(r for r in eng.run(with_news=False) if r.action == "config")
    assert res.status == "OK", res.detail
    assert user_cfg.read_bytes() == before, "an operator's config must never be overwritten"


def test_existing_live_config_survives_repair(tmp_path: Path) -> None:
    """A LIVE operator config is a deliberate choice — repair must never rewrite it."""
    eng = _engine(tmp_path)
    user_cfg = rpaths.get_user_config_path()
    user_cfg.parent.mkdir(parents=True, exist_ok=True)
    user_cfg.write_text("execution:\n  mode: LIVE\n  symbol: XAUUSD\n", encoding="utf-8")
    before = user_cfg.read_bytes()

    res = next(r for r in eng.run(with_news=False) if r.action == "config")
    assert res.status == "OK"
    assert user_cfg.read_bytes() == before


# ---------------------------------------------------------------------------
# 4. An INVALID existing config reports FAILED, never a silent OK, and is
#    left untouched (the operator's data, the operator's decision).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["{ this: is: not: yaml", "execution:\n  mode: NOT_A_MODE\n"])
def test_invalid_existing_config_reports_failed_and_is_preserved(tmp_path: Path, bad: str) -> None:
    eng = _engine(tmp_path)
    user_cfg = rpaths.get_user_config_path()
    user_cfg.parent.mkdir(parents=True, exist_ok=True)
    user_cfg.write_text(bad, encoding="utf-8")
    before = user_cfg.read_bytes()

    res = next(r for r in eng.run(with_news=False) if r.action == "config")
    assert res.status == "FAILED", "an unloadable config must never report OK"
    assert "invalid" in res.detail.lower()
    # Truthful remedy, named exactly.
    assert "recreate-config" in res.suggestion
    # And the file is NOT overwritten — repair never destroys operator data.
    assert user_cfg.read_bytes() == before


def test_invalid_config_then_recreate_restores_loadable_config(tmp_path: Path) -> None:
    """The honest remedy path: --recreate-config (explicit, operator-confirmed)."""
    eng = _engine(tmp_path)
    user_cfg = rpaths.get_user_config_path()
    user_cfg.parent.mkdir(parents=True, exist_ok=True)
    user_cfg.write_text("execution:\n  mode: NOT_A_MODE\n", encoding="utf-8")

    res = eng.repair_configuration(recreate=True)
    assert res.status == "OK", res.detail
    from nexus_scalp.configuration.config import AppConfig

    AppConfig.load_from_yaml(user_cfg)


# ---------------------------------------------------------------------------
# 5. run() never deletes or overwrites ANY user data
# ---------------------------------------------------------------------------
def test_run_never_deletes_user_data(tmp_path: Path) -> None:
    """A marker file the operator owns must survive a repair run byte-for-byte."""
    marker = tmp_path / "artifacts" / "audit.db"
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = b"user-data-must-survive"
    marker.write_bytes(payload)

    eng = _engine(tmp_path)
    eng.run(with_news=False)
    assert marker.read_bytes() == payload


def test_no_failed_results_on_a_clean_first_run(tmp_path: Path) -> None:
    """A fresh workspace repairs without a single FAILED step (honest green)."""
    results = _engine(tmp_path).run(with_news=False)
    failed = [r for r in results if r.status == "FAILED"]
    assert not failed, [(r.action, r.detail) for r in failed]
    # And the config step specifically reports OK (the probe's CONFIGURATION fix).
    assert next(r for r in results if r.action == "config").status == "OK"


# ---------------------------------------------------------------------------
# 6. The repair result is the same truth check_configuration reports
#    (repair and health must agree on ONE artifact).
# ---------------------------------------------------------------------------
def test_repaired_config_passes_health_check_configuration(tmp_path: Path) -> None:
    from nexus_scalp.release import health as rhealth

    assert _engine(tmp_path).repair_configuration().status == "OK"
    entry = rhealth.HealthEngine(config_path=rpaths.get_user_config_path()).check_configuration()
    assert entry.verdict == "PASS", f"{entry.verdict}: {entry.reason}"
