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
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexus_scalp.observability.ci_telegram_reporter import CITelegramReporter  # noqa: E402


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

    sub.add_parser("run-started").set_defaults(
        func=lambda a: _emit(_reporter(a).notify_run_started())
    )
    sub.add_parser("run-finished").set_defaults(
        func=lambda a: _emit(_reporter(a).notify_run_finished())
    )
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

    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as err:  # never crash CI on a Telegram hiccup
        print(json.dumps({"ok": False, "error": str(err)[:300]}))
        return 0


def _send_custom(reporter: CITelegramReporter, html_text: str) -> dict:
    return reporter._send_text(html_text, event_type="CUSTOM")


if __name__ == "__main__":
    raise SystemExit(main())
