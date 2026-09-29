"""Job step-log deep extraction for checks with no usable diagnostic surface.

Some failed checks carry nothing but an exit code:

* GitHub's **Copilot code-scanning agent** (``github-advanced-security``) —
  ``dynamic/agents/*.md`` prompts a hosted Copilot session; the run's only
  annotation is ``.github:218 → "Process completed with exit code 1."`` and
  its check-run output is empty.
* Third-party apps that fail inside a wrapped runtime.

The real cause lives in the Actions **job log**: for the Copilot agent it is
``SessionModelError: Execution failed: CAPIError: 400 The requested model is
not supported.`` — a server-side model-availability problem that has nothing
to do with the PR diff, and which the user must see instead of a bare exit
code.

This module fetches the job log for a failed check-run and extracts the
highest-signal diagnostic lines, bounded and redacted.

Security: logs routinely embed bearer tokens, runner temp paths that act as
secrets, and Copilot/C teardown curls. Every line is passed through
``redact_secrets`` before it is stored or rendered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from nexus_scalp.pr_evidence.locations import extract_error_type, is_usable_path
from nexus_scalp.pr_evidence.models import (
    UNKNOWN,
    Failure,
    FailureCategory,
    SourceLocation,
)

# --------------------------------------------------------------------------
# Budgets — the report must never dump whole logs.
# --------------------------------------------------------------------------

#: Max characters of a job log we will buffer before slicing it.
_MAX_LOG_BYTES = 200_000
#: Max diagnostic lines kept per failed job.
_MAX_LINES_PER_JOB = 6
#: Max characters of any single surfaced line.
_MAX_LINE_CHARS = 700
#: Max characters of the surfaced traceback excerpt.
_MAX_EXCERPT_CHARS = 1_600
#: Max characters of the rendered "what was happening" step context.
_MAX_CONTEXT_CHARS = 900

# --------------------------------------------------------------------------
# Patterns that mark a genuinely informative line. Order = preference.
# --------------------------------------------------------------------------

#: A raised exception with a type — the single highest-signal shape.
#: Not anchored: the Copilot log embeds the type mid-line
#: (``Error creating PR review request: SessionModelError: ...``).
_RE_EXCEPTION = re.compile(
    r"(?:[A-Za-z_$][\w$.]*?(?:Error|Exception|Warning)[\w$.]*?)(?::\s*|\s*:\s*|\s+)"
)
#: ``##[error]`` / ``::error`` GitHub command annotations.
_RE_GH_ERROR = re.compile(r"^##\[error\]|^::error\b", re.IGNORECASE)
#: ``Error:`` / ``ERROR ...`` / ``failed:`` free-form markers.
_RE_ERROR_WORD = re.compile(
    r"\b(error|failed|failure|cannot|could not|not supported|invalid|"
    r"unauthorized|forbidden|timed out|aborted|denied)\b",
    re.IGNORECASE,
)
#: Lines we never want to surface — runner bookkeeping and shell noise.
_RE_NOISE = re.compile(
    r"^(##\[group\]|##\[endgroup\]|shell:|env:|  [A-Za-z_]+=|"
    r"Cleaning up|Terminate orphan|Removing |hint:|Note: switching|"
    r"You are in 'detached|If you want to create|changes and (?:discard|keep)|"
    r"state without impacting|\*$|\s*$|"
    # Shell scaffolding: retry/download guards read like failures but are not.
    r"if \[ |set -|mkdir -p|curl |/usr/bin/|\"\$|RETRY_COUNT|MAX_RETRIES|"
    r"Stopping git-proxy|Resolved repo directory|Running in CCA|"
    r"\[memory\]|\[debug\]|\[skills\]|Built prompt|Sessions disabled|"
    r"Reporting error to sweagentd|Error successfully reported|"
    r"Using default CAPI|Using copilot CLI|Pre-cache|Download Autofind|"
    r"Generate agent firewall|Start MCP Servers|Setup Agent|"
    r"Download git-proxy|Prepare Copilot|Validate |Initialize|"
    r"Clean Up|Complete job|Set up job|Checkout repository)",
    re.IGNORECASE,
)
#: A Copilot/agent model-availability failure — the canonical fix-by-waiting case.
_RE_MODEL_NOT_SUPPORTED = re.compile(
    r"The requested model is not supported|model is not (?:available|supported)|"
    r"model_not_found|No such model",
    re.IGNORECASE,
)
#: ``something.py:123`` — a real source location inside a log.
_RE_FILE_LINE = re.compile(
    r"([A-Za-z0-9_@./\-]+\.(?:py|ts|js|go|rs|java|rb|yml|yaml|json|toml|md)):(\d+)"
)
#: GitHub Actions timestamp prefix: ``2026-09-29T19:57:59.2170982Z`` or
#: ``2026-09-29T19:57:59Z`` (the logs API uses both shapes).
_RE_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s*")
#: The ``Processing Request (Linux)`` step-name prefix in Copilot logs.
_RE_STEP_PREFIX = re.compile(r"^[^(]*\([^)]*\)\s*")


def _is_runner_scratch(path: str) -> bool:
    """True for runner-temp JS bundles (``/home/runner/...``), not repo source."""
    low = path.lower()
    return (
        "/home/runner/" in low
        or "/_temp/" in low
        or low.startswith("/tmp/")
        or "node_modules" in low
        or "app.js" in low
        or "autofind.js" in low
    )


def _clean_step(step: str) -> str:
    """``Processing Request (Linux)`` → ``Processing Request``."""
    if not step or step == UNKNOWN:
        return ""
    return _RE_STEP_TAIL.sub("", step).strip()


_RE_STEP_TAIL = re.compile(r"\s*\([^)]*\)\s*$")


#: Actions log rows are ``<step>\t<timestamp> <body>``; strip both.
_RE_STEP_TS_PREFIX = re.compile(r"^[^\t]*\t\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*")


def _strip_ts(line: str) -> str:
    """Drop the Actions step/timestamp prefix so the surfaced line is readable.

    Handles both row shapes the logs API returns: ``<ts> <body>`` and
    ``<step>\\t<ts> <body>``.
    """
    line = _RE_STEP_TS_PREFIX.sub("", line)
    return _RE_TS.sub("", line).rstrip()


def _bounded(text: str, limit: int) -> str:
    """Truncate with an ellipsis marker, never mid-escape-sequence."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Do not leave a dangling ANSI/escape sequence.
    if "\x1b[" in cut[-12:]:
        cut = cut[: cut.rfind("\x1b[")]
    return cut.rstrip() + " …"


