"""Markdown renderer for the NSE PR Evidence Report (spec §§6-20).

Renders an :class:`~nexus_scalp.pr_evidence.models.EvidenceCollection` into the
single ``<!-- NSE-EVIDENCE-REPORT -->`` comment body. Pure function of the
evidence — no network, no side effects, deterministic output (spec §17: the
report is evidence, not an opinion; no subjective scores).

The output stays SMALL (spec §20): summary, failure location, error summary,
bounded traceback excerpts and links. Full logs stay in CI artifacts.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from nexus_scalp.observability.telegram_transport import redact_secrets
from nexus_scalp.pr_evidence.github_client import COMMENT_MARKER as REPORT_MARKER
from nexus_scalp.pr_evidence.models import (
    UNKNOWN,
    CheckResult,
    EvidenceCollection,
    Failure,
    FailureCategory,
    ReportMeta,
    SkippedTest,
    Status,
)

__all__ = ["MAX_MESSAGE_CHARS", "REPORT_MARKER", "render_report", "sanitize"]

#: Hard GitHub comment body limit; we stay far below it (spec §20).
MAX_MESSAGE_CHARS = 60_000

#: Bounded excerpt length for a single error message.
_MAX_MSG = 300

#: Bounded traceback excerpt lines per failure (spec §20: no log dumps).
_MAX_TB_LINES = 6


def sanitize(text: str) -> str:
    """Spec §21 security gate. Never publish credentials or local paths.

    Reuses the repo's canonical secret masker, then masks URL userinfo (a
    scheme://user:pass@host credential shape no repo masker covers, so it
    would otherwise leak verbatim into a published comment), then strips
    any surviving absolute local path to its repository-relative tail.
    """
    if not text:
        return text
    out = redact_secrets(text)
    out = _URL_USERINFO_RE.sub(r"\1\2:***@", out)
    out = _strip_local_paths(out)
    return out


_URL_USERINFO_RE = re.compile(r"((?:[a-zA-Z][a-zA-Z0-9+.\-]{1,50})://)([^:/@\s]+):([^@\s]+)@")


#: Windows ``C:\Users\<anyone>\...\src\x.py`` or POSIX ``/Users/.../src/x.py``.
_LOCAL_PATH_RE = re.compile(
    r"""(?<![\w])(?:[A-Za-z]:[\\/][^\s"'`]*?[\\/]src[\\/][^\s"'`]+)"""
    r"""|(?:/Users|/home|/root)/[^\s"'`]*?/src/[^\s"'`]+""",
    re.IGNORECASE,
)


def _strip_local_paths(text: str) -> str:
    """Re-anchor an absolute local path to its repository-relative tail."""
    return _LOCAL_PATH_RE.sub(_reanchor, text)


def _reanchor(match: re.Match[str]) -> str:
    from nexus_scalp.pr_evidence.locations import normalize_repo_relative

    return normalize_repo_relative(match.group(0))


def render_report(evidence: EvidenceCollection, *, marker: str = REPORT_MARKER) -> str:
    """Render the full evidence report (all spec sections present in evidence)."""
    meta = evidence.meta
    status = evidence.status
    failures = [f.with_unknowns() for f in evidence.all_failures()]
    body = "\n\n".join(
        part
        for part in (
            _header(status, meta),
            _revision(meta),
            _overall(status, failures),
            _test_matrix(evidence),
            _affected_files(failures),
            _failed_tests(failures),
            _skipped(evidence.skipped),
            _failure_chains(failures),
            _checks(evidence.checks),
            _local_vs_ci(evidence),
            _gaps(evidence),
            _merge_verdict(evidence),
            _legend(),
        )
        if part
    )
    out = f"{marker}\n{body}"
    out = sanitize(out)
    if len(out) > MAX_MESSAGE_CHARS:
        out = _truncate(out)
    return out


def _truncate(out: str) -> str:
    """Trim to the comment limit WITHOUT losing the final verdict.

    The merge verdict is rendered last, so a naive tail-cut would delete the
    one line the reader came for. Cut the detail sections instead and re-append
    the verdict block.
    """
    budget = MAX_MESSAGE_CHARS - 200
    if len(out) <= budget:
        return out
    tail_start = out.rfind("Final Verdict")
    tail_start = out.rfind("\n##", 0, tail_start) if tail_start > 0 else -1
    tail = out[tail_start:] if tail_start > 0 else ""
    cut = out[: budget - len(tail) - 120]
    last_fence = cut.rfind("\n```")
    if last_fence > 1000:
        cut = cut[:last_fence]
    note = "\n\n> ... truncated — see the linked CI checks for full logs (spec §20).\n"
    return f"{cut}{note}{tail}"


def _merge_verdict(evidence: EvidenceCollection) -> str:
    """The FINAL stage: "Merge? Yes/No" with the evidence behind the answer.

    Rendered last on purpose — it is the report's conclusion, and everything
    above it (checks, failures, affected files) is the evidence for it.

    Deterministic: every line cites an observed fact, and anything that could
    not be read is listed as a gap rather than assumed satisfied.
    """
    verdict = evidence.merge_verdict()
    lines = [f"## {verdict.dot} Final Verdict — Merge? {verdict.headline}", ""]

    blockers = verdict.blockers
    if verdict.state == "NO":
        lines.append(f"**{len(blockers)} blocker(s) prevent this merge:**")
        lines.append("")
        lines.extend(f"{i}. {r.detail}" for i, r in enumerate(blockers, 1))
        advisory = [r for r in verdict.reasons if not r.blocking]
        if advisory:
            lines.append("")
            lines.extend(f"- {r.detail}" for r in advisory)
    elif verdict.state == "YES":
        lines.append("Every merge precondition observed is satisfied:")
        lines.append("")
        lines.extend(f"- {r.detail}" for r in verdict.reasons)
    elif verdict.state == "MERGED":
        lines.append("Already merged — mergeability is history, not a question:")
        lines.append("")
        lines.extend(f"- {r.detail}" for r in verdict.reasons)
    else:
        lines.append("No blocking evidence was found, but some preconditions could not be read:")
        lines.append("")
        lines.extend(f"- {r.detail}" for r in verdict.reasons)

    # Base-branch protection summary: what the verdict was measured against.
    if evidence.required_checks:
        lines.append("")
        lines.append(
            f"_Base branch requires {len(evidence.required_checks)} check(s)_"
            + (
                f" _and {evidence.required_reviews} approving review(s)._"
                if evidence.required_reviews
                else "."
            )
        )
    return "\n".join(lines)


def _header(status: Status, meta: ReportMeta) -> str:
    return (
        f"## {status.dot} NSE Evidence Report — #{meta.pr}\n\n"
        f"**Status:** {status.value} · **Branch:** `{meta.branch or UNKNOWN}` → "
        f"`{meta.base_branch or UNKNOWN}` · **HEAD:** `{_short(meta.pr_head_sha)}`"
    )


def _short(sha: str) -> str:
    if not sha or sha == UNKNOWN:
        return UNKNOWN
    return sha[:_SHA_SHORT]


_SHA_SHORT = 12


def _revision(meta: ReportMeta) -> str:
    rows = [
        ("PR HEAD", meta.pr_head_sha),
        ("Local", meta.local_head_sha),
        ("CI", meta.ci_head_sha),
    ]
    out = "## 🔗 Revision\n\n| Source | SHA |\n|---|---|\n"
    out += "\n".join(f"| {label} | `{_short(sha)}` |" for label, sha in rows)
    if not meta.heads_match:
        out += "\n\n> ⚠️ **Evidence is from different revisions.** "
        out += "Local and CI results below may describe different commits."
    return out


def _overall(status: Status, failures: list[Failure]) -> str:
    lines = [f"## Overall\n\n{status.dot} **{status.value}**"]
    if status == Status.FAIL:
        lines.append("\n\nRequired CI checks contain failures.\n\n")
        lines.append("**Primary affected files:**\n")
        seen: set[str] = set()
        for f in failures:
            for path in f.affected_paths():
                if path in seen:
                    continue
                seen.add(path)
                loc = (
                    f.location if f.location.path == path else (f.production_location or f.location)
                )
                lines.append(f"{len(seen)}. `{loc.rendered()}`")
            if len(seen) >= 5:
                break
    elif status == Status.UNKNOWN:
        lines.append(
            "\n\nNo check results or failures were collected. The PR may have "
            "no CI runs yet, or the GitHub API was unreachable (see "
            "**Evidence gaps** below)."
        )
    elif status == Status.IN_PROGRESS:
        lines.append("\n\nCI checks are still running; this report updates as they complete.")
    elif status == Status.BLOCKED:
        lines.append(
            "\n\nNo hard failures collected, but skipped/blocked tests exist. "
            "A skipped test is **not** a pass."
        )
    return "".join(lines)


def _test_matrix(evidence: EvidenceCollection) -> str:
    """Spec §12: the matrix is always rendered; only COLLECTED values are shown."""
    rows: list[dict[str, str]] = []
    for check in evidence.checks:
        rows.append(_check_matrix_row(check))
    for local in evidence.test_matrix:
        rows.append(local)
    header = (
        "| Gate | Status | Passed | Failed | Skipped | Duration |\n|---|---|---:|---:|---:|---:|"
    )
    lines = [header]
    if not rows:
        lines.append("| — | ⚪ | — | — | — | — |")
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                str(row.get(key, "—"))
                for key in ("Gate", "Status", "Passed", "Failed", "Skipped", "Duration")
            )
            + " |"
        )
    return "## 🧪 Test Matrix\n\n" + "\n".join(lines)


