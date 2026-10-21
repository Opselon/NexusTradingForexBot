"""Request correlation binding for the logging pipeline (OBS-003 / OBS-015).

The logging pipeline has always installed
``structlog.contextvars.merge_contextvars`` as its first processor — so any
bound contextvar lands on every record — but nothing ever bound one.
Live evidence (audit 2026-08-31):

  * OBS-003: ``bind_correlation_id()`` had ZERO callers; a day of
    info/error logs held 0 real correlation ids, TELEGRAM lines carried the
    ``"-"`` placeholder, and no boot id existed;
  * OBS-015: ``X-Request-ID`` survived header -> response but never reached a
    server log, so a client-reported id could not be joined to a log line.

This module is the missing binding edge. The HTTP middleware
(:func:`nexus_scalp.web.errors.attach_request_id_middleware`) clears the
context per request and binds its ``request_id``; that single binding then
rides on every record emitted while the request is in flight, including
records emitted by stdlib loggers (the ``foreign_pre_chain`` merges the same
contextvars).

Design rules:

  * **Context-scoped, never global.** A ContextVar is per-task, so
    concurrent requests cannot overwrite each other's id the way a module
    global would.
  * **Always cleared.** The middleware binds inside a ``try/finally`` and
    clears in the ``finally``, so a worker thread reused by the next request
    cannot inherit a stale id — a wrong id is worse than no id.
  * **Cheap.** Binding is two ContextVar writes; no I/O, no allocation on the
    hot path, and a no-op when the key is already absent.

Nothing here is required for the pipeline to work: an unbound process logs
exactly as before.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

#: ContextVar keys the logging pipeline renders as record fields. Names match
#: the fields the audit's log census looks for.
CORRELATION_KEYS: tuple[str, ...] = ("request_id", "correlation_id", "boot_id")


def _contextvars() -> Any:
    """``structlog.contextvars`` (imported lazily so this module is safe early)."""
    import structlog

    return structlog.contextvars


def new_boot_id() -> str:
    """A fresh process-boot id (``boot_<12 hex>``).

    Shape deliberately matches the ``req_`` convention already on the
    redaction allowlist, so a boot id is never mistaken for a secret by the
    high-entropy catcher.
    """
    return "boot_" + uuid.uuid4().hex[:12]


def bind_boot_id(*, pid: int | None = None, boot_id: str | None = None) -> str:
    """Bind a boot id (and pid) for the lifetime of this process.

    Returns the id so a caller can report it (``nexus status --json``,
    ``/health``). Never bound per record: one boot = one id, which is what
    makes two interleaved boots in a single log file distinguishable.
    """
    bid = boot_id or new_boot_id()
    extra = {"pid": pid if pid is not None else os.getpid(), "boot_id": bid}
    try:
        _contextvars().bind_contextvars(**extra)
    except Exception:
        return bid
    return bid


def current_boot_id() -> str:
    """The boot id bound in this process, or ``""``."""
    try:
        return str(_contextvars().get_contextvars().get("boot_id") or "")
    except Exception:
        return ""


def current_request_id() -> str:
    """The request id bound to the current context, or ``""``.

    ``""`` (not ``None``) keeps call sites from formatting "None" into a log
    line; an absent id renders exactly as it did before this module existed.
    """
    try:
        bound = _contextvars().get_contextvars()
    except Exception:
        return ""
    return str(bound.get("request_id") or "")


def bind_request_scope(request_id: str, **extra: Any) -> None:
    """Bind ``request_id`` (plus any extra fields) to the CURRENT context.

    Callers must clear it afterwards; prefer :func:`request_scope`, which is
    the same thing with the guarantee enforced.
    """
    if not request_id:
        return
    cv = _contextvars()
    payload: dict[str, Any] = {"request_id": request_id}
    for key, value in extra.items():
        if value:
            payload[key] = value
    cv.bind_contextvars(**payload)


def clear_request_scope() -> None:
    """Drop every key this module binds (never touches unrelated context)."""
    try:
        cv = _contextvars()
    except Exception:
        return
    for key in CORRELATION_KEYS:
        try:
            cv.unbind_contextvars(key)
        except Exception:
            pass


@contextmanager
def request_scope(request_id: str, **extra: Any) -> Iterator[None]:
    """Bind a request id for the duration of a block, then always clear it.

    The ``finally`` clear is the contract: a pooled worker thread that serves
    the next request must not inherit this one's id.
    """
    bind_request_scope(request_id, **extra)
    try:
        yield
    finally:
        clear_request_scope()


__all__ = [
    "CORRELATION_KEYS",
    "bind_boot_id",
    "bind_request_scope",
    "clear_request_scope",
    "current_boot_id",
    "current_request_id",
    "new_boot_id",
    "request_scope",
]
