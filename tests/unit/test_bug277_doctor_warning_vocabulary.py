"""BUG-277 — `nexus doctor` WARNING-actions filter used the RENDERED label.

Producer vocabulary (release/health.py, 37 sites): HealthEntry.verdict is
``PASS | WARNING | FAIL``. The console LABEL for WARNING is "WARN"
(cli/styling.py:_verdict_style maps "WARNING" -> "[yellow]WARN[/yellow]").

doctor_cmd's filter compared the RAW field against the rendered label::

    warns = [e for e in entries if e.verdict == "WARN"]   # <- always []

Consequence (role 9 / operator observability): the actionable-suggestion
surface built from WARNING entries — the ``USER ACTION`` count and the
``NEXT: <suggestion>`` line in the closing panel (added by the 2026-09-02
"the operator must never have to ask what do I do next" UX pass) — was
DEAD. An install whose DB is a schema behind (`DATABASE` WARNING +
"Run `nexus db migrate`..."), or whose news feature is disabled, or whose
NVIDIA driver lacks CUDA, reported ``USER ACTION: 0`` and told the operator
``NEXT: nexus start   (paper mode by default)`` — start the engine on a
migration-behind ledger. That is exactly the BUG-254 dead-letter shape the
doctor exists to warn about BEFORE the first tick.

Tests here:
  1. behavior: a WARNING entry carrying a suggestion reaches USER ACTION/NEXT
     (RED before the fix: "USER ACTION: 0" + "paper mode by default").
  2. behavior: FAIL-only / clean states keep the existing guidance shape
     (no WARN inflation when nothing warns).
  3. contract: HealthEngine never PRODUCES the literal verdict "WARN" — the
     word exists only in the rendering layer.
  4. class guard: no HealthEntry-verdict comparison against the bare
     rendered label "WARN" anywhere in src/ (``verdict == "WARN"`` /
     ``!= "WARN"``). Non-verdict ``status == "WARN"`` users (smoke runner,
     release verify) have their own PASS|FAIL|WARN|SKIP contract and are
     out of scope by design.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _entry(category: str, verdict: str, reason: str = "r", suggestion: str = "", **kw):
    from nexus_scalp.release.health import HealthEntry

    return HealthEntry(category, verdict, reason, suggestion, **kw)


def _rich_plain(obj) -> str:
    """Render any rich renderable to plain text (width-pinned, no ANSI)."""
    from rich.console import Console

    buf = io.StringIO()
    Console(file=buf, width=200, color_system=None, force_terminal=False).print(obj)
    return buf.getvalue()


def _text_of(obj) -> str:
    """Best-effort plain text for whatever console.print received."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (list, tuple)):
        return "\n".join(_text_of(o) for o in obj)
    try:
        return _rich_plain(obj)
    except Exception:
        inner = getattr(obj, "renderable", None)
        if inner is not None and inner is not obj:
            return _text_of(inner)
        return str(obj)


def _invoke_doctor(monkeypatch, entries, verdict: str) -> str:
    """Runs `nexus doctor` (human path) with a stubbed HealthEngine surface.

    ``_health_entries`` is the ONLY source of entries for the human path, so
    the stub isolates the filter under test from host state (no DB, no
    model, no network). ``console.print`` is captured at module level and
    every renderable degraded to plain text for the assertions.
    """
    from nexus_scalp.cli import doctor as doctor_mod

    monkeypatch.setattr(doctor_mod, "_health_entries", lambda: (verdict, list(entries)))
    monkeypatch.setattr(doctor_mod, "_banner", lambda **kw: "doctor-banner")
    captured: list = []
    monkeypatch.setattr(doctor_mod.console, "print", lambda *a, **k: captured.extend(a))
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app

    res = CliRunner().invoke(app, ["doctor"])
    assert res.exit_code == 0, f"doctor exited {res.exit_code}: {res.output}"
    return "\n".join(_text_of(o) for o in captured)


def test_warning_with_suggestion_surfaces_as_user_action(monkeypatch) -> None:
    """DEGRADED install, no FAILs, one actionable WARNING (DB behind)."""
    out = _invoke_doctor(
        monkeypatch,
        [
            _entry("SYSTEM", "PASS", "linux x64"),
            _entry("RUNTIME", "PASS", "version 9.0.11"),
            _entry("CONFIGURATION", "PASS", "mode=PAPER"),
            _entry(
                "DATABASE",
                "WARNING",
                "audit.db: database schema 8 behind expected 9 (1 pending)",
                "Run `nexus db migrate` to apply pending schema migrations.",
            ),
            _entry("MODEL", "PASS", "champion present"),
        ],
        "DEGRADED",
    )
    # The actionable WARNING must be COUNTED ...
    assert "USER ACTION: 1" in out, out
    # ... and must DRIVE the next step instead of the all-clear default.
    assert "paper mode by default" not in out, out
    assert "nexus db migrate" in out.replace("\n", " "), out