@dataclass
class StepDiagnostic:
    """One extracted signal from a job log."""

    #: Human label, e.g. ``"Copilot model error"`` or ``"Raised exception"``.
    label: str
    #: The cleaned, redacted line.
    line: str
    #: Bounded surrounding context (the lines around it), redacted.
    context: str = ""
    #: True when the failure is infrastructure, not the PR diff.
    infrastructure: bool = False

    def render(self) -> str:
        out = f"{self.label}: {self.line}"
        if self.context:
            out += f"\n{self.context}"
        return out


@dataclass
class LogDiagnostics:
    """Deep diagnostics harvested from a failed check-run's job log."""

    check_run_id: int
    job_name: str = UNKNOWN
    failed_step: str = UNKNOWN
    diagnostics: list[StepDiagnostic] = field(default_factory=list)
    #: Bounded traceback excerpt, redacted (empty when the log has none).
    traceback: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.diagnostics and not self.traceback

    def to_failures(self, workflow: str, job: str, check: str, url: str) -> list[Failure]:
        """Convert into :class:`Failure` rows for the evidence report."""
        out: list[Failure] = []
        seen: set[str] = set()

        for d in self.diagnostics:
            key = d.line.strip()
            if not key or key in seen:
                continue
            seen.add(key)

            # A log line may still name a real source file — keep it, but only
            # when it is a repo path, not the runner's scratch JS bundle.
            m = _RE_FILE_LINE.search(d.line)
            if m and is_usable_path(m.group(1)) and not _is_runner_scratch(m.group(1)):
                path, line_no = m.group(1), int(m.group(2))
            else:
                line_no = None
                # ``.github`` and ``dynamic/agents/...`` are workflow/agent
                # coordinates, not repo source — the failed step is the where.
                path = _clean_step(self.failed_step) or check

            category = (
                FailureCategory.SECURITY_FINDING
                if "Copilot" in d.label or d.infrastructure
                else FailureCategory.CI_FAILURE
            )

            out.append(
                Failure(
                    source="job-logs",
                    suite=workflow,
                    location=SourceLocation(path, line_no, None),
                    error_type=extract_error_type(d.line) or UNKNOWN,
                    message=_bounded(d.line, _MAX_LINE_CHARS),
                    category=category,
                    workflow=workflow,
                    job=job,
                    check=check,
                    check_url=url,
                    evidence_source="job-logs",
                    infrastructure=d.infrastructure,
                    traceback=self.traceback or UNKNOWN,
                )
            )
        return out


