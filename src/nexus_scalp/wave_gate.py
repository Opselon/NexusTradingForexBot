"""Multi-PR wave gate for the push/merge loop (wave-1).

A *wave* is a set of lanes (one branch + PR each) that must ALL be green
before ANY of them merges, because the lanes are order-dependent parts of
one deliverable. This module answers two questions the single-PR evidence
report does not:

  1. ``wave_status`` — is every lane green *right now*, and what blocks?
  2. ``drift_report`` — did base ``main`` move under a lane after its PR
     was opened? Under ``strict`` branch protection a behind lane cannot
     merge at all; and even without it, a stale base silently reverts the
     lanes that merged first.

It reuses the package's own evidence pipeline (``collect_evidence``) rather
than a parallel GitHub client, so wave decisions are made from the SAME
evidence the single-PR reports use — including the required-context list
read from branch protection (``MergePreconditionCollector``) and the
check-run state at the PR head (``CheckRunCollector``).

The module never merges anything: it is a read-only decision surface. The
caller (the orchestrating agent) performs the merge once the verdict is
``READY``, and only via the normal ``gh pr merge`` path.

Usage::

    from nexus_scalp.wave_gate import wave_status, DriftReport

    board = wave_status(client, repo="Opselon/NexusTradingForexBot",
                        lanes=[{"name": "ci-summary", "pr": 592}])
    print(board.state)            # READY | IN_PROGRESS | BLOCKED | UNKNOWN
    for lane in board.lanes:
        print(lane.name, lane.state, lane.blockers)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.pr_evidence.collectors import (
    EvidenceOptions,
    collect_evidence,
)
from nexus_scalp.pr_evidence.github_client import GitHubClient
from nexus_scalp.pr_evidence.models import UNKNOWN

__all__ = [
    "DriftReport",
    "LaneSpec",
    "LaneState",
    "WaveBoard",
    "drift_report",
    "wave_status",
]

#: Conclusions that count as a green check (mirrors pr_evidence models).
_OK_CONCLUSIONS = ("SUCCESS", "NEUTRAL", "SKIPPED")


@dataclass(frozen=True)
class LaneSpec:
    """One lane of a wave: a name plus the PR that carries it."""

    name: str
    pr: int
    #: Optional because the wave may be declared before the PR is opened.
    branch: str = UNKNOWN


@dataclass(frozen=True)
class LaneState:
    """The gate state of one lane at a fixed instant."""

    name: str
    pr: int
    branch: str
    state: str  # GREEN | PENDING | RED | CONFLICT | UNKNOWN | MERGED
    #: Required-context names that are red at the PR head.
    failed_required: tuple[str, ...] = ()
    #: Required-context names still running (or not yet reported).
    pending_required: tuple[str, ...] = ()
    #: Required contexts absent from the check-run surface entirely.
    missing_required: tuple[str, ...] = ()
    #: Non-required checks that are red (informational; never blocks).
    failed_other: tuple[str, ...] = ()
    mergeable: bool | None = None
    mergeable_state: str = UNKNOWN
    head_sha: str = UNKNOWN
    blockers: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def green(self) -> bool:
        return self.state == "GREEN"

    @property
    def ready(self) -> bool:
        """Mergeable AND every required context green AND no conflict."""
        return self.state == "GREEN" and self.mergeable is True


@dataclass(frozen=True)
class WaveBoard:
    """The live state of the whole wave."""

    lanes: tuple[LaneState, ...]
    required_checks: tuple[str, ...] = ()
    base_branch: str = "main"
    base_sha: str = UNKNOWN
    collected_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )

    @property
    def state(self) -> str:
        """READY when every lane is green and mergeable.

        A single RED lane is BLOCKED (one lane can fail the whole wave,
        because the lanes are order-dependent). IN_PROGRESS means nothing
        is red yet but at least one check is still pending or unknown.
        """
        if not self.lanes:
            return "UNKNOWN"
        if any(l.state in {"RED", "CONFLICT"} for l in self.lanes):
            return "BLOCKED"
        if all(l.state == "MERGED" for l in self.lanes):
            return "MERGED"
        if all(l.ready for l in self.lanes):
            return "READY"
        return "IN_PROGRESS"

    @property
    def ready(self) -> bool:
        return self.state == "READY"

    @property
    def blockers(self) -> list[str]:
        out: list[str] = []
        for lane in self.lanes:
            for b in lane.blockers:
                out.append(f"{lane.name}: {b}")
        return out


def _to_str_tuple(items: Any) -> tuple[str, ...]:
    return tuple(str(i) for i in items if i)


def _classify(evidence: Any, required: tuple[str, ...]) -> LaneState:
    """Turn one PR's evidence into a LaneState (never raises)."""
    meta = evidence.meta
    pr = meta.pr
    branch = meta.branch
    name = getattr(evidence, "_lane_name", UNKNOWN) or UNKNOWN

    # Check-run map at the CI head: name -> normalized conclusion.
    # A run that has not finished (status QUEUED/IN_PROGRESS) or that finished
    # without a conclusion must read as PENDING, never FAILED: right after a
    # push the rollup reports an empty conclusion, and treating that as red
    # aborts a healthy wave while CI is still running.
    by_name: dict[str, str] = {}
    for check in evidence.checks:
        if str(check.status).upper() != "COMPLETED":
            by_name[str(check.name)] = "PENDING"
            continue
        concl = str(check.conclusion or "").upper()
        if not concl or concl == UNKNOWN.upper():
            by_name[str(check.name)] = "PENDING"
        else:
            by_name[str(check.name)] = concl

    failed_req: list[str] = []
    pending_req: list[str] = []
    missing_req: list[str] = []
    failed_other: list[str] = []
    seen: set[str] = set()
    for check in evidence.checks:
        seen.add(str(check.name))
    for name_, concl in by_name.items():
        if name_ in required:
            if concl == "PENDING":
                pending_req.append(name_)
            elif concl in _OK_CONCLUSIONS:
                pass
            else:
                failed_req.append(name_)
        elif concl not in _OK_CONCLUSIONS and concl != "PENDING":
            failed_other.append(name_)
    for req in required:
        if req not in seen:
            missing_req.append(req)

    mergeable = meta.mergeable
    mergeable_state = meta.mergeable_state
    blockers: list[str] = []
    if failed_req:
        blockers.append("required checks failed: " + ", ".join(sorted(set(failed_req))))
    if missing_req:
        blockers.append("required checks missing: " + ", ".join(sorted(set(missing_req))))
    if meta.merged is True:
        state = "MERGED"
        blockers = []
    elif mergeable is False:
        state = "CONFLICT"
        blockers.append(f"not mergeable ({mergeable_state})")
    elif failed_req or missing_req:
        state = "RED"
    elif pending_req:
        state = "PENDING"
    elif mergeable is None:
        state = "UNKNOWN"
        blockers.append("mergeability not yet computed by GitHub")
    else:
        state = "GREEN"

    return LaneState(
        name=name if name != UNKNOWN else f"pr-{pr}",
        pr=pr,
        branch=branch,
        state=state,
        failed_required=tuple(sorted(set(failed_req))),
        pending_required=tuple(sorted(set(pending_req))),
        missing_required=tuple(sorted(set(missing_req))),
        failed_other=tuple(sorted(set(failed_other))),
        mergeable=mergeable,
        mergeable_state=mergeable_state,
        head_sha=meta.pr_head_sha,
        blockers=tuple(blockers),
        errors=tuple(evidence.errors or ()),
    )


