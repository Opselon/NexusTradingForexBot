"""Startup lifecycle coordinator (lifecycle spec §3/§8/§9).

ONE authoritative background handshake per process. Every entry point
(desktop EXE, CLI, packaged launcher, web server) calls
:func:`start_background_handshake` — never its own polling loop.

The handshake performs the spec's startup sequence against NSE-Platform:

    LOAD SECURE INSTALLATION ID  (platform_link.store)
    LOAD EXISTING CLIENT TOKEN   (platform_link.store)
    CONTACT NSE-PLATFORM         (/api/client/me — canonical startup payload)
    VALIDATE TOKEN + BINDING     (server-side; 401 => VERIFICATION_EXPIRED)
    REVALIDATE ENTITLEMENT       (server lease verdict, e.g. GrantedFresh)
    DISCOVER OFFICIAL MODEL CATALOG + BENCHMARK METADATA
    COMPARE INSTALLED MODEL WITH LATEST VERSION (client-side, advisory)
    PERSIST SNAPSHOT             (offline-visible, never a substitute for live)

Rules honored here:
- Network failures are NEVER misread as NotStarred (UNAVAILABLE state).
- The snapshot is an offline UX convenience; every protected operation still
  goes through the fail-closed live gate (`require_download_authorization`).
- A user never waits on this: it runs on a daemon thread with hard timeouts.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from typing import Any

from nexus_scalp.platform_link.client import (
    PlatformClientError,
    PlatformRateLimitedError,
    PlatformTokenRejectedError,
    PlatformUnavailableError,
)
from nexus_scalp.platform_link.service import LinkStatus, PlatformLinkService
from nexus_scalp.platform_link.states import (
    VerificationState,
    state_from_startup_payload,
)

logger = logging.getLogger(__name__)


@dataclass
class StartupHandshake:
    """Everything the UI/CLI needs after launch — no live call required."""

    ok: bool = False
    state: VerificationState = VerificationState.NOT_CONNECTED
    reason: str | None = None
    github_login: str | None = None
    installation_id: str | None = None
    entitlement_decision: str | None = None
    access_allowed: bool | None = None
    checked_at: str | None = None
    valid_until: str | None = None
    next_recheck_at: str | None = None
    latest_model: dict[str, Any] | None = None
    installed_model_version: str | None = None
    update_available: bool | None = None
    recovered_setup_stage: str | None = None
    checked_at_epoch: float | None = None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["state"] = self.state.value
        return out

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StartupHandshake":
        try:
            state = VerificationState(payload.get("state", VerificationState.NOT_CONNECTED.value))
        except ValueError:
            state = VerificationState.NOT_CONNECTED
        data = dict(payload)
        data["state"] = state
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def degraded(self) -> bool:
        """True when the snapshot could not be refreshed this launch."""
        return not self.ok


def _installed_model_version() -> str | None:
    """Advisory read of the serving slot's recorded official version.

    Truth comes from the install-state sidecar NEXT TO the verified bundle
    (``model_provisioning.record_install``); a sidecar claiming a version it
    cannot hash-match is ignored downstream, and here it is only UX input.
    """
    try:
        from nexus_scalp.model_provisioning.service import (
            read_install_state,
            serving_model_path,
        )

        state = read_install_state(serving_model_path())
        version = str(state.get("model_version", "") or "")
        return version or None
    except Exception:  # pragma: no cover - advisory only, never fails boot
        return None


def run_startup_handshake(
    service: PlatformLinkService | None = None,
    *,
    snapshot_store: Any | None = None,
) -> StartupHandshake:
    """Blocking handshake. Bounded by the client's own HTTP timeouts."""
    svc = service or _default_service()
    store = snapshot_store or svc.store
    result = StartupHandshake()

    token = None
    try:
        token = svc.store.client_token()
    except Exception:  # pragma: no cover - store failure => not connected
        token = None
    if token is None:
        result.reason = "no client credential — official model access not configured"
        result.state = VerificationState.NOT_CONNECTED
        return _finish(store, result)

    try:
        payload = svc.client.client_me(token)
    except PlatformTokenRejectedError:
        svc.store.clear_client_token()
        result.state = VerificationState.VERIFICATION_EXPIRED
        result.reason = "The platform token was rejected. Reconnect GitHub to renew it."
        return _finish(store, result)
    except PlatformUnavailableError as exc:
        # Offline: report the LAST KNOWN snapshot alongside the failure —
        # a network error must never look like a revoked star.
        cached = _cached_or_none(store)
        result.reason = str(exc)
        if cached is not None and cached.ok:
            # Label it as stale-but-known so the UI never mistakes this for a
            # fresh green check.
            cached.reason = f"handshake unavailable ({exc}); showing last known state"
            return cached
        result.state = VerificationState.VERIFICATION_UNAVAILABLE
        return _finish(store, result)
    except PlatformRateLimitedError as exc:
        result.state = VerificationState.VERIFICATION_UNAVAILABLE
        result.reason = str(exc)
        return _finish(store, result)
    except PlatformClientError as exc:  # unexpected taxonomy => unknown, fail closed
        result.state = VerificationState.VERIFICATION_UNAVAILABLE
        result.reason = str(exc)
        return _finish(store, result)

    result.ok = True
    result.state = state_from_startup_payload(payload)
    result.github_login = payload.get("github_login")
    result.installation_id = payload.get("installation_id")
    entitlement = payload.get("entitlement") or {}
    result.entitlement_decision = entitlement.get("decision")
    result.access_allowed = bool(entitlement.get("access_allowed"))
    result.checked_at = entitlement.get("checked_at")
    result.valid_until = entitlement.get("valid_until")
    result.next_recheck_at = entitlement.get("next_recheck_at")
    result.latest_model = _model_catalog(payload)
    result.installed_model_version = _installed_model_version()
    result.update_available = _compare_versions(result.latest_model, result.installed_model_version)

    # Keep the LinkStatus cache coherent with the live verdict so the
    # offline-grace window in require_download_authorization stays honest.
    svc.publish_status(
        LinkStatus(
            state=result.state,
            github_login=result.github_login,
            repository=payload.get("repository"),
            entitlement_state=(payload.get("entitlement") or {}).get("state"),
            installation_id=result.installation_id,
            reason=result.reason,
        )
    )
    return _finish(store, result)


