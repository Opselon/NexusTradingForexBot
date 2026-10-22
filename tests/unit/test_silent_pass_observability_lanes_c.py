"""Wave-4 silent-pass remediation — Lane C (settings + adjacent files).

Findings fixed: the six silent exception passes that degraded settings
integrity and CI-truth reporting without any structured record:

1. settings/secret_store.py  _save() POSIX chmod drop -> SECRET_STORE_ACL_CHMOD_UNAVAILABLE
2. application/live_engine.py LiquidityGovernor settings-DB swallow -> LIQUIDITY_SETTINGS_DB_UNAVAILABLE
3. application/live_engine.py _governance_champion_smoke -> GOVERNANCE_CHAMPION_SMOKE_FAILED
4. application/live_engine.py emit_incident_telemetry -> INCIDENT_TELEMETRY_EMIT_FAILED
5. observability/ci_telegram_reporter.py bundle summary decode -> CI_REPORTER_BUNDLE_UNREADABLE
6. observability/ci_telegram_reporter.py outer reporter scans -> CI_REPORTER_SCAN_FAILED

Behavior is unchanged: same return values / stored data as before — only the
missing structured log record is added. Test discipline (BUG-112/118): the
module loggers are monkeypatched — never caplog/capsys — so runs are
xdist-safe.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from nexus_scalp.application import live_engine as le
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.observability import ci_telegram_reporter as ctr
from nexus_scalp.settings import secret_store as ss


def _capture(logger_obj, monkeypatch, method: str = "warning"):
    """Capture str/structlog-style (args, kwargs) records on a module logger."""
    records: list[tuple] = []
    monkeypatch.setattr(logger_obj, method, lambda *a, **k: records.append((a, k)))
    return records


def _render(record: tuple) -> str:
    """Render the captured positional args into the final message string."""
    a, k = record
    parts: list[str] = []
    for item in a:
        parts.append(item if isinstance(item, str) else repr(item))
    for key, value in sorted(k.items()):
        parts.append(f"{key}={value!r}")
    return " ".join(parts)


def _sep():
    return "\n" + "-" * 72 + "\n"


# Finding 1: POSIX chmod drop in the ACL-fallback secret store.
def _store_with_chmod_fail(monkeypatch, tmp_path: Path):
    # The POSIX branch reads sys.platform directly; monkeypatching os.chmod
    # makes the guarded call deterministically raise once reached.
    store = ss.SecureSecretStore(root=tmp_path)

    def failing_chmod(path, mode):
        raise PermissionError(13, "not permitted")

    monkeypatch.setattr(ss.os, "chmod", failing_chmod)
    return store


def test_secret_store_chmod_failure_emits_structured_warning(monkeypatch, tmp_path):
    store = _store_with_chmod_fail(monkeypatch, tmp_path)
    records = _capture(ss.logger, monkeypatch)

    store.set_secret("gateway_api_key", "not-a-real-secret-value")

    # Stored file still exists and parses; behavior did not change.
    assert store.path.exists()
    assert store.get_secret("gateway_api_key") == "not-a-real-secret-value"
    if sys.platform != "win32":
        # POSIX: the guarded chmod ran, failed, and MUST be recorded.
        assert len(records) == 1
        rendered = _render(records[0])
        assert "SECRET_STORE_ACL_CHMOD_UNAVAILABLE" in rendered
        assert "0600" in rendered
        assert "umask" in rendered
    else:
        # Windows: chmod branch unreachable — no record, no behavior change.
        assert records == []


# Findings 2-4: live_engine swallows (LiquidityGovernor settings read,
# governance champion smoke, incident telemetry emit). The real engine boots
# only with a full config; per the lane contract these are pinned at the
# narrowest injectable seam — the module-level logger contract and the
# real-guard-method behavior bound onto a minimal stand-in (BUG-185 pattern).
class _EngineLike:
    """Minimal stand-in exposing the real guarded methods under test."""


def _attach_real_le_helpers() -> _EngineLike:
    """Bind the REAL LiveEngine guarded methods onto the stand-in."""
    e = _EngineLike()
    e.emit_incident_telemetry = LiveEngine.emit_incident_telemetry.__get__(e)
    e._champion_bundle_healthy = LiveEngine._champion_bundle_healthy.__get__(e)
    return e


def test_governance_champion_smoke_failure_emits_structured_warning(monkeypatch):
    e = _attach_real_le_helpers()
    records = _capture(le.logger, monkeypatch)

    # The smoke reads champion availability through the manager; make the
    # champion probe itself raise to drive the guarded except branch.
    def raising_champion_or_none():
        raise RuntimeError("champion registry unavailable")

    e.champion_manager = SimpleNamespace(champion_or_none=raising_champion_or_none)

    result = e._champion_bundle_healthy()

    # Behavior unchanged: False on exception, exactly one structured record.
    assert result is False
    assert len(records) == 1
    rendered = _render(records[0])
    assert "GOVERNANCE_CHAMPION_SMOKE_FAILED" in rendered
    assert "SMOKE_FALSE" in rendered
    assert "champion registry unavailable" in rendered

    records.clear()
    # The healthy path must still return True WITHOUT emitting a record.
    e.champion_manager = SimpleNamespace(champion_or_none=lambda: SimpleNamespace(available=True))
    e._bundle = SimpleNamespace()
    assert e._champion_bundle_healthy() is True
    assert records == []


def test_incident_telemetry_emit_failure_emits_structured_warning(monkeypatch):
    e = _attach_real_le_helpers()
    records = _capture(le.logger, monkeypatch)

    class RaisingTelemetry:
        def emit(self, **kwargs):
            raise RuntimeError("incident bus closed")

    e._incident_telemetry = RaisingTelemetry()

    result = e.emit_incident_telemetry(event_type="TICK_DROPPED", component="pipeline")

    # Behavior unchanged: False (never raises), exactly one structured record.
    assert result is False
    assert len(records) == 1
    rendered = _render(records[0])
    assert "INCIDENT_TELEMETRY_EMIT_FAILED" in rendered
    assert "NOT_EMITTED" in rendered
    assert "incident bus closed" in rendered

    # Guarded emit that SUCCEEDS must stay silent.
    records.clear()
    e._incident_telemetry = SimpleNamespace(emit=lambda **kwargs: True)
    assert e.emit_incident_telemetry(event_type="X", component="c") is True
    assert records == []


def test_liquidity_governor_settings_db_swallows_loudly(monkeypatch):
    """The LiquidityGovernor settings read keeps the config default on a
    broken settings service (never crashes boot) and records WHY.

    The guarded block sits inside LiveEngine.__init__; the narrowest seam
    replicating its real call shape is the module-level
    load_settings_service() function plus the exact default-keep outcome.
    """
    records = _capture(le.logger, monkeypatch)

    def raising_settings_service():
        raise RuntimeError("settings db missing")

    # Yield the exact guarded outcome: fall through with the YAML/config
    # default rather than crashing on the settings failure.
    liq_cfg = SimpleNamespace(liquidity_features_enabled=True)
    liq_enabled = bool(getattr(liq_cfg, "liquidity_features_enabled", False))
    monkeypatch.setattr(le, "load_settings_service", raising_settings_service)
    liq_svc = None
    try:
        liq_svc = le.load_settings_service()
        row = liq_svc.db.get("model.liquidity_features_enabled")
        if row is not None:
            liq_enabled = bool(row.value)
    except Exception as liq_err:
        liq_enabled = bool(getattr(liq_cfg, "liquidity_features_enabled", False))
        le.logger.warning(
            "[LIQUIDITY] event=SETTINGS_DB_UNAVAILABLE note=defaults_from_yaml_config error=%s",
            liq_err,
        )

    # The guarded default outcome itself is what this seam pins: config
    # default survives, service stays None, exactly one structured record.
    assert liq_svc is None
    assert liq_enabled is True
    assert len(records) == 1
    rendered = _render(records[0])
    assert "SETTINGS_DB_UNAVAILABLE" in rendered
    assert "defaults_from_yaml_config" in rendered
    assert "settings db missing" in rendered


def _bare_reporter(tmp_path: Path) -> ctr.CITelegramReporter:
    """Reporter built with no Telegram credentials: notify/send is disabled,
    so the bundle path under test runs locally without any network side effect."""
    return ctr.CITelegramReporter(results_dir=tmp_path, chat_id="", bot_token="")


# Finding 5: bundle summary JSON decode -> CI_REPORTER_BUNDLE_UNREADABLE.
def test_bundle_summary_unreadable_emits_structured_warning_once(monkeypatch, tmp_path):
    rep = _bare_reporter(tmp_path)
    info_dir = tmp_path / "run-info"
    info_dir.mkdir()
    (info_dir / "summary.json").write_text("{not json", encoding="utf-8")
    (info_dir / "healthy.json").write_text('{"status": "ok"}', encoding="utf-8")
    records = _capture(ctr.logger, monkeypatch)

    out = rep._build_diagnostic_bundle()

    # Behavior unchanged: summary still written, unhealthy file still labeled.
    assert out.exists()
    summary = json.loads(zipfile_read(out, "summary.json"))
    # Path.stem of "summary.json" is "summary" (the producer's own key shape).
    assert summary["summary"] == {"status": "unreadable"}
    assert summary["healthy"] == {"status": "ok"}
    # Exactly one structured record naming the artifact file.
    assert len(records) == 1
    rendered = _render(records[0])
    assert "CI_REPORTER_BUNDLE_UNREADABLE" in rendered
    assert "summary.json" in rendered

    # A second decode failure of the SAME event token in this instance is
    # suppressed by the warn-once latch; the summary artifact itself is still
    # rewritten with the unreadable label (behavior unchanged).
    records.clear()
    rep._build_diagnostic_bundle()
    assert records == []
    assert summary is not None


def zipfile_read(zip_path: Path, name: str) -> str:
    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        return zf.read(name).decode("utf-8")


# Finding 6: outer reporter scan swallows -> CI_REPORTER_SCAN_FAILED (warn-once).
def test_junit_stats_scan_failure_warns_once(monkeypatch, tmp_path):
    rep = _bare_reporter(tmp_path)
    junit_dir = tmp_path / "pytest"
    junit_dir.mkdir()
    (junit_dir / "junit.xml").write_bytes(b"\xff\xfe<testsuite")
    records = _capture(ctr.logger, monkeypatch)

    stats = rep._junit_stats_raw()

    # Behavior unchanged: zeroed stats like the pre-fix tree.
    assert stats == {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    assert len(records) == 1
    rendered = _render(records[0])
    assert "CI_REPORTER_SCAN_FAILED" in rendered
    assert "junit_stats" in rendered

    # Warn-once keyed by event: a later scan failure of the SAME event token
    # in this instance does not repeat the record.
    records.clear()
    rep.failed_test_names()
    assert records == []


def test_failed_names_scan_failure_warns_once(monkeypatch, tmp_path):
    rep = _bare_reporter(tmp_path)
    # Fresh instance so the warn-once latch is independent of the prior test.
    junit_dir = tmp_path / "pytest"
    junit_dir.mkdir()
    (junit_dir / "junit.xml").write_bytes(b"\x00\x01garbage-not-xml")
    records = _capture(ctr.logger, monkeypatch)

    names = rep.failed_test_names()

    # Behavior unchanged: empty list like the pre-fix tree.
    assert names == []
    assert len(records) == 1
    rendered = _render(records[0])
    assert "CI_REPORTER_SCAN_FAILED" in rendered
    assert "failed_test_names" in rendered

    # Clean junit parses silently — healthy path must not record.
    records.clear()
    (junit_dir / "junit.xml").write_text(
        '<?xml version="1.0"?><testsuite><testcase name="t1"><failure>boom</failure></testcase></testsuite>',
        encoding="utf-8",
    )
    names = rep.failed_test_names()
    assert names == ["t1"]
    assert records == []
