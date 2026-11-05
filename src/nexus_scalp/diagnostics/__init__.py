"""Opt-in diagnostics pipeline: Warning/Error logs shipped to the Nexus server.

Public surface:
- :func:`install_diagnostics` — attach the collector handler to the root
  logger and start the background shipper (only when consented).
- :func:`ensure_consent` — ask the user once (interactive contexts only).
- :mod:`nexus_scalp.diagnostics.consent` — persisted consent state.
- :mod:`nexus_scalp.diagnostics.shipper` — batching, retry queue, delivery.
"""

from __future__ import annotations

import logging
from pathlib import Path

from nexus_scalp.diagnostics.collector import DiagnosticCollector, DiagnosticLogHandler
from nexus_scalp.diagnostics.consent import consent_state

__all__ = [
    "DiagnosticCollector",
    "DiagnosticLogHandler",
    "consent_state",
    "ensure_consent",
    "install_diagnostics",
]


def ensure_consent(**kwargs) -> bool:
    """Ask-once wrapper over :func:`prompt_for_consent` (stable public name)."""
    from nexus_scalp.diagnostics.consent import prompt_for_consent

    return prompt_for_consent(**kwargs)


def install_diagnostics(
    source: str,
    *,
    queue_dir: Path | None = None,
    root_logger: logging.Logger | None = None,
) -> bool:
    """Attach the diagnostics handler + shipper when consented. True = active.

    Call sites: engine boot, CLI entry, web server startup. Safe to call
    multiple times; the handler is attached at most once per source.
    """
    from nexus_scalp.diagnostics.consent import is_enabled
    from nexus_scalp.diagnostics.shipper import get_session_shipper

    if not is_enabled():
        return False

    target = root_logger or logging.getLogger()
    shipper = get_session_shipper(source=source)
    for existing in target.handlers:
        if isinstance(existing, DiagnosticLogHandler) and existing.collector is shipper.collector:
            return True  # already installed for this session
    handler = DiagnosticLogHandler(shipper.collector)
    target.addHandler(handler)
    return True
