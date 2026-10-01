#!/usr/bin/env python3
"""Render provider runtime evidence into GitHub annotations and a step summary."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()

    if not args.evidence.exists():
        print("::error title=Runtime evidence missing::" + str(args.evidence))
        return 0

    data: dict[str, Any] = json.loads(args.evidence.read_text(encoding="utf-8"))
    obs = data.get("observability", {})
    errors = list(obs.get("errors", []))
    warnings = list(obs.get("warnings", []))
    tracebacks = list(obs.get("tracebacks", []))
    findings = list(data.get("findings", []))
    launcher = data.get("launcher", {})
    exit_code = launcher.get("exit_code")

    error_types = {
        "process-log",
        "traceback",
        "api",
        "database-query",
        "process-exit",
        "harness",
        "coverage-floor",
        "soak-duration",
    }
    finding_errors = [item for item in findings if item.get("type") in error_types]
    finding_warnings = [
        item
        for item in findings
        if item.get("type") not in error_types
        and item.get("type") not in {"process-log", "traceback"}
    ]

    provider = data.get("provider", "?")
    status = data.get("status", "?")
    soak_sec = data.get("actual_soak_sec", 0)
    req_soak = data.get("requested_soak_sec", 120)

    is_crashed = exit_code not in (0, None) or any(
        f.get("type") == "process-exit" for f in findings
    )

    print(
        f"provider={provider} "
        f"status={status} "
        f"soak={soak_sec}s/{req_soak}s "
        f"process_exit={exit_code} "
        f"crashed={is_crashed} "
        f"api_queries={data.get('api', {}).get('query_count_total', 0)} "
        f"db_queries={data.get('database', {}).get('query_count_total', 0)} "
        f"errors={max(len(errors), len(finding_errors))} "
        f"warnings={len(warnings) + len(finding_warnings)} "
        f"tracebacks={len(tracebacks)}"
    )

    if is_crashed:
        print(
            f"::error title=Process Crashed [{provider}]::Engine process terminated prematurely with exit code {exit_code} during soak/startup"
        )

    for event in errors[:30]:
        source = event.get("source") or {}
        suffix = f" [{source.get('file')}:{source.get('line')}]" if source else ""
        message = str(event.get("message") or event.get("error") or event)
        print(f"::error title=Provider runtime error [{provider}]::{message[:3000]}{suffix}")

    for event in warnings[:30]:
        source = event.get("source") or {}
        suffix = f" [{source.get('file')}:{source.get('line')}]" if source else ""
        message = str(event.get("message") or event.get("error") or event)
        print(f"::warning title=Provider runtime warning [{provider}]::{message[:3000]}{suffix}")

    for item in finding_errors[:30]:
        message = str(item.get("message") or item.get("error") or item)
        print(f"::error title=Runtime certification finding [{provider}]::{message[:3000]}")
    for item in finding_warnings[:30]:
        message = str(item.get("message") or item.get("error") or item)
        print(f"::warning title=Runtime certification warning [{provider}]::{message[:3000]}")

    for tb in tracebacks[:15]:
        source = tb.get("source") or {}
        suffix = f" [{source.get('file')}:{source.get('line')}]" if source else ""
        tb_lines = tb.get("lines", [])
        trace = "\n".join(str(x) for x in tb_lines[-16:]) if tb_lines else "No lines"
        exc = f" {tb.get('exception_type')}" if tb.get("exception_type") else ""
        print(f"::error title=Runtime traceback{exc} [{provider}]::{trace[:4000]}{suffix}")

    # Render Step Summary markdown if GITHUB_STEP_SUMMARY is available
    summary_env = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_env:
        md_lines = [
            f"### 🧪 Full NSE Runtime Soak Report: `{provider}`",
            "",
        ]
        if is_crashed:
            md_lines.extend(
                [
                    f"> 💥 **PROCESS CRASHED:** Engine process exited prematurely with code `{exit_code}` after {soak_sec}s.",
                    "",
                ]
            )
        else:
            state_desc = (
                "Clean" if not errors and not warnings and not tracebacks else "With Findings"
            )
            md_lines.extend(
                [
                    f"> ✅ **PROCESS ALIVE:** Remained running for full {soak_sec}s soak ({state_desc}).",
                    "",
                ]
            )

        md_lines.extend(
            [
                "| Metric | Value |",
                "|---|---|",
                f"| **Provider** | `{provider}` |",
                f"| **Overall Status** | **`{status}`** |",
                f"| **Process Exit Code** | `{exit_code if exit_code is not None else 0}` |",
                f"| **Soak Duration** | `{soak_sec}s` / `{req_soak}s` |",
                f"| **API Queries** | `{data.get('api', {}).get('query_count_total', 0)}` |",
                f"| **DB Queries** | `{data.get('database', {}).get('query_count_total', 0)}` |",
                f"| **Errors** | `{max(len(errors), len(finding_errors))}` |",
                f"| **Warnings** | `{len(warnings) + len(finding_warnings)}` |",
                f"| **Tracebacks** | `{len(tracebacks)}` |",
                "",
            ]
        )

        if tracebacks:
            md_lines.append(f"#### 📋 Full Tracebacks ({len(tracebacks)})")
            for i, tb in enumerate(tracebacks[:20], 1):
                tb_lines = "\n".join(tb.get("lines", []))
                exc_title = (
                    f"{tb.get('exception_type', 'Traceback')}: {tb.get('exception_message', '')}"
                ).strip(": ")
                md_lines.append(
                    f"<details open>\n<summary><b>#{i} {html.escape(exc_title or 'Traceback')}</b> (lines {tb.get('start_line_no')}-{tb.get('end_line_no')})</summary>\n\n```python\n{tb_lines}\n```\n</details>\n"
                )

        if errors or finding_errors:
            md_lines.append(f"#### ❌ Errors ({max(len(errors), len(finding_errors))})")
            for err in errors[:25]:
                msg = err.get("message") or err.get("error") or str(err)
                md_lines.append(f"- `line {err.get('line_no', '?')}`: {html.escape(str(msg))}")
            for item in finding_errors[:25]:
                msg = item.get("message") or item.get("error") or str(item)
                md_lines.append(f"- **Finding [{item.get('type')}]:** {html.escape(str(msg))}")
            md_lines.append("")

        if warnings or finding_warnings:
            md_lines.append(f"#### ⚠️ Warnings ({len(warnings) + len(finding_warnings)})")
            for w in warnings[:25]:
                msg = w.get("message") or w.get("error") or str(w)
                md_lines.append(f"- `line {w.get('line_no', '?')}`: {html.escape(str(msg))}")
            for item in finding_warnings[:25]:
                msg = item.get("message") or item.get("error") or str(item)
                md_lines.append(f"- **Finding [{item.get('type')}]:** {html.escape(str(msg))}")
            md_lines.append("")

        if is_crashed:
            log_tail = obs.get("log_tail", [])
            if log_tail:
                tail_str = "\n".join(log_tail[-40:])
                md_lines.append(
                    f"#### 📄 Last Log Output Before Crash\n\n```text\n{tail_str}\n```\n"
                )

        summary_file = Path(summary_env)
        try:
            with summary_file.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(md_lines) + "\n")
        except Exception as exc:
            print(f"::warning::Failed to write step summary: {exc}")

    # Deliver Telegram notification if credentials are configured
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("NEXUS_TELEGRAM_BOT_TOKEN")
    tg_chat = (
        os.environ.get("TELEGRAM_CHAT_ID")
        or os.environ.get("NEXUS_TELEGRAM_ADMIN_ID")
        or os.environ.get("USER_ID")
    )
    if tg_token and tg_chat:
        try:
            from nexus_scalp.observability.telegram_notifier import TelegramNotifier

            notifier = TelegramNotifier(bot_token=tg_token, admin_id=tg_chat, enabled=True)
            icon = (
                "💥"
                if is_crashed
                else (
                    "🔴"
                    if status not in ("PASS", "PASS_WITH_WARNINGS")
                    else ("🟡" if (warnings or finding_warnings) else "🟢")
                )
            )
            tg_lines = [
                f"{icon} <b>NSE Runtime Soak Test: <code>{provider}</code></b>",
                f"<b>Status:</b> <code>{status}</code> · <b>Duration:</b> {soak_sec}s / {req_soak}s",
                f"<b>Exit code:</b> <code>{exit_code if exit_code is not None else 0}</code>",
                f"<b>API Queries:</b> {data.get('api', {}).get('query_count_total', 0)} · <b>DB Queries:</b> {data.get('database', {}).get('query_count_total', 0)}",
                f"<b>Errors:</b> {max(len(errors), len(finding_errors))} · <b>Warnings:</b> {len(warnings) + len(finding_warnings)} · <b>Tracebacks:</b> {len(tracebacks)}",
            ]
            if tracebacks:
                tg_lines.append("\n<b>Tracebacks:</b>")
                for tb in tracebacks[:3]:
                    exc_title = f"{tb.get('exception_type', 'Traceback')}: {tb.get('exception_message', '')}".strip(
                        ": "
                    )
                    tb_tail = "\n".join(tb.get("lines", [])[-6:])
                    tg_lines.append(
                        f"• <b>{html.escape(exc_title or 'Traceback')}</b>\n<pre>{html.escape(tb_tail)}</pre>"
                    )
            if errors or finding_errors:
                tg_lines.append("\n<b>Errors:</b>")
                for err in (errors + finding_errors)[:3]:
                    msg = err.get("message") or err.get("error") or str(err)
                    tg_lines.append(f"• <code>{html.escape(str(msg)[:150])}</code>")
            elif warnings or finding_warnings:
                tg_lines.append("\n<b>Top Warnings:</b>")
                for w in (warnings + finding_warnings)[:3]:
                    msg = w.get("message") or w.get("error") or str(w)
                    tg_lines.append(f"• <code>{html.escape(str(msg)[:150])}</code>")

            notifier.send(
                "\n".join(tg_lines),
                event_type="RUNTIME_SOAK",
                severity="INFO",
                wait_timeout=10.0,
            )
            notifier.stop_worker(timeout=5.0)
        except Exception as tg_err:
            print(f"::warning::Telegram notification skipped: {tg_err}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