# --------------------------------------------------------------------------
# Log slicing
# --------------------------------------------------------------------------


def _find_exception_block(lines: list[str]) -> tuple[int, int] | None:
    """Locate the last raised-exception block (start, end-exclusive).

    The Copilot log's exception row is
    ``Error creating PR review request: SessionModelError: Execution failed:
    CAPIError: 400 ... (errorDetails: {"...stack": "SessionModelError:\\n at ..."})``
    — the type is not at column 0, so match anywhere in the line.
    """
    start = -1
    for i in range(len(lines) - 1, -1, -1):
        body = _strip_ts(lines[i])
        if _RE_EXCEPTION.search(body) and " at " not in body[:6]:
            start = i
            break
    if start < 0:
        return None
    end = start + 1
    # Walk forward to the end of the printed stack / error object.
    while end < len(lines):
        body = _strip_ts(lines[end])
        if not body.strip():
            break
        if body.strip() == "}" and end > start:
            end += 1
            break
        end += 1
        if end - start > 24:  # cap the block walk
            break
    return start, min(end, start + 24)


def _classify_line(body: str) -> str | None:
    """Return the diagnostic label for a line, or None to skip it."""
    if _RE_NOISE.match(body):
        return None
    # Shell retry guards read like failures but are scaffolding, not causes.
    if "RETRY_COUNT" in body or "MAX_RETRIES" in body:
        return None
    # Inline shell scaffolding inside the action's own wrapper.
    if body.lstrip().startswith(("#", "FALLBACK_FILE", "RUNNER_TEMP")):
        return None
    if "${" in body or body.startswith("  "):
        return None
    # The action's own fallback-annotation shell comment.
    if "fallback error annotations" in body:
        return None
    # Env-var bookkeeping the wrapper prints (``COPILOT_*``, ``env:`` rows).
    if re.match(r"^[A-Z][A-Z0-9_]{2,}:", body):
        return None
    # A missing ``cca-setup`` file is the agent's own diagnostic channel being
    # empty — it never contains the failure and only adds noise.
    if "cca-setup error log" in body:
        return None
    if _RE_MODEL_NOT_SUPPORTED.search(body):
        return "Copilot model error"
    if _RE_EXCEPTION.match(body):
        return "Raised exception"
    if _RE_GH_ERROR.match(body):
        return "CI error"
    if _RE_ERROR_WORD.search(body):
        return "Log error"
    return None


def parse_job_log(log_text: str, job_name: str = UNKNOWN) -> LogDiagnostics:
    """Extract the highest-signal diagnostics from a raw Actions job log.

    ``log_text`` is the full ``gh api .../actions/jobs/<id>/logs`` body. It is
    a plain text stream with ``<step>\t<timestamp> <line>`` rows and embedded
    ``##[group]``/``##[error]`` commands.
    """
    redact = _redact_secrets
    # Redact immediately so no secret ever enters the pipeline.
    text = redact(log_text[:_MAX_LOG_BYTES])

    out = LogDiagnostics(check_run_id=0, job_name=job_name)
    lines = text.splitlines()

    # The failed step comes from the job's step list (the streamed log has no
    # per-step prefix in this shape); the caller resolves it via get_job_steps.
    failed_step = job_name if job_name and job_name != UNKNOWN else UNKNOWN
    for raw in lines:
        body = _strip_ts(raw)
        if body.startswith("##[error]"):
            # Attempt #1: a ``<step>\t<ts> ##[error]`` row.
            prefix = body.split("##[error]")[0]
            prefix = _RE_STEP_PREFIX.sub("", prefix).strip()
            if prefix:
                failed_step = prefix
            break
    out.failed_step = failed_step or UNKNOWN

    # ---- traceback excerpt -------------------------------------------------
    block = _find_exception_block(lines)
    if block is not None:
        start, end = block
        excerpt = "\n".join(
            _bounded(_strip_ts(lines[i]), _MAX_LINE_CHARS) for i in range(start, end)
        )
        if excerpt.strip():
            out.traceback = _bounded(excerpt, _MAX_EXCERPT_CHARS)

    # ---- diagnostic lines --------------------------------------------------
    scored: list[tuple[int, str, str]] = []
    for idx, raw in enumerate(lines):
        body = _strip_ts(raw)
        label = _classify_line(body)
        if label is None:
            continue
        if label == "Log error" and not _looks_genuine(body):
            continue
        scored.append((idx, label, _bounded(body, _MAX_LINE_CHARS)))

    # Prefer exception-shaped lines, then model errors, then plain errors.
    order = {
        "Copilot model error": 0,
        "Raised exception": 1,
        "CI error": 2,
        "Log error": 3,
    }
    scored.sort(key=lambda t: order.get(t[1], 9))

    emitted: set[str] = set()
    for idx, label, line in scored[:_MAX_LINES_PER_JOB]:
        key = line.strip()
        # Drop echo lines: ``[cause]: CAPIError ...`` repeats the primary
        # exception already surfaced, and GitHub's own exit-code line adds
        # nothing once the real cause is known.
        if _is_echo(key) or key in emitted:
            continue
        emitted.add(key)
        ctx = _context(lines, idx)
        out.diagnostics.append(
            StepDiagnostic(
                label=label,
                line=line,
                context=_bounded(ctx, _MAX_CONTEXT_CHARS),
                infrastructure=bool(_RE_MODEL_NOT_SUPPORTED.search(line)),
            )
        )

    if not out.diagnostics and out.traceback:
        # Fall back to the traceback head as the single diagnostic.
        head = out.traceback.splitlines()[0]
        out.diagnostics.append(
            StepDiagnostic(
                label="Raised exception",
                line=head,
                context="",
                infrastructure=bool(_RE_MODEL_NOT_SUPPORTED.search(head)),
            )
        )
    return out


