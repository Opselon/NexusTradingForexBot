"""Deterministic tests for the canonical NSE change classifier.

These are PRODUCTION-INFRASTRUCTURE tests: the classifier drives which CI
gates run. A wrong mapping means a required gate is silently skipped (or an
unrelated gate needlessly runs). If you change a mapping, you MUST update the
expectation here — drift between the code and these tests is a bug.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "ci"))

from classify_changes import classify_file, classify_files, KNOWN_LANES  # noqa: E402


def test_python_source_changes():
    assert classify_file("src/nexus_scalp/risk/risk_engine.py") == {"python"}
    assert classify_file("src/nexus_scalp/models/scalp_net.py") == {"python"}
    assert classify_file("src/nexus_scalp/cli/main.py") == {"python"}


def test_web_js_changes_are_web_and_js():
    assert classify_file("Web/app.js") == {"web", "js"}
    assert classify_file("Web/forensic_console.js") == {"web", "js"}
    assert classify_file("tests/js/forensic_console.test.js") == {"web", "js"}


def test_docker_changes():
    assert classify_file("Dockerfile") == {"docker"}
    assert classify_file("docker-compose.yml") == {"docker"}
    assert classify_file("docker/entrypoint.sh") == {"docker"}


def test_ci_changes():
    assert classify_file(".github/workflows/ci.yml") == {"ci"}
    assert classify_file("scripts/ci/check_workflows.py") == {"ci"}
    assert classify_file("beforePush.ps1") == {"ci"}
    assert classify_file("beforePush.sh") == {"ci"}


def test_dependency_manifest_changes():
    assert classify_file("pyproject.toml") == {"deps", "python"}
    assert classify_file("uv.lock") == {"deps", "python"}
    assert classify_file("requirements.txt") == {"deps", "python"}


def test_release_and_scripts_changes():
    assert classify_file("installer/NexusScalpEngine.iss") == {"release"}
    assert classify_file("scripts/build/build_release.ps1") == {"release", "scripts"}
    assert classify_file("scripts/start.ps1") == {"scripts"}
    assert classify_file("scripts/doctor.ps1") == {"scripts"}


def test_docs_only_changes():
    assert classify_file("docs/CI_ARCHITECTURE.md") == {"docs"}
    assert classify_file("agents/skill.md") == {"docs"}
    assert classify_file("README.md") == {"docs"}


def test_unknown_path_fails_safe_not_empty_skip():
    # An unmapped file must NOT map to nothing meaningful in a way that lets a
    # gate think "docs_only". classify_files() must surface it so the workflow
    # fail-safe (run Python unless strictly docs_only) still fires.
    res = classify_files(["mystery_blob.xyz", "some/path.cs"])
    assert res["docs_only"] is False
    assert res["python"] is False  # unknown -> not python, handled by workflow default


def test_docs_only_aggregation():
    assert classify_files(["docs/CI_ARCHITECTURE.md"])["docs_only"] is True
    assert classify_files(["README.md"])["docs_only"] is True
    # docs + python => NOT docs-only
    assert classify_files(["docs/x.md", "src/y.py"])["docs_only"] is False
    # python only => NOT docs-only
    assert classify_files(["src/y.py"])["docs_only"] is False


def test_multiple_changed_areas():
    res = classify_files(
        ["src/x.py", "Web/app.js", "Dockerfile", ".github/workflows/ci.yml"]
    )
    assert res["python"] and res["web"] and res["docker"] and res["ci"]


def test_deleted_and_renamed_are_still_detected():
    # Removing a Python file is a Python change; the analyzer feeds raw names.
    res = classify_files(["src/nexus_scalp/old_module.py"])
    assert res["python"] is True


def test_all_known_lanes_present_in_output():
    res = classify_files(["src/x.py"])
    assert set(res.keys()) == set(KNOWN_LANES) | {"docs_only"}


def test_no_lane_outside_known_set():
    res = classify_files(["src/x.py", "Web/a.js", "docs/b.md"])
    for k in res:
        assert k in (KNOWN_LANES + ("docs_only",)), f"unexpected lane {k}"
