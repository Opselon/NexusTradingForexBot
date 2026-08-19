"""Incident reports — machine-readable JSON + human-readable Markdown (spec 34/46).

Every incident produces:
    artifacts/incidents/<incident_id>.json  (machine-readable, secret-masked)
    artifacts/incidents/<incident_id>.md    (human-readable)

Reports include: timeline, root cause, evidence, impact, affected IDs,
recovery plan, tests, status. Secret masking (spec 47) is applied to every
export — API keys, bot tokens, passwords, credentials, secrets from
configuration are never included.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.incidents.models import Incident

#: Sensitive key fragments — text/keys containing these are masked (spec 47).
SENSITIVE_FRAGMENTS: tuple[str, ...] = (
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "bot_token",
    "admin_id",
    "credential",
    "authorization",
    "private_key",
    "passwd",
)

_SECRET_RE = re.compile(
    r"(?i)((?:bot|api|access|secret|private|auth)[_-]?token|password|passwd|"
    r"api[_ -]?key|secret|credential|authorization)[\"']?\s*[:=]\s*"
    r"[\"']?[A-Za-z0-9_\-./+]{6,}[\"']?"
)


def mask_secrets(value: Any) -> Any:
    """Recursively masks sensitive fields in any JSON-able structure (spec 47)."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if any(frag in str(k).lower() for frag in SENSITIVE_FRAGMENTS):
                out[str(k)] = "[REDACTED]" if v not in (None, "") else v
            else:
                out[str(k)] = mask_secrets(v)
        return out
    if isinstance(value, list):
        return [mask_secrets(v) for v in value]
    if isinstance(value, str):
        return _SECRET_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    return value


@contextlib.contextmanager
def _restrictive_umask() -> Iterator[None]:
    """Temporarily set umask to 0o077 (owner-only), restore afterwards.

    Windows: umask is not supported; os.umask raises -> yield directly.
    POSIX: 0600-equivalent perms on newly created report files.
    """
    try:
        old_umask = os.umask(0o077)
        try:
            yield
        finally:
            os.umask(old_umask)
    except (AttributeError, OSError):
        yield


def incident_json(incident: Incident) -> dict[str, Any]:
    """Secret-masked machine-readable incident payload (spec 34/47)."""
    return mask_secrets(incident.as_dict())


def incident_markdown(incident: Incident) -> str:
    """Human-readable incident report (spec 34)."""
    d = incident.as_dict()
    lines = [
        f"# INCIDENT {d['incident_id']}",
        "",
        f"- **Status**: {d['status']}",
        f"- **Severity**: {d['severity']}",
        f"- **Category**: {d['category']}",
        f"- **Detected**: {d['detected_at']}",
        f"- **First seen**: {d['first_seen_at']}",
        f"- **Last seen**: {d['last_seen_at']}",
        f"- **Component**: {d['component']}",
        f"- **Operation**: {d['operation']}",
        f"- **Correlation ID**: {d['correlation_id'] or '—'}",
        f"- **Root cause status**: {d['root_cause_status']}",
        f"- **Fingerprint**: {d['fingerprint'] or '—'}",
        f"- **Repeated count**: {d['repeated_count']}",
        "",
        "## Root cause",
        "",
        d["root_cause"] or "UNKNOWN — requires further evidence.",
        "",
        "## Evidence",
        "",
    ]
    if not d["evidence"]:
        lines.append("_No evidence recorded yet._")
    for e in d["evidence"]:
        lines.append(f"- [{e['kind']}] {e['source']}: {e['detail']}")
    lines += [
        "",
        "## Impact",
        "",
        f"- Affected records: {d['impact']['affected_records']}",
        f"- Affected trades: {d['impact']['affected_trades']}",
        f"- Affected models: {d['impact']['affected_models']}",
        f"- Affected research runs: {d['impact']['affected_research_runs']}",
        f"- Blast radius: {d['impact']['blast_radius']}",
    ]
    if d["impact"]["affected_ui_endpoints"]:
        lines.append("- Affected UI endpoints: " + ", ".join(d["impact"]["affected_ui_endpoints"]))
    lines += ["", "## Timeline", ""]
    if not d["timeline"]:
        lines.append("_No timeline events yet._")
    for t in d["timeline"]:
        lines.append(f"- `{t['timestamp']}` [{t['source']}] {t['event_type']}")
    lines += ["", "## Recovery plan", ""]
    plan = d["recovery_plan"]
    lines.append(f"- State: {plan['status']}")
    lines.append(f"- What failed: {plan['what_failed']}")
    lines.append(f"- Why: {plan['why']}")
    lines.append(
        "- Trustworthy: " + "; ".join(plan["trustworthy"])
        if plan["trustworthy"]
        else "- Trustworthy: —"
    )
    lines.append("- Suspect: " + "; ".join(plan["suspect"]) if plan["suspect"] else "- Suspect: —")
    lines.append(
        "- Must NOT change: " + "; ".join(plan["must_not_change"])
        if plan["must_not_change"]
        else "- Must NOT change: —"
    )
    for o in plan["options"]:
        lines.append(f"- [{o['status']}] {o['step_id']} ({o['kind']}): {o['action']}")
    if d["quarantine_entries"]:
        lines += ["", "## Quarantine", ""]
        for q in d["quarantine_entries"]:
            lines.append(
                f"- {q['target_table']}::{q['record_key']} -> {q['status']} "
                f"({q['reason']}, incident {q['incident_id']})"
            )
    if d["related_bug_id"]:
        lines += ["", "## BUG linkage", "", f"- {d['incident_id']} -> {d['related_bug_id']}"]
        if d["fix_commit"]:
            lines.append(f"- Fix commit: {d['fix_commit']}")
        if d["regression_test"]:
            lines.append(f"- Regression test: {d['regression_test']}")
    if d["is_regression"]:
        lines.append(f"- REGRESSION of {d['previous_bug_id'] or 'unknown previous bug'}")
    lines += ["", f"_Generated {datetime.now(UTC).isoformat()} — TASK-12 incident engine._"]
    return "\n".join(lines)


