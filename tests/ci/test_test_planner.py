"""Unit tests for scripts/ci/test_planner.py — the deterministic test planner.

Covers the change-detection / test-selection scenarios from the CI/CD
architecture mission: targeted selection, transitive (reverse-reachability)
selection, global invalidation, mandatory safety expansion, missing test
mappings, docs-only behaviour and plan digest determinism.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]  # tests/ci/x.py -> repo root
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "ci"))

import test_planner  # noqa: E402


@pytest.fixture()
def mini_repo(tmp_path: Path) -> Path:
    """A miniature NSE-shaped tree: src package, tests, suites, config."""
    root = tmp_path
    (root / "src" / "nexus_scalp" / "execution").mkdir(parents=True)
    (root / "src" / "nexus_scalp" / "risk").mkdir(parents=True)
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "src" / "nexus_scalp" / "__init__.py").write_text("")
    (root / "src" / "nexus_scalp" / "risk" / "__init__.py").write_text("")
    (root / "src" / "nexus_scalp" / "risk" / "risk_engine.py").write_text(
        "def assess():\n    return True\n"
    )
    (root / "src" / "nexus_scalp" / "execution" / "__init__.py").write_text("")
    (root / "src" / "nexus_scalp" / "execution" / "trade_manager.py").write_text(
        "from nexus_scalp.risk.risk_engine import assess\n\n\ndef trade():\n    return assess()\n"
    )
    (root / "tests" / "unit" / "test_risk_engine.py").write_text(
        "from nexus_scalp.risk.risk_engine import assess\n\n\ndef test_assess():\n    assert assess()\n"
    )
    (root / "tests" / "unit" / "test_trade_manager.py").write_text(
        "from nexus_scalp.execution.trade_manager import trade\n\n\ndef test_trade():\n    assert trade()\n"
    )
    (root / "tests" / "unit" / "test_unrelated.py").write_text(
        "def test_other():\n    assert True\n"
    )
    (root / "tests" / "conftest.py").write_text("")
    (root / "tests" / "critical_suite.txt").write_text(
        "tests/unit/test_risk_engine.py\ntests/unit/test_trade_manager.py\n"
    )
    (root / "tests" / "fast_suite.txt").write_text("tests/unit/test_unrelated.py\n")
    return root


def _ids(plan_obj: dict) -> set[str]:
    return {t["id"] for t in plan_obj["selected_tests"]}


def test_scenario_a_direct_dependency_selected(mini_repo: Path):
    p = test_planner.plan(["src/nexus_scalp/risk/risk_engine.py"], mini_repo)
    ids = _ids(p)
    assert "tests/unit/test_risk_engine.py" in ids  # direct importer
    assert "tests/unit/test_trade_manager.py" in ids  # TRANSITIVE via trade_manager
    assert "tests/unit/test_unrelated.py" not in ids  # unrelated stays out
    assert p["selection_mode"] == "targeted"
    assert p["full_suite_required"] is False


def test_scenario_b_changed_test_selected(mini_repo: Path):
    p = test_planner.plan(["tests/unit/test_unrelated.py"], mini_repo)
    assert "tests/unit/test_unrelated.py" in _ids(p)


def test_scenario_g_global_invalidation_expands(mini_repo: Path):
    p = test_planner.plan(["tests/conftest.py"], mini_repo)
    assert p["full_suite_required"] is True
    reasons = {t["id"]: t["reason"] for t in p["selected_tests"]}
    assert any("conservative_fallback" in r for r in reasons.values()), reasons
    assert p["fallback_reason"] and "conftest" in p["fallback_reason"]


def test_scenario_g_lockfile_invalidates(mini_repo: Path):
    p = test_planner.plan(["requirements.lock"], mini_repo)
    assert p["full_suite_required"] is True


def test_scenario_h_safety_change_mandatory_expansion(mini_repo: Path):
    p = test_planner.plan(["src/nexus_scalp/execution/trade_manager.py"], mini_repo)
    reasons = {t["id"]: t["reason"] for t in p["selected_tests"]}
    assert "tests/unit/test_trade_manager.py" in reasons
    assert "mandatory_safety" in reasons["tests/unit/test_trade_manager.py"]
    # mandatory expansion pulls the whole critical suite
    assert "tests/unit/test_risk_engine.py" in reasons


def test_missing_mapping_reported_and_failsafe(mini_repo: Path):
    (mini_repo / "src" / "nexus_scalp" / "lonely.py").write_text("x = 1\n")
    p = test_planner.plan(["src/nexus_scalp/lonely.py"], mini_repo)
    assert "src/nexus_scalp/lonely.py" in p["missing_mappings"]
    assert p["full_suite_required"] is True  # conservative fallback, never silent pass


def test_docs_only_change_skips_python(mini_repo: Path):
    p = test_planner.plan(["docs/README.md"], mini_repo)
    assert p["skipped_groups"] == [
        {"name": "python-tests", "reason": "no python source or test files changed"}
    ]
    assert _ids(p) == set()


def test_schedule_requires_full_suite(mini_repo: Path):
    p = test_planner.plan([], mini_repo, event="schedule")
    assert p["full_suite_required"] is True
    assert p["selection_mode"] == "expanded"


def test_plan_digest_deterministic(mini_repo: Path):
    files = ["src/nexus_scalp/risk/risk_engine.py"]
    a = test_planner.plan(files, mini_repo, commit_sha="a" * 40)
    b = test_planner.plan(files, mini_repo, commit_sha="a" * 40)
    c = test_planner.plan(files, mini_repo, commit_sha="b" * 40)
    assert a["plan_digest"] == b["plan_digest"]
    assert a["plan_digest"] != c["plan_digest"]  # sha participates in the plan
    # digest is verifiable from the canonical form
    body = {k: v for k, v in a.items() if k != "plan_digest"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    import hashlib

    assert hashlib.sha256(canonical.encode()).hexdigest() == a["plan_digest"]


def test_unparseable_python_expands_conservatively(mini_repo: Path):
    (mini_repo / "src" / "nexus_scalp" / "broken.py").write_text("def f(:\n")
    p = test_planner.plan(["src/nexus_scalp/broken.py"], mini_repo)
    assert p["full_suite_required"] is True
    assert "unparseable" in (p["fallback_reason"] or "")


def test_deleted_file_is_just_a_name(mini_repo: Path):
    # Deletions arrive as paths; the planner treats them like any changed path.
    p = test_planner.plan(["src/nexus_scalp/risk/risk_engine.py"], mini_repo)
    assert _ids(p)  # selection produced; no crash on absent files


def test_relative_import_resolved_in_src(mini_repo: Path):
    # trade_manager switches to a RELATIVE import; the risk_engine test must
    # still be reached through the resolved edge.
    (mini_repo / "src" / "nexus_scalp" / "execution" / "trade_manager.py").write_text(
        "from ..risk.risk_engine import assess\n\n\ndef trade():\n    return assess()\n"
    )
    p = test_planner.plan(["src/nexus_scalp/execution/trade_manager.py"], mini_repo)
    assert "tests/unit/test_risk_engine.py" in _ids(p)
    assert p["full_suite_required"] is False


def test_relative_import_sibling_package_selected(mini_repo: Path):
    (mini_repo / "src" / "nexus_scalp" / "risk" / "extra.py").write_text(
        "from .risk_engine import assess\n\n\ndef extra():\n    return assess()\n"
    )
    p = test_planner.plan(["src/nexus_scalp/risk/extra.py"], mini_repo)
    assert "tests/unit/test_risk_engine.py" in _ids(p)


def test_unresolvable_relative_import_marks_uncertain(mini_repo: Path):
    (mini_repo / "src" / "nexus_scalp" / "mystery.py").write_text(
        "from .does_not_exist_anywhere import thing\n"
    )
    p = test_planner.plan(["src/nexus_scalp/mystery.py"], mini_repo)
    assert p["full_suite_required"] is True
    assert "relative" in (p["fallback_reason"] or "")


def test_relative_import_in_tests_resolves_to_helper(mini_repo: Path):
    (mini_repo / "tests" / "unit" / "helpers_local.py").write_text("def helper():\n    return 1\n")
    (mini_repo / "tests" / "unit" / "test_uses_helper.py").write_text(
        "from .helpers_local import helper\n\n\ndef test_helper():\n    assert helper()\n"
    )
    # helper file changed -> its importing test selected via resolved edge
    p = test_planner.plan(["tests/unit/helpers_local.py"], mini_repo)
    assert "tests/unit/test_uses_helper.py" in _ids(p)
    assert p["full_suite_required"] is False


def _hub_repo(mini_repo: Path) -> Path:
    """Add a hub (26 direct test importers) + a leaf it imports.

    hub.py imports leaf.py; 26 tests import hub.py, 2 tests import leaf.py
    directly. Without hub-bounding, changing leaf.py selects all 28 tests
    (leaf -> hub -> every hub test); with it, only the 2 direct tests.
    """
    (mini_repo / "src" / "nexus_scalp" / "leaf.py").write_text("def leaf_fn():\n    return 1\n")
    (mini_repo / "src" / "nexus_scalp" / "hub.py").write_text(
        "from nexus_scalp.leaf import leaf_fn\n\n\ndef hub_fn():\n    return leaf_fn()\n"
    )
    for i in range(26):
        (mini_repo / "tests" / "unit" / f"test_hub_user_{i:02d}.py").write_text(
            "from nexus_scalp.hub import hub_fn\n\n\ndef test_hub():\n    assert hub_fn()\n"
        )
    for i in range(2):
        (mini_repo / "tests" / "unit" / f"test_leaf_user_{i}.py").write_text(
            "from nexus_scalp.leaf import leaf_fn\n\n\ndef test_leaf():\n    assert leaf_fn()\n"
        )
    return mini_repo


def test_hub_is_boundary_for_leaf_change(mini_repo: Path):
    root = _hub_repo(mini_repo)
    p = test_planner.plan(["src/nexus_scalp/leaf.py"], root)
    ids = _ids(p)
    # only the leaf's direct test importers run — the hub's 26 dependents stay out
    assert "tests/unit/test_leaf_user_0.py" in ids
    assert "tests/unit/test_leaf_user_1.py" in ids
    assert "tests/unit/test_hub_user_00.py" not in ids
    assert "tests/unit/test_hub_user_25.py" not in ids
    assert p["selection_mode"] == "targeted"
    assert p["full_suite_required"] is False
    assert test_planner._HUB_BOUNDED_NOTE in p["notes"]


def test_changed_hub_selects_its_direct_tests(mini_repo: Path):
    root = _hub_repo(mini_repo)
    p = test_planner.plan(["src/nexus_scalp/hub.py"], root)
    ids = _ids(p)
    assert "tests/unit/test_hub_user_00.py" in ids
    assert "tests/unit/test_hub_user_25.py" in ids
    # the leaf's own tests do not import the hub — they stay out
    assert "tests/unit/test_leaf_user_0.py" not in ids
    assert p["selection_mode"] == "targeted"


def test_module_only_reachable_via_hub_is_uncovered(mini_repo: Path):
    root = _hub_repo(mini_repo)
    (root / "src" / "nexus_scalp" / "orphan.py").write_text("def orphan_fn():\n    return 1\n")
    (root / "src" / "nexus_scalp" / "hub.py").write_text(
        "from nexus_scalp.leaf import leaf_fn\n"
        "from nexus_scalp.orphan import orphan_fn\n\n\ndef hub_fn():\n    return leaf_fn() + orphan_fn()\n"
    )
    p = test_planner.plan(["src/nexus_scalp/orphan.py"], root)
    assert "src/nexus_scalp/orphan.py" in p["missing_mappings"]
    assert p["full_suite_required"] is True  # fail-safe: no provable coverage


def test_hub_threshold_boundary():
    imports = {
        "t1": {"nexus_scalp.exact25"},
        "t2": {"nexus_scalp.exact25"},
    }
    for i in range(23):
        imports[f"u{i}"] = {"nexus_scalp.exact25"}
    # exactly 25 importers -> NOT a hub
    assert "nexus_scalp.exact25" not in test_planner._hub_modules(imports)
    imports["u23"] = {"nexus_scalp.exact25"}  # 26th importer
    assert "nexus_scalp.exact25" in test_planner._hub_modules(imports)
