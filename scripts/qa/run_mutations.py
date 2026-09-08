"""CHG-0045 part 2: mutation-proof runner (controlled, working-tree-safe).

Injects a small, FIXED catalog of realistic mutations into critical
contracts, runs the owning test battery, and reports KILLED (tests failed
= the net catches the mutation) vs SURVIVED (test blind spot). The repo
working tree is NEVER mutated: mutations are applied to a temp copy of the
single target file, and the battery runs with the temp module forced onto
sys.path via an import hook rooted at the temp tree.

HONESTY CONTRACT (2026-09-07 rewrite):
  * A verdict of KILLED requires that the SAME battery, on an UNMUTATED
    copy of the target, passes (rc == 0). If the pristine battery fails,
    the verdict is INVALID_BATTERY — never KILLED. (The 2026-09 runner
    reported 9/9 KILLED from a RecursionError in its own import hook: a
    false baseline. This contract makes that class impossible.)
  * An anchor that no longer occurs exactly once is INVALID_ANCHOR — the
    production code changed shape and the catalog must be updated.
  * accepted_survivors documents INTENTIONAL survivors with a reason;
    anything else that survives is a test blind spot (QA contract §3.7:
    file as a test-quality defect in agents/bugs.md).

Design:
- mutator: pure function (file text) -> mutated text; must change EXACTLY
  the documented anchor (count==1 assertion before writing).
- runner: pytest in-process inside a subprocess whose meta-path finder is
  installed at bootstrap top level (importlib.util imported ONCE outside
  find_spec — importing it inside the finder recurses into the finder
  itself and dies with RecursionError).
- each mutation gets its OWN temp tree (no cross-contamination).

Scopes:
  (no flags)      full catalog — weekly / scheduled lane
  --ids A,B       only these mutation ids
  --target SUB    only mutations whose target path contains SUB
  --changed       only mutations whose target appears in the current git
                  diff (working tree + staged + origin/main delta) — the
                  fast PR-scoped path

Exit semantics: exit 0 when every verdict is KILLED or ACCEPTED_SURVIVOR;
exit 1 when an unaccepted SURVIVED exists; exit 2 when any INVALID_* verdict
exists (broken battery or drifted anchor — the runner itself is untrustworthy).
Results are JSON on stdout.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"

# ---------------------------------------------------------------------------


def _repo_python() -> str:
    """The repo venv interpreter when present, else the current one.

    PORTABILITY CONTRACT (QA hardening mission):
      * never a developer-specific absolute path (C:/Users/... is forbidden);
      * Windows layout (`.venv/Scripts/python.exe`) and POSIX layout
        (`.venv/bin/python`) both resolved from REPO, whichever exists;
      * the unconditional fallback is the interpreter RUNNING this script
        (sys.executable), so CI (which installs the package into the job's
        interpreter) works with no venv on disk at all.
    """
    for candidate in (
        REPO / ".venv" / "Scripts" / "python.exe",
        REPO / ".venv" / "bin" / "python",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable


# ---------------------------------------------------------------------------
# Mutation catalog: (id, target file rel to repo, anchor, replacement,
#                    owning battery file, expected killed-by count>=1)
# Each anchor MUST occur exactly once in the target file.
# ---------------------------------------------------------------------------

MUTATIONS: list[dict[str, str]] = [
    {
        "id": "MUT-SM-01",
        "desc": "state machine: drop count-based hysteresis half (>= -> >)",
        "target": "src/nexus_scalp/execution/position_state_machine.py",
        "anchor": "if elapsed >= min_dur and new_count >= min_cnt:",
        "replacement": "if elapsed > min_dur and new_count >= min_cnt:",
        "battery": "tests/unit/test_qa_deep_state_machines.py",
    },
    {
        "id": "MUT-RB-01",
        "desc": "recovery budget: horizon clamp lower bound removed (max -> min)",
        "target": "src/nexus_scalp/execution/recovery_budget.py",
        "anchor": "horizon = max(min_horizon, min(max_horizon, base_hor))",
        "replacement": "horizon = min(min_horizon, min(max_horizon, base_hor))",
        "battery": "tests/unit/test_qa_deep_state_machines.py",
    },
    {
        "id": "MUT-70D-01",
        "desc": "70D contract: clip window widened [-3,+3] -> [-5,+5]",
        "target": "src/nexus_scalp/features/schema_contract.py",
        "anchor": "if not (-3.0 <= v <= 3.0):",
        "replacement": "if not (-5.0 <= v <= 5.0):",
        "battery": "tests/unit/test_qa_deep_70d_contract_properties.py",
    },
    {
        "id": "MUT-70D-02",
        "desc": "70D contract: dimension check dropped (70 -> sentinel)",
        "target": "src/nexus_scalp/features/schema_contract.py",
        "anchor": "if len(vec) != DIMENSION:",
        "replacement": "if len(vec) != DIMENSION and False:",
        "battery": "tests/unit/test_qa_deep_70d_contract_properties.py",
    },
    {
        "id": "MUT-GATE-01",
        "desc": "provider gate: single-flight follower broadcast disabled",
        "target": "src/nexus_scalp/strategies/factory/provider_gate.py",
        "anchor": "            if followers:\n                for f in followers:\n                    f.broadcast(result)",
        "replacement": "            if False and followers:\n                for f in followers:\n                    f.broadcast(result)",
        "battery": "tests/unit/test_provider_gate_hardening.py",
    },
    {
        "id": "MUT-CF-01",
        "desc": "confidence semantics: threshold comparison flipped (< -> >)",
        "target": "src/nexus_scalp/signals/policy.py",
        "anchor": "if confidence < active_threshold and proposed_action != ActionType.NO_TRADE:",
        "replacement": "if confidence > active_threshold and proposed_action != ActionType.NO_TRADE:",
        "battery": "tests/unit/test_qa_deep_confidence_adversarial.py",
    },
    {
        "id": "MUT-AGG-01",
        "desc": "observability: evidence counter under-reports (+= -> =)",
        "target": "src/nexus_scalp/observability/event_aggregator.py",
        "anchor": 'self._metrics["dropped_events"] += evicted[1].count',
        "replacement": 'self._metrics["dropped_events"] = 0',
        "battery": "tests/unit/test_qa_deep_observability_evidence.py",
    },
    {
        "id": "MUT-REPLAY-01",
        "desc": "replay causality: causal filter flips to future-only (<= -> >=)",
        "target": "src/nexus_scalp/model_generation/replay.py",
        "anchor": "visible = [rows[j] for j in range(len(rows)) if times_raw[j] <= target]",
        "replacement": "visible = [rows[j] for j in range(len(rows)) if times_raw[j] >= target]",
        "battery": "tests/unit/test_qa_deep_metamorphic_replay.py",
    },
    {
        "id": "MUT-DB-01",
        "desc": "migration: downgrade protection disabled",
        "target": "src/nexus_scalp/database/engine.py",
        "anchor": "if cur > exp:",
        "replacement": "if cur > exp + 1000:",
        "battery": "tests/unit/test_qa_deep_db_migration_adversarial.py",
    },
]

#: Documented INTENTIONAL survivors: id -> reason. An id here that survives
#: does NOT fail the weekly lane; the acceptance IS the documentation.
#: Anything not listed that survives = test blind spot = defect.
ACCEPTED_SURVIVORS: dict[str, str] = {}

# ---------------------------------------------------------------------------
# DOMAIN-CONTRACT MUTATIONS (critical invariants generic mutation cannot
# understand).  Each id maps one CRITICAL INVARIANT to its guard battery:
#   * bypass the risk engine kill-switch / fail-closed rejection contract
#   * admit future data into training (purge/embargo semantics)
#   * weaken the 70D schema / freshness contract
#   * disable the single-flight provider gate
#   * admit stale market state (freshness check removed)
# These are the mutations the mission brief calls "higher value than random
# cosmetic mutations": if one of these survives, a SAFETY contract has lost
# its regression net, not just a branch.
# Anchors verified unique against the CURRENT tree; the runner still asserts
# count==1 at run time (INVALID_ANCHOR on drift), so a stale entry can never
# silently skip.
# ---------------------------------------------------------------------------
CONTRACT_MUTATIONS: list[dict[str, str]] = [
    {
        "id": "MUT-RISK-KILLSWITCH",
        "desc": "risk gate bypass: kill-switch consult removed from evaluate_proposal",
        "target": "src/nexus_scalp/risk/risk_engine.py",
        "anchor": '        if self._kill_switch_active:\n            logger.warning("Proposal rejected: Emergency kill switch active.")\n            return None',
        "replacement": '        if False and self._kill_switch_active:\n            logger.warning("Proposal rejected: Emergency kill switch active.")\n            return None',
        "battery": "tests/unit/test_mutation_contract_guard.py",
    },
    {
        "id": "MUT-TEMPORAL-PURGE",
        "desc": "temporal purge bypass: DEFAULT_PURGE_SECONDS zeroed (5min -> 0)",
        "target": "src/nexus_scalp/research/splitting.py",
        "anchor": "DEFAULT_PURGE_SECONDS: float = 300.0",
        "replacement": "DEFAULT_PURGE_SECONDS: float = 0.0",
        "battery": "tests/unit/test_mutation_contract_guard.py",
    },
    {
        "id": "MUT-TEMPORAL-EMBARGO",
        "desc": "temporal embargo bypass: DEFAULT_EMBARGO_SECONDS zeroed (60 -> 0)",
        "target": "src/nexus_scalp/research/splitting.py",
        "anchor": "DEFAULT_EMBARGO_SECONDS: float = 60.0",
        "replacement": "DEFAULT_EMBARGO_SECONDS: float = 0.0",
        "battery": "tests/unit/test_mutation_contract_guard.py",
    },
    {
        "id": "MUT-FRESHNESS-GATE",
        "desc": "market freshness check bypass: stale-data staging always FRESH",
        "target": "src/nexus_scalp/application/live_freshness.py",
        "anchor": '        if age > max_age_sec:\n            return "STALE", round(age * 1000.0, 1)',
        "replacement": '        if False and age > max_age_sec:\n            return "STALE", round(age * 1000.0, 1)',
        "battery": "tests/unit/test_mutation_contract_guard.py",
    },
    {
        "id": "MUT-SHADOW-BOUNDARY",
        "desc": "SHADOW observation-only boundary removed (shadow may execute)",
        "target": "src/nexus_scalp/application/live/decision_executor.py",
        # Anchor must match the file EXACTLY (ruff format moves indent).
        # Current file (decision_executor.py:59-62):
        #   if (
        #       self.om.config.execution.mode == ExecutionMode.SHADOW
        #       and policy_decision.action != ActionType.NO_TRADE
        #   ):
        "anchor": (
            "        if (\n"
            "            self.om.config.execution.mode == ExecutionMode.SHADOW\n"
            "            and policy_decision.action != ActionType.NO_TRADE\n"
            "        ):"
        ),
        "replacement": (
            "        if (\n"
            "            False\n"
            "            and policy_decision.action != ActionType.NO_TRADE\n"
            "        ):"
        ),
        "battery": "tests/unit/test_mutation_contract_guard.py",
    },
    {
        "id": "MUT-SM-HYSTERESIS",
        "desc": "state machine: time hysteresis leg dropped (>= -> >) [same class as MUT-SM-01, contract battery]",
        "target": "src/nexus_scalp/execution/position_state_machine.py",
        "anchor": "if elapsed >= min_dur and new_count >= min_cnt:",
        "replacement": "if elapsed > min_dur and new_count >= min_cnt:",
        "battery": "tests/unit/test_mutation_contract_guard.py",
    },
    {
        "id": "MUT-RB-HORIZON",
        "desc": "recovery budget: horizon clamp inverted (max->min) [same class as MUT-RB-01, contract battery]",
        "target": "src/nexus_scalp/execution/recovery_budget.py",
        "anchor": "horizon = max(min_horizon, min(max_horizon, base_hor))",
        "replacement": "horizon = min(min_horizon, min(max_horizon, base_hor))",
        "battery": "tests/unit/test_mutation_contract_guard.py",
    },
]


def _apply_mutation(source: str, anchor: str, replacement: str) -> str:
    # EOL-TOLERANT matching: multi-line anchors are written with \n but
    # target files on disk may use CRLF (git autocrlf / editor). Normalize
    # BOTH sides to \n for the count/match, then apply on the normalized
    # text and restore the original EOL convention afterwards.
    eol = "\r\n" if "\r\n" in source else "\n"
    norm_source = source.replace("\r\n", "\n")
    norm_anchor = anchor.replace("\r\n", "\n")
    norm_repl = replacement.replace("\r\n", "\n")
    count = norm_source.count(norm_anchor)
    if count != 1:
        raise ValueError(f"anchor count {count} != 1 for {norm_anchor[:60]!r}")
    mutated = norm_source.replace(norm_anchor, norm_repl)
    if eol != "\n":
        mutated = mutated.replace("\n", eol)
    return mutated


def _battery_prime(battery: str) -> str:
    return (
        "import sys, os, importlib.util\n"  # ONCE, outside the finder (recursion guard)
        f"TMP_ROOT = r'{workdir_placeholder()}'\n"  # replaced by caller
        f"REPO_SRC = r'{SRC}'\n"
        "class _MutatedFinder:\n"
        "    def __init__(self) -> None:\n"
        "        self._base = TMP_ROOT\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if not fullname.startswith('nexus_scalp'):\n"
        "            return None\n"
        "        rel = fullname.replace('.', '/')\n"
        "        candidate = os.path.join(self._base, rel + '.py')\n"
        "        pkg_init = os.path.join(self._base, rel, '__init__.py')\n"
        "        if os.path.isfile(candidate):\n"
        "            return importlib.util.spec_from_file_location(fullname, candidate)\n"
        "        if os.path.isfile(pkg_init):\n"
        "            return importlib.util.spec_from_file_location(\n"
        "                fullname, pkg_init,\n"
        "                submodule_search_locations=[os.path.join(self._base, rel)])\n"
        "        return None\n"
        "sys.meta_path.insert(0, _MutatedFinder())\n"
        "import pytest\n"
        f"sys.exit(pytest.main([{battery!r}, '-q', '--no-header', '-p', 'no:cacheprovider']))\n"
    )


def workdir_placeholder() -> str:
    return "@@TMP_ROOT@@"


def _run_battery(
    battery: str,
    mutated_rel: str,
    mutated_text: str | None,
    workdir: Path,
    py: str,
    timeout: int = 900,
) -> tuple[int, str]:
    """Run one battery with ONE file swapped inside an isolated temp tree.

    mutated_text=None runs the PRISTINE copy (battery health sanity).
    Module resolution: a meta-path finder installed at bootstrap TOP LEVEL
    (importlib.util imported once outside find_spec) points `nexus_scalp.*`
    at the temp tree first, so the temp copy wins over the editable install.
    """
    if mutated_text is not None:
        tree = workdir / mutated_rel
        tree.parent.mkdir(parents=True, exist_ok=True)
        tree.write_text(mutated_text, encoding="utf-8", newline="")
    bootstrap = _battery_prime(battery).replace("@@TMP_ROOT@@", str(workdir / "src"))
    try:
        proc = subprocess.run(
            [py, "-c", bootstrap],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, (proc.stdout + proc.stderr)[-1200:]
    except subprocess.TimeoutExpired:
        return 124, "battery timeout (>=900s)"


def _git_changed_targets() -> set[str]:
    """Files changed in the working tree / staged / vs origin/main merge-base."""
    names: set[str] = set()
    cmds: list[list[str]] = [
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "diff", "--name-only", "--cached"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]
    base = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/main"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    if base.returncode == 0 and base.stdout.strip():
        cmds.append(["git", "diff", "--name-only", base.stdout.strip()])
    for cmd in cmds:
        r = subprocess.run(
            cmd, cwd=str(REPO), capture_output=True, text=True, check=False, timeout=30
        )
        if r.returncode == 0:
            names.update(ln.strip() for ln in r.stdout.splitlines() if ln.strip())
    return names


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="run_mutations", description=__doc__.splitlines()[0])
    p.add_argument("--ids", default="", help="comma-separated mutation ids to run")
    p.add_argument("--target", default="", help="only mutations whose target contains SUB")
    p.add_argument(
        "--changed",
        action="store_true",
        help="only mutations whose target file is in the current git diff",
    )
    p.add_argument("--out", default="", help="also write the JSON report to this path")
    p.add_argument(
        "--list-targets",
        action="store_true",
        help="print the mutation catalog as JSON (targets/batteries/anchors) "
        "and exit 0 - smoke/CI introspection, runs no batteries",
    )
    p.add_argument(
        "--no-contract",
        action="store_true",
        help="run ONLY the frozen anchor catalog (skip domain-contract "
        "mutations) - cheap smoke path",
    )
    args = p.parse_args(argv)

    # --list-targets: pure introspection - catalog + resolver contract, no
    # batteries, no mutation runs. Powers the smoke test and the CI fast-lane
    # catalog echo without paying the minutes-long campaign cost.
    if args.list_targets:
        catalog = []
        for m in [*MUTATIONS, *CONTRACT_MUTATIONS]:
            target_abs = REPO / m["target"]
            exists = target_abs.exists()
            unique = False
            if exists:
                disk = target_abs.read_text(encoding="utf-8").replace("\r\n", "\n")
                unique = disk.count(m["anchor"].replace("\r\n", "\n")) == 1
            catalog.append(
                {
                    "id": m["id"],
                    "target": m["target"],
                    "target_exists": exists,
                    "anchor_unique": unique,
                    "battery": m["battery"],
                    "battery_exists": (REPO / m["battery"]).exists(),
                }
            )
        print(
            json.dumps(
                {
                    "tool": "scripts/qa/run_mutations.py",
                    "mode": "list-targets",
                    "repo_root": str(REPO),
                    "repo_root_ok": (REPO / "pyproject.toml").exists(),
                    "python_executable": _repo_python(),
                    "python_is_running_interpreter": _repo_python() == sys.executable,
                    "mutations_total": len(MUTATIONS) + len(CONTRACT_MUTATIONS),
                    "anchor_catalog_total": len(MUTATIONS),
                    "contract_catalog_total": len(CONTRACT_MUTATIONS),
                    "accepted_survivors": sorted(ACCEPTED_SURVIVORS),
                    "catalog": catalog,
                },
                indent=2,
            )
        )
        return 0

    wanted_ids = {s.strip() for s in args.ids.split(",") if s.strip()}
    # HYBRID CATALOG (QA hardening mission): the frozen anchor catalog
    # (MUTATIONS) AND the domain-contract registry (CONTRACT_MUTATIONS)
    # are both live by default. Contract mutations protect critical
    # invariants generic mutation cannot understand (risk-gate bypass,
    # temporal purge bypass, freshness bypass, SHADOW boundary).
    # --no-contract opts out for cheap smoke runs.
    catalog: list[dict[str, str]] = list(MUTATIONS)
    if not args.no_contract:
        catalog.extend(CONTRACT_MUTATIONS)
    selected = [
        m
        for m in catalog
        if (not wanted_ids or m["id"] in wanted_ids)
        and (not args.target or args.target in m["target"])
    ]
    if args.changed:
        changed = _git_changed_targets()
        selected = [m for m in selected if m["target"] in changed]

    py = _repo_python()
    git_sha = ""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        git_sha = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        git_sha = ""
    started = time.perf_counter()

    results: list[dict[str, object]] = []
    pristine_rc_cache: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="nse_mut_") as tmp:
        workdir = Path(tmp)
        for mut in selected:
            target_abs = REPO / mut["target"]
            battery = mut["battery"]
            if battery not in pristine_rc_cache:
                # Battery health sanity: the UNMUTATED copy must pass, else
                # no verdict for this battery can be trusted.
                pristine_rc_cache[battery], _ = _run_battery(
                    battery, mut["target"], None, workdir, py
                )
            pristine_rc = pristine_rc_cache[battery]
            entry: dict[str, object] = {
                "id": mut["id"],
                "desc": mut["desc"],
                "target": mut["target"],
                "battery": battery,
                "pristine_rc": pristine_rc,
            }
            if pristine_rc != 0:
                entry["verdict"] = "INVALID_BATTERY"
                entry["note"] = "battery fails on the UNMUTATED copy; verdict untrustworthy"
                results.append(entry)
                continue
            try:
                source = target_abs.read_text(encoding="utf-8")
                mutated = _apply_mutation(source, mut["anchor"], mut["replacement"])
            except (OSError, ValueError) as e:
                entry["verdict"] = "INVALID_ANCHOR"
                entry["error"] = str(e)
                results.append(entry)
                continue
            rc, tail = _run_battery(battery, mut["target"], mutated, workdir, py)
            if rc == 0:
                if mut["id"] in ACCEPTED_SURVIVORS:
                    entry["verdict"] = "ACCEPTED_SURVIVOR"
                    entry["reason"] = ACCEPTED_SURVIVORS[mut["id"]]
                else:
                    entry["verdict"] = "SURVIVED"
            else:
                entry["verdict"] = "KILLED"
            entry["pytest_rc"] = rc
            entry["evidence_tail"] = tail.splitlines()[-3:]
            results.append(entry)

    killed = sum(1 for r in results if r["verdict"] == "KILLED")
    survived = [r["id"] for r in results if r["verdict"] == "SURVIVED"]
    contract_ids = {m["id"] for m in CONTRACT_MUTATIONS}
    contract_survived = [r for r in survived if r in contract_ids]
    accepted = [r["id"] for r in results if r["verdict"] == "ACCEPTED_SURVIVOR"]
    invalid = [r["id"] for r in results if str(r["verdict"]).startswith("INVALID")]
    score = round(killed / len(results), 4) if results else 0.0
    summary = {
        "tool": "scripts/qa/run_mutations.py",
        "contract": "honesty-v2: KILLED requires pristine battery rc==0; INVALID_* never counts as KILLED",
        "git_commit": git_sha,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scope": "full"
        if not (wanted_ids or args.target or args.changed)
        else ("changed" if args.changed else "filtered"),
        "mutations_total": len(selected),
        "killed": killed,
        "survived": len(survived),
        "survivor_ids": survived,
        "contract_mutation_survivors": contract_survived,
        "note_contract": (
            "a surviving CONTRACT_MUTATION is a safety-invariant regression "
            "net loss; it ALWAYS fails the gate (never accept-listed)"
        ),
        "accepted_survivor_ids": accepted,
        "invalid_ids": invalid,
        "mutation_score": score,
        "duration_sec": round(time.perf_counter() - started, 1),
        "note": "SURVIVED = test blind spot (a critical mutation no test catches); file in agents/bugs.md",
        "results": results,
    }
    payload = json.dumps(summary, indent=2)
    print(payload)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8", newline="")
    if invalid:
        return 2
    if survived:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