def _options() -> EvidenceOptions:
    """CI-only evidence: no local tests, no comment publication."""
    return EvidenceOptions(
        fetch_annotations=True,
        fetch_codeql=False,
        local_tests=False,
        reviews=False,
        fetch_logs=False,
        fetch_artifacts=False,
    )


def wave_status(
    client: GitHubClient,
    *,
    repo: str,
    lanes: list[LaneSpec] | list[dict[str, Any]],
    base_branch: str = "main",
    verbose: bool = False,
) -> WaveBoard:
    """Collect the live gate state of every lane in the wave.

    ``lanes`` accepts ``LaneSpec`` objects or plain dicts with the same
    keys (``name``, ``pr``, optional ``branch``) — the orchestrator's
    JSON-shaped lane table works directly.
    """

    specs: list[LaneSpec] = []
    for lane in lanes:
        if isinstance(lane, LaneSpec):
            specs.append(lane)
            continue
        specs.append(
            LaneSpec(
                name=str(lane["name"]),
                pr=int(lane["pr"]),
                branch=str(lane.get("branch") or UNKNOWN),
            )
        )

    # Required contexts come from branch protection, same as the evidence
    # report, so the wave gate can never disagree with the merge gate.
    required: tuple[str, ...] = ()
    base_sha = UNKNOWN
    try:
        protection = client.get_branch_protection(base_branch)
        if isinstance(protection, dict) and "_error" not in protection:
            ctxs = (protection.get("required_status_checks") or {}).get("contexts") or []
            required = _to_str_tuple(ctxs)
            base_sha = str((protection.get("required_status_checks") or {}).get("sha") or UNKNOWN)
    except Exception:
        pass

    lane_states: list[LaneState] = []
    for spec in specs:
        try:
            evidence = collect_evidence(
                client=client,
                repo=repo,
                pr=spec.pr,
                options=_options(),
            )
            # tag the evidence with the lane name for _classify
            object.__setattr__(evidence, "_lane_name", spec.name)
        except Exception as exc:  # collection failure is a lane gap, not a crash
            lane_states.append(
                LaneState(
                    name=spec.name,
                    pr=spec.pr,
                    branch=spec.branch,
                    state="UNKNOWN",
                    blockers=(f"evidence collection failed: {type(exc).__name__}: {exc}",),
                )
            )
            continue
        state = _classify(evidence, required)
        # keep the declared lane name even when the PR branch differs
        object.__setattr__(state, "name", spec.name)
        lane_states.append(state)

    return WaveBoard(
        lanes=tuple(lane_states),
        required_checks=required,
        base_branch=base_branch,
        base_sha=base_sha,
    )


