# Nexus Scalp Engine — BUG-075 regression tests: research registry null-score
# crash + stale web bundle detection
# =============================================================================
# Verified defects (2026-08-18 forensics):
#   1. StrategyRegistry._json(None) -> "null" persisted into strategy_registry
#      JSON columns; frontend JSON.parse("null") -> null -> .final_score crash.
#   2. The packaged release shipped a STALE Web bundle (old app.js) while the
#      runtime served the current source — no build-time freshness guard.
# These tests lock both regressions: writer emits '{}' for absent values, the
# read path tolerates historical 'null'/'null'/''/malformed values, and the
# release verifier FAILS on a stale web bundle.
# =============================================================================

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1. Writer: _json() must never emit the JSON literal "null"
# ---------------------------------------------------------------------------


def test_json_writer_absent_score_becomes_empty_object() -> None:
    """BUG-075: _json(None) previously produced 'null' which crashed the
    research UI. Absent score/results MUST round-trip to the schema's
    canonical empty object '{}'."""
    from nexus_scalp.research.registry import _json

    assert _json(None) == "{}"
    assert _json({}) == "{}"


def test_json_writer_never_emits_null_literal() -> None:
    from nexus_scalp.research.registry import _json

    assert _json(None) != "null"
    # A value that json.dumps would render as 'null' (e.g. None) is guarded;
    # a plain string 'null' is a value, not absence — keep it unambiguous:
    assert json.loads(_json(None)) is None or _json(None) == "{}"


def test_json_writer_preserves_valid_scores() -> None:
    from nexus_scalp.research.registry import _json

    payload = {"final_score": 0.42, "verdict": "ACCEPT"}
    assert json.loads(_json(payload)) == payload
    assert json.loads(_json({"a": [1, 2]})) == {"a": [1, 2]}


# ---------------------------------------------------------------------------
# 2. Reader: historical 'null'/'null'/''/malformed values never crash
# ---------------------------------------------------------------------------


def test_row_safe_normalizes_null_literals() -> None:
    """BUG-075 read path: rows persisted as 'null' or null must be read back
    as the canonical empty object, so the API never serializes a literal
    'null' into the frontend."""
    from nexus_scalp.research.store import _json_text_safe, _registry_row_safe

    assert _json_text_safe(None) == "{}"
    assert _json_text_safe("null") == "{}"
    assert _json_text_safe("") == "{}"
    assert _json_text_safe('{"final_score": 0.5}') == '{"final_score": 0.5}'

    row = _registry_row_safe(
        {
            "score": "null",
            "backtest": None,
            "lifecycle": "DISCOVERED",
            "context_definition": "null",
        }
    )
    assert row["score"] == "{}"
    assert row["backtest"] == "{}"
    assert row["context_definition"] == "{}"
    assert row["lifecycle"] == "DISCOVERED"  # non-JSON columns untouched


def test_row_safe_keeps_valid_json() -> None:
    from nexus_scalp.research.store import _json_text_safe

    assert _json_text_safe("{}") == "{}"
    assert _json_text_safe("[]") == "[]"
    assert _json_text_safe('{"final_score": 0.9}') == '{"final_score": 0.9}'


def test_registry_from_row_tolerates_null_score() -> None:
    """A full decode of a registry row whose score column is the literal
    'null' must succeed (entry.score is None) — the historical data must
    remain readable."""
    import sqlite3

    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.research.registry import StrategyRegistry

    tmp_db = Path(__import__("tempfile").gettempdir()) / f"bug075_registry_{id(object())}.db"
    repo = AuditRepository(db_url=f"sqlite:///{tmp_db.as_posix()}")
    try:
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO strategy_registry (strategy_id, strategy_version,"
            " feature_schema_id, feature_dimension, discovery_source,"
            " discovery_window, context_definition, parent_strategy_ids,"
            " lifecycle, backtest, walkforward, oos, robustness, score,"
            " confidence, sample_count, validation_lineage, retirement_reason,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "STRAT-NULLSCORE",
                "v1",
                "scalp_v1",
                50,
                "test",
                "ALL",
                "{}",
                "[]",
                "DISCOVERED",
                "null",
                "null",
                "null",
                "null",
                "null",
                0.0,
                0,
                "[]",
                "",
                "2026-08-18T00:00:00+00:00",
                "2026-08-18T00:00:00+00:00",
            ),
        )
        conn.commit()
        conn.close()
        entry = StrategyRegistry(repo).get("STRAT-NULLSCORE")
        assert entry is not None, "row with literal 'null' score must decode"
        assert entry.score is None
        assert entry.lifecycle.value == "DISCOVERED"
    finally:
        repo.close()
        tmp_db.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 3. Release: stale web bundle must FAIL verification (BUG-075 deployment drift)
