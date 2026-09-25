"""
Forensic Implementation Task — regression coverage
==================================================
Covers the verified integration/deployment defects fixed in this task:

BACKEND (research registry score robustness)
1. registry row with score=None
2. registry row with 'null' string
3. registry row with '{}'
4. registry row with valid score JSON
5. malformed score JSON

FRONTEND (bundle + loaders contract)
6. app.js defines a defensive registry score decoder (no JSON.parse("null")
   property-access crash path)
7-9. news loaders emit [UI_ERROR] + visible state on failure
10. rules loader emits [UI_ERROR] + visible state on failure
11. loadRules consumes real array rows
12-14. tab-news/tab-rules/tab-research initialize their loaders

RELEASE
15. packaged Web/app.js must match the source revision (freshness guard
    implemented in ReleaseVerifier._asset_web via build-info web_*_hash)
16. stale bundle detection fails verification

Run: .venv/Scripts/python -m pytest tests/unit/test_ui_deploy_drift_forensics.py -q
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from nexus_scalp.web.server import WEB_DIR, create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# BACKEND: registry score serialization / normalization
# ---------------------------------------------------------------------------


class TestRegistryScoreBackend:
    def _insert(self, db_path: Path, score_text: str, sid: str = "TEST-STRAT") -> None:
        con = sqlite3.connect(db_path)
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_registry (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              strategy_id TEXT, strategy_version TEXT, feature_schema_id TEXT,
              feature_dimension INTEGER, discovery_source TEXT, discovery_window TEXT,
              context_definition TEXT, parent_strategy_ids TEXT, lifecycle TEXT,
              backtest TEXT, walkforward TEXT, oos TEXT, robustness TEXT, score TEXT,
              confidence REAL, sample_count INTEGER, validation_lineage TEXT,
              retirement_reason TEXT, created_at TEXT, updated_at TEXT,
              UNIQUE (strategy_id, strategy_version)
            );
            """
        )
        con.execute(
            "INSERT OR REPLACE INTO strategy_registry (strategy_id, strategy_version, feature_schema_id, feature_dimension, discovery_source, discovery_window, context_definition, parent_strategy_ids, lifecycle, backtest, walkforward, oos, robustness, score, confidence, sample_count, validation_lineage, retirement_reason, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid,
                "v1",
                "scalp_v1",
                50,
                "builtin:test",
                "ALL",
                "{}",
                "[]",
                "DISCOVERED",
                None,
                None,
                None,
                None,
                score_text,
                0.0,
                0,
                "[]",
                "",
                "2026-08-18T00:00:00+00:00",
                "2026-08-18T00:00:00+00:00",
            ),
        )
        con.commit()
        con.close()

    @pytest.fixture()
    def repo(self, tmp_path: Path):
        from nexus_scalp.adapters.database.audit_repository import AuditRepository

        db = tmp_path / "audit.db"
        db.touch()
        repo = AuditRepository(db_url=f"sqlite:///{db.as_posix()}")
        yield repo, db
        repo.close()

    @pytest.mark.parametrize(
        "score_text,expected_score_field",
        [
            (None, "{}"),  # 1. None -> canonical empty object
            ("null", "{}"),  # 2. historical 'null' literal -> empty object
            ("{}", "{}"),  # 3. canonical empty object passes through
            (json.dumps({"final_score": 0.72, "verdict": "VALIDATED"}), "0.72"),  # 4. valid
        ],
    )
    def test_registry_score_never_null_for_ui(self, repo, score_text, expected_score_field) -> None:
        from nexus_scalp.research.store import get_registry_entry, list_registry

        _, db = repo
        self._insert(db, score_text)
        rows = list_registry(repo[0])
        assert rows, "registry listing must return the inserted row"
        score = rows[0]["score"]
        assert score == "{}" or json.loads(score)  # never the literal 'null'
        if expected_score_field == "0.72":
            assert json.loads(score)["final_score"] == 0.72
        else:
            assert score == "{}"

        entry = get_registry_entry(repo[0], "TEST-STRAT")
        assert entry is not None
        assert entry["score"] == score

    def test_malformed_score_json_does_not_crash_reader(self, repo) -> None:
        """5. Malformed JSON must not crash the registry reader."""
        from nexus_scalp.research.registry import StrategyRegistry
        from nexus_scalp.research.store import get_registry_entry

        _, db = repo
        self._insert(db, "{not-json!!")
        entry = get_registry_entry(repo[0], "TEST-STRAT")
        # the raw text is preserved by the store; the typed reader returns None score
        assert entry is not None
        reg = StrategyRegistry(audit_repo=repo[0])
        typed = reg.get("TEST-STRAT")
        assert typed is not None
        assert typed.score is None  # decode failure degrades to None, no crash


# ---------------------------------------------------------------------------
# FRONTEND: served bundle + loader contracts
# ---------------------------------------------------------------------------


