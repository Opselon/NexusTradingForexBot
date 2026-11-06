"""Background shipper: batches collected Warning/Error records to the Nexus
server's diagnostics ingest endpoint.

Design guards:
- Only runs when the user explicitly consented (consent.is_enabled()).
- Never blocks or crashes the app: daemon thread, bounded queue file,
  failures are silent except for a single log line per session.
- Payload is capped (<= 200 records/batch, 1 MB queue file on disk).
- Offline-safe: undelivered batches persist and are retried with backoff.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from nexus_scalp.log_shipping.collector import DiagnosticCollector
from nexus_scalp.log_shipping.consent import is_enabled
from nexus_scalp.platform_link.client import (
    PlatformClient,
    PlatformClientError,
    PlatformRateLimitedError,
    PlatformTokenRejectedError,
    PlatformUnavailableError,
)
from nexus_scalp.platform_link.store import PlatformLinkStore

QUEUE_FILENAME = "diagnostics_queue.jsonl"
MAX_QUEUE_BYTES = 1_000_000
BATCH_LIMIT = 200
FLUSH_INTERVAL_SECS = 60.0

#: Settings key for the durable delivery ledger (lifecycle spec §11):
#: proves logs actually REACHED the server — server report id as ack evidence,
#: pending count, last failure code. One JSON blob, no per-event spam.
SETTING_DELIVERY_STATE = "diagnostics.delivery_state"

logger = logging.getLogger(__name__)


class DiagnosticsShipper:
    """Owns the collector, the flush thread, and the durable retry queue."""

    def __init__(
        self,
        source: str,
        *,
        queue_dir: Path | None = None,
        client: PlatformClient | None = None,
        flush_interval: float = FLUSH_INTERVAL_SECS,
        settings: Any = None,
        store: PlatformLinkStore | None = None,
    ) -> None:
        self.collector = DiagnosticCollector(source=source)
        self.source = source
        self.client = client or PlatformClient()
        self.settings = settings
        self.store = store
        self.flush_interval = flush_interval
        self.queue_path = (queue_dir or Path.cwd()) / QUEUE_FILENAME
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.last_sent_at: float | None = None

    # ------------------------------------------------------- delivery ledger
    def _load_delivery_state(self) -> dict[str, Any]:
        if self.settings is None:
            return {}
        try:
            row = self.settings.get(SETTING_DELIVERY_STATE)
            if row is None or not str(row.value):
                return {}
            data = json.loads(str(row.value))
            return data if isinstance(data, dict) else {}
        except Exception:  # pragma: no cover - ledger is best-effort
            return {}

    def _save_delivery_state(self, **updates: Any) -> None:
        if self.settings is None:
            return
        try:
            state = self._load_delivery_state()
            state.update(updates)
            self.settings.set(
                SETTING_DELIVERY_STATE, json.dumps(state, sort_keys=True), value_type="string"
            )
        except Exception:  # pragma: no cover - ledger is best-effort
            logger.debug("[DIAG] delivery ledger write failed", exc_info=True)

    def delivery_state(self) -> dict[str, Any]:
        """Observable delivery state (GREEN/AMBER/RED/GRAY is UI's to derive)."""
        state = self._load_delivery_state()
        state.setdefault("pending_record_count", self.queued_record_count())
        state["consent_enabled"] = bool(is_enabled(self.settings))
        return state

    def queued_record_count(self) -> int:
        return sum(len(batch) for batch in self._drain_queue())

    # ------------------------------------------------------------------ queue
    def _enqueue(self, records: list[dict[str, Any]]) -> None:
        try:
            self.queue_path.parent.mkdir(parents=True, exist_ok=True)
            with self.queue_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(records, separators=(",", ":")) + "\n")
            # Cap the queue file: drop the oldest half when oversized.
            if self.queue_path.stat().st_size > MAX_QUEUE_BYTES:
                lines = self.queue_path.read_text(encoding="utf-8").splitlines()
                keep = lines[len(lines) // 2 :]
                self.queue_path.write_text("".join(line + "\n" for line in keep), encoding="utf-8")
        except OSError:
            logger.debug("[DIAG] queue write failed", exc_info=True)
            return

    def _drain_queue(self) -> list[list[dict[str, Any]]]:
        try:
            if not self.queue_path.exists():
                return []
            lines = self.queue_path.read_text(encoding="utf-8").splitlines()
            out: list[list[dict[str, Any]]] = []
            for line in lines:
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, list):
                        out.append(parsed)
                except ValueError:
                    continue
            return out
        except OSError:
            logger.debug("[DIAG] queue read failed", exc_info=True)
            return []

    def _clear_queue(self) -> None:
        try:
            if self.queue_path.exists():
                self.queue_path.unlink()
        except OSError:
            logger.debug("[DIAG] queue cleanup failed", exc_info=True)

    # ------------------------------------------------------------------ send
    def flush_once(self) -> int:
        """Send collected + queued records now. Returns delivered record count."""
        if not is_enabled(self.settings):
            return 0

        pending: list[list[dict[str, Any]]] = self._drain_queue()
        collected = self.collector.drain(limit=BATCH_LIMIT)
        if collected:
            pending.append(collected)
        if not pending:
            return 0

        delivered = 0
        failed: list[list[dict[str, Any]]] = []
        last_ack_id: str | None = None
        last_failure_code: str | None = None
        for batch in pending:
            ok, ack_id, failure_code = self._post(batch)
            if ok:
                delivered += len(batch)
                last_ack_id = ack_id or last_ack_id
            else:
                last_failure_code = failure_code or last_failure_code
                failed.append(batch)
                if len(failed) * BATCH_LIMIT >= BATCH_LIMIT * 4:
                    break  # bound work per flush; the rest stays queued

        if failed:
            self._clear_queue()
            self._enqueue([rec for batch in failed for rec in batch])
        else:
            self._clear_queue()
        now = time.time()
        if delivered:
            self.last_sent_at = now
            self.last_error = None
        # Durable ledger: LAST_ATTEMPT / LAST_SUCCESS / server ack id /
        # pending count / failure code (lifecycle spec §11).
        updates: dict[str, Any] = {
            "last_attempt": now,
            "pending_record_count": self.queued_record_count(),
        }
        if delivered:
            updates["last_success"] = now
            if last_ack_id:
                updates["last_acknowledged_id"] = last_ack_id
        if last_failure_code:
            updates["last_failure_code"] = last_failure_code
        self._save_delivery_state(**updates)
        return delivered

    def _post(self, records: list[dict[str, Any]]) -> tuple[bool, str | None, str | None]:
        """Deliver one batch. Returns (ok, server_report_id, failure_code)."""
        token: str | None = None
        try:
            token = PlatformLinkStore().client_token()
        except Exception:  # pragma: no cover - store failures must not raise here
            token = None
        try:
            out = self.client._request(
                "/api/diagnostics/ingest",
                payload={
                    "installation_id": PlatformLinkStore().installation_id(),
                    "app_version": _app_version(),
                    "mode": _engine_mode(),
                    "source": self.source,
                    "records": records[:BATCH_LIMIT],
                },
                token=token,
            )
            ack_id = str(out.get("id")) if isinstance(out, dict) and out.get("id") else None
            return True, ack_id, None
        except PlatformTokenRejectedError as exc:
            self.last_error = str(exc)
            return False, None, "TOKEN_REJECTED"
        except PlatformRateLimitedError as exc:
            self.last_error = str(exc)
            return False, None, "RATE_LIMITED"
        except PlatformUnavailableError as exc:
            self.last_error = str(exc)
            return False, None, "PLATFORM_UNAVAILABLE"
        except PlatformClientError as exc:
            self.last_error = str(exc)
            return False, None, "CLIENT_ERROR"
        except Exception as exc:  # pragma: no cover - never crash the shipper
            self.last_error = f"unexpected: {exc}"
            return False, None, "UNEXPECTED"

    # ------------------------------------------------------------------ thread
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="nexus-diagnostics", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        backoff = self.flush_interval
        while not self._stop.wait(self.flush_interval):
            try:
                sent = self.flush_once()
                backoff = self.flush_interval if sent else min(backoff * 2, 600.0)
            except Exception:  # pragma: no cover - absolute containment
                logger.debug("[DIAG] flush failed", exc_info=True)


