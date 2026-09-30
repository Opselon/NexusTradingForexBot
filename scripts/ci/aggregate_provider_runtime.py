#!/usr/bin/env python3
"""Aggregate the two provider runtime certification reports."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    files = sorted(args.root.glob("*/provider_runtime_soak.json"))
    if not files:
        print("::error title=Runtime provider evidence missing::No provider report files found")
        return 1

    reports: list[dict[str, Any]] = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    failed = [report for report in reports if report.get("status") == "FAIL"]
    warned = [report for report in reports if report.get("status") == "PASS_WITH_WARNINGS"]
    overall = "FAIL" if failed else ("PASS_WITH_WARNINGS" if warned else "PASS")

    lines = [
        "## Provider Runtime Certification",
        "",
        f"Overall: **{overall}**",
        "",
        "| Provider | Status | Soak | API queries | DB queries | Log errors | Warnings | Tracebacks |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    for report in reports:
        obs = report.get("observability", {})
        findings = list(report.get("findings", []))
        error_types = {
            "process-log",
            "traceback",
            "api",
            "database-query",
            "process-exit",
            "harness",
            "coverage-floor",
            "soak-duration",
        }
        error_findings = [item for item in findings if item.get("type") in error_types]
        warning_findings = [
            item
            for item in findings
            if item.get("type") not in error_types
            and item.get("type") not in {"process-log", "traceback"}
        ]
        lines.append(
            f"| {report.get('provider', '?')} | {report.get('status', '?')} | "
            f"{float(report.get('actual_soak_sec', 0)):.1f}s | "
            f"{int(report.get('api', {}).get('query_count_total', 0))} | "
            f"{int(report.get('database', {}).get('query_count_total', 0))} | "
            f"{max(len(obs.get('errors', [])), len(error_findings))} | "
            f"{len(obs.get('warnings', [])) + len(warning_findings)} | "
            f"{len(obs.get('tracebacks', []))} |"
        )

    payload = {
        "status": overall,
        "providers": reports,
        "provider_count": len(reports),
        "failed_provider_count": len(failed),
        "warning_provider_count": len(warned),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )

    summary = "\n".join(lines) + "\n"
    print(summary)
    summary_path = Path(os.environ["GITHUB_STEP_SUMMARY"])
    with summary_path.open("a", encoding="utf-8") as handle:
        handle.write(summary)

    for report in failed:
        print(
            f"::error title=Provider runtime certification failed::"
            f"{report.get('provider', '?')}: status={report.get('status')}"
        )
    for report in warned:
        print(
            f"::warning title=Provider runtime certification has warnings::"
            f"{report.get('provider', '?')} has warnings; inspect provider_runtime_soak.json"
        )

    return 0 if overall != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