@dataclass(frozen=True)
class DriftReport:
    """How far a lane's base has fallen behind ``main`` since the PR opened.

    Under strict branch protection a behind lane cannot merge at all; even
    without it, merging a stale-base lane silently reverts the lanes that
    merged before it — the classic multi-PR wave failure. The orchestrator
    must rebase (or merge-base update) before merging.
    """

    lane: str
    pr: int
    head_sha: str
    base_recorded: str
    base_current: str
    behind: bool
    ahead_of_base: int

    @property
    def stale(self) -> bool:
        return self.behind


def drift_report(
    client: GitHubClient,
    *,
    repo: str,
    lane: LaneSpec | dict[str, Any],
    base_branch: str = "main",
) -> DriftReport:
    """Compare the base recorded on the PR with the tip of ``base_branch``.

    Compares full SHAs (not commit counts) so it stays exact even when the
    repo force-pushes base or squashes: ``behind`` means the recorded base
    is not reachable from the current base tip.
    """
    spec = lane
    if isinstance(spec, dict):
        spec = LaneSpec(
            name=str(spec["name"]),
            pr=int(spec["pr"]),
            branch=str(spec.get("branch") or UNKNOWN),
        )
    pr_payload = client.get_pr(spec.pr)
    base_recorded = str((pr_payload.get("base") or {}).get("sha") or UNKNOWN)
    head_sha = str((pr_payload.get("head") or {}).get("sha") or UNKNOWN)
    base_current = _base_tip(client, base_branch)
    ahead = _ahead_count(client, base_branch, base_recorded)
    behind = UNKNOWN not in (base_recorded, base_current) and base_recorded != base_current
    return DriftReport(
        lane=spec.name,
        pr=spec.pr,
        head_sha=head_sha,
        base_recorded=base_recorded,
        base_current=base_current,
        behind=behind,
        ahead_of_base=ahead,
    )


def _base_tip(client: GitHubClient, base_branch: str) -> str:
    try:
        payload = client._get(f"/repos/{client._repo}/branches/{base_branch}")
        return str((payload.get("commit") or {}).get("sha") or UNKNOWN)
    except Exception:
        return UNKNOWN


def _ahead_count(client: GitHubClient, base_branch: str, base_recorded: str) -> int:
    """Commits on base tip that the recorded base does not have."""
    if base_recorded in (UNKNOWN, ""):
        return -1
    try:
        payload = client._get(f"/repos/{client._repo}/compare/{base_recorded}...{base_branch}")
        return int(payload.get("ahead_by") or 0)
    except Exception:
        return -1
