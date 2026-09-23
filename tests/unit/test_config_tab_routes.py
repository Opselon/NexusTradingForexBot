"""TASK-CFGUI-001 — /alt/config tab backend contract tests.

Covers the four backend fixes behind the Settings tab rewrite:

1. GET /api/config never serves plaintext credentials (BUG-072 extended to
   mt5.password + notification.telegram_api_key) — and the paired POST guard
   so a mask echoing back from the legacy config form never overwrites the
   stored credential in live.yaml.
2. POST /api/engine/mode enforces the SAME transition matrix that
   /api/v1/runtime/mode documents as server truth (it previously validated
   only enum membership — any mode could be applied from any mode), and
   persists exactly once through the versioned runtime store (the former
   unconditional db.set double-wrote every switch).
3. POST /api/settings/validate dry-runs a proposed value through
   build_runtime_configuration — the apply path's own validator — instead of
   answering valid:true for every key without seeing a value. Key-only calls
   stay mutability-only (backward compatible).
4. POST /api/v1/runtime/mode/preview answers 200 with validation+impact for
   BOTH valid and invalid proposals (the shape the frontend consumes).

Run: .venv/Scripts/python -m pytest tests/unit/test_config_tab_routes.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from nexus_scalp.domain.enums import ExecutionMode
from nexus_scalp.web.diagnostics_state_routes import (
    current_execution_mode,
    mask_config_secrets,
    mask_secret_tail,
    restore_masked_mt5_password,
)

# ---------------------------------------------------------------------------
# 1. secret masking helpers
# ---------------------------------------------------------------------------


class TestMaskSecretTail:
    def test_keeps_last_four(self) -> None:
        assert mask_secret_tail("supersecret99") == "*" * 9 + "et99"

    def test_short_value_fully_masked(self) -> None:
        assert mask_secret_tail("abc") == "***"


class TestMaskConfigSecrets:
    def test_masks_all_secret_shaped_fields(self) -> None:
        payload = {
            "mt5": {"login": 123456, "password": "BrokerPass!77", "server": "Live"},
            "telegram": {"bot_token": "999999:zzzzzzzzzzzzzzzzzzzz", "enabled": True},
            "notification": {"telegram_api_key": "123456:abcdefABCDEF"},
            "risk": {"risk_per_trade_pct": 0.5},
        }
        out = mask_config_secrets(payload)
        assert out["mt5"]["password"] != "BrokerPass!77"
        assert "*" in out["mt5"]["password"]
        assert out["telegram"]["bot_token"] != "999999:zzzzzzzzzzzzzzzzzzzz"
        assert "*" in out["telegram"]["bot_token"]
        assert "*" in out["notification"]["telegram_api_key"]
        # non-secrets untouched (mt5.login stays readable, risk untouched)
        assert out["mt5"]["login"] == 123456
        assert out["mt5"]["server"] == "Live"
        assert out["risk"]["risk_per_trade_pct"] == 0.5

    def test_never_double_masks(self) -> None:
        payload = {"telegram": {"bot_token": "*****zzzz"}}
        out = mask_config_secrets(payload)
        assert out["telegram"]["bot_token"] == "*****zzzz"

    def test_empty_and_missing_stay_empty(self) -> None:
        out = mask_config_secrets({"telegram": {"bot_token": ""}, "mt5": {}})
        assert out["telegram"]["bot_token"] == ""
        assert "password" not in out["mt5"]


class TestRestoreMaskedMt5Password:
    def _write_yaml(self, tmp_path, password: str):
        cfg_dir = tmp_path / "configs"
        cfg_dir.mkdir(exist_ok=True)
        path = cfg_dir / "live.yaml"
        path.write_text(yaml.safe_dump({"mt5": {"password": password}}), encoding="utf-8")
        return path

    def test_masked_inbound_restores_stored_credential(self, tmp_path) -> None:
        path = self._write_yaml(tmp_path, "realpass1234")
        payload = {"mt5": {"password": "*********t34"}}
        restore_masked_mt5_password(payload, path)
        assert payload["mt5"]["password"] == "realpass1234"

    def test_empty_inbound_restores_stored_credential(self, tmp_path) -> None:
        path = self._write_yaml(tmp_path, "realpass1234")
        payload = {"mt5": {"password": ""}}
        restore_masked_mt5_password(payload, path)
        assert payload["mt5"]["password"] == "realpass1234"

    def test_real_inbound_password_is_kept(self, tmp_path) -> None:
        path = self._write_yaml(tmp_path, "old")
        payload = {"mt5": {"password": "brandnew"}}
        restore_masked_mt5_password(payload, path)
        assert payload["mt5"]["password"] == "brandnew"

    def test_missing_yaml_means_no_stored_credential(self, tmp_path) -> None:
        path = tmp_path / "configs" / "live.yaml"  # never written
        payload = {"mt5": {"password": "****"}}
        restore_masked_mt5_password(payload, path)
        assert payload["mt5"]["password"] == ""

    def test_no_mt5_block_is_a_noop(self) -> None:
        payload: dict = {"execution": {"symbol": "XAUUSD"}}
        assert restore_masked_mt5_password(payload, None) is payload


# ---------------------------------------------------------------------------
# 2. route harness (engine-less app)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("NSE_WEB_AUTH_DISABLE", "1")
    from nexus_scalp.web.server import create_app

    app = create_app(engine_ref=None)
    return TestClient(app)


class TestGetConfigMasksCredentials:
    def test_engine_offline_yaml_fallback_never_serves_plaintext(
        self, client, tmp_path, monkeypatch
    ) -> None:
        cfg_dir = tmp_path / "configs"
        cfg_dir.mkdir()
        (cfg_dir / "live.yaml").write_text(
            yaml.safe_dump(
                {
                    "execution": {"symbol": "XAUUSD", "timeframe": "M1", "mode": "PAPER"},
                    "mt5": {"login": 555, "password": "supersecret99", "server": "Live"},
                    "telegram": {"bot_token": "999999:zzzzzzzzzzzzzzzzzzzz", "enabled": True},
                    "notification": {"telegram_api_key": "123456:abcdefABCDEF"},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)  # route resolves configs/live.yaml on CWD

        resp = client.get("/api/config")
        assert resp.status_code == 200
        body = resp.json()
        assert "supersecret99" not in resp.text
        assert "999999:zzzzzzzzzzzzzzzzzzzz" not in resp.text
        assert "123456:abcdefABCDEF" not in resp.text
        assert "*" in body["mt5"]["password"]
        assert "*" in body["telegram"]["bot_token"]
        assert body["mt5"]["password"].endswith("et99")  # last-4 kept for recognition
        # non-secret config still round-trips for the form
        assert body["execution"]["symbol"] == "XAUUSD"


class TestSettingsValidate:
    def test_key_only_stays_mutability_only(self, client) -> None:
        """Back-compat: no `value` in the payload -> no value verdict, valid=True."""
        resp = client.post("/api/settings/validate", json={"key": "risk.risk_per_trade_pct"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["checked"] is False
        assert body["valid"] is True
        assert body["mutability"]

    def test_valid_value_passes_apply_path_validator(self, client) -> None:
        resp = client.post(
            "/api/settings/validate",
            json={"key": "risk.risk_per_trade_pct", "value": 0.5},
        )
        body = resp.json()
        assert body["checked"] is True
        assert body["valid"] is True, body["errors"]
        assert body["errors"] == []

    def test_out_of_range_value_is_refused(self, client) -> None:
        """0 < risk_per_trade_pct <= 100 is the runtime-config validator."""
        resp = client.post(
            "/api/settings/validate",
            json={"key": "risk.risk_per_trade_pct", "value": 250},
        )
        body = resp.json()
        assert body["valid"] is False
        assert body["errors"], "validator must explain the refusal"

    def test_wrong_type_value_is_refused(self, client) -> None:
        resp = client.post(
            "/api/settings/validate",
            json={"key": "risk.risk_per_trade_pct", "value": "not-a-number"},
        )
        body = resp.json()
        assert body["valid"] is False
        assert body["errors"]

    def test_unknown_key_is_refused(self, client) -> None:
        resp = client.post(
            "/api/settings/validate",
            json={"key": "nope.nope", "value": 1},
        )
        body = resp.json()
        assert body["valid"] is False
        assert any("unknown" in e for e in body["errors"])


# ---------------------------------------------------------------------------
# 3. engine mode route: transition matrix + single persist
# ---------------------------------------------------------------------------


def _fake_engine(mode: ExecutionMode) -> SimpleNamespace:
    """Engine stub with a versioned-store double (no settings-DB writes)."""
    store = SimpleNamespace(
        apply=lambda updates, source=None, actor=None: SimpleNamespace(
            persisted=True, runtime_applied=True
        ),
        calls=[],
    )

    engine = SimpleNamespace(
        config=SimpleNamespace(execution=SimpleNamespace(mode=mode)),
        _running=True,
        runtime_config=store,
        applied=[],
    )

    def set_execution_mode(target, source=None):
        engine.config.execution.mode = target
        return {"success": True}

    engine.set_execution_mode = set_execution_mode
    return engine


@pytest.fixture()
def mode_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("NSE_WEB_AUTH_DISABLE", "1")
    from nexus_scalp.web.server import create_app

    app = create_app(engine_ref=None)
    return TestClient(app)


class TestEngineModeRoute:
    def test_engineless_is_400(self, mode_client) -> None:
        resp = mode_client.post("/api/engine/mode", json={"mode": "PAPER"})
        assert resp.status_code == 400

    def test_illegal_transition_refused(self, mode_client) -> None:
        """PAPER -> BACKTEST is not in the documented transition matrix."""
        engine = _fake_engine(ExecutionMode.PAPER)
        mode_client.app.state.engine = engine  # type: ignore[attr-defined]
        resp = mode_client.post("/api/engine/mode", json={"mode": "BACKTEST"})
        assert resp.status_code == 422
        assert "not allowed" in str(resp.json()["detail"])
        # nothing was swapped
        assert engine.config.execution.mode is ExecutionMode.PAPER

    def test_legal_transition_applies_once(self, mode_client) -> None:
        engine = _fake_engine(ExecutionMode.PAPER)
        mode_client.app.state.engine = engine  # type: ignore[attr-defined]
        resp = mode_client.post("/api/engine/mode", json={"mode": "LIVE"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["mode"] == "LIVE"
        assert body["persisted"] is True
        assert engine.config.execution.mode is ExecutionMode.LIVE

    def test_unknown_mode_still_422(self, mode_client) -> None:
        engine = _fake_engine(ExecutionMode.PAPER)
        mode_client.app.state.engine = engine  # type: ignore[attr-defined]
        resp = mode_client.post("/api/engine/mode", json={"mode": "TEAPOT"})
        assert resp.status_code == 422
        assert "Invalid execution mode" in str(resp.json()["detail"])


class TestCurrentExecutionMode:
    def test_reads_enum_value(self) -> None:
        engine = _fake_engine(ExecutionMode.SHADOW)
        assert current_execution_mode(engine) == "SHADOW"

    def test_unreadable_engine_is_none(self) -> None:
        assert current_execution_mode(SimpleNamespace()) is None


# ---------------------------------------------------------------------------
# 4. v1 mode/preview: 200 for BOTH valid and invalid proposals
# ---------------------------------------------------------------------------


class TestModePreviewContract:
    @pytest.fixture()
    def v1_client(self) -> TestClient:
        from nexus_scalp.web.api_v1_wiring import create_v1_app

        return TestClient(create_v1_app())

    def test_preview_valid_proposal(self, v1_client) -> None:
        resp = v1_client.post("/api/v1/runtime/mode/preview", json={"mode": "PAPER"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["validation"]["valid"] is True
        assert data["validation"]["proposed_mode"] == "PAPER"
        assert "impact" in data

    def test_preview_illegal_transition_stays_200(self, v1_client) -> None:
        """Transition verdicts are DATA (the frontend consumes them inline):
        an illegal transition answers 200 with validation.valid=false."""
        v1_client.app.state.engine = _fake_engine(ExecutionMode.PAPER)  # type: ignore[attr-defined]
        resp = v1_client.post("/api/v1/runtime/mode/preview", json={"mode": "BACKTEST"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["validation"]["valid"] is False
        assert any("not allowed" in e for e in data["validation"]["errors"])
        assert data["applies"] is False
        assert "impact" in data

    def test_preview_unknown_mode_is_422_error_envelope(self, v1_client) -> None:
        """A syntactically unknown mode never reaches the matrix — the v1
        error envelope (code VALIDATION_ERROR) answers instead, which the
        frontend's ApiError normalization parses."""
        resp = v1_client.post("/api/v1/runtime/mode/preview", json={"mode": "TEAPOT"})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
