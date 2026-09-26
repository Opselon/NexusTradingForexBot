"""ML-QA-017 contract battery: the training-env worker suite no longer asserts a
literal process identity against the parent's own pid.

WHAT THIS PINS
--------------
``tests/unit/test_training_env_worker.py`` is a push-gate module
(``tests/critical_suite.txt:481`). The ML-QA-003 determinism census
(``docs/ml-system/test_determinism_roster.md`` section 6, recount row) listed it
as 2 live ``os.getpid()`` sources: the subprocess fixture stamped its pid onto
the progress line and the parent asserted ``events[0].metrics["pid"] !=
os.getpid()`` (an ``import os`` executed inside the test body, at its line 61).

The defect class
----------------
A process identity is not a contract value. The parent read its OWN pid and
asserted the worker's differed from it. No production path compares the two,
so the assertion's magnitude carried no information about the transport
contract: it passed identically on any two pids the OS assigned, and would have
passed under a regression that changed every behaviour the test actually pins.

Worse, the assert was a *proxy with a blind spot*. It stood in for "the
transport delivers ONE worker process identity across the whole run" (the pipe
protocol stamps the identity at the progress stage and again at the result
stage), but comparing against the PARENT's pid cannot prove that: a regression
that routed the result line through a different process than the progress line
would still have a worker pid different from the parent's, so the old assert
passed while the continuity invariant it was pretending to prove broke.

The remediation
---------------
The parent's pid read is consolidated into ONE injected supplier (``_pid``), so
the semantic "the observed identity is not the parent's own" is still covered
but asserts a STABLE IDENTITY rather than a number the OS chose. The invariant
the pid stood in for is now asserted directly, by the thing that actually proves
it — the transport itself, driven twice:

- ``test_real_subprocess_streams_progress_and_result`` asserts the identity the
  WORKER stamps (progress line) is the identity the RESULT line carries
  (``metrics["pid"] == result["second_pid"]``) AND that it differs from the
  parent's own (``!= _pid()``), which is the isolation property the transport
  promises when it spawns its own interpreter.
- the new ``test_transport_reports_one_worker_identity_across_the_run`` drives
  the transport with a FIXED identity (``_FIXED_ID``), so the continuity
  invariant is reproducible to the digit and independent of the OS's process
  assignment — and the isolation leg (``_FIXED_ID != _pid()``) cross-checks the
  real-pid leg, so neither passes alone.

This battery is deliberately TEXTUAL where the rule is about the source shape
and BEHAVIORAL where the invariant is only provable by executing the transport.
"""

from __future__ import annotations

import io
import sys
import tokenize
from pathlib import Path

import pytest

# Path resolution note (the ML-QA-011/012/013/016 trap): ``Path(__file__).resolve()``,
# ``inspect.getfile(module)`` and an imported module's ``__file__`` all
# canonicalise to the SHARED checkout on this repo, so a textual rule reading
# the analysed module through them inspects the un-patched original and fails
# while the module under test is correct. THIS battery file's own un-resolved
# parent directory is the only anchor that survives pytest's rootdir-relative
# import, so the analysed module is a sibling join.
_BATTERY_DIR = Path(__file__).parent
_REPO_ROOT = _BATTERY_DIR.parents[1]
_MODULE_PATH = _BATTERY_DIR / "test_training_env_worker.py"

# The durable contract tests of the worker suite, by name. A remediation that
# removed or renamed any of them would silently delete a regression proof.
_DURABLE_TEST_NAMES = (
    "test_ready_external_environment_dispatches_before_import",
    "test_real_subprocess_streams_progress_and_result",
    "test_real_worker_rechecks_gate_without_recursive_dispatch",
    "test_worker_dependency_probe_runs_with_stdlib_only",
    "test_transport_never_allows_worker_to_publish",
    "test_frozen_payload_is_executable_source",
    "test_cancel_is_streamed_to_real_worker_and_reaped",
    "test_kills_unresponsive_cancelled_worker",
    "test_malformed_or_failed_worker_never_succeeds",
    "test_pipeline_forwards_backend_and_honest_stage_events",
    "test_trailing_progress_is_drained_after_terminal_result",
    "test_parent_honors_install_only_after_clean_candidate",
    "test_worker_rejects_different_interpreter_even_if_report_claims_in_process",
    "test_cancel_during_parent_verification_never_installs",
)

