#!/usr/bin/env python3
"""CI final gate for the ci.yml quality job (CHG-0052 successor, fail-closed).

Full diagnostic upgrade:
- Fail-closed evaluation of all mandatory checks.
- Deep failure extraction: parses file names, line numbers, columns, rule codes,
  error types, bug traces, and test traces across all quality stages
  (ruff lint, ruff format, mypy, pytest, smoke, coverage, critical coverage,
  runtime gate, runtime deps, layered smoke).
- Professional terminal presentation using `rich` (with clean fallback).
- Emits GitHub Actions workflow error annotations targeting exact files & lines:
  ::error file={file},line={line},col={col},title={title}::{message}
- Writes detailed step summary markdown to $GITHUB_STEP_SUMMARY and artifacts.

Exit codes: 0 all checks passed/blocked-with-root-failure; 1 gate failure.

Local probe (no CI needed):
    CI_RESULTS_DIR=ci-results python scripts/ci/ci_final_gate.py
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table

    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False

#: The quality-job gate checks. MUST stay in sync with the steps in ci.yml
#: that write run-info/<name>.json (every one of them is mandatory on the
#: clean-format path).
CHECKS = (
    "ruff_lint",
    "ruff_format",
    "mypy",
    "pytest",
    "smoke",
    "coverage",
    "critical_coverage",
    "runtime_gate",
    "runtime_deps",
    "layered_smoke",
)


@dataclass
class FailureTrace:
    """Detailed diagnostic for a single failure / violation."""

    file: str | None = None
    line: int | None = None
    column: int | None = None
    rule_or_type: str = ""
    message: str = ""
    traceback: str = ""
    test_node: str = ""


@dataclass
class StepDiagnostic:
    """Aggregated diagnostics for one check."""

    check: str
    status: str
    exit_code: int = 0
    detail: str = ""
    failures: list[FailureTrace] = field(default_factory=list)
    raw_output: str = ""


def _rel_path(path: str | Path | None) -> str | None:
    if not path:
        return None
    p_str = str(path).replace("\\", "/").strip()
    cwd_str = os.getcwd().replace("\\", "/").rstrip("/") + "/"
    if p_str.startswith(cwd_str):
        p_str = p_str[len(cwd_str) :]
    return p_str if p_str else None


def _find_innermost_frame(tb_text: str) -> tuple[str | None, int | None]:
    """Extract innermost repo (src/ or tests/) frame from a Python traceback."""
    if not tb_text:
        return None, None
    matches = list(re.finditer(r'File "([^"]+)", line (\d+), in (\w+)', tb_text))
    for m in reversed(matches):
        p = m.group(1).replace("\\", "/")
        if "src/" in p or "tests/" in p:
            rel = _rel_path(p)
            try:
                line = int(m.group(2))
                return rel, line
            except ValueError:
                pass
    if matches:
        last = matches[-1]
        try:
            return _rel_path(last.group(1)), int(last.group(2))
        except ValueError:
            pass
    return None, None


def parse_ruff_lint(root: Path) -> list[FailureTrace]:
    out: list[FailureTrace] = []
    json_path = root / "ruff" / "lint.json"
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    loc = item.get("location") or {}
                    out.append(
                        FailureTrace(
                            file=_rel_path(item.get("filename")),
                            line=loc.get("row") if isinstance(loc.get("row"), int) else None,
                            column=loc.get("column")
                            if isinstance(loc.get("column"), int)
                            else None,
                            rule_or_type=str(item.get("code") or "LINT"),
                            message=str(item.get("message") or ""),
                        )
                    )
                if out:
                    return out
        except Exception:
            pass

    txt_path = root / "ruff" / "lint.txt"
    if txt_path.is_file():
        try:
            for line in txt_path.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"^([^:\s]+):(\d+):(\d+):\s*([A-Z]\d+)\s*(.*)$", line.strip())
                if m:
                    out.append(
                        FailureTrace(
                            file=_rel_path(m.group(1)),
                            line=int(m.group(2)),
                            column=int(m.group(3)),
                            rule_or_type=m.group(4),
                            message=m.group(5),
                        )
                    )
        except Exception:
            pass
    return out


def parse_ruff_format(root: Path) -> list[FailureTrace]:
    out: list[FailureTrace] = []
    txt_path = root / "format" / "format.txt"
    if txt_path.is_file():
        try:
            for line in txt_path.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.search(r"Would reformat:\s*(.+)$", line.strip())
                if m:
                    out.append(
                        FailureTrace(
                            file=_rel_path(m.group(1)),
                            rule_or_type="FormatViolation",
                            message="File requires reformatting (ruff format)",
                        )
                    )
        except Exception:
            pass

    repair_json = root / "format" / "ruff-repair-report.json"
    if repair_json.is_file():
        try:
            data = json.loads(repair_json.read_text(encoding="utf-8", errors="replace"))
            for rep_file in data.get("repaired_files", []):
                if not any(f.file == _rel_path(rep_file) for f in out):
                    out.append(
                        FailureTrace(
                            file=_rel_path(rep_file),
                            rule_or_type="FormatViolation",
                            message="Committed tree had unformatted source (auto-repaired locally)",
                        )
                    )
        except Exception:
            pass
    return out


def parse_mypy(root: Path) -> list[FailureTrace]:
    out: list[FailureTrace] = []
    txt_path = root / "mypy" / "mypy.txt"
    if txt_path.is_file():
        try:
            for line in txt_path.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(
                    r"^([^:\s]+):(\d+)(?::(\d+))?:\s*(error|warning|note):\s*(.+)$", line.strip()
                )
                if m and m.group(4).lower() == "error":
                    msg = m.group(5)
                    code_match = re.search(r"\[([a-z-]+)\]$", msg)
                    rule = f"Mypy[{code_match.group(1)}]" if code_match else "TypeError"
                    col = int(m.group(3)) if m.group(3) and m.group(3).isdigit() else None
                    out.append(
                        FailureTrace(
                            file=_rel_path(m.group(1)),
                            line=int(m.group(2)),
                            column=col,
                            rule_or_type=rule,
                            message=msg,
                        )
                    )
        except Exception:
            pass

    if not out:
        xml_path = root / "mypy" / "mypy-junit.xml"
        if xml_path.is_file():
            try:
                tree = ET.parse(xml_path)
                for tc in tree.getroot().iter("testcase"):
                    for child in tc:
                        if child.tag in ("failure", "error"):
                            tb = child.text or ""
                            frame_file, frame_line = _find_innermost_frame(tb)
                            out.append(
                                FailureTrace(
                                    file=frame_file or _rel_path(tc.attrib.get("file")),
                                    line=frame_line,
                                    rule_or_type=child.attrib.get("type", "TypeError"),
                                    message=child.attrib.get("message", "Type check failed"),
                                    traceback=tb,
                                    test_node=tc.attrib.get("name", ""),
                                )
                            )
            except Exception:
                pass
    return out


def parse_pytest_suite(root: Path, suite_dir: str = "pytest") -> list[FailureTrace]:
    out: list[FailureTrace] = []
    xml_path = root / suite_dir / "junit.xml"
    if xml_path.is_file():
        try:
            tree = ET.parse(xml_path)
            for tc in tree.getroot().iter("testcase"):
                for child in tc:
                    if child.tag in ("failure", "error"):
                        node = f"{tc.attrib.get('classname', '')}::{tc.attrib.get('name', '')}"
                        f_attr = _rel_path(tc.attrib.get("file"))
                        l_attr = tc.attrib.get("line")
                        line_val = int(l_attr) if l_attr and l_attr.isdigit() else None
                        tb = child.text or ""
                        if not f_attr or line_val is None:
                            frame_f, frame_l = _find_innermost_frame(tb)
                            f_attr = f_attr or frame_f
                            line_val = line_val or frame_l
                        out.append(
                            FailureTrace(
                                file=f_attr,
                                line=line_val,
                                rule_or_type=child.attrib.get("type", "AssertionError"),
                                message=child.attrib.get("message", ""),
                                traceback=tb,
                                test_node=node,
                            )
                        )
        except Exception:
            pass

    if not out:
        txt_path = root / suite_dir / "pytest.txt"
        if txt_path.is_file():
            try:
                content = txt_path.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(
                    r"^FAILED\s+([^\s:]+::[^\s]+)(?:\s+-\s+(.+))?$", content, re.MULTILINE
                ):
                    node = m.group(1)
                    file_cand = node.split("::")[0]
                    msg = m.group(2) or "Test assertion failed"
                    out.append(
                        FailureTrace(
                            file=_rel_path(file_cand),
                            rule_or_type="TestFailure",
                            message=msg,
                            test_node=node,
                        )
                    )
            except Exception:
                pass
    return out


def parse_critical_coverage(root: Path) -> list[FailureTrace]:
    out: list[FailureTrace] = []
    txt_path = root / "coverage" / "critical-gate.txt"
    if txt_path.is_file():
        try:
            for line in txt_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if any(k in line for k in ("FAIL", "BELOW FLOOR", "FAILED", "NO_DATA")):
                    m = re.search(r"([A-Za-z0-9_/\\-]+\.py)", line)
                    out.append(
                        FailureTrace(
                            file=_rel_path(m.group(1)) if m else None,
                            rule_or_type="CoverageFloorBreach",
                            message=line.strip(),
                        )
                    )
        except Exception:
            pass
    return out


def parse_coverage(root: Path) -> list[FailureTrace]:
    cov_json = root / "run-info" / "coverage.json"
    if cov_json.is_file():
        try:
            data = json.loads(cov_json.read_text(encoding="utf-8", errors="replace"))
            pct = data.get("percent")
            status = data.get("status")
            if status in ("failed", "errored") or (pct is not None and float(pct) < 80.0):
                return [
                    FailureTrace(
                        rule_or_type="CoverageUnderThreshold",
                        message=data.get("detail", f"Total coverage {pct}%"),
                    )
                ]
        except Exception:
            pass
    return []


def parse_runtime_deps(root: Path) -> list[FailureTrace]:
    out: list[FailureTrace] = []
    json_path = root / "runtime_deps.json"
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
            for missing_dep in data.get("missing", []):
                out.append(
                    FailureTrace(
                        rule_or_type="MissingRuntimeDependency",
                        message=f"Missing runtime dependency: {missing_dep}",
                    )
                )
            for err in data.get("errors", []):
                out.append(
                    FailureTrace(
                        rule_or_type="RuntimeDependencyError",
                        message=str(err),
                    )
                )
        except Exception:
            pass
    log_path = root / "runtime_deps.log"
    if log_path.is_file() and not out:
        try:
            txt = log_path.read_text(encoding="utf-8", errors="replace")
            f, l = _find_innermost_frame(txt)
            if "Traceback" in txt or "ERROR" in txt:
                lines = [line.strip() for line in txt.splitlines() if line.strip()]
                out.append(
                    FailureTrace(
                        file=f,
                        line=l,
                        rule_or_type="DependencyClosureCrash",
                        message=lines[-1] if lines else "Runtime dependency closure check failed",
                        traceback=txt,
                    )
                )
        except Exception:
            pass
    return out


def parse_runtime_gate(root: Path) -> list[FailureTrace]:
    out: list[FailureTrace] = []
    log_path = root / "runtime_gate.log"
    json_path = root / "runtime_gate.json"
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
            for err in data.get("errors", []):
                out.append(
                    FailureTrace(
                        rule_or_type="RuntimeCertificationError",
                        message=str(err),
                    )
                )
        except Exception:
            pass
    if log_path.is_file():
        try:
            txt = log_path.read_text(encoding="utf-8", errors="replace")
            f, l = _find_innermost_frame(txt)
            if "Traceback" in txt and not out:
                lines = [line.strip() for line in txt.splitlines() if line.strip()]
                msg = lines[-1] if lines else "Runtime gate failed"
                out.append(
                    FailureTrace(
                        file=f,
                        line=l,
                        rule_or_type="RuntimeCrash",
                        message=msg,
                        traceback=txt,
                    )
                )
        except Exception:
            pass
    return out


def parse_layered_smoke(root: Path) -> list[FailureTrace]:
    out: list[FailureTrace] = []
    json_path = root / "layered-smoke" / "smoke.json"
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
            for layer in data.get("layers", []):
                if not layer.get("passed", True) or layer.get("status") == "failed":
                    out.append(
                        FailureTrace(
                            rule_or_type=f"LayerSmoke[{layer.get('name', 'L?')}]",
                            message=layer.get("error")
                            or layer.get("detail")
                            or "Layer failed verification",
                        )
                    )
        except Exception:
            pass
    log_path = root / "layered-smoke" / "smoke.log"
    if log_path.is_file() and not out:
        try:
            txt = log_path.read_text(encoding="utf-8", errors="replace")
            if "Traceback" in txt or "ERROR" in txt:
                f, l = _find_innermost_frame(txt)
                lines = [line.strip() for line in txt.splitlines() if line.strip()]
                out.append(
                    FailureTrace(
                        file=f,
                        line=l,
                        rule_or_type="LayeredSmokeCrash",
                        message=lines[-1] if lines else "Layered smoke log contains error",
                        traceback=txt,
                    )
                )
        except Exception:
            pass
    return out


def collect_diagnostics(root: Path, check_name: str, status: str) -> StepDiagnostic:
    info_path = root / "run-info" / f"{check_name}.json"
    detail = ""
    exit_code = 1 if status in ("failed", "errored") else 0
    if info_path.is_file():
        try:
            payload = json.loads(info_path.read_text(encoding="utf-8", errors="replace"))
            detail = payload.get("detail", "")
            exit_code = payload.get("exit_code", exit_code)
        except Exception:
            pass

    diag = StepDiagnostic(check=check_name, status=status, exit_code=exit_code, detail=detail)
    if status not in ("failed", "errored"):
        return diag

    if check_name == "ruff_lint":
        diag.failures = parse_ruff_lint(root)
    elif check_name == "ruff_format":
        diag.failures = parse_ruff_format(root)
    elif check_name == "mypy":
        diag.failures = parse_mypy(root)
    elif check_name == "pytest":
        diag.failures = parse_pytest_suite(root, "pytest")
    elif check_name == "smoke":
        diag.failures = parse_pytest_suite(root, "smoke")
    elif check_name == "coverage":
        diag.failures = parse_coverage(root)
    elif check_name == "critical_coverage":
        diag.failures = parse_critical_coverage(root)
    elif check_name == "runtime_gate":
        diag.failures = parse_runtime_gate(root)
    elif check_name == "runtime_deps":
        diag.failures = parse_runtime_deps(root)
    elif check_name == "layered_smoke":
        diag.failures = parse_layered_smoke(root)

    if not diag.failures:
        diag.failures.append(
            FailureTrace(
                rule_or_type="GateFailure",
                message=detail or f"Check '{check_name}' finished with exit code {exit_code}",
            )
        )
    return diag


def emit_github_annotations(diagnostics: list[StepDiagnostic]) -> None:
    for diag in diagnostics:
        if diag.status not in ("failed", "errored"):
            continue
        for fail in diag.failures:
            props = []
            if fail.file:
                props.append(f"file={fail.file}")
            if fail.line and fail.line > 0:
                props.append(f"line={fail.line}")
            if fail.column and fail.column > 0:
                props.append(f"col={fail.column}")
            t_str = f"{diag.check}" + (f" ({fail.rule_or_type})" if fail.rule_or_type else "")
            props.append(f"title={t_str}")

            msg_text = fail.message or f"Failure in {diag.check}"
            if fail.traceback:
                tb_lines = fail.traceback.strip().splitlines()
                tb_snippet = (
                    "\n".join(tb_lines[-25:]) if len(tb_lines) > 25 else fail.traceback.strip()
                )
                msg_text = f"{msg_text}\n\n[Test Trace / Traceback]:\n{tb_snippet}"

            escaped = msg_text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            if len(escaped) > 4000:
                escaped = escaped[:3980] + "...%0A[truncated]"

            p_str = (" " + ",".join(props)) if props else ""
            print(f"::error{p_str}::{escaped}")


def _utf8_console(width: int = 120) -> Console:
    """A rich console that cannot die on non-UTF-8 default output encodings.

    On the windows-latest CI runner the process locale is cp1252, so a rich
    Console writing to the captured stdout raises UnicodeEncodeError on the
    first non-ASCII glyph (the U+1F534 rule banner). That truncated the gate's
    stdout before the diagnostic traces were emitted, which is exactly what the
    fail-closed regression tests assert on. Route through a UTF-8 text wrapper
    instead so the report renders on every OS leg.
    """

    target: IO[str] = sys.stdout
    try:
        enc = (getattr(sys.stdout, "encoding", None) or "").lower()
        if enc and enc.replace("-", "") not in ("utf8", "utf16", "utf32"):
            wrapper = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
            _UTF8_WRAPPERS.append(wrapper)  # keep the wrapper alive for the process
            target = wrapper
    except Exception:
        pass
    return Console(file=target, width=width, force_terminal=True)


_UTF8_WRAPPERS: list[IO[str]] = []


def render_rich_report(
    statuses: dict[str, str],
    diagnostics: list[StepDiagnostic],
    blocked: list[str],
    failed: list[str],
    missing: list[str],
) -> None:
    if not _RICH_AVAILABLE:
        _render_plain_report(statuses, diagnostics, blocked, failed, missing)
        return

    console = _utf8_console(width=120)
    console.print()
    console.rule("[bold red]🔴 CI GATE RESULTS & DIAGNOSTIC TRACES[/bold red]")
    console.print()

    summary_table = Table(
        title="CI Gate Status Matrix", show_header=True, header_style="bold magenta"
    )
    summary_table.add_column("Check Name", style="bold")
    summary_table.add_column("Status", justify="center")
    summary_table.add_column("Exit Code", justify="center")
    summary_table.add_column("Diagnostics / Violations Summary")

    for diag in diagnostics:
        st = diag.status
        if st == "passed":
            st_text = "[bold green]PASSED[/bold green]"
        elif st == "blocked":
            st_text = "[bold yellow]BLOCKED[/bold yellow]"
        elif st == "missing":
            st_text = "[bold magenta]MISSING[/bold magenta]"
        else:
            st_text = "[bold red]FAILED[/bold red]"

        v_count = len(diag.failures) if diag.failures else 0
        v_summary = f"{v_count} violation(s) / trace(s)" if v_count else (diag.detail or "—")
        summary_table.add_row(diag.check, st_text, str(diag.exit_code), v_summary)

    console.print(summary_table)
    console.print()

    failing_diags = [d for d in diagnostics if d.status in ("failed", "errored")]
    if not failing_diags:
        return

    console.rule("[bold red]Detailed Step Failures, Locations & Traces[/bold red]")
    for diag in failing_diags:
        panel_title = f"[bold white on red] ❌ STEP: {diag.check} [/bold white on red] (exit code {diag.exit_code})"
        detail_table = Table(show_header=True, header_style="bold cyan", expand=True)
        detail_table.add_column("#", width=4)
        detail_table.add_column("Location (File:Line:Col)", style="yellow", width=36)
        detail_table.add_column("Rule / Error Type", style="bold red", width=22)
        detail_table.add_column("Message / Context")

        for idx, f in enumerate(diag.failures, 1):
            loc_str = f.file or "—"
            if f.line:
                loc_str += f":{f.line}"
                if f.column:
                    loc_str += f":{f.column}"
            detail_table.add_row(str(idx), loc_str, f.rule_or_type or "—", f.message or "—")

        console.print(Panel(detail_table, title=panel_title, border_style="red"))

        for f in diag.failures:
            if f.traceback:
                header = (
                    f"[bold red]── Traceback: {f.test_node or f.file or diag.check} ──[/bold red]"
                )
                console.print(header)
                syntax = Syntax(f.traceback.strip(), "python", theme="monokai", line_numbers=False)
                console.print(Panel(syntax, border_style="red", title=f.test_node or "Bug Trace"))
                console.print()


def _render_plain_report(
    statuses: dict[str, str],
    diagnostics: list[StepDiagnostic],
    blocked: list[str],
    failed: list[str],
    missing: list[str],
) -> None:
    failing_diags = [d for d in diagnostics if d.status in ("failed", "errored")]
    if not failing_diags:
        return
    print("\n" + "=" * 70)
    print("DETAILED STEP FAILURES, LOCATIONS & TRACES:")
    print("=" * 70)
    for diag in failing_diags:
        print(f"\n[STEP FAILED: {diag.check} (rc={diag.exit_code})]")
        for idx, f in enumerate(diag.failures, 1):
            loc = f.file or "unknown"
            if f.line:
                loc += f":{f.line}"
                if f.column:
                    loc += f":{f.column}"
            print(f"  {idx}. Location : {loc}")
            print(f"     Type     : {f.rule_or_type}")
            print(f"     Message  : {f.message}")
            if f.test_node:
                print(f"     Test Node: {f.test_node}")
            if f.traceback:
                print("     Traceback:")
                for tb_l in f.traceback.strip().splitlines():
                    print(f"       {tb_l}")


def write_step_summary(root: Path, diagnostics: list[StepDiagnostic]) -> None:
    lines = ["# 🔴 CI Gate Diagnostics Report\n\n"]
    lines.append("| Check | Status | Exit Code | Details |\n")
    lines.append("|:---|:---:|:---:|:---|\n")

    for d in diagnostics:
        icon = "✅" if d.status == "passed" else "⏭️" if d.status == "blocked" else "❌"
        lines.append(f"| `{d.check}` | {icon} {d.status} | `{d.exit_code}` | {d.detail or '—'} |\n")

    failing = [d for d in diagnostics if d.status in ("failed", "errored")]
    if failing:
        lines.append("\n## 💥 Failure Breakdown & Traces\n\n")
        for d in failing:
            lines.append(f"### ❌ `{d.check}`\n\n")
            lines.append("| Location | Rule / Type | Message |\n")
            lines.append("|:---|:---|:---|\n")
            for f in d.failures:
                loc = (
                    f"`{f.file}:{f.line}`"
                    if f.file and f.line
                    else f"`{f.file}`"
                    if f.file
                    else "—"
                )
                lines.append(f"| {loc} | `{f.rule_or_type}` | {f.message} |\n")
            lines.append("\n")

            for f in d.failures:
                if f.traceback:
                    title = f.test_node or f.file or d.check
                    lines.append(
                        f"<details><summary><b>🔍 Bug Trace / Test Trace: {title}</b></summary>\n\n"
                    )
                    lines.append("```python\n")
                    lines.append(f.traceback.strip() + "\n")
                    lines.append("```\n</details>\n\n")

    report_md = "".join(lines)
    summary_env = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_env:
        try:
            with open(summary_env, "a", encoding="utf-8") as fh:
                fh.write(report_md)
        except Exception:
            pass

    out_file = root / "run-info" / "gate_diagnostics.md"
    try:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(report_md, encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    root_str = os.environ.get("CI_RESULTS_DIR", "ci-results")
    root = Path(root_str)
    info_dir = root / "run-info"
    statuses: dict[str, str] = {}
    missing: list[str] = []

    for name in CHECKS:
        path = info_dir / f"{name}.json"
        try:
            with open(path, encoding="utf-8") as fh:
                statuses[name] = json.load(fh).get("status", "skipped")
        except FileNotFoundError:
            statuses[name] = "missing"
            missing.append(name)
        except Exception as e:
            statuses[name] = "errored"
            print(f"UNREADABLE CHECK JSON: {path} -> {e}")

    failed = [k for k, v in statuses.items() if v in ("failed", "errored")]
    blocked = [k for k, v in statuses.items() if v == "blocked"]
    repair = os.environ.get("RUFF_REPAIR_STATUS", "")

    # Preserve exact status line for CI parsers and legacy tests
    print(" | ".join(f"{k}={v}" for k, v in statuses.items()))
    if repair == "repaired":
        print(
            "AUTO_REPAIRED_BUT_SOURCE_DIRTY: ruff format repaired the CHECKOUT;"
            " the COMMITTED tree remains malformed and ruff_format stays failed."
        )
    if blocked:
        print(f"BLOCKED (never ran - upstream root failure): {', '.join(blocked)}")
    if failed:
        print(f"FAILING CHECKS: {', '.join(failed)}")
    if missing:
        print("MISSING RESULTS (never ran, never classified blocked): " + ", ".join(missing))

    diagnostics = [
        collect_diagnostics(root, name, statuses.get(name, "missing")) for name in CHECKS
    ]

    if failed:
        render_rich_report(statuses, diagnostics, blocked, failed, missing)
        emit_github_annotations(diagnostics)
        write_step_summary(root, diagnostics)

    failing = failed + missing
    if failing:
        print(
            f"::error::CI gate failed on: {', '.join(failing)} | "
            + " | ".join(f"{k}={v}" for k, v in statuses.items())
        )
        return 1

    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
