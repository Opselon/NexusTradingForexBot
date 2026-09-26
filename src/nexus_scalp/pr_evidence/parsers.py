"""Parsers for test/gate output: pytest, ruff, mypy — any PR, any suite.

Spec §3: parsers fill as many structured fields as the output supports and
leave the rest at ``unknown``. They must NEVER guess, and must tolerate
malformed output by returning what they can instead of raising.

BOUNDARY: pure functions over text. No network, no filesystem writes.
"""

from __future__ import annotations

import re

from nexus_scalp.pr_evidence.locations import (
    extract_error_type,
    extract_locations,
    extract_test_nodeid,
    parse_pytest_traceback,
)
from nexus_scalp.pr_evidence.models import (
    UNKNOWN,
    Failure,
    FailureCategory,
    SkippedTest,
    SourceLocation,
)

__all__ = [
    "parse_mypy_output",
    "parse_pytest_output",
    "parse_ruff_output",
    "parse_short_test_summary",
]


_RE_FAILURE_HEADER = re.compile(r"_{3,}\s+([^\s].*?)\s+_{3,}")
_RE_NODEID_LINE = re.compile(
    r"^(?P<file>[A-Za-z0-9_@./+-]+\.(?:py|js|ts|tsx|jsx)):(?P<line>\d+): in (?P<test>[A-Za-z_][\w.\[\]':-]*)\s*$",
    re.MULTILINE,
)
_RE_NODEID = re.compile(r"^(?P<nodeid>[\w./-]+::[^\s]+)\s*$", re.MULTILINE)
_RE_SHORT_FAILED = re.compile(r"^FAILED\s+(.+)$", re.MULTILINE)
_RE_SHORT_SKIPPED = re.compile(
    r"^SKIPPED\s+(?:\[[^\]]*\]\s*)?(?P<node>.+?)(?:\s+(?P<reason>\S.*)?)?$", re.MULTILINE
)
_RE_SHORT_ERROR = re.compile(r"^ERROR\s+(.+)$", re.MULTILINE)
_RE_SUMMARY_LINE = re.compile(
    r"(?P<count>\d+)\s+(?P<verb>passed|failed|skipped|errors?|xpassed|xfailed|warnings?|error)"
)
_RE_MYPY_LINE = re.compile(
    r"^(?P<path>[^:\s]+):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?P<level>error|note|warning):\s*(?P<msg>.*)$"
)
_RE_RUFF_LINE = re.compile(
    r"^(?P<path>[^:\s]+):(?P<line>\d+):(?P<col>\d+):\s*(?P<code>[A-Z]\d+)\s+(?P<msg>.*)$"
)
_RE_RUFF_CODE = re.compile(r"\b([A-Z]\d{3,4})\b")
_RE_RULE_ID = re.compile(r"\b([a-z][a-z0-9-]*/[a-z0-9-]+)\b")


def _clean(text: str) -> str:
    return (text or "").replace("\r\n", "\n")


def parse_short_test_summary(text: str) -> dict[str, int]:
    """``2 failed, 13 passed, 1 skipped`` → counts by verb (any suite).

    Malformed input yields an empty dict, never an exception.
    """
    counts: dict[str, int] = {}
    for line in _clean(text).split("\n"):
        for m in _RE_SUMMARY_LINE.finditer(line):
            verb = m.group("verb").rstrip("s")
            counts[verb] = counts.get(verb, 0) + int(m.group("count"))
    return counts


