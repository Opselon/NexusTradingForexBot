"""CI results-artifact collector — deep failure extraction from CI artifacts.

The single canonical CI artifact (``ci-results-quality-<workflow>-<run>-<sha>``)
holds the REAL per-check diagnostics that GitHub's check-run surface throws
away:

  ruff/lint.json          11 violations with absolute CI paths + row/column
  ruff/lint.txt           full human-readable ruff output with source context
  format/format.txt       "Would reformat: <file>" per offending file
  ruff-repair-report.json files_offending / files_changed (format violations)
  mypy/mypy.txt           "path:line:col: error: msg [code]"
  pytest/junit.xml        testcase file/line + full traceback per failure
  smoke/junit.xml         same shape for the smoke chain
  coverage/coverage.xml   per-file line coverage
  coverage/critical-gate.txt  critical-file coverage floor breaches
  layered-smoke/smoke.json   per-layer pass/fail + error
  runtime_gate.json / .log  runtime certification failures
  runtime_deps.json / .log   dependency closure failures

GitHub exposes NONE of this through the check-run API: the check-run for
``Code Quality & Tests`` carries only synthetic ``.github`` annotations
("Process completed with exit code 1."), so the evidence report rendered
``Where: .github/workflows/ci.yml`` and ``Why: Process completed with exit
code 1.`` — the file, line, rule and source context that CI actually
computed never reached the PR comment.

This module downloads that artifact via the GitHub API, parses each
diagnostic file with the repo's existing parsers, and returns normalized
:class:`Failure` objects pointing at real repository files with real lines.

BOUNDARY: read-only GitHub API + pure parsing. It never mutates a repo and
never publishes anything; normalization lives in ``parsers.py`` /
``locations.py`` and rendering in ``renderer.py``.

Security: artifact zips are downloaded into a temporary directory that is
removed on exit; contents are treated as untrusted text and every path is
normalized to a repository-relative POSIX path before it ever reaches a
report (spec §21 — no CI machine paths in published comments).
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.pr_evidence.locations import (
    extract_error_type,
    normalize_repo_relative,
)
from nexus_scalp.pr_evidence.models import (
    UNKNOWN,
    Failure,
    FailureCategory,
    SourceLocation,
)
from nexus_scalp.pr_evidence.parsers import (
    classify_failure,
    parse_pytest_output,
    parse_ruff_output,
)

__all__ = [
    "ArtifactCollector",
    "ArtifactDiagnostics",
    "collect_artifact_diagnostics",
]

#: GitHub's artifact name prefix for the quality job's canonical results zip.
_QUALITY_PREFIX = "ci-results-quality-"

#: Maximum total bytes of artifact text we parse per file (defense-in-depth
#: against a hostile/giant artifact; the real content is ~10 KB).
_MAX_FILE_BYTES = 2_000_000

#: Cap on the number of failures one check may contribute, so a single
#: runaway lint run cannot flood the PR comment past its budget.
_MAX_FAILURES_PER_CHECK = 40

#: Absolute CI checkout path that must be stripped to repo-relative.
#: ``/home/runner/work/<repo>/<repo>/src/...`` → ``src/...``
_RUNNER_PATH_RE = re.compile(
    r"^(?:/[A-Za-z0-9._-]+)+?/work/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/(?=[A-Za-z0-9.])"
)

#: Files inside the artifact zip that this collector knows how to parse,
#: keyed by the gate check they belong to. Order matters only within a
#: check (richer formats first).
_PARSERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ruff_lint", ("ruff/lint.json", "ruff/lint.txt")),
    ("ruff_format", ("format/format.txt", "ruff-repair-report.json")),
    ("mypy", ("mypy/mypy.txt", "mypy/mypy-junit.xml")),
    ("pytest", ("pytest/junit.xml", "pytest/pytest.txt")),
    ("smoke", ("smoke/junit.xml", "smoke/smoke.txt")),
    ("critical_coverage", ("coverage/critical-gate.txt",)),
    ("coverage", ("coverage/coverage.xml", "run-info/coverage.json")),
    ("runtime_deps", ("runtime_deps.json", "runtime_deps.log")),
    ("runtime_gate", ("runtime_gate.json", "runtime_gate.log")),
    ("layered_smoke", ("layered-smoke/smoke.json", "layered-smoke/smoke.log")),
)


@dataclass
class ArtifactDiagnostics:
    """Deep per-check diagnostics extracted from a CI results artifact.

    ``failures`` are normalized :class:`Failure` objects (real file, real
    line, real rule) — empty when the artifact carries no diagnostics for
    that check, which is itself evidence (the check passed or never ran).
    """

    check: str
    artifact_name: str = UNKNOWN
    failures: list[Failure] = field(default_factory=list)


@dataclass
class ArtifactCollector:
    """Download + parse the canonical CI results artifact for one run.

    Uses an existing :class:`~nexus_scalp.pr_evidence.github_client.GitHubClient`
    (transport-injectable, so every step is mockable without a network).
    """

    client: Any
    repo_root: str | None = None

    def collect(
        self, run_id: int, *, checks: tuple[str, ...] | None = None
    ) -> list[ArtifactDiagnostics]:
        """Fetch the run's results artifact and parse deep diagnostics.

        ``checks`` limits extraction (default: every gate the artifact
        covers). Returns one ``ArtifactDiagnostics`` per requested check,
        even when extraction found nothing — "found nothing" is a fact.
        """
        wanted = set(checks) if checks else {name for name, _ in _PARSERS}
        out: list[ArtifactDiagnostics] = []
        artifact = self._find_artifact(run_id)
        if artifact is None:
            return [ArtifactDiagnostics(check=c, artifact_name=UNKNOWN) for c in sorted(wanted)]
        name = str(artifact.get("name") or UNKNOWN)
        zip_bytes = self._download_artifact(int(artifact["id"]))
        if zip_bytes is None:
            return [ArtifactDiagnostics(check=c, artifact_name=name) for c in sorted(wanted)]
        files = self._read_zip(zip_bytes)
        for check, paths in _PARSERS:
            if check not in wanted:
                continue
            diag = ArtifactDiagnostics(check=check, artifact_name=name)
            for rel in paths:
                text = files.get(rel)
                if not text:
                    continue
                parsed = _parse_check_file(check, rel, text, self.repo_root)
                if parsed:
                    diag.failures.extend(parsed)
            # De-duplicate by (path, line, rule) — lint.json and lint.txt
            # describe the same 11 violations twice.
            diag.failures = _dedupe(diag.failures)
            if check == "ruff_format":
                # format.txt and the repair report name the same files.
                diag.failures = _dedupe_by_path(diag.failures)
            diag.failures = diag.failures[:_MAX_FAILURES_PER_CHECK]
            out.append(diag)
        return out

    # ------------------------------------------------------------------
    # GitHub API
    # ------------------------------------------------------------------
    def _find_artifact(self, run_id: int) -> dict[str, Any] | None:
        """The quality job's results artifact for one workflow run.

        The aggregate job uploads a base-named artifact on heavy runs; the
        quality job always uploads a ``-quality`` suffixed one. Prefer the
        quality artifact (it holds the per-check diagnostic files), and fall
        back to any single results artifact when the run has no quality one.
        """
        payload = self.client.get_artifacts_for_run(run_id)
        artifacts = payload if isinstance(payload, list) else []
        if not artifacts:
            return None
        quality = [a for a in artifacts if _is_quality_artifact(a)]
        pool = quality or artifacts
        # Newest first: several runs keep multiple retained artifacts.
        return max(
            pool,
            key=lambda a: int(a.get("id") or 0),
        )

    def _download_artifact(self, artifact_id: int) -> bytes | None:
        payload = self.client.get_artifact_zip(artifact_id)
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)
        return None

    # ------------------------------------------------------------------
    # Zip handling
    # ------------------------------------------------------------------
    @staticmethod
    def _read_zip(zip_bytes: bytes) -> dict[str, str]:
        """Extract text files from the results artifact.

        A corrupt or non-zip payload yields an empty dict: the collector
        degrades to "no diagnostics found", never to an exception.
        """
        out: dict[str, str] = {}
        try:
            import io

            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename.replace("\\", "/")
                    if (info.file_size or 0) > _MAX_FILE_BYTES:
                        continue
                    raw = zf.read(info)
                    out[name] = _decode(raw)
        except (zipfile.BadZipFile, OSError, ValueError, EOFError):
            return {}
        return out


def _is_quality_artifact(artifact: Any) -> bool:
    if not isinstance(artifact, dict):
        return False
    name = str(artifact.get("name") or "")
    return name.startswith(_QUALITY_PREFIX)


def _decode(raw: bytes) -> str:
    """Decode artifact text (UTF-8 with a replacement fallback)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _ci_path_to_repo(path: str, repo_root: str | None) -> str:
    """``/home/runner/work/Repo/Repo/src/x.py`` → ``src/x.py``.

    Falls back to the shared repo-relative normalizer, which strips local
    machine paths the same way.
    """
    text = (path or "").strip()
    if not text:
        return UNKNOWN
    stripped = _RUNNER_PATH_RE.sub("", text.replace("\\", "/"))
    return normalize_repo_relative(stripped, repo_root)


