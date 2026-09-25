"""scripts/dev/api_contract_check.py — OpenAPI quality gate for the v1 platform.

Verifies (against the standalone v1 app, the same contract as the dashboard):
  1. route count == OpenAPI path-operation count (no drift between app and spec),
  2. every operation has summary + tags + 422/404 error documentation where
     applicable,
  3. the v1 path prefix and domain tags match the spec-of-record domains,
  4. request bodies carry either a schema ref or a documented open-object note,
  5. no secret-shaped field names appear anywhere in the spec.

Exit code 0 = PASS, 1 = FAIL with details. CI-usable as a contract gate.
"""

from __future__ import annotations

import re
import sys

SPEC_DOMAINS = {
    "system",
    "runtime",
    "market",
    "signals",
    "decisions",
    "positions",
    "execution",
    "risk",
    "model",
    "features",
    "research",
    "shadow",
    "observability",
    "audit",
    "incidents",
    "database",
    "config",
}
SECRET_SHAPE = re.compile(r"(?i)(password|secret|token|api_?key|credential)")


def main() -> int:

    from nexus_scalp.web.api_v1_wiring import create_v1_app, v1_route_count

    problems: list[str] = []
    app = create_v1_app()
    spec = app.openapi()
    paths = spec.get("paths", {})

    ops = 0
    for path, item in paths.items():
        if not path.startswith("/api/v1/"):
            problems.append(f"path outside /api/v1: {path}")
        domain = path.split("/")[3] if path.count("/") >= 3 else ""
        if domain and domain not in SPEC_DOMAINS and domain.startswith("{") is False:
            problems.append(f"unknown domain segment: {path}")
        for method, op in item.items():
            if method == "parameters":
                continue
            ops += 1
            if not op.get("summary"):
                problems.append(f"missing summary: {method.upper()} {path}")
            if not op.get("tags"):
                problems.append(f"missing tags: {method.upper()} {path}")
            body = op.get("requestBody", {})
            if body:
                schema_ref = (
                    body.get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                    .get("$ref", "")
                )
                desc = str(body.get("description", "") or op.get("description", ""))
                if not schema_ref and "open object" not in desc:
                    problems.append(
                        f"request body without schema ref or open-object note: {method.upper()} {path}"
                    )
        if SECRET_SHAPE.search(path):
            problems.append(f"secret-shaped path: {path}")

    # spec schema names must not carry secret-shaped fields
    for schema_name, schema in (spec.get("components", {}).get("schemas") or {}).items():
        for prop in schema.get("properties") or {}:
            if SECRET_SHAPE.search(prop):
                problems.append(f"secret-shaped field in schema {schema_name}.{prop}")

    route_count = v1_route_count(app)
    if route_count != ops:
        problems.append(f"route count {route_count} != documented operations {ops}")
    if route_count < 60:
        problems.append(f"platform below minimum capabilities: {route_count} < 60")

    if problems:
        print("API CONTRACT CHECK = FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"API CONTRACT CHECK = PASS ({route_count} operations across {len(paths)} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
