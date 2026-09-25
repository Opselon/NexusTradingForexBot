"""BUG-289: CI Telegram notifier accepted a call shape CI never used.

tests-os.yml:184 runs
    telegram_notify.py --results ci-results os-finished --os "<runner>"
and js-tests.yml:82 runs
    telegram_notify.py --results ci-results js-finished
With ``--os`` declared only on the GLOBAL parser, argparse routes flags after
the subcommand to the SUBPARSER -> every run died with
``error: unrecognized arguments: --os windows-latest`` (visible in
Py Tests (windows-latest) job log, run 34925973236 at b1fe0137). ``js-finished``
was not a subcommand at all -> usage error. Both steps are ``|| true`` +
``continue-on-error``, so the CI-finish Telegram notifications for the OS
matrix and the JS lane had NEVER been delivered; only the CI workflow's own
``run-finished`` (no trailing flags) worked.

Fix lives in scripts/ci/telegram_notify.py (the workflow files cannot be
edited from the swarm scope), so the script adapts to the shipped call shape.

These pins drive the CLI's main() with the EXACT argv shapes the workflows
emit and assert the DISPATCH seam: which reporter method ran with which
os_name tag. The reporter's network send is stubbed at the class level —
CI exports TELEGRAM_BOT_TOKEN/CHAT_ID into every job (a red on the first
version of this file proved the env leak), so the tests must never depend
on configuration state and must never touch the network.
"""

from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from nexus_scalp.observability.ci_telegram_reporter import CITelegramReporter

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


@pytest.fixture
def spy(monkeypatch):
    """Stub the reporter's completion dispatch; record (os_name) per call.

    Class-level patch: main() constructs a fresh CITelegramReporter per call
    (also via the release/push/pr lambdas), so instance patching is racy.
    notify_run_finished is the seam under test (os-finished + js-finished +
    run-finished all dispatch through it); the network sender below is a
    belt-and-braces guard so no test path can ever POST to Telegram.
    """
    calls: list[str | None] = []

    def fake_finished(self, *, os_name: str = ""):
        calls.append(os_name)
        return {"ok": True, "category": "STUBBED", "os_name": os_name}

    monkeypatch.setattr(CITelegramReporter, "notify_run_finished", fake_finished)
    monkeypatch.setattr(
        CITelegramReporter, "_send_text", lambda self, text, *, event_type="": {"ok": True}
    )
    return calls


def _run(tn, argv: list[str]) -> tuple[int, dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = tn.main(argv)
    return int(rc), json.loads(buf.getvalue())


def test_os_finished_with_trailing_os_flag_matches_workflow_argv(tn, spy, tmp_path):
    """The exact tests-os.yml shape must parse and carry the OS tag."""
    rc, payload = _run(
        tn,
        ["--results", str(tmp_path), "os-finished", "--os", "windows-latest"],
    )
    assert rc == 0
    assert spy == ["windows-latest"], (
        "os-finished --os must reach notify_run_finished(os_name=...) — the "
        "flag is emitted AFTER the subcommand by tests-os.yml:184"
    )
    assert payload["os_name"] == "windows-latest"


def test_js_finished_subcommand_exists(tn, spy, tmp_path):
    """The exact js-tests.yml shape must parse (it was a usage error before)."""
    rc, payload = _run(tn, ["--results", str(tmp_path), "js-finished"])
    assert rc == 0
    assert spy == ["js"], "js-finished must dispatch the completion with the 'js' tag"
    assert payload["os_name"] == "js"


def test_run_finished_without_flags_still_works(tn, spy, tmp_path):
    """The ci.yml shape (no trailing flags, no tag) is unchanged."""
    rc, _payload = _run(tn, ["--results", str(tmp_path), "run-finished"])
    assert rc == 0
    assert spy == [""], "plain run-finished dispatches untagged"


def test_unknown_subcommand_still_rejected(tn, spy, tmp_path):
    with pytest.raises(SystemExit):
        _run(tn, ["--results", str(tmp_path), "definitely-not-a-command"])
    assert spy == []


def test_bug289_grammar_source_pins():
    """Class guard: --os may NEVER return to the global parser only, and the
    workflow call shapes must stay parseable (regex over the shipped script).
    """
    src = SCRIPT.read_text(encoding="utf-8")
    global_section = src.split("sub = parser.add_subparsers")[0]
    assert '"--os"' not in global_section, (
        "BUG-289 shape returned: --os declared before the subparsers means "
        "post-subcommand '--os <runner>' from tests-os.yml is unparsed"
    )
    assert 'sub.add_parser("os-finished")' in src and 'sub.add_parser("js-finished")' in src
    assert '"--os", dest="os_name"' in src.split('sub.add_parser("os-finished")')[1], (
        "the os-finished SUBPARSER must own --os"
    )