# ---------------------------------------------------------------------------


def _make_bundle(tmp_path: Path, app_js: str, index_html: str, build_info: dict) -> Path:
    """Creates a minimal packaged-bundle tree under tmp_path and returns it.

    Includes api_client.js + styles.css with their hashes recorded so the
    repo-Web fallback freshness comparison (BUG-075) also passes.
    """
    import hashlib as _hashlib

    root = tmp_path / "bundle"
    web = root / "_internal" / "Web"
    web.mkdir(parents=True)
    (web / "app.js").write_text(app_js, encoding="utf-8")
    (web / "index.html").write_text(index_html, encoding="utf-8")
    styles = "body {}"
    api_client = "window.NX = {};"
    (web / "styles.css").write_text(styles, encoding="utf-8")
    (web / "api_client.js").write_text(api_client, encoding="utf-8")
    cfg = root / "_internal" / "configs"
    cfg.mkdir(parents=True)
    (cfg / "base.yaml").write_text("news:\n  enabled: true\n", encoding="utf-8")
    build_info.setdefault("web_asset_hash", _hashlib.sha256(app_js.encode()).hexdigest())
    build_info.setdefault("web_index_hash", _hashlib.sha256(index_html.encode()).hexdigest())
    build_info.setdefault("web_styles_hash", _hashlib.sha256(styles.encode()).hexdigest())
    build_info.setdefault("web_api_client_hash", _hashlib.sha256(api_client.encode()).hexdigest())
    (root / "_internal" / "build-info.json").write_text(json.dumps(build_info), encoding="utf-8")
    return root


def test_verify_web_assets_pass_when_hashes_match(tmp_path: Path) -> None:
    """A bundle whose packaged app.js/index.html match the recorded source
    hashes must PASS the Web/assets verification."""
    from nexus_scalp.release.verify import ReleaseVerifier

    app_js = "console.log('current');"
    index_html = "<html>current</html>"
    root = _make_bundle(
        tmp_path,
        app_js,
        index_html,
        {
            "web_asset_hash": __import__("hashlib").sha256(app_js.encode()).hexdigest(),
            "web_index_hash": __import__("hashlib").sha256(index_html.encode()).hexdigest(),
        },
    )
    verifier = ReleaseVerifier(root=root, timeout=30)
    result = verifier._asset_web()
    assert result.status == "PASS", result.detail


def test_verify_web_assets_fail_on_stale_bundle(tmp_path: Path) -> None:
    """BUG-075 deployment drift: a packaged app.js that does NOT match the
    recorded source hash (the stale-bundle scenario) MUST fail release
    verification instead of silently shipping an outdated UI."""
    from nexus_scalp.release.verify import ReleaseVerifier

    app_js = "console.log('current');"
    index_html = "<html>current</html>"
    root = _make_bundle(
        tmp_path,
        app_js,
        index_html,
        {
            # recorded hash of a DIFFERENT (older) app.js
            "web_asset_hash": __import__("hashlib")
            .sha256(b"console.log('STALE-OLD-BUNDLE');")
            .hexdigest(),
            "web_index_hash": __import__("hashlib").sha256(index_html.encode()).hexdigest(),
        },
    )
    verifier = ReleaseVerifier(root=root, timeout=30)
    result = verifier._asset_web()
    assert result.status == "FAIL", result.detail
    assert "STALE WEB BUNDLE" in result.detail


def test_build_script_records_web_hashes() -> None:
    """build_release.ps1 must stamp web_asset_hash/web_index_hash into
    build-info.json so the verifier can detect stale bundles."""
    text = (REPO_ROOT / "scripts/build/build_release.ps1").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "web_asset_hash" in text
    assert "web_index_hash" in text
    assert "Get-FileHash" in text


def test_repo_web_bundle_is_current() -> None:
    """The repository's Web/app.js must contain the BUG-075 null-score guard
    (safeScore) and the [UI_API]/[UI_ERROR] observability — the canonical
    source of the bundle served at runtime."""
    app_js = (REPO_ROOT / "Web" / "app.js").read_text(encoding="utf-8", errors="replace")
    assert "safeScore" in app_js
    assert "[UI_ERROR]" in app_js
    assert "[UI_API] endpoint=/api/rules" in app_js
    assert "PENDING" in app_js
