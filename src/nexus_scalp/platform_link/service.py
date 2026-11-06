"""Platform link service: connect flow, verification state, and the
fail-closed authorization gate for protected official model downloads.

Single source of truth for star status is the platform (NSE-Platform). This
service only reflects platform-attested state — it never decides star status
locally and never trusts client-supplied claims.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from nexus_scalp.platform_link.browser import open_default_browser
from nexus_scalp.platform_link.client import (
    PlatformClient,
    PlatformForbiddenError,
    PlatformRateLimitedError,
    PlatformTokenRejectedError,
    PlatformUnavailableError,
)
from nexus_scalp.platform_link.config import PlatformLinkConfig
from nexus_scalp.platform_link.states import (
    VerificationState,
    state_from_platform_payload,
)
from nexus_scalp.platform_link.store import PlatformLinkStore

logger = logging.getLogger(__name__)


@dataclass
class LinkStatus:
    """Everything the UI/CLI needs about the platform link.

    ``verification_url`` is the OFFICIAL GitHub authorization URL (or the
    platform's login entry that immediately redirects there). No codes.
    """

    state: VerificationState
    github_login: str | None = None
    repository: str | None = None
    entitlement_state: str | None = None
    installation_id: str | None = None
    checked_at: float | None = None
    reason: str | None = None
    verification_url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["state"] = self.state.value
        return out

    @classmethod
    def from_cache(cls, payload: dict[str, Any]) -> LinkStatus:
        try:
            state = VerificationState(payload.get("state", VerificationState.NOT_CONNECTED.value))
        except ValueError:
            state = VerificationState.NOT_CONNECTED
        return cls(
            state=state,
            github_login=payload.get("github_login"),
            repository=payload.get("repository"),
            entitlement_state=payload.get("entitlement_state"),
            installation_id=payload.get("installation_id"),
            checked_at=payload.get("checked_at"),
            reason=payload.get("reason"),
        )


class PlatformAuthorizationRequiredError(Exception):
    """Raised when a protected operation is attempted without authorization.

    Carries the current :class:`LinkStatus` so every caller (web API, CLI,
    engine boot) can surface the exact state and remedy without re-querying.
    """

    code = "PLATFORM_VERIFICATION_REQUIRED"

    def __init__(self, status: LinkStatus, message: str | None = None) -> None:
        self.status = status
        self.message = message or _default_denial_message(status.state)
        super().__init__(self.message)


def _default_denial_message(state: VerificationState) -> str:
    return {
        VerificationState.NOT_CONNECTED: (
            "No GitHub account is linked. Connect GitHub to unlock official model downloads."
        ),
        VerificationState.AUTHENTICATION_PENDING: (
            "Finish the GitHub verification in your browser, then retry."
        ),
        VerificationState.VERIFICATION_PENDING: (
            "Star Opselon/NexusTradingForexBot, then run verification again."
        ),
        VerificationState.NOT_STARRED: (
            "Your GitHub account has not starred Opselon/NexusTradingForexBot. "
            "Star the repository, then verify again."
        ),
        VerificationState.VERIFICATION_EXPIRED: (
            "The platform verification expired. Reconnect GitHub to renew it."
        ),
        VerificationState.VERIFICATION_UNAVAILABLE: (
            "The verification platform is unreachable; official downloads are paused. "
            "Try again later — installed models keep working."
        ),
    }.get(state, "Official model downloads require verified GitHub star status.")


class PlatformLinkService:
    """Coordinates the device-connect flow and the authorization gate."""

    def __init__(
        self,
        config: PlatformLinkConfig | None = None,
        store: PlatformLinkStore | None = None,
        client: PlatformClient | None = None,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.config = config or PlatformLinkConfig.from_env()
        self.store = store or PlatformLinkStore()
        self.client = client or PlatformClient(self.config)
        self._now = now_fn

    # ------------------------------------------------------------ installation
    def installation_id(self) -> str:
        return self.store.installation_id()

    # ------------------------------------------------------------------ status
    def status(self) -> LinkStatus:
        """Cached status (no network). Always returns a coherent snapshot."""
        cached = self.store.load_status()
        if cached:
            status = LinkStatus.from_cache(cached)
            status.repository = status.repository or self.config.repository
            status.installation_id = status.installation_id or self.store.installation_id()
            return status
        connected = self.store.has_client_token()
        state = (
            VerificationState.VERIFICATION_PENDING if connected else VerificationState.NOT_CONNECTED
        )
        return LinkStatus(
            state=state,
            repository=self.config.repository,
            installation_id=self.store.installation_id(),
        )

    def _cache(self, status: LinkStatus) -> LinkStatus:
        status.repository = status.repository or self.config.repository
        status.installation_id = status.installation_id or self.store.installation_id()
        status.checked_at = status.checked_at or self._now()
        self.store.save_status(status.as_dict())
        return status

    def publish_status(self, status: LinkStatus) -> LinkStatus:
        """Public cache seam for the lifecycle coordinator (keeps the offline
        grace window honest with live verdicts)."""
        return self._cache(status)

    # ----------------------------------------------------------- connect flow
    def start_connect(self) -> LinkStatus:
        """One-click auth: create the desktop session and return the OFFICIAL
        GitHub authorize URL. The user opens that URL; nothing else."""
        session = self.client.start_desktop_session(self.installation_id())
        try:
            poll_interval = float(session.get("poll_interval") or 0) or None
        except (TypeError, ValueError):
            poll_interval = None
        self.store.save_desktop_session(
            session["session_id"], session["session_secret"], poll_interval=poll_interval
        )
        return self._cache(
            LinkStatus(
                state=VerificationState.AUTHENTICATION_PENDING,
                repository=self.config.repository,
                verification_url=session["authorize_url"],
                reason="Continue with GitHub in your browser.",
            )
        )

    def open_verification_browser(self) -> bool:
        """Open the pending verification URL in the system default browser."""
        cached = self.store.load_status() or {}
        url = self.status().verification_url or cached.get("verification_url")
        if not url:
            return False
        return open_default_browser(url)

    def poll_once(self) -> LinkStatus:
        """Single poll step (web UI drives this; CLI uses await_verification)."""
        session = self.store.load_desktop_session()
        if session is None:
            return self._cache(
                LinkStatus(state=VerificationState.NOT_CONNECTED, reason="no auth session")
            )
        session_id, session_secret = session
        try:
            payload = self.client.poll_desktop_session(session_id, session_secret)
        except PlatformUnavailableError as exc:
            return self._cache(
                LinkStatus(state=VerificationState.AUTHENTICATION_PENDING, reason=str(exc))
            )
        except PlatformRateLimitedError as exc:
            return self._cache(
                LinkStatus(state=VerificationState.AUTHENTICATION_PENDING, reason=str(exc))
            )

        status = str(payload.get("status"))
        if status == "pending":
            return self._cache(
                LinkStatus(
                    state=VerificationState.AUTHENTICATION_PENDING,
                    reason="Waiting for approval in the browser.",
                )
            )
        if status == "expired":
            self.store.clear_desktop_session()
            return self._cache(
                LinkStatus(
                    state=VerificationState.NOT_CONNECTED,
                    reason="The authorization session expired. Start again.",
                )
            )
        if status == "complete":
            token = str(payload.get("client_token") or "")
            if not token:
                return self._cache(
                    LinkStatus(
                        state=VerificationState.VERIFICATION_UNAVAILABLE, reason="missing token"
                    )
                )
            self.store.save_client_token(token)
            try:
                days = int(payload.get("expires_in_days") or 30)
            except (TypeError, ValueError):
                days = 30
            self.store.save_token_meta(self._iso_in_days(days))
            self.store.clear_desktop_session()
            return self.reverify()

        return self._cache(
            LinkStatus(
                state=VerificationState.VERIFICATION_UNAVAILABLE, reason=f"unknown status {status}"
            )
        )

    def await_verification(
        self,
        timeout: float | None = None,
        stop: Callable[[], bool] | None = None,
        on_tick: Callable[[LinkStatus], None] | None = None,
    ) -> LinkStatus:
        """Resilient blocking poll for the CLI.

        Terminal states end the wait: STARRED_VERIFIED, NOT_STARRED (recovery
        is shown to the user), NOT_CONNECTED (session expired), and
        VERIFICATION_EXPIRED. Transient platform failures keep polling with a
        gentle backoff — the user may open the link themselves at any time,
        so we wait as long as the deadline allows.
        """
        deadline = self._now() + (timeout or self.config.await_timeout)
        latest = self.status()
        consecutive_errors = 0
        while self._now() < deadline:
            if stop is not None and stop():
                return latest
            latest = self.poll_once()
            if on_tick is not None:
                on_tick(latest)
            if latest.state is VerificationState.VERIFICATION_UNAVAILABLE:
                # Resilience: transient outage/rate limit — back off, retry.
                consecutive_errors += 1
                time.sleep(min(self.config.poll_interval * consecutive_errors, 15.0))
                continue
            consecutive_errors = 0
            if latest.state not in (
                VerificationState.AUTHENTICATION_PENDING,
                VerificationState.VERIFICATION_PENDING,
            ):
                return latest
            time.sleep(self._effective_poll_interval())
        return latest

    def _effective_poll_interval(self) -> float:
        """Server-honored cadence (§3.1 poll_interval) clamped to sane bounds."""
        try:
            server_value = self.store.load_session_poll_interval()
        except Exception:  # anti-crash: allow
            server_value = None
        if server_value:
            return min(max(server_value, 1.0), 15.0)
        return self.config.poll_interval

    def token_status(self) -> tuple[str, str] | None:
        """(masked token, expires ISO) when a client token exists, else None."""
        token = self.store.client_token()
        if token is None:
            return None
        masked = f"{token[:8]}…{token[-4:]}" if len(token) > 16 else token
        return masked, self.store.load_token_meta() or ""

    def _iso_in_days(self, days: int) -> str:
        import datetime as _dt

        return (_dt.datetime.now(_dt.UTC) + _dt.timedelta(days=days)).isoformat()

    # ------------------------------------------------------------- verify
    def reverify(self) -> LinkStatus:
        """Fresh platform check of identity + star + entitlement (authoritative)."""
        token = self.store.client_token()
        if token is None:
            return self._cache(LinkStatus(state=VerificationState.NOT_CONNECTED))
        try:
            payload = self.client.client_me(token)
        except PlatformTokenRejectedError:
            # Spec §3.3: 401 = token dead — the ONLY recovery is a fresh §3 flow.
            self.store.clear_client_token()
            return self._cache(
                LinkStatus(
                    state=VerificationState.VERIFICATION_EXPIRED,
                    reason="The platform token was rejected. Reconnect to renew it.",
                )
            )
        except PlatformForbiddenError as exc:
            # Spec §5 invariant 4: 403 carries a lease reason — branch on it.
            # The token itself is still valid: never evict it here.
            reason = (exc.reason or "").strip()
            if reason in ("Revoked", "NotStarred"):
                state = VerificationState.NOT_STARRED
            elif reason in ("LeaseExpired", "Pending", ""):
                state = VerificationState.VERIFICATION_PENDING
            else:
                state = VerificationState.VERIFICATION_UNAVAILABLE
            return self._cache(
                LinkStatus(
                    state=state,
                    reason=reason or str(exc),
                    repository=self.config.repository,
                )
            )
        except (PlatformUnavailableError, PlatformRateLimitedError) as exc:
            # Transient failure: do NOT evict the last-known-good cache (the
            # offline grace window depends on it). Report without persisting.
            return LinkStatus(
                state=VerificationState.VERIFICATION_UNAVAILABLE,
                reason=str(exc),
                repository=self.config.repository,
                installation_id=self.store.installation_id(),
            )

        # Model catalog entry (spec §6.1): version + benchmark stats are
        # display-only truth — cached so the UI can show what a star unlocks.
        model = payload.get("model")
        if isinstance(model, dict) and model.get("available"):
            try:
                self.store.save_model_info(model)
            except Exception as exc:  # anti-crash: allow (display-only cache)
                logger.debug("[PLATFORM-LINK] model info cache failed: %s", exc)

        state = state_from_platform_payload(
            payload.get("verification_state"), payload.get("entitlement_state")
        )
        return self._cache(
            LinkStatus(
                state=state,
                github_login=payload.get("github_login"),
                repository=payload.get("repository") or self.config.repository,
                entitlement_state=payload.get("entitlement_state"),
            )
        )

    def model_info(self) -> dict[str, Any]:
        """Cached official-model catalog facts (version + benchmark)."""
        try:
            return self.store.load_model_info() or {}
        except Exception:  # anti-crash: allow (display-only)
            return {}

    def disconnect(self) -> LinkStatus:
        """Drop the local link. (Server-side revocation lives in the dashboard.)"""
        self.store.clear_client_token()
        self.store.clear_desktop_session()
        return self._cache(LinkStatus(state=VerificationState.NOT_CONNECTED, reason="disconnected"))

    # ------------------------------------------------------------ handshake
    #: Handshake.md §3 reason_code vocabulary -> local verification state.
    #: LeaseExpired maps to PENDING (the remedy is "auto-retry the handshake —
    #: one successful recheck restores", NOT a token reconnect).
    _REASON_STATE: ClassVar[dict[str, VerificationState]] = {
        "GrantedFresh": VerificationState.STARRED_VERIFIED,
        "GrantedStale": VerificationState.STARRED_VERIFIED,
        "Revoked": VerificationState.NOT_STARRED,
        "Pending": VerificationState.VERIFICATION_PENDING,
        "LeaseExpired": VerificationState.VERIFICATION_PENDING,
    }

    def handshake(
        self,
        *,
        force: bool = False,
        min_interval_secs: float = 300.0,
        timeout: float | None = None,
    ) -> LinkStatus:
        """Ask the server for the authoritative verdict (Handshake.md §3).

        Cadence: boot + before each delivery + at most every
        ``min_interval_secs`` while running (spec guidance) — the server's
        cooldown collapses extra calls, and this throttle keeps bursts free.
        A network failure NEVER revokes: the cached state is kept untouched
        and only the server's explicit verdict can change entitlement.
        """
        now = self._now()
        if not force:
            last = None
            try:
                last = self.store.load_last_handshake()
            except Exception:  # anti-crash: allow (throttle is best-effort)
                last = None
            if last is not None and (now - last) < min_interval_secs:
                return self.status()  # fresh enough — no call, cached verdict

        token = self.store.client_token()
        if token is None:
            return self._cache(
                LinkStatus(state=VerificationState.NOT_CONNECTED, repository=self.config.repository)
            )
        try:
            payload = self.client.client_verify(token, timeout=timeout)
        except PlatformTokenRejectedError:
            # 401: the token is dead — §3.3 recovery is a fresh connect flow.
            self.store.clear_client_token()
            return self._cache(
                LinkStatus(
                    state=VerificationState.VERIFICATION_EXPIRED,
                    reason="The platform token was rejected. Reconnect to renew it.",
                )
            )
        except PlatformForbiddenError as exc:
            # A 403 on the handshake is a server-side lease answer too.
            reason = (exc.reason or "").strip()
            state = self._REASON_STATE.get(reason, VerificationState.VERIFICATION_PENDING)
            return self._cache(LinkStatus(state=state, reason=reason or str(exc)))
        except (PlatformUnavailableError, PlatformRateLimitedError) as exc:
            # NEVER revoke on a network error — keep the last-known-good cache
            # untouched, and report UNAVAILABLE (NOT the cached state) so the
            # gate's offline-grace logic can engage on the cache explicitly.
            logger.warning("[PLATFORM-LINK] handshake unreachable; cache untouched (%s)", exc)
            return LinkStatus(
                state=VerificationState.VERIFICATION_UNAVAILABLE,
                reason=f"handshake unreachable: {exc}",
                repository=self.config.repository,
            )

        reason_code = str(payload.get("reason_code") or "")
        entitlement = (
            payload.get("entitlement") if isinstance(payload.get("entitlement"), dict) else {}
        )
        state = self._REASON_STATE.get(reason_code, VerificationState.VERIFICATION_PENDING)
        allowed = payload.get("access_allowed") is True
        if state is VerificationState.STARRED_VERIFIED and not allowed:
            # Defensive: a server that says Granted* but access_allowed=false
            # (spec's LeaseExpired example has state VerifiedStarred + denied)
            # must NOT read as verified.
            state = VerificationState.VERIFICATION_PENDING
        try:
            self.store.save_last_handshake(now)
        except Exception:  # anti-crash: allow
            pass
        return self._cache(
            LinkStatus(
                state=state,
                github_login=self.status().github_login,
                repository=self.config.repository,
                entitlement_state=str(entitlement.get("state") or ""),
                checked_at=now,
                reason=reason_code or None,
            )
        )

    # ----------------------------------------------------------------- gate
    def require_download_authorization(self) -> LinkStatus:
        """Fail-closed gate for protected official downloads/updates.

        Live platform check first (authoritative, prevents stale-forever
        caching). If the platform is unreachable, a still-fresh cached
        STARRED_VERIFIED (<= cache_ttl_hours) grants an offline grace window —
        a deliberate, expiring policy; anything else denies.
        """
        token = self.store.client_token()
        if token is None:
            status = self.status()
            if status.state is VerificationState.AUTHENTICATION_PENDING:
                raise PlatformAuthorizationRequiredError(status)
            raise PlatformAuthorizationRequiredError(
                LinkStatus(state=VerificationState.NOT_CONNECTED, repository=self.config.repository)
            )

        fresh = self.handshake(force=True)
        if fresh.state.allows_official_download:
            return fresh
        if fresh.state is VerificationState.VERIFICATION_UNAVAILABLE:
            cached = self.store.load_status() or {}
            previous = LinkStatus.from_cache(cached)
            checked_at = cached.get("checked_at")
            age_hours = (
                (self._now() - float(checked_at)) / 3600.0
                if isinstance(checked_at, (int, float))
                else None
            )
            if (
                previous.state.allows_official_download
                and age_hours is not None
                and age_hours <= self.config.cache_ttl_hours
            ):
                logger.warning(
                    "[PLATFORM-LINK] platform unreachable; offline grace window active "
                    "(age=%.1fh, ttl=%.0fh)",
                    age_hours,
                    self.config.cache_ttl_hours,
                )
                return previous
        raise PlatformAuthorizationRequiredError(fresh)


_service: PlatformLinkService | None = None


def get_platform_link_service() -> PlatformLinkService:
    """Process-wide service (constructed lazily; env-driven config)."""
    global _service  # noqa: PLW0603
    if _service is None:
        _service = PlatformLinkService()
    return _service


def reset_platform_link_service() -> None:
    """Test seam."""
    global _service  # noqa: PLW0603
    _service = None