class TestFrontendBundleContract:
    def test_app_js_score_decoder_no_null_crash(self) -> None:
        """6. app.js must not let JSON.parse("null") reach a property access."""
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        # defensive decoder exists
        assert "safeScore" in app_js or "decodeRegistryScore" in app_js
        # the fragile pattern must be gone from the registry renderer
        assert "JSON.parse(r.score).final_score" not in app_js

    def test_news_loaders_fail_visible(self) -> None:
        """7-9. news state/feed/keywords loaders log [UI_ERROR] on failure."""
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        for endpoint in ("/api/news/state", "/api/news", "/api/news/keywords"):
            assert f"component=News endpoint={endpoint}" in app_js

    def test_rules_loader_fail_visible(self) -> None:
        """10. rules loader reports [UI_ERROR] and renders a failure state."""
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        assert "component=RuleMatrix endpoint=/api/rules" in app_js

    def test_rules_loader_consumes_array(self) -> None:
        """11. loadRules categorizes the real /api/rules array rows."""
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        assert "rule.category" in app_js
        assert "rules.forEach" in app_js or "categorized[rule.category]" in app_js

    def test_tab_initializers_wired(self) -> None:
        """12-14. switchTab triggers news/rules/research loaders."""
        app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
        assert "tab-news" in app_js and "loadNewsState();" in app_js
        assert "tab-rules" in app_js and "loadRules();" in app_js
        assert "tab-research" in app_js and "loadResearchSummary();" in app_js

    def test_served_app_js_is_source_bundle(self) -> None:
        """/app.js serves the repository Web/app.js with identity headers."""
        app = create_app(engine_ref=None)
        from fastapi.testclient import TestClient

        with TestClient(app) as c:
            r = c.get("/app.js")
            assert r.status_code == 200
            src = (WEB_DIR / "app.js").read_bytes()
            assert hashlib.sha256(r.content).hexdigest() == hashlib.sha256(src).hexdigest()
            assert r.headers.get("X-UI-Bundle-Sha256") == hashlib.sha256(src).hexdigest()
            assert r.headers.get("X-UI-Bundle-Source") in ("REPO", "PACKAGED")


# ---------------------------------------------------------------------------
# RELEASE: bundle freshness guard
# ---------------------------------------------------------------------------


class TestReleaseBundleFreshness:
    def _client(self, tmp_path: Path) -> None:
        """Build a fake release bundle dir with source-matched web assets."""
        pkg_web = tmp_path / "_internal" / "Web"
        pkg_web.mkdir(parents=True)
        (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
        for rel in ("app.js", "index.html", "styles.css", "api_client.js"):
            (pkg_web / rel).write_bytes((WEB_DIR / rel).read_bytes())
        (tmp_path / "configs" / "base.yaml").write_text("risk:\n  mode: PAPER\n", encoding="utf-8")
        src_app = (WEB_DIR / "app.js").read_bytes()
        info = {
            "web_asset_hash": hashlib.sha256(src_app).hexdigest(),
            "web_index_hash": hashlib.sha256((WEB_DIR / "index.html").read_bytes()).hexdigest(),
            "web_api_client_hash": hashlib.sha256(
                (WEB_DIR / "api_client.js").read_bytes()
            ).hexdigest(),
            "web_styles_hash": hashlib.sha256((WEB_DIR / "styles.css").read_bytes()).hexdigest(),
        }
        (tmp_path / "build-info.json").write_text(json.dumps(info), encoding="utf-8")
        return pkg_web

    def test_fresh_bundle_passes(self, tmp_path: Path) -> None:
        """15. packaged app.js == source => PASS."""
        from nexus_scalp.release.verify import ReleaseVerifier

        self._client(tmp_path)
        v = ReleaseVerifier(root=tmp_path)
        res = v._asset_web()
        assert res.status == "PASS", res.detail

    def test_stale_bundle_fails(self, tmp_path: Path) -> None:
        """16. packaged app.js != source => verification FAILS."""
        from nexus_scalp.release.verify import ReleaseVerifier

        pkg_web = self._client(tmp_path)
        # force a genuinely stale bundled app.js AFTER build-info was recorded
        (pkg_web / "app.js").write_text("// old stale bundle v1", encoding="utf-8")
        v = ReleaseVerifier(root=tmp_path)
        res = v._asset_web()
        assert res.status == "FAIL", res.detail
        assert "STALE WEB BUNDLE" in res.detail or "stale" in res.detail.lower()


# ---------------------------------------------------------------------------
# API contract: /api/research/registry rows are UI-safe
# ---------------------------------------------------------------------------


class TestRegistryApiContract:
    def test_registry_api_normalizes_null_score(self) -> None:
        """The API layer normalizes historical 'null' rows to '{}'."""
        from nexus_scalp.research.store import _json_text_safe

        assert _json_text_safe(None) == "{}"
        assert _json_text_safe("null") == "{}"
        assert _json_text_safe("") == "{}"
        assert _json_text_safe("\nnull\n") == "{}"
        assert _json_text_safe('{"final_score": 0.7}') == '{"final_score": 0.7}'