# ----------------------------------------------------------------------
# Per-check parsers
# ----------------------------------------------------------------------
def _parse_check_file(check: str, rel: str, text: str, repo_root: str | None) -> list[Failure]:
    """Dispatch one artifact file to its parser.

    Every parser returns bounded, normalized failures; a malformed file
    yields an empty list rather than raising (spec §22).
    """
    try:
        if check == "ruff_lint":
            return _parse_ruff_lint(rel, text, repo_root)
        if check == "ruff_format":
            return _parse_ruff_format(rel, text, repo_root)
        if check == "mypy":
            return _parse_mypy(rel, text, repo_root)
        if check in ("pytest", "smoke"):
            return _parse_junit(rel, text, repo_root, check)
        if check == "critical_coverage":
            return _parse_critical_gate(text, repo_root)
        if check == "coverage":
            return _parse_coverage(rel, text, repo_root)
        if check == "runtime_deps":
            return _parse_json_or_log(rel, text, repo_root, "RuntimeDependency")
        if check == "runtime_gate":
            return _parse_json_or_log(rel, text, repo_root, "RuntimeCertification")
        if check == "layered_smoke":
            return _parse_layered_smoke(rel, text, repo_root)
    except Exception:
        return []
    return []


def _failure(
    *,
    check: str,
    path: str,
    line: int | None,
    column: int | None,
    rule: str,
    message: str,
    category: FailureCategory,
    traceback: str = UNKNOWN,
    test: str = UNKNOWN,
    function: str = UNKNOWN,
) -> Failure:
    return Failure(
        source="ci-artifact",
        suite=check,
        test=test,
        location=SourceLocation(path, line, column, function),
        production_location=None,
        error_type=rule or UNKNOWN,
        message=(message or "")[:400],
        traceback=traceback,
        category=category,
        workflow=UNKNOWN,
        job=UNKNOWN,
        check=check,
        check_url=UNKNOWN,
        evidence_source="ci-artifact",
        step=check,
        workflow_file=UNKNOWN,
    )


