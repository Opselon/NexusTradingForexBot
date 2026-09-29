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

    print(
        "provider={} status={} soak={}s api_queries={} db_queries={} errors={} warnings={} tracebacks={}".format(
            data.get("provider", "?"),
            data.get("status", "?"),
            data.get("actual_soak_sec", 0),
            data.get("api", {}).get("query_count_total", 0),
            data.get("database", {}).get("query_count_total", 0),
            len(errors),
            len(warnings),
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

    for tb in tracebacks[:10]:
        source = tb.get("source") or {}
        suffix = " [{}:{}]".format(source.get("file"), source.get("line")) if source else ""
        trace = "\n".join(str(x) for x in tb.get("lines", [])[-12:])
        print("::error title=Runtime traceback {}::{}{}".format(tb.get("traceback_id", "?"), trace[:4000], suffix))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
