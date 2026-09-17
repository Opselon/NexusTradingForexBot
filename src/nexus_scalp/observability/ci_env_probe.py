"""CI environment probe for dependency health before importing observability."""

from __future__ import annotations

import json
import sys

REQUIRED = {"structlog", "pydantic", "pydantic-settings", "PyYAML", "numpy"}

MODULE_MAP = {
    "PyYAML": "yaml",
    "pydantic-settings": "pydantic_settings",
}


def _import_check(pkg: str) -> bool:
    mod_name = MODULE_MAP.get(pkg, pkg)
    try:
        __import__(mod_name)
        return True
    except ImportError:
        return False


def probe() -> dict[str, object]:
    imports: dict[str, bool] = {p: _import_check(p) for p in REQUIRED}
    status: dict[str, object] = {
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "imports": imports,
        "missing": sorted([p for p in REQUIRED if not imports[p]]),
    }
    return status


if __name__ == "__main__":
    print(json.dumps(probe(), indent=2, sort_keys=True))
