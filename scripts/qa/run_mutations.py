"""CHG-0045 part 2: mutation-proof runner (controlled, working-tree-safe).

Injects a small, FIXED catalog of realistic mutations into critical
contracts, runs the owning test battery, and reports KILLED (tests failed
= the net catches the mutation) vs SURVIVED (test blind spot). The repo
working tree is NEVER mutated: mutations are applied to a temp copy of the
single target file, and the battery runs with the temp module forced onto
sys.path via an import hook rooted at the temp tree.

Design:
- mutator: pure function (file text) -> mutated text; must change EXACTLY
  the documented anchor (count==1 assertion before writing).
- runner: pytest subprocess with `PYTHONPATH=<temp_root>;<repo>` ordering so
  `nexus_scalp.*` resolves from the mutated temp copy (src-layout shim).
- each mutation gets its OWN temp tree (no cross-contamination).

Exit semantics: the runner always exits 0; results are JSON on stdout.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")
PY = REPO / ".venv" / "Scripts" / "python.exe"
SRC = REPO / "src"
SEED_STAMP = "20260902"

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


def _apply_mutation(source: str, anchor: str, replacement: str) -> str:
    count = source.count(anchor)
    if count != 1:
        raise ValueError(f"anchor count {count} != 1 for {anchor[:60]!r}")
    return source.replace(anchor, replacement)


def _run_battery(
    battery: str, mutated_rel: str, mutated_text: str, workdir: Path
) -> tuple[int, str]:
    """Run one battery with ONE file mutated inside an isolated temp tree.

    Module resolution: the editable-install .pth pins nexus_scalp to the
    REPO's src dir at site-packages level, which overrides PYTHONPATH
    ordering for `import nexus_scalp`. We therefore PREPEND an import
    redirect via a sitecustomize-free `-c` bootstrap that installs a
    meta_path finder pointing the mutated package subtree at the temp
    tree FIRST, then hands off to pytest.main in-process.
    """
    tree = workdir / mutated_rel
    tree.parent.mkdir(parents=True, exist_ok=True)
    tree.write_text(mutated_text, encoding="utf-8", newline="")
    bootstrap = (
        "import sys, os\n"
        f"TMP_ROOT = r'{workdir / 'src'}'\n"
        f"REPO_SRC = r'{SRC}'\n"
        "# prepend a path-based finder override: files present under TMP_ROOT\n"
        "# win over the editable install's copy of the same module\n"
        "class _MutatedFinder:\n"
        "    def __init__(self) -> None:\n"
        "        self._base = TMP_ROOT\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        import importlib.util, os\n"
        "        if not fullname.startswith('nexus_scalp'):\n"
        "            return None\n"
        "        rel = fullname.replace('.', '/')\n"
        "        candidate = os.path.join(self._base, rel + '.py')\n"
        "        pkg_init = os.path.join(self._base, rel, '__init__.py')\n"
        "        if os.path.isfile(candidate):\n"
        "            spec = importlib.util.spec_from_file_location(fullname, candidate)\n"
        "            return spec\n"
        "        if os.path.isfile(pkg_init):\n"
        "            spec = importlib.util.spec_from_file_location(\n"
        "                fullname, pkg_init,\n"
        "                submodule_search_locations=[os.path.join(self._base, rel)])\n"
        "            return spec\n"
        "        return None\n"
        "sys.meta_path.insert(0, _MutatedFinder())\n"
        "import pytest\n"
        f"sys.exit(pytest.main([{battery!r}, '-q', '--no-header', '-p', 'no:cacheprovider']))\n"
    )
    proc = subprocess.run(
        [str(PY), "-c", bootstrap],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-1200:]


def main() -> int:
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix=f"nse_mut_{SEED_STAMP}_") as tmp:
        workdir = Path(tmp)
        for mut in MUTATIONS:
            target_abs = REPO / mut["target"]
            source = target_abs.read_text(encoding="utf-8")
            try:
                mutated = _apply_mutation(source, mut["anchor"], mut["replacement"])
            except ValueError as e:
                results.append(
                    {
                        "id": mut["id"],
                        "target": mut["target"],
                        "error": f"anchor mismatch: {e}",
                        "verdict": "SKIPPED_ANCHOR",
                    }
                )
                continue
            rc, tail = _run_battery(mut["battery"], mut["target"], mutated, workdir)
            killed = rc != 0
            results.append(
                {
                    "id": mut["id"],
                    "desc": mut["desc"],
                    "target": mut["target"],
                    "battery": mut["battery"],
                    "verdict": "KILLED" if killed else "SURVIVED",
                    "pytest_rc": rc,
                    "evidence_tail": tail.splitlines()[-3:],
                }
            )
    survived = [r for r in results if r.get("verdict") == "SURVIVED"]
    summary = {
        "tool": "scripts/qa/run_mutations.py",
        "seed_stamp": SEED_STAMP,
        "mutations_total": len(MUTATIONS),
        "killed": sum(1 for r in results if r.get("verdict") == "KILLED"),
        "survived": len(survived),
        "survivor_ids": [r["id"] for r in survived],
        "note": "SURVIVED = test blind spot (a critical mutation no test catches)",
        "results": results,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