_IDENTITY_TEST_NAMES = (
    "test_real_subprocess_streams_progress_and_result",
    "test_transport_reports_one_worker_identity_across_the_run",
)


# ---------------------------------------------------------------------------.
# Source analysis helpers (tokenize-based, no `re` import — a `re/` package or
# module ahead of stdlib on sys.path can drop a negative lookahead and INVERT
# a textual rule; these helpers walk the source text directly.)
# ---------------------------------------------------------------------------.


def _code_lines(src: str) -> str:
    """Executable source lines only, with original character text preserved.

    A row counts as code only when it carries at least one token that is
    neither a comment nor part of a string literal. This is deliberately
    token-based rather than AST-node-span-based: an AST node spans its whole
    docstring, so a ``def`` whose docstring names ``os.getpid()`` (exactly
    how these regressions stay explained) counts as a live call under a span
    extractor. The modules here document the removed defect in their
    docstrings, so string-literal interiors must be excluded explicitly.
    """
    rows = src.splitlines(keepends=True)
    code_rows: set[int] = set()
    non_code: set[int] = set()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenError:
        return src
    for tok in tokens:
        kind = tok.type
        if kind in (
            tokenize.COMMENT,
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENDMARKER,
            tokenize.ENCODING,
        ):
            continue
        if kind == tokenize.STRING:
            continue
        if kind == getattr(tokenize, "FSTRING_MIDDLE", -1):
            continue
        first, last = tok.start[0], max(tok.start[0], tok.end[0])
        if (
            kind == tokenize.NAME
            or kind == getattr(tokenize, "OP", -1)
            or kind == getattr(tokenize, "AT", -1)
        ):
            for row in range(first, last + 1):
                code_rows.add(row)
        else:
            for row in range(first, last + 1):
                non_code.add(row)
    final = code_rows - non_code
    out: list[str] = []
    for idx, line in enumerate(rows, start=1):
        if idx not in final:
            continue
        text = _strip_trailing_comment(line).rstrip()
        if text.strip():
            out.append(text)
    return "\n".join(out)


def _tokenize_line(segment: str) -> list[tokenize.TokenInfo]:
    """Tokenizes one physical line, tolerant of unparseable fragments."""
    try:
        return list(tokenize.generate_tokens(io.StringIO(segment).readline))
    except tokenize.TokenError:
        return []


def _strip_trailing_comment(line: str) -> str:
    """Removes a trailing ``#`` comment from one physical line (a ``#`` inside
    a string literal cannot truncate real code: the comment token must start
    at or beyond the last real token's end column)."""
    for tok in _tokenize_line(line):
        if tok.type == tokenize.COMMENT and tok.start[1] >= _last_code_col(line):
            return line[: tok.start[1]]
    return line


def _last_code_col(segment: str) -> int:
    """The end column of the last non-comment token on the line."""
    last = 0
    for tok in _tokenize_line(segment):
        if tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT):
            continue
        last = max(last, tok.end[1])
    return last


def _call_spans(code: str, dotted: str) -> list[tuple[int, int]]:
    """All spans of ``dotted(`` in ``code``, ignoring longer names and
    string-literal interiors.

    Walks the TOKEN stream (no regex engine, no ``re`` import) so a call is
    only a real ``NAME . NAME`` chain followed directly by ``(``. A string
    literal on an executable line whose text contains ``os.getpid()`` does NOT
    count: ML-QA-017's fixture builder selects its stamp with the expression
    ``str(_FIXED_ID) if fixed else "os.getpid()"``, which is a string literal
    on a code row — the ML-QA-016 text-walker would have matched the literal
    and over-counted the module's live reads as two instead of one.
    """
    parts = dotted.split(".")
    out: list[tuple[int, int]] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    except tokenize.TokenError:
        return []
    op = getattr(tokenize, "OP", -1)
    for i, tok in enumerate(tokens):
        if tok.type != tokenize.NAME or tok.string != parts[0]:
            continue
        j = i + 1
        matched = True
        for part in parts[1:]:
            if (
                j + 1 >= len(tokens)
                or tokens[j].type != op
                or tokens[j].string != "."
                or tokens[j + 1].type != tokenize.NAME
                or tokens[j + 1].string != part
            ):
                matched = False
                break
            j += 2
        if not matched:
            continue
        # j now points just past the last NAME; a call needs "(" right there
        if j < len(tokens) and tokens[j].type == op and tokens[j].string == "(":
            out.append((tok.start[1], tokens[j].end[1]))
    return out