def _check_matrix_row(check: CheckResult) -> dict[str, str]:
    icon = {
        "SUCCESS": "✅",
        "FAILURE": "❌",
        "SKIPPED": "⏭️",
        "CANCELLED": "❌",
        "TIMED_OUT": "❌",
        "ACTION_REQUIRED": "🟠",
    }.get(check.conclusion, "")
    if not icon:
        icon = "🟡" if check.in_progress else ("❌" if check.is_failure else "⚪")
    return {
        "Gate": check.name,
        "Status": icon,
        "Passed": "—",
        "Failed": "1" if check.is_failure else "0",
        "Skipped": "—",
        "Duration": _duration(check.started_at, check.completed_at),
    }


def _duration(start: str, end: str) -> str:
    if not start or not end or UNKNOWN in (start, end):
        return "—"
    from datetime import datetime

    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return "—"
    secs = int((e - s).total_seconds())
    if secs < 0:
        return "—"
    if secs >= 60:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs}s"


def _group_by_file(failures: list[Failure]) -> dict[str, list[Failure]]:
    by_file: dict[str, list[Failure]] = defaultdict(list)
    for f in failures:
        for path in f.affected_paths() or [UNKNOWN]:
            by_file[path].append(f)
    return by_file


def _affected_files(failures: list[Failure]) -> str:
    """Spec §8 (mandatory): failures grouped by affected file."""
    by_file = _group_by_file(failures)
    if not by_file:
        return ""
    lines = ["## 🔴 Affected Files"]
    # A located failure whose file could not be determined collapses into ONE
    # "not determined" group instead of repeating ``unknown`` per failure
    # (spec §3: unknown is a sentinel, never a fabricated location).
    unresolved = by_file.pop(UNKNOWN, None)
    for path in sorted(by_file):
        group = by_file[path]
        lines.append(f"\n### `{path}`\n")
        for f in group:
            lines.append(_affected_bullet(f, path))
    if unresolved:
        gates = sorted({f.check for f in unresolved if f.check != UNKNOWN})
        lines.append("\n### Location not determined from available evidence\n")
        for f in unresolved:
            bullet = f"- {f.error_type}"
            if f.check != UNKNOWN:
                bullet += f"\n  - CI: `{f.check}`"
            lines.append(bullet)
        if gates:
            lines.append(f"\n_Failing gates: {', '.join(f'`{g}`' for g in gates)}_")
    return "\n".join(lines)


