"""Generic file/line/column/function extraction from arbitrary failure text.

Spec §4: whenever a failure contains ``/path/to/file.py:123``,
``File ".../file.py", line 123``, ``tests/foo_test.py::test_bar`` or a GitHub
annotation like ``src/foo.py:42``, this module extracts the repository-relative
path, line and column.

Spec §21: absolute paths are normalized to repo-relative so the local username
and machine path NEVER reach the PR comment.

BOUNDARY: pure regex/AST-free text extraction over evidence strings. No network,
no repo mutation. Deterministic and side-effect free.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from nexus_scalp.pr_evidence.models import UNKNOWN, SourceLocation

__all__ = [
    "PYTEST_NODEID_RE",
    "extract_location",
    "extract_locations",
    "extract_test_nodeid",
    "normalize_repo_relative",
    "parse_pytest_traceback",
]

#: ``tests/foo_test.py::test_bar`` — pytest node id (spec §4).
#: The path body excludes ``/`` from the repeated segment class: including the
#: separator inside a ``*``-quantified group makes the match ambiguous and lets
#: a long ``-/-/-/`` run backtrack exponentially (CodeQL py/redos).
PYTEST_NODEID_RE = re.compile(
    r"(?<![\w./])((?:[\w.@-]+/)*[\w.@-]+\.(?:py|js|ts|jsx|tsx))::([^\s:]+)"
)

#: ``File "/abs/path/file.py", line 123, in test_func`` (Python traceback).
TRACEBACK_FRAME_RE = re.compile(r'File\s+"([^"]+)",\s+line\s+(\d+)(?:,\s+in\s+([^\s(]+))?')

#: pytest's repo-relative frame form: ``tests/db/x.py:184: in test_func``.
REL_FRAME_RE = re.compile(
    r"(?<![\w/.])((?:[\w@.-]+/)*[\w@.-]+\.(?:py|js|ts|jsx|tsx)):(\d+): in ([^\s(]+)"
)

#: ``File "/abs/path/file.py", line 123, in MyClass.method`` -> class capture.
TRACEBACK_FRAME_CLASS_RE = re.compile(
    r'File\s+"([^"]+)",\s+line\s+(\d+),\s+in\s+([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)'
)

#: ``/path/to/file.py:123`` or ``/path/to/file.py:123:45`` — compiler/lint style.
#: ``/`` is kept out of the repeated segment class for the same ReDoS reason.
PATH_LINE_RE = re.compile(
    r"(?<![\w])"
    r"((?:[A-Za-z]:[\\/]|/|[\w@.-]+/)+[\w@.+-]+\.(?:py|js|ts|jsx|tsx|go|rs|sql|ya?ml|json|toml|cfg|ini"
    r"|sh|ps1|md|tsx?)):(\d+)(?::(\d+))?"
)

#: ``at foo (file.js:123:45)`` — JS/stack style.
JS_FRAME_RE = re.compile(r"at\s+([^\s(]+)\s*\(([^:)]+):(\d+):(\d+)\)")

#: Error-type prefix in a one-line message: ``AssertionError: ...``.
ERROR_TYPE_PREFIX_RE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Warning))\s*:")

#: Function-name token after ``in `` / ``def `` / ``::``.
FUNCTION_TOKEN_RE = re.compile(r"\b(?:in|def)\s+([A-Za-z_][\w.]*)\s*\(?")

#: Known repo roots that are NOT source roots — prefixes stripped during
#: normalization. All discovery is path-structural, never a hardcoded file list.
_NON_SOURCE_ROOTS = (".git", ".github/.", "node_modules", "__pycache__", ".venv", "site")

#: Extensions that mark a file as a TEST file (used only to classify the
#: detected vs suspected-production location, never to filter evidence).
_TEST_PATH_MARKERS = ("/test", "tests/", "test_", "_test.py", "/tests/", "spec/")


def _is_real_source_path(path: str) -> bool:
    """Reject git-internal / dependency paths that would pollute evidence."""
    if not path or len(path) > 512:
        return False
    normalized = path.replace("\\", "/")
    return not any(
        normalized.startswith(root) or f"/{root}/" in normalized for root in _NON_SOURCE_ROOTS
    )


def normalize_repo_relative(path: str, repo_root: str | None = None) -> str:
    """Convert any path shape into a repository-relative POSIX path (spec §4/§21).

    Handles absolute POSIX, absolute Windows (``C:\\Users\\...\\src\\foo.py``),
    UNC, and already-relative paths. If ``repo_root`` is given, that prefix is
    stripped; otherwise the longest stable source root (``src/``, ``tests/``,
    ``scripts/``, ``frontend/src``, ``go-api/`` ...) is used as the anchor so a
    bare absolute path still becomes repo-relative WITHOUT leaking the local
    username or machine path.

    Returns ``UNKNOWN`` when nothing usable remains.
    """
    if not path or not isinstance(path, str):
        return UNKNOWN
    raw = path.strip().strip('"').strip("'")
    if not raw or raw == UNKNOWN:
        return UNKNOWN

    # Split off a leading scheme (postgresql://, file://) — never a source path.
    if "://" in raw:
        return UNKNOWN

    # Normalize separators to POSIX for uniform processing.
    as_posix = raw.replace("\\", "/")

    # Detect a Windows drive prefix (C:/...) and treat the rest POSIX.
    has_drive = bool(re.match(r"^[A-Za-z]:/", as_posix))

    # Repo-root anchor (POSIX, no trailing slash).
    root_posix = repo_root.replace("\\", "/").rstrip("/") if repo_root else None

    # Case 1: absolute path anchored at the given repo root.
    if root_posix and as_posix.startswith(root_posix + "/"):
        rel = as_posix[len(root_posix) + 1 :]
        return _finalize(rel)

    # Case 2: absolute path with a Windows drive — find the last source anchor.
    if has_drive or as_posix.startswith("/"):
        candidate = _strip_to_source_anchor(as_posix, root_posix)
        if candidate:
            return candidate

    # Case 3: already relative — normalize and guard.
    if not as_posix.startswith("/") and not has_drive:
        return _finalize(as_posix)

    return UNKNOWN


def _strip_to_source_anchor(posix_path: str, root_posix: str | None) -> str:
    """Reduce an absolute path to its first stable source-root-relative tail.

    Scans for a recognized source anchor (``src``, ``tests``, ``scripts``,
    ``frontend/src``, ``go-api``, ``docs``, ``Web``, ``.github``) and returns
    everything from it. This is what makes ``C:\\Users\\<user>\\...\\src\\foo.py``
    become ``src/foo.py`` without ever emitting the local username.
    """
    parts = [p for p in posix_path.split("/") if p not in ("", ".")]
    if not parts:
        return UNKNOWN
    if root_posix:
        root_parts = [p for p in root_posix.split("/") if p]
        for i in range(len(parts) - len(root_parts) + 1):
            if parts[i : i + len(root_parts)] == root_parts:
                return _finalize("/".join(parts[i + len(root_parts) :]))
    anchors = ("src", "tests", "test", "scripts", "docs", "frontend", "go-api", "Web", ".github")
    for i, part in enumerate(parts):
        if part in anchors:
            tail = parts[i:]
            # ``frontend/node_modules`` is not evidence.
            if "node_modules" in tail:
                continue
            return "/".join(tail)
    return UNKNOWN


def _finalize(rel: str) -> str:
    rel = rel.strip().strip("/")
    if not rel:
        return UNKNOWN
    if not _is_real_source_path(rel):
        return UNKNOWN
    if PureWindowsPath(rel).is_absolute() or PurePosixPath(rel).is_absolute():
        # Re-anchor a leftover absolute tail.
        return _strip_to_source_anchor(rel, None)
    return rel.replace("\\", "/")


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        n = int(value)
    except ValueError:
        return None
    return n if n > 0 else None


def extract_test_nodeid(text: str) -> tuple[str, str] | None:
    """Extract ``(file, test_name)`` from a pytest node id (spec §4).

    ``tests/db/test_pg_pool_config.py::test_pg_pool_config_reaches_pool``
    → ``("tests/db/test_pg_pool_config.py", "test_pg_pool_config_reaches_pool")``.
    Parametrized ids keep their bracket: ``test_x[a:b]``.
    """
    if not text:
        return None
    m = PYTEST_NODEID_RE.search(text)
    if not m:
        return None
    file, test = m.group(1), m.group(2).strip()
    return file, test


def extract_location(
    text: str, repo_root: str | None = None, prefer_test: bool = False
) -> SourceLocation | None:
    """Extract the best single :class:`SourceLocation` from evidence text.

    Prefers the most precise form available, in order: pytest node id (carries
    the test FUNCTION name), traceback frame (carries ``in <func>``), JS frame
    (carries function + column), then bare ``path:line[:col]``.

    ``prefer_test=True`` biases selection toward test-file locations (use it to
    resolve the DETECTED location of a test failure, not the production one).
    """
    if not text:
        return None

    candidates: list[tuple[int, SourceLocation]] = []

    nodeid = extract_test_nodeid(text)
    if nodeid is not None:
        file, test = nodeid
        path = normalize_repo_relative(file, repo_root)
        if path != UNKNOWN:
            # A node id carries no line; ``::test_x[1]`` has no line evidence.
            candidates.append((10, SourceLocation(path, None, None, test)))

    for m in TRACEBACK_FRAME_RE.finditer(text):
        path = normalize_repo_relative(m.group(1), repo_root)
        if path == UNKNOWN:
            continue
        func = m.group(3) or UNKNOWN
        candidates.append((8, SourceLocation(path, _to_int(m.group(2)), None, func)))

    for m in JS_FRAME_RE.finditer(text):
        path = normalize_repo_relative(m.group(2), repo_root)
        if path == UNKNOWN:
            continue
        candidates.append(
            (7, SourceLocation(path, _to_int(m.group(3)), _to_int(m.group(4)), m.group(1)))
        )

    for m in PATH_LINE_RE.finditer(text):
        path = normalize_repo_relative(m.group(1), repo_root)
        if path == UNKNOWN:
            continue
        candidates.append((5, SourceLocation(path, _to_int(m.group(2)), _to_int(m.group(3)))))

    if not candidates:
        return None

    if prefer_test:
        test_hits = [c for c in candidates if _looks_like_test_path(c[1].path)]
        pool = test_hits or candidates
    else:
        non_test = [c for c in candidates if not _looks_like_test_path(c[1].path)]
        pool = non_test or candidates

    # Highest precision score wins; ties → first occurrence (stable order).
    return max(pool, key=lambda c: c[0])[1]


def _looks_like_test_path(path: str) -> bool:
    if not path or path == UNKNOWN:
        return False
    p = path.replace("\\", "/")
    return any(marker in p for marker in _TEST_PATH_MARKERS)


def extract_locations(text: str, repo_root: str | None = None) -> list[SourceLocation]:
    """All distinct locations mentioned in evidence text, most-precise first."""
    found: list[SourceLocation] = []
    seen: set[tuple[str, int | None, int | None, str]] = set()
    for regex in (
        PYTEST_NODEID_RE,
        TRACEBACK_FRAME_RE,
        JS_FRAME_RE,
        PATH_LINE_RE,
    ):
        for m in regex.finditer(text or ""):
            if regex is PYTEST_NODEID_RE:
                path = normalize_repo_relative(m.group(1), repo_root)
                loc = SourceLocation(path, None, None, m.group(2))
            elif regex is TRACEBACK_FRAME_RE:
                path = normalize_repo_relative(m.group(1), repo_root)
                loc = SourceLocation(path, _to_int(m.group(2)), None, m.group(3) or UNKNOWN)
            elif regex is JS_FRAME_RE:
                path = normalize_repo_relative(m.group(2), repo_root)
                loc = SourceLocation(path, _to_int(m.group(3)), _to_int(m.group(4)), m.group(1))
            else:
                path = normalize_repo_relative(m.group(1), repo_root)
                loc = SourceLocation(path, _to_int(m.group(2)), _to_int(m.group(3)))
            if loc.path == UNKNOWN:
                continue
            key = (loc.path, loc.line, loc.column, loc.function)
            if key in seen:
                continue
            seen.add(key)
            found.append(loc)
    return found


def extract_function(text: str) -> str:
    """Best-effort function/class name from a traceback or message (spec §6)."""
    if not text:
        return UNKNOWN
    m = TRACEBACK_FRAME_RE.search(text)
    if m and m.group(3):
        return m.group(3)
    m = JS_FRAME_RE.search(text)
    if m:
        return m.group(1)
    m = FUNCTION_TOKEN_RE.search(text)
    return m.group(1) if m else UNKNOWN


def extract_error_type(text: str) -> str:
    """``AssertionError: msg`` → ``AssertionError``. ``unknown`` if not found."""
    if not text:
        return UNKNOWN
    m = ERROR_TYPE_PREFIX_RE.match(text.strip())
    if m:
        return m.group(1)
    # ``E  AssertionError`` (pytest) or ``raise ValueError``.
    m = re.search(r"\b((?:[A-Za-z_][\w]*\.)*[A-Za-z_][\w]*(?:Error|Exception))\b", text)
    return m.group(1) if m else UNKNOWN


def parse_pytest_traceback(
    text: str, repo_root: str | None = None
) -> tuple[SourceLocation | None, SourceLocation | None, str]:
    """Split a pytest failure block into (test_location, production_location, type).

    The DETECTED location is the assertion frame in the test file; the suspected
    PRODUCTION location is the deepest non-test frame in the traceback — the
    file/function where the code under test actually raised (spec §6/§7).
    Returns ``(None, None, "unknown")`` for malformed output rather than guessing.
    """
    if not text:
        return None, None, UNKNOWN

    frames = list(TRACEBACK_FRAME_RE.finditer(text)) + list(REL_FRAME_RE.finditer(text))
    if not frames:
        return None, None, extract_error_type(text)

    test_loc: SourceLocation | None = None
    prod_loc: SourceLocation | None = None
    for m in frames:
        raw_path = m.group(1)
        path = normalize_repo_relative(raw_path, repo_root)
        if path == UNKNOWN:
            # pytest prints repo-RELATIVE frame paths (no absolute prefix); a
            # frame that is not a source path still anchors the test location.
            path = _relative_frame_path(raw_path)
        if path == UNKNOWN:
            continue
        loc = SourceLocation(path, _to_int(m.group(2)), None, m.group(3) or UNKNOWN)
        if _looks_like_test_path(path):
            if test_loc is None:
                test_loc = loc
        elif prod_loc is None:
            prod_loc = loc

    # A traceback frame in a test file may still point at library internals
    # (assert helpers); only adopt it as production evidence when it is a
    # repo source path (src/, scripts/), never site-packages or stdlib.
    if prod_loc is not None and not _is_repo_source(prod_loc.path):
        prod_loc = None

    return test_loc, prod_loc, extract_error_type(text)


def _relative_frame_path(raw_path: str) -> str:
    """Accept pytest's repo-relative frame paths (``tests/db/x.py``).

    These carry no absolute prefix, so :func:`normalize_repo_relative` cannot
    anchor them; validate structurally instead and never emit a machine path.
    """
    candidate = raw_path.strip().replace("\\", "/")
    if not candidate or "://" in candidate:
        return UNKNOWN
    if not _is_real_source_path(candidate):
        return UNKNOWN
    if PureWindowsPath(candidate).is_absolute() or PurePosixPath(candidate).is_absolute():
        return normalize_repo_relative(candidate)
    return candidate


def _is_repo_source(path: str) -> bool:
    """True for paths under the repo's own source roots (not vendored/stdlib)."""
    if not path or path == UNKNOWN:
        return False
    p = path.replace("\\", "/")
    return p.startswith(("src/", "scripts/", "tests/", "frontend/src/", "go-api/", "Web/", "docs/"))


def iter_lines(text: Iterable[str] | str) -> list[str]:
    """Normalize multi-line evidence into a line list (CRLF tolerant)."""
    if isinstance(text, str):
        return text.replace("\r\n", "\n").split("\n")
    return [str(line).replace("\r\n", "\n") for line in text]


def coerce_text(value: Any) -> str:
    """Best-effort text coercion for API payloads that may be None."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # pragma: no cover - defensive coercion
        return ""