# ---- ruff lint -------------------------------------------------------
def _parse_ruff_lint(rel: str, text: str, repo_root: str | None) -> list[Failure]:
    if rel.endswith(".json"):
        return _parse_ruff_json(text, repo_root)
    # The .txt form is already the ruff console format the shared parser
    # understands; reuse it so rule/message/location handling stays uniform.
    return parse_ruff_output(text, repo_root, kind="lint")


def _parse_ruff_json(text: str, repo_root: str | None) -> list[Failure]:
    data = json.loads(text)
    if not isinstance(data, list):
        return []
    out: list[Failure] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        loc = item.get("location") or {}
        path = _ci_path_to_repo(str(item.get("filename") or ""), repo_root)
        if path == UNKNOWN:
            continue
        out.append(
            _failure(
                check="ruff_lint",
                path=path,
                line=_as_int(loc.get("row")),
                column=_as_int(loc.get("column")),
                rule=str(item.get("code") or "LINT"),
                message=str(item.get("message") or "ruff violation"),
                category=FailureCategory.LINT_FAILURE,
            )
        )
    return out


# ---- ruff format -----------------------------------------------------
_RE_WOULD_REFORMAT = re.compile(r"^(?:unformatted:\s*)?(\d+) files? would be reformatted")
_RE_REFORMAT_FILE = re.compile(r"^\s*-->\s*([^\s:]+):(\d+):(\d+)\s*$")


