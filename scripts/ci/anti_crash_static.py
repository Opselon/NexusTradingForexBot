#!/usr/bin/env python3
"""Static anti-crash check (MASTER crash-containment pass, section 48).

Scans the engine source for silent-failure / lost-traceback patterns:

  P1 (hard violations, exit 1 when allowlist does not cover them):
    * bare ``except:``                                   (E722 class)
    * ``except BaseException`` outside deliberate process boundaries
    * ``except Exception:`` whose handler body is exactly
      ``pass`` / ``continue`` / ``return None`` / ``return False`` /
      ``return 0`` / ``return {}`` / ``return []``

  P2 (warn-level, reported, never fail the run):
    * ``except Exception`` handler body with no logging/re-raise/state write
    * ``asyncio.create_task`` / ``Thread(`` spawn sites without a visible
      done-callback or crash-capture in the same statement window
    * ``subprocess.run(..., check=False)`` whose result is discarded

Each hit can be suppressed with an inline comment marker::

    # anti-crash: allow (<short reason>)

A small built-in registry documents the known-EXPECTED control flows
(idempotent schema migrations, best-effort parse fallbacks) so the default
run stays honest instead of flagging reviewed-and-accepted behavior.

Exit codes: 0 = clean (warnings allowed), 1 = violations, 2 = usage/config.
Output: human summary on stdout; ``--json`` prints a machine report.

Run:  .venv/Scripts/python.exe scripts/ci/anti_crash_static.py
      .venv/Scripts/python.exe scripts/ci/anti_crash_static.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = [REPO_ROOT / "src" / "nexus_scalp", REPO_ROOT / "NexusTradingForexBot.py"]

#: inline suppression marker
ALLOW_MARKER = "anti-crash: allow"

#: files where BaseException boundaries are DELIBERATE (process/shutdown edge)
BASE_EXCEPTION_ALLOWLIST = {
    "src/nexus_scalp/diagnostics/runner.py",
}

#: known-EXPECTED ``except Exception: pass`` control flows (relative paths).
#: Each entry was reviewed: the failure is either idempotent-DDL noise or a
#: best-effort cache/enrichment write whose failure must not disturb the path.
_EXPECTED_PASS_SITE_PREFIXES = {
    # idempotent migration ALTER TABLE / CREATE INDEX (duplicate column/index)
    "src/nexus_scalp/adapters/database/audit_repository.py",
    "src/nexus_scalp/strategies/factory/store.py",
}

#: reviewed-EXPECTED silent handlers: multi-stage probe chains where every
#: stage falls through to an explicit, DOCUMENTED default (the exception is
#: the "stage did not apply" signal, the default is the contract). Listed as
#: (file, line_comment_substring) pairs so a line-number shift cannot silently
#: keep a dead entry: the pair matches by content, not position.
EXPECTED_SILENT_HANDLERS = {
    # live_engine declared-contract probes (meta -> scaler -> checkpoint chain,
    # each falls through to the NEXT probe or an explicit class default)
    ("src/nexus_scalp/application/live_engine.py", "return int(self.mean.shape[0])"),
    ("src/nexus_scalp/application/live_engine.py", "return int(self.__class__.FEATURE_DIM)"),
    ("src/nexus_scalp/application/live_engine.py", "return str(self.__class__.FEATURE_SCHEMA_ID)"),
    ("src/nexus_scalp/application/live_engine.py", "_eff_dim0 = 0"),
}

_HANDLER_ONLY = re.compile(
    r"^\s*except[^:\n]*:\s*$"  # handler line (any indent) with nothing after the colon
)
_HANDLER_INLINE_SILENT = re.compile(
    r"^\s*except[^:\n]*:\s*(pass|continue|return\s+(None|False|0|\{\}|\[\]))\s*(#.*)?$"
)
_FIRST_BODY_SILENT = re.compile(r"^\s*(pass|continue|return\s+(None|False|0|\{\}|\[\]))\s*(#.*)?$")
_EXC_LINE = re.compile(r"^\s*except(\s+[A-Za-z_][\w\.]*(\s*,\s*\w+)?|\s*\*?\s*\w+)?\s*:")
_BARE = re.compile(r"^\s*except\s*:")
_BASE = re.compile(r"^\s*except\s+BaseException\b")
_CREATE_TASK = re.compile(r"\bcreate_task\s*\(")
_THREAD_SPAWN = re.compile(r"\bthreading\.Thread\s*\(|\bThread\s*\(")
_CHECK_FALSE = re.compile(r"check\s*=\s*False")
_DONE_CALLBACK = re.compile(r"add_done_callback")
_LOGGING_HINT = re.compile(
    r"\b(logger|log|_log|console)\b|\bexc_info\b|\btraceback\b|\braise\b|"
    r"\bnotifier\b|\b_console_push\b|self\.\w*(count|state|status|error\w*)\s*[\+\.=]"
)


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(sorted(root.rglob("*.py")))
    return [f for f in files if "__pycache__" not in f.parts]


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix().replace("\\", "/")


def _has_allow(line: str) -> bool:
    return ALLOW_MARKER in line


def _is_expected_pass_site(rel: str, line: str) -> bool:
    if not any(rel.startswith(p) for p in _EXPECTED_PASS_SITE_PREFIXES):
        return False
    # Reviewed-EXPECTED contexts inside the allowlisted files:
    # - idempotent migration DDL (duplicate column/index is the success path);
    #   matched against the handler line, the try-body line above it, OR the
    #   SQL argument line two lines above (multi-line conn.execute calls)
    # - BUG-149/156 runtime workspace anchor fallback (probe import/call
    #   failure falls back to the raw relative path, matching legacy behavior)
    # - queue.Empty (batch-collection loop exit) / queue.Full (drop telemetry
    #   when saturated) — both are documented non-error control flow
    return (
        "ALTER TABLE" in line
        or "CREATE INDEX" in line
        or "ADD COLUMN" in line
        or "get_runtime_workspace" in line
        or "ensure_" in line
        or "conn.execute" in line
        or "queue.Empty" in line
        or "queue.Full" in line
        or "_shared_conn.close" in line
    )


def scan() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    violations: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    for path in _iter_py_files():
        rel = _rel(path)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        for idx, line in enumerate(lines):
            stripped = line.rstrip("\n")

            # --- P1: bare except ------------------------------------------------
            if _BARE.match(stripped) and not _has_allow(stripped):
                violations.append(
                    {
                        "file": rel,
                        "line": str(idx + 1),
                        "kind": "BARE_EXCEPT",
                        "text": stripped.strip()[:160],
                    }
                )

            # --- P1: BaseException outside deliberate boundaries ----------------
            if (
                _BASE.match(stripped)
                and rel not in BASE_EXCEPTION_ALLOWLIST
                and not _has_allow(stripped)
            ):
                violations.append(
                    {
                        "file": rel,
                        "line": str(idx + 1),
                        "kind": "BASE_EXCEPTION",
                        "text": stripped.strip()[:160],
                    }
                )

            # --- P1: silent handler (inline or first-body-statement) -------------
            if _EXC_LINE.match(stripped):
                is_silent_inline = bool(_HANDLER_INLINE_SILENT.match(stripped))
                first_body = lines[idx + 1] if idx + 1 < len(lines) else ""
                try_body = lines[idx - 1] if idx > 0 else ""
                try_arg2 = lines[idx - 2] if idx > 1 else ""
                is_silent_block = bool(_HANDLER_ONLY.match(stripped)) and bool(
                    _FIRST_BODY_SILENT.match(first_body)
                )
                if (is_silent_inline or is_silent_block) and not _has_allow(stripped):
                    # Reviewed-EXPECTED control flow downgrades to a warning:
                    # idempotent migration DDL (duplicate column/index) inside
                    # the migration-allowlisted files, multi-format parse
                    # fallbacks where the next format is tried immediately
                    # (failure is the loop's continue condition, not a swallow),
                    # or a documented probe-chain default in EXPECTED_SILENT_HANDLERS.
                    is_parse_fallback = stripped.strip().startswith(
                        "except ValueError"
                    ) and first_body.strip() in ("pass", "continue")
                    is_documented_default = any(
                        rel == f and (first_body.strip() == m or stripped.strip() == m)
                        for f, m in EXPECTED_SILENT_HANDLERS
                    )
                    if (
                        _is_expected_pass_site(rel, first_body)
                        or _is_expected_pass_site(rel, stripped)
                        or _is_expected_pass_site(rel, try_body)
                        or _is_expected_pass_site(rel, try_arg2)
                        or is_parse_fallback
                        or is_documented_default
                    ):
                        warnings.append(
                            {
                                "file": rel,
                                "line": str(idx + 1),
                                "kind": "EXPECTED_MIGRATION_PASS",
                                "text": stripped.strip()[:160],
                            }
                        )
                    else:
                        violations.append(
                            {
                                "file": rel,
                                "line": str(idx + 1),
                                "kind": "SILENT_HANDLER",
                                "text": stripped.strip()[:160],
                            }
                        )
                    continue

                # --- P2: generic-exception handler with no logging/raise/state --
                if re.match(r"^\s*except\s+Exception\b", stripped):
                    body: list[str] = []
                    if _HANDLER_ONLY.match(stripped):
                        for nxt in lines[idx + 1 : idx + 9]:
                            if _EXC_LINE.match(nxt) or (nxt.strip() and not nxt[0].isspace()):
                                break
                            body.append(nxt)
                    else:
                        body.append(stripped)
                    joined = "\n".join(body)
                    if not _LOGGING_HINT.search(joined) and not _has_allow(stripped):
                        warnings.append(
                            {
                                "file": rel,
                                "line": str(idx + 1),
                                "kind": "HANDLER_NO_TRACE",
                                "text": stripped.strip()[:160],
                            }
                        )

            # --- P2: fire-and-forget task/thread spawns -------------------------
            if _CREATE_TASK.search(stripped) or _THREAD_SPAWN.search(stripped):
                window = "\n".join(lines[idx : idx + 6])
                if not _DONE_CALLBACK.search(window) and not _has_allow(stripped):
                    warnings.append(
                        {
                            "file": rel,
                            "line": str(idx + 1),
                            "kind": "SPAWN_NO_DONE_CALLBACK",
                            "text": stripped.strip()[:160],
                        }
                    )

            # --- P2: discarded check=False subprocess result --------------------
            if _CHECK_FALSE.search(stripped):
                window = "\n".join(lines[max(0, idx - 3) : idx + 8])
                if (
                    "returncode" not in window
                    and ".stdout" not in window
                    and not _has_allow(stripped)
                ):
                    warnings.append(
                        {
                            "file": rel,
                            "line": str(idx + 1),
                            "kind": "CHECK_FALSE_DISCARDED",
                            "text": stripped.strip()[:160],
                        }
                    )

    return violations, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Static anti-crash check (NSE section 48).")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--warn-only", action="store_true", help="violations downgrade to warnings")
    args = parser.parse_args()

    violations, warnings = scan()

    report = {
        "check": "anti_crash_static",
        "repo": str(REPO_ROOT.name),
        "files_scanned": len(_iter_py_files()),
        "violations": violations,
        "warnings": warnings,
        "violation_count": len(violations),
        "warning_count": len(warnings),
        "allowlist": {
            "base_exception_files": sorted(BASE_EXCEPTION_ALLOWLIST),
            "expected_pass_prefixes": sorted(_EXPECTED_PASS_SITE_PREFIXES),
            "expected_silent_handlers": sorted(f"{f} :: {m}" for f, m in EXPECTED_SILENT_HANDLERS),
            "inline_marker": ALLOW_MARKER,
        },
        "exit_code": 0 if (args.warn_only or not violations) else 1,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"anti-crash static check — files scanned: {report['files_scanned']}")
        print(f"violations: {report['violation_count']}   warnings: {report['warning_count']}")
        for kind in ("BARE_EXCEPT", "BASE_EXCEPTION", "SILENT_HANDLER"):
            rows = [v for v in violations if v["kind"] == kind]
            if rows:
                print(f"\n[{kind}]")
                for v in rows[:20]:
                    print(f"  {v['file']}:{v['line']}: {v['text']}")
        kinds: dict[str, list[dict[str, str]]] = {}
        for w in warnings:
            kinds.setdefault(w["kind"], []).append(w)
        for kind, rows in sorted(kinds.items()):
            print(f"\n[warn:{kind}] x{len(rows)}")
            for w in rows[:8]:
                print(f"  {w['file']}:{w['line']}: {w['text']}")
        if violations and not args.warn_only:
            print("\nRESULT: VIOLATIONS FOUND (exit 1)")
        else:
            print(
                "\nRESULT: CLEAN (suppress with '# anti-crash: allow (<reason>)' only when reviewed)"
            )

    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
