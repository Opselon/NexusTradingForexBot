"""Observability self-test — offline diagnostic for the doctor surface.

`run_observability_selftest()` executes the ENTIRE frozen log contract
against SYNTHETIC IN-MEMORY events and returns a verdict payload:

    {
      "overall": "PASS" | "FAIL",
      "checks": {"contract": PASS, "redaction": PASS, "aggregation_bound": PASS,
                 "singleton": PASS, "recovery": PASS, "process_scope": PASS,
                 "summary_content": PASS, "evidence_preservation": PASS},
      "metrics": {...aggregator counters...},
      "failures": [...],
    }

HARD SAFETY CONTRACT of this self-test:
  * no network / providers / MT5 / Telegram / DB / disk writes
  * no production state mutation (fresh in-memory objects only)
  * deterministic; safe to call from the doctor CLI or any diagnostic route

Wired through the existing diagnostics architecture as a `HealthEngine`
check category ("OBSERVABILITY") so `nexus doctor` gains the surface without
a parallel CLI (guardrail task §6).
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.observability.contract_validator import (
    CapturedEvent,
    ObservabilityContractValidator,
)
from nexus_scalp.observability.event_aggregator import EventBatchAggregator


def _synthetic_storm(n: int, *, event: str = "LLM_EMPTY") -> tuple[EventBatchAggregator, list[str]]:
    """Deterministic in-memory storm; mirrors the PRO_AUTO producer shape."""
    agg = EventBatchAggregator(sample_ids=5)
    lines: list[str] = []
    for i in range(n):
        if agg.add(
            event=event, reason="", stage="pro_auto", recoverable=True, trade_id=f"news_{i:05d}"
        ):
            lines.append(f"[PRO_AUTO] {event} article_id=news_{i:05d}")
    agg.flush(lines.append, only_repeats=True)
    return agg, lines


_SAFE_TEXT_RE: Any = None
_WS_RE: Any = None


def _compile_safe_text_res() -> tuple[Any, Any]:
    """Lazy compile of the safe-text allow-list regexes (no import-time cost)."""
    import re as _re

    return (
        _re.compile(r"[^A-Za-z0-9 =_.,:\-\[\]\(\)%]"),
        _re.compile(r"\s+"),
    )


def _safe_assert_text(exc: BaseException) -> str:
    """Sanitized AssertionError text for the failures payload.

    CodeQL py/stack-trace-exposure: ``str(exc)`` is a tainted value (the
    exception could carry file/SQL/trace fragments), so it must never be
    echoed even after a character filter — the taint flows through the
    filter for the analyzer.  Instead rebuild the message from the
    exception's *parts*: the synthetic harness raises AssertionError with
    static format strings, so ``exc.args`` contain only literal chunks
    and small ints.  Each arg is passed through a strict allow-list
    (words/digits/punct) as an INDEPENDENT constant-sized fragment; args
    of any other type are replaced by their type name.  The taint never
    enters the output channel.
    """
    global _SAFE_TEXT_RE, _WS_RE
    if _SAFE_TEXT_RE is None or _WS_RE is None:
        _SAFE_TEXT_RE, _WS_RE = _compile_safe_text_res()
    parts: list[str] = []
    for arg in exc.args or ():
        if isinstance(arg, str):
            cleaned = _SAFE_TEXT_RE.sub(" ", arg)
            parts.append(_WS_RE.sub(" ", cleaned).strip()[:80])
        elif isinstance(arg, int) and not isinstance(arg, bool):
            parts.append(str(abs(arg)))
        else:
            parts.append(type(arg).__name__)
    text = " ".join(p for p in parts if p)
    return (text or "assertion failed")[:200]


def run_observability_selftest() -> dict[str, Any]:
    """Runs all offline contract checks on synthetic data. Never raises."""
    failures: list[str] = []
    checks: dict[str, str] = {}
    global _SAFE_TEXT_RE, _WS_RE
    if _SAFE_TEXT_RE is None or _WS_RE is None:
        _SAFE_TEXT_RE, _WS_RE = _compile_safe_text_res()

    def _check(name: str, fn) -> None:  # type: ignore[no-untyped-def]
        try:
            fn()
            checks[name] = "PASS"
        except AssertionError as exc:
            checks[name] = "FAIL"
            # AssertionError text is synthetic harness diagnostics ("storm
            # produced 45 lines") — safe to surface, needed by the guardrail
            # tests; not a file/SQL path leak. Sanitized through a
            # conservative character filter so no tainted value flows raw.
            failures.append(f"{name}: {_safe_assert_text(exc)}")
        except Exception as exc:  # absolute isolation
            checks[name] = "FAIL"
            failures.append(f"{name}: unexpected {type(exc).__name__}")

    # 1) contract: storm bound + first occurrence + summary content (45 = the
    #    live-flood scale)
    def _contract() -> None:
        _agg, lines = _synthetic_storm(45)
        v = ObservabilityContractValidator()
        for ln in lines:
            level = "WARNING" if "BATCH_SUMMARY" not in ln else "INFO"
            v.observe(CapturedEvent(level=level, event="LLM_EMPTY", message=ln))
        if len(lines) > 2:
            raise AssertionError(f"storm produced {len(lines)} lines")
        summary = [ln for ln in lines if "BATCH_SUMMARY" in ln]
        if not summary:
            raise AssertionError("no summary emitted")
        v.assert_summary_content(summary[-1], min_count=2)

    _check("contract", _contract)

    # 2) redaction: secrets redacted, numeric pairs readable
    def _redaction() -> None:
        from nexus_scalp.observability.logging import _redact_value

        if "[REDACTED" not in _redact_value("bot_token=123456:ABCDEFghijkl"):
            raise AssertionError("bot token not redacted")
        if "[REDACTED" not in _redact_value("password=hunter2secret"):
            raise AssertionError("password not redacted")
        sample = "consistency_violations=1 orphans=3755 duplicates=3 failures=67"
        if "[REDACTED" in _redact_value(sample):
            raise AssertionError("numeric audit counters falsely redacted")

    _check("redaction", _redaction)

    # 3) aggregation bound: 10k events stay bounded + count accurate
    def _aggregation_bound() -> None:
        agg, lines = _synthetic_storm(10_000, event="STORM")
        if len(lines) > 2:
            raise AssertionError(f"10k storm produced {len(lines)} lines")
        if "count=10000" not in (lines[-1] if lines else ""):
            raise AssertionError("count not accurate after 10k events")
        if agg.metrics()["dropped_events"] != 0:
            raise AssertionError("evidence dropped in single-signature storm")

    _check("aggregation_bound", _aggregation_bound)

    # 4) singleton: 1 event -> immediate visibility, no summary needed
    def _singleton() -> None:
        _agg, lines = _synthetic_storm(1)
        if len(lines) != 1 or "BATCH_SUMMARY" in lines[0]:
            raise AssertionError("singleton not immediate / wrongly summarized")

    _check("singleton", _singleton)

    # 5) recovery: exactly one recovery event for a degraded->recovered cycle
    def _recovery() -> None:
        v = ObservabilityContractValidator()
        v.observe_pairs(
            [
                ("WARNING", "NEWS_FETCH_FAILURE", "source=bls status=FAILURE failures=1"),
                ("INFO", "NEWS_FETCH_RECOVERED", "source=bls status=RECOVERED"),
            ]
        )
        n = v.assert_recovery_once("NEWS_FETCH", "RECOVERED")
        if n != 1:
            raise AssertionError(f"expected exactly 1 RECOVERED, saw {n}")

    _check("recovery", _recovery)

    # 6) process scope: fresh aggregator re-emits first occurrence
    def _process_scope() -> None:
        a1 = EventBatchAggregator()
        a2 = EventBatchAggregator()
        f1 = a1.add(event="E", reason="R", stage="s", recoverable=False, trade_id="x")
        f2 = a2.add(event="E", reason="R", stage="s", recoverable=False, trade_id="x")
        if not (f1 and f2):
            raise AssertionError("cross-process dedup assumed (fresh instance suppressed)")

    _check("process_scope", _process_scope)

    # 7) evidence preservation: memory bound never evicts unflushed evidence
    #    in single-signature workloads; multi-signature eviction is counted.
    def _evidence() -> None:
        agg = EventBatchAggregator(max_groups=4)
        for i in range(8):
            agg.add(event=f"E{i}", reason="R", stage="s", recoverable=False, trade_id=f"t{i}")
        m = agg.metrics()
        if m["active_signatures"] > 4:
            raise AssertionError("store exceeded max_groups")
        if m["dropped_events"] == 0:
            raise AssertionError("eviction not accounted (metrics blind spot)")

    _check("evidence_preservation", _evidence)

    # 8) summary content invariants on a mixed storm
    def _summary_content() -> None:
        agg = EventBatchAggregator(sample_ids=5)
        lines: list[str] = []
        for i in range(30):
            agg.add(
                event="DATASET_REJECTED",
                reason="MISSING_REALIZED_R",
                stage="dataset",
                recoverable=True,
                trade_id=f"exp_{i:04d}",
            )
        agg.flush(lines.append, only_repeats=True)
        s = lines[0] if lines else ""
        for key in ("count=30", "recoverable=true", "sample_ids=[", "first_seen=", "last_seen="):
            if key not in s:
                raise AssertionError(f"summary lost {key}: {s[:120]}")

    _check("summary_content", _summary_content)

    agg_final, _ = _synthetic_storm(10)
    overall = "PASS" if not failures else "FAIL"
    return {
        "overall": overall,
        "checks": checks,
        "metrics": agg_final.metrics(),
        "failures": failures,
    }
