"""BUG-543: a UI save must never make configs/live.yaml unloadable.

Root cause (measured, from a live engine that would not boot):

    Configuration File  INVALID  Parse error: 2 validation errors for AppConfig
        configuration_version  Extra inputs are not permitted
        runtime_applied       Extra inputs are not permitted
    Pre-flight diagnostics failed!  Halting launch

``POST /api/config`` and ``PUT /api/algo/config`` projected the YAML body back
to disk verbatim. Top-level ``configuration_version`` / ``runtime_applied`` are
runtime-*snapshot* bookkeeping, not bootstrap ``AppConfig`` fields —
``AppConfig.load_from_yaml`` is extra-forbidden — so once any payload carried
them, the bootstrap file was poisoned permanently and the engine halted at the
next boot. A save the UI reported as successful broke booting.

These tests drive the REAL routes against a tmp cwd, so the file the projection
writes is the file ``AppConfig.load_from_yaml`` must read.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.web.server import create_app

from .test_bug542_drawdown_sync import _DrawdownEngine
from .test_live_state_contract import _FakeEngine

_POISON_KEYS = ("configuration_version", "runtime_applied")

# Snapshot bookkeeping — belongs to the runtime snapshot, never the bootstrap.
_POISONED_TOP = {"configuration_version": 3, "runtime_applied": True}

_SECTIONS = {
    "execution": {"symbol": "XAUUSD", "mode": "PAPER", "timeframe": "M1"},
    "risk": {
        "max_account_drawdown_pct": 5.0,
        "risk_per_trade_pct": 1.0,
        "max_concurrent_positions": 1,
        "max_spread_points": 40,
    },
    "algo": {"min_risk_reward_ratio": 1.8, "atr_sl_buffer_multiplier": 1.5},
    "telegram": {"enabled": False, "bot_token": "", "admin_id": ""},
    "mt5": {"login": 0, "password": "", "server": "", "terminal_path": ""},
}


@pytest.fixture
def yaml_client(tmp_path, monkeypatch):
    """A client whose cwd (and so configs/live.yaml) is a tmp directory."""
    monkeypatch.chdir(tmp_path)
    tok = os.environ.get("NSE_WEB_AUTH_TOKEN")
    os.environ["NSE_WEB_AUTH_TOKEN"] = "web-yaml-test-token"
    db = tmp_path / "app_settings.db"
    client = TestClient(create_app(engine_ref=_DrawdownEngine(db)))
    client.headers.update({"Authorization": "Bearer web-yaml-test-token"})
    yield client
    if tok is None:
        os.environ.pop("NSE_WEB_AUTH_TOKEN", None)
    else:
        os.environ["NSE_WEB_AUTH_TOKEN"] = tok


@pytest.fixture
def live_yaml(tmp_path, yaml_client) -> Path:
    """A schema-valid configs/live.yaml, as a clean checkout ships it."""
    p = tmp_path / "configs" / "live.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(_SECTIONS), encoding="utf-8")
    return p


def _poison(path: Path) -> None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.update(_POISONED_TOP)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


# ---------------------------------------------------------------- boot halt --


def test_poisoned_yaml_rejects_at_boot(tmp_path):
    """The exact operator-facing failure: poisoned file -> AppConfig rejects."""
    p = tmp_path / "live.yaml"
    p.write_text(
        yaml.safe_dump({"algo": {"min_risk_reward_ratio": 1.8}, **_POISONED_TOP}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as excinfo:
        AppConfig.load_from_yaml(p)
    msg = str(excinfo.value)
    assert "configuration_version" in msg, msg
    assert "runtime_applied" in msg, msg
    assert "Extra inputs are not permitted" in msg, msg


# ------------------------------------------------------- POST /api/config --


def test_post_config_does_not_poison_yaml(yaml_client, live_yaml):
    """A save carrying snapshot bookkeeping must not write it to live.yaml."""
    base = yaml_client.get("/api/config").json()
    base["risk"]["max_account_drawdown_pct"] = 25.0
    base.update(_POISONED_TOP)  # the bug's vector

    resp = yaml_client.post("/api/config", json=base)
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True, resp.json()

    reloaded = yaml.safe_load(live_yaml.read_text(encoding="utf-8"))
    for key in _POISON_KEYS:
        assert key not in reloaded, (key, sorted(reloaded))

    # The file the engine boots from stayed loadable — boot no longer halts.
    cfg = AppConfig.load_from_yaml(live_yaml)
    assert cfg.risk.max_account_drawdown_pct == pytest.approx(25.0)


def test_post_config_heals_already_poisoned_yaml(yaml_client, live_yaml):
    """A save must HEAL a live.yaml that an older projection poisoned."""
    _poison(live_yaml)
    with pytest.raises(ValueError):  # poisoned, exactly as reported at boot
        AppConfig.load_from_yaml(live_yaml)

    base = yaml_client.get("/api/config").json()
    resp = yaml_client.post("/api/config", json=base)
    assert resp.status_code == 200, resp.text

    reloaded = yaml.safe_load(live_yaml.read_text(encoding="utf-8"))
    for key in _POISON_KEYS:
        assert key not in reloaded, (key, sorted(reloaded))
    AppConfig.load_from_yaml(live_yaml)  # bootable again


def test_post_config_keeps_legitimate_sections(yaml_client, live_yaml):
    """The schema filter must not drop sections AppConfig does model."""
    base = yaml_client.get("/api/config").json()
    base["risk"]["max_account_drawdown_pct"] = 99.0
    resp = yaml_client.post("/api/config", json=base)
    assert resp.status_code == 200, resp.text

    reloaded = yaml.safe_load(live_yaml.read_text(encoding="utf-8"))
    # No key outside the AppConfig schema survived, and every section the clean
    # file carried is still present.
    valid = set(AppConfig.model_fields.keys())
    assert set(reloaded) <= valid, set(reloaded) - valid
    assert set(_SECTIONS) <= set(reloaded), set(_SECTIONS) - set(reloaded)
    assert reloaded["risk"]["max_account_drawdown_pct"] == pytest.approx(99.0)
    AppConfig.load_from_yaml(live_yaml)


# --------------------------------------------------- PUT /api/algo/config --


_ALGO_PAYLOAD = {
    "atr_sl_buffer_multiplier": 1.8,
    "min_risk_reward_ratio": 2.0,
    "ai_zone_confidence_threshold": 0.6,
    "fvg_mitigation_sensitivity": 0.5,
    "order_block_lookback_bars": 30,
}


def test_algo_save_does_not_poison_yaml(yaml_client, live_yaml):
    """PUT /api/algo/config round-trips the file; it must not preserve poison."""
    _poison(live_yaml)

    resp = yaml_client.put("/api/algo/config", json=_ALGO_PAYLOAD)
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True, resp.json()

    reloaded = yaml.safe_load(live_yaml.read_text(encoding="utf-8"))
    for key in _POISON_KEYS:
        assert key not in reloaded, (key, sorted(reloaded))
    cfg = AppConfig.load_from_yaml(live_yaml)
    assert cfg.algo.atr_sl_buffer_multiplier == pytest.approx(1.8)


def test_algo_save_is_idempotent(yaml_client, live_yaml):
    """Repeated saves must not accumulate or re-introduce non-schema keys."""
    payload = dict(_ALGO_PAYLOAD)
    for i in range(3):
        payload["min_risk_reward_ratio"] = 1.5 + i
        resp = yaml_client.put("/api/algo/config", json=payload)
        assert resp.status_code == 200, resp.text

    reloaded = yaml.safe_load(live_yaml.read_text(encoding="utf-8"))
    assert not (set(reloaded) & set(_POISON_KEYS)), sorted(reloaded)
    assert reloaded["algo"]["min_risk_reward_ratio"] == pytest.approx(3.5)
    AppConfig.load_from_yaml(live_yaml)


# ------------------------------------------------- restart / rehydration ---


def test_saved_value_survives_restart(yaml_client, live_yaml):
    """Drawdown saved via the route rehydrates on a fresh store (restart)."""
    from nexus_scalp.configuration.runtime_config import (
        PersistentConfigStore,
        RuntimeConfigStore,
    )
    from nexus_scalp.settings.service import SettingsDatabase, SettingsService

    base = yaml_client.get("/api/config").json()
    base["risk"]["max_account_drawdown_pct"] = 99.0
    resp = yaml_client.post("/api/config", json=base)
    assert resp.status_code == 200, resp.text

    # A NEW store over the same persisted DB — the restart path.
    svc = SettingsService(db=SettingsDatabase(db_path=live_yaml.parent / "app_settings.db"))
    store = RuntimeConfigStore(bootstrap=AppConfig.load_from_yaml(live_yaml))
    store.rehydrate(PersistentConfigStore(svc))

    assert store.get_snapshot().risk.max_account_drawdown_pct == pytest.approx(99.0)


# ------------------------------------------------------- safety: no bypass --


def test_projection_does_not_touch_risk_enforcement(yaml_client, live_yaml):
    """The fix only heals the file; it must not weaken any risk check."""
    engine: _FakeEngine = yaml_client.app.state.engine
    before = dict(engine.config.risk.model_dump())

    base = yaml_client.get("/api/config").json()
    base.update(_POISONED_TOP)
    resp = yaml_client.post("/api/config", json=base)
    assert resp.status_code == 200, resp.text

    # The bootstrap AppConfig risk section is untouched — the projection never
    # loosened or bypassed the engine's configured safety envelope.
    assert engine.config.risk.model_dump() == before
    snap = yaml_client.get("/api/runtime-config").json()
    assert "max_account_drawdown_pct" in snap["effective"]["risk"]
