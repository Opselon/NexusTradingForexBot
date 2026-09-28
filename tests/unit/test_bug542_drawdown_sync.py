"""BUG-542 regression: Max Drawdown UI save must reach the effective runtime
AND every surface that displays it.

The defect: ``POST /api/config`` persisted the drawdown into the versioned
``RuntimeConfigStore`` (which the risk engine consumes via
``_update_survival_state`` / ``_sync_runtime_config``) but ``/api/live/state``
still read ``engine.config.risk`` — the BOOTSTRAP AppConfig (live.yaml). So an
operator saved 99%, the API answered ``saved``, the risk engine moved to 99%,
and the header/control-center kept rendering the stale bootstrap 5%. Two
surfaces of one fact disagreed.

This test builds the real store + apply pipeline against a live-state contract
client and asserts the single authoritative value is what every surface
reports, for several values (5 / 25 / 99 / restart-style rehydration).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.configuration.config import AppConfig, RiskConfig
from nexus_scalp.configuration.runtime_config import (
    PersistentConfigStore,
    RuntimeConfigStore,
)
from nexus_scalp.settings.service import SettingsDatabase, SettingsService
from nexus_scalp.web.server import create_app

from .test_live_state_contract import _FakeEngine

# The bootstrap AppConfig drawdown — deliberately distinct from every value
# the test saves, so a stale-bootstrap read is unambiguous.
BOOTSTRAP_DRAWDOWN = 20.0


def _bootstrap_config() -> AppConfig:
    """A real AppConfig whose risk section carries the bootstrap drawdown."""
    cfg = AppConfig()
    cfg.risk = RiskConfig(
        max_account_drawdown_pct=BOOTSTRAP_DRAWDOWN,
        risk_per_trade_pct=0.5,
        max_concurrent_positions=2,
        max_spread_points=40,
    )
    return cfg


class _DrawdownEngine(_FakeEngine):
    """The contract suite's live engine, with the REAL config wiring.

    Inherits every attribute /api/live/state and /api/config touch (tick,
    aggregator, adapter, audit, health) so the test exercises the real route
    code rather than a stub, and swaps in the authoritative store pair.
    ``config.risk`` is the bootstrap (never mutated by a save); the engine's
    own ``apply_runtime_update`` mirrors LiveEngine's exactly.
    """

    def __init__(self, db_path):
        super().__init__()
        # Swap the SimpleNamespace bootstrap for a REAL AppConfig so the
        # runtime store can be built from it (RuntimeConfigStore needs the
        # full schema). The bootstrap drawdown stays BOOTSTRAP_DRAWDOWN.
        self.config = _bootstrap_config()
        # The store is built from the bootstrap, as at boot; a REAL persistent
        # settings DB is attached and rehydrated so the save path is exercised
        # end to end (persist -> version -> snapshot swap) exactly as live.
        # SettingsService supplies set_telegram/get_telegram_credentials, which
        # POST /api/config also calls.
        self.settings_service = SettingsService(db=SettingsDatabase(db_path=Path(db_path)))
        self.runtime_config = RuntimeConfigStore(bootstrap=self.config)
        self.runtime_config.rehydrate(PersistentConfigStore(self.settings_service))

    def apply_runtime_update(self, updates, *, source="WEB_UI", actor="web"):
        """The real LiveEngine seam POST /api/config calls through."""
        report = self.runtime_config.apply(updates, source=source, actor=actor)
        return report


@pytest.fixture
def dd_client(tmp_path, monkeypatch):
    tok = os.environ.get("NSE_WEB_AUTH_TOKEN")
    os.environ["NSE_WEB_AUTH_TOKEN"] = "web-contract-test-token"
    # POST /api/config writes configs/live.yaml at the PROCESS cwd. Without
    # isolating the cwd this fixture clobbers the real checkout's live.yaml and,
    # under xdist, races sibling workers reading it -- the write can land on a
    # file another test already moved, surfacing as KeyError 'runtime_applied'
    # via the route's OPERATION_FAILED branch. Chdir to a tmp tree first, and
    # create the configs/ dir the route's atomic write needs (it writes
    # configs/live.yaml.tmp, so a bare tmp cwd raises FileNotFoundError).
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir(exist_ok=True)
    db = tmp_path / "app_settings.db"
    client = TestClient(create_app(engine_ref=_DrawdownEngine(db)))
    client.headers.update({"Authorization": "Bearer web-contract-test-token"})
    yield client
    if tok is None:
        os.environ.pop("NSE_WEB_AUTH_TOKEN", None)
    else:
        os.environ["NSE_WEB_AUTH_TOKEN"] = tok


def _save_drawdown(client: TestClient, value: float) -> dict:
    """POST /api/config with a full risk block, mirroring the real UI payload."""
    base = client.get("/api/config").json()
    base["risk"]["max_account_drawdown_pct"] = value
    resp = client.post("/api/config", json=base)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize("value", [5.0, 25.0, 99.0])
def test_drawdown_save_reaches_every_surface(dd_client, value):
    """save(value) -> persisted == runtime == live/state == runtime-config."""
    report = _save_drawdown(dd_client, value)
    assert report["success"] is True, report
    assert report["runtime_applied"] is True, report
    assert report["persisted"] is True, report

    # 1. The store's effective snapshot (what the risk engine reads).
    snap = dd_client.get("/api/runtime-config").json()
    assert snap["effective"]["risk"]["max_account_drawdown_pct"] == value

    # 2. /api/config — the editor's own read-back.
    cfg = dd_client.get("/api/config").json()
    assert cfg["risk"]["max_account_drawdown_pct"] == value

    # 3. /api/live/state — the header / control-center feed. THIS is the
    #    regression: it used to answer the bootstrap BOOTSTRAP_DRAWDOWN.
    live = dd_client.get("/api/live/state").json()
    assert live["risk"]["limits"]["max_drawdown_pct"] == value, (
        f"/api/live/state still shows the stale bootstrap after saving {value}"
    )

    # 4. The other stale-bootstrap risk knobs on the same payload.
    assert live["risk"]["risk_pct"] == cfg["risk"]["risk_per_trade_pct"]
    assert (
        live["risk"]["limits"]["max_concurrent_positions"]
        == cfg["risk"]["max_concurrent_positions"]
    )
    assert live["risk"]["limits"]["max_spread_points"] == cfg["risk"]["max_spread_points"]

    # 5. The accounting plan derives risk-per-trade from the SAME authority.
    plan = dd_client.get(
        "/api/live/accounting",
        params={"equity": 10000.0, "entry": 2334.21, "stop_loss": 2328.0},
    ).json()
    assert plan["available"] is True
    want = round(10000.0 * (cfg["risk"]["risk_per_trade_pct"] / 100.0), 2)
    assert plan["plan"]["risk_usd"] == want


def test_drawdown_header_never_reports_stale_bootstrap(dd_client):
    """The header must not keep the bootstrap value once a save lands."""
    # Bootstrap is what the engine was constructed with.
    live = dd_client.get("/api/live/state").json()
    assert live["risk"]["limits"]["max_drawdown_pct"] == BOOTSTRAP_DRAWDOWN

    _save_drawdown(dd_client, 99.0)

    live = dd_client.get("/api/live/state").json()
    assert live["risk"]["limits"]["max_drawdown_pct"] == 99.0
    assert live["risk"]["limits"]["max_drawdown_pct"] != BOOTSTRAP_DRAWDOWN


def test_drawdown_survives_rehydrate(dd_client):
    """A rehydrated store (restart path) must restore the saved value."""
    _save_drawdown(dd_client, 99.0)

    engine = dd_client.app.state.engine  # type: ignore[attr-defined]
    persisted = engine.runtime_config.get_snapshot().to_dict()["risk"]

    # Simulate a restart: a FRESH store built from the bootstrap, then the
    # persisted risk section layered over it via the SAME builder
    # RuntimeConfigStore.rehydrate uses (build_runtime_configuration over
    # the persisted settings DB rows).
    from nexus_scalp.configuration.runtime_config import build_runtime_configuration

    fresh = RuntimeConfigStore(bootstrap=engine.config)
    result = build_runtime_configuration(
        version=fresh.get_version() + 1,
        base=fresh.get_snapshot(),
        updates={"risk.max_account_drawdown_pct": persisted["max_account_drawdown_pct"]},
        source="PERSISTED_RESTORE",
    )
    assert result.snapshot is not None
    assert result.snapshot.risk.max_account_drawdown_pct == 99.0
    # ... and it must NOT be the bootstrap the fresh store started from.
    assert fresh.get_snapshot().risk.max_account_drawdown_pct == BOOTSTRAP_DRAWDOWN
    assert result.snapshot.risk.max_account_drawdown_pct != BOOTSTRAP_DRAWDOWN


def test_save_carries_margin_usage_through_the_runtime_store(dd_client):
    """The /api/config risk allowlist must include max_margin_usage_pct.

    It is a runtime-config field (validated, persisted, hot-applied) that the
    save route used to omit, so a UI edit wrote live.yaml only and the runtime
    kept the bootstrap.
    """
    base = dd_client.get("/api/config").json()
    base["risk"]["max_account_drawdown_pct"] = 99.0
    base["risk"]["max_margin_usage_pct"] = 42.0
    resp = dd_client.post("/api/config", json=base).json()
    assert resp["runtime_applied"] is True, resp

    snap = dd_client.get("/api/runtime-config").json()
    assert snap["effective"]["risk"]["max_margin_usage_pct"] == 42.0
    assert snap["effective"]["risk"]["max_account_drawdown_pct"] == 99.0


def test_save_succeeds_when_configs_dir_is_not_the_process_cwd(dd_client, monkeypatch, tmp_path):
    """The atomic live.yaml write must survive a CWD that is not the repo root.

    ``save_config`` resolves ``configs/live.yaml`` as a RELATIVE path, so the
    temp file lands wherever the process happens to be. On CI the parent
    directory did not exist from that vantage and the atomic swap raised
    ``FileNotFoundError`` — the route then returned OPERATION_FAILED and the
    runtime apply never ran, so the operator's save silently did nothing.
    """
    payload = dd_client.get("/api/config").json()
    payload["risk"]["max_account_drawdown_pct"] = 77.0

    # chdir into an EMPTY directory: no configs/ here, exactly the CI case.
    # monkeypatch.chdir restores the original CWD on teardown so the fixture's
    # other tests keep resolving the repo-relative paths they rely on.
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    monkeypatch.chdir(empty)

    resp = dd_client.post("/api/config", json=payload).json()
    assert resp["success"] is True, resp
    assert resp["runtime_applied"] is True, resp

    snap = dd_client.get("/api/runtime-config").json()
    assert snap["effective"]["risk"]["max_account_drawdown_pct"] == 77.0