def parse_pytest_output(
    text: str, repo_root: str | None = None, *, source: str = "pytest"
) -> tuple[list[Failure], list[SkippedTest], dict[str, int]]:
    """Parse pytest console output into failures + skipped tests + summary counts.

    Handles both the long form (``_____ test_name _____`` + ``_ test_file.py:12``
    + traceback) and the short ``FAILED tests/x.py::test_y`` summary line.
    A failure with no recoverable location still yields a Failure with
    ``location.path == unknown`` — the reporter never drops evidence.
    """
    text = _clean(text)
    if not text:
        return [], [], {}

    counts = parse_short_test_summary(text)
    failures: list[Failure] = []
    skipped: list[SkippedTest] = []

    # ---- long form: split the output into per-failure blocks --------------
    headers = list(_RE_FAILURE_HEADER.finditer(text))
    if headers:
        for idx, header in enumerate(headers):
            block = text[
                header.end() : headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
            ]
            failures.extend(_parse_pytest_block(header.group(1), block, repo_root, source))
    else:
        # ---- short form ---------------------------------------------------
        for m in _RE_SHORT_FAILED.finditer(text):
            failures.append(_failure_from_short_line(m.group(1), repo_root, source=source))

    for m in _RE_SHORT_SKIPPED.finditer(text):
        raw = m.group(0)
        # Split the node from the reason at the first run of whitespace that is
        # followed by a reason phrase (pytest: ``SKIPPED [1] file:91: reason``).
        body = raw[len("SKIPPED") :].strip()
        body = re.sub(r"^\[[^\]]*\]\s*", "", body)
        node, reason = _split_node_and_reason(body)
        nodeid = extract_test_nodeid(node)
        node_path = nodeid[0] if nodeid else node
        path = normalize_or_unknown(_strip_trailing_colon(node_path), repo_root)
        skipped.append(
            SkippedTest(
                test=(nodeid[1] if nodeid else UNKNOWN),
                location=SourceLocation(
                    path, _line_of(node), None, (nodeid[1] if nodeid else UNKNOWN)
                ),
                reason=reason or UNKNOWN,
                category=classify_skip_reason(reason),
                suite=source,
            )
        )

    return failures, skipped, counts


def _strip_trailing_colon(path: str) -> str:
    """``tests/x.py:91:`` → ``tests/x.py`` (pytest's line + trailing separator)."""
    out = path.strip()
    while out.endswith(":"):
        out = out[:-1].rstrip()
    # Drop a trailing ``:line[:col]`` suffix — it is a position, not a path part.
    parts = out.split(":")
    while len(parts) > 1 and parts[-1].isdigit():
        parts.pop()
    return ":".join(parts)


def _line_of(node: str) -> int | None:
    """Recover the line from a ``file.py:91`` node when the node is not a nodeid."""
    if "::" in node:
        return None
    parts = node.split(":")
    for part in parts[1:]:
        try:
            n = int(part)
        except ValueError:
            continue
        if n > 0:
            return n
    return None


def _split_node_and_reason(body: str) -> tuple[str, str]:
    """``tests/x.py:91: reason text`` → (node, reason).

    The node is the ``file:line[:col]`` token; the reason is the remaining
    phrase. A bare ``file.py::test`` node with no reason is returned as-is.
    """
    parts = body.split(None, 1)
    if not parts:
        return body, ""
    node = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    if "::" in node:
        # ``file.py::test`` — the test name is part of the node, no reason split.
        return node, rest
    return node, rest


def normalize_or_unknown(path: str, repo_root: str | None = None) -> str:
    from nexus_scalp.pr_evidence.locations import normalize_repo_relative

    return normalize_repo_relative(path, repo_root)


def classify_skip_reason(reason: str) -> FailureCategory:
    """A skipped test is an ENVIRONMENT_FAILURE unless the reason says otherwise."""
    if not reason:
        return FailureCategory.ENVIRONMENT_FAILURE
    lowered = reason.lower()
    if "migrat" in lowered:
        return FailureCategory.MIGRATION_FAILURE
    if "not configured" in lowered or "missing" in lowered or "unavailable" in lowered:
        return FailureCategory.ENVIRONMENT_FAILURE
    if "database" in lowered or "postgres" in lowered:
        return FailureCategory.DATABASE_FAILURE
    return FailureCategory.ENVIRONMENT_FAILURE


