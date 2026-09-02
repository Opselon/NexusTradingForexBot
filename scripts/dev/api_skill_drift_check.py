"""APISkill drift validator (Phase 29): checks agents/APISkill.md against live OpenAPI.

Detects:
  - current /api/v1 operation missing from APISkill (by METHOD PATH)
  - APISkill-documented current operation that no longer exists
  - operation-count drift (APISkill claimed count != live count)
  - legacy routes marked as current (paths documented as /api/v1/... that are not)

Exit 0 = in sync, 1 = drift (documentation/contract defect).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

SKILL = ROOT / "agents/APISkill.md"


def live_operations() -> set[str]:
    from nexus_scalp.web.api_v1_wiring import create_v1_app

    spec = create_v1_app().openapi()
    ops: set[str] = set()
    for path, item in spec["paths"].items():
        for method in item:
            if method != "parameters":
                ops.add(f"{method.upper()} {path}")
    return ops


def documented_operations(text: str) -> set[str]:
    """Current operations are documented in the machine-registry table rows
    (``| GET | /api/v1/... |``) and the per-domain section headers
    (``#### GET /api/v1/...``). Both forms are parsed; prose curl examples are
    deliberately NOT counted (they may carry query strings)."""
    ops: set[str] = set()
    for m in re.finditer(r"^\|\s*(GET|POST)\s*\|\s*(/api/v1/[^\s|`]+)", text, re.M):
        ops.add(f"{m.group(1)} {m.group(2)}")
    for m in re.finditer(r"####\s*(GET|POST)\s+(/api/v1/[^\s`]+)", text):
        ops.add(f"{m.group(1)} {m.group(2)}")
    return ops


def main() -> int:
    if not SKILL.exists():
        print(f"APISkill missing: {SKILL}")
        return 1
    text = SKILL.read_text(encoding="utf-8")
    live = live_operations()
    # Exclude machine-registry markers of removed endpoints: the skill's
    # 'Removed / Superseded' section is allowed to mention old paths; we only
    # enforce coverage for the CANONICAL MAP + REGISTRY sections.
    canonical_zone = text.split("## 4. MACHINE-READABLE REGISTRY", 1)[-1]
    documented = documented_operations(canonical_zone)

    missing = sorted(live - documented)
    extra = sorted(documented - live)

    count_m = re.search(r"(\d+)\s+documented operations", text)
    problems: list[str] = []
    if missing:
        problems.append(f"current operations missing from APISkill ({len(missing)}): {missing}")
    if extra:
        problems.append(
            f"APISkill documents non-existent current operations ({len(extra)}): {extra}"
        )
    if count_m and int(count_m.group(1)) != len(live):
        problems.append(f"APISkill claims {count_m.group(1)} operations; live count is {len(live)}")

    if problems:
        print("APISKILL DRIFT = FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"APISKILL DRIFT = PASS ({len(live)}/{len(live)} live operations documented)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