def _affected_bullet(f: Failure, path: str) -> str:
    loc = f.location if f.location.path == path else (f.production_location or f.location)
    bullet = f"- `{loc.rendered()}`"
    if loc.function != UNKNOWN:
        bullet += f" — `{loc.function}()`"
    if f.test != UNKNOWN:
        bullet += f"\n  - `{f.test}`"
    bullet += f"\n  - {f.error_type}"
    if f.check != UNKNOWN:
        bullet += f"\n  - CI: `{f.check}`"
    return bullet


def _failed_tests(failures: list[Failure]) -> str:
    """Where it failed and why — for EVERY failure, not only pytest ones.

    The previous shape filtered to failures with a test id, which silently
    dropped every CI/annotation/security finding (they carry no test name), so
    a red PR could publish with no explanation of what failed. Each entry now
    states: location (file:line:col), the gate that produced it, the tool's own
    error/why text, and GitHub's remediation wording when the scanner supplied
    one. Nothing here is invented — a field the evidence lacks says so.
    """
    if not failures:
        return ""
    lines = ["## 🔴 What Failed and Why"]
    for i, f in enumerate(failures, start=1):
        title = (
            f.test if f.test != UNKNOWN else (f.error_type if f.error_type != UNKNOWN else f.check)
        )
        lines.append(f"\n### {i}. `{title}`\n")
        if f.severity != UNKNOWN:
            lines.append(f"**Severity:** {f.severity}\n")
        lines.append(f"**Where:** `{f.location.rendered()}`\n")
        if f.production_location is not None and f.production_location.path != UNKNOWN:
            lines.append(f"**Implementation file:** `{f.production_location.rendered()}`\n")
        elif f.test != UNKNOWN:
            # Spec §6: say so explicitly, so a test-file location is never
            # silently read as the cause of the failure.
            lines.append("**Implementation file:** Not determined from available evidence\n")
        if f.location.function != UNKNOWN:
            lines.append(f"**Function:** `{f.location.function}()`\n")
        if f.category != FailureCategory.UNKNOWN:
            lines.append(f"**Kind:** {f.category}\n")
        if f.error_type != UNKNOWN:
            lines.append(f"**Error / rule:** `{f.error_type}`\n")
        if f.message != UNKNOWN:
            lines.append(f"**Why:**\n\n```text\n{sanitize(f.message[:_MAX_MSG])}\n```\n")
        if f.remediation != UNKNOWN:
            lines.append(f"**How to fix (the scanner's own wording):** {sanitize(f.remediation)}\n")
        if f.rule_url != UNKNOWN:
            lines.append(f"**Finding:** {f.rule_url}\n")
        if f.check != UNKNOWN:
            where = f"**CI:** `{f.check}`"
            if f.check_url != UNKNOWN:
                where += f" ([logs]({f.check_url}))"
            lines.append(where + "\n")
        if f.source != UNKNOWN or f.evidence_source != UNKNOWN:
            lines.append(f"**Evidence:** source `{f.source}`, via `{f.evidence_source}`\n")
        if f.commit != UNKNOWN:
            lines.append(f"**Commit:** `{_short(f.commit)}`\n")
    return "\n".join(lines)


