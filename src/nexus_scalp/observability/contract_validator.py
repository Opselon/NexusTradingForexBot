"""Observability contract validator + metrics (Agent 2, runtime guardrails).

Two pieces, both OFFLINE and side-effect free:

1. ``ObservabilityContractValidator`` — a reusable, deterministic validator
   that consumes CAPTURED log events (level, event name, message, fields)
   and asserts the frozen contract
   (docs/architecture/observability-log-contract.md):

   A severity      repeated normal state must not escalate to WARNING
   B storm bounds  one signature -> <= STORM_BOUND lines total
                   (1 first-occurrence + 1 summary; the only contract
                   exceptions are singletons and edge-triggered transitions)
   C first         first occurrence is visible immediately
   D singleton     singletons are not delayed behind a flush
   E recovery      a recovery transition is emitted exactly once
   F summary       count/sample_ids(<=5)/first_seen/last_seen/recoverable
   G memory        store bounded at MAX_GROUPS signatures
   H process scope fresh instance re-emits first occurrence (no cross-process dedup)
   I/J redaction   secrets redacted; numeric key=value readable; no credential
                   material in structured payloads

   The validator describes BEHAVIOR, not formatting: it parses the
   contract's key=value fields out of message text but never asserts exact
   wording beyond the semantic keys the contract itself defines.

2. ``AggregatorMetrics`` — lightweight counters surfaced by
   ``EventBatchAggregator.metrics()`` (events_seen / first_occurrences /
   events_aggregated / summaries_flushed / active_signatures /
   dropped_events). Dropped events MUST remain zero for protected evidence;
   the contract tests pin that. These are information-about-the-system only
   and are never read by trading logic.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.observability.event_aggregator import (
    DEFAULT_SAMPLE_IDS,
    MAX_GROUPS,
    EventBatchAggregator,
)

#: Contract storm bound: one signature may produce the first-occurrence line
#: plus ONE summary. Anything beyond 2 lines for one signature is a flood.
STORM_BOUND = 2

#: Contract sample-id cap inside summaries.
SAMPLE_IDS_BOUND = DEFAULT_SAMPLE_IDS

_SEVERITY_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}

_KV_RE = re.compile(r"(\w+)=([^\s]+)")

#: Credential-shaped fragments that must never appear unredacted in payloads.
_SECRET_FRAGMENTS = (
    "password",
    "passwd",
    "secret=",
    "token=",
    "api_key=",
    "apikey=",
    "authorization",
    "bearer ",
    "credential=",
    "bot_token=",
)

_SECRET_SHAPES = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),  # openai-style key
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),  # telegram bot token
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{16,}"),
)


@dataclass
class CapturedEvent:
    """One captured log event, normalized for the validator."""

    level: str  # DEBUG | INFO | WARNING | ERROR | CRITICAL
    event: str  # event name (e.g. ORPHAN_CLASSIFIED_UNKNOWN_BATCH_SUMMARY)
    message: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    signature: tuple[str, ...] = ()  # (event, reason, stage, recoverable)

    def kv(self, key: str) -> str | None:
        """Contract key=value lookup: structured field first, then message."""
        if key in self.fields:
            return str(self.fields[key])
        found = {k: v for k, v in _KV_RE.findall(self.message)}
        return found.get(key)


class ObservabilityContractValidator:
    """Asserts the frozen observability contract over captured events.

    Usage::

        v = ObservabilityContractValidator()
        v.observe_all(events)               # list[CapturedEvent]
        v.assert_storm_bound("LLM_EMPTY")
        v.assert_summary_content("LLM_EMPTY")
        v.assert_severity_no_escalation("HEARTBEAT", max_level="INFO")
        v.assert_recovery_once("PROVIDER_GATE", "RECOVERED", degrade_first=True)
    """

    def __init__(self, events: Iterable[CapturedEvent] | None = None) -> None:
        self.events: list[CapturedEvent] = list(events or [])

    # -- collection ------------------------------------------------------
    def observe(self, event: CapturedEvent) -> None:
        self.events.append(event)

    def observe_all(self, events: Iterable[CapturedEvent]) -> None:
        self.events.extend(events)

    def observe_pairs(self, pairs: Iterable[tuple[str, str, str]]) -> None:
        """Convenience: (level, event, message) triples."""
        for level, event, message in pairs:
            self.events.append(CapturedEvent(level=level, event=event, message=message))

    # -- A. severity -------------------------------------------------------
    def max_severity(self, event_substr: str) -> str:
        levels = [
            e.level for e in self.events if event_substr in e.event or event_substr in e.message
        ]
        return max(levels, key=lambda lv: _SEVERITY_ORDER.get(lv, 0)) if levels else "NONE"

    def assert_severity_no_escalation(self, event_substr: str, max_level: str = "INFO") -> None:
        seen = self.max_severity(event_substr)
        if seen != "NONE" and _SEVERITY_ORDER.get(seen, 99) > _SEVERITY_ORDER.get(max_level, 1):
            raise AssertionError(
                f"SEVERITY_REGRESSION: '{event_substr}' reached {seen}; "
                f"contract allows <= {max_level}"
            )

    def assert_degraded_transitions_allowed(self, event_substr: str) -> None:
        """Degraded/recovery transitions may be WARNING (first failure) or
        INFO (recovery/degraded-steady) but never ERROR for expected noise."""
        for e in self.events:
            if event_substr in e.event or event_substr in e.message:
                if e.level in ("ERROR", "CRITICAL"):
                    raise AssertionError(
                        f"SEVERITY_REGRESSION: expected-noise event '{event_substr}' at {e.level}: "
                        f"{e.message[:120]}"
                    )

    # -- B. storm bounds ----------------------------------------------------
    def count_for(self, signature_substring: str) -> int:
        return sum(1 for e in self.events if signature_substring in e.message)

    def assert_storm_bound(self, signature_substring: str, bound: int = STORM_BOUND) -> None:
        n = self.count_for(signature_substring)
        if n > bound:
            raise AssertionError(
                f"STORM_BOUND_VIOLATION: '{signature_substring}' emitted {n} lines "
                f"(contract bound {bound})"
            )

    def assert_lines_for_events(
        self, n_events: int, n_lines: int, bound: int = STORM_BOUND
    ) -> None:
        """N identical events must never produce more than `bound` lines."""
        if n_events >= 2 and n_lines > bound:
            raise AssertionError(
                f"STORM_BOUND_VIOLATION: {n_events} events produced {n_lines} lines (bound {bound})"
            )

    # -- C. first occurrence --------------------------------------------------
    def assert_first_occurrence_immediate(
        self, signature_substring: str, summary_position: int | None = None
    ) -> None:
        """The first occurrence must be visible immediately: the captured
        event stream must contain an immediate (non-summary) line for the
        signature, not just the batch summary."""
        non_summary = [
            e
            for e in self.events
            if signature_substring in e.message and "BATCH_SUMMARY" not in e.message
        ]
        if not non_summary:
            raise AssertionError(
                f"FIRST_OCCURRENCE_DELAYED: no immediate event line before the "
                f"summary for '{signature_substring}'"
            )
        if summary_position is not None and summary_position == 0:
            raise AssertionError(
                "FIRST_OCCURRENCE_DELAYED: summary at position 0 with event lines after it"
            )

    # -- D. singleton --------------------------------------------------------
    def assert_singletons_immediate(self, signature_substring: str) -> None:
        """A single occurrence must already be visible without any flush:
        at least one non-summary line exists."""
        non_summary = [
            e
            for e in self.events
            if signature_substring in e.message and "BATCH_SUMMARY" not in e.message
        ]
        if not non_summary:
            raise AssertionError(
                f"SINGLETON_DELAYED: '{signature_substring}' only present via summary"
            )

    # -- E. recovery -----------------------------------------------------------
    def assert_recovery_once(self, source: str, recovery_event: str) -> int:
        recs = [
            e
            for e in self.events
            if source in e.event or source in e.message
            if recovery_event in e.event or recovery_event in e.message
        ]
        if len(recs) > 1:
            raise AssertionError(
                f"RECOVERY_SPAM: {recovery_event} emitted {len(recs)} times (contract: exactly 1)"
            )
        return len(recs)

    # -- F. summary content ------------------------------------------------------
    def assert_summary_content(self, summary_message: str, *, min_count: int = 2) -> None:
        required = ("count=", "sample_ids=[", "first_seen=", "last_seen=")
        missing = [k for k in required if k not in summary_message]
        if missing:
            raise AssertionError(
                f"SUMMARY_CONTENT_LOSS: missing {missing} in {summary_message[:140]}"
            )
        count_m = re.search(r"count=(\d+)", summary_message)
        if count_m and int(count_m.group(1)) < min_count:
            raise AssertionError(
                f"SUMMARY_SCOPE_ERROR: summary for count={count_m.group(1)} "
                f"(< {min_count}) should have stayed a singleton line"
            )
        ids_m = re.search(r"sample_ids=\[([^\]]*)\]", summary_message)
        if ids_m and ids_m.group(1).strip() not in ("", "-"):
            n_ids = len(ids_m.group(1).split(","))
            if n_ids > SAMPLE_IDS_BOUND:
                raise AssertionError(f"SAMPLE_IDS_UNBOUNDED: {n_ids} ids > {SAMPLE_IDS_BOUND}")
        if "recoverable=" not in summary_message:
            raise AssertionError(
                f"RECOVERABILITY_LOSS: no recoverable= flag in {summary_message[:140]}"
            )

    # -- G. memory bound -------------------------------------------------------
    @staticmethod
    def assert_memory_bound(agg: EventBatchAggregator, *, allow_dropped: bool = False) -> None:
        n = len(agg._groups)
        if n > MAX_GROUPS:
            raise AssertionError(
                f"MEMORY_BOUND_VIOLATION: aggregator holds {n} signatures > {MAX_GROUPS}"
            )
        m = agg.metrics()
        # dropped_events must remain 0 whenever the workload stays within the
        # signature bound (single- or few-signature protected paths). A
        # multi-signature overflow beyond max_groups is an eviction event the
        # caller explicitly opted into via allow_dropped=True.
        if m["dropped_events"] != 0 and not allow_dropped:
            raise AssertionError("EVIDENCE_LOSS: dropped_events != 0 without allow_dropped")

    # -- H. process scope ---------------------------------------------------------
    @staticmethod
    def assert_process_local(first: bool, second_instance_first: bool) -> None:
        if not first or not second_instance_first:
            raise AssertionError(
                "PROCESS_SCOPE_REGRESSION: aggregator must be process-local "
                "(a fresh instance re-emits its first occurrence)"
            )

    # -- I/J. redaction + payload safety -----------------------------------------
    @staticmethod
    def assert_secrets_redacted(payload: str) -> None:
        low = payload.lower()
        for frag in _SECRET_FRAGMENTS:
            if frag in low:
                # allowed only when followed by an explicit redaction marker
                tail = payload[low.index(frag) :][:80]
                if "[REDACTED" not in tail.upper() and "token_present=" not in frag:
                    raise AssertionError(
                        f"SECRET_LEAK: credential fragment '{frag}' unredacted: {tail!r}"
                    )
        for shape in _SECRET_SHAPES:
            if shape.search(payload):
                raise AssertionError(
                    f"SECRET_LEAK: credential-shaped value in payload: {shape.pattern}"
                )

    @staticmethod
    def assert_numeric_readable(payload: str) -> None:
        """Numeric key=value pairs must survive redaction (contract I)."""
        for token in re.findall(r"\b\w+=-?\d[\d.,%]*", payload):
            if "[REDACTED" in token:
                raise AssertionError(f"FALSE_REDACTION: numeric pair redacted: {token}")
        # direct spot check on typical audit counters
        sample = "consistency_violations=1 orphans=3755 duplicates=3 count=243 failures=67"
        from nexus_scalp.observability.logging import _redact_value

        if "[REDACTED" in _redact_value(sample):
            raise AssertionError("FALSE_REDACTION: numeric audit counters redacted")

    # -- aggregated helper ---------------------------------------------------------
    def run_all_checks(
        self,
        *,
        storm_signatures: dict[str, int] | None = None,
        summaries: list[str] | None = None,
        payloads: list[str] | None = None,
    ) -> dict[str, str]:
        """Runs the whole battery; returns check-name -> PASS/FAIL map."""
        report: dict[str, str] = {}

        def _run(name: str, fn: Callable[[], Any]) -> None:
            try:
                fn()
                report[name] = "PASS"
            except AssertionError as exc:
                report[name] = f"FAIL: {exc}"

        for sig, bound in (storm_signatures or {}).items():
            _run(
                f"storm_bound[{sig}]",
                self._bind2(self.assert_storm_bound, sig, bound),
            )
        for s in summaries or []:
            _run("summary_content", self._bind1(self.assert_summary_content, s))
        for p in payloads or []:
            _run("secret_safety", self._bind1(self.assert_secrets_redacted, p))
            _run("numeric_readable", self._bind1(self.assert_numeric_readable, p))
        return report

    # The bound-method factories below replace `lambda x=x: self.f(x)` so mypy
    # can infer the callable type (bare lambdas with default-arg binding are
    # not inferrable under [misc] rules).
    @staticmethod
    def _bind1(fn: Callable[[Any], Any], arg: Any) -> Callable[[], Any]:
        def call() -> Any:
            return fn(arg)

        return call

    @staticmethod
    def _bind2(fn: Callable[[Any, Any], Any], a: Any, b: Any) -> Callable[[], Any]:
        def call() -> Any:
            return fn(a, b)

        return call


def is_bounded_summary_count(n_events: int, n_lines: int) -> bool:
    """Pure helper for property tests: contract bound check."""
    if n_events <= 1:
        return n_lines <= 1
    return n_lines <= STORM_BOUND


def expected_summary_count(n_events: int) -> int:
    """Deterministic expectation: 0 summaries for singletons, 1 otherwise."""
    return 0 if n_events <= 1 else 1


def sample_ids_in(summary_message: str) -> list[str]:
    m = re.search(r"sample_ids=\[([^\]]*)\]", summary_message)
    if not m or m.group(1).strip() in ("", "-"):
        return []
    return m.group(1).split(",")


def count_in(summary_message: str) -> int:
    m = re.search(r"count=(\d+)", summary_message)
    return int(m.group(1)) if m else -1


def approx_storm_ratio(n_events: int, n_lines: int) -> float:
    """Information density: lines per 1000 events (bounded contract => -> 0)."""
    if n_events == 0:
        return 0.0
    return (n_lines / n_events) * 1000.0


def finite_and_small(x: float) -> bool:
    return math.isfinite(x) and x >= 0