def _model_catalog(payload: dict[str, Any]) -> dict[str, Any] | None:
    model = payload.get("model")
    if isinstance(model, dict) and model.get("available"):
        return model
    return None


def _compare_versions(latest: dict[str, Any] | None, installed: str | None) -> bool | None:
    """Advisory only: newer official release exists vs the installed one."""
    if latest is None or not latest.get("version"):
        return None
    if not installed:
        return True  # nothing installed: official adoption is available
    return str(latest.get("version")) != str(installed)


def _finish(store: Any, result: StartupHandshake) -> StartupHandshake:
    import time as _time

    result.checked_at_epoch = _time.time()
    try:
        store.save_handshake(result.as_dict())
    except Exception:  # pragma: no cover - persistence is best-effort
        logger.debug("[PLATFORM-LINK] handshake snapshot save failed", exc_info=True)
    return result


def _cached_or_none(store: Any) -> StartupHandshake | None:
    try:
        raw = store.load_handshake()
    except Exception:  # pragma: no cover
        return None
    if not raw:
        return None
    try:
        return StartupHandshake.from_dict(raw)
    except Exception:  # pragma: no cover - malformed snapshot
        return None


def load_last_handshake(store: Any | None = None) -> StartupHandshake | None:
    """Last persisted handshake (offline-visible UI state)."""
    svc_store = store or (_default_service().store if _service_ready() else None)
    if svc_store is None:
        return None
    return _cached_or_none(svc_store)


# --------------------------------------------------------------------- owner
_handshake_lock = threading.Lock()
_handshake_thread: threading.Thread | None = None
_last_result: StartupHandshake | None = None


def _service_ready() -> bool:
    try:
        _default_service()
        return True
    except Exception:  # pragma: no cover - settings store unavailable
        return False


def _default_service() -> PlatformLinkService:
    from nexus_scalp.platform_link.service import get_platform_link_service

    return get_platform_link_service()


def run_handshake_snapshot() -> StartupHandshake:
    """Process-wide snapshot: run-or-return-last (single owner, no stampede)."""
    global _last_result  # noqa: PLW0603
    with _handshake_lock:
        if _last_result is None:
            _last_result = run_startup_handshake()
        return _last_result


def start_background_handshake(service: PlatformLinkService | None = None) -> threading.Thread | None:
    """Launch THE startup handshake on a daemon thread (once per process).

    Entry points call this and forget it — failures are logged, never raised;
    the UI reads :func:`run_handshake_snapshot` / the web route when ready.
    """
    global _handshake_thread, _last_result  # noqa: PLW0603

    def _run() -> None:
        global _last_result  # noqa: PLW0603
        try:
            svc = service or _default_service()
            result = run_startup_handshake(svc)
            with _handshake_lock:
                _last_result = result
            if result.ok:
                logger.info(
                    "[PLATFORM-LINK] startup handshake: %s (decision=%s, update=%s)",
                    result.state.value,
                    result.entitlement_decision,
                    result.update_available,
                )
            else:
                logger.warning(
                    "[PLATFORM-LINK] startup handshake unavailable: %s", result.reason
                )
        except Exception:  # pragma: no cover - absolute containment
            logger.warning("[PLATFORM-LINK] startup handshake crashed", exc_info=True)

    with _handshake_lock:
        if _handshake_thread is not None and _handshake_thread.is_alive():
            return None  # one owner — no duplicate polling chaos
        _handshake_thread = threading.Thread(
            target=_run, name="nexus-platform-handshake", daemon=True
        )
        _handshake_thread.start()
        return _handshake_thread


def reset_lifecycle() -> None:
    """Test seam."""
    global _handshake_thread, _last_result  # noqa: PLW0603
    with _handshake_lock:
        _handshake_thread = None
        _last_result = None
