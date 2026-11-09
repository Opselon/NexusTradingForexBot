#!/usr/bin/env python3
"""Fail-closed parity check for the canonical NSE Linux runner policy.

The selector is dynamic, but workflow input drift can silently reverse tier
priority. This check verifies that both automatic Linux workflows consume the
same tier order defined by runner_config.py and preserve the hosted fallback.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:
    raise SystemExit(f"runner routing check requires PyYAML: {exc}") from exc

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
import runner_config as config  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"RUNNER ROUTING CONTRACT FAILED: {message}")


def load_workflow(relative: str) -> dict:
    path = ROOT / relative
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"cannot parse {relative}: {exc}") from exc
    require(isinstance(value, dict), f"{relative}: workflow root must be a mapping")
    require(isinstance(value.get("jobs"), dict), f"{relative}: jobs mapping is missing")
    return value


def selector_inputs(workflow: dict, relative: str) -> dict:
    job = workflow["jobs"].get("runner-select")
    require(isinstance(job, dict), f"{relative}: runner-select job is missing")
    for step in job.get("steps") or []:
        if isinstance(step, dict) and step.get("uses") == "./.github/actions/runner-select":
            inputs = step.get("with") or {}
            require(isinstance(inputs, dict), f"{relative}: selector inputs are malformed")
            return inputs
    raise SystemExit(
        f"RUNNER ROUTING CONTRACT FAILED: {relative}: composite selector step is missing"
    )


def check_fork_guard(inputs: dict, relative: str) -> None:
    expression = str(inputs.get("untrusted", ""))
    require(
        "github.event.pull_request.head.repo.full_name != github.repository" in expression,
        f"{relative}: fork PR trust-boundary expression is missing",
    )


def main() -> int:
    tier1 = ",".join(config.LINUX_SELF_HOSTED_TIER1_LABELS)
    tier2 = ",".join(config.LINUX_SELF_HOSTED_TIER2_LABELS)

    ci_path = ".github/workflows/ci.yml"
    ci = load_workflow(ci_path)
    ci_inputs = selector_inputs(ci, ci_path)
    require(
        ci_inputs.get("labels") == tier1,
        f"ci.yml tier-1 must be {tier1!r}; got {ci_inputs.get('labels')!r}",
    )
    require(
        ci_inputs.get("fallback_labels") == tier2,
        f"ci.yml tier-2 must be {tier2!r}; got {ci_inputs.get('fallback_labels')!r}",
    )
    check_fork_guard(ci_inputs, ci_path)

    os_path = ".github/workflows/tests-os.yml"
    os_workflow = load_workflow(os_path)
    env = os_workflow.get("env") or {}
    require(
        env.get("NSE_RUNNER_LINUX_TIER1_LABELS") == tier1,
        f"tests-os.yml tier-1 environment must be {tier1!r}",
    )
    require(
        env.get("NSE_RUNNER_LINUX_TIER2_LABELS") == tier2,
        f"tests-os.yml tier-2 environment must be {tier2!r}",
    )
    os_inputs = selector_inputs(os_workflow, os_path)
    require(
        os_inputs.get("labels") == "${{ env.NSE_RUNNER_LINUX_TIER1_LABELS }}",
        "tests-os.yml selector must consume the canonical tier-1 environment value",
    )
    require(
        os_inputs.get("fallback_labels") == "${{ env.NSE_RUNNER_LINUX_TIER2_LABELS }}",
        "tests-os.yml selector must consume the canonical tier-2 environment value",
    )
    check_fork_guard(os_inputs, os_path)

    hosted = os_workflow["jobs"].get("tests-linux-hosted")
    require(isinstance(hosted, dict), "tests-os.yml hosted Linux fallback job is missing")
    require(
        hosted.get("runs-on") == "ubuntu-latest",
        "tests-os.yml hosted fallback must remain ubuntu-latest",
    )

    self_hosted = os_workflow["jobs"].get("tests-linux-self-hosted")
    require(isinstance(self_hosted, dict), "tests-os.yml self-hosted Linux job is missing")
    require(
        self_hosted.get("runs-on")
        == "${{ fromJSON(needs.runner-select.outputs.self_hosted_labels) }}",
        "tests-os.yml self-hosted job must schedule on the selected tier label set",
    )

    print("runner routing contract: PASS")
    print(f"tier-1 (preferred): {tier1}")
    print(f"tier-2 (fallback):  {tier2}")
    print("hosted fallback and fork trust-boundary expressions: present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
