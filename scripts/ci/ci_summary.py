#!/usr/bin/env python3
"""Whole-run evidence collector for the FINAL CI summary lane (BUG-300).

Runs in .github/workflows/ci-summary.yml (workflow_run — fires AFTER every
watched workflow completes, i.e. the very end of the pipeline). Assembles
one JSON evidence bundle from GitHub APIs + the target run's ci-results
artifact, so the AI triage (ci_ai_triage) can answer: why failed / why
succeeded / is the PR good to merge / what to check next.

  python scripts/ci/ci_summary.py --out evidence.json [--kind auto]

Kind is auto-detected from the watched workflow name unless --kind given.
Exit code is always 0; missing inputs degrade to partial evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _sh(args: list[str], timeout: int = 120) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _json_sh(args: list[str], timeout: int = 120):
    out = _sh(args, timeout)
    try:
        return json.loads(out) if out.strip() else None
    except Exception:
        return None


def detect_kind(workflow_name: str, event: str, pr_number: str) -> str:
    n = (workflow_name or "").lower()
    if pr_number and (
        "ci" in n
        or "test" in n
        or "doc" in n
        or "lock" in n
        or "osv" in n
        or "security" in n
        or "codeql" in n
        or "trivy" in n
    ):
        return "pr-analysis"
    if "release" in n or "nightly" in n or "e2e" in n or "assurance" in n:
        return "release-failure"
    if "security" in n or "osv" in n or "codeql" in n:
        return "security"
    return "ci-failure"


def _target_run_id() -> str:
    # workflow_run payloads expose the completed run under run_id input/env.
    return os.environ.get("TARGET_RUN_ID", "") or os.environ.get("GITHUB_RUN_ID", "")


def collect(pr_number: str, kind: str) -> dict:
    run_id = _target_run_id()
    ctx: dict = {"kind": kind, "repository": os.environ.get("GITHUB_REPOSITORY", "")}

    run = (
        _json_sh(
            [
                "gh",
                "run",
                "view",
                run_id,
                "--json",
                "displayTitle,conclusion,workflowName,headBranch,event,headSha,url",
                "--repo",
                os.environ.get("GITHUB_REPOSITORY", ""),
            ]
        )
        if run_id
        else None
    )
    if run:
        ctx["run"] = {
            "title": run.get("displayTitle", ""),
            "conclusion": run.get("conclusion", ""),
            "workflow": run.get("workflowName", ""),
            "branch": run.get("headBranch", ""),
            "event": run.get("event", ""),
            "sha": (run.get("headSha") or "")[:10],
            "url": run.get("url", ""),
        }

    if run_id:
        jobs = (
            _json_sh(
                [
                    "gh",
                    "run",
                    "view",
                    run_id,
                    "--json",
                    "jobs",
                    "--repo",
                    os.environ.get("GITHUB_REPOSITORY", ""),
                ]
            )
            or {}
        )
        brief = {}
        for j in jobs.get("jobs") or []:
            steps = {s.get("name", "?"): s.get("conclusion", "") for s in j.get("steps") or []}
            failed_steps = [k for k, v in steps.items() if v in ("failure", "timed_out")]
            brief[j.get("name", j.get("id", "?"))] = {
                "conclusion": j.get("conclusion", ""),
                "failed_steps": failed_steps[:8],
            }
        ctx["jobs"] = brief
        try:
            checks = _json_sh(
                [
                    "gh",
                    "run",
                    "view",
                    run_id,
                    "--json",
                    "conclusion",
                    "--repo",
                    os.environ.get("GITHUB_REPOSITORY", ""),
                ]
            )
            if checks:
                ctx["run_conclusion"] = checks.get("conclusion", "")
        except Exception:
            pass

    # ci-results artifact (best-effort): junit + per-check statuses
    tmp = tempfile.mkdtemp(prefix="ci-results-")
    if run_id:
        _sh(
            [
                "gh",
                "run",
                "download",
                run_id,
                "--pattern",
                "ci-results*",
                "--path",
                tmp,
                "--repo",
                os.environ.get("GITHUB_REPOSITORY", ""),
            ]
        )
    results_root = Path(tmp)
    sub = (
        next((p.parent for p in results_root.rglob("run-info")), None)
        if results_root.exists()
        else None
    )
    if sub:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
            from nexus_scalp.observability.ci_ai_triage import _context_from_results

            extra = _context_from_results(kind, sub)
            ctx["ci_results"] = {
                k: extra.get(k)
                for k in (
                    "checks",
                    "junit",
                    "failed_tests",
                    "coverage_percent",
                    "pytest_tail",
                    "mypy_tail",
                    "ruff_tail",
                )
                if extra.get(k)
            }
        except Exception:
            pass

    # PR evidence: diff stats + review state + comments
    if pr_number:
        pr = _json_sh(
            [
                "gh",
                "pr",
                "view",
                pr_number,
                "--json",
                "title,author,additions,deletions,changedFiles,files,mergeable,"
                "reviewDecision,headRefName,baseRefName,isDraft,url",
                "--repo",
                os.environ.get("GITHUB_REPOSITORY", ""),
            ]
        )
        if pr:
            files = [
                {
                    "path": f.get("path"),
                    "additions": f.get("additions"),
                    "deletions": f.get("deletions"),
                }
                for f in (pr.get("files") or [])[:120]
            ]
            ctx["pr"] = {
                "number": pr_number,
                "title": (pr.get("title") or "")[:200],
                "author": (pr.get("author") or {}).get("login", ""),
                "head": pr.get("headRefName", ""),
                "base": pr.get("baseRefName", ""),
                "draft": pr.get("isDraft", False),
                "additions": pr.get("additions", 0),
                "deletions": pr.get("deletions", 0),
                "changed_files": pr.get("changedFiles", 0),
                "files": files,
                "mergeable": pr.get("mergeable", ""),
                "review_decision": pr.get("reviewDecision", ""),
                "url": pr.get("url", ""),
            }
            comments = (
                _json_sh(
                    [
                        "gh",
                        "pr",
                        "view",
                        pr_number,
                        "--json",
                        "comments",
                        "--repo",
                        os.environ.get("GITHUB_REPOSITORY", ""),
                    ]
                )
                or {}
            )
            ctx["pr"]["recent_comments"] = [
                (c.get("body") or "")[:200] for c in (comments.get("comments") or [])[-5:]
            ]
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                import classify_changes

                lanes: set[str] = set()
                for f in files:
                    lanes |= set(classify_changes.classify_file(f["path"] or ""))
                ctx["pr"]["lanes"] = sorted(lanes)
            except Exception:
                pass
    return ctx


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="NSE final-run evidence bundle")
    ap.add_argument("--out", required=True)
    ap.add_argument("--kind", default="auto")
    ap.add_argument("--pr", default=os.environ.get("PR_NUMBER", ""))
    args = ap.parse_args(argv)

    wf_name = os.environ.get("TARGET_WORKFLOW", "") or os.environ.get("GITHUB_WORKFLOW", "")
    event = os.environ.get("TARGET_EVENT", "") or os.environ.get("GITHUB_EVENT_NAME", "")
    kind = args.kind if args.kind != "auto" else detect_kind(wf_name, event, args.pr)
    ctx = collect(args.pr, kind)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ctx, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(f"CI-SUMMARY evidence -> {out} (kind={kind}, keys={sorted(ctx)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
