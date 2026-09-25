"""scripts/dev/api_openapi_snapshot.py — deterministic OpenAPI snapshot for drift detection.

Produces a canonical, sorted JSON artifact containing:
  - api version + product version,
  - path inventory (path -> methods -> summary),
  - per-path schema-name sets (response/request models),
Captured via the STANDALONE v1 app (identical contract surface to the dashboard
mount). Output goes to ``artifacts/api/openapi_snapshot.json`` by default.
Deterministic: sorted keys everywhere, no timestamps in the artifact body.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_snapshot() -> dict:
    from nexus_scalp.release.metadata import get_version_info
    from nexus_scalp.web.api_v1_wiring import create_v1_app

    app = create_v1_app()
    spec = app.openapi()
    paths: dict[str, dict[str, dict]] = {}
    for path in sorted(spec.get("paths", {})):
        ops = spec["paths"][path]
        entry: dict[str, dict] = {}
        for method in sorted(ops):
            if method == "parameters":
                continue
            op = ops[method]
            entry[method] = {
                "summary": op.get("summary", ""),
                "tags": sorted(op.get("tags", [])),
                "request_schema": sorted(
                    (
                        op.get("requestBody", {})
                        .get("content", {})
                        .get("application/json", {})
                        .get("schema", {})
                        .get("$ref", "")
                        .rsplit("/", 1)[-1],
                    )
                )
                if op.get("requestBody")
                else [],
            }
        paths[path] = entry
    version = get_version_info()
    return {
        "api_version": "v1",
        "product": version.get("product"),
        "product_version": version.get("version"),
        "path_count": len(paths),
        "paths": paths,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a deterministic OpenAPI v1 snapshot")
    parser.add_argument(
        "--out",
        default=str(Path("artifacts/api/openapi_snapshot.json")),
        help="output path (default artifacts/api/openapi_snapshot.json)",
    )
    args = parser.parse_args(argv)
    snapshot = build_snapshot()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"snapshot: {snapshot['path_count']} paths -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
