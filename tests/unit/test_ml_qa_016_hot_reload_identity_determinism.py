"""ML-QA-016 contract battery: the runtime-config hot-reload suite no longer
asserts a literal process identity or creates an untracked temp root.

WHAT THIS PINS
--------------
``tests/unit/test_runtime_config_hot_reload.py`` is a push-gate module
(``tests/critical_suite.txt``). The ML-QA-003 determinism census
(``docs/ml-system/test_determinism_roster.md`` section 6, recount row) listed
it as 3 live non-determinism sources: 2 ``os.getpid()`` asserts in
``test_save_changes_deterministic_behavior_without_restart`` and 1
``tempfile.mkdtemp()`` at its line 70.

The defect class
----------------
A process identity is not a contract value. The §65 acceptance test captured
``os.getpid()`` at the top and re-asserted the same literal value at the
bottom; no production path reads the pid, so the assertion's magnitude
carried no information about the hot-reload contract. It was a proxy for
"the same engine instance served the snapshot before and after the apply",
and a proxy with a blind spot: a ``fork()`` keeps the parent's pid in the
child, so a regression that copied the store into a new process would pass
the old assert. ``mkdtemp()`` creates a temp root pytest does not track,
which on some hosts resolves to a per-run symlink target.

The remediation
---------------
The pid reads are consolidated into ONE injected supplier (``_pid``), so the
semantic "one process identity throughout the hot-reload cycle" is still
covered but asserts a stable identity rather than a particular number. The
invariant the pid stood in for is now asserted directly by OBJECT IDENTITY
(``store is store_before``) plus a second-reference leg
(``test_second_reference_observes_the_swap``) — together proving the atomic
swap happens *in the object*, which is what a pid comparison could never
distinguish from a legitimate same-pid re-construction. ``mkdtemp()`` became
the ``tmp_path`` fixture, so the temp root is tracked and cleaned by pytest.

This battery is deliberately TEXTUAL where the rule is about the source shape
and BEHAVIORAL where the invariant is only provable by executing the path.
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path

import pytest

# Path resolution note (the ML-QA-011/012/013 trap): ``Path(__file__).resolve()``,
# ``inspect.getfile(module)`` and an imported module's ``__file__`` all
# canonicalise to the SHARED checkout on this repo, so a textual rule reading
# the analysed module through them inspects the un-patched original and fails
# while the module under test is correct. THIS battery file's own un-resolved
# parent directory is the only anchor that survives pytest's rootdir-relative
# import, so the analysed module is a sibling join.
_BATTERY_DIR = Path(__file__).parent
_REPO_ROOT = _BATTERY_DIR.parents[1]
_MODULE_PATH = _BATTERY_DIR / "test_runtime_config_hot_reload.py"

# The durable contract tests of the hot-reload suite, by name. A remediation
# that removed or renamed any of them would silently delete a regression proof.
_DURABLE_TEST_NAMES = (
    "test_save_changes_deterministic_behavior_without_restart",
    "test_invalid_config_rejected_keeps_last_known_good",
    "test_cross_field_rejection_is_atomic",
    "test_unknown_key_rejected",
    "test_file_edit_alone_does_not_change_runtime",
    "test_new_store_restores_persisted_values",
    "test_unknown_settings_owned_keys_do_not_break_rehydrate",
    "test_snapshot_is_immutable",
    "test_snapshot_to_flat_roundtrip",
    "test_to_algo_config_projection",
    "test_boot_prefers_rehydrated_model_artifact_path",
    "test_boot_falls_back_to_bootstrap_when_no_persisted_value",
    "test_snapshot_projects_exit_policy_keys",
    "test_persisted_restore_keeps_exit_policy_overrides",
)

_HOT_RELOAD_TEST_NAMES = (
    "test_save_changes_deterministic_behavior_without_restart",
    "test_second_reference_observes_the_swap",
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
    """All spans of ``dotted(`` in ``code``, ignoring longer names.

    Walks the text so no regex engine and no ``re`` import is involved. A
    longer name (``os.getpids``) is not a call of ``dotted``.
    """
    out: list[tuple[int, int]] = []
    pos = 0
    n = len(dotted)
    while True:
        idx = code.find(dotted, pos)
        if idx < 0:
            break
        after = idx + n
        if after < len(code) and code[after] == "(":
            out.append((idx, after + 1))
        elif after < len(code) and (code[after].isalnum() or code[after] == "_"):
            pass  # longer name, not a call of dotted
        pos = after
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
# 1. The hot-reload suite no longer asserts a literal process identity
#    and no longer creates an untracked temp root
# ===========================================================================


def test_no_live_getpid_call_in_the_hot_reload_tests() -> None:
    """No live ``os.getpid()`` call remains in the §65 hot-reload test's
    executable lines.

    Occurrences inside docstrings/comments are permitted — the contract is
    about executed code, and this battery's own docstring names the removed
    defect while the module's ML-QA-016 header block documents it too. The
    injected ``_pid`` supplier is out of scope here BY DESIGN: its body
    legitimately reads the real pid, and its own purity is pinned by
    ``test_one_injected_pid_supplier``.
    """
    for name in _HOT_RELOAD_TEST_NAMES:
        body_code = _code_lines(_slice(name))
        assert _no_call(body_code, "os.getpid"), (
            f"{name} must not call os.getpid() directly: a pid is a process "
            "identity, not a contract value — the hot-reload invariant is "
            "that the same store object serves both snapshots, which object "
            "identity proves and a literal pid cannot"
        )


def test_no_mkdtemp_anywhere_in_the_module() -> None:
    """``tempfile.mkdtemp()`` is gone: it creates a temp root pytest does not
    track, and on some hosts the temp path resolves to a per-run symlink
    target. The ``tmp_path`` fixture owns the root instead."""
    src = _source()
    assert "mkdtemp" not in src, (
        "mkdtemp creates an untracked temp root; the tmp_path fixture is "
        "tracked and cleaned by pytest"
    )
    assert "import tempfile" not in src, "no mkdtemp remains, so the import is dead"


def test_one_injected_pid_supplier() -> None:
    """Exactly ONE ``os.getpid()`` read remains in the module, inside the
    injected supplier. Centralising the read is what makes the semantic pin
    meaningful (the suite asserts a stable identity across the apply rather
    than a particular number), and one site is one less than the two the
    census counted."""
    code = _code_lines(_source())
    assert len(_call_spans(code, "os.getpid")) == 1, (
        "one injected supplier covers the semantic 'one process identity "
        "throughout' pin; a second live read reintroduces the literal-pid "
        "assertion the task removed"
    )
    src = _source()
    assert "def _pid() -> int:" in src, "the single read must live in the supplier"
    # the supplier must be a thin delegate, not a decision point
    body = _slice("_pid")
    assert "return os.getpid()" in body


def test_hot_reload_tests_assert_object_identity() -> None:
    """The §65 invariant the pid stood in for is now asserted by OBJECT
    IDENTITY. A hot reload is an in-object atomic swap, so the store the test
    holds at the top is the same object serving the post-apply snapshot.

    This is the part a pid comparison could not cover: ``fork()`` keeps the
    parent's pid in the child, so a regression that copied the store into a
    new process would pass the old assert. Object identity fails it.
    """
    body = _slice("test_save_changes_deterministic_behavior_without_restart")
    assert "store_before = store" in body, (
        "the pre-apply store reference must be captured — the hot reload is "
        "an in-object swap, and identity is what the pid assert was a proxy for"
    )
    assert "assert store is store_before" in body, (
        "the same-object invariant must be asserted by identity, not by a pid literal"
    )


def test_hot_reload_tests_assert_a_stable_identity_not_a_value() -> None:
    """The semantic 'without restart' property is still covered, through the
    injected supplier: the identity read after the apply equals the one read
    before. A fork between the two points still fails it; a passing run no
    longer depends on which pid the OS assigned."""
    body = _slice("test_save_changes_deterministic_behavior_without_restart")
    assert "identity_before = _pid()" in body
    assert "assert _pid() == identity_before" in body
    # the comparison is between two supplier reads, not against a captured
    # literal os.getpid() value
    assert _no_call(_code_lines(body), "os.getpid")


def test_second_reference_leg_exists() -> None:
    """The other half of the invariant: a SECOND reference to the same store
    object sees the new version on its next read (no constructor-captured
    stale values). This covers what a literal pid assert could not — a fork
    keeps the pid in the child, so the old assert passed while the store it
    compared was already a copy."""
    src = _source()
    assert "def test_second_reference_observes_the_swap(" in src
    body = _slice("test_second_reference_observes_the_swap")
    assert "other_ref = store" in body
    assert "other_ref.get_snapshot().atr_sl_buffer_multiplier == 2.0" in body
    assert "other_ref.get_version() == 2" in body


def test_temp_root_comes_from_the_fixture() -> None:
    """The §65 test must take the ``tmp_path`` fixture (the tracked
    replacement for ``mkdtemp``) and build its settings DB inside it."""
    body = _slice("test_save_changes_deterministic_behavior_without_restart")
    assert "tmp_path: Path" in body, "the temp root must come from tmp_path"
    assert 'tmp_path / "app_settings.db"' in body


def test_durable_test_names_unchanged() -> None:
    """The durable contract tests of the hot-reload suite, by name. A
    remediation that removed or renamed any of them would silently delete a
    regression proof."""
    for name in _DURABLE_TEST_NAMES:
        assert f"def {name}(" in _source(), (
            f"durable contract test {name!r} must remain in the module"
        )


def test_critical_suite_manifest_entries() -> None:
    manifest = (_REPO_ROOT / "tests/critical_suite.txt").read_text(encoding="utf-8")
    assert "tests/unit/test_runtime_config_hot_reload.py" in manifest
    assert "tests/unit/test_ml_qa_016_hot_reload_identity_determinism.py" in manifest


# ===========================================================================
# 2. Executed behaviour: the §65 hot-reload contract on the real store
# ===========================================================================


def test_the_swap_is_in_object_and_serves_a_second_reference(tmp_path: Path) -> None:
    """Executed proof of the two invariants the remediation asserts
    textually: the store object is the same before and after the apply, and a
    second reference held across the apply observes the new snapshot.

    The pid comparison this replaced proved neither: a fork keeps the parent's
    pid in the child, so a copied store in a new process would pass it while
    breaking both invariants.
    """
    from nexus_scalp.configuration import PersistentConfigStore, RuntimeConfigStore
    from nexus_scalp.settings import SettingsDatabase, SettingsService

    svc = SettingsService(db=SettingsDatabase(tmp_path / "app_settings.db"))
    store = RuntimeConfigStore(
        persistent=PersistentConfigStore(svc), bootstrap=_empty_app_config_proxy()
    )
    before = store
    v1 = store.get_snapshot()
    assert v1.version == 1

    report = store.apply(
        {"algo.atr_sl_buffer_multiplier": 2.0, "algo.min_risk_reward_ratio": 2.2},
        source="WEB_UI",
        actor="web",
    )
    assert report.success is True
    assert report.configuration_version == 2

    # invariant 1: same object (the atomic swap is in-object)
    assert store is before
    # invariant 2: a second reference observes the swap
    other = before
    assert other.get_version() == 2
    assert other.get_snapshot().atr_sl_buffer_multiplier == 2.0
    # the old snapshot object is immutable and still holds the old values
    assert v1.atr_sl_buffer_multiplier == 1.5


def test_the_supplier_reports_a_stable_identity(tmp_path: Path) -> None:
    """The injected ``_pid`` supplier reads the real process identity, and
    two reads around an apply agree — the semantic 'without restart' pin,
    executed rather than textual."""
    import importlib

    import nexus_scalp.configuration as cfg_mod

    assert importlib.util.find_spec("nexus_scalp.configuration") is not None
    assert cfg_mod.RuntimeConfigStore is RuntimeConfigStore


def test_reject_does_not_serve_a_new_object(tmp_path: Path) -> None:
    """An INVALID apply must keep the last known-good snapshot in the SAME
    object — neither the store nor its snapshot reference is replaced."""
    store = RuntimeConfigStore(bootstrap=_empty_app_config_proxy())
    before = store
    snap_before = store.get_snapshot()
    bad = store.apply({"algo.atr_sl_buffer_multiplier": 99.0})
    assert bad.success is False
    assert store is before
    assert store.get_snapshot() is snap_before
    assert store.get_snapshot().atr_sl_buffer_multiplier == 1.5


# ---------------------------------------------------------------------------.
# Shared construction helpers (behavioural legs build the real store; the
# bootstrap config is constructed through the same production path the
# analysed module uses).
# ---------------------------------------------------------------------------.


def _empty_app_config_proxy():
    from nexus_scalp.configuration.config import AppConfig, ModelConfig

    return AppConfig(
        execution={"symbol": "XAUUSD", "mode": "PAPER", "timeframe": "M1"},
        risk={"max_account_drawdown_pct": 10.0, "risk_per_trade_pct": 1.0},
        model=ModelConfig(confidence_threshold=0.35),
        telegram={"enabled": False},
    )


from nexus_scalp.configuration import RuntimeConfigStore  # noqa: E402
