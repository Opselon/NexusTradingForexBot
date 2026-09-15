"""BUG-289: CI Telegram notifier accepted a call shape CI never used.

tests-os.yml:184 runs
    telegram_notify.py --results ci-results os-finished --os "<runner>"
and js-tests.yml:82 runs
    telegram_notify.py --results ci-results js-finished
With ``--os`` declared only on the GLOBAL parser, argparse routes flags after
the subcommand to the SUBPARSER -> every OS-leg run died with
``error: unrecognized arguments: --os windows-latest`` (visible in
Py Tests (windows-latest) job log, run 34925973236 at b1fe0137). ``js-finished``
was not a subcommand at all -> usage error. Both steps are ``|| true`` +
``continue-on-error``, so the CI-finish Telegram notifications for the OS
matrix and the JS lane had NEVER been delivered; only the CI workflow's own
``run-finished`` (no trailing flags) worked.

Fix lives in scripts/ci/telegram_notify.py (the workflow files are outside
this job's edit scope): --os moves onto the os-finished subparser, and
js-finished becomes a real subcommand (run-finished tagged 'js').

These pins run the CLI's main() with the EXACT argv shapes the workflows
emit. Telegram is unconfigured in tests (no token), so the reporter returns
the TELEGRAM_CONFIG_ERROR payload without any network — what is under test is
that the calls PARSE and reach the reporter, not that a message is sent.
"""

from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "telegram_notify.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("telegram_notify_bug289", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tn():
    return _load_module()


def _run(tn, argv: list[str]) -> tuple[int, dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = tn.main(argv)
    return int(rc), json.loads(buf.getvalue())


def test_os_finished_with_trailing_os_flag_matches_workflow_argv(tn, tmp_path):
    """The exact tests-os.yml shape must parse and reach the reporter."""
    rc, payload = _run(
        tn,
        ["--results", str(tmp_path), "os-finished", "--os", "windows-latest"],
    )
    assert rc == 0
    # parsed into the completion path (not an argparse SystemExit); unconfigured
    # Telegram returns the advisory config payload.
    assert payload["category"] == "TELEGRAM_CONFIG_ERROR"


def test_js_finished_subcommand_exists(tn, tmp_path):
    """The exact js-tests.yml shape must parse (it was a usage error before)."""
    rc, payload = _run(tn, ["--results", str(tmp_path), "js-finished"])
    assert rc == 0
    assert payload["category"] == "TELEGRAM_CONFIG_ERROR"


def test_run_finished_without_flags_still_works(tn, tmp_path):
    """The ci.yml shape (global --results, no trailing flags) is unchanged."""
    rc, payload = _run(tn, ["--results", str(tmp_path), "run-finished"])
    assert rc == 0
    assert payload["category"] == "TELEGRAM_CONFIG_ERROR"


def test_unknown_subcommand_still_rejected(tn, tmp_path):
    with pytest.raises(SystemExit):
        _run(tn, ["--results", str(tmp_path), "definitely-not-a-command"])