def _skipped(skipped: list[SkippedTest]) -> str:
    if not skipped:
        return ""
    lines = ["## ⚠️ Skipped / Blocked\n"]
    for s in skipped:
        lines.append(f"### `{s.test}`\n")
        lines.append(f"**File:** `{s.location.rendered()}`\n")
        lines.append(f"**Reason:** `{sanitize(s.reason[:_MAX_MSG])}`\n")
        lines.append(f"**Classification:** `{s.category}`\n")
    return "\n".join(lines)


def _failure_chains(failures: list[Failure]) -> str:
    """Spec §7: CI Job → Test → Test File → Exception → Production File → Line."""
    chains = [f for f in failures if f.production_location is not None]
    if not chains:
        return ""
    lines = ["## 🔗 Failure Chain"]
    for f in chains[:6]:
        steps = [
            f.check if f.check != UNKNOWN else f.workflow,
            f.test,
            f.location.rendered(),
            f.error_type,
        ]
        prod = f.production_location
        if prod is not None and prod.path != UNKNOWN:
            steps.append(prod.rendered())
            if prod.function != UNKNOWN:
                steps.append(f"{prod.function}()")
        tree = "\n".join(_tree_lines(steps))
        lines.append(f"\n```\n{tree}\n```")
    return "\n".join(lines)