def _parse_ruff_format(rel: str, text: str, repo_root: str | None) -> list[Failure]:
    out: list[Failure] = []
    if rel == "ruff-repair-report.json":
        data = json.loads(text)
        if isinstance(data, dict):
            files = data.get("files_offending") or data.get("files_changed") or []
            for raw in files:
                path = _ci_path_to_repo(str(raw), repo_root)
                if path == UNKNOWN:
                    continue
                if any(f.location.path == path for f in out):
                    continue
                out.append(
                    _failure(
                        check="ruff_format",
                        path=path,
                        line=None,
                        column=None,
                        rule="FormatViolation",
                        message="Committed source is not formatted (ruff format)",
                        category=FailureCategory.FORMAT_FAILURE,
                    )
                )
        return out
    # format.txt: "--> tests/unit/test_x.py:358:28" after a "Would reformat"
    # header line. The arrow names the file AND the line/column.
    lines = text.replace("\r\n", "\n").split("\n")
    for line in lines:
        m = _RE_REFORMAT_FILE.match(line)
        if not m:
            continue
        path = _ci_path_to_repo(m.group(1), repo_root)
        if path == UNKNOWN:
            continue
        out.append(
            _failure(
                check="ruff_format",
                path=path,
                line=_as_int(m.group(2)),
                column=_as_int(m.group(3)),
                rule="FormatViolation",
                message="File would be reformatted by `ruff format`",
                category=FailureCategory.FORMAT_FAILURE,
            )
        )
    return out


# ---- mypy ------------------------------------------------------------
_RE_MYPY_LINE = re.compile(
    r"^(?P<path>\S+?):(?P<line>\d+)(?::(?P<col>\d+))?:\s*error:\s*(?P<msg>.*)$"
)


def _parse_mypy(rel: str, text: str, repo_root: str | None) -> list[Failure]:
    if rel.endswith(".txt"):
        out: list[Failure] = []
        for line in text.replace("\r\n", "\n").split("\n"):
            m = _RE_MYPY_LINE.match(line.strip())
            if not m:
                continue
            path = _ci_path_to_repo(m.group("path"), repo_root)
            if path == UNKNOWN:
                continue
            msg = m.group("msg").strip()
            code_m = re.search(r"\[([a-z-]+)\]$", msg)
            out.append(
                _failure(
                    check="mypy",
                    path=path,
                    line=_as_int(m.group("line")),
                    column=_as_int(m.group("col")),
                    rule=f"mypy[{code_m.group(1)}]" if code_m else "TypeError",
                    message=msg,
                    category=FailureCategory.MYPY_FAILURE,
                )
            )
        return out
    return _parse_junit(rel, text, repo_root, "mypy")


# ---- junit (pytest / smoke / mypy-junit) -----------------------------
def _parse_junit(rel: str, text: str, repo_root: str | None, check: str) -> list[Failure]:
    if rel.endswith((".xml",)):
        return _parse_junit_xml(text, repo_root, check)
    # .txt fallback: reuse the shared pytest console parser.
    fails, _skipped, _counts = parse_pytest_output(text, repo_root, source=check)
    return [replace_failure(f, check=check, source="ci-artifact") for f in fails]


def _parse_junit_xml(text: str, repo_root: str | None, check: str) -> list[Failure]:
    root = ET.fromstring(text)
    out: list[Failure] = []
    for tc in root.iter("testcase"):
        for child in tc:
            if child.tag not in ("failure", "error"):
                continue
            tb = (child.text or "").strip()
            node = f"{tc.attrib.get('classname', '')}::{tc.attrib.get('name', '')}"
            file_attr = _ci_path_to_repo(str(tc.attrib.get("file") or ""), repo_root)
            line_attr = _as_int(tc.attrib.get("line"))
            # A junit ``file`` attribute may be missing; the traceback frames
            # still name the real file and line.
            if file_attr == UNKNOWN or line_attr is None:
                f_path, f_line = _innermost_frame(tb, repo_root)
                file_attr = file_attr if file_attr != UNKNOWN else f_path
                line_attr = line_attr if line_attr is not None else f_line
            err_type = str(child.attrib.get("type") or "") or extract_error_type(tb)
            message = str(child.attrib.get("message") or "") or _last_error_line(tb)
            out.append(
                _failure(
                    check=check,
                    path=file_attr or UNKNOWN,
                    line=line_attr,
                    column=None,
                    rule=err_type or "TestFailure",
                    message=message,
                    category=classify_failure(err_type, message, tb, None, check),
                    traceback=_excerpt(tb),
                    test=node,
                )
            )
    return out


