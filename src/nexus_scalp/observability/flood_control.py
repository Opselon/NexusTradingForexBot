"""Repeated-line flood suppression for the logging pipeline (OBS-013).

The 2026-08-31 audit measured the failure mode precisely: **15,289 identical
STRATEGY_FACTORY "provider network failure" lines and 20,373 identical
DATASET_REJECTED lines per day**, plus ASCII panel borders landing in the error
stream. Identical lines are not information — they push the one line that
matters out of the rotation window and make a soak report unreadable (60+
identical risk-rejection warnings in two minutes).

WHY A logging.Filter AND NOT A STRUCTLOG PROCESSOR
--------------------------------------------------
structlog's ``DropEvent`` only stops records that originate inside structlog.
``ProcessorFormatter`` runs ``foreign_pre_chain`` for stdlib records and
IGNORES a ``DropEvent`` raised there — verified against structlog 25.x on this
repo, where a stdlib record still rendered. Since this service logs from both
structlog and plain ``logging.getLogger`` (adapters, workers, asyncio), a
processor-level suppressor covers only half the surface. A ``logging.Filter``
attached to the handlers sits ABOVE both paths: by the time a record reaches a
handler it is a LogRecord regardless of origin.

DESIGN CONSTRAINTS (priority order)
-----------------------------------
1. **Never lose the signal.** The first ``flood_threshold`` occurrences always
   pass. Repeats beyond that are counted, and when the burst ends (or the
   window elapses) ONE summary record reports exactly how many were dropped.
   Silence is never the outcome.
2. **CRITICAL/FATAL are never suppressed.** An operator must see every one.
3. **Per-signature.** Suppressing one noisy component must never swallow a
   different component's first error.
4. **Cheap.** One dict lookup + a scalar compare per record; the signature is
   a bounded string, not a hash.
"""

from __future__ import annotations

import logging
import threading
import time

#: Repeats of one signature inside this window are collapsed.
DEFAULT_WINDOW_SEC = 60.0

#: First N identical records always pass, so the line is never invisible and
#: ordinary occasional repetition (a retry, a transient blip) is untouched.
DEFAULT_FLOOD_THRESHOLD = 5

#: Severities that are ALWAYS emitted, never suppressed.
NEVER_SUPPRESS = frozenset({"CRITICAL", "FATAL"})

#: Hard cap on tracked signatures so an event-name explosion cannot grow the
#: bookkeeping dict without bound.
MAX_TRACKED_SIGNATURES = 512


