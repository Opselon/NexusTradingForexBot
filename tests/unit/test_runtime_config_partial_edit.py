"""TASK-CFGUI-002 — the /config single-field edit contract (regression).

The /config page applies a PARTIAL update: the UI sends only the dotted key(s)
the operator edited (``buildPayload``), never the whole document. The
authoritative path must therefore be:

    authoritative stored config
            +  partial user edit
            =  canonical merge
            =  canonical validation
            =  canonical persistence
            =  canonical read-back

The bug this pins lived one step before the wire: the UI's own gate validated
the sparse payload against every REQUIRED field spec, so a one-field edit was
refused with a whole-form ``{label} is required`` list and nothing reached the
backend. The server side of the contract was always correct — these tests pin
it so a future change to ``build_runtime_configuration`` / ``apply`` cannot
silently re-break the merge semantics the UI now relies on.

Covers the task's Tests 1-6 against the real ``RuntimeConfigStore`` +
``PersistentConfigStore`` (SQLite-backed settings DB; the same store class the
PostgreSQL provider resolves through the settings-service seam).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus_scalp.configuration import PersistentConfigStore, RuntimeConfigStore
from nexus_scalp.configuration.runtime_config import build_runtime_configuration
from nexus_scalp.settings import SettingsDatabase, SettingsService

# The runtime fields the /config form owns (frontend model.ts runtimeConfigSpecs
# — kept in sync deliberately; the backend table is the authority).
FORM_KEYS = [
    "execution.symbol",
    "execution.timeframe",
    "execution.magic_number",
    "execution.max_slippage_points",
    "risk.max_account_drawdown_pct",
    "risk.risk_per_trade_pct",
    "risk.max_concurrent_positions",
    "risk.max_spread_points",
    "risk.max_allowed_lots",
    "risk.enforce_stop_loss",
    "model.confidence_threshold",
    "model.model_artifact_path",
]


def _svc(tmp_path: Path) -> SettingsService:
    return SettingsService(db=SettingsDatabase(tmp_path / "app_settings.db"))


def _store(tmp_path: Path) -> tuple[RuntimeConfigStore, SettingsService]:
    svc = _svc(tmp_path)
    store = RuntimeConfigStore(persistent=PersistentConfigStore(svc))
    return store, svc


def _stored(svc: SettingsService) -> dict[str, object]:
    """The authoritative persisted values keyed `section.field`."""
    return {k: sv.value for k, sv in svc.db.all().items()}


class TestSingleFieldEdit:
    """Test 1 — change ONLY risk.max_account_drawdown_pct."""

    def test_partial_update_merges_into_the_complete_document(self, tmp_path: Path) -> None:
        store, svc = _store(tmp_path)
        before = store.get_snapshot().to_flat_dict()
        assert before["risk.max_account_drawdown_pct"] == 2.0  # bootstrap default

        report = store.apply({"risk.max_account_drawdown_pct": 6.25}, source="WEB_UI")

        assert report.success is True
        assert report.persisted is True
        assert report.runtime_applied is True
        assert report.requested_fields == ("risk.max_account_drawdown_pct",)

        snap = store.get_snapshot()
        assert snap.risk.max_account_drawdown_pct == 6.25
        # every OTHER form field is untouched
        for key in FORM_KEYS:
            if key == "risk.max_account_drawdown_pct":
                continue
            assert snap.to_flat_dict()[key] == before[key], f"{key} changed"

        # the authoritative store holds the new value only
        stored = _stored(svc)
        assert stored["risk.max_account_drawdown_pct"] == 6.25
        assert stored["risk.risk_per_trade_pct"] == 0.5

    def test_read_back_returns_the_applied_value(self, tmp_path: Path) -> None:
        # Test 6 — read-after-write through the same store path
        store, svc = _store(tmp_path)
        store.apply({"risk.max_account_drawdown_pct": 7.5}, source="WEB_UI")
        assert store.get_snapshot().risk.max_account_drawdown_pct == 7.5
        assert _stored(svc)["risk.max_account_drawdown_pct"] == 7.5
        # the read-back equals the persisted row (one source of truth)
        assert store.get_snapshot().to_flat_dict()["risk.max_account_drawdown_pct"] == 7.5


class TestNestedFieldEdit:
    """Test 2 — change one field under `execution`."""

    def test_execution_nested_edit_leaves_everything_else_intact(self, tmp_path: Path) -> None:
        store, svc = _store(tmp_path)
        before = store.get_snapshot().to_flat_dict()

        report = store.apply({"execution.max_slippage_points": 45}, source="WEB_UI")

        assert report.success is True
        snap = store.get_snapshot()
        assert snap.execution.max_slippage_points == 45
        assert snap.execution.symbol == before["execution.symbol"]
        assert snap.execution.timeframe == before["execution.timeframe"]
        assert snap.execution.magic_number == before["execution.magic_number"]
        assert snap.risk.risk_per_trade_pct == before["risk.risk_per_trade_pct"]
        assert snap.model.confidence_threshold == before["model.confidence_threshold"]
        assert _stored(svc)["execution.max_slippage_points"] == 45


class TestTypePreservation:
    """Test 3 — types survive the store round-trip."""

    def test_int_float_bool_string_round_trip(self, tmp_path: Path) -> None:
        store, svc = _store(tmp_path)
        store.apply(
            {
                "execution.magic_number": 123456,
                "execution.max_slippage_points": 7,
                "risk.max_account_drawdown_pct": 3.5,
                "risk.risk_per_trade_pct": 0.25,
                "risk.max_concurrent_positions": 3,
                "risk.max_spread_points": 22,
                "risk.max_allowed_lots": 1.5,
                "risk.enforce_stop_loss": False,
                "model.confidence_threshold": 0.42,
                "model.model_artifact_path": "artifacts/models/scalp/model.pt",
            },
            source="WEB_UI",
        )
        snap = store.get_snapshot()
        # ints stay ints
        assert isinstance(snap.execution.magic_number, int)
        assert isinstance(snap.execution.max_slippage_points, int)
        assert isinstance(snap.risk.max_concurrent_positions, int)
        assert isinstance(snap.risk.max_spread_points, int)
        # floats stay numeric
        assert isinstance(snap.risk.max_account_drawdown_pct, float)
        assert isinstance(snap.risk.risk_per_trade_pct, float)
        assert isinstance(snap.risk.max_allowed_lots, float)
        assert isinstance(snap.model.confidence_threshold, float)
        # bool stays bool
        assert snap.risk.enforce_stop_loss is False
        assert isinstance(snap.risk.enforce_stop_loss, bool)
        # string stays string
        assert isinstance(snap.model.model_artifact_path, str)

        # ... and the persisted store keeps the same types
        stored = _stored(svc)
        assert isinstance(stored["execution.magic_number"], int)
        assert isinstance(stored["risk.max_account_drawdown_pct"], float)
        assert stored["risk.enforce_stop_loss"] is False
        assert isinstance(stored["model.model_artifact_path"], str)


class TestMultipleEdits:
    """Test 4 — several fields in one operation, atomically."""

    def test_multi_field_apply_is_atomic(self, tmp_path: Path) -> None:
        store, svc = _store(tmp_path)
        before = store.get_snapshot().to_flat_dict()

        report = store.apply(
            {
                "risk.max_allowed_lots": 4.5,
                "model.confidence_threshold": 0.55,
                "execution.max_slippage_points": 15,
            },
            source="WEB_UI",
        )

        assert report.success is True
        assert set(report.requested_fields) == {
            "risk.max_allowed_lots",
            "model.confidence_threshold",
            "execution.max_slippage_points",
        }
        snap = store.get_snapshot()
        assert snap.risk.max_allowed_lots == 4.5
        assert snap.model.confidence_threshold == 0.55
        assert snap.execution.max_slippage_points == 15
        for key in FORM_KEYS:
            if key not in report.requested_fields:
                assert snap.to_flat_dict()[key] == before[key], f"{key} changed"


class TestInvalidEdit:
    """Test 5 — an invalid value is rejected and the store is NOT corrupted."""

    def test_out_of_range_value_is_refused_and_store_intact(self, tmp_path: Path) -> None:
        store, svc = _store(tmp_path)
        store.apply({"risk.max_account_drawdown_pct": 6.25}, source="WEB_UI")
        before = store.get_snapshot().to_flat_dict()
        version_before = store.get_version()

        report = store.apply({"risk.max_account_drawdown_pct": 0.0001}, source="WEB_UI")

        assert report.success is False
        assert report.persisted is False
        assert report.runtime_applied is False
        assert report.configuration_version == version_before  # unchanged
        assert "risk.max_account_drawdown_pct" in report.reason

        # the previous good snapshot is still active
        assert store.get_snapshot().risk.max_account_drawdown_pct == 6.25
        assert store.get_snapshot().to_flat_dict() == before
        assert _stored(svc)["risk.max_account_drawdown_pct"] == 6.25

    def test_cross_field_violation_is_refused_atomically(self, tmp_path: Path) -> None:
        # risk_per_trade_pct must stay <= max_account_drawdown_pct
        store, _svc = _store(tmp_path)
        store.apply({"risk.max_account_drawdown_pct": 2.0}, source="WEB_UI")

        report = store.apply({"risk.risk_per_trade_pct": 5.0}, source="WEB_UI")

        assert report.success is False
        assert store.get_snapshot().risk.risk_per_trade_pct == 0.5  # bootstrap value kept

    def test_unknown_key_is_refused(self, tmp_path: Path) -> None:
        store, _svc = _store(tmp_path)
        report = store.apply({"risk.totally_bogus_field": 1.0}, source="WEB_UI")
        assert report.success is False
        assert "unknown configuration key" in report.reason


class TestCanonicalMergeContract:
    """The merge the UI relies on: partial edit over the authoritative base."""

    def test_build_merges_one_key_over_the_complete_base(self, tmp_path: Path) -> None:
        store, _svc = _store(tmp_path)
        base = store.get_snapshot()

        result = build_runtime_configuration(
            version=base.version + 1,
            base=base,
            updates={"risk.max_account_drawdown_pct": 6.25},
            source="WEB_UI",
        )

        assert result.snapshot is not None
        flat = result.snapshot.to_flat_dict()
        # the edited value is the new one
        assert flat["risk.max_account_drawdown_pct"] == 6.25
        # every other field carries the authoritative base value
        for key in FORM_KEYS:
            if key == "risk.max_account_drawdown_pct":
                continue
            assert flat[key] == base.to_flat_dict()[key], f"{key} lost during merge"
        assert list(result.changed_fields) == ["risk.max_account_drawdown_pct"]

    def test_an_empty_update_is_a_no_op_not_a_refusal(self, tmp_path: Path) -> None:
        store, _svc = _store(tmp_path)
        base = store.get_snapshot()
        result = build_runtime_configuration(
            version=base.version + 1, base=base, updates={}, source="WEB_UI"
        )
        assert result.snapshot is not None
        assert list(result.changed_fields) == []
        assert result.snapshot.to_flat_dict() == base.to_flat_dict()


class TestRestartPersistence:
    """Test 7's persistence half — a fresh store rehydrates the saved values."""

    def test_new_store_rehydrates_the_persisted_configuration(self, tmp_path: Path) -> None:
        store, svc = _store(tmp_path)
        store.apply(
            {"risk.max_account_drawdown_pct": 6.25, "execution.max_slippage_points": 45},
            source="WEB_UI",
        )

        # a NEW store over the SAME settings DB (process restart)
        svc2 = _svc(tmp_path)
        store2 = RuntimeConfigStore(persistent=PersistentConfigStore(svc2))

        snap = store2.get_snapshot()
        assert snap.risk.max_account_drawdown_pct == 6.25
        assert snap.execution.max_slippage_points == 45
        # and the values the operator did NOT touch are the schema defaults
        assert snap.risk.risk_per_trade_pct == 0.5