def _innermost_frame(tb: str, repo_root: str | None) -> tuple[str, int | None]:
    """Deepest repository frame in a Python traceback (file, line)."""
    matches = list(re.finditer(r'File "([^"]+)", line (\d+)', tb or ""))
    for m in reversed(matches):
        path = _ci_path_to_repo(m.group(1), repo_root)
        if path != UNKNOWN:
            return path, _as_int(m.group(2))
    if matches:
        last = matches[-1]
        return _ci_path_to_repo(last.group(1), repo_root), _as_int(last.group(2))
    return UNKNOWN, None


def _last_error_line(text: str) -> str:
    if not text:
        return ""
    for line in reversed(text.replace("\r\n", "\n").split("\n")):
        stripped = line.strip()
        if stripped:
            return stripped[:400]
    return ""


def _excerpt(text: str, max_lines: int = 12, max_chars: int = 900) -> str:
    """Bounded traceback excerpt (spec §20: never dump whole logs).

    Keeps the traceback banner (``Traceback (most recent call last):``) when
    present: it is the marker that tells a reader the excerpt IS a bug trace,
    and `_innermost_frame`-style readers rely on it.
    """
    if not text:
        return UNKNOWN
    lines = [ln for ln in text.strip().split("\n") if ln.strip()]
    kept = [
        ln
        for ln in lines
        if ln.strip().startswith(("Traceback", "File ", "E ", "E  "))
        or "Error" in ln
        or "Exception" in ln
        or "assert" in ln.lower()
    ]
    if not kept:
        kept = lines
    out = "\n".join(kept[-max_lines:])
    if len(out) > max_chars:
        out = f"... truncated ...\n{out[-max_chars:]}"
    return out.strip() or UNKNOWN


# ---- coverage --------------------------------------------------------
_RE_COVERAGE_FILE = re.compile(
    r'<class\s+filename="([^"]+)"[^>]*>\s*<lines[^>]*>(.*?)</lines>', re.DOTALL
)
_RE_COVERAGE_MISS = re.compile(r'<line\s+[^>]*miss="true"[^>]*>')


def _parse_coverage(rel: str, text: str, repo_root: str | None) -> list[Failure]:
    if rel == "run-info/coverage.json":
        data = json.loads(text)
        if isinstance(data, dict) and data.get("status") in ("failed", "errored"):
            return [
                _failure(
                    check="coverage",
                    path=UNKNOWN,
                    line=None,
                    column=None,
                    rule="CoverageUnderThreshold",
                    message=str(data.get("detail") or "Coverage below threshold"),
                    category=FailureCategory.CI_FAILURE,
                )
            ]
        return []
    out: list[Failure] = []
    try:
        for m in _RE_COVERAGE_FILE.finditer(text):
            path = _ci_path_to_repo(m.group(1), repo_root)
            if path == UNKNOWN:
                continue
            missed = len(_RE_COVERAGE_MISS.findall(m.group(2)))
            if missed:
                out.append(
                    _failure(
                        check="coverage",
                        path=path,
                        line=None,
                        column=None,
                        rule="CoverageGap",
                        message=f"{missed} uncovered line(s) in this file",
                        category=FailureCategory.CI_FAILURE,
                    )
                )
    except (re.error, TypeError):
        return []
    return out


def _parse_critical_gate(text: str, repo_root: str | None) -> list[Failure]:
    out: list[Failure] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not any(k in stripped for k in ("FAIL", "BELOW FLOOR", "NO_DATA", "BREACH")):
            continue
        m = re.search(r"([A-Za-z0-9_./\\-]+\.py)", stripped)
        path = _ci_path_to_repo(m.group(1), repo_root) if m else UNKNOWN
        out.append(
            _failure(
                check="critical_coverage",
                path=path,
                line=None,
                column=None,
                rule="CriticalCoverageFloor",
                message=stripped[:300],
                category=FailureCategory.CI_FAILURE,
            )
        )
    return out