def _parse_pytest_block(
    header: str, block: str, repo_root: str | None, source: str
) -> list[Failure]:
    nodeid = _nodeid_from(header) or _nodeid_from(block)
    if nodeid is None:
        # pytest prints ``test_file.py:184: in test_name`` under the header.
        line = _RE_NODEID_LINE.search(block) or _RE_NODEID_LINE.search(header)
        if line is not None:
            nodeid = (line.group("file"), line.group("test"))
    test_loc, prod_loc, err_type = parse_pytest_traceback(block, repo_root)
    message = _extract_message(block) or UNKNOWN
    loc = test_loc or (SourceLocation(nodeid[0], None, None, nodeid[1]) if nodeid else None)
    if loc is None:
        # Fall back to any location mentioned anywhere in the block.
        all_locs = extract_locations(block, repo_root)
        loc = all_locs[0] if all_locs else None
    return [
        Failure(
            source=source,
            suite=source,
            test=(nodeid[1] if nodeid else UNKNOWN),
            location=loc or SourceLocation.unknown(),
            production_location=prod_loc,
            error_type=err_type,
            message=message,
            traceback=_excerpt(block),
            category=classify_failure(err_type, message, block, loc, source),
        )
    ]


def _nodeid_from(text: str) -> tuple[str, str] | None:
    """Header form ``_____ test_name _____`` or an inline ``file::test`` node id."""
    m = _RE_NODEID.search(text)
    if m is not None:
        node = extract_test_nodeid(m.group("nodeid"))
        if node is not None:
            return node
    header = text.strip().strip("_").strip()
    if header and "::" not in header and not _looks_like_path_line(header):
        # The pytest failure banner is the bare test function name.
        token = header.split()[0] if header.split() else ""
        if _is_identifier(token):
            return UNKNOWN_FILE, token
    return None


def _looks_like_path_line(text: str) -> bool:
    return bool(_RE_NODEID_LINE.search(text))


def _is_identifier(token: str) -> str:
    return token if token.isidentifier() else ""


UNKNOWN_FILE = "unknown"


def _failure_from_short_line(line: str, repo_root: str | None, *, source: str) -> Failure:
    """``FAILED tests/db/test_x.py::test_y - AssertionError: msg``."""
    text = line.strip()
    nodeid = extract_test_nodeid(text)
    err_type = extract_error_type(text)
    message = _extract_message(text)
    loc = None
    if nodeid:
        loc = SourceLocation(normalize_or_unknown(nodeid[0], repo_root), None, None, nodeid[1])
    if loc is None:
        found = extract_locations(text, repo_root)
        loc = found[0] if found else None
    return Failure(
        source=form_source(source),
        suite=source,
        test=(nodeid[1] if nodeid else UNKNOWN),
        location=loc or SourceLocation.unknown(),
        error_type=err_type,
        message=message,
        category=classify_failure(err_type, message, text, loc, source),
    )


def form_source(tool: str) -> str:
    """Normalize a tool name into an evidence ``source`` label."""
    return (tool or UNKNOWN).strip() or UNKNOWN


def _extract_message(text: str) -> str:
    """First concise error line: ``E  AssertionError: msg`` or ``AssertionError: msg``."""
    if not text:
        return UNKNOWN
    for line in text.strip().split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("E "):
            return stripped[2:].strip()[:400]
        if stripped.startswith("E  "):
            return stripped[3:].strip()[:400]
        m = re.match(r"^([A-Za-z_][\w.]*Error(?:Exception|Warning)?)\s*:\s*(.+)$", stripped)
        if m:
            return f"{m.group(1)}: {m.group(2)}"[:400]
    return UNKNOWN


def _excerpt(text: str, max_lines: int = 8, max_chars: int = 600) -> str:
    """Bounded traceback excerpt (spec §20: never dump whole logs)."""
    if not text:
        return UNKNOWN
    lines = [ln for ln in text.strip().split("\n")]
    # Prefer frames + the E line (the actionable part of a traceback).
    kept = [
        ln
        for ln in lines
        if ln.strip().startswith(("File ", "E ", "E  ")) or "Error" in ln or "Exception" in ln
    ]
    if not kept:
        kept = lines
    out = "\n".join(kept[-max_lines:])
    if len(out) > max_chars:
        out = f"... truncated ...\n{out[-max_chars:]}"
    return out.strip() or UNKNOWN