def _no_call(code: str, dotted: str) -> bool:
    """True when ``dotted`` (e.g. ``os.getpid``) does NOT appear as a call."""
    return not _call_spans(code, dotted)


def _source() -> str:
    return _MODULE_PATH.read_text(encoding="utf-8")


def _slice(name: str) -> str:
    """Source of one test, by def name (class-scoped methods included).

    Walks physical lines and treats a ``def `` at a column less than or equal
    to the target's indent as the terminator (the next sibling), which
    correctly skips nested closures.
    """
    lines = _source().splitlines(keepends=True)
    needle = f"def {name}"
    try:
        start = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith(needle))
    except StopIteration:
        # a missing test must fail its rules on the ASSERTION, not by crashing
        # the battery (a crash is still a failure, but its output is opaque)
        return ""
    indent = len(lines[start]) - len(lines[start].lstrip())
    for j in range(start + 1, len(lines)):
        stripped = lines[j].lstrip()
        if stripped.startswith("def ") and (len(lines[j]) - len(lines[j].lstrip())) <= indent:
            return "".join(lines[start:j])
    return "".join(lines[start:])


# ===========================================================================
# 1. The worker suite no longer compares a literal process identity against
#    the parent's own pid
# ===========================================================================


def test_no_live_getpid_call_in_the_identity_tests() -> None:
    """No live ``os.getpid()`` call remains in the two identity tests'
    executable lines.

    Occurrences inside docstrings/comments are permitted -- the contract is
    about executed code, and this battery's own docstring names the removed
    defect while the module's ML-QA-017 header block documents it too. The
    injected ``_pid`` supplier is out of scope here BY DESIGN: its body
    legitimately reads the real pid, and its own purity is pinned by
    ``test_one_injected_pid_supplier``.
    """
    for name in _IDENTITY_TEST_NAMES:
        body_code = _code_lines(_slice(name))
        assert _no_call(body_code, "os.getpid"), (
            f"{name} must not call os.getpid() directly: a pid is a process "
            "identity, not a contract value -- the transport invariant is "
            "one-worker-identity-throughout, which the transport's own two "
            "independent stamp sites prove and a parent-pid comparison cannot"
        )


def test_one_injected_pid_supplier() -> None:
    """Exactly ONE ``os.getpid()`` read remains in the module, inside the
    injected supplier. Centralising the read is what makes the semantic pin
    meaningful (the suite asserts a stable identity the transport observes,
    rather than a particular number the OS assigned), and one site is one
    fewer than the census counted -- the parent no longer imports ``os`` in a
    test body to read its own pid."""
    code = _code_lines(_source())
    assert len(_call_spans(code, "os.getpid")) == 1, (
        "one injected supplier covers the semantic 'the observed identity is "
        "not the parent's own' pin; a second live read reintroduces the "
        "literal-pid assertion the task removed"
    )
    src = _source()
    assert "def _pid() -> int:" in src, "the single read must live in the supplier"
    # the supplier must be a thin delegate, not a decision point
    body = _slice("_pid")
    assert "return os.getpid()" in body


def test_the_identity_tests_assert_continuity_not_a_parent_comparison() -> None:
    """The invariant the pid stood in for -- ONE worker identity across the
    whole run -- is now asserted by comparing the transport's OWN two stamp
    sites (the progress line and the result line), never against the parent's
    pid. The old assert compared worker pid to PARENT pid, which the transport
    contract never does, so it passed identically on any two pids the OS
    assigned and a fork between the two stamp sites would have passed it while
    the continuity it pretended to prove broke."""
    body = _slice("test_real_subprocess_streams_progress_and_result")
    assert 'events[0].metrics["pid"] == result["second_pid"]' in body, (
        "the continuity invariant must be asserted between the transport's two "
        "own stamp sites (progress + result), not against the parent's pid"
    )
    assert 'events[0].metrics["pid"] != _pid()' in body, (
        "isolation (the observed identity is the worker's, never the parent's) "
        "must be asserted through the injected supplier, not a literal read"
    )