def write_incident_reports(incident: Incident, base_dir: str | Path) -> dict[str, str]:
    """Writes JSON + Markdown reports under <base_dir>/incidents (spec 34).

    ``base_dir`` must be a DIRECTORY (e.g. the workspace root); the reports
    land in ``<base_dir>/incidents/<incident_id>.{json,md}``.
    """
    if str(base_dir).endswith(".db"):
        base_dir = Path(str(base_dir)).parent.parent  # tolerate db path passed by accident
    out_dir = Path(base_dir) / "incidents"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{incident.incident_id}.json"
    md_path = out_dir / f"{incident.incident_id}.md"
    # CodeQL #77 (clear-text storage): write report files with a
    # restrictive umask so credentials never land world-readable.
    with _restrictive_umask():
        json_path.write_text(
            json.dumps(incident_json(incident), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        md_path.write_text(incident_markdown(incident), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(md_path),
    }


def export_zip_bundle(
    incident: Incident,
    base_dir: str | Path,
    *,
    log_excerpts: list[str] | None = None,
    db_query_results: dict[str, Any] | None = None,
    model_manifest: dict[str, Any] | None = None,
    runtime_snapshot: dict[str, Any] | None = None,
) -> Path:
    """Optional ZIP export (spec 46): incident report + log excerpts + DB
    query results + model manifest + runtime snapshot. NEVER includes
    secrets — every payload is masked before zipping.
    """
    import zipfile

    out_dir = Path(base_dir) / "incidents" / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{incident.incident_id}.zip"
    payloads: dict[str, Any] = {
        f"incident_{incident.incident_id}.json": incident_json(incident),
        f"incident_{incident.incident_id}.md": incident_markdown(incident),
    }
    if log_excerpts:
        payloads["log_excerpts.txt"] = "\n".join(
            mask_secrets(str(x)) if isinstance(x, str) else str(x) for x in log_excerpts
        )
    if db_query_results is not None:
        payloads["db_query_results.json"] = mask_secrets(db_query_results)
    if model_manifest is not None:
        payloads["model_manifest.json"] = mask_secrets(model_manifest)
    if runtime_snapshot is not None:
        payloads["runtime_snapshot.json"] = mask_secrets(runtime_snapshot)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in payloads.items():
            data = (
                content
                if isinstance(content, str)
                else json.dumps(content, ensure_ascii=False, indent=2, default=str)
            )
            zf.writestr(name, data)
    return zip_path


__all__ = [
    "export_zip_bundle",
    "incident_json",
    "incident_markdown",
    "mask_secrets",
    "write_incident_reports",
]
