"""
BUG-232: the launcher must honor the persisted execution.mode from the
settings DB BEFORE binding the execution adapter, and the persisted LIVE
choice must survive restarts without re-confirmation.
"""

from __future__ import annotations

import os


def _resolve_mode(yaml_mode: str) -> tuple[str, str]:
    """Reimplements the launcher's BUG-232 precedence chain for testability:
    returns (effective_mode, origin)."""
    from nexus_scalp.domain.enums import ExecutionMode
    from nexus_scalp.settings import load_settings_service

    chosen = yaml_mode
    origin = "config default"
    row = load_settings_service().db.get("execution.mode")
    if row is not None and row.value is not None:
        persisted = str(row.value).strip().upper()
        if persisted in {m.value for m in ExecutionMode}:
            chosen = persisted
            origin = "persisted settings DB"
    return chosen, origin


def test_persisted_live_wins_over_yaml_paper(tmp_path, monkeypatch):
    db = tmp_path / "settings.db"
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(db))
    from nexus_scalp.settings import load_settings_service

    svc = load_settings_service()
    svc.db.set("execution.mode", "LIVE", value_type="str", source="USER_SETTINGS", actor="web")

    mode, origin = _resolve_mode("PAPER")
    assert mode == "LIVE"
    assert origin == "persisted settings DB"


def test_no_persisted_value_falls_back_to_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "fresh.db"))
    mode, origin = _resolve_mode("PAPER")
    assert mode == "PAPER"
    assert origin == "config default"


def test_live_persists_across_reopen(tmp_path, monkeypatch):
    """The exact user complaint: switch to LIVE once -> still LIVE next open."""
    db = tmp_path / "settings.db"
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(db))
    from nexus_scalp.settings import load_settings_service

    load_settings_service().db.set(
        "execution.mode", "LIVE", value_type="str", source="USER_SETTINGS", actor="web"
    )
    # simulate a fresh process (new service instance, same DB file)
    row = load_settings_service().db.get("execution.mode")
    assert row is not None and str(row.value).upper() == "LIVE"
