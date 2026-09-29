#!/usr/bin/env python3
"""Render provider runtime evidence into GitHub annotations and a step summary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()

    if not args.evidence.exists():
        print("::error title=Runtime evidence missing::" + str(args.evidence))
        return 0

    data: dict[str, Any] = json.loads(args.evidence.read_text(encoding="utf-8"))
    obs = data.get("observability", {})
    errors = list(obs.get("errors", []))
    warnings = list(obs.get("warnings", []))
    tracebacks = list(obs.get("tracebacks", []))
    findings = list(data.get("findings", []))

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
    finding_errors = [item for item in findings if item.get("type") in error_types]
    finding_warnings = [
        item
        for item in findings
        if item.get("type") not in error_types
        and item.get("type") not in {"process-log", "traceback"}
    ]

    print(
        "provider={} status={} soak={}s api_queries={} db_queries={} errors={} warnings={} tracebacks={}".format(
            data.get("provider", "?"),
            data.get("status", "?"),
            data.get("actual_soak_sec", 0),
            data.get("api", {}).get("query_count_total", 0),
            data.get("database", {}).get("query_count_total", 0),
            max(len(errors), len(finding_errors)),
            len(warnings) + len(finding_warnings),
            len(tracebacks),
        )
    )

    for event in errors[:30]:
        source = event.get("source") or {}
        suffix = " [{}:{}]".format(source.get("file"), source.get("line")) if source else ""
        message = str(event.get("message") or event.get("error") or event)
        print("::error title=Provider runtime error::{}{}".format(message[:3000], suffix))

    for event in warnings[:30]:
        source = event.get("source") or {}
        suffix = " [{}:{}]".format(source.get("file"), source.get("line")) if source else ""
        message = str(event.get("message") or event.get("error") or event)
        print("::warning title=Provider runtime warning::{}{}".format(message[:3000], suffix))

    for item in finding_errors[:30]:
        message = str(item.get("message") or item.get("error") or item)
        print("::error title=Runtime certification finding::{}".format(message[:3000]))
    for item in finding_warnings[:30]:
        message = str(item.get("message") or item.get("error") or item)
        print("::warning title=Runtime certification warning::{}".format(message[:3000]))

    for tb in tracebacks[:10]:
        source = tb.get("source") or {}
        suffix = " [{}:{}]".format(source.get("file"), source.get("line")) if source else ""
        trace = "\n".join(str(x) for x in tb.get("lines", [])[-12:])
        print("::error title=Runtime traceback {}::{}{}".format(tb.get("traceback_id", "?"), trace[:4000], suffix))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
