# BUG-304: Dependency-drift gate RED on main — stale requirements.lock after upstream nvidia-nvjitlink release

**Run:** 2026-09-16T2241Z_run7 (Role 7 — QA/Tests)
**Repo state at start:** local /tmp/NexusTradingForexBot on foreign branch `release-9.0.14` (a release session); origin/main = 8416aa1c (PR #246, merged 20:51:31Z).

## Defect (P0 — blocks the main gate)

At main HEAD 8416aa1c the **Dependency drift (lock vs pyproject)** check is RED (run 35149043181, job 104972368369):

```
DEPENDENCY DRIFT DETECTED:
  - requirements.lock is STALE vs pyproject.toml (uv pip compile output differs) - run: python scripts/ci/check_dependency_drift.py --regen
```

PR #246 did not touch pyproject.toml, requirements.lock, or requirements.txt — the red is NOT caused by that PR's changes.

## Root cause (file:line evidence)

- The lock carries `nvidia-nvjitlink==13.4.52` with **no upper bound** (pulled transitively via torch; requirements.lock:~nvjitlink lines, marker arm `(platform_machine == 'aarch64' and sys_platform == 'linux') or (platform_machine == 'x86_64' and sys_platform == 'linux')`).
- Upstream published **13.4.92** (new wheels + new sha256 hashes). Any fresh `uv pip compile` now resolves nvjitlink to 13.4.92, so the committed lock reads as stale.
- This is the **genuine-staleness shape**, not the known 3.12-marker false positive: the regen diff touches ONLY the nvjitlink pin (13.4.52 → 13.4.92, 4 hashes) — marker arms for 3.11 (numpy 2.4.6, tomli) are intact, and local uv is 0.12.10, exactly what CI ran.

## Fix (minimal)

Regenerated the lock from pyproject.toml:
- `python scripts/ci/check_dependency_drift.py --regen` → 1 file changed: requirements.lock, 5 insertions/5 deletions (nvjitlink 13.4.52→13.4.92 + hashes).
- requirements.txt unchanged (nvjitlink is not a direct pin there).

## Verification (all run locally, real output)

1. `check_dependency_drift.py` (check mode): `dependency drift check: OK - requirements.lock matches pyproject.toml resolution (98 pins), requirements.txt consistent with lock` — exit 0.
2. Regression test: `PYTHONPATH=src:. .venv-linux/bin/python -m pytest tests/unit/test_dependency_drift_resolver.py -q` → **3 passed**.
3. Interpreter: repo slim venv Python 3.11.16 (matches CI's 3.11.16 setup-python pin).

## Landing

Branch-protected main → landed as PR **#248** (`swarm/deplock-nvjitlink-bump`, commit 9feaf5d3, squash-merge).

## Follow-ups

- Nothing to do for nvjitlink itself: it is a transitive torch dep; next upstream bump will re-trigger the same regen. Optional hardening: consider capping or auto-regen policy — out of scope for this run.
- Note for the next release (9.0.15): the tag build will now pick up nvjitlink 13.4.92 in lock-based installs.
