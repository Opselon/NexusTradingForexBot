"""Telegram operational control surface (mission P0/P1 item 5).

Design (INV-010 compliant — Telegram stays OUT of the execution path):

    Telegram getUpdates poll → TelegramCommandBus → authenticated INTENT →
        LiveEngine.apply_command_intent() → EXISTING authority layers.

    - The bus NEVER touches the broker adapter, never imports MT5, never
      mutates execution state itself. An intent is a REQUEST the engine's
      own gates still evaluate (kill switch through RiskEngine, rollback
      through ModelGovernanceEngine.rollback with its load-gate checks).
    - Authentication: a chat is authorized iff its chat id equals the
      configured admin_id (the same identity the notifier already sends
      TO). Commands from any other chat are rejected + counted. /halt
      additionally requires the token echoed in the message body so a
      leaked screenshot of the chat cannot replay the emergency verb.
    - Idempotency: each update_id is processed exactly once (Telegram
      offset semantics + a bounded processed-set).
    - Auditability: every accepted/rejected command is returned and logged
      with actor/chat/command/dry-run flag; the ENGINE records the
      authoritative outcome through its existing event surfaces (kill
      switch log, governance event ledger).

Implemented commands (minimum viable control surface):
    /status          — one compact operator snapshot (account, mode, risk
                       gates, breaker state, drift state, champion)
    /halt <token>    — arms the RiskEngine kill switch (blocks ALL new
                       dispatch at the risk layer; open positions stay
                       protectively managed — same semantics as the
                       dashboard kill switch)
    /resume <token>  — disarms the kill switch (operator-authorized)
    /rollback        — rolls the champion back to the previous artifact
                       via governance_engine.rollback (engine validates;
                       bus only forwards the intent)

Polling is a BACKGROUND LOOP (default 2.5s) with full exception isolation:
any Telegram fault is logged + counted, never propagated.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.observability.tg_command_bus")

#: Commands that require the confirmation token in the message text.
_TOKEN_COMMANDS = frozenset({"halt", "resume"})

_COMMAND_HELP = (
    "🎮 <b>NEXUS OPERATOR COMMANDS</b>\n"
    "/status — engine snapshot\n"
    "/halt <token> — HALT new entries (kill switch)\n"
    "/resume <token> — lift the halt\n"
    "/rollback — rollback champion to previous artifact"
)


class CommandTarget(Protocol):
    """Structural view of the engine surface the bus may call."""

    def apply_command_intent(self, intent: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class TelegramCommand:
    """One authenticated operator intent (data, never authority)."""

    command: str
    args: list[str] = field(default_factory=list)
    chat_id: str = ""
    username: str = ""
    update_id: int = 0
    raw_text: str = ""


class TelegramCommandBus:
    """Authenticated inbound Telegram command surface (background poll)."""

    def __init__(
        self,
        *,
        bot_token: str,
        admin_id: str,
        target: CommandTarget | None,
        halt_token: str = "",
        poll_interval_sec: float = 2.5,
        api_base: str = "https://api.telegram.org",
    ) -> None:
        self.bot_token = bot_token or ""
        self.admin_id = str(admin_id or "")
        self.target = target
        self.halt_token = str(halt_token or "")
        self.poll_interval_sec = max(1.0, float(poll_interval_sec))
        self.api_base = api_base.rstrip("/")
        self.enabled = bool(self.bot_token and self.admin_id and target is not None)

        # Telemetry (digest consumers)
        self.received: int = 0
        self.rejected_auth: int = 0
        self.rejected_token: int = 0
        self.rejected_unknown: int = 0
        self.accepted: int = 0
        self.last_command: str = ""
        self.last_result: dict[str, Any] | None = None
        self.last_poll_error: str = ""

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset: int = 0
        self._processed: set[int] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tg-command-bus", daemon=True)
        self._thread.start()
        logger.info("[TG_CMD] event=BUS_START admin_configured=%s", bool(self.admin_id))

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def status(self) -> dict[str, Any]:
        return {
            "available": self.enabled,
            "received": self.received,
            "accepted": self.accepted,
            "rejected_auth": self.rejected_auth,
            "rejected_token": self.rejected_token,
            "rejected_unknown": self.rejected_unknown,
            "last_command": self.last_command,
            "last_result": self.last_result,
            "last_poll_error": self.last_poll_error,
        }

    # ------------------------------------------------------------------
    # Background poll loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                self.last_poll_error = f"{type(exc).__name__}: {exc}"[:160]
                logger.warning("[TG_CMD] poll failed (isolated)", error=self.last_poll_error)
            self._stop.wait(self.poll_interval_sec)

    def _poll_once(self) -> None:
        import urllib.request

        url = (
            f"{self.api_base}/bot{self.bot_token}/getUpdates"
            f"?timeout=1&offset={self._offset}&allowed_updates=%5B%22message%22%5D"
        )
        with urllib.request.urlopen(url, timeout=self.poll_interval_sec + 5.0) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        if not body.get("ok") or not isinstance(body.get("result"), list):
            return
        for update in body["result"]:
            update_id = int(update.get("update_id", 0))
            if update_id <= self._offset or update_id in self._processed:
                continue  # idempotency: exactly-once per update_id
            self._offset = update_id
            self._processed.add(update_id)
            if len(self._processed) > 500:
                self._processed = set(sorted(self._processed)[-200:])
            msg = update.get("message") or {}
            text = str(msg.get("text", "") or "")
            chat = str((msg.get("chat") or {}).get("id", "") or "")
            username = str((msg.get("from") or {}).get("username", "") or "")
            if text.startswith("/"):
                self._handle(chat, username, update_id, text)

    # ------------------------------------------------------------------
    # Authentication + dispatch
    # ------------------------------------------------------------------

    def _handle(self, chat_id: str, username: str, update_id: int, text: str) -> None:
        self.received += 1
        # AUTH: admin chat only (the same id notifications are sent to).
        if not self.admin_id or chat_id != self.admin_id:
            self.rejected_auth += 1
            logger.warning("[TG_CMD] event=UNAUTHORIZED_CHAT chat=%s user=%s", chat_id, username)
            self._reply(chat_id, "⛔ Unauthorized chat.")
            return

        parts = text.strip().split()
        command = parts[0][1:].split("@")[0].lower()  # /halt@botname -> halt
        args = parts[1:]

        if command in ("help", "start"):
            self.accepted += 1
            self._reply(chat_id, _COMMAND_HELP)
            return

        if command in ("halt", "resume"):
            if not self.halt_token or not args or args[0] != self.halt_token:
                self.rejected_token += 1
                logger.warning(
                    "[TG_CMD] event=COMMAND_TOKEN_REJECTED command=%s chat=%s",
                    command,
                    chat_id,
                )
                self._reply(
                    chat_id,
                    f"⛔ {command.upper()} requires the confirmation token: "
                    f"/{command} &lt;token&gt;",
                )
                return

        if command not in ("status", "halt", "resume", "rollback"):
            self.rejected_unknown += 1
            self._reply(chat_id, "Unknown command.\n\n" + _COMMAND_HELP)
            return

        intent = {
            "command": command,
            "args": args,
            "chat_id": chat_id,
            "username": username,
            "update_id": update_id,
            "raw_text": text,
            "source": "TELEGRAM",
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.last_command = command
        result: dict[str, Any] = {"ok": False, "message": "no engine target"}
        if self.target is not None:
            try:
                result = self.target.apply_command_intent(intent)
            except Exception as exc:
                result = {"ok": False, "message": f"engine error: {type(exc).__name__}"}
        self.accepted += 1
        self.last_result = result
        logger.info(
            "[TG_CMD] event=COMMAND_ACCEPTED command=%s chat=%s ok=%s",
            command,
            chat_id,
            result.get("ok"),
        )
        self._reply(chat_id, str(result.get("message", "done")))

    def _reply(self, chat_id: str, text: str) -> None:
        """Direct synchronous reply (bounded, isolated; best-effort)."""
        if not self.bot_token:
            return
        try:
            import urllib.parse
            import urllib.request

            data = urllib.parse.urlencode(
                {"chat_id": chat_id, "text": text[:3500], "parse_mode": "HTML"}
            ).encode()
            url = f"{self.api_base}/bot{self.bot_token}/sendMessage"
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=6.0):
                pass
        except Exception as exc:
            logger.warning("[TG_CMD] reply failed (isolated)", error=str(exc))
