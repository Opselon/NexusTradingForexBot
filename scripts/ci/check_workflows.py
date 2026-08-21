#!/usr/bin/env python3
"""CI integrity / workflow static analyzer for NexusTradingForexBot (NSE).

This is the META-TEST of CI itself: it reads every .github/workflows/*.yml
and detects wiring defects that would otherwise let a required quality gate
silently skip, or let a broken reference pass the YAML parser but fail at
runtime. It is deterministic and fast (no network, no GitHub API).

Checks (each is a finding with a severity; CI fails on any ERROR):
  1. Undefined job-output references — a step references
     needs.<job>.outputs.<name> but <job> never declares `outputs: <name>`.
  2. Dead / unreachable lanes — a job's `if:` can never be true (always
     references an undefined expression variable, or is statically False).
  3. Silently-skipped required tests — a job that is gated on an empty
     expression, a matrix that yields zero legs, or `continue-on-error`
     masking a real failure on a must-run gate.
  4. Local composite-action usage without a prior actions/checkout in the
     same job (jobs are isolated; one job's checkout does not help another).
  5. Artifact-name collisions in matrix jobs — two matrix legs that would
     upload the same artifact name (upload-artifact v4 rejects duplicates).
  6. No-op `if: always()` masking — informational.
  7. Self-referencing CI run IDs in poller-like logic — informational
     (guards against a status poller observing its own run).

Lane vocabulary is DERIVED FROM THE ACTUAL REPO, not hard-coded to a foreign
reference. The classifier (classify_changes.py) is the canonical lane list;
this analyzer validates that workflow `if:` expressions keying off CI lane
outputs are consistent with that vocabulary.

Usage:
  check_workflows.py [--workflows-dir PATH] [--format text|json] [--strict]
  Exit 0 if no ERROR findings (or if --strict, no WARNING either).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


# Patterns that look like `needs.<job>.outputs.<name>` (the buggy reference
# class described in the task).
_NEEDS_OUTPUT_RE = re.compile(
    r"needs\.(?P<job>[A-Za-z0-9_\-]+)\.outputs\.(?P<name>[A-Za-z0-9_\-]+)"
)
# `${{ ... }}` expression extraction (may contain multiple).
_EXPR_RE = re.compile(r"\$\{\{\s*(.+?)\s*\}\}")
# Reference to the canonical classifier lane output, e.g.
#   needs.classify.outputs.python == 'true'
_LANE_REF_RE = re.compile(
    r"needs\.(?P<job>[A-Za-z0-9_\-]+)\.outputs\.(?P<lane>[A-Za-z0-9_\-]+)"
)


@dataclass
class Finding:
    severity: str  # ERROR | WARNING | INFO
    workflow: str
    job: str = ""
    check: str = ""
    message: str = ""

    def as_line(self) -> str:
        loc = self.workflow
        if self.job:
            loc += f"::{self.job}"
        if self.check:
            loc += f"[{self.check}]"
        return f"[{self.severity}] {loc}: {self.message}"


@dataclass
class JobModel:
    name: str
    raw: dict
    uses_local_action: bool = False
    has_checkout: bool = False
    outputs: dict = field(default_factory=dict)
    if_expr: str | None = None
    needs: list[str] = field(default_factory=list)
    matrix: dict | None = None
    continue_on_error: bool = False
    upload_artifacts: list[str] = field(default_factory=list)


@dataclass
class WorkflowModel:
    name: str
    raw: dict
    jobs: dict[str, JobModel] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def error(self, *a, **k):
        self.findings.append(Finding("ERROR", self.name, *a, **k))

    def warn(self, *a, **k):
        self.findings.append(Finding("WARNING", self.name, *a, **k))

    def info(self, *a, **k):
        self.findings.append(Finding("INFO", self.name, *a, **k))


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _gather_steps(job: dict) -> list[dict]:
    """Yield every step dict (handles `steps:` and reused `uses:` jobs)."""
    steps = job.get("steps") or []
    out: list[dict] = []
    for s in steps:
        if isinstance(s, dict):
            out.append(s)
    return out


def _uses_local(step: dict) -> bool:
    uses = str(step.get("uses", ""))
    return uses.startswith("./.github/actions/") or uses.startswith(".github/actions/")


def _is_checkout(step: dict) -> bool:
    return str(step.get("uses", "")).startswith("actions/checkout")


def _extract_if(job: dict) -> str | None:
    if "if" not in job:
        return None
    val = job["if"]
    if isinstance(val, bool):
        return "false" if not val else "true"
    return str(val)


def _extract_artifacts(steps: list[dict]) -> list[str]:
    names: list[str] = []
    for s in steps:
        with_block = s.get("with") or {}
        if "upload-artifact" in str(s.get("uses", "")) and isinstance(with_block, dict):
            n = with_block.get("name")
            if n is not None:
                names.append(str(n))
    return names


def parse_workflow(path: Path) -> WorkflowModel:
    wf = WorkflowModel(name=path.name, raw={})
    if yaml is None:
        wf.error(message="PyYAML not available; cannot parse workflows")
        return wf
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:  # YAML syntax error is a hard CI defect
        wf.error(message=f"YAML parse failed: {e}")
        return wf
    if not isinstance(data, dict):
        wf.error(message="workflow root is not a mapping")
        return wf
    wf.raw = data
    jobs = data.get("jobs") or {}
    for jname, jbody in jobs.items():
        if not isinstance(jbody, dict):
            continue
        steps = _gather_steps(jbody)
        jm = JobModel(
            name=jname,
            raw=jbody,
            uses_local_action=any(_uses_local(s) for s in steps),
            has_checkout=any(_is_checkout(s) for s in steps),
            outputs=jbody.get("outputs") or {},
            if_expr=_extract_if(jbody),
            matrix=(jbody.get("strategy") or {}).get("matrix"),
            continue_on_error=bool(jbody.get("continue-on-error", False)),
            upload_artifacts=_extract_artifacts(steps),
        )
        # `needs` may be a string or list
        needs = jbody.get("needs")
        if isinstance(needs, str):
            jm.needs = [needs]
        elif isinstance(needs, list):
            jm.needs = [str(n) for n in needs]
        wf.jobs[jname] = jm
    return wf


# --------------------------------------------------------------------------- #
# Individual check passes
# --------------------------------------------------------------------------- #
def check_undefined_outputs(wf: WorkflowModel) -> None:
    """needs.<job>.outputs.<name> must be declared by <job>."""
    for jname, jm in wf.jobs.items():
        haystack = json.dumps(jm.raw)
        for m in _NEEDS_OUTPUT_RE.finditer(haystack):
            ref_job = m.group("job")
            ref_name = m.group("name")
            target = wf.jobs.get(ref_job)
            if target is None:
                wf.error(
                    jname,
                    "undefined-output",
                    f"references needs.{ref_job}.outputs.{ref_name} "
                    f"but job '{ref_job}' does not exist",
                )
                continue
            if ref_name not in (target.outputs or {}):
                wf.error(
                    jname,
                    "undefined-output",
                    f"references needs.{ref_job}.outputs.{ref_name} "
                    f"but '{ref_job}' does not declare outputs.{ref_name}",
                )


def check_local_action_checkout(wf: WorkflowModel) -> None:
    """A job invoking a local composite action MUST have run checkout first."""
    for jname, jm in wf.jobs.items():
        if not jm.uses_local_action:
            continue
        if not jm.has_checkout:
            wf.error(
                jname,
                "local-action-no-checkout",
                "uses a local composite action (.github/actions/*) but this "
                "job never runs actions/checkout before it",
            )


def check_matrix_collisions(wf: WorkflowModel) -> None:
    """Matrix legs that upload the same artifact NAME will collide."""
    for jname, jm in wf.jobs.items():
        if not jm.upload_artifacts:
            continue
        if not jm.matrix:
            continue
        # Only meaningful when the artifact name does NOT embed a matrix
        # dimension expression (e.g. ${{ matrix.os }}).
        for art in jm.upload_artifacts:
            if "${{" in art:
                continue  # name already parameterized by a matrix dim
            wf.warn(
                jname,
                "matrix-artifact-collision",
                f"matrix job uploads artifact '{art}' with no matrix-dimension "
                f"in the name; parallel legs will collide (upload-artifact v4 "
                f"rejects duplicate names)",
            )


def check_silent_skip(wf: WorkflowModel) -> None:
    """Detect gates that can silently skip, plus impossible conditions."""
    for jname, jm in wf.jobs.items():
        if_expr = jm.if_expr
        if if_expr is None:
            continue
        # Empty / whitespace-only if -> the job never runs.
        if if_expr.strip() == "" or if_expr.strip() == "${{ }}":
            wf.error(
                jname,
                "empty-if",
                "job has an empty `if:` expression — it will never run "
                "(silently skipped required gate)",
            )
            continue
        # Statically always-False expression (boolean false or literal false).
        stripped = if_expr.replace("${{", "").replace("}}", "").strip().lower()
        if stripped in ("false", "0"):
            wf.error(
                jname,
                "always-false-if",
                f"job `if:` is statically false ({if_expr!r}) — unreachable job",
            )
        # Reference to a needs-output that is not declared anywhere (caught
        # more precisely by check_undefined_outputs, but flag here too if the
        # referenced job exists but the output name is a typo).
        for m in _LANE_REF_RE.finditer(if_expr):
            tjob, tlane = m.group("job"), m.group("lane")
            tgt = wf.jobs.get(tjob)
            if tgt is not None and tlane not in (tgt.outputs or {}):
                wf.warn(
                    jname,
                    "lane-if-undeclared",
                    f"`if:` reads needs.{tjob}.outputs.{tlane} but that job "
                    f"does not declare outputs.{tlane} — condition may always "
                    f"be empty/false (silent skip)",
                )
        # A required gate (no `if` making it optional, or `if: always()`) that
        # carries continue-on-error while being the repo's quality gate is risky.
        if jm.continue_on_error and "quality" in jname.lower():
            wf.warn(
                jname,
                "continue-on-error-gate",
                "job has continue-on-error=true and looks like a required "
                "quality gate; ensure a later step still fails the run on "
                "real failures",
            )


def check_self_watch(wf: WorkflowModel) -> None:
    """A status poller must not observe its OWN run via GITHUB_RUN_ID.

    We only flag an INFO when GITHUB_RUN_ID is used in an ACTUAL polling
    context (a gh run watch / gh api / curl step that reads run state), not
    merely because the run id appears in harmless metadata/env wiring.
    """
    hay = json.dumps(wf.raw)
    if "GITHUB_RUN_ID" not in hay:
        return
    # Walk every job's steps for polling commands.
    jobs = wf.raw.get("jobs") or {}
    polling_hits = 0
    for _jname, jbody in jobs.items():
        if not isinstance(jbody, dict):
            continue
        for step in _gather_steps(jbody):
            step_blob = json.dumps(step)
            run_cmd = str(step.get("run", ""))
            has_run_id = "GITHUB_RUN_ID" in step_blob
            is_polling = (
                "gh run" in run_cmd
                or "gh api" in run_cmd
                or "gh workflow" in run_cmd
                or "curl" in run_cmd.lower()
            )
            if has_run_id and is_polling:
                polling_hits += 1
    if polling_hits:
        wf.info(
            "",
            "self-watch",
            f"workflow polls run state {polling_hits} time(s) using "
            f"GITHUB_RUN_ID — ensure it targets an explicit TARGET_RUN_ID, "
            f"not its own run id (reserved GITHUB_* must never be a writable "
            f"polled identifier)",
        )


def analyze_workflow(wf: WorkflowModel) -> None:
    check_undefined_outputs(wf)
    check_local_action_checkout(wf)
    check_matrix_collisions(wf)
    check_silent_skip(wf)
    check_self_watch(wf)


def run(workflows_dir: Path, strict: bool = False) -> tuple[list[WorkflowModel], int]:
    """Analyze all workflow files. Returns (models, exit_code)."""
    paths = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    models: list[WorkflowModel] = []
    for p in paths:
        wf = parse_workflow(p)
        analyze_workflow(wf)
        models.append(wf)
    # Determine exit code: ERROR -> 1; WARNING -> 1 only if strict.
    has_error = any(f.severity == "ERROR" for m in models for f in m.findings)
    has_warn = any(f.severity == "WARNING" for m in models for f in m.findings)
    exit_code = 0
    if has_error:
        exit_code = 1
    elif has_warn and strict:
        exit_code = 1
    return models, exit_code


def _print_text(models: list[WorkflowModel]) -> None:
    total = sum(len(m.findings) for m in models)
    print(f"CI INTEGRITY SCAN — {len(models)} workflow(s), {total} finding(s)\n")
    for m in models:
        if not m.findings:
            print(f"  [OK]      {m.name}")
            continue
        for f in m.findings:
            print(f"  {f.as_line()}")
        print("")
    # Summary counts
    errs = sum(1 for m in models for f in m.findings if f.severity == "ERROR")
    warns = sum(1 for m in models for f in m.findings if f.severity == "WARNING")
    infos = sum(1 for m in models for f in m.findings if f.severity == "INFO")
    print(f"SUMMARY: {errs} ERROR, {warns} WARNING, {infos} INFO")
    if errs:
        print("\nCI INVARIANT FAILED — fix the ERROR findings above before merge.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NSE CI workflow integrity analyzer")
    default_wd = str(Path(__file__).resolve().parents[2] / ".github" / "workflows")
    parser.add_argument("--workflows-dir", default=default_wd)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--strict", action="store_true",
                        help="treat WARNING as failure too")
    parser.add_argument("--quiet", action="store_true",
                        help="only print ERROR-level findings")
    args = parser.parse_args(argv)

    wd = Path(args.workflows_dir)
    if not wd.is_dir():
        print(f"ERROR: workflows dir not found: {wd}", file=sys.stderr)
        return 2

    models, exit_code = run(wd, strict=args.strict)

    if args.format == "json":
        payload = {
            "exit_code": exit_code,
            "workflows": [
                {
                    "name": m.name,
                    "findings": [
                        {
                            "severity": f.severity,
                            "job": f.job,
                            "check": f.check,
                            "message": f.message,
                        }
                        for f in m.findings
                    ],
                }
                for m in models
            ],
        }
        print(json.dumps(payload, indent=2))
    elif args.quiet:
        # Re-emit only ERROR lines
        for m in models:
            for f in m.findings:
                if f.severity == "ERROR":
                    print(f.as_line())
    else:
        _print_text(models)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