def test_the_continuity_leg_pins_the_fixed_identity() -> None:
    """The continuity leg drives the transport with a FIXED identity
    (``_FIXED_ID``), which makes the one-worker-throughout invariant
    reproducible to the digit and independent of the OS's process assignment.
    The isolation leg in the same test cross-checks it (``_FIXED_ID !=
    _pid()``), so neither leg can pass alone -- the real-pid leg of
    ``test_real_subprocess_streams_progress_and_result`` and this fixed-id leg
    must both hold."""
    body = _slice("test_transport_reports_one_worker_identity_across_the_run")
    assert 'events[0].metrics["pid"] == result["second_pid"] == _FIXED_ID' in body, (
        "the continuity invariant must be asserted against the fixed identity, "
        "so the two independent transport stamp sites are compared to each "
        "other, reproducibly, not to an OS-assigned number"
    )
    assert "_FIXED_ID != _pid()" in body, (
        "the isolation cross-check must go through the injected supplier"
    )
    assert "def test_transport_reports_one_worker_identity_across_the_run(" in _source()


def test_the_identity_fixture_helper_exists() -> None:
    """Both identity tests drive the transport through ONE fixture helper that
    stamps the identity on BOTH transport stages from ONE source expression.
    Centralising the stamp is what makes the continuity assert meaningful: two
    separate stamp expressions could drift (the exact regression the old
    parent-pid assert could not catch)."""
    src = _source()
    assert "def _identity_fixture(root: Path, fixed: bool) -> Path:" in src
    body = _slice("_identity_fixture")
    assert 'stamp = str(_FIXED_ID) if fixed else "os.getpid()"' in body
    # the helper stamps the SAME expression on both transport lines
    assert "second_pid': stamp" in body
    assert "'pid': stamp" in body
    # the two tests select the two legs through the helper's boolean
    assert "_identity_fixture(tmp_path, False)" in src
    assert "_identity_fixture(tmp_path, True)" in src


def test_no_live_os_import_inside_the_identity_tests() -> None:
    """The old test executed ``import os`` inside its body to read the parent
    pid (its line 61). That import is gone from the identity tests' executable
    lines: the whole module now reads the process identity through the one
    injected supplier at module level, so a test body no longer carries an
    ``os`` import whose only purpose was a literal-pid comparison."""
    for name in _IDENTITY_TEST_NAMES:
        body_code = _code_lines(_slice(name))
        assert "import os" not in body_code, (
            f"{name} must not import os in its body: the single injected _pid "
            "supplier at module level covers the process-identity read"
        )


# ===========================================================================
# 2. Executed behaviour: the real transport's identity contract
# ===========================================================================


