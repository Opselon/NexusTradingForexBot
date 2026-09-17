"""CI lane report — advisory summary writer for every CI lane (stdlib + repo only).

Writes three artifacts (none of them can fail CI):
  * GITHUB_STEP_SUMMARY  — human-readable lane purpose/limit + analysis verbatim
  * JSON artifact        — structured payload for downstream consumers
  * Telegram block       — via CITelegramReporter when configured (best-effort)

Every workflow lane that invokes telegram_notify.py should end with a step
that calls this module so the run always leaves a trace even when Telegram
is misconfigured or the AI endpoint is down.

This module is **stdlib + repo only**. It never imports torch/polars/heavy
deps. Import failures in CITelegramReporter are swallowed and reported as a
degraded verdict in the JSON payload.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

_SUMMARY_PATH = os.environ.get("GITHUB_STEP_SUMMARY") or ""
STEP_SUMMARY: Path | None = Path(_SUMMARY_PATH) if _SUMMARY_PATH else None
ARTIFACT_DIR = (
    Path(os.environ.get("GITHUB_WORKSPACE") or str(REPO_ROOT)) / "ci-results" / "run-info"
)


def _safe_text_payload(payload: dict[str, Any]) -> str:
    """Render the payload as a one-line status string for stdout."""
    return (
        f"lane={payload.get('lane', '?')} "
        f"purpose={payload.get('purpose', '?')} "
        f"verdict={payload.get('env_status', {}).get('verdict', 'unknown')}"
    )


def _append_summary(lines: list[str]) -> None:
    if not STEP_SUMMARY:
        return
    try:
        with STEP_SUMMARY.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        # The summary file may not exist on a non-Actions runner; never raise.
        pass


def _write_artifact(payload: dict[str, Any]) -> None:
    try:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        (ARTIFACT_DIR / "lane-report.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        pass


def _try_telegram_block(lane: str, payload: dict[str, Any]) -> None:
    """Best-effort Telegram block. Imports are lazy + isolated."""
    try:
        from nexus_scalp.observability.ci_telegram_reporter import CITelegramReporter
        from nexus_scalp.observability.telegram_html import format_artifact_summary

        reporter = CITelegramReporter(str(ARTIFACT_DIR))
        reporter._send_text(
            format_artifact_summary(
                reporter.context(),
                artifacts=["run-info/lane-report.json"],
                verified=[
                    f"lane={lane}",
                    payload.get("purpose", ""),
                    payload.get("limit", ""),
                ],
            ),
            event_type="LANE_REPORT",
        )
    except Exception as exc:
        payload["telegram_error"] = f"{exc.__class__.__name__}: {exc}"[:200]


def lane_report(
    lane: str,
    *,
    purpose: str = "",
    limit: str = "",
    analysis: str = "",
    why: str = "",
    env_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write lane-report artifacts and return the structured payload."""
    payload: dict[str, Any] = {
        "category": "LANE_REPORT",
        "lane": lane,
        "purpose": purpose,
        "limit": limit,
        "analysis": analysis,
        "why": why,
        "env_status": env_status or {},
    }

    # 1) GITHUB_STEP_SUMMARY (Markdown)
    _append_summary(
        [
            f"## NSE CI lane report — `{lane}`",
            "",
            f"- **Purpose**: {purpose or '—'}",
            f"- **Limit**: {limit or '—'}",
            f"- **WHY**: {why or '—'}",
            f"- **Specialist analysis**: {analysis or '—'}",
            (
                "- **Env deps**: "
                + (json.dumps(env_status, sort_keys=True) if env_status else "unknown")
            ),
            "",
        ]
    )

    # 2) JSON artifact
    _write_artifact(payload)

    # 3) Telegram block (advisory — never fails the lane)
    _try_telegram_block(lane, payload)

    return payload


def render_text(payload: dict[str, Any]) -> str:
    """Public wrapper: render a lane-report payload as a one-line string."""
    return _safe_text_payload(payload)


if __name__ == "__main__":
    lane = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    result = lane_report(
        lane,
        purpose=os.environ.get("CI_LANE_PURPOSE", ""),
        limit=os.environ.get("CI_LANE_LIMIT", ""),
        analysis=os.environ.get("CI_LANE_ANALYSIS", ""),
        why=os.environ.get("CI_LANE_WHY", ""),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    print(render_text(result))