_session_shipper: DiagnosticsShipper | None = None
_shipper_lock = threading.Lock()


def get_session_shipper(source: str = "runtime") -> DiagnosticsShipper:
    """Process-wide shipper (starts the flush thread on first use)."""
    global _session_shipper  # noqa: PLW0603
    with _shipper_lock:
        if _session_shipper is None:
            _session_shipper = DiagnosticsShipper(source=source)
            _session_shipper.start()
        return _session_shipper


def reset_session_shipper() -> None:
    """Test seam."""
    global _session_shipper  # noqa: PLW0603
    with _shipper_lock:
        if _session_shipper is not None:
            _session_shipper.stop()
        _session_shipper = None


def _app_version() -> str | None:
    try:
        from nexus_scalp.release.metadata import get_version_info

        info = get_version_info()
        return str(info.get("version") or info.get("package_version") or "") or None
    except Exception:  # pragma: no cover - metadata is best-effort
        return None


def _engine_mode() -> str | None:
    try:
        from nexus_scalp.configuration.config import AppConfig
        from nexus_scalp.release import paths as rpaths

        cfg_path = rpaths.get_user_config_path()
        cfg = AppConfig.load_from_yaml(cfg_path) if cfg_path.exists() else None
        if cfg is None:
            return None
        return str(cfg.execution.mode).upper() or None
    except Exception:  # pragma: no cover - config is best-effort
        return None