class FloodControlFilter(logging.Filter):
    """Drop repeats of an identical record, then report what was dropped.

    ``filter()`` returning False discards the record. Attach the SAME instance
    to every handler of one logging tree so the counts stay coherent.
    """

    def __init__(
        self,
        *,
        window_sec: float = DEFAULT_WINDOW_SEC,
        flood_threshold: int = DEFAULT_FLOOD_THRESHOLD,
        max_signatures: int = MAX_TRACKED_SIGNATURES,
    ) -> None:
        super().__init__(name="nse.flood_control")
        self.window_sec = window_sec
        self.flood_threshold = max(1, flood_threshold)
        self.max_signatures = max_signatures
        self._lock = threading.Lock()
        #: signature -> [window_start_monotonic, passed, suppressed]
        self._state: dict[str, list[float]] = {}

    # -- signature -------------------------------------------------------

    @staticmethod
    def signature(record: logging.LogRecord) -> str:
        """Identify a repeat: same level + same rendered event.

        Deliberately readable (not a hash) so it can be inspected in a
        debugger, and truncated because two records sharing a long prefix are,
        for flood purposes, the same line.

        STRUCTLOG NOTE: a structlog record carries its event dict as
        ``record.msg`` (only the final renderer turns it into a string).
        ``record.getMessage()`` therefore returns ``str(dict)`` which embeds
        the per-record ``timestamp`` - every line would hash to a unique
        signature and nothing would ever collapse. For a dict payload the
        stable identity is the ``event`` FIELD (the message), which is
        exactly what the user-visible line will be.
        """
        payload = record.msg
        if isinstance(payload, dict):
            msg = str(payload.get("event", ""))
        elif getattr(record, "flood_annotated", False):
            # A stdlib record already annotated: `msg` is the rendered
            # annotated text. The signature must stay the ORIGINAL
            # message, so strip our own annotation back off. Without this
            # the annotated record becomes a new signature and the flood
            # escapes collapse.
            msg = str(record.msg).split(" [repeated")[0].split(" [flood-suppressed")[0]
        else:
            try:
                msg = record.getMessage()
            except Exception:  # a broken %-format must never break logging
                msg = str(record.msg)
        return f"{record.levelname}|{msg[:200]}"

    # -- filter ----------------------------------------------------------

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelname in NEVER_SUPPRESS:
            return True

        sig = self.signature(record)
        now = time.monotonic()

        with self._lock:
            state = self._state.get(sig)

            if state is None:
                if len(self._state) >= self.max_signatures:
                    self._state.clear()
                self._state[sig] = [now, 1, 0]
                return True

            window_start, passed, suppressed = state

            if now - window_start > self.window_sec:
                # Window rolled over. Annotate this record as the first of a
                # fresh window and report what the previous window hid.
                self._state[sig] = [now, 1, 0]
                if suppressed:
                    self._annotate(
                        record,
                        suppressed=int(suppressed),
                        window_sec=now - window_start,
                    )
                return True

            if passed < self.flood_threshold:
                state[1] = passed + 1
                if passed + 1 == self.flood_threshold:
                    # Tell the operator that repetition is now being collapsed,
                    # so a quiet period is never mistaken for no occurrences.
                    self._annotate(record, repeat=int(passed) + 1, threshold=True)
                return True

            state[2] = suppressed + 1
            return False

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _annotate(
        record: logging.LogRecord,
        *,
        suppressed: int | None = None,
        repeat: int | None = None,
        threshold: bool = False,
        window_sec: float | None = None,
    ) -> None:
        """Attach suppression metadata to the record's message.

        A structlog record keeps its event dict in ``record.msg``; replacing
        that with a string would break ProcessorFormatter downstream,
        which still expects the mapping. Annotate the ``event`` FIELD in
        that case so rendering, redaction and DB persistence all keep
        working.
        """
        if threshold and repeat is not None:
            suffix = (
                f" [repeated {repeat}x within "
                f"{int(DEFAULT_WINDOW_SEC)}s; further repeats collapsed]"
            )
        elif suppressed is not None:
            secs = f"{int(window_sec)}s" if window_sec is not None else "window"
            suffix = f" [flood-suppressed {suppressed} identical line(s) over {secs}]"
        else:
            return

        payload = record.msg
        if isinstance(payload, dict):
            # Signature stability: the signature is computed from `event`,
            # so annotating `event` in place would give the NEXT occurrence
            # a different signature and the flood would escape suppression
            # (observed live: annotated lines 5..6 passed as a fresh flood).
            # The note rides alongside `event` as its own field.
            payload["flood_note"] = suffix.strip()
            payload.setdefault("suppressed_count", suppressed or 0)
        else:
            try:
                text = record.getMessage()
            except Exception:
                text = str(record.msg)
            record.msg = text + suffix
            record.args = ()
            record.flood_annotated = True
        record.flood_suppressed = suppressed or 0

    # -- introspection ---------------------------------------------------

    def stats(self) -> dict[str, object]:
        """Current tracked signatures and suppression totals (diagnostics)."""
        with self._lock:
            return {
                "tracked_signatures": len(self._state),
                "suppressed_total": sum(int(s) for _, _, s in self._state.values()),
                "window_sec": self.window_sec,
                "flood_threshold": self.flood_threshold,
            }


__all__ = [
    "DEFAULT_FLOOD_THRESHOLD",
    "DEFAULT_WINDOW_SEC",
    "MAX_TRACKED_SIGNATURES",
    "NEVER_SUPPRESS",
    "FloodControlFilter",
]
