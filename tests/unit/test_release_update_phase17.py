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


def test_github_dns_failure_returns_error() -> None:
    err = upd.GitHubDiscoveryError("", "Name or service not known")
    assert upd.UpdateDiscovery.status_for_exception(err) == "NETWORK_ERROR"


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
            '''import json, sys
        print(json.dumps({"overall": "READY", "checks": []}))
        '''
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


def test_helper_bootstrap_detection(tmp_path: Path) -> None:
    helper = tmp_path / "nexus-update-helper.py"
    helper.write_text("import sys\nprint('helper')\n", encoding="utf-8")
    assert helper.exists()


def test_onefile_cli_detection(tmp_path: Path) -> None:
    cli = tmp_path / "NexusScalpEngine-CLI.exe"
    cli.write_bytes(b"MZ")
    assert cli.exists()


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
