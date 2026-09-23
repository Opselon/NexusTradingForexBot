"""Async decision-trace observer — bounded, non-blocking, failure-isolated.

Hot-path contract (INV-001 companion, §6/§7 of the trace spec):
- ``emit``/``begin_trace`` cost, while the observer is OFF, is one status
  check returning immediately — no serialization, no allocation, no I/O.
- While ACTIVE, every operation is in-memory, O(1)-ish, lock-bounded and
  wrapped so that NO observer failure can ever propagate to the caller
  (BUG-311 audit rule: the guarded import at each call site + a body that
  cannot raise).
- The trading engine never waits for a subscriber, a browser, or a DB:
  subscribers are bounded buffers with a terminal-preserving drop policy
  and explicit drop counters (``dropped_visual_events`` never silently
  hides; terminal/rejection/execution events are never dropped first).

The observer is PASSIVE: it records what the runtime emitted and nothing
else. Topology, causality and latency aggregates are derived exclusively
from observed events — never from a hardcoded business graph.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.trace_contract import (
    TRACE_SCHEMA_VERSION,
    TERMINAL_STATUSES,
    TraceEvent,
    decision_summary_dict,
    sanitize_detail,
)

# Bounded retention (§72: nothing long-lived is unbounded).
_EVENTS_RING = 5000
_DECISIONS_RING = 500
_TRACES_MAX = 200
_SUBSCRIBER_CAP = 1000
_SUBSCRIBERS_MAX = 8
_LATENCY_SAMPLES = 400
_NODES_MAX = 200
_EDGES_MAX = 1000
_MIN_SAMPLE_N = 30  # below this: INSUFFICIENT_DATA, never fake percentiles
_SESSION_HISTORY = 20
_NO_SUBSCRIBER_GRACE_S = 60.0
_COALESCE_THRESHOLD = _SUBSCRIBER_CAP * 3 // 4

_SUBSCRIBER_COALESCE_STAGES = frozenset(
    {"MARKET", "FEATURES", "REGIME", "INFERENCE", "POLICY", "POST_POLICY"}
)


class ObserverStatus:
    OFF = "OFF"
    STARTING = "STARTING"
    ACTIVE = "ACTIVE"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def _percentile(sorted_vals: list[int], p: float) -> int:
    if not sorted_vals:
        return 0
    idx = min(len(sorted_vals) - 1, max(0, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


@dataclass
class Subscriber:
    """One live stream consumer. Buffer drops are counted, never silent."""

    subscriber_id: str
    connected_at: float = field(default_factory=time.monotonic)
    buffer: list[dict[str, Any]] = field(default_factory=list)
    dropped_visual_events: int = 0  # intermediate visual events dropped
    coalesced_events: int = 0  # merged redundant visual updates
    forced_drops: int = 0  # terminal-pressure forced removals (surfaced)
    last_seq_sent: int = 0

    def offer(self, event: dict[str, Any]) -> None:
        terminal = bool(event.get("terminal"))
        status = str(event.get("status", ""))
        is_decision_important = terminal or status in TERMINAL_STATUSES
        if len(self.buffer) >= _SUBSCRIBER_CAP:
            if not is_decision_important:
                self.dropped_visual_events += 1  # §43: never hide the drop
                return
            # Terminal pressure: evict the oldest NON-terminal visual event;
            # if the buffer is all-terminal, force-drop the oldest and count.
            idx = next(
                (i for i, e in enumerate(self.buffer) if not e.get("terminal")), None
            )
            if idx is None:
                self.buffer.pop(0)
                self.forced_drops += 1
            else:
                self.buffer.pop(idx)
                self.dropped_visual_events += 1
        elif (
            len(self.buffer) >= _COALESCE_THRESHOLD
            and self.buffer
            and is_decision_important is False
            and event.get("stage") in _SUBSCRIBER_COALESCE_STAGES
            and self.buffer[-1].get("trace_id") == event.get("trace_id")
            and self.buffer[-1].get("stage") == event.get("stage")
            and self.buffer[-1].get("event_type") == event.get("event_type")
            and not self.buffer[-1].get("terminal")
        ):
            self.coalesced_events += 1
            prev = dict(self.buffer[-1])
            merged_count = int(prev.get("coalesced_events", 1)) + 1
            self.buffer[-1] = event | {"coalesced_events": merged_count}
            return
        self.buffer.append(event)

    def drain(self) -> list[dict[str, Any]]:
        out = self.buffer
        self.buffer = []
        return out


class TraceObserver:
    """Process-wide passive observer (module singleton ``trace_observer``)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._status = ObserverStatus.OFF
        self.observer_id = _new_id("OBS")
        self._session: dict[str, Any] | None = None
        self._sessions: deque[dict[str, Any]] = deque(maxlen=_SESSION_HISTORY)
        self._tl = threading.local()
        self._seq = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=_EVENTS_RING)
        self._decisions: deque[dict[str, Any]] = deque(maxlen=_DECISIONS_RING)
        self._trace_events: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._trace_ctx: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._decision_index: OrderedDict[str, str] = OrderedDict()
        self._subscribers: dict[str, Subscriber] = {}
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[tuple[str, str], dict[str, Any]] = {}
        self._latency: dict[str, deque[int]] = {}
        self._counters: dict[str, int] = {
            "events_emitted": 0,
            "decisions_total": 0,
            "unmapped_events": 0,
            "emit_errors": 0,
            "sessions_total": 0,
            "node_overflow": 0,
            "edge_overflow": 0,
        }
        self._last_no_subscriber_at: float | None = None

    # ------------------------------------------------------------------ state
    @property
    def status(self) -> str:
        return self._status

    @property
    def active(self) -> bool:
        # First instruction of the hot-path guard when called via emit/…;
        # plain attribute read, never raises.
        return self._status is ObserverStatus.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            subs = [
                {
                    "subscriber_id": s.subscriber_id,
                    "connected_at": s.connected_at,
                    "buffered": len(s.buffer),
                    "dropped_visual_events": s.dropped_visual_events,
                    "coalesced_events": s.coalesced_events,
                }
                for s in self._subscribers.values()
            ]
            return {
                "observer_id": self.observer_id,
                "status": self._status,
                "session": dict(self._session) if self._session else None,
                "recent_sessions": [dict(s) for s in self._sessions],
                "counters": dict(self._counters),
                "events_retained": len(self._events),
                "decisions_retained": len(self._decisions),
                "events_ring_capacity": _EVENTS_RING,
                "decisions_ring_capacity": _DECISIONS_RING,
                "trace_schema_version": TRACE_SCHEMA_VERSION,
                "subscribers": subs,
            }

    # --------------------------------------------------------------- lifecycle
    def start_session(self) -> dict[str, Any]:
        with self._lock:
            if self._status is ObserverStatus.ACTIVE and self._session:
                return dict(self._session)
            try:
                self._status = ObserverStatus.STARTING
                self._session = {
                    "observer_id": self.observer_id,
                    "session_id": _new_id("SES"),
                    "started_at": _now_iso(),
                    "stopped_at": None,
                    "status": ObserverStatus.ACTIVE,
                }
                self._status = ObserverStatus.ACTIVE
                self._last_no_subscriber_at = None
                self._counters["sessions_total"] += 1
                return dict(self._session)
            except Exception:
                self._status = ObserverStatus.ERROR
                self._counters["emit_errors"] += 1
                return {"observer_id": self.observer_id, "status": ObserverStatus.ERROR}

    def stop_session(self) -> dict[str, Any]:
        with self._lock:
            if self._status is ObserverStatus.OFF:
                return dict(self._session) if self._session else {"status": ObserverStatus.OFF}
            try:
                self._status = ObserverStatus.STOPPING
                if self._session:
                    self._session["stopped_at"] = _now_iso()
                    self._session["status"] = ObserverStatus.OFF
                    self._sessions.append(dict(self._session))
                self._status = ObserverStatus.OFF
                self._session = None
                self._tl.trace_id = None
                self._tl.last_event_id = None
                # Visual buffers are a no-op cost while OFF: drop them.
                for sub in self._subscribers.values():
                    sub.buffer.clear()
                return {"status": ObserverStatus.OFF}
            except Exception:
                self._status = ObserverStatus.ERROR
                return {"status": ObserverStatus.ERROR}

    def subscribe(self) -> Subscriber | None:
        with self._lock:
            if len(self._subscribers) >= _SUBSCRIBERS_MAX:
                return None
            sub = Subscriber(subscriber_id=_new_id("SUB"))
            self._subscribers[sub.subscriber_id] = sub
            self._last_no_subscriber_at = None
            return sub

    def unsubscribe(self, subscriber_id: str) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)
            if not self._subscribers and self._status is ObserverStatus.ACTIVE:
                self._last_no_subscriber_at = time.monotonic()
                self._sweep_locked()

    def _sweep_locked(self) -> None:
        """Auto-stop an ACTIVE session left without any stream consumer
        (crashed/closed UI) so detailed tracing cannot run unobserved."""
        if (
            self._status is ObserverStatus.ACTIVE
            and not self._subscribers
            and self._last_no_subscriber_at is not None
            and (time.monotonic() - self._last_no_subscriber_at) > _NO_SUBSCRIBER_GRACE_S
        ):
            self.stop_session()

    # ------------------------------------------------------------------ emit
    def begin_trace(self, *, symbol: str | None, detail: dict[str, Any] | None = None) -> str:
        """Open a per-market-decision trace (detailed tracing only).

        Returns "" when the observer is OFF — callers treat that as
        'not observed' and pass no trace id onward. Never raises.
        """
        if self._status is not ObserverStatus.ACTIVE:
            return ""
        try:
            with self._lock:
                trace_id = _new_id("TRC")
                self._tl.trace_id = trace_id
                self._tl.last_event_id = None
                self._push_locked(
                    trace_id=trace_id,
                    parent_event_id=None,
                    stage="MARKET",
                    component="tick_pipeline",
                    event_type="MARKET_EVENT",
                    status="OBSERVED",
                    symbol=symbol,
                    detail=detail,
                )
                return trace_id
        except Exception:
            self._counters["emit_errors"] += 1
            return ""

    def emit(
        self,
        *,
        stage: str,
        component: str,
        event_type: str,
        status: str,
        symbol: str | None = None,
        decision_id: str | None = None,
        detail: dict[str, Any] | None = None,
        latency_us: int | None = None,
        terminal: bool = False,
        provenance_gap: bool = False,
        trace_id: str | None = None,
    ) -> str:
        """Record one runtime fact into the active trace. OFF = immediate
        return; ACTIVE = in-memory only; never raises."""
        if self._status is not ObserverStatus.ACTIVE:
            return ""
        try:
            with self._lock:
                tid = trace_id or getattr(self._tl, "trace_id", None) or ""
                # Parent = this trace's own last event, never another
                # thread-local trace's tail (cross-trace link leak guard).
                parent = (
                    getattr(self._tl, "last_event_id", None)
                    if tid and tid == getattr(self._tl, "trace_id", None)
                    else None
                )
                return self._push_locked(
                    trace_id=tid,
                    parent_event_id=parent,
                    stage=stage,
                    component=component,
                    event_type=event_type,
                    status=status,
                    symbol=symbol,
                    decision_id=decision_id,
                    detail=detail,
                    latency_us=latency_us,
                    terminal=terminal,
                    provenance_gap=provenance_gap,
                )
        except Exception:
            self._counters["emit_errors"] += 1
            return ""

    def _push_locked(
        self,
        *,
        trace_id: str,
        parent_event_id: str | None,
        stage: str,
        component: str,
        event_type: str,
        status: str,
        symbol: str | None = None,
        decision_id: str | None = None,
        detail: dict[str, Any] | None = None,
        latency_us: int | None = None,
        terminal: bool = False,
        provenance_gap: bool = False,
    ) -> str:
        self._seq += 1
        event_id = f"EV-{self._seq:08d}"
        unmapped = parent_event_id is None and stage != "MARKET"
        ev = TraceEvent(
            event_id=event_id,
            trace_id=trace_id,
            sequence=self._seq,
            timestamp=_now_iso(),
            monotonic_ns=time.monotonic_ns(),
            stage=stage,
            component=component,
            event_type=event_type,
            status=status,
            parent_event_id=parent_event_id,
            symbol=symbol,
            decision_id=decision_id,
            latency_us=latency_us,
            terminal=terminal,
            unmapped=unmapped,
            provenance_gap=provenance_gap,
            detail=sanitize_detail(detail) if detail else {},
        )
        d = ev.to_dict()
        self._events.append(d)
        self._counters["events_emitted"] += 1
        if unmapped:
            self._counters["unmapped_events"] += 1
        refs = self._trace_events.get(trace_id) if trace_id else None
        parent_stage: str | None = None
        if refs and parent_event_id and refs and refs[-1]["event_id"] == parent_event_id:
            parent_stage = refs[-1]["stage"]
        if trace_id:
            if refs is None:
                refs = self._trace_events.setdefault(trace_id, [])
            refs.append(d)
            if len(self._trace_events) > _TRACES_MAX:
                self._trace_events.popitem(last=False)
            if decision_id:
                self._note_decision_id_locked(trace_id, decision_id)
            if stage == "INFERENCE":
                ctx = self._trace_ctx.setdefault(trace_id, {})
                ctx.update(
                    {
                        k: v
                        for k, v in (detail or {}).items()
                        if k
                        in (
                            "model_id",
                            "model_version",
                            "feature_dim",
                            "feature_schema_id",
                            "schema_hash",
                            "artifact_path",
                            "artifact_fingerprint",
                        )
                    }
                )
                if len(self._trace_ctx) > _TRACES_MAX:
                    self._trace_ctx.popitem(last=False)
        if latency_us is not None:
            samples = self._latency.setdefault(stage, deque(maxlen=_LATENCY_SAMPLES))
            samples.append(int(latency_us))
        self._observe_topology_locked(
            stage, parent_stage=parent_stage is not None
        )
        if parent_stage:
            self._observe_edge_locked(parent_stage, stage)
        if trace_id and not terminal:
            self._tl.last_event_id = event_id
        elif terminal:
            self._tl.trace_id = None
            self._tl.last_event_id = None
        d_sub = dict(d)
        for sub in self._subscribers.values():
            sub.offer(d_sub)
            sub.last_seq_sent = self._seq
        return event_id

    def _note_decision_id_locked(self, trace_id: str, decision_id: str) -> None:
        if decision_id not in self._decision_index:
            self._decision_index[decision_id] = trace_id
            if len(self._decision_index) > _DECISIONS_RING:
                self._decision_index.popitem(last=False)
        # Backfill the canonical join key onto earlier events of this trace.
        for e in self._trace_events.get(trace_id, ()):
            if e.get("decision_id") is None:
                e["decision_id"] = decision_id

    # ------------------------------------------------------ always-on decision
    def emit_decision(
        self,
        *,
        summary: dict[str, Any],
        detail: dict[str, Any] | None = None,
        terminal_status: str | None = None,
        terminal: bool = True,
        component: str = "signal_policy",
    ) -> None:
        """Low-overhead decision record — emitted even when the observer is
        OFF so recent decisions hydrate when the UI opens. Never raises.

        ``terminal=False`` records an APPROVED decision whose downstream
        stages (RISK/EXECUTION/MT5) are still to come: the open trace is
        kept so later events chain onto it.
        """
        try:
            with self._lock:
                trace_id = str(summary.get("trace_id") or getattr(self._tl, "trace_id", "") or "")
                decision_id = summary.get("decision_id")
                row = decision_summary_dict(summary | ({"trace_id": trace_id} if trace_id else {}))
                row["recorded_at"] = _now_iso()
                if terminal_status:
                    row["status"] = terminal_status
                ctx = self._trace_ctx.pop(trace_id, None) if trace_id else None
                if ctx:
                    row["model"] = ctx
                if detail:
                    row["detail"] = sanitize_detail(detail)
                self._decisions.append(row)
                self._counters["decisions_total"] += 1
                if decision_id and trace_id:
                    self._note_decision_id_locked(trace_id, str(decision_id))
                # audit_signals is keyed by request_id — index it too so the
                # /api/v1/decisions/<id>/gates join works from either key.
                req_id = summary.get("request_id")
                if req_id and trace_id and req_id != decision_id:
                    self._decision_index[str(req_id)] = trace_id
                self._observe_topology_locked("DECISION", parent_stage=bool(trace_id))
                if trace_id:
                    parent_stage = None
                    evs = self._trace_events.get(trace_id)
                    if evs:
                        parent_stage = evs[-1]["stage"]
                    if parent_stage and parent_stage != "DECISION":
                        self._observe_edge_locked(parent_stage, "DECISION")
                if self._status is ObserverStatus.ACTIVE and trace_id:
                    self._seq += 1
                    ev = TraceEvent(
                        event_id=f"EV-{self._seq:08d}",
                        trace_id=trace_id,
                        sequence=self._seq,
                        timestamp=_now_iso(),
                        monotonic_ns=time.monotonic_ns(),
                        stage="DECISION",
                        component=component,
                        event_type="DECISION",
                        status=str(row.get("status") or "UNKNOWN"),
                        parent_event_id=getattr(self._tl, "last_event_id", None),
                        symbol=row.get("symbol"),
                        decision_id=str(decision_id) if decision_id else None,
                        terminal=terminal,
                        detail={
                            k: v
                            for k, v in row.items()
                            if k
                            in (
                                "action",
                                "decision_stage",
                                "blocked_by",
                                "reason_code",
                                "rejection_reason",
                                "model_action",
                                "confidence",
                                "regime",
                                "risk_checks",
                            )
                        },
                    )
                    d = ev.to_dict()
                    self._events.append(d)
                    refs = self._trace_events.get(trace_id)
                    if refs is not None:
                        refs.append(d)
                    self._counters["events_emitted"] += 1
                    for sub in self._subscribers.values():
                        sub.offer(dict(d))
                # Terminal end of path: close the open trace. Non-terminal
                # (APPROVED) decisions keep the trace open for RISK/EXEC/MT5.
                if terminal:
                    self._tl.trace_id = None
                    self._tl.last_event_id = None
        except Exception:
            self._counters["emit_errors"] += 1

    # ------------------------------------------------------------- aggregates
    def _observe_topology_locked(self, stage: str, *, parent_stage: bool) -> None:
        node = self._nodes.get(stage)
        if node is None:
            if len(self._nodes) >= _NODES_MAX:
                self._counters["node_overflow"] += 1
                return
            node = {"stage": stage, "count": 0, "first_seen_seq": self._seq, "last_ts": _now_iso()}
            self._nodes[stage] = node
        node["count"] += 1
        node["last_ts"] = _now_iso()
        node["root"] = node.get("root", True) and not parent_stage

    def _observe_edge_locked(self, source: str, target: str) -> None:
        key = (source, target)
        edge = self._edges.get(key)
        if edge is None:
            if len(self._edges) >= _EDGES_MAX:
                self._counters["edge_overflow"] += 1
                return
            edge = {"source": source, "target": target, "count": 0, "last_ts": _now_iso()}
            self._edges[key] = edge
        edge["count"] += 1
        edge["last_ts"] = _now_iso()

    def topology(self) -> dict[str, Any]:
        """Derived runtime topology — built ONLY from observed events."""
        with self._lock:
            return {
                "generated_at": _now_iso(),
                "observer_status": self._status,
                "nodes": sorted(self._nodes.values(), key=lambda n: n["first_seen_seq"]),
                "edges": sorted(self._edges.values(), key=lambda e: -e["count"]),
                "counters": dict(self._counters),
                "trace_schema_version": TRACE_SCHEMA_VERSION,
            }

    def latency_stats(self) -> dict[str, Any]:
        with self._lock:
            out: dict[str, Any] = {}
            for stage, samples in self._latency.items():
                vals = sorted(samples)
                n = len(vals)
                entry: dict[str, Any] = {"n": n}
                if n >= _MIN_SAMPLE_N:
                    entry.update(
                        {
                            "p50_us": _percentile(vals, 0.50),
                            "p95_us": _percentile(vals, 0.95),
                            "p99_us": _percentile(vals, 0.99),
                            "max_us": vals[-1],
                            "mean_us": sum(vals) // n,
                        }
                    )
                else:
                    entry["insufficient_data"] = True
                out[stage] = entry
            return {
                "generated_at": _now_iso(),
                "min_sample_n": _MIN_SAMPLE_N,
                "stages": out,
            }

    # ----------------------------------------------------------------- queries
    def events_since(self, last_seq: int, *, limit: int = 1000) -> dict[str, Any]:
        with self._lock:
            batch = [e for e in self._events if e["sequence"] > last_seq][-limit:]
            gap = False
            if self._events and last_seq > 0:
                oldest = self._events[0]["sequence"]
                if last_seq < oldest - 1:
                    gap = True  # ring evicted events the client asked for
            return {"events": batch, "gap": gap, "last_seq": self._seq}

    def decisions_list(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        with self._lock:
            rows = list(self._decisions)
            rows.reverse()  # newest first
            return {
                "total": len(rows),
                "offset": offset,
                "rows": rows[offset : offset + limit],
                "last_seq": self._seq,
            }

    def trace_bundle(self, key: str) -> dict[str, Any]:
        """Full forensic bundle for a trace_id OR decision_id (EXEC-…)."""
        with self._lock:
            trace_id = key
            if key not in self._trace_events:
                trace_id = self._decision_index.get(key, "")
            evs = list(self._trace_events.get(trace_id, ())) if trace_id else []
            summary = None
            for row in reversed(self._decisions):
                if row.get("decision_id") == key or (trace_id and row.get("trace_id") == trace_id):
                    summary = row
                    break
            return {
                "query": key,
                "trace_id": trace_id or None,
                "decision_id": (summary or {}).get("decision_id") or (
                    key if key.startswith("EXEC-") else None
                ),
                "events": evs,
                "summary": summary,
                "found": bool(evs or summary),
            }

    def integrity_report(self) -> dict[str, Any]:
        """Trace-integrity scan over retained events (§45): duplicate ids,
        sequence holes, rootless mid-chain links, impossible orders."""
        with self._lock:
            warnings: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            last_seq_seen = 0
            by_trace: dict[str, list[dict[str, Any]]] = {}
            for e in self._events:
                eid = e["event_id"]
                if eid in seen_ids:
                    warnings.append({"code": "DUPLICATE_EVENT", "event_id": eid})
                seen_ids.add(eid)
                if last_seq_seen and e["sequence"] > last_seq_seen + 1:
                    warnings.append(
                        {
                            "code": "MISSING_SEQUENCE",
                            "after": last_seq_seen,
                            "before": e["sequence"],
                        }
                    )
                last_seq_seen = max(last_seq_seen, e["sequence"])
                by_trace.setdefault(e.get("trace_id") or "", []).append(e)
            known_ids = seen_ids
            for tid, evs in by_trace.items():
                if not tid:
                    continue
                for e in evs:
                    p = e.get("parent_event_id")
                    if p and p not in known_ids:
                        warnings.append(
                            {"code": "MISSING_PARENT", "event_id": e["event_id"], "parent": p}
                        )
                has_exec_attempt = any(x["stage"] in ("EXECUTION", "MT5") for x in evs)
                has_decision = any(x["stage"] == "DECISION" for x in evs)
                has_mt5_resp = any(x["stage"] == "MT5" for x in evs)
                has_order = any(x["stage"] == "EXECUTION" for x in evs)
                if has_exec_attempt and not has_decision:
                    warnings.append({"code": "EXECUTION_WITHOUT_DECISION", "trace_id": tid})
                if has_mt5_resp and not has_order:
                    warnings.append({"code": "MT5_WITHOUT_ORDER", "trace_id": tid})
            return {
                "generated_at": _now_iso(),
                "traces_scanned": len([t for t in by_trace if t]),
                "warnings": warnings[:200],
                "truncated": len(warnings) > 200,
            }


# Process-wide singleton. Every guarded call-site import binds to this object.
trace_observer = TraceObserver()
