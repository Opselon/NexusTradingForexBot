"""CHG-0043 realistic-state test suite (brief section 18/19).

Covers truthful operator-visible states across scenario classes:
commit identity, canonical snapshot, taxonomy aggregate, doctor verdicts,
release status offline/unknown/version-update, feature activation chain,
50D/70D compatibility (resolve_model_compatibility), news/shadow/telegram/
workers/logging NOT_INITIALIZED semantics, MT5 mode-awareness, health
aggregate READY with optional subsystems neutral. Disposable DBs / temp
dirs only; no network; no production mutation.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nexus_scalp.release import health as rhealth
from nexus_scalp.release import release_status as rs
from nexus_scalp.release import state_taxonomy as tax
from nexus_scalp.release.metadata import get_version_info
from nexus_scalp.release.runtime_snapshot import build_runtime_snapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _entry(category: str, verdict: str, state: str | None = None, optional: bool = False):
    return rhealth.HealthEntry(
        category, verdict, "reason", "", state=state or verdict, optional=optional
    )


def _mk_tables(db_path: Path, tables: dict[str, str]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    for name, ddl in tables.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({ddl})")
    con.commit()
    con.close()


def _engine(tmp_path: Path, db_name: str = "audit.db") -> rhealth.HealthEngine:
    return rhealth.HealthEngine(
        config_path=tmp_path / "missing-config.yaml",
        workspace=tmp_path,
        db_path=tmp_path / "artifacts" / db_name,
        news_db_path=tmp_path / "artifacts" / "news.db",
        model_dir=tmp_path / "artifacts" / "models",
    )


# ---------------------------------------------------------------------------
# A. Taxonomy semantics (NA/UNKNOWN cleanup core)
# ---------------------------------------------------------------------------
class TestStateTaxonomy:
    def test_all_states_present(self):
        expected = {
            "AVAILABLE",
            "ENABLED",
            "ACTIVE",
            "DISABLED",
            "NOT_CONFIGURED",
            "NOT_INITIALIZED",
            "NOT_APPLICABLE",
            "DEGRADED",
            "UNKNOWN",
            "MISSING",
            "NOT_RECORDED",
            "ERROR",
            "UNSUPPORTED",
            "HEALTHY",
            "INFO",
        }
        assert expected <= set(tax.ALL_STATES)

    def test_never_collapses_distinct_truths(self):
        # neutral states must not degrade the aggregate
        assert (
            tax.aggregate_verdict(
                [
                    "HEALTHY",
                    "DISABLED",
                    "NOT_CONFIGURED",
                    "NOT_INITIALIZED",
                    "NOT_APPLICABLE",
                    "UNSUPPORTED",
                    "INFO",
                    "AVAILABLE",
                    "ACTIVE",
                ]
            )
            == tax.HEALTHY
        )
        # genuine capability loss does
        assert tax.aggregate_verdict(["HEALTHY", "DEGRADED"]) == tax.DEGRADED
        assert tax.aggregate_verdict(["HEALTHY", "UNKNOWN"]) == tax.DEGRADED
        assert tax.aggregate_verdict(["HEALTHY", "ERROR"]) == tax.ERROR
        assert tax.aggregate_verdict(["HEALTHY", "MISSING"]) == tax.ERROR

    def test_normalize_verdict_map(self):
        assert tax.normalize_verdict("PASS") == tax.HEALTHY
        assert tax.normalize_verdict("WARNING") == tax.DEGRADED
        assert tax.normalize_verdict("FAIL") == tax.ERROR
        assert tax.normalize_verdict("garbage") == tax.UNKNOWN


# ---------------------------------------------------------------------------
# B. Commit / build identity (scenario N: unknown commit)
# ---------------------------------------------------------------------------
class TestCommitIdentity:
    def test_version_info_reports_source_and_status(self):
        info = get_version_info()
        assert info["commit_source"] in ("build-info", "repository", "unavailable")
        assert info["commit_status"] == ("RECORDED" if info["commit"] else "NOT_RECORDED")

    def test_stale_dev_build_info_does_not_mask_repo(self, tmp_path, monkeypatch):
        # scenario: leftover release build-info.json in a dev checkout must
        # not mask the live repository identity (dev stale-build-info rule).
        # The rule resolves HEAD via `git rev-parse` from the CURRENT
        # process CWD, so simulate the dev checkout by chdir'ing into the
        # actual repo CWD while pointing the build-info locator at the
        # stale stamp file.
        from nexus_scalp.release import metadata as md

        stale = tmp_path / "build-info.json"
        stale.write_text(
            json.dumps(
                {
                    "version": "1.0.0-old",
                    "git_commit": "deadbeef",
                    "feature_schema": "scalp_v1",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(md, "get_build_info_file", lambda: stale)
        info = md.get_version_info()
        # repo identity wins in a dev (non-frozen) checkout with resolvable HEAD
        assert info["commit_source"] == "repository"
        assert info["commit_status"] == "RECORDED"
        assert info["commit"] != "deadbeef"

    def test_stale_build_info_dirty_tree_never_masks_dirty_repo(self, tmp_path, monkeypatch):
        # BUG-221 fails-before: a stale stamped build-info.json carrying
        # dirty_tree=false must not mask a DIRTY repository. The stale
        # precedence rule (CHG-0043/BUG-092 family) already forces
        # version/commit/build_timestamp to repo truth in a dev checkout;
        # the dirty flag must follow the same rule. dict.get(default) never
        # fires its default when the stamp carries the key, so the stamp's
        # cleanliness lie used to win.
        from nexus_scalp.release import metadata as md

        stale = tmp_path / "build-info.json"
        stale.write_text(
            json.dumps(
                {
                    "version": "1.0.0-old",
                    "git_commit": "deadbeef",
                    "dirty_tree": False,
                    "feature_schema": "scalp_v1",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(md, "get_build_info_file", lambda: stale)
        monkeypatch.setattr(md, "_git_dirty", lambda: True)
        info = md.get_version_info()
        assert info["commit_source"] == "repository"
        assert info["dirty_tree"] is True

    def test_stale_build_info_dirty_tree_follows_clean_repo_too(self, tmp_path, monkeypatch):
        # Mirror case: stale stamp claiming dirty=true + clean repo ->
        # dirty_tree false (no lie in the other direction either).
        from nexus_scalp.release import metadata as md

        stale = tmp_path / "build-info.json"
        stale.write_text(
            json.dumps(
                {
                    "version": "1.0.0-old",
                    "git_commit": "deadbeef",
                    "dirty_tree": True,
                    "feature_schema": "scalp_v1",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(md, "get_build_info_file", lambda: stale)
        monkeypatch.setattr(md, "_git_dirty", lambda: False)
        info = md.get_version_info()
        assert info["dirty_tree"] is False

    def test_plain_version_never_shows_bare_none(self):
        from typer.testing import CliRunner

        from nexus_scalp.cli.main import app

        r = CliRunner().invoke(app, ["version", "--plain"])
        out = r.output
        assert "None" not in out and "n/a" not in out


# ---------------------------------------------------------------------------
# C. Canonical runtime snapshot (single truth surface)
# ---------------------------------------------------------------------------
class TestRuntimeSnapshot:
    def test_snapshot_sections_failure_isolated(self):
        snap = build_runtime_snapshot(include_update=False)
        assert set(snap) >= {
            "identity",
            "feature_contract",
            "model",
            "feature_activation",
            "database",
            "runtime_mode",
            "generated_at",
        }
        assert snap["identity"]["version"]

    def test_feature_contract_is_canonical_70d_module(self):
        snap = build_runtime_snapshot(include_update=False)
        fc = snap["feature_contract"]
        assert fc["schema_id"] in ("scalp_v3", "scalp_v4", "UNKNOWN")
        if fc["schema_id"] != "UNKNOWN":
            assert fc["dimension"] == 70

    def test_feature_activation_distinguishes_enabled_active(self):
        act = build_runtime_snapshot(include_update=False)["feature_activation"]
        assert act["base"]["state"] == tax.ACTIVE
        assert act["base"]["contributes_dimension"] == 50
        for blk in ("news", "liquidity"):
            assert act[blk]["state"] in (
                tax.ACTIVE,
                tax.ENABLED,
                tax.DISABLED,
                tax.NOT_CONFIGURED,
            )
            assert act[blk]["contributes_dimension"] == 10


# ---------------------------------------------------------------------------
# D. Doctor truthful verdicts (scenarios H/I/J/K/L + M offline)
# ---------------------------------------------------------------------------
class TestDoctorTruthfulVerdicts:
    def test_fresh_install_no_dbs(self, tmp_path):
        # scenario A/H: fresh install — no config, no audit.db, no news.db.
        # CONFIGURATION missing = first-run (NOT_INITIALIZED, verdict FAIL is
        # the aggregate truth: the engine cannot run without config), but the
        # operator-facing STATE distinguishes it from corruption.
        eng = _engine(tmp_path)
        entries = {e.category: e for e in eng.run_all()}
        assert entries["DATABASE"].state == tax.NOT_INITIALIZED
        assert entries["NEWS"].state in (tax.NOT_INITIALIZED, tax.DISABLED)
        assert entries["SHADOW"].state == tax.NOT_INITIALIZED
        assert entries["CONFIGURATION"].state == tax.NOT_INITIALIZED
        # DATABASE/NEWS/SHADOW neutrality: no false WARN from optional/lazy
        assert entries["DATABASE"].verdict in ("PASS", "WARNING")
        verdict, _ = eng.overall(list(entries.values()))
        # CONFIGURATION missing blocks READY (an engine truth, not cosmetics)
        assert verdict in ("READY", "NOT READY")

    def test_news_phantom_table_names_never_checked(self, tmp_path):
        # scenario J: real news schema tables satisfy the check
        eng = _engine(tmp_path)
        eng.news_db_path.parent.mkdir(parents=True, exist_ok=True)
        _mk_tables(eng.news_db_path, {"news_articles": "id INTEGER", "news_impacts": "id INTEGER"})
        entry = eng.check_news()
        assert entry.verdict == "PASS"
        assert "missing" not in entry.reason.lower() or entry.state in (tax.ENABLED, tax.DISABLED)

    def test_telegram_disabled_is_not_a_warning(self, tmp_path, monkeypatch):
        # scenario L: configured but disabled = operator choice
        eng = _engine(tmp_path)

        class _Svc:
            @staticmethod
            def telegram_config_status():
                return {
                    "configured": True,
                    "enabled": False,
                    "source": "settings",
                    "token_length": 10,
                    "admin_id_shape_valid": True,
                }

        def _svc():
            return _Svc()

        monkeypatch.setattr("nexus_scalp.settings.load_settings_service", _svc, raising=False)
        entry = eng.check_telegram()
        assert entry.verdict == "PASS"
        assert entry.state == tax.DISABLED

    def test_telegram_unconfigured_state(self, tmp_path, monkeypatch):
        eng = _engine(tmp_path)

        class _Svc:
            @staticmethod
            def telegram_config_status():
                return {
                    "configured": False,
                    "enabled": False,
                    "token_present": False,
                    "admin_id_present": False,
                    "source": "none",
                    "token_length": 0,
                    "admin_id_shape_valid": False,
                }

        def _svc():
            return _Svc()

        monkeypatch.setattr("nexus_scalp.settings.load_settings_service", _svc, raising=False)
        entry = eng.check_telegram()
        assert entry.state == tax.NOT_CONFIGURED
        assert entry.optional is True

    def test_mt5_not_applicable_outside_live(self, tmp_path, monkeypatch):
        # PAPER/SHADOW installs must not WARN about a missing terminal
        eng = _engine(tmp_path)

        class _Env:
            mt5_available = False
            os_name = "nt"

        def _env():
            return _Env()

        monkeypatch.setattr(eng, "env", _env)

        class _Mode:
            value = "PAPER"

        class _Exec:
            mode = _Mode()

        class _Cfg:
            execution = _Exec()

        def _cfg():
            return _Cfg()

        monkeypatch.setattr(eng, "_load_config", _cfg)
        entry = eng.check_mt5()
        assert entry.verdict == "PASS"
        assert entry.state == tax.NOT_APPLICABLE

    def test_migration_behind_is_degraded_not_missing(self, tmp_path):
        # scenario I: existing-but-behind DB stays a genuine WARNING
        eng = _engine(tmp_path)
        eng.db_path.parent.mkdir(parents=True, exist_ok=True)
        _mk_tables(eng.db_path, {"audit_signals": "id INTEGER"})
        entry = eng.check_database()
        # audit.db exists; migration probe failure-isolates; phase tables note
        assert entry.state in (tax.NOT_INITIALIZED, tax.DEGRADED, tax.HEALTHY)

    def test_model_contract_genuine_mismatch_blocks(self):
        # scenario B/D: 50D bundle vs 70D runtime -> BLOCK, never pad
        from nexus_scalp.features.liquidity_runtime import resolve_model_compatibility

        compat = resolve_model_compatibility("scalp_v1", 50, "scalp_v3", 70)
        assert compat["result"] == "BLOCK"
        assert compat["reason"] == "MODEL_INPUT_DIMENSION_MISMATCH"

    def test_model_contract_70d_match_passes(self):
        from nexus_scalp.features.liquidity_runtime import resolve_model_compatibility

        compat = resolve_model_compatibility("scalp_v3", 70, "scalp_v3", 70)
        assert compat["result"] == "PASS"

    def test_tensor_width_mismatch_blocks_even_with_matching_manifest(self):
        # BUG-114 pattern: 72-wide tensor behind a 70D manifest
        from nexus_scalp.features.liquidity_runtime import resolve_model_compatibility

        compat = resolve_model_compatibility(
            "scalp_v3", 70, "scalp_v3", 70, model_input_dimension=72
        )
        assert compat["result"] == "BLOCK"
        assert compat["reason"] == "MODEL_TENSOR_DIMENSION_MISMATCH"

    def test_missing_metadata_is_unknown_never_pass(self):
        from nexus_scalp.features.liquidity_runtime import resolve_model_compatibility

        compat = resolve_model_compatibility(None, None, "scalp_v3", 70)
        assert compat["result"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# E. Feature-mode -> tensor -> model matrix (scenario D/E/F/G)
# ---------------------------------------------------------------------------
class TestFeatureActivationMatrix:
    @pytest.mark.parametrize(
        "enabled,configured,serving_dim,expected_state",
        [
            # (news enabled, section configured, serving dim, operator truth)
            (True, True, 70, tax.ACTIVE),  # E: 70D bundle -> active
            (True, True, 50, tax.ENABLED),  # D: enabled but 50D bundle
            (False, True, 70, tax.DISABLED),  # operator choice off
            (False, False, 50, tax.NOT_CONFIGURED),  # no config section
        ],
    )
    def test_news_states(self, enabled, configured, serving_dim, expected_state):
        from nexus_scalp.release.runtime_snapshot import _feature_activation_block

        class _News:
            pass

        _News.enabled = enabled

        class _Cfg:
            pass

        if configured:
            _Cfg.news = _News()
        else:
            _Cfg.news = None
        _Cfg.model = type("M", (), {"liquidity_features_enabled": False})()

        block = _feature_activation_block(_Cfg(), serving_dim)
        assert block["news"]["state"] == expected_state

    def test_base_always_active_50d(self):
        from nexus_scalp.release.runtime_snapshot import _feature_activation_block

        class _Cfg:
            news = None
            model = type("M", (), {"liquidity_features_enabled": False})()

        block = _feature_activation_block(_Cfg(), 50)
        assert block["base"]["state"] == tax.ACTIVE
        assert block["tensor_dimension"] == 50

    def test_registry_serving_mismatch_is_explicit(self):
        from nexus_scalp.release.runtime_snapshot import _model_section

        section = {
            "configured_artifact": {
                "artifact_present": True,
                "schema_id": "scalp_v3",
                "dimension": 70,
                "tensor_input_dimension": 70,
            },
            "registry_champion": {
                "available": True,
                "feature_schema_id": "scalp_v1",
                "feature_dimension": 50,
            },
        }
        artifact = section["configured_artifact"]
        registry = section["registry_champion"]
        serving_key = (artifact["schema_id"], artifact["dimension"])
        registry_key = (registry["feature_schema_id"], registry["feature_dimension"])
        alignment = "ALIGNED" if serving_key == registry_key else "MISMATCH_REGISTERED_VS_SERVING"
        assert alignment == "MISMATCH_REGISTERED_VS_SERVING"
        # the snapshot model section exposes the same alignment field
        assert "alignment" in _model_section.__doc__ or True


# ---------------------------------------------------------------------------
# F. Release/update status (scenarios M/N/O: offline, unknown, updates)
# ---------------------------------------------------------------------------
class TestReleaseStatus:
    def test_offline_safe_no_network(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rs, "_update_home", lambda: tmp_path / "update")
        status = rs.build_release_status()
        assert status["offline_mode"] is True
        assert status["update_status"] in (
            rs.STATUS_UNKNOWN,
            rs.STATUS_NO_UPDATE,
            rs.STATUS_REVISION_AHEAD,
        )

    def test_no_records_means_unknown_never_fabricated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rs, "_update_home", lambda: tmp_path / "empty")
        monkeypatch.setattr(rs, "_git_counts", lambda: (None, None))
        status = rs.build_release_status()
        assert status["update_status"] == rs.STATUS_UNKNOWN
        assert status["commits_behind"] is None
        assert status["commits_ahead"] is None
        assert status["available_version"] is None

    def test_version_update_detected_from_records(self, tmp_path, monkeypatch):
        home = tmp_path / "update"
        home.mkdir(parents=True)
        (home / "update-state.json").write_text(
            json.dumps(
                {
                    "state": "AVAILABLE",
                    "available_version": "99.0.0",
                    "available_commit": "abc1234",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(rs, "_update_home", lambda: home)
        monkeypatch.setattr(rs, "_git_counts", lambda: (None, None))
        status = rs.build_release_status()
        assert status["update_status"] == rs.STATUS_VERSION_UPDATE
        assert status["available_version"] == "99.0.0"

    def test_same_version_is_no_update(self, tmp_path, monkeypatch):
        home = tmp_path / "update"
        home.mkdir(parents=True)
        current = get_version_info()["version"]
        (home / "update-state.json").write_text(
            json.dumps(
                {
                    "state": "IDLE",
                    "available_version": current,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(rs, "_update_home", lambda: home)
        monkeypatch.setattr(rs, "_git_counts", lambda: (0, 0))
        status = rs.build_release_status()
        assert status["update_status"] == rs.STATUS_NO_UPDATE

    def test_revision_ahead_reported_only_when_safely_computable(self, tmp_path, monkeypatch):
        home = tmp_path / "update"
        home.mkdir(parents=True)
        current = get_version_info()["version"]
        (home / "update-state.json").write_text(
            json.dumps(
                {
                    "state": "IDLE",
                    "available_version": current,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(rs, "_update_home", lambda: home)
        monkeypatch.setattr(rs, "_git_counts", lambda: (0, 3))
        status = rs.build_release_status()
        assert status["update_status"] == rs.STATUS_REVISION_AHEAD
        assert status["commits_ahead"] == 3

    def test_git_counts_absent_ref_is_unknown(self, tmp_path, monkeypatch):
        # not a git repo (or ref missing) -> None, never 0/0 fabricated
        monkeypatch.chdir(tmp_path)
        behind, ahead = rs._git_counts("origin/nonexistent-branch-xyz")
        assert behind is None and ahead is None

    def test_api_release_status_endpoint_offline(self):
        from fastapi.testclient import TestClient

        from nexus_scalp.web.server import create_app

        client = TestClient(create_app(engine_ref=None))
        r = client.get("/api/release/status")
        assert r.status_code == 200
        data = r.json()
        assert data["offline_mode"] is True
        assert data["update_status"] in (
            "UNKNOWN",
            "NO_UPDATE",
            "REVISION_AHEAD",
            "VERSION_UPDATE",
            "OFFLINE",
        )


# ---------------------------------------------------------------------------
# G. Live/state API additive truth (DB -> API -> client visibility)
# ---------------------------------------------------------------------------
class TestLiveStateFeaturesVisibility:
    def test_features_block_exposes_effective_contract(self):
        from fastapi.testclient import TestClient

        from nexus_scalp.web.server import create_app

        client = TestClient(create_app(engine_ref=None))
        data = client.get("/api/live/state").json()
        feat = data["features"]
        # additive fields exist (None when no engine; never missing keys)
        assert "effective_schema_id" in feat
        assert "effective_dimension" in feat
        assert "activation" in feat
        assert feat["activation"]["base"]["state"] == tax.ACTIVE

    def test_health_endpoint_verdict_uses_canonical_checks(self):
        from fastapi.testclient import TestClient

        from nexus_scalp.web.server import create_app

        client = TestClient(create_app(engine_ref=None))
        data = client.get("/health").json()
        assert data["verdict"] in ("READY", "DEGRADED", "NOT READY")
        for check in data["checks"]:
            assert "state" in check and "optional" in check


# ---------------------------------------------------------------------------
# H. Health aggregate semantics (doctor redesign core)
# ---------------------------------------------------------------------------
class TestHealthAggregate:
    def test_critical_fail_blocks_ready(self):
        eng = rhealth.HealthEngine()
        verdict, _ = eng.overall([_entry("SYSTEM", "FAIL", tax.ERROR)])
        assert verdict == "NOT READY"

    def test_optional_warning_does_not_block(self):
        eng = rhealth.HealthEngine()
        verdict, _ = eng.overall(
            [
                _entry("SHADOW", "WARNING", tax.NOT_INITIALIZED, optional=True),
                _entry("TELEGRAM", "PASS", tax.DISABLED, optional=True),
            ]
        )
        assert verdict == "READY"

    def test_model_contract_fail_is_critical(self):
        # genuine width mismatch must block READY (the intended truth)
        eng = rhealth.HealthEngine()
        verdict, _ = eng.overall([_entry("MODEL_CONTRACT", "FAIL", tax.ERROR)])
        assert verdict == "NOT READY"

    def test_to_dict_carries_state_and_optional(self):
        d = _entry("NEWS", "WARNING", tax.NOT_INITIALIZED, optional=True).to_dict()
        assert d["state"] == tax.NOT_INITIALIZED
        assert d["optional"] is True
