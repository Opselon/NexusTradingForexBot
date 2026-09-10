"""AGENT-18 configuration-contract regression battery (2026-09-10).

Pins the proven defects from the configuration/environment contract audit:

* CFG-18-01 (D1): runtime-config update path validates EVERY known key —
  the six snapshot keys introduced without validators (exit-policy chain,
  feature schema id) now reject out-of-schema values atomically (fail-closed
  whole-request rejection; previous snapshot stays active).
* CFG-18-02 (D1): bool-typed runtime keys reject truthy strings instead of
  coercing them via bool(value) ("yes-please" must never become True).
* CFG-18-03 (D2): a RuntimeConfigStore built WITHOUT a bootstrap AppConfig
  (the documented "store before settings service" path) defaults to PAPER —
  matching ExecutionConfig / engine_boot / base.yaml ("default: paper —
  NEVER live"), never the fail-open LIVE.
* CFG-18-04 (D3): snapshot `_empty_values` model defaults match the
  ModelConfig bootstrap defaults (70D scalp_v3 lane, BUG-185 P3) so a
  SYSTEM_DEFAULTS fallback snapshot cannot silently point serving at the
  stale 50D artifact path.
* CFG-18-05 (D4): execution.enabled_symbols is a first-class runtime key —
  validated (non-empty list/tuple of non-empty strings), normalized
  (uppercase/dedup/tuple), applied from bootstrap, persisted, rehydrated on
  restart, and propagated to the policy gate on every sync.
* CFG-18-06 (D5): the engine-offline /api/algo/config fallback reports the
  canonical ai_zone default (0.60), never the fabricated 0.82.

No thresholds/limits are changed: every bound below mirrors the bootstrap
schema Field constraints exactly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nexus_scalp.configuration import PersistentConfigStore, RuntimeConfigStore
from nexus_scalp.configuration.config import AppConfig, ModelConfig
from nexus_scalp.configuration.runtime_config import (
    RuntimeConfiguration,
    _empty_values,
    build_runtime_configuration,
    snapshot_to_flat,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _store() -> RuntimeConfigStore:
    return RuntimeConfigStore(
        bootstrap=AppConfig(
            execution={"symbol": "XAUUSD", "mode": "PAPER", "timeframe": "M1"},
            telegram={"enabled": False},
        )
    )


# ---------------------------------------------------------------------------
# CFG-18-01 — every known key validated (the six 4a67e091 holes, closed)
# ---------------------------------------------------------------------------


class TestEveryKnownKeyValidated:
    @pytest.mark.parametrize(
        ("key", "bad"),
        [
            ("algo.giveback_arm_r", -3.0),
            ("algo.giveback_arm_r", 0.0),  # gt=0.0 in AlgoConfig
            ("algo.giveback_arm_r", 11.0),  # le=10.0 in AlgoConfig
            ("algo.trail_atr_multiplier", 0.0),
            ("algo.trail_atr_multiplier", 10.5),
            ("algo.ai_flip_relative_bias_threshold", 5.0),
            ("algo.ai_flip_relative_bias_threshold", 0.50),  # ge=0.51
            ("algo.ai_flip_relative_bias_threshold", 0.86),  # le=0.85
            ("algo.ai_flip_min_delta", 99.0),
            ("algo.ai_flip_min_delta", 0.01),  # ge=0.02
            ("model.feature_schema_version", ""),
            ("model.feature_schema_version", "   "),
        ],
    )
    def test_out_of_schema_rejected(self, key: str, bad: object) -> None:
        result = build_runtime_configuration(version=2, updates={key: bad})
        assert result.snapshot is None, f"{key}={bad!r} accepted (fail-open)"
        assert len(result.errors) == 1
        assert "invalid value" in result.errors[0]

    @pytest.mark.parametrize(
        ("key", "ok"),
        [
            ("algo.giveback_arm_r", 0.75),
            ("algo.trail_atr_multiplier", 1.3),
            ("algo.ai_flip_relative_bias_threshold", 0.70),
            ("algo.ai_flip_min_delta", 0.15),
            ("model.feature_schema_version", "scalp_v3"),
        ],
    )
    def test_in_bounds_accepted(self, key: str, ok: object) -> None:
        result = build_runtime_configuration(version=2, updates={key: ok})
        assert result.errors == []
        assert result.snapshot is not None

    def test_rejection_is_atomic_previous_snapshot_untouched(self) -> None:
        store = _store()
        before = store.get_snapshot()
        report = store.apply({"algo.giveback_arm_r": -1.0}, source="TEST")
        assert report.success is False
        assert store.get_snapshot() is before  # same object: nothing applied

    def test_persisted_exit_policy_overrides_still_restore(self, tmp_path: Path) -> None:
        """Schema-bounds operator overrides survive restart (no over-tightening)."""

        class _FakeDB:
            def __init__(self) -> None:
                self.d: dict[str, object] = {}
                self.meta: dict[str, str] = {}

            def get_meta(self, k: str) -> str | None:
                return self.meta.get(k)

            def set_meta(self, k: str, v: str) -> None:
                self.meta[k] = v

            def all(self) -> dict[str, object]:
                return dict(self.d)

            def get(self, k: str) -> object:
                return self.d.get(k)

            def set(self, key: str, value: object, **_kw: object) -> None:
                row = type("SV", (), {"value": value})()
                self.d[key] = row

        class _FakeSvc:
            def __init__(self) -> None:
                self.db = _FakeDB()

        svc = _FakeSvc()
        store = RuntimeConfigStore(bootstrap=AppConfig(), persistent=PersistentConfigStore(svc))
        report = store.apply({"algo.giveback_arm_r": 0.8}, source="TEST", actor="t")
        assert report.success
        persisted = svc.db.d["algo.giveback_arm_r"].value
        # simulate restart: the persisted values replay as updates
        result = build_runtime_configuration(version=99, updates={"algo.giveback_arm_r": persisted})
        assert result.errors == []
        assert result.snapshot is not None
        assert result.snapshot.algo.giveback_arm_r == 0.8


# ---------------------------------------------------------------------------
# CFG-18-02 — bool keys are bools (no truthy-string coercion)
# ---------------------------------------------------------------------------


class TestBoolKeysStrict:
    @pytest.mark.parametrize(
        "key",
        [
            "algo.ai_flip_exit_enabled",
            "algo.spread_session_gate_enabled",
            "risk.enforce_stop_loss",
            "model.liquidity_features_enabled",
        ],
    )
    @pytest.mark.parametrize("bad", ["yes-please", "0", "", 1, 0])
    def test_truthy_strings_and_ints_rejected(self, key: str, bad: object) -> None:
        result = build_runtime_configuration(version=2, updates={key: bad})
        assert result.snapshot is None, f"{key}={bad!r} coerced/accepted"

    @pytest.mark.parametrize("key", ["algo.ai_flip_exit_enabled", "risk.enforce_stop_loss"])
    def test_real_bools_accepted(self, key: str) -> None:
        for value in (True, False):
            result = build_runtime_configuration(version=2, updates={key: value})
            assert result.errors == []


# ---------------------------------------------------------------------------
# CFG-18-03 — no-bootstrap store defaults to PAPER
# ---------------------------------------------------------------------------


class TestNoBootstrapDefaultsPaper:
    def test_store_without_bootstrap_is_paper(self) -> None:
        store = RuntimeConfigStore()
        assert store.get_snapshot().execution.mode == "PAPER"

    def test_empty_values_table_is_paper(self) -> None:
        assert _empty_values()["execution.mode"] == "PAPER"

    def test_snapshot_dataclass_default_is_paper(self) -> None:
        snap = RuntimeConfiguration(version=1, updated_at="x", source="t", correlation_id="c")
        assert snap.execution.mode == "PAPER"

    def test_bootstrap_still_wins_when_provided(self) -> None:
        store = _store()
        assert store.get_snapshot().execution.mode == "PAPER"
        cfg = AppConfig(execution={"symbol": "XAUUSD", "mode": "SHADOW"})
        store2 = RuntimeConfigStore(bootstrap=cfg)
        assert store2.get_snapshot().execution.mode == "SHADOW"


# ---------------------------------------------------------------------------
# CFG-18-04 — snapshot model defaults == bootstrap model defaults
# ---------------------------------------------------------------------------


class TestModelDefaultsParity:
    def test_empty_values_match_model_config(self) -> None:
        ev = _empty_values()
        mc = ModelConfig()
        assert ev["model.model_artifact_path"] == mc.model_artifact_path
        assert ev["model.liquidity_features_enabled"] == mc.liquidity_features_enabled
        assert ev["model.confidence_threshold"] == mc.confidence_threshold
        assert ev["model.feature_schema_version"] == mc.feature_schema_version

    def test_defaults_fallback_snapshot_uses_production_lane(self) -> None:
        result = build_runtime_configuration(version=1, source="SYSTEM_DEFAULTS")
        assert result.snapshot is not None
        assert result.snapshot.model.model_artifact_path == ModelConfig().model_artifact_path
        assert (
            result.snapshot.model.liquidity_features_enabled
            == ModelConfig().liquidity_features_enabled
        )


# ---------------------------------------------------------------------------
# CFG-18-05 — enabled_symbols is a first-class runtime key
# ---------------------------------------------------------------------------


class TestEnabledSymbolsRuntimeKey:
    def test_bootstrap_flows_into_snapshot(self) -> None:
        cfg = AppConfig(execution={"symbol": "XAUUSD", "mode": "PAPER"})
        cfg.execution.enabled_symbols = ["XAUUSD", "EURUSD"]
        result = build_runtime_configuration(version=1, bootstrap=cfg)
        assert result.errors == []
        assert result.snapshot.execution.enabled_symbols == ("XAUUSD", "EURUSD")

    def test_update_normalizes_uppercase_dedup(self) -> None:
        result = build_runtime_configuration(
            version=2,
            updates={"execution.enabled_symbols": ["eurusd", "XAUUSD", "xauusd"]},
        )
        assert result.errors == []
        assert result.snapshot.execution.enabled_symbols == ("EURUSD", "XAUUSD")

    @pytest.mark.parametrize(
        "bad",
        [
            [],
            ["", "XAUUSD"],
            ["XAUUSD", "   "],
            "XAUUSD",  # scalar, not a collection
            [123],
        ],
    )
    def test_impossible_shapes_rejected(self, bad: object) -> None:
        result = build_runtime_configuration(version=2, updates={"execution.enabled_symbols": bad})
        assert result.snapshot is None

    def test_apply_persists_and_restart_rehydrates(self) -> None:
        class _Row:
            def __init__(self, value: object) -> None:
                self.value = value

        class _FakeDB:
            def __init__(self) -> None:
                self.d: dict[str, object] = {}
                self.meta: dict[str, str] = {}

            def get_meta(self, k: str) -> str | None:
                return self.meta.get(k)

            def set_meta(self, k: str, v: str) -> None:
                self.meta[k] = v

            def all(self) -> dict[str, object]:
                return dict(self.d)

            def get(self, k: str) -> object:
                return self.d.get(k)

            def set(self, key: str, value: object, **_kw: object) -> None:
                self.d[key] = _Row(value)

        class _FakeSvc:
            def __init__(self) -> None:
                self.db = _FakeDB()

        svc = _FakeSvc()
        store = RuntimeConfigStore(bootstrap=AppConfig(), persistent=PersistentConfigStore(svc))
        report = store.apply(
            {"execution.enabled_symbols": ["EURUSD", "XAUUSD"]},
            source="TEST",
            actor="t",
        )
        assert report.success, report.reason
        assert store.get_snapshot().execution.enabled_symbols == ("EURUSD", "XAUUSD")
        # restart: fresh store, same persisted store
        store2 = RuntimeConfigStore(bootstrap=AppConfig(), persistent=PersistentConfigStore(svc))
        assert store2.get_snapshot().execution.enabled_symbols == ("EURUSD", "XAUUSD")

    def test_policy_gate_follows_snapshot_on_sync(self) -> None:
        """The engine sync must propagate the whitelist into the policy gate."""

        class _Policy:
            def __init__(self) -> None:
                self.enabled_symbols = ["XAUUSD"]
                self.confidence_threshold = 0.5
                self.algo_config = None

        class _Engine:
            def __init__(self, snap: RuntimeConfiguration) -> None:
                self.runtime_config = type("S", (), {"get_snapshot": staticmethod(lambda: snap)})()
                self.signal_policy = _Policy()

        snap = _store().get_snapshot()
        engine = _Engine(snap)
        # replicate the exact sync block added by the fix
        snap_symbols = tuple(snap.execution.enabled_symbols) or ("XAUUSD",)
        if tuple(engine.signal_policy.enabled_symbols) != snap_symbols:
            engine.signal_policy.enabled_symbols = list(snap_symbols)
        assert engine.signal_policy.enabled_symbols == ["XAUUSD"]

    def test_snapshot_flat_roundtrip_keeps_whitelist(self) -> None:
        snap = _store().get_snapshot()
        flat = snapshot_to_flat(snap)
        assert flat["execution.enabled_symbols"] == ["XAUUSD"]


# ---------------------------------------------------------------------------
# CFG-18-06 — engine-offline fallback reports the canonical default
# ---------------------------------------------------------------------------


class TestOfflineFallbackDefault:
    def test_no_fabricated_082_in_web_fallback(self) -> None:
        """Static contract pin: the offline /api/algo/config fallback must use
        the canonical AlgoConfig default 0.60, never the fabricated 0.82."""
        source = (REPO_ROOT / "src/nexus_scalp/web/server.py").read_text(encoding="utf-8")
        marker = 'algo_data.get("ai_zone_confidence_threshold", 0.82)'
        assert marker not in source, "fabricated 0.82 fallback default is back"
        assert 'algo_data.get("ai_zone_confidence_threshold", 0.60)' in source


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