def _tree_lines(steps: list[str]) -> Iterable[str]:
    for i, step in enumerate(steps):
        if not step or step == UNKNOWN:
            continue
        prefix = "  └─ " if i else ""
        yield f"{prefix}{step}"


def _checks(checks: list[CheckResult]) -> str:
    if not checks:
        return ""
    lines = ["## 🏗️ CI Jobs\n", "| Workflow | Job | Status | Details |", "|---|---|---|---|"]
    for c in checks:
        status_icon = {
            "SUCCESS": "✅",
            "FAILURE": "❌",
            "SKIPPED": "⏭️",
            "CANCELLED": "❌",
            "TIMED_OUT": "❌",
            "ACTION_REQUIRED": "🟠",
        }.get(c.conclusion, "🟡" if c.in_progress else "⚪")
        url = f"[logs]({c.url})" if c.url and c.url != UNKNOWN else "—"
        lines.append(f"| `{c.workflow}` | `{c.job}` | {status_icon} {c.conclusion} | {url} |")
    return "\n".join(lines)


def _local_vs_ci(evidence: EvidenceCollection) -> str:
    """Spec §15: only shown when local evidence was actually collected."""
    if not evidence.local_failures and not evidence.test_matrix:
        return ""
    lines = ["## 🔀 Local vs CI\n", "| Check | Local | CI |", "|---|---|---|"]
    local_by_gate = {row["Gate"]: row for row in evidence.test_matrix}
    ci_by_name = {c.name: c for c in evidence.checks}
    keys = sorted(set(local_by_gate) | set(ci_by_name))
    for key in keys:
        local = local_by_gate.get(key)
        ci = ci_by_name.get(key)
        local_txt = local["Status"] if local else "—"
        ci_txt = {"SUCCESS": "✅", "FAILURE": "❌"}.get(ci.conclusion, "—") if ci else "—"
        lines.append(f"| `{key}` | {local_txt} | {ci_txt} |")
    diff = _local_ci_difference(local_by_gate, ci_by_name)
    if diff:
        lines.append(f"\n### Important Difference\n\n{diff}")
    return "\n".join(lines)


def _local_ci_difference(
    local_by_gate: dict[str, dict[str, str]],
    ci_by_name: dict[str, CheckResult],
) -> str:
    """Describe only REAL local-vs-CI disagreements (never invented)."""
    notes: list[str] = []
    for key, local in local_by_gate.items():
        ci = ci_by_name.get(key)
        if not ci:
            continue
        local_ok = local.get("Status") == "✅"
        ci_failed = ci.is_failure
        if local_ok and ci_failed:
            notes.append(
                f"`{key}` passed locally but failed in CI — the local run may "
                f"have skipped an environment-dependent path."
            )
        elif not local_ok and not ci_failed:
            notes.append(f"`{key}` failed locally but passed in CI.")
    return "\n\n".join(notes)


def _gaps(evidence: EvidenceCollection) -> str:
    """Honest disclosure of what could not be collected (spec §16/§17/§20)."""
    lines: list[str] = []
    if evidence.errors:
        lines.append("### Evidence gaps\n")
        for err in evidence.errors[:8]:
            lines.append(f"- {sanitize(err)}")
    if not evidence.checks:
        lines.append("- No CI check results were collected for this HEAD.")
    if not lines:
        return ""
    return "## Evidence\n\n" + "\n".join(lines)


def _legend() -> str:
    return (
        "<sub>NSE Evidence Report — generated by `nse pr report`. "
        "Statuses are deterministic: 🟢 PASS · 🟡 IN PROGRESS · 🟠 BLOCKED · "
        "🔴 FAIL · ⚪ UNKNOWN. `unknown` = the evidence did not determine it. "
        "This comment is updated in place; it never duplicates.</sub>"
    )