def _looks_genuine(body: str) -> bool:
    """Heuristic gate: a ``Log error`` line should read like a failure cause.

    Rejects shell bookkeeping that merely contains the word ``error``.
    """
    low = body.lower()
    if any(
        low.startswith(p)
        for p in (
            "removing ",
            "cleaning",
            "terminate",
            "hint:",
            "note:",
            "shell:",
            "env:",
            "failed to download runtime",
        )
    ):
        return False
    # Needs at least one alphabetic run beyond the trigger word.
    return bool(re.search(r"[a-z]{4,}", low))


_RE_ECHO = re.compile(
    r"^(\[cause\]|\$t \[|\}\s*$|at async |at runNextTicks|at process\.processImmediate|"
    r"##\[error\]Process completed with exit code \d)"
)


def _is_echo(line: str) -> bool:
    """True for lines that repeat an already-surfaced exception."""
    return bool(_RE_ECHO.match(line))


def _context(lines: list[str], idx: int, before: int = 2, after: int = 2) -> str:
    """Bounded window of surrounding log lines, redacted already."""
    lo = max(0, idx - before)
    hi = min(len(lines), idx + after + 1)
    parts: list[str] = []
    for i in range(lo, hi):
        body = _strip_ts(lines[i])
        if not body.strip():
            continue
        marker = "→" if i == idx else " "
        parts.append(f"{marker} {body.strip()}")
    return "\n".join(parts)


def _redact_secrets(text: str) -> str:
    """Mask secret-shaped strings before any line is stored or rendered.

    Defers to the shared redactor when importable; otherwise falls back to a
    local generic-secret mask so redaction never becomes a hard failure.
    """
    if not text:
        return text
    try:
        from nexus_scalp.observability.telegram_transport import (
            redact_secrets as _impl,
        )

        return _impl(text)
    except Exception:  # pragma: no cover - defensive fallback
        return _GENERIC_SECRET_FALLBACK.sub(r"\1=[REDACTED]", text)


_GENERIC_SECRET_FALLBACK = re.compile(
    r"(?i)(password|passwd|secret|api[_-]?key|token|auth|credential)\s*[=:]\s*[^\s,;&'\"]+"
)


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

#: Check-run names whose failure lives in a hosted agent runtime rather than
#: in repo source. Fetching their job log is the ONLY way to surface the cause.
#: ``code scanning AI findings on PR #NNN`` is the workflow RUN name GitHub
#: assigns to the Copilot agent — the CHECK name is ``github-advanced-security``.
_AGENT_CHECK_NAMES = (
    "github-advanced-security",
    "code scanning ai findings",
    "copilot",
    "autofind",
)


def is_agent_check(check_name: str) -> bool:
    """True when a failed check's cause lives in its job log, not the diff."""
    if not check_name or check_name == UNKNOWN:
        return False
    low = check_name.lower()
    return any(tok in low for tok in _AGENT_CHECK_NAMES)


__all__ = [
    "LogDiagnostics",
    "StepDiagnostic",
    "is_agent_check",
    "parse_job_log",
]
