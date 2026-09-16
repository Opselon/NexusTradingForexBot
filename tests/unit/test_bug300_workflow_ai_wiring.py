"""BUG-300: CI workflow wiring for the FINAL AI summary lane.

Architecture pin: the AI summary is a SEPARATE workflow (ci-summary.yml,
workflow_run@completed) that fires after EVERY watched pipeline finishes —
the very end of the run — and is the ONLY consumer of AI_HOST/AI_KEY. The
per-run notify workflows must stay AI-free (single AI surface, no scattered
gates). Static pins here (the repo's check_workflows.py validates shape,
not semantics):

* every pipeline workflow name appears in ci-summary's watch list;
* the AI env block exists ONLY in ci-summary.yml;
* no per-run workflow calls ai-triage anymore;
* the summary lane is advisory: read-only content permissions except
  pull-requests:write (comment), and it never gates anything (workflow_run
  has no status-check consumers by name);
* secret VALUES never appear in any workflow text (names only).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"


def _wf(name: str) -> dict:
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def _summary_body() -> str:
    return (WF / "ci-summary.yml").read_text(encoding="utf-8")


def test_all_pipeline_workflows_are_watched_by_the_final_lane():
    watched = set(_summary_body().split("workflow_run:")[1].split("types:")[0].split())
    names = set()
    for f in WF.glob("*.yml"):
        if f.name in {"ci-summary.yml", "drift-debug.yml"}:
            continue
        body = f.read_text(encoding="utf-8")
        m = re.search(r"^name:\s*(.+)$", body, flags=re.MULTILINE)
        if m:
            names.add(m.group(1).strip().strip('"'))
    missing = {n for n in names if f'"{n}"' not in _summary_body() and n not in watched}
    assert not missing, f"workflows not watched by ci-summary: {missing}"


def test_ai_env_is_centralized_in_summary_lane_only():
    for f in WF.glob("*.yml"):
        body = f.read_text(encoding="utf-8")
        has_env = "AI_HOST: ${{ secrets.AI_HOST }}" in body
        if f.name == "ci-summary.yml":
            assert has_env and "AI_KEY: ${{ secrets.AI_KEY }}" in body
        else:
            assert not has_env, f"{f.name}: AI env must live only in ci-summary.yml"


def test_no_per_run_workflow_calls_ai_triage():
    for f in WF.glob("*.yml"):
        if f.name == "ci-summary.yml":
            continue
        body = f.read_text(encoding="utf-8")
        assert "ai-triage" not in body, f"{f.name}: triage belongs to the final lane"


def test_summary_lane_permissions_are_bounded():
    doc = _wf("ci-summary.yml")
    perms = doc.get("permissions") or {}
    assert perms.get("contents") == "read"
    assert perms.get("actions") == "read"
    assert perms.get("pull-requests") == "write"  # PR comment only
    assert "id-token" not in perms and "deployments" not in perms


def test_summary_lane_triggers_on_completed_only():
    doc = _wf("ci-summary.yml")
    trig = doc[True]["workflow_run"] if True in doc else doc["on"]["workflow_run"]
    assert trig["types"] == ["completed"]


def test_summary_lane_uses_pinned_actions():
    body = _summary_body()
    for ref in re.findall(r"uses:\s*([^\s#]+)", body):
        if ref.startswith(("./", "docker:", "gcr.io")):
            continue
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), f"unpinned action: {ref}"


def test_no_secret_values_in_workflow_text():
    for f in WF.glob("*.yml"):
        body = f.read_text(encoding="utf-8")
        assert not re.search(r"sk-[A-Za-z0-9_\-]{16,}", body), f.name
        assert not re.search(r"\d{8,10}:[A-Za-z0-9_-]{25,}", body), f.name


def test_summary_writes_all_three_outputs():
    body = _summary_body()
    assert "ai-triage" in body and "--context ci-results/run-info/summary-evidence.json" in body
    assert "GITHUB_STEP_SUMMARY" in body  # run-page summary
    assert 'gh", "pr", "comment' in body  # PR review comment path


def test_make_ci_results_presence_registry_updated():
    src = (ROOT / "scripts" / "ci" / "make_ci_results.py").read_text(encoding="utf-8")
    m = re.search(r"SECRET_PRESENCE_ENV = \[(.*?)\]", src, flags=re.DOTALL)
    assert m and '"AI_HOST"' in m.group(1) and '"AI_KEY"' in m.group(1)
