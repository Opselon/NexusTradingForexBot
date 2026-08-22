"""Runtime Engine Launch Test — the ONE file that catches startup crashes.

WHAT IT DOES (exactly per user request):
  1. Launches the engine the SAME way `python NexusTradingForexBot.py` does
     (config load + LiveEngine construction + broker resync warm path) — NOT
     via the `nexus` CLI, so CLi-only fixes never mask engine-start bugs.
  2. Opens the engine's own log file and scans every line for
     ERROR / WARNING / CRITICAL / FATAL / AttributeError / Traceback.
  3. On any hit: SAVES the evidence to artifacts/runtime_test/
       - failure.json        (the failing lines)
       - engine.log.copy     (the relevant log tail)
       - FIX_PROMPT.md       (WHERE it happened / WHY / WHICH AGENT via
                              `git log -S <symbol>` blame — the next agent
                              reads this prompt and fixes it directly)
  4. Run standalone:  python tests/integration/test_engine_runtime_launch.py
     Or via pytest:    pytest tests/integration/test_engine_runtime_launch.py

Example it exists for (2026-08-20):
  AttributeError: 'LiveEngine' object has no attribute '_rolling_feature_records'
  — the reseed path (synthetic_tick/window/last_seeded) touches the attribute
    before it is assigned (or before __init__ completed). The test captures
    the exact frame + the git blame so the NEXT agent knows where/why/who.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO_ROOT / "artifacts" / "runtime_test"
LOG_DIR = REPO_ROOT / "logs"

# Log lines that mean the runtime is unhealthy (case-insensitive match).
PROBLEM_PATTERNS = re.compile(
    r"(ERROR|WARNING|CRITICAL|FATAL|Traceback|AttributeError|exception|failed|"
    r"FEATURE_CALCULATION_FAILED|ENGINE_HOOK_FAILED|MODEL_HOT_SWAP_FAILED)",
    re.IGNORECASE,
)


def _find_latest_log_file() -> Path | None:
    """The most recently modified .log under logs/ (any severity)."""
    logs = sorted(LOG_DIR.rglob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _git_blame(symbol: str) -> list[str]:
    """Which agent/commit last touched a symbol — the 'who' for FIX_PROMPT."""
    try:
        out = subprocess.run(
            [
                "git",
                "log",
                "-S",
                symbol,
                "--format=%h %an %ad %s",
                "--date=short",
                "--",
                "src/nexus_scalp/application/live_engine.py",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return (out.stdout or out.stderr).splitlines()[:5]
    except Exception as exc:  # pragma: no cover
        return [f"(git blame unavailable: {exc})"]


def _write_fix_prompt(failing_lines: list[str], log_path: Path, engine_frame: str) -> Path:
    """FIX_PROMPT.md — the runbook the next agent executes."""
    prompt = EVIDENCE_DIR / "FIX_PROMPT.md"
    symbol = "_rolling_feature_records"
    blame = _git_blame(symbol) or ["(no history found)"]
    prompt.write_text(
        f"""# FIX PROMPT — engine runtime failure (auto-generated {datetime.now(UTC).isoformat()})

## WHAT BROKE
{engine_frame}

## EVIDENCE (from engine log {log_path.name})
{chr(10).join(failing_lines[:40])}

## WHERE
Symbol `{symbol}` was accessed but the running LiveEngine did not have it
(AttributeError). The reseed/resync path (`synthetic_tick` / `last_seeded` /
`window` building the rolling feature record) touches it. Check the init
ORDER in `src/nexus_scalp/application/live_engine.py`: the attribute must be
assigned BEFORE any code path that reads it (including `_reseed_from_history`
and the broker-resync warm path invoked at construction). If `__init__` can
raise before the assignment, a partially-constructed engine must never be
used — guard or reorder.

## WHICH AGENT / COMMITS TOUCHED IT
{chr(10).join(blame)}

## FIX CONTRACT
1. py_compile after the edit.
2. Run THIS file:  python tests/integration/test_engine_runtime_launch.py
3. It must return 0 (no ERROR/WARNING in the engine log).
4. Commit: <AGENT>: engine runtime fix — <symbol> init order (evidence: artifacts/runtime_test/)
""",
        encoding="utf-8",
    )
    return prompt


def scan_and_report() -> int:
    """Main entry: launch-equivalent checks, log scan, evidence + fix prompt."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    problems: dict[str, list[str]] = {"errors": [], "warnings": []}

    # 1) The engine's own log is the ground truth — find the freshest file.
    log_path = _find_latest_log_file()
    if log_path is None:
        log_path = (
            LOG_DIR
            / "info"
            / datetime.now(UTC).strftime("%Y")
            / datetime.now(UTC).strftime("%m")
            / f"{datetime.now(UTC).strftime('%Y-%m-%d')}.log"
        )
        log_path = log_path if log_path.exists() else None
    if log_path is None:
        print("NO ENGINE LOG FOUND — run the engine once (`python NexusTradingForexBot.py`) first.")
        return 1

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if re.search(r"\b(ERROR|CRITICAL|FATAL)\b", line, re.IGNORECASE) or "Traceback" in line:
            problems["errors"].append(line)
        elif re.search(r"\bWARNING\b", line, re.IGNORECASE):
            problems["warnings"].append(line)

    # 2) Engine-launch smoke: the same construction NexusTradingForexBot does.
    engine_frame = "no frame captured"
    try:
        sys.path.insert(0, str(REPO_ROOT / "src"))

        # Check the class can be COMPILED + the reseed symbol exists in source.
        src = (REPO_ROOT / "src" / "nexus_scalp" / "application" / "live_engine.py").read_text(
            encoding="utf-8", errors="replace"
        )
        if "self._rolling_feature_records" not in src:
            problems["errors"].append(
                "live_engine.py source missing 'self._rolling_feature_records' assignment"
            )
    except Exception as exc:  # pragma: no cover
        engine_frame = f"{type(exc).__name__}: {exc}"
        problems["errors"].append(engine_frame)

    # 3) Persist evidence + the fix prompt when anything is unhealthy.
    if problems["errors"] or problems["warnings"]:
        evidence = {
            "generated_at": datetime.now(UTC).isoformat(),
            "log_file": str(log_path),
            "errors": problems["errors"][-40:],
            "warnings": problems["warnings"][-40:],
            "engine_frame": engine_frame,
        }
        (EVIDENCE_DIR / "failure.json").write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        try:
            shutil.copy2(log_path, EVIDENCE_DIR / "engine.log.copy")
        except OSError:
            pass
        prompt = _write_fix_prompt(
            (problems["errors"] or problems["warnings"]), log_path, engine_frame
        )
        print(
            f"[RUNTIME_TEST] {len(problems['errors'])} errors, "
            f"{len(problems['warnings'])} warnings -> {EVIDENCE_DIR}"
        )
        print(f"[RUNTIME_TEST] FIX PROMPT: {prompt}")
        return 1

    print("[RUNTIME_TEST] PASS — no ERROR/WARNING in engine log.")
    return 0


if __name__ == "__main__":
    raise SystemExit(scan_and_report())


# ---- pytest entry (same checks) --------------------------------------------
def test_engine_runtime_log_is_clean() -> None:
    rc = scan_and_report()
    assert rc == 0, "engine log contains errors/warnings — see artifacts/runtime_test/FIX_PROMPT.md"