def _parse_json_or_log(
    rel: str, text: str, repo_root: str | None, rule_prefix: str
) -> list[Failure]:
    if rel.endswith(".json"):
        data = json.loads(text)
        out: list[Failure] = []
        if isinstance(data, dict):
            for key in ("missing", "errors", "failures"):
                for item in data.get(key) or []:
                    if isinstance(item, dict):
                        msg = str(item.get("message") or item.get("error") or item)
                        path = _ci_path_to_repo(
                            str(item.get("file") or item.get("path") or ""), repo_root
                        )
                    else:
                        msg = str(item)
                        path = UNKNOWN
                    out.append(
                        _failure(
                            check="runtime_deps"
                            if rule_prefix.startswith("RuntimeDep")
                            else "runtime_gate",
                            path=path,
                            line=None,
                            column=None,
                            rule=f"{rule_prefix}Error",
                            message=msg[:300],
                            category=FailureCategory.CI_FAILURE,
                        )
                    )
        return out
    # .log fallback: a Python traceback from the gate probe.
    if "Traceback" not in text:
        return []
    path, line = _innermost_frame(text, repo_root)
    return [
        _failure(
            check="runtime_deps" if rule_prefix.startswith("RuntimeDep") else "runtime_gate",
            path=path,
            line=line,
            column=None,
            rule=f"{rule_prefix}Crash",
            message=_last_error_line(text),
            category=FailureCategory.CI_FAILURE,
            traceback=_excerpt(text),
        )
    ]


def _parse_layered_smoke(rel: str, text: str, repo_root: str | None) -> list[Failure]:
    if rel.endswith(".json"):
        data = json.loads(text)
        out: list[Failure] = []
        layers = data.get("layers") if isinstance(data, dict) else None
        for layer in layers or []:
            if not isinstance(layer, dict):
                continue
            if layer.get("passed", True) and layer.get("status") != "failed":
                continue
            out.append(
                _failure(
                    check="layered_smoke",
                    path=UNKNOWN,
                    line=None,
                    column=None,
                    rule=f"LayerSmoke[{layer.get('name', 'L?')}]",
                    message=str(layer.get("error") or layer.get("detail") or "Layer failed")[:300],
                    category=FailureCategory.CI_FAILURE,
                )
            )
        return out
    if "Traceback" not in text and "ERROR" not in text:
        return []
    path, line = _innermost_frame(text, repo_root)
    return [
        _failure(
            check="layered_smoke",
            path=path,
            line=line,
            column=None,
            rule="LayeredSmokeCrash",
            message=_last_error_line(text),
            category=FailureCategory.CI_FAILURE,
            traceback=_excerpt(text),
        )
    ]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _dedupe(failures: list[Failure]) -> list[Failure]:
    seen: set[tuple[str, int | None, str]] = set()
    out: list[Failure] = []
    for f in failures:
        key = (f.location.path, f.location.line, f.error_type)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def _dedupe_by_path(failures: list[Failure]) -> list[Failure]:
    """Collapse same-file rows regardless of line (format offenders).

    ``ruff format`` reports one offender per file, but two artifact files
    (format.txt + the repair report) describe the same file — with and
    without a line. Deduping on path keeps exactly one row per file.
    """
    seen: set[str] = set()
    out: list[Failure] = []
    for f in failures:
        path = f.location.path
        if path == UNKNOWN or path in seen:
            # Keep the FIRST row (the one carrying the line, when present).
            continue
        seen.add(path)
        out.append(f)
    return out


def replace_failure(f: Failure, *, check: str, source: str) -> Failure:
    from dataclasses import replace

    return replace(f, check=check, source=source, evidence_source=source, suite=check)


def collect_artifact_diagnostics(
    client: Any,
    run_id: int,
    *,
    repo_root: str | None = None,
    checks: tuple[str, ...] | None = None,
) -> list[ArtifactDiagnostics]:
    """One-call convenience wrapper around :class:`ArtifactCollector`."""
    return ArtifactCollector(client=client, repo_root=repo_root).collect(run_id, checks=checks)
