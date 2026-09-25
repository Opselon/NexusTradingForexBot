#!/usr/bin/env python3
"""GitHub Actions entry point for the NSE Telegram CI/CD observability layer.

Usage (in workflow steps — NEVER fail CI on a Telegram error):

    python scripts/ci/telegram_notify.py run-started [--results DIR]
    python scripts/ci/telegram_notify.py run-finished [--results DIR]
    python scripts/ci/telegram_notify.py test-summary [--results DIR]
    python scripts/ci/telegram_notify.py artifacts [--results DIR]
    python scripts/ci/telegram_notify.py release-started --tag v1.0.0
    python scripts/ci/telegram_notify.py release-success --tag v1.0.0 [--results DIR]
    python scripts/ci/telegram_notify.py release-failed --tag v1.0.0 \
        --failed-phase build --error-class BUILD_FAILURE [--results DIR]
    python scripts/ci/telegram_notify.py push --author ... --commits N --latest SHA
    python scripts/ci/telegram_notify.py pr --action opened --title ... [--pr 123]

Exit code: 0 always (notifications are advisory; CI must never fail because
Telegram is degraded). Prints a JSON summary of what was attempted/sent.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

# BUG-303: stdlib-only env probe BEFORE importing the observability package.
# Lanes that call this entry point WITHOUT installing the project (JS Tests,
# Trivy, SKIP-REPORT OS leg, ci-summary) previously died with
# ModuleNotFoundError: No module named 'structlog'. Because every call site is
# `|| true` + continue-on-error, the Telegram notification was silently lost.
# The probe reports missing deps as JSON; if any are missing and --no-env-repair
# is not set, we pip install --user the small pinned subset in THIS interpreter
# (no torch/polars, no repo write). Import failure still emits a structured
# ENV_IMPORT_FAILED payload instead of a bare traceback.
_ENV_REPAIR = "--no-env-repair" not in sys.argv


def _env_probe_status() -> dict:
    """Stdlib-only dependency probe. Safe to call before any app import."""
    try:
        from nexus_scalp.observability.ci_env_probe import probe as env_probe

        return {"category": "ENV_PROBE", "status": env_probe()}
    except Exception as exc:  # pragma: no cover - probe is stdlib-only
        return {"category": "ENV_PROBE_ERROR", "error": f"{type(exc).__name__}: {exc}"[:300]}


def _env_import_failed_payload(exc: BaseException) -> dict:
    """BUG-303 structured payload replacing a bare traceback."""
    return {
        "category": "ENV_IMPORT_FAILED",
        "error": f"{type(exc).__name__}: {exc}"[:300],
        "env_probe": _env_probe_status().get("status", {}),
        "diagnosis": (
            "The observability package could not be imported because required "
            "third-party deps are absent in the CI interpreter (typical shape: "
            "ModuleNotFoundError: No module named 'structlog' on lanes that do "
            "not `pip install -e .`)."
        ),
        "remedy": (
            "Re-run without --no-env-repair so the pinned subset "
            "(structlog, pydantic, pydantic-settings, PyYAML, numpy) is "
            "installed into the user site; or pre-install the project in the lane."
        ),
    }


def _repair_env() -> dict:
    """Install missing observability deps via pip --user (same interpreter)."""
    probe_result = _env_probe_status()
    status = probe_result.get("status") or {}
    missing = status.get("missing") or []
    if not missing:
        return {"category": "ENV_REPAIR", "needed": False, "missing": []}
    cmd = [sys.executable, "-m", "pip", "install", "--user", *missing]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=300)
    except Exception as exc:  # advisory, never fail CI
        return {
            "category": "ENV_REPAIR_FAILED",
            "needed": True,
            "missing": missing,
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }
    after = _env_probe_status().get("status") or {}
    return {
        "category": "ENV_REPAIR",
        "needed": True,
        "missing": missing,
        "pip_rc": proc.returncode,
        "pip_stderr_tail": (proc.stderr or "")[-400:],
        "missing_after": after.get("missing", []),
    }


if not _ENV_REPAIR:
    _ENV_REPAIR_RESULT: dict | None = None
else:
    # Probe FIRST (stdlib only); repair only when deps are missing. Both happen
    # BEFORE `from nexus_scalp.observability...` so the reporter import lands on
    # a healthy interpreter instead of dying with a masked ModuleNotFoundError.
    _pre = _env_probe_status()
    if (_pre.get("status") or {}).get("missing"):
        _ENV_REPAIR_RESULT = _repair_env()
        print(json.dumps(_ENV_REPAIR_RESULT, indent=2, sort_keys=True))
    else:
        _ENV_REPAIR_RESULT = {"category": "ENV_REPAIR", "needed": False, "missing": []}

try:
    from nexus_scalp.observability.ci_telegram_reporter import CITelegramReporter

    _IMPORT_ERROR: BaseException | None = None
except BaseException as _exc:
    CITelegramReporter = None  # type: ignore[assignment,misc]
    _IMPORT_ERROR = _exc


def _reporter(args: argparse.Namespace) -> CITelegramReporter:
    return CITelegramReporter(
        args.results,
        chat_id=args.chat_id or None,
        bot_token=args.bot_token or None,
    )


def _emit(result: dict) -> int:
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NSE Telegram CI/CD notifier")
    parser.add_argument("--results", default=str(REPO_ROOT / "ci-results"))
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--bot-token", default="")
    sub = parser.add_subparsers(dest="command", required=True)
    # BUG-303: allow callers to skip the pip repair step.
    parser.add_argument(
        "--no-env-repair",
        action="store_true",
        help="skip stdlib-only env probe + pip --user repair of missing observability deps",
    )

    sub.add_parser("run-started").set_defaults(
        func=lambda a: _emit(_reporter(a).notify_run_started())
    )
    sub.add_parser("run-finished").set_defaults(
        func=lambda a: _emit(_reporter(a).notify_run_finished())
    )
    # OS-matrix variant (tests-os.yml os-finished): same payload as run-finished,
    # tagged with the runner OS so the channel shows which leg finished.
    # BUG-289: the --os FLAG LIVES ON THE SUBCOMMAND. tests-os.yml:184 calls
    # `telegram_notify.py --results ci-results os-finished --os "..."` — flags
    # after the subcommand are parsed by the SUBPARSER, so a global-only --os
    # made every run die with "unrecognized arguments: --os windows-latest"
    # and the OS-leg Telegram notification silently never fired (the `|| true`
    # in the workflow hid it). The workflow file cannot be edited from here,
    # so the script adapts to the shipped call shape.
    p = sub.add_parser("os-finished")
    p.add_argument("--os", dest="os_name", default="", help="runner OS tag for the message")
    p.set_defaults(
        func=lambda a: _emit(_reporter(a).notify_run_finished(os_name=getattr(a, "os_name", "")))
    )
    # JS lane (js-tests.yml:82): same completion payload, tagged 'js' so the
    # channel shows WHICH lane finished. BUG-289: the workflow has always
    # called this non-existent subcommand -> usage error, notification never
    # sent.
    p = sub.add_parser("js-finished")
    p.set_defaults(func=lambda a: _emit(_reporter(a).notify_run_finished(os_name="js")))
    sub.add_parser("test-summary").set_defaults(
        func=lambda a: _emit(_reporter(a).notify_test_summary())
    )
    sub.add_parser("artifacts").set_defaults(
        func=lambda a: _emit(_reporter(a).notify_artifact_summary())
    )

    p = sub.add_parser("release-started")
    p.add_argument("--tag", default="")
    p.add_argument("--phase", default="")
    p.set_defaults(
        func=lambda a: _emit(_reporter(a).notify_release_started(tag=a.tag, phase=a.phase))
    )

    p = sub.add_parser("release-success")
    p.add_argument("--tag", default="")
    p.set_defaults(func=lambda a: _emit(_reporter(a).notify_release_success(tag=a.tag)))

    p = sub.add_parser("release-failed")
    p.add_argument("--tag", default="")
    p.add_argument("--failed-phase", default="")
    p.add_argument("--failed-job", default="")
    p.add_argument("--error-class", default="")
    p.add_argument("--error-detail", default="")
    p.add_argument("--retry-count", type=int, default=0)
    p.set_defaults(
        func=lambda a: _emit(
            _reporter(a).notify_release_failure(
                tag=a.tag,
                failed_phase=a.failed_phase,
                failed_job=a.failed_job,
                error_class=a.error_class,
                error_detail=a.error_detail,
                retry_count=a.retry_count,
            )
        )
    )

    from nexus_scalp.observability.telegram_html import format_pr_event, format_push_event

    p = sub.add_parser("push")
    p.add_argument("--author", default="")
    p.add_argument("--commits", type=int, default=1)
    p.add_argument("--latest", default="")
    p.add_argument("--messages", default="")
    p.add_argument("--additions", type=int, default=0)
    p.add_argument("--deletions", type=int, default=0)
    p.set_defaults(
        func=lambda a: _emit(
            _send_custom(
                _reporter(a),
                format_push_event(
                    _reporter(a).context(),
                    author=a.author,
                    commit_count=a.commits,
                    latest_sha=a.latest,
                    messages=[m for m in a.messages.split("||") if m] if a.messages else [],
                    additions=a.additions,
                    deletions=a.deletions,
                ),
            )
        )
    )

    p = sub.add_parser("pr")
    p.add_argument("--pr", default="")
    p.add_argument("--action", default="updated")
    p.add_argument("--title", default="")
    p.add_argument("--author", default="")
    p.add_argument("--changed-files", type=int, default=0)
    p.add_argument("--additions", type=int, default=0)
    p.add_argument("--deletions", type=int, default=0)
    p.set_defaults(
        func=lambda a: _emit(
            _send_custom(
                _reporter(a),
                format_pr_event(
                    _reporter(a).context().with_pr(a.pr),
                    action=a.action,
                    title=a.title,
                    author=a.author,
                    changed_files=a.changed_files,
                    additions=a.additions,
                    deletions=a.deletions,
                ),
            )
        )
    )
    from nexus_scalp.observability.telegram_html import format_security_event

    p = sub.add_parser("security")
    p.add_argument("--scan", default="")
    p.add_argument("--status", default="")
    p.add_argument("--detail", default="")
    p.set_defaults(
        func=lambda a: _emit(
            _send_custom(
                _reporter(a),
                format_security_event(
                    _reporter(a).context(), scan=a.scan, status=a.status, detail=a.detail
                ),
            )
        )
    )

    # BUG-300: AI failure triage appended to the summary lanes. The analysis
    # is advisory; the command exits 0 even when the AI endpoint is down —
    # then the deterministic rules fallback carries the message instead.
    p = sub.add_parser("ai-triage")
    p.add_argument(
        "--kind",
        default="ci-failure",
        choices=("ci-failure", "release-failure", "security", "pr-analysis", "push-summary"),
    )
    p.add_argument(
        "--context", default="", help="optional JSON evidence file (else built from --results)"
    )
    p.add_argument(
        "--no-send", action="store_true", help="write ci-results/run-info/ai-analysis.* only"
    )
    p.set_defaults(
        func=lambda a: _emit(
            _ai_triage(
                a.results,
                kind=a.kind,
                context_path=a.context or None,
                send_telegram=not a.no_send,
            )
        )
    )

    args = parser.parse_args(argv)
    if not args.no_env_repair:
        status_probe = _env_probe_status()
        if (status_probe.get("status") or {}).get("missing"):
            repair_res = _repair_env()
            print(json.dumps(repair_res, indent=2, sort_keys=True))

    if _IMPORT_ERROR is not None or CITelegramReporter is None:
        print(
            json.dumps(
                _env_import_failed_payload(
                    _IMPORT_ERROR or ImportError("CITelegramReporter not available")
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    try:
        return int(args.func(args) or 0)
    except Exception as err:
        status = _env_probe_status()
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(err)[:300],
                    "env_probe": status.get("status", {}),
                    "category": "ENV_IMPORT_FAILED",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0


def _send_custom(reporter: CITelegramReporter, html_text: str) -> dict:
    return reporter._send_text(html_text, event_type="CUSTOM")


def _ai_triage(
    results_dir: str, *, kind: str, context_path: str | None, send_telegram: bool
) -> dict:
    """BUG-300 dispatch seam: delegate to ci_ai_triage.notify_triage."""
    from nexus_scalp.observability.ci_ai_triage import notify_triage

    return notify_triage(
        kind,
        results_dir,
        context_path=context_path,
        send_telegram=send_telegram,
    )


if __name__ == "__main__":
    raise SystemExit(main())
