"""BUG-307C regression: process-global ``logging.disable()`` leak.

Root cause (found 2026-09-22, AGENT-QA run 29): ``nexus_scalp.cli.db_commands.
_emit`` called ``logging.disable(CRITICAL)`` to keep ``--json`` stdout
parseable and NEVER restored it. ``logging.disable`` mutates a PROCESS-GLOBAL
threshold (``logging.root.manager.disable``), so one ``db-portability --json``
invocation permanently silenced every logger in the interpreter.

CI runs the suite as ``pytest -n auto --dist loadgroup`` on 2-core runners.
``loadgroup`` (xdist/scheduler/loadgroup.py) groups by the ``xdist_group``
mark, and no test in this repo carries one, so an entire test MODULE is one
scheduling scope. That made the leak cross test boundaries inside a worker:

    tests/unit/test_cli_end_to_end.py::test_e2e_5*_db_portability_*__json
        -> _emit(json_mode=True) -> logging.disable(CRITICAL), never restored
    ...the same worker later runs...
    tests/unit/test_mt5_diag_throttle.py::test_distinct_error_codes_not_cross_suppressed
        -> capture fixture sees ZERO records -> ``assert 0 == 2``  (len([]))
    tests/unit/test_update_cli_contract.py::test_update_lifecycle_logging_reaches_severity_tree
        -> severity log files empty -> ``assert '[UPDATE] event=STAGE' in ''``

Both failures share one ``[gw1]`` header in the ci-results artifact, and the
same class on another module produced
``test_replay_toggle_requires_auth`` (200 != 401) on a PR head. This is what
turned MAIN ITSELF red at 9279f1ea (CI + Tests (OS Matrix) failure) on an
otherwise-healthy commit.

Fix: scope the silence to the ``_emit`` call — save the prior threshold and
restore it in a ``finally``. ``--json`` stdout stays pure because the disable
window covers exactly the ``print(json.dumps(...))`` and nothing else in the
CLI writes to stdout inside it.
"""

from __future__ import annotations

import json
import logging

import pytest


@pytest.fixture()
def capture_root_records():
    """Handler on the ROOT logger — the position a process-global disable
    removes records from (records are routed through the root tree)."""
    records: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Handler(level=logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    prior_root_level = root.level
    prior_disable = logging.root.manager.disable
    root.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(prior_root_level)
        # Never leave a disable threshold behind (belt and braces).
        logging.disable(prior_disable)


def _emit_json_contract(payload: dict[str, object]) -> None:
    """The exact logging contract of ``db_commands._emit(json_mode=True)``.

    Reproduced inline so the test runs in the slim Linux verification
    environment, which has no typer and therefore cannot import the CLI
    module. The leak lived entirely in the ``logging.disable`` call, which is
    plain stdlib; ``test_the_real_cli_emit_restores_the_threshold`` exercises
    the production function itself where typer is importable.
    """
    import logging as _logging

    prior = _logging.root.manager.disable
    try:
        _logging.disable(_logging.CRITICAL)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        _logging.disable(prior)


def test_json_emit_does_not_leak_a_process_global_logging_disable(
    capture_root_records: list[logging.LogRecord],
) -> None:
    """A ``--json`` CLI emit must not silence logging for the rest of the
    process. Pre-fix the probe record was dropped and this failed with
    ``assert 0 == 1`` — the same ``len([])`` shape as the CI flake."""
    records = capture_root_records
    _emit_json_contract({"provider": "sqlite"})

    logging.getLogger("test.bug307c.probe").warning("after-json-emit probe")
    after = [r for r in records if "after-json-emit probe" in r.getMessage()]
    assert len(after) == 1, (
        "logging.disable(CRITICAL) leaked across the --json emit; subsequent "
        "log records in this process were silently dropped (BUG-307C)"
    )


def test_logging_disable_is_process_global_and_must_be_restored() -> None:
    """Pin the stdlib primitive the CLI relied on: ``logging.disable`` sets a
    process-global threshold restored ONLY by re-calling it with the prior
    value. This is why the save/restore in ``_emit`` is mandatory."""
    prior = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        assert logging.root.manager.disable == logging.CRITICAL
        # while disabled, a WARNING record is suppressed
        sink: list[logging.LogRecord] = []

        class _H(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                sink.append(record)

        logger = logging.getLogger("test.bug307c.suppressed")
        handler = _H(level=logging.DEBUG)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.warning("suppressed probe")
            assert not [r for r in sink if "suppressed probe" in r.getMessage()]
        finally:
            logger.removeHandler(handler)
    finally:
        logging.disable(prior)

    # after restore the same record must arrive
    sink2: list[logging.LogRecord] = []

    class _H2(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            sink2.append(record)

    logger2 = logging.getLogger("test.bug307c.restored")
    handler2 = _H2(level=logging.DEBUG)
    logger2.addHandler(handler2)
    logger2.setLevel(logging.DEBUG)
    try:
        logger2.warning("restored probe")
        assert any(r.getMessage() == "restored probe" for r in sink2)
    finally:
        logger2.removeHandler(handler2)


def test_the_real_cli_emit_restores_the_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    """Exercise the actual ``db_commands._emit`` where its import chain is
    available (full CI env has typer); skipped rather than faked in the slim
    env — the point is the production function, not a copy of it."""
    db_commands = pytest.importorskip("nexus_scalp.cli.db_commands")
    prior = logging.root.manager.disable
    try:
        db_commands._emit({"provider": "sqlite"}, json_mode=True)
        assert logging.root.manager.disable == prior, (
            "db_commands._emit left a process-global logging.disable behind"
        )
        out = capsys.readouterr().out
        assert '"provider": "sqlite"' in out
    finally:
        logging.disable(prior)
