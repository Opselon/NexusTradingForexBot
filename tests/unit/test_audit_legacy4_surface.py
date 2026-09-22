#!/usr/bin/env python3
"""ML-ARCH-001 legacy-4 surface audit probe — regression tests.

These tests pin the ML-ARCH-001 evidence so the audit cannot drift silently:
they assert the *measured* legacy-4 surface at HEAD and the probe's own
correctness on synthetic trees. Torch-free: the probe is a static analyzer and
its tests must run in the slim verification venv.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = REPO_ROOT / "scripts" / "audit" / "audit_legacy4_surface.py"
PY = sys.executable


def _run_probe(repo: Path, json_out: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = [PY, str(PROBE), "--repo", str(repo)]
    if json_out:
        cmd.append("--json")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _probe_json(repo: Path) -> dict:
    proc = _run_probe(repo, json_out=True)
    assert proc.returncode == 0, f"probe rc={proc.returncode} stderr={proc.stderr}"
    assert proc.stdout, "probe produced no stdout"
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# 1. The probe is correct on synthetic trees (the analyzer itself is the SUT)
# ---------------------------------------------------------------------------


def _write_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp_path


def test_probe_classifies_production_serving_literal(tmp_path: Path) -> None:
    """A 4-wide literal in the live engine is PRODUCTION-SERVING."""
    repo = _write_tree(
        tmp_path,
        {
            "src/nexus_scalp/application/live_engine.py": (
                "def mint():\n    return ScalpNet(num_features=50, num_classes=4)\n"
            ),
        },
    )
    report = _probe_json(repo)
    assert report["src_legacy4_sites"] == 1
    assert report["src_legacy4_by_subsystem"]["PRODUCTION-SERVING"] == 1
    assert report["verdict"] == "LEGACY-4-LOAD-BEARING"


def test_probe_skips_comment_only_lines(tmp_path: Path) -> None:
    """A `# num_classes = 4` comment is prose, not a live literal."""
    repo = _write_tree(
        tmp_path,
        {
            "src/nexus_scalp/application/live_engine.py": (
                "# hardcoded ScalpNet(num_classes=4); the contract SSoT is 3.\n"
                "def mint():\n"
                "    return ScalpNet(num_features=50, num_classes=3)\n"
            ),
        },
    )
    report = _probe_json(repo)
    assert report["src_legacy4_sites"] == 0, "comment-only line must not count"
    assert report["verdict"] == "LEGACY-4-DEAD-WEIGHT"


def test_probe_detects_committed_artifact_shape(tmp_path: Path) -> None:
    """A git-committed .pt flips the verdict to the committed-artifact class."""
    repo = _write_tree(
        tmp_path,
        {"src/nexus_scalp/models/x.py": "def f():\n    return 3\n"},
    )
    (repo / "models").mkdir(exist_ok=True)
    (repo / "models" / "champion.pt").write_bytes(b"\x00" * 16)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    report = _probe_json(repo)
    assert report["committed_artifacts"]["committed_pt_count"] == 1
    assert report["verdict"] == "LEGACY-4-COMMITTED-ARTIFACT"


def test_probe_reports_function_context(tmp_path: Path) -> None:
    """The report names the enclosing function, not just the file."""
    repo = _write_tree(
        tmp_path,
        {
            "src/nexus_scalp/smoke/runner.py": (
                "def _layer_l2_integration():\n"
                "    def _chain():\n"
                "        return ScalpNet(num_features=50, num_classes=4)\n"
            ),
        },
    )
    report = _probe_json(repo)
    detail = report["src_legacy4_detail"]
    assert len(detail) == 1
    assert detail[0]["function"] == "_chain"
    assert detail[0]["subsystem"] == "SMOKE"


def test_probe_classifies_subsystem_independently_of_os_sep(tmp_path: Path) -> None:
    """The subsystem table matches on any OS separator (BUG-307D).

    ``Path.relative_to`` joins with ``os.sep``; the probe's classification table
    and this test file use ``/``. On the Windows leg of the OS Matrix every site
    read as OTHER, so ``test_head_legacy4_sites_are_non_serving`` failed with a
    backslash-joined path in the message. Force the failure shape here by
    feeding the probe a tree whose classification depends only on the separator
    round-trip, not on any file content difference.
    """
    repo = _write_tree(
        tmp_path,
        {
            "src/nexus_scalp/smoke/runner.py": (
                "def _chain():\n    return ScalpNet(num_features=50, num_classes=4)\n"
            ),
            "src/nexus_scalp/shadow/recorder.py": (
                "def _rec():\n    return ScalpNet(num_features=50, num_classes=4)\n"
            ),
            "src/nexus_scalp/application/live/inference.py": (
                "def _fwd():\n    return ScalpNet(num_features=50, num_classes=4)\n"
            ),
        },
    )
    report = _probe_json(repo)
    by_sub = report["src_legacy4_by_subsystem"]
    # The separator round-trip must not erase any classification. On Windows the
    # pre-fix probe reported OTHER=3 for exactly these three files.
    assert by_sub["SMOKE"] == 1, by_sub
    assert by_sub["SHADOW"] == 1, by_sub
    assert by_sub["PRODUCTION-SERVING"] == 1, by_sub
    assert by_sub["OTHER"] == 0, by_sub
    # The emitted paths stay forward-slash regardless of the host OS, so the
    # report is byte-identical between matrix legs (stable pinned evidence).
    for site in report["src_legacy4_detail"]:
        assert "\\" not in site["path"], site["path"]
        assert site["subsystem"] != "OTHER", site
    assert report["verdict"] == "LEGACY-4-LOAD-BEARING"


# ---------------------------------------------------------------------------
# 2. The measured HEAD surface — the ML-ARCH-001 evidence, pinned
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def head_report() -> dict:
    return _probe_json(REPO_ROOT)


def test_head_report_probe_identity(head_report: dict) -> None:
    assert head_report["probe"] == "ml-arch-001-legacy4-surface"
    assert head_report["trained_class_count_src"] == 3
    assert head_report["legacy_head_classes_src"] == 4


def test_head_has_no_serving_path_legacy4(head_report: dict) -> None:
    """The money assertion: no 4-wide literal on the production serving path."""
    by_sub = head_report["src_legacy4_by_subsystem"]
    assert by_sub["PRODUCTION-SERVING"] == 0, (
        "a 4-wide literal appeared on the production serving path -- "
        "ML-ARCH-001's DEAD-WEIGHT verdict no longer holds; update DEC-0010"
    )


def test_head_verdict_is_dead_weight(head_report: dict) -> None:
    assert head_report["verdict"] == "LEGACY-4-DEAD-WEIGHT", head_report["verdict_reason"]


def test_head_zero_committed_pt_artifacts(head_report: dict) -> None:
    """No 4-wide checkpoint is committed to git (the Option A risk surface)."""
    assert head_report["committed_artifacts"]["committed_pt_count"] == 0


def test_head_legacy4_sites_are_non_serving(head_report: dict) -> None:
    """Every remaining 4-wide literal is smoke/shadow tooling, not serving."""
    detail = head_report["src_legacy4_detail"]
    assert detail, "expected at least one documented legacy-4 compatibility site"
    for site in detail:
        assert site["subsystem"] in {"SMOKE", "SHADOW", "WEB"}, (
            f"unexpected serving-path legacy-4 site {site['path']}:{site['line']}"
        )


# ---------------------------------------------------------------------------
# 3. CLI contract: --json must be machine-readable (BUG-300 class)
# ---------------------------------------------------------------------------


def test_cli_json_output_is_machine_readable() -> None:
    proc = _run_probe(REPO_ROOT, json_out=True)
    assert proc.returncode == 0
    # A raw dict repr (single quotes) or a line-wrapped body both break this.
    parsed = json.loads(proc.stdout)
    assert isinstance(parsed, dict)
    assert "verdict" in parsed


def test_cli_human_output_names_verdict() -> None:
    proc = _run_probe(REPO_ROOT)
    assert proc.returncode == 0
    assert "VERDICT:" in proc.stdout


def test_probe_missing_src_errors_loudly(tmp_path: Path) -> None:
    proc = _run_probe(tmp_path)
    assert proc.returncode == 1
    assert "no src/" in proc.stderr
