"""Gate-scope integrity: CI green must mean the declared checks RAN.

TASK-A10 (CI/CD gate-integrity): the nightly client-E2E wrapper records the
journey rc and prints an error line, but historically ended with a bare
`exit 0`, so a red journey still produced a green job. That defect was fixed
in the workflow; these tests pin the CONTRACT so it cannot regress silently:

1. every workflow step that records a check rc then explicitly `exit 0`
   must either (a) fail on a non-zero rc inside the step, or (b) hand the
   verdict to a later always() step that exits non-zero when the recorded
   status is failed/errored;
2. the nightly client-E2E workflow in particular must NOT swallow the
   journey rc: the gate step itself exits non-zero on failed journeys.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(".github/workflows")


def _steps_with_exit0(text: str) -> list[tuple[str, str]]:
    """Yield (step-name, body) for every run: step whose body ends in `exit 0`."""
    # Minimal YAML run-block splitter: iterate '### NAME' boundaries reliably
    # by scanning "- name:" ... next "- name:"/"steps:" end. Regex over body.
    step_re = re.compile(r"(?m)^\s*- name:\s*(.+)$")
    matches = list(step_re.finditer(text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        # stop at next top-level job/step key heuristics: good enough for scan
        if "run:" not in body and "uses:" not in body:
            continue
        if re.search(
            r"(?m)^\s*exit 0\s*$", body.strip().splitlines()[-1] if body.strip() else ""
        ) or re.search(r"(?m)^\s*exit 0\s*$", body):
            out.append((m.group(1).strip().strip("`\"'"), body))
    return out


def test_no_step_records_failure_then_exits_zero_unguarded() -> None:
    """A step that writes rc into GITHUB_ENV and then exits 0 must be paired
    with a later non-zero exit step, or fail inline on the rc."""
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        text = wf.read_text(encoding="utf-8")
        for name, body in _steps_with_exit0(text):
            m = re.search(r"\b([A-Z0-9_]+_RC)=\$rc\b", body)
            if not m:
                continue  # not a check-recording step (e.g. advisory notify)
            # Inline failure handling? The ci.yml pattern runs the tool, records
            # rc, exits 0, and a LATER "Fail job if any check failed" step reads
            # the run-info JSON status (failed/errored -> exit 1). Treat a step
            # whose recorded check flows into that final gate as guarded.
            inline_fail = re.search(r"if \[ \"\$rc\" -ne 0 \].*?exit 1", body, re.S)
            has_final_gate = "Fail job if any check failed" in text
            # or delegated: a later step reads ${{ env.<VAR> }} and exit 1
            var = m.group(1)
            delegated = re.search(r"if:\s*.*env\." + var + r".*!=\s*'0'", text) or re.search(
                rf"env\.{var}\s*!=\s*'0'", text
            )
            assert inline_fail or delegated or has_final_gate, (
                f"{wf.name} step '{name}' records {var}=$rc then exits 0 with "
                f"no later gate step failing on {var} != 0 — a red check would "
                f"report green"
            )


def test_nightly_e2e_gate_fails_on_failed_journeys() -> None:
    """REGRESSION PIN (TASK-A10): the journeys step must exit non-zero when
    pytest fails — commit 26c5d9ef originally ended it with `exit 0`."""
    text = (WORKFLOWS / "nightly-e2e.yml").read_text(encoding="utf-8")
    m = re.search(r"Run client E2E journeys.*?(?=\n      - name:)", text, re.S)
    assert m, "journeys gate step not found in nightly-e2e.yml"
    body = m.group(0)
    assert "rc=$?" in body, "journeys step must capture the pytest rc"
    assert re.search(r"if \[ \"\$rc\" -ne 0 \]", body), "journeys step must check rc"
    assert "exit 1" in body, "journeys step must fail the job on rc != 0"
    # the swallow must not come back: no bare `exit 0` OUTSIDE the rc-failure
    # branch after the rc check (the trailing `exit 0` is the success path —
    # the `exit 1` inside the if-branch must be present and reachable).
    tail = body.split('if [ "$rc" -ne 0 ]', 1)[1]
    then_block = tail.split("fi", 1)[0]
    assert "exit 1" in then_block, (
        "journeys rc-failure branch lost its `exit 1` — the gate is being swallowed again"
    )