def test_multiple_actionable_warnings_are_all_counted(monkeypatch) -> None:
    out = _invoke_doctor(
        monkeypatch,
        [
            _entry("DATABASE", "WARNING", "behind", "Run `nexus db migrate` now."),
            _entry("NEWS", "WARNING", "disabled", "Enable `news:` in config."),
            _entry("GPU", "WARNING", "no cuda", "Update the NVIDIA driver."),
            _entry("MODEL", "PASS", "ok"),
        ],
        "DEGRADED",
    )
    assert "USER ACTION: 3" in out, out


def test_no_warnings_keeps_the_safe_start_hint(monkeypatch) -> None:
    """Regression guard for the fix being 'always warn': a clean install must
    still say nexus start and USER ACTION: 0."""
    out = _invoke_doctor(
        monkeypatch,
        [
            _entry("SYSTEM", "PASS", "linux x64"),
            _entry("DATABASE", "PASS", "integrity ok"),
        ],
        "READY",
    )
    assert "USER ACTION: 0" in out, out
    assert "nexus start" in out, out


def test_warnings_without_suggestions_do_not_inflate_actions(monkeypatch) -> None:
    out = _invoke_doctor(
        monkeypatch,
        [
            _entry("SHADOW", "WARNING", "tables not created yet", ""),
            _entry("MODEL", "PASS", "ok"),
        ],
        "DEGRADED",
    )
    assert "USER ACTION: 0" in out, out


def test_health_engine_never_produces_the_rendered_label() -> None:
    """Producer vocabulary contract: run_all() emits PASS/WARNING/FAIL.

    The literal "WARN" belongs to the rendering layer only. If this ever
    flips, the doctor filter (and every consumer comparing to "WARNING")
    silently goes dead again — the exact failure mode of BUG-277.
    """
    from nexus_scalp.release.health import HealthEngine

    root = SRC_ROOT.parent
    engine = HealthEngine(
        config_path=root / "configs" / "base.yaml",
        workspace=root,
        db_path=root / "artifacts" / "audit.db",
    )
    entries = engine.run_all()
    assert entries, "HealthEngine.run_all() produced no entries"
    verdicts = {e.verdict for e in entries}
    assert "WARN" not in verdicts, verdicts
    assert verdicts <= {"PASS", "WARNING", "FAIL", "UNKNOWN"}, verdicts


def test_verdict_style_is_the_warn_label_producer() -> None:
    """Source pin: the styling layer maps WARNING -> WARN for display."""
    from nexus_scalp.cli.styling import _verdict_style

    assert _verdict_style("WARNING") == "[yellow]WARN[/yellow]"


_VERDICT_WARN_CMP = re.compile(r"""\bverdict\s*(?:==|!=)\s*["']WARN["']""")


def test_no_verdict_filter_uses_the_rendered_label() -> None:
    """CLASS GUARD: no HealthEntry.verdict comparison against bare "WARN".

    ``status == "WARN"`` consumers (nexus_scalp/smoke/runner.py,
    release/verify.py ReleaseCheckResult) own a DIFFERENT contract
    (PASS|FAIL|WARN|SKIP) and are intentionally out of scope: the guard is
    keyed on the field name ``verdict``.
    """
    offenders: list[str] = []
    for py in sorted(SRC_ROOT.rglob("*.py")):
        text = py.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _VERDICT_WARN_CMP.search(line):
                offenders.append(f"{py.relative_to(SRC_ROOT.parent)}:{lineno}: {line.strip()}")
    assert not offenders, "verdict filtered against rendered label 'WARN':\n" + "\n".join(offenders)


def test_doctor_filter_matches_producer_vocabulary_source_pin() -> None:
    """md7-style pin: the shipped filter text carries the producer word."""
    src = (SRC_ROOT / "nexus_scalp" / "cli" / "doctor.py").read_text(encoding="utf-8")
    assert 'e.verdict in ("WARNING", "WARN")' in src
    assert 'e.verdict == "WARN"]' not in src
    # Adjacent drift pin: the banner count derives from the engine (SSOT),
    # never a hard-coded literal ("24 checks" had already gone stale). The
    # phrase may appear in the explanatory comment; it must not be an argument.
    assert "system doctor · {len(entries)} checks" in src
    assert 'subtitle="system doctor · 24 checks"' not in src
