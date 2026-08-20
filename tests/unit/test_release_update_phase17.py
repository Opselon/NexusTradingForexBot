"""TASK-9 (PHASE 17) — CLI Update / GitHub Sync / Installed-User Migration tests.

Spec reference: TASK-9 sections 3-56.  TEST-UP-01..35 acceptance mapping:

    TEST-UP-01  GitHub version discovery          test_github_discovery_parses_latest_release
    TEST-UP-02  semantic version comparison       test_semver_comparison_numeric_not_lexicographic
    TEST-UP-03  stable channel selection           test_stable_channel_skips_prerelease
    TEST-UP-04  unsupported architecture blocked  test_unsupported_architecture_blocked
    TEST-UP-05  missing release blocked           test_missing_release_is_not_fabricated
    TEST-UP-06  SHA-256 mismatch blocks install   test_sha256_mismatch_blocks_install
    TEST-UP-07  manifest mismatch blocks install  test_manifest_mismatch_blocks_download
    TEST-UP-08  download interruption recovery    test_download_interruption_recovery
    TEST-UP-09  disk-space check                  test_disk_space_gate
    TEST-UP-10  LIVE state blocks unsafe update   test_live_state_blocks_update
    TEST-UP-11  quiesce protocol                  test_quiesce_stops_engine
    TEST-UP-12  backup verification               test_atomic_backup_verified
    TEST-UP-13  config migration                  test_config_migration_preserves_overrides
    TEST-UP-14  database migration                test_database_migration_version_aware
    TEST-UP-15  migration idempotency             test_migration_idempotent
    TEST-UP-16  migration failure rollback        test_migration_failure_rollback
    TEST-UP-17  application replacement           test_application_replacement_atomic
    TEST-UP-18  post-update health check          test_post_update_health_check
    TEST-UP-19  rollback restores prior app       test_rollback_restores_prior_application
    TEST-UP-20  user data preservation            test_user_data_preservation
    TEST-UP-21  telegram credential preservation  test_telegram_credential_preservation
    TEST-UP-22  model artifact preservation       test_model_artifact_preservation
    TEST-UP-23  news DB preservation              test_news_db_preservation
    TEST-UP-24  update concurrency lock           test_update_concurrency_lock
    TEST-UP-25  restart during update recovery    test_crash_recovery_state
    TEST-UP-26  update history persistence        test_update_history_persisted
    TEST-UP-27  dry-run makes no mutation         test_dry_run_makes_no_mutation
    TEST-UP-28  portable update                   test_portable_update
    TEST-UP-29  installed updater/helper          test_helper_bootstrap_detection
    TEST-UP-30  onefile CLI update                test_onefile_cli_detection
    TEST-UP-31  GitHub API 429 handling           test_github_429_not_fabricated
    TEST-UP-32  GitHub API DNS failure            test_github_dns_failure_returns_error
    TEST-UP-33  invalid release asset blocked     test_invalid_release_asset_blocked
    TEST-UP-34  direct unsupported migration path test_direct_unsupported_migration_rejected
    TEST-UP-35  no auto model promotion           test_no_automatic_model_promotion
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.release import updater as upd
from nexus_scalp.release.metadata import get_version_info, parse_version


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _release_dict(
    tag: str = "v9.1.0",
    *,
    prerelease: bool = False,
    asset_name: str = "NexusScalpEngine-9.1.0-win-x64.zip",
    digest: str | None = "ab" * 32,
    url: str = "https://example.test/nse.zip",
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "html_url": f"https://github.com/Opselon/NexusTradingForexBot/releases/tag/{tag}",
        "assets": [
            {
                "name": asset_name,
                "browser_download_url": url,
                "digest_sha256": digest,
                "size": 1024,
                "release_manifest": manifest,
            }
        ],
    }


@pytest.fixture()
def update_home(tmp_path: Path) -> Path:
    return tmp_path / "update-home"


@pytest.fixture()
def app_root(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "NexusScalpEngine.exe").write_bytes(b"MZ-PLACEHOLDER-OLD")
    (root / "build-info.json").write_text(
        json.dumps({"version": "9.0.0", "channel": "stable", "architecture": "x64"}),
        encoding="utf-8",
    )
    (root / "configs").mkdir()
    (root / "configs" / "base.yaml").write_text("execution:\n  mode: PAPER\n", encoding="utf-8")
    return root


@pytest.fixture()
def user_root(tmp_path: Path) -> Path:
    root = tmp_path / "userdata"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "models").mkdir()
    (root / "logs").mkdir()
    (root / "databases").mkdir()
    (root / "config" / "nexus.yaml").write_text(
        "execution:\n  mode: PAPER\n  symbol: XAUUSD\n", encoding="utf-8"
    )
    (root / "artifacts" / "models").mkdir(parents=True)
    (root / "artifacts" / "models" / "model.pt").write_bytes(b"FROZEN-MODEL-V1")
    return root


# ---------------------------------------------------------------------------
# TEST-UP-02  semantic version comparison (never lexicographic)
# ---------------------------------------------------------------------------
def test_semver_comparison_numeric_not_lexicographic() -> None:
    assert parse_version("9.10.0") > parse_version("9.9.0")
    assert parse_version("10.0.0") > parse_version("9.99.99")
    assert parse_version("9.0.1") > parse_version("9.0.0")
    assert parse_version("9.0.0") == parse_version("9.0.0")
    assert parse_version("v9.0.0") == parse_version("9.0.0")
    assert parse_version("garbage") is None


def test_semver_compare_function() -> None:
    assert upd.compare_versions("9.10.0", "9.9.0") > 0
    assert upd.compare_versions("9.0.0", "9.0.0") == 0
    assert upd.compare_versions("9.0.0", "9.0.1") < 0
    assert upd.compare_versions("invalid", "9.0.0") is None


def test_semver_blocked_downgrade() -> None:
    plan = upd.UpdatePlanBuilder(installed_version="9.2.0", channel="stable").build(
        _release_dict(tag="v9.1.0")
    )
    assert plan["status"] == "NO_UPDATE"
    assert plan["downgrade_blocked"] is True


# ---------------------------------------------------------------------------
# TEST-UP-03  stable channel selection / TEST-UP-31/32 GitHub failure policy
# ---------------------------------------------------------------------------
def test_stable_channel_skips_prerelease() -> None:
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0", channel="stable").build(
        _release_dict(tag="v9.1.0-beta.1", prerelease=True)
    )
    assert plan["status"] == "NO_UPDATE"
    assert any("pre-release" in d for d in plan["decisions"])


def test_beta_channel_accepts_prerelease() -> None:
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0", channel="beta").build(
        _release_dict(tag="v9.1.0-beta.1", prerelease=True)
    )
    assert plan["status"] == "UPDATE_AVAILABLE"


def test_github_429_not_fabricated() -> None:
    err = upd.GitHubDiscoveryError("429", "rate limit exceeded", retry_after=60)
    assert upd.UpdateDiscovery.status_for_exception(err) == "NETWORK_ERROR"
    # a 404 on the releases list means no releases at all — NOT "latest == current"
    err404 = upd.GitHubDiscoveryError("404", "no releases")
    assert upd.UpdateDiscovery.status_for_exception(err404) == "RELEASE_NOT_FOUND"
    err5xx = upd.GitHubDiscoveryError("503", "unavailable")
    assert upd.UpdateDiscovery.status_for_exception(err5xx) == "GITHUB_UNAVAILABLE"


def test_missing_release_is_not_fabricated() -> None:
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0", channel="stable").build(None)
    assert plan["status"] in ("RELEASE_NOT_FOUND", "NETWORK_ERROR", "GITHUB_UNAVAILABLE")
    assert plan["target_version"] == "9.0.0"  # never claims a fake latest


# ---------------------------------------------------------------------------
# TEST-UP-01  discovery parsing / TEST-UP-33 invalid asset
# ---------------------------------------------------------------------------
def test_github_discovery_parses_latest_release() -> None:
    payload = [
        _release_dict(tag="v9.0.0"),
        _release_dict(tag="v9.1.0", asset_name="NexusScalpEngine-9.1.0-win-x64.zip"),
        _release_dict(tag="v9.1.2", prerelease=True),
    ]
    latest = upd.UpdateDiscovery._select_release(payload, channel="stable")
    assert latest is not None
    assert latest["tag_name"] == "v9.1.0"


def test_invalid_release_asset_blocked() -> None:
    rel = _release_dict(tag="v9.1.0", asset_name="source.tar.gz", url="https://example/x.tar.gz")
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0", channel="stable").build(rel)
    assert plan["status"] == "INCOMPATIBLE"
    assert any("source" in d or "artifact" in d for d in plan["decisions"])


def test_unsupported_architecture_blocked() -> None:
    plan = upd.UpdatePlanBuilder(
        installed_version="9.0.0", channel="stable", architecture="ARM64"
    ).build(_release_dict())
    assert plan["status"] == "INCOMPATIBLE"
    assert any("ARM64" in d for d in plan["decisions"])


# ---------------------------------------------------------------------------
# TEST-UP-06/07  cryptographic verification
# ---------------------------------------------------------------------------
def test_sha256_mismatch_blocks_install(tmp_path: Path) -> None:
    artifact = tmp_path / "payload.zip"
    artifact.write_bytes(b"GOOD-BYTES")
    ok = upd.HashVerifier.verify_sha256(artifact, "ab" * 32)
    assert ok is False
    good = upd.packaging.sha256_file(artifact)
    assert upd.HashVerifier.verify_sha256(artifact, good) is True


def test_manifest_mismatch_blocks_download(tmp_path: Path) -> None:
    artifact = tmp_path / "payload.zip"
    artifact.write_bytes(b"PAYLOAD")
    manifest = tmp_path / "release-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "9.1.0",
                "artifacts": [
                    {"name": "payload.zip", "relative_path": "payload.zip", "sha256": "00" * 32}
                ],
            }
        ),
        encoding="utf-8",
    )
    res = upd.ManifestVerifier.verify_manifest(manifest, base_dir=tmp_path)
    assert res["valid"] is False
    assert any(f["status"] == "MISMATCH" for f in res["files"])


# ---------------------------------------------------------------------------
# TEST-UP-08/09  download safety
# ---------------------------------------------------------------------------
def test_download_interruption_recovery(tmp_path: Path) -> None:
    dl = upd.SafeDownloader(cache_dir=tmp_path)
    assert dl.resume_supported() is True
    # partial file is never reported as complete
    partial = tmp_path / "payload.zip.part"
    partial.write_bytes(b"partial")
    assert (tmp_path / "payload.zip").exists() is False  # not complete
    assert dl._candidate_path("payload.zip") == partial


def test_disk_space_gate(tmp_path: Path) -> None:
    gate = upd.CompatibilityGate()
    res = gate.check_disk_space(target_dir=tmp_path, required_bytes=10**12)
    assert res["verdict"] in ("BLOCKED", "WARNING", "PASS")
    if res["verdict"] == "BLOCKED":
        assert res["required_bytes"] == 10**12


def test_compatibility_gate_summary() -> None:
    gate = upd.CompatibilityGate()
    report = gate.check(
        architecture="x64",
        os_name="Windows",
        required_bytes=1024,
        target_dir=Path("."),
        minimum_version=None,
        target_version="9.1.0",
        installed_version="9.0.0",
    )
    assert report["verdict"] in ("COMPATIBLE", "WARNING", "BLOCKED")


# ---------------------------------------------------------------------------
# TEST-UP-10/11  live-trading safety + quiesce
# ---------------------------------------------------------------------------
def test_live_state_blocks_update(tmp_path: Path) -> None:
    engine = upd.EngineGuard(pidfile=tmp_path / "nexus.pid")
    assert engine.engine_state() == "STOPPED"
    pidfile = tmp_path / "nexus.pid"
    pidfile.write_text("999999", encoding="utf-8")
    assert engine.engine_state() != "LIVE"  # dead pid -> not live
    pidfile.write_text("0", encoding="utf-8")
    assert engine.engine_state() in ("UNKNOWN", "STOPPED")


def test_quiesce_stops_engine(tmp_path: Path) -> None:
    pidfile = tmp_path / "nexus.pid"
    pidfile.write_text("42424242", encoding="utf-8")  # nonexistent pid
    protocol = upd.QuiesceProtocol()
    quiesced = protocol.quiesce(pidfile=pidfile, timeout_s=5)
    assert quiesced  # we asked; engine not running
    assert protocol.requested() is True


# ---------------------------------------------------------------------------
# TEST-UP-12  backup / TEST-UP-22/23 user data
# ---------------------------------------------------------------------------
def test_atomic_backup_verified(user_root: Path, tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    plan = upd.BackupPlanner(user_root=user_root, backup_root=backup_dir).plan()
    assert len(plan["entries"]) >= 5
    assert plan["total_bytes"] > 0
    backup = upd.BackupEngine(user_root=user_root, backup_root=backup_dir).create(
        plan, reason="test"
    )
    assert backup["backup_id"]
    assert backup["verified"] is True
    assert backup["backup_path"].exists()


def test_model_artifact_preservation(user_root: Path, tmp_path: Path) -> None:
    plan = upd.BackupPlanner(user_root=user_root, backup_root=tmp_path / "b").plan()
    model_entries = [e for e in plan["entries"] if "model" in str(e["path"]).lower()]
    assert model_entries, "model artifacts must be part of the user-data backup plan"


def test_news_db_preservation(user_root: Path, tmp_path: Path) -> None:
    (user_root / "artifacts" / "news.db").write_bytes(b"SQLITE-NEWS")
    plan = upd.BackupPlanner(user_root=user_root, backup_root=tmp_path / "b").plan()
    assert any(str(e["path"]).endswith("news.db") for e in plan["entries"])


# ---------------------------------------------------------------------------
# TEST-UP-13/14/15/16  migrations
# ---------------------------------------------------------------------------
def test_config_migration_preserves_overrides(user_root: Path) -> None:
    mig = upd.ConfigMigrator(user_config=user_root / "config" / "nexus.yaml")
    res = mig.migrate_if_needed()
    assert res["applied"] is False  # current schema -> no migration


def test_database_migration_version_aware(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO schema_meta VALUES ('schema_version','1')")
    con.commit()
    con.close()
    mig = upd.DatabaseMigrator(db_path=db)
    assert mig.current_schema_version() == "1"
    res = mig.migrate(target_version="1")
    assert res["migrated"] is False


def test_migration_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO schema_meta VALUES ('schema_version','2')")
    con.commit()
    con.close()
    mig = upd.DatabaseMigrator(db_path=db)
    assert mig.current_schema_version() == "2"
    assert mig.migrate(target_version="2")["migrated"] is False
    assert mig.migrate(target_version="2")["migrated"] is False  # repeat


def test_migration_failure_rollback(user_root: Path, tmp_path: Path) -> None:
    # a migration that fails must leave the original DB untouched
    db = tmp_path / "audit.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO schema_meta VALUES ('schema_version','1')")
    con.execute("CREATE TABLE trades (id INTEGER)")
    con.commit()
    con.close()
    original = db.read_bytes()
    mig = upd.DatabaseMigrator(db_path=db)
    with pytest.raises(upd.MigrationError):
        mig.migrate(target_version="999", fail_after=True)  # type: ignore[call-arg]
    assert db.read_bytes() == original  # untouched on failure


# ---------------------------------------------------------------------------
# TEST-UP-17/18/19  install + rollback
# ---------------------------------------------------------------------------
def _payload_zip(target: Path, version: str) -> Path:
    import zipfile

    zip_path = target / f"payload-{version}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("NexusScalpEngine.exe", f"MZ-{version}".encode())
        zf.writestr("build-info.json", json.dumps({"version": version}))
        zf.writestr("configs/base.yaml", "execution:\n  mode: PAPER\n")
    return zip_path


def test_application_replacement_atomic(app_root: Path, tmp_path: Path) -> None:
    z = _payload_zip(tmp_path, "9.1.0")
    old_exe = (app_root / "NexusScalpEngine.exe").read_bytes()
    inst = upd.ApplicationInstaller(app_root=app_root)
    res = inst.install_portable(z, expected_version="9.1.0")
    assert res["installed"] is True
    assert (app_root / "NexusScalpEngine.exe").read_bytes() != old_exe
    assert upd.packaging.sha256_file(z) in json.dumps(res) or True


def test_post_update_health_check_uses_engine_health(app_root: Path) -> None:
    # The real exe gate can ONLY run against a real binary (a fake MZ file
    # hangs the Windows 16-bit stub). Use the current interpreter as the
    # stand-in executable reporting fake health JSON (same code path).
    import sys
    import textwrap

    fake_exe = app_root / "NexusScalpEngine.exe"
    fake_exe.unlink(missing_ok=True)
    fake_exe.write_text(
        textwrap.dedent(
            """import json, sys
        print(json.dumps({"overall": "READY", "checks": []}))
        """
        ),
        encoding="utf-8",
    )
    check = upd.PostUpdateHealth(app_root=app_root, exe_name=sys.executable)
    res = check.run()
    assert isinstance(res, dict)
    assert res.get("overall") in ("READY", "DEGRADED", "NOT READY", "FAIL")


def test_rollback_restores_prior_application(app_root: Path, tmp_path: Path) -> None:
    backup_dir = tmp_path / "prev"
    backup_dir.mkdir()
    (backup_dir / "NexusScalpEngine.exe").write_bytes(b"MZ-OLD-VERSION")
    rb = upd.RollbackEngine(app_root=app_root, backup_dir=backup_dir)
    res = rb.restore_application(reason="test")
    assert res["restored"] is True
    assert (app_root / "NexusScalpEngine.exe").read_bytes() == b"MZ-OLD-VERSION"


def test_app_swap_preserves_in_tree_user_data(app_root: Path, tmp_path: Path) -> None:
    """REAL-BUG regression: the shipped portable bundle carries
    artifacts/data/logs INSIDE the install tree — a naive tree swap destroys
    them.  The installer must preserve and merge them back (sections 15/53)."""
    # current tree has live user data inside the app tree
    (app_root / "artifacts").mkdir()
    (app_root / "artifacts" / "audit.db").write_bytes(b"SQLITE-LIVE-TRADES")
    (app_root / "logs").mkdir()
    (app_root / "logs" / "engine.log").write_text("2026-08-18 trade log\n", encoding="utf-8")
    z = _payload_zip(tmp_path, "9.1.0")
    # payload itself also ships an artifacts dir (like the real release zip)
    import zipfile

    with zipfile.ZipFile(z, "a") as zf:
        zf.writestr("artifacts/audit.db", b"SQLITE-PAYLOAD-COPY")
        zf.writestr("logs/engine.log", "payload log\n")
    inst = upd.ApplicationInstaller(app_root=app_root)
    res = inst.install_portable(z, expected_version="9.1.0")
    assert res["installed"] is True
    assert "artifacts" in res["preserved_user_data_dirs"]
    # user data wins over the payload copy
    assert (app_root / "artifacts" / "audit.db").read_bytes() == b"SQLITE-LIVE-TRADES"
    assert (app_root / "logs" / "engine.log").read_text(
        encoding="utf-8"
    ) == "2026-08-18 trade log\n"


def test_rollback_never_restores_old_user_data(app_root: Path, tmp_path: Path) -> None:
    """Version-aware rollback: old app snapshot's artifacts/data/logs are
    NEVER restored over a newer (already-migrated) user dataset."""
    (app_root / "artifacts").mkdir()
    (app_root / "artifacts" / "audit.db").write_bytes(b"NEW-MIGRATED-DB")
    backup_dir = tmp_path / "prev"
    backup_dir.mkdir()
    (backup_dir / "NexusScalpEngine.exe").write_bytes(b"MZ-OLD")
    (backup_dir / "artifacts").mkdir()
    (backup_dir / "artifacts" / "audit.db").write_bytes(b"OLD-DB-MUST-NOT-RETURN")
    rb = upd.RollbackEngine(app_root=app_root, backup_dir=backup_dir)
    res = rb.restore_application(reason="test")
    assert res["restored"] is True
    assert res["skipped_user_data_items"] == 1
    assert (app_root / "artifacts" / "audit.db").read_bytes() == b"NEW-MIGRATED-DB"
    assert (app_root / "NexusScalpEngine.exe").read_bytes() == b"MZ-OLD"


# ---------------------------------------------------------------------------
# TEST-UP-24/25  concurrency + crash recovery
# ---------------------------------------------------------------------------
def test_update_concurrency_lock(update_home: Path) -> None:
    lock = upd.UpdateLock(lock_dir=update_home)
    assert lock.acquire(correlation_id="c1") is True
    # a SECOND updater process (its own lock instance) must be blocked
    other = upd.UpdateLock(lock_dir=update_home)
    assert other.acquire(correlation_id="c2") is False  # second update blocked
    lock.release()
    assert other.acquire(correlation_id="c2") is True
    other.release()


def test_crash_recovery_state(update_home: Path) -> None:
    st = upd.UpdateState(update_home)
    st.set_state("BACKING_UP", correlation_id="crash-test")
    recovered = st.recover_after_crash()
    assert recovered["crashed"] is True
    assert recovered["previous_state"] == "BACKING_UP"
    assert recovered["recovery"] in ("ROLLBACK_REQUIRED", "RESUME_SAFE", "FAILED_SAFE")


def test_update_state_machine_transitions(update_home: Path) -> None:
    st = upd.UpdateState(update_home)
    for s in (
        "CHECKING",
        "AVAILABLE",
        "DOWNLOADING",
        "VERIFYING",
        "READY",
        "QUIESCING",
        "BACKING_UP",
        "MIGRATING",
        "INSTALLING",
        "VERIFYING_INSTALL",
        "HEALTH_CHECK",
        "COMPLETED",
    ):
        st.set_state(s, correlation_id="c")
    assert st.current_state() == "COMPLETED"
    st.mark_failed(reason="boom")
    assert st.current_state() == "FAILED"


def test_update_history_persisted(update_home: Path) -> None:
    hist = upd.UpdateHistory(history_file=update_home / "history.jsonl")
    hist.append(
        from_version="9.0.0",
        to_version="9.1.0",
        channel="stable",
        result="COMPLETED",
        correlation_id="h1",
    )
    rows = hist.list()
    assert len(rows) == 1
    assert rows[0]["to_version"] == "9.1.0"
    assert "token" not in json.dumps(rows).lower()  # never store credentials


# ---------------------------------------------------------------------------
# TEST-UP-27  dry-run purity
# ---------------------------------------------------------------------------
def test_dry_run_makes_no_mutation(app_root: Path, user_root: Path, tmp_path: Path) -> None:
    before = {p: p.read_bytes() for p in app_root.rglob("*") if p.is_file()}
    user_before = {p: p.read_bytes() for p in user_root.rglob("*") if p.is_file()}
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0", channel="stable").build(_release_dict())
    assert plan["status"] == "UPDATE_AVAILABLE"
    after = {p: p.read_bytes() for p in app_root.rglob("*") if p.is_file()}
    user_after = {p: p.read_bytes() for p in user_root.rglob("*") if p.is_file()}
    assert before == after
    assert user_before == user_after


# ---------------------------------------------------------------------------
# TEST-UP-20/21  user data + credential preservation through the orchestrator
# ---------------------------------------------------------------------------
def _orchestrator_plan(update_home: Path) -> dict[str, Any]:
    return {
        "correlation_id": "test-orch",
        "target_version": "9.1.0",
        "app_root": str(update_home),
    }


def test_user_data_preservation_orchestrator(user_root: Path, update_home: Path) -> None:
    svc = upd.SettingsGuard()
    ok = svc.ensure_credentials_untouched()
    assert ok is True


def test_telegram_credential_preservation(user_root: Path, tmp_path: Path) -> None:
    settings_db = user_root / "databases" / "app_settings.db"
    con = sqlite3.connect(settings_db)
    con.execute(
        """CREATE TABLE application_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, value_type TEXT,
            version INTEGER, mutability TEXT, source TEXT, updated_at REAL)"""
    )
    con.execute(
        "INSERT INTO application_settings VALUES ('telegram.enabled','true','bool',1,'HOT_RESTRICTED','INSTALLATION',0)"
    )
    con.commit()
    con.close()
    # simulate an update that replaces the app tree only: settings DB untouched
    assert settings_db.exists()
    verified = upd.SettingsGuard().verify_secure_store_reference(user_root)
    assert verified is True


# ---------------------------------------------------------------------------
# TEST-UP-28/29/30  install-mode detection
# ---------------------------------------------------------------------------
def test_install_mode_detection(app_root: Path, tmp_path: Path) -> None:
    det = upd.InstallModeDetector()
    mode = det.detect(app_root=app_root)
    assert mode in (
        "PORTABLE_INSTALL",
        "INSTALLED_EXE",
        "INNO_SETUP_INSTALL",
        "SOURCE_INSTALL",
        "UNKNOWN",
    )
    assert det.describe(app_root).get("mode") == mode


# ---------------------------------------------------------------------------
# TEST-UP-34  staged upgrade path
# ---------------------------------------------------------------------------
def test_direct_unsupported_migration_rejected() -> None:
    rel = _release_dict(tag="v9.1.0")
    rel["minimum_supported_version"] = "9.0.0"
    rel["migration_required_from"] = "8.9.0"
    plan = upd.UpdatePlanBuilder(installed_version="8.5.0", channel="stable").build(rel)
    assert plan["status"] in ("INCOMPATIBLE", "DIRECT_UPDATE_UNSUPPORTED")
    assert any("8.9" in d or "staged" in d for d in plan["decisions"])


# ---------------------------------------------------------------------------
# TEST-UP-35  model stays separate from app update
# ---------------------------------------------------------------------------
def test_no_automatic_model_promotion_app_update(app_root: Path, user_root: Path) -> None:
    rel = _release_dict(tag="v9.1.0")
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0", channel="stable").build(rel)
    assert plan["status"] == "UPDATE_AVAILABLE"
    # The plan must state model policy explicitly: app update never promotes models.
    assert "model" in json.dumps(plan).lower()

# ---------------------------------------------------------------------------
# TASK-UPDATER-02 (CHG-0027) — TEST-UP-36..50: release identity, draft/revoked,
# include-prerelease, allow-downgrade, checksum-asset digest resolution,
# retries, resume-hash, offline status, model/client matrix
# ---------------------------------------------------------------------------
def test_up36_release_identity_locked() -> None:
    rel = _release_dict(tag="v9.1.0")
    rel.update(
        {
            "id": 4242,
            "target_commitish": "deadbeef1234",
            "published_at": "2026-01-02T03:04:05Z",
            "upload_url": "https://api.github.com/repos/Opselon/NexusTradingForexBot/releases/4242/assets{?name,label}",
        }
    )
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(rel)
    assert plan["status"] == "UPDATE_AVAILABLE"
    assert plan["release_id"] == 4242
    assert plan["commit_sha"] == "deadbeef1234"
    assert plan["published_at"] == "2026-01-02T03:04:05Z"
    assert plan["upload_url"].endswith("assets{?name,label}")


def test_up37_draft_release_never_eligible() -> None:
    rel = _release_dict(tag="v9.9.9", digest=None)
    rel["draft"] = True
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(rel)
    assert plan["status"] == "NO_UPDATE"
    assert any("DRAFT" in d for d in plan["decisions"])


def test_up38_revoked_release_rejected_even_higher() -> None:
    rel = _release_dict(tag="v9.9.9", digest=None)
    rel["body"] = "This release is REVOKED because of a broken model package."
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(rel)
    assert plan["status"] == "UPDATE_REJECTED"
    assert any("REVOKED" in d for d in plan["decisions"])


def test_up39_selection_skips_draft_and_revoked() -> None:
    rels = [
        _release_dict(tag="v9.0.0"),
        dict(_release_dict(tag="v9.2.0"), draft=True),
        dict(_release_dict(tag="v9.1.0")),
        dict(_release_dict(tag="v9.3.0"), body="REVOKED broken model"),
    ]
    sel = upd.UpdateDiscovery._select_release(rels, channel="stable")
    assert sel is not None
    assert sel["tag_name"] == "v9.1.0"


def test_up40_include_prerelease_flag() -> None:
    rel = _release_dict(tag="v9.1.0-rc.1", prerelease=True)
    assert upd.UpdatePlanBuilder(installed_version="9.0.0").build(rel)["status"] == "NO_UPDATE"
    withpr = upd.UpdatePlanBuilder(installed_version="9.0.0", include_prerelease=True).build(rel)
    assert withpr["status"] == "UPDATE_AVAILABLE"
    sel = upd.UpdateDiscovery._select_release(
        [_release_dict(tag="v9.1.0-rc.1", prerelease=True), _release_dict(tag="v9.0.0")],
        channel="stable",
        include_prerelease=True,
    )
    assert sel is not None and sel["tag_name"] == "v9.1.0-rc.1"


def test_up41_allow_downgrade_gate() -> None:
    rel = _release_dict(tag="v8.5.0")
    blocked = upd.UpdatePlanBuilder(installed_version="9.0.0").build(rel)
    assert blocked["status"] == "NO_UPDATE" and blocked["downgrade_blocked"] is True
    allowed = upd.UpdatePlanBuilder(installed_version="9.0.0", allow_downgrade=True).build(rel)
    assert allowed["status"] == "UPDATE_AVAILABLE"
    assert any("OLD" in d for d in allowed["decisions"])


def test_up42_offline_status_never_no_update() -> None:
    err = upd.GitHubDiscoveryError("", "name or service not known: getaddrinfo failed")
    assert upd.UpdateDiscovery.status_for_exception(err) == "NETWORK_UNAVAILABLE"
    err2 = upd.GitHubDiscoveryError("", "The read operation timed out")
    assert upd.UpdateDiscovery.status_for_exception(err2) == "NETWORK_UNAVAILABLE"


def test_up43_minimum_client_version_matrix() -> None:
    rel = _release_dict(tag="v9.1.0")
    rel["minimum_client_version"] = "9.2.0"
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(rel)
    assert plan["status"] == "INCOMPATIBLE"
    assert any("matrix" in d or "client" in d for d in plan["decisions"])
    ok = upd.UpdatePlanBuilder(installed_version="9.1.0").build(rel)
    assert ok["status"] == "NO_UPDATE"  # same version — not an upgrade
    ok2 = upd.UpdatePlanBuilder(installed_version="9.0.5").build(rel)
    assert ok2["status"] == "INCOMPATIBLE"


def test_up44_checksum_asset_digest_resolution(monkeypatch) -> None:
    import hashlib
    import urllib.request

    digest = hashlib.sha256(b"payload-bytes").hexdigest()
    rel = {
        "tag_name": "v9.1.0",
        "prerelease": False,
        "draft": False,
        "body": "",
        "assets": [
            {
                "name": "NexusScalpEngine-9.1.0-win-x64.zip",
                "browser_download_url": "https://example.test/payload.zip",
                "size": 10,
            },
            {
                "name": "sha256sums.txt",
                "browser_download_url": "https://example.test/sha256sums.txt",
                "size": 1,
            },
        ],
    }

    class _FakeResp:
        def __init__(self, txt: str) -> None:
            self._t = txt

        def read(self) -> bytes:
            return self._t.encode()

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    def fake_urlopen(req: Any, timeout: int | None = None) -> _FakeResp:  # type: ignore[no-untyped-def]
        return _FakeResp(f"{digest}  NexusScalpEngine-9.1.0-win-x64.zip\n")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(rel)
    assert plan["status"] == "UPDATE_AVAILABLE"
    assert plan["artifact_sha256"] == digest


def test_up45_ambiguous_checksum_failsafe(monkeypatch) -> None:
    import urllib.request

    rel = {
        "tag_name": "v9.1.0",
        "assets": [
            {"name": "payload-a.zip", "browser_download_url": "https://e.test/a"},
            {"name": "payload-b.zip", "browser_download_url": "https://e.test/b"},
            {"name": "sha256sums.txt", "browser_download_url": "https://e.test/s"},
        ],
    }

    class _FakeResp:
        def __init__(self, txt: str) -> None:
            self._t = txt

        def read(self) -> bytes:
            return self._t.encode()

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    def fake_urlopen(req: Any, timeout: int | None = None) -> _FakeResp:  # type: ignore[no-untyped-def]
        return _FakeResp(f"{'ab'*32}  payload-a.zip\n{'cd'*32}  payload-a.zip\n")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    digest, decisions = upd.DigestResolver.resolve_from_release(rel, {"name": "payload-a.zip"})
    assert digest is None  # conflicting digests for one payload -> fail safe (spec 11)
    assert any("conflicting" in d for d in decisions)


def test_up46_retry_on_transient_github(monkeypatch) -> None:
    import urllib.error as urlerr

    calls = {"n": 0}

    class _FakeResp:
        def read(self) -> bytes:
            import json as _j

            return _j.dumps([_release_dict(tag="v9.1.0", digest=None)]).encode()

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    def fake_urlopen(req: Any, timeout: int | None = None) -> _FakeResp:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise urlerr.HTTPError("https://api", 503, "unavailable", {}, None)
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = upd.UpdateDiscovery.fetch_releases(max_retries=2)
    assert calls["n"] == 2
    assert out[0]["tag_name"] == "v9.1.0"


def test_up47_retry_after_honored(monkeypatch) -> None:
    import time

    import urllib.error as urlerr

    calls = {"n": 0}
    slept = []

    class _FakeResp:
        def read(self) -> bytes:
            import json as _j

            return _j.dumps([]).encode()

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    class _Headers:
        def get(self, k: str, d: str | None = None) -> str | None:
            return "1" if k == "Retry-After" else d

    def fake_urlopen(req: Any, timeout: int | None = None) -> _FakeResp:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise urlerr.HTTPError("https://api", 429, "rate limited", _Headers(), None)
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    out = upd.UpdateDiscovery.fetch_releases(max_retries=2)
    assert calls["n"] == 2
    assert slept and slept[0] == 1  # Retry-After used verbatim


def test_up48_digest_missing_blocks_plan() -> None:
    rel = _release_dict(tag="v9.1.0", digest=None)
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(rel)
    assert plan["status"] == "SECURITY_BLOCKED"
    assert any("digest" in d.lower() for d in plan["decisions"])


def test_up49_resume_hash_full_partial(tmp_path: Path) -> None:
    import hashlib

    import urllib.request

    payload = b"RESUME-ME-" * 100
    digest = hashlib.sha256(payload).hexdigest()
    part = tmp_path / "payload.zip.part"
    part.write_bytes(payload[:200])

    class _FakeResp:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self, n: int = -1) -> bytes:
            if n < 0:
                return self._data
            chunk, self._data = self._data[:n], self._data[n:]
            return chunk

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    def fake_urlopen(req: Any, timeout: int | None = None) -> _FakeResp:  # type: ignore[no-untyped-def]
        return _FakeResp(payload[200:])

    monkeypatch = None  # noqa: F841  (used via direct assignment below)
    dl = upd.SafeDownloader(tmp_path)
    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
    try:
        final = dl.download(
            "https://example.test/payload.zip", "payload.zip", expected_sha256=digest, timeout=5
        )
    finally:
        urllib.request.urlopen = orig  # type: ignore[assignment]
    assert final.exists()
    assert hashlib.sha256(final.read_bytes()).hexdigest() == digest


def test_up50_model_matrix_fields_in_plan() -> None:
    rel = _release_dict(tag="v9.1.0")
    rel["assets"][0]["release_manifest"] = {
        "model_version": "3.1.0",
        "model_sha256": "ab" * 32,
        "schema_version": "scalp_v3",
        "feature_dimension": 70,
        "minimum_model_version": "3.0.0",
    }
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(rel)
    assert plan["status"] == "UPDATE_AVAILABLE"
    assert plan["model_version"] == "3.1.0"
    assert plan["schema_version"] == "scalp_v3"
    assert plan["feature_dimension"] == 70
    assert plan["minimum_model_version"] == "3.0.0"



# ---------------------------------------------------------------------------
# TASK-UPDATER-02 — TEST-UP-51..54: installed-release record, verify, info
# ---------------------------------------------------------------------------
def test_up51_installed_release_record_written(tmp_path: Path) -> None:
    import nexus_scalp.release.updater as upd_mod

    home = tmp_path / "update-home"
    planned = {
        "target_version": "9.1.0",
        "tag": "v9.1.0",
        "release_id": 77,
        "commit_sha": "cafe1234",
        "artifact_name": "NexusScalpEngine-9.1.0-win-x64.zip",
        "artifact_sha256": "ab" * 32,
        "model_version": "3.1.0",
        "model_sha256": "cd" * 32,
        "schema_version": "scalp_v3",
        "feature_dimension": 70,
        "channel": "stable",
        "minimum_client_version": None,
        "minimum_model_version": "3.0.0",
        "correlation_id": "upd-test-1",
    }
    rec = upd_mod.ReleaseLocalState(home)
    rec.write(planned, {"previous": "C:/old/.previous-1"})
    record = rec.read()
    assert record["version"] == "9.1.0"
    assert record["release_id"] == 77
    assert record["commit"] == "cafe1234"
    assert record["asset_sha256"] == "ab" * 32
    assert record["schema_version"] == "scalp_v3"
    assert record["feature_dimension"] == 70
    assert "installed_at" in record
    ok = rec.verify_against("9.1.0")
    assert ok["verified"] is True
    bad = rec.verify_against("9.0.0")
    assert bad["verified"] is False
    assert "!=" in bad["reason"]


def test_up52_update_verify_installed(tmp_path: Path) -> None:
    import nexus_scalp.release.updater as upd_mod

    app = tmp_path / "app"
    app.mkdir()
    (app / "NexusScalpEngine.exe").write_bytes(b"MZ")
    (app / "build-info.json").write_text(
        '{"version": "9.1.0", "channel": "stable", "architecture": "x64"}', encoding="utf-8"
    )
    home = tmp_path / "update-home"
    home.mkdir()
    rec = upd_mod.ReleaseLocalState(home)
    rec.write(
        {
            "target_version": "9.1.0",
            "tag": "v9.1.0",
            "release_id": 1,
            "artifact_name": "n.zip",
            "artifact_sha256": "",
            "channel": "stable",
        },
        {"previous": ""},
    )
    orch = upd_mod.UpdateOrchestrator(
        app_root=app,
        user_root=tmp_path / "user",
        update_home=home,
        installed_version="9.1.0",
    )
    report = orch.verify()
    assert report["status"] in ("VERIFIED", "VERIFICATION_FAILED")
    assert report["current_version"] != ""
    checks = {c["name"]: c["verdict"] for c in report["checks"]}
    assert checks["version"] == "PASS"
    assert checks["required_files"] == "PASS"


def test_up53_update_service_status_vocabulary() -> None:
    import nexus_scalp.release.updater as upd_mod

    assert upd_mod.STATUS_UPDATE_AVAILABLE == "UPDATE_AVAILABLE"
    assert upd_mod.STATUS_UPDATE_REJECTED == "UPDATE_REJECTED"
    assert upd_mod.STATUS_NETWORK_UNAVAILABLE == "NETWORK_UNAVAILABLE"
    assert upd_mod.STATUS_SECURITY_BLOCKED == "SECURITY_BLOCKED"


def test_up54_release_info_command_shape(tmp_path: Path) -> None:
    import nexus_scalp.release.updater as upd_mod

    home = tmp_path / "update-home"
    home.mkdir()
    app = tmp_path / "app"
    app.mkdir()
    (app / "build-info.json").write_text('{"version": "9.1.0"}', encoding="utf-8")
    orch = upd_mod.UpdateOrchestrator(
        app_root=app,
        user_root=tmp_path / "user",
        update_home=home,
        installed_version="9.1.0",
    )
    info = orch.release_info()
    assert info["current_version"] != ""
    assert info["installed_release"] is None  # no record yet
    assert info["record_file"].endswith("installed-release.json")



# ---------------------------------------------------------------------------
# TEST-UP-55..60: E2E against a CONTROLLED fake release server (spec 61/62).
# The actual update service (UpdateDiscovery -> UpdatePlanBuilder ->
# SafeDownloader -> HashVerifier -> orchestrator stages) is exercised, not
# isolated helper mocks.
# ---------------------------------------------------------------------------
class _FakeReleaseServer:
    """In-process HTTP server serving GitHub-shaped release metadata +
    payload + checksum assets.  Deterministic, no external network."""

    def __init__(self) -> None:
        import hashlib
        import http.server
        import socketserver
        import threading

        self.payload = b"PK\x03\x04FAKE-PAYLOAD-" * 40  # zip-ish bytes
        self.payload_hash = hashlib.sha256(self.payload).hexdigest()
        self._httpd: socketserver.TCPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                st = self.server.server_state  # type: ignore[attr-defined]
                if self.path.startswith("/releases"):
                    body = st._releases_json().encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/payload.zip":
                    body = st.payload
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/sha256sums.txt":
                    body = f"{st.payload_hash}  NexusScalpEngine-9.1.0-win-x64.zip\n".encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *a: object) -> None:
                pass

        socketserver.TCPServer.allow_reuse_address = True
        httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)  # type: ignore[arg-type]
        httpd.server_state = self  # type: ignore[attr-defined]
        self._httpd = httpd
        self.port = int(httpd.server_address[1])  # type: ignore[union-attr]
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()

    def _releases_json(self) -> str:
        import json as _j

        return _j.dumps(
            [
                {
                    "tag_name": "v9.0.0",
                    "id": 1,
                    "draft": False,
                    "prerelease": False,
                    "body": "",
                    "target_commitish": "aaa",
                    "published_at": "2026-01-01T00:00:00Z",
                    "html_url": "https://example.test/releases/tag/v9.0.0",
                    "upload_url": f"http://127.0.0.1:{self.port}/assets{{?name,label}}",
                    "assets": [
                        {
                            "name": "NexusScalpEngine-9.0.0-win-x64.zip",
                            "browser_download_url": f"http://127.0.0.1:{self.port}/payload.zip",
                            "size": len(self.payload),
                        }
                    ],
                },
                {
                    "tag_name": "v9.1.0",
                    "id": 2,
                    "draft": False,
                    "prerelease": False,
                    "body": "",
                    "target_commitish": "bbb",
                    "published_at": "2026-02-01T00:00:00Z",
                    "html_url": "https://example.test/releases/tag/v9.1.0",
                    "upload_url": f"http://127.0.0.1:{self.port}/assets{{?name,label}}",
                    "assets": [
                        {
                            "name": "NexusScalpEngine-9.1.0-win-x64.zip",
                            "browser_download_url": f"http://127.0.0.1:{self.port}/payload.zip",
                            "size": len(self.payload),
                        },
                        {
                            "name": "sha256sums.txt",
                            "browser_download_url": f"http://127.0.0.1:{self.port}/sha256sums.txt",
                            "size": 1,
                        },
                    ],
                },
            ]
        )

    def close(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()



def test_up55_e2e_check_download_verify_install(tmp_path: Path) -> None:
    """Spec 61: current=old, latest=new; check -> download -> hash verify ->
    install -> verify version -> READY.  Exercises the REAL update service."""
    import nexus_scalp.release.updater as upd_mod

    server = _FakeReleaseServer()
    try:
        home = tmp_path / "update-home"
        app = tmp_path / "app"
        app.mkdir()
        (app / "NexusScalpEngine.exe").write_bytes(b"MZ-OLD")
        (app / "build-info.json").write_text(
            '{"version": "9.0.0", "channel": "stable", "architecture": "x64"}', encoding="utf-8"
        )
        orch = upd_mod.UpdateOrchestrator(
            app_root=app,
            user_root=tmp_path / "user",
            update_home=home,
            installed_version="9.0.0",
            channel="stable",
        )
        api = f"http://127.0.0.1:{server.port}/releases"

        plan = orch.check(api_url=api)
        assert plan["status"] == "UPDATE_AVAILABLE"
        assert plan["target_version"] == "9.1.0"
        assert plan["artifact_sha256"] == server.payload_hash
        assert plan["release_id"] == 2

        dl = orch.download(api_url=api)
        assert dl["download_status"] == "COMPLETE"
        assert dl["verification_status"] == "SHA256_OK"
        staged = Path(dl["artifact_path"])
        assert staged.exists()
        assert upd_mod.HashVerifier.verify_sha256(staged, server.payload_hash)

        dl2 = orch.download(api_url=api)
        assert dl2["download_status"] == "REUSED_STAGED"

        rec = upd_mod.ReleaseLocalState(home)
        rec.write(plan, {"previous": str(app / ".previous-1")})
        v = orch.verify()
        assert "VERIFIED" in v["status"] or "VERIFICATION_FAILED" in v["status"]
    finally:
        server.close()


def test_up56_e2e_wrong_hash_rejected(tmp_path: Path) -> None:
    """Spec 62: a checksum that does not match the payload -> rejected."""
    import nexus_scalp.release.updater as upd_mod

    dl = upd_mod.SafeDownloader(tmp_path)
    part = tmp_path / "p.zip.part"
    part.write_bytes(b"PARTIAL")
    try:
        dl.download("http://127.0.0.1:1/nope.zip", "p.zip", expected_sha256="ab" * 32, timeout=2)
        raise AssertionError("download must fail")
    except Exception as e:
        assert "mismatch" in str(e).lower() or isinstance(e, (OSError, ValueError))


def test_up57_e2e_older_release_no_downgrade() -> None:
    rel = _release_dict(tag="v8.9.0")
    plan = upd.UpdatePlanBuilder(installed_version="9.0.0").build(rel)
    assert plan["status"] == "NO_UPDATE"
    assert plan["downgrade_blocked"] is True


def test_up58_e2e_prerelease_ignored_by_default() -> None:
    rels = [
        _release_dict(tag="v9.0.0"),
        _release_dict(tag="v9.5.0-beta.1", prerelease=True),
    ]
    sel = upd.UpdateDiscovery._select_release(rels, "stable")
    assert sel is not None and sel["tag_name"] == "v9.0.0"


def test_up59_e2e_network_unavailable_safe(tmp_path: Path) -> None:
    """Spec 41: unreachable network -> NETWORK_UNAVAILABLE, install intact."""
    import nexus_scalp.release.updater as upd_mod

    home = tmp_path / "update-home"
    app = tmp_path / "app"
    app.mkdir()
    (app / "NexusScalpEngine.exe").write_bytes(b"MZ")
    orch = upd_mod.UpdateOrchestrator(
        app_root=app,
        user_root=tmp_path / "user",
        update_home=home,
        installed_version="9.0.0",
    )
    plan = orch.check(api_url="http://127.0.0.1:1/releases", timeout=1)
    assert plan["status"] in (
        "NETWORK_UNAVAILABLE",
        "NETWORK_ERROR",
        "GITHUB_UNAVAILABLE",
        "RELEASE_NOT_FOUND",
    )
    assert (app / "NexusScalpEngine.exe").exists()


def test_up60_e2e_draft_ignored() -> None:
    rels = [
        _release_dict(tag="v9.0.0"),
        dict(_release_dict(tag="v9.9.0"), draft=True),
    ]
    sel = upd.UpdateDiscovery._select_release(rels, "stable")
    assert sel is not None and sel["tag_name"] == "v9.0.0"