def classify_failure(
    error_type: str,
    message: str,
    text: str,
    location: SourceLocation | None,
    source: str,
) -> FailureCategory:
    """Map evidence to a factual category (spec §5). Never invents a category.

    Classification is driven by the tool that produced the evidence and the
    error type it reports — not by any knowledge of a specific PR or file.
    """
    blob = f"{error_type}\n{message}".lower()
    et = error_type.lower()
    tool = (source or "").lower()

    if tool.startswith("ruff"):
        return FailureCategory.FORMAT_FAILURE if "format" in tool else FailureCategory.LINT_FAILURE
    if tool.startswith("mypy"):
        return FailureCategory.MYPY_FAILURE
    if tool.startswith("codeql"):
        return FailureCategory.CODEQL_FINDING
    if "migrat" in blob:
        return FailureCategory.MIGRATION_FAILURE
    if error_type == UNKNOWN:
        return FailureCategory.UNKNOWN
    if "typeerror" in et:
        return FailureCategory.TYPE_ERROR
    if "import" in et or "modulenotfound" in et:
        return FailureCategory.IMPORT_FAILURE
    if "assert" in et:
        return FailureCategory.TEST_FAILURE
    if "connect" in et or "operational" in et or "pool" in et:
        return FailureCategory.DATABASE_FAILURE
    if "database" in et:
        return FailureCategory.DATABASE_FAILURE
    if "config" in et:
        return FailureCategory.CONFIGURATION_FAILURE
    if "syntax" in et:
        return FailureCategory.BUILD_FAILURE
    return FailureCategory.TEST_FAILURE


def parse_ruff_output(
    text: str, repo_root: str | None = None, *, kind: str = "lint"
) -> list[Failure]:
    """Parse ruff check / ruff format --check output (any file, any rule)."""
    text = _clean(text)
    if not text:
        return []
    failures: list[Failure] = []
    seen: set[tuple[str, int, str]] = set()
    for line in text.split("\n"):
        m = _RE_RUFF_LINE.match(line.strip())
        if not m:
            continue
        path = normalize_or_unknown(m.group("path"), repo_root)
        if path == UNKNOWN:
            continue
        code = m.group("code")
        key = (path, int(m.group("line")), code)
        if key in seen:
            continue
        seen.add(key)
        failures.append(
            Failure(
                source="ruff",
                suite=f"ruff-{kind}",
                test=UNKNOWN,
                location=SourceLocation(path, int(m.group("line")), int(m.group("col"))),
                error_type=code or UNKNOWN,
                message=(m.group("msg") or "").strip()[:300],
                category=FailureCategory.FORMAT_FAILURE
                if kind == "format"
                else FailureCategory.LINT_FAILURE,
            )
        )
    return failures


def parse_mypy_output(text: str, repo_root: str | None = None) -> list[Failure]:
    """Parse ``mypy src`` output (any module, any error)."""
    text = _clean(text)
    if not text:
        return []
    failures: list[Failure] = []
    seen: set[tuple[str, int, str]] = set()
    for line in text.split("\n"):
        m = _RE_MYPY_LINE.match(line.strip())
        if not m or m.group("level") != "error":
            continue
        path = normalize_or_unknown(m.group("path"), repo_root)
        if path == UNKNOWN:
            continue
        key = (path, int(m.group("line")), m.group("msg")[:80])
        if key in seen:
            continue
        seen.add(key)
        col = m.group("col")
        failures.append(
            Failure(
                source="mypy",
                suite="mypy",
                test=UNKNOWN,
                location=SourceLocation(path, int(m.group("line")), int(col) if col else None),
                error_type="mypy",
                message=m.group("msg")[:300],
                category=FailureCategory.MYPY_FAILURE,
            )
        )
    return failures
