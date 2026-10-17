"""Every inline Python heredoc in release.yml must survive the SHELL.

A YAML block scalar that runs `python - <<PY` (UNQUOTED delimiter) lets the
shell expand the body before Python sees it. Any backslash escape inside the
body is then collapsed by the shell, so source that is valid Python on disk
becomes a SyntaxError at runtime:

    base = an.split("\\")[-1].split("/")[-1]   # intended
    base = an.split("\")[-1].split("/")[-1]    # what the shell delivers
    SyntaxError: unterminated string literal

That is exactly how v9.0.16's post-release verification died AFTER the release
had already published: the build, upload, and undraft all succeeded, then the
verifier crashed with a SyntaxError, so `Publish GitHub Release` reported
failure on a healthy release. Escaping bugs in a VERIFIER are the worst kind —
they fail loud about a release that is actually fine.

The gate below extracts each heredoc body from the real release.yml, de-indents
it the way the YAML block scalar does, and (a) parses it with ast, then
(b) delivers it through bash to confirm the shell agrees. A body containing no
shell-expandable character is safe either way, but a QUOTED delimiter is what
makes that a guarantee rather than a coincidence.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RELEASE_YML = REPO / ".github" / "workflows" / "release.yml"

#: `python - <<PY` / `python3 - <<EOF` etc. — capture the optional quotes.
_HEREDOC_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<launcher>python3?\s+-\s*<<\s*(?P<q>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?P=q))\s*$"
)


def _heredocs(text: str) -> list[tuple[int, int, str, str, str]]:
    """Yield (line_no, indent, tag, quoted, body) for each python heredoc."""
    lines = text.splitlines()
    out: list[tuple[int, int, str, str, str]] = []
    i = 0
    while i < len(lines):
        m = _HEREDOC_RE.match(lines[i])
        if not m:
            i += 1
            continue
        indent = m.group("indent")
        tag = m.group("tag")
        quoted = m.group("q")
        body_lines: list[str] = []
        j = i + 1
        while j < len(lines) and lines[j].strip() != tag:
            body_lines.append(lines[j])
            j += 1
        # The YAML block scalar strips the step's own indentation; the heredoc
        # body is de-indented by however much the launcher line is indented.
        dedented = "\n".join(
            ln[len(indent) :] if ln.startswith(indent) else ln for ln in body_lines
        )
        out.append((i + 1, len(indent), tag, quoted, dedented))
        i = j + 1
    return out


def _workflow_text() -> str:
    if not RELEASE_YML.exists():
        pytest.skip("release.yml not present in this checkout")
    return RELEASE_YML.read_bytes().decode("utf-8")


def test_release_yml_has_the_post_release_verifier():
    """Guard the guard: the verifier this suite protects must still exist."""
    text = _workflow_text()
    assert "Verify published release (post-release check)" in text, (
        "the post-release verification step vanished — if it was intentionally "
        "removed, delete these tests; if it was renamed, update RELEASE_YML "
        "expectations here so the heredoc gate keeps covering it"
    )
    assert "SHA256SUMS.txt" in text
    assert "UNCHECKED ASSET" in text, "the strict coverage rule (F3) went missing"


def test_every_python_heredoc_body_is_valid_python():
    """The body as Python sees it on disk must parse."""
    failures: list[str] = []
    for line_no, _indent, tag, _q, body in _heredocs(_workflow_text()):
        if not body.strip():
            continue
        try:
            ast.parse(body)
        except SyntaxError as exc:
            failures.append(f"release.yml:{line_no} (<<{tag}) does not parse: {exc}")
    assert not failures, "\n".join(failures)


def _real_bash() -> str | None:
    """Prefer a REAL bash. On Windows `shutil.which('bash')` can land on the
    WSL relay (or a wrapper), which cannot deliver a heredoc from this process;
    git-bash is the one that behaves like CI's bash."""
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        shutil.which("bash.exe"),
    ]
    for cand in candidates:
        if not cand:
            continue
        try:
            probe = subprocess.run(
                [cand, "-c", "echo probe-ok"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except OSError:
            continue
        if probe.returncode == 0 and "probe-ok" in (probe.stdout or ""):
            return cand
    return None


def test_every_python_heredoc_survives_the_shell():
    """The DECISIVE check: deliver each body through the shell to Python.

    An unquoted delimiter + any backslash escape makes the shell disagree with
    the file. This is the exact shipped failure mode, so it is asserted rather
    than reasoned about.
    """
    bash = _real_bash()
    if bash is None:
        pytest.skip("no runnable real bash in this environment")

    delivered_bodies = 0
    failures: list[str] = []
    for line_no, _indent, tag, quoted, body in _heredocs(_workflow_text()):
        if not body.strip():
            continue
        # Rebuild the shell construct EXACTLY: both quotes are required around
        # the delimiter, else bash opens a quote it never closes ("unexpected
        # EOF while looking for matching `''"). An earlier version of this test
        # emitted only the opening quote and failed on its own bug.
        heredoc = f"cat <<{quoted}{tag}{quoted}\n{body}\n{tag}\n"
        proc = subprocess.run(
            [bash, "-c", heredoc],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        stderr = proc.stderr or ""
        if proc.returncode != 0:
            failures.append(
                f"release.yml:{line_no} (<<{tag}) shell delivery failed: {stderr.strip()[:200]}"
            )
            continue

        delivered_bodies += 1
        delivered = proc.stdout
        if delivered.rstrip("\n") != body.rstrip("\n"):
            failures.append(
                f"release.yml:{line_no} (<<{tag}) the shell CHANGED the body "
                f"(unquoted heredoc with shell-expandable characters)"
            )
        parse = subprocess.run(
            [sys.executable, "-c", "import sys, ast; ast.parse(sys.stdin.read())"],
            input=delivered,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if parse.returncode != 0:
            failures.append(
                f"release.yml:{line_no} (<<{tag}) shell-delivered source does not "
                f"parse: {(parse.stderr or '').strip()[:200]}"
            )
    assert not failures, "\n".join(failures)
    assert delivered_bodies > 0, (
        "no heredoc body was actually delivered through the shell — this gate "
        "silently checked nothing, which is worse than failing"
    )


def test_heredoc_bodies_do_not_rely_on_shell_expansion():
    """If a body needs no shell expansion, the delimiter must be QUOTED.

    An unquoted delimiter is only correct when the body genuinely interpolates
    shell values. None of release.yml's Python bodies do, so leaving one
    unquoted is a latent escaping bug waiting for the next backslash.
    """
    offenders: list[str] = []
    for line_no, _indent, tag, quoted, body in _heredocs(_workflow_text()):
        if quoted:
            continue
        if "$" in body or "`" in body:
            continue  # genuinely needs the shell; expansion is intended
        offenders.append(
            f"release.yml:{line_no} uses an UNQUOTED <<{tag} but the body has no "
            f"shell expansion — quote it as <<'{tag}' so backslashes survive"
        )
    assert not offenders, "\n".join(offenders)