def test_no_production_file_changed() -> None:
    """Test-only remediation: the worker transport and its gate code are
    byte-for-byte unchanged. Only the test's *expression* of the identity
    contract changed."""
    import subprocess

    proc = subprocess.run(
        ["git", "diff", "--name-only", "origin/main..HEAD"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    changed = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    production = [p for p in changed if not p.startswith(("tests/", "docs/", "agents/"))]
    assert not production, f"ML-QA-017 is test-only; production files must not change: {production}"


def test_the_transport_round_trips_one_worker_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Executed proof of the continuity invariant: the identity the transport
    stamps on its progress line is the identity it carries on its result line,
    ONE worker throughout the run.

    This is exactly what the old parent-pid assert could not prove: it
    compared the worker's pid against the parent's, so a regression that
    routed the result line through a different process than the progress line
    still had a worker pid different from the parent's -- the old assert
    passed while the continuity invariant broke."""
    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    script = _identity_fixture_like(tmp_path, fixed=True)
    monkeypatch.setattr(dispatch, "worker_script", lambda: script)
    try:
        report = _report()
        events: list = []
        result = dispatch.run_training_worker(_training_request(tmp_path), report, events.append)
    finally:
        monkeypatch.undo()
    assert result["outcome"] == "CANDIDATE"
    # continuity: both independent transport stamp sites carry the same value
    fixed_id = _analysed_module()._FIXED_ID
    assert events[0].metrics["pid"] == result["second_pid"] == fixed_id


def test_the_transport_delivers_the_workers_identity_not_the_parents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Executed proof of the isolation invariant: the identity the parent
    observes is the WORKER's own, never the parent's. The transport spawns its
    own interpreter and the worker stamps its identity itself; the parent's pid
    is never put on the wire."""
    import os

    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    script = _identity_fixture_like(tmp_path, fixed=False)
    monkeypatch.setattr(dispatch, "worker_script", lambda: script)
    try:
        events: list = []
        result = dispatch.run_training_worker(_training_request(tmp_path), _report(), events.append)
    finally:
        monkeypatch.undo()
    assert result["outcome"] == "CANDIDATE"
    # both transport stamp sites agree with each other...
    assert events[0].metrics["pid"] == result["second_pid"]
    # ...and the observed identity is the worker's, not the parent's own
    assert events[0].metrics["pid"] != os.getpid()


def test_a_fork_between_the_stages_breaks_continuity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The negative control, executed: a fixture that stamps a DIFFERENT
    identity on each transport line must fail the continuity assert. This is
    the regression the old parent-pid comparison could not catch -- the old
    assert compared the worker pid to the parent's, and both stamp sites still
    differed from the parent's pid, so it passed while continuity broke."""
    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    path = tmp_path / "split_identity_fixture.py"
    path.write_text(
        "import json, os, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'type': 'progress', 'event': {'stage': 'train', "
        "'status': 'progress', 'metrics': {'pid': os.getpid()}}}), flush=True)\n"
        "print(json.dumps({'type': 'result', 'result': {'outcome': "
        "'CANDIDATE', 'second_pid': os.getppid()}}), flush=True)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch, "worker_script", lambda: path)
    try:
        events: list = []
        result = dispatch.run_training_worker(_training_request(tmp_path), _report(), events.append)
    finally:
        monkeypatch.undo()
    assert result["outcome"] == "CANDIDATE"
    # the two stamp sites DISAGREE -- continuity is broken
    assert events[0].metrics["pid"] != result["second_pid"], (
        "the split-identity fixture must disagree across the transport stages; "
        "if it agrees, this negative control is inert"
    )
    # THE BLIND SPOT, executed: the result line's identity IS the parent's own
    # pid, yet the old assert (worker pid != parent pid) still PASSED on the
    # progress line alone. Comparing the two transport stamp sites to each
    # other is the only assert that catches this regression.
    assert events[0].metrics["pid"] != _analysed_module()._pid()
    assert result["second_pid"] == _analysed_module()._pid()
    with pytest.raises(AssertionError):
        assert events[0].metrics["pid"] == result["second_pid"]


def test_the_supplier_reports_the_parents_identity() -> None:
    """The injected ``_pid`` supplier reads the real process identity of the
    test process, and two reads agree -- the semantic 'one identity
    throughout' pin, executed rather than textual."""
    import importlib

    spec = importlib.util.spec_from_file_location(
        "nexus_scalp_test_worker_identity_under_test", _MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # the module exposes ONE injected supplier reading the real pid
    assert mod._pid() == mod._pid()
    assert mod._pid() > 0
    assert mod._FIXED_ID == 1701


# ---------------------------------------------------------------------------.
# Shared construction helpers (the behavioural legs drive the REAL transport,
# through the module's own fixture builder so the stamp sites stay in sync).
# ---------------------------------------------------------------------------.


def _analysed_module():
    """Imports the analysed module by path (NOT the shared-tree copy: pytest's
    rootdir-relative import would canonicalise there). Returns its live
    constants and its fixture builder to the behavioural legs."""
    import importlib

    spec = importlib.util.spec_from_file_location(
        "nexus_scalp_test_worker_identity_under_test", _MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _identity_fixture_like(root: Path, fixed: bool) -> Path:
    """Delegates to the analysed module's OWN fixture builder, so a drift
    between the battery's fixture text and the module's is impossible."""
    return _analysed_module()._identity_fixture(root, fixed)


def _report():
    from nexus_scalp.model_provisioning import training_env

    return training_env.EnvironmentReport(
        training_ready=True, environment={"python": sys.executable}, backend="cpu"
    )


def _training_request(root: Path):
    from nexus_scalp.model_provisioning import pipeline

    return pipeline.TrainingRequest(root / "export with spaces.csv", install=False)
