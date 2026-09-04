"""Canonical runtime-truth field matrix (state-truth hardening, TASK-STATE-SEMANTICS).

Machine-checkable single inventory of every critical externally visible runtime
fact: where it comes from, how it is derived, who consumes it, and what a
contradiction looks like. The matrix is DATA, not prose: tests
(tests/unit/test_state_truth_matrix.py) compare this module against the repo's
own release surfaces (get_version_info, RuntimeVersionBlock, the CHG-0043
runtime snapshot) so drift between the matrix and reality fails CI instead of
surfacing as a live contradiction.

Scope guard (ownership contract):
- This module CONSUMES release/runtime_snapshot.py + release/state_taxonomy.py
  (CHG-0043, foreign owner) through their public functions; it never
  re-implements them and never forks a parallel snapshot engine.
- Files owned by other agents (operator_routes.py, api_v1/*, Web/*) are NOT
  patched here; contradictions found on their surfaces are filed in
  agents/bugs.md instead.

State vocabulary is borrowed from state_taxonomy / release_status (UNKNOWN,
NOT_INITIALIZED, NOT_RECORDED, DEGRADED, ...) so the matrix cannot invent a
fourth spelling for a state the taxonomy already owns.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

Resolver = Callable[[], object]

_STATE_UNAVAILABLE: Final = "STATE_UNAVAILABLE"
_NO_PROBE: Final = "NO_PROBE_REGISTERED"
# Section-level marker: the snapshot section could not be OBSERVED (build
# failure / missing / wrong shape). Distinct from "observed and verified
# absent" so resolvers can emit taxonomy UNKNOWN instead of NOT_CONFIGURED.
STATE_UNAVAILABLE_KEY: Final = "_state_section_unavailable"

# ---------------------------------------------------------------------------
# Live-engine probe plug point.
#
# The CHG-0043 snapshot covers configured/config identity, but the ACTUAL
# engine mode and aggregated health live on the booted LiveEngine (web/server
# "Canonical live-state snapshot", engine._runtime_mode + _build_health_section).
# That object is process-local, so the authoritative observation is REGISTERED
# here by the API/CLI layer at boot. With no probe registered the matrix
# reports an explicit NO_PROBE_REGISTERED status - never a synthesized value
# (no fake fallback presented as authoritative, R3).
# ---------------------------------------------------------------------------

_LIVE_PROBE: Callable[[], dict[str, object]] | None = None


_LIVE_PROBE_STATE: Final[list[Callable[[], dict[str, object]]]] = []


def _get_live_probe() -> Callable[[], dict[str, object]] | None:
    return _LIVE_PROBE_STATE[0] if _LIVE_PROBE_STATE else None


def register_live_probe(probe: Callable[[], dict[str, object]]) -> None:
    """Register the process-local live-engine observer (idempotent, replaces)."""
    # Single-slot container keeps this lint-safe without a global statement.
    _LIVE_PROBE_STATE.clear()
    _LIVE_PROBE_STATE.append(probe)


def clear_live_probe() -> None:
    """Drop any registered live probe (tests / shutdown)."""
    _LIVE_PROBE_STATE.clear()


def _resolve_via_live_probe(key: str) -> tuple[str, object]:
    probe = _get_live_probe()
    if probe is None:
        return _NO_PROBE, _STATE_UNAVAILABLE
    try:
        data = probe() or {}
    except Exception as exc:
        return "UNKNOWN", f"probe_error:{type(exc).__name__}"
    value = data.get(key, _STATE_UNAVAILABLE)
    if value in (None, _STATE_UNAVAILABLE):
        return "UNKNOWN", value
    return "RESOLVED", value


def _probe_backed(key: str) -> object:
    # Preserve the NO_PROBE distinction through the plain-resolver signature.
    status, value = _resolve_via_live_probe(key)
    return _NO_PROBE if status == _NO_PROBE else value


# ---------------------------------------------------------------------------
# Snapshot-backed resolvers (lazy imports; read-only consumption of CHG-0043
# surfaces). Each resolver NEVER raises: unresolvable state is an explicit
# UNKNOWN / UNAVAILABLE outcome.
# ---------------------------------------------------------------------------


def _build_snapshot() -> dict[str, object]:
    from nexus_scalp.release.runtime_snapshot import build_runtime_snapshot

    return dict(build_runtime_snapshot(include_update=False))


def _snapshot_section(name: str) -> dict[str, object]:
    """Read one CHG-0043 snapshot section with taxonomy-honest failure.

    State-truth ruling (NX-STP0): a section that CANNOT be observed
    (snapshot build failed / section missing / wrong shape) is UNKNOWN in
    the state taxonomy — the operator must be able to distinguish "this
    truth is unobservable right now" from "verified absent configuration"
    (NOT_CONFIGURED). Resolvers map an empty section to their own verified
    -absent state only where absence is genuinely verifiable.
    """
    try:
        section = _build_snapshot().get(name)
    except Exception:
        return {STATE_UNAVAILABLE_KEY: True}
    if not isinstance(section, dict):
        return {STATE_UNAVAILABLE_KEY: True}
    return dict(section)


def _resolve_identity() -> dict[str, object]:
    return _snapshot_section("identity")


def _resolve_runtime_mode_section() -> dict[str, object]:
    return _snapshot_section("runtime_mode")


def _resolve_model_section() -> dict[str, object]:
    return _snapshot_section("model")


def _resolve_db_capability() -> dict[str, object]:
    database = _snapshot_section("database")
    capability = database.get("capability")
    return dict(capability) if isinstance(capability, dict) else {}


def _resolve_web_bundle_block() -> dict[str, object]:
    from nexus_scalp.release.versioning import RuntimeVersionBlock

    try:
        return dict(RuntimeVersionBlock().build())
    except Exception:
        # Bundle unobservable — marked, never an empty dict masquerading as
        # "verified absent" (resolvers translate the marker to taxonomy
        # UNKNOWN).
        return {STATE_UNAVAILABLE_KEY: True}


def _resolve_configured_mode() -> object:
    section = _resolve_runtime_mode_section()
    if section.get(STATE_UNAVAILABLE_KEY):
        # Section unobservable: taxonomy UNKNOWN, never a fabricated mode.
        return "UNKNOWN"
    value = section.get("configured_mode")
    return value if value else "UNKNOWN"


def _resolve_actual_engine_mode() -> object:
    return _probe_backed("actual_engine_mode")


def _resolve_health() -> object:
    return _probe_backed("health")


def _resolve_version() -> object:
    identity = _resolve_identity()
    if identity.get(STATE_UNAVAILABLE_KEY):
        # Section unobservable: taxonomy UNKNOWN, never a fabricated version.
        return "UNKNOWN"
    value = identity.get("version")
    return value if value else "UNKNOWN"


def _resolve_commit() -> object:
    # CHG-0043: unstamped builds carry commit=None + commit_status NOT_RECORDED;
    # None IS the truth, not a failure.
    identity = _resolve_identity()
    if identity.get(STATE_UNAVAILABLE_KEY):
        # Section unobservable: taxonomy UNKNOWN.
        return "UNKNOWN"
    return identity.get("commit", _STATE_UNAVAILABLE)


def _resolve_build_target_schema() -> object:
    identity = _resolve_identity()
    if identity.get(STATE_UNAVAILABLE_KEY):
        # Section unobservable: taxonomy UNKNOWN, never defaulted to 50D.
        return "UNKNOWN"
    value = identity.get("feature_schema")
    return value if value else "UNKNOWN"


def _resolve_live_active_schema() -> object:
    block = _resolve_web_bundle_block()
    if block.get(STATE_UNAVAILABLE_KEY):
        # Bundle unobservable: taxonomy UNKNOWN (bundle failure maps to the
        # same marker shape as snapshot sections).
        return "UNKNOWN"
    schema = block.get("feature_schema")
    if isinstance(schema, dict):
        return schema.get("id") or "UNKNOWN"
    return schema if schema else "UNKNOWN"


def _resolve_model_dimension() -> object:
    model = _resolve_model_section()
    if model.get(STATE_UNAVAILABLE_KEY):
        # Section unobservable: taxonomy UNKNOWN, never a verified-absent lie.
        return "UNKNOWN"
    registry = model.get("registry_champion")
    if isinstance(registry, dict) and registry.get("available"):
        dim = registry.get("feature_dimension")
        if dim is not None:
            return dim
    return "NOT_CONFIGURED"


def _resolve_model_identity() -> object:
    model = _resolve_model_section()
    if model.get(STATE_UNAVAILABLE_KEY):
        # Section unobservable: taxonomy UNKNOWN, never a verified-absent lie.
        return "UNKNOWN"
    registry = model.get("registry_champion")
    if isinstance(registry, dict) and registry.get("available"):
        schema_id = registry.get("feature_schema_id")
        if schema_id is not None:
            return str(schema_id)
    return "NOT_CONFIGURED"


def _resolve_db_state() -> object:
    capability = _resolve_db_capability()
    if capability.get(STATE_UNAVAILABLE_KEY):
        # Section unobservable: taxonomy UNKNOWN, never a verified-absent lie.
        return "UNKNOWN"
    audit = capability.get("audit")
    if audit == "AVAILABLE":
        return "READY"
    if audit == "NOT_INITIALIZED":
        return "NOT_INITIALIZED"
    return "UNKNOWN"


@dataclass(frozen=True)
class TruthField:
    """One externally visible runtime fact and its contradiction rules."""

    name: str
    source: str
    field_type: str
    valid_states: tuple[str, ...]
    derivation: str
    consumers: tuple[str, ...]
    unknown_semantics: str
    contradiction_rule: str
    resolver: Resolver | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# THE MATRIX.
# ---------------------------------------------------------------------------

MATRIX: Final[tuple[TruthField, ...]] = (
    TruthField(
        name="configured_mode",
        source="runtime snapshot runtime_mode section (config.execution.mode)",
        field_type="enum(LIVE, PAPER, SHADOW)",
        valid_states=("LIVE", "PAPER", "SHADOW", "UNKNOWN"),
        derivation="operator setting resolved at snapshot time; no runtime detection",
        consumers=("launcher", "engine boot", "CLI status", "/api/status"),
        unknown_semantics="state_taxonomy UNKNOWN when config absent; never fabricated as LIVE",
        contradiction_rule="configured_mode is a COMMAND, not an observation; it must never be reported as actual engine state (R1)",
        resolver=_resolve_configured_mode,
    ),
    TruthField(
        name="effective_mode",
        source="launcher decision log (config + guards resolved at boot)",
        field_type="enum(LIVE, PAPER, SHADOW)",
        valid_states=("LIVE", "PAPER", "SHADOW"),
        derivation="configured_mode after paper-guard / kill-switch resolution",
        consumers=("launcher logging", "audit"),
        unknown_semantics="unresolved guards at probe time = UNKNOWN, not PAPER",
        contradiction_rule="effective_mode diverging from configured_mode is EXPECTED_DIVERGENCE only when a named guard fired",
    ),
    TruthField(
        name="actual_engine_mode",
        source="LiveEngine observation registered via register_live_probe (engine._runtime_mode)",
        field_type="enum(LIVE, PAPER, SHADOW)",
        valid_states=("LIVE", "PAPER", "SHADOW"),
        derivation="engine-internal adapter binding observed at runtime, never config-derived",
        consumers=("/api/status", "/api/live/state", "CLI health", "dashboard"),
        unknown_semantics="engine not booted = NOT_INITIALIZED; probe absent = NO_PROBE_REGISTERED (explicit, never mapped to PAPER)",
        contradiction_rule="actual_engine_mode == effective_mode unless a guard fired; configured LIVE with actual PAPER is a SEVERE contradiction (BUG-211 family) (R1)",
        resolver=_resolve_actual_engine_mode,
        tags=("severe",),
    ),
    TruthField(
        name="readiness",
        source="LiveEngine readiness gate (warmup + model + provider)",
        field_type="enum(NOT_INITIALIZED, STARTING, READY, DEGRADED, STOPPING, STOPPED)",
        valid_states=("NOT_INITIALIZED", "STARTING", "READY", "DEGRADED", "STOPPING", "STOPPED"),
        derivation="engine lifecycle state machine, monotonic per boot",
        consumers=("health endpoint", "dashboard", "telegram"),
        unknown_semantics="process alive but engine object absent = NOT_INITIALIZED",
        contradiction_rule="readiness READY with missing model identity is impossible; data-less READY is forbidden (R2)",
    ),
    TruthField(
        name="health",
        source="live probe health key (web/server _build_health_section aggregation)",
        field_type="enum(HEALTHY, DEGRADED, UNAVAILABLE)",
        valid_states=("HEALTHY", "DEGRADED", "UNAVAILABLE"),
        derivation="aggregated subsystem health; DEGRADED survives partial recovery (BUG-213 semantics)",
        consumers=("/api/status", "CLI health", "dashboard banner"),
        unknown_semantics="no engine = UNAVAILABLE; probe absent = NO_PROBE_REGISTERED - never HEALTHY-by-default",
        contradiction_rule="health must never be synthesized as HEALTHY when its inputs are unavailable (no fake fallback as authoritative) (R3)",
        resolver=_resolve_health,
    ),
    TruthField(
        name="release_status",
        source="release/release_status.py",
        field_type="enum(NO_UPDATE, UPDATE_AVAILABLE, VERSION_UPDATE, OFFLINE, UNKNOWN, REVISION_AHEAD)",
        valid_states=(
            "NO_UPDATE",
            "UPDATE_AVAILABLE",
            "VERSION_UPDATE",
            "OFFLINE",
            "UNKNOWN",
            "REVISION_AHEAD",
        ),
        derivation="update channel check against recorded build identity",
        consumers=("CLI update", "dashboard update widget"),
        unknown_semantics="unreachable channel = OFFLINE (explicit), not NO_UPDATE",
        contradiction_rule="OFFLINE must never collapse into NO_UPDATE: absence of evidence is not evidence of absence",
    ),
    TruthField(
        name="version",
        source="release/metadata.get_version_info (build-stamped identity)",
        field_type="string (semver)",
        valid_states=("*",),
        derivation="packaged build info; unstamped dev keeps the declared pyproject version",
        consumers=("nexus version --json", "/api/status", "updater", "installer"),
        unknown_semantics="missing version falls back to 0.0.0 inside RuntimeVersionBlock - surface-level only, identity stays authoritative",
        contradiction_rule="web_bundle.application_version must equal identity version for the same build (R4)",
        resolver=_resolve_version,
    ),
    TruthField(
        name="commit_sha",
        source="release/metadata.get_version_info (build-stamped identity)",
        field_type="string|null (git SHA)",
        valid_states=("*", "NOT_RECORDED"),
        derivation="stamped at build; absent in dev/unstamped",
        consumers=("nexus version --json", "diagnostics", "update checks"),
        unknown_semantics="None + commit_source 'unavailable' + commit_status NOT_RECORDED is the explicit unstamped contract (CHG-0043)",
        contradiction_rule="a STAMPED build reporting commit=None is a contradiction; an unstamped build reporting None is correct (R4 family)",
        resolver=_resolve_commit,
    ),
    TruthField(
        name="feature_schema_build_target",
        source="runtime snapshot identity section (identity.feature_schema)",
        field_type="string (schema id)",
        valid_states=("scalp_v1", "scalp_v3", "scalp_v4"),
        derivation="schema the build/runtime targets (70D scalp_v3 canonical contract identity)",
        consumers=("models", "training", "runtime inference validator"),
        unknown_semantics="UNKNOWN when identity section unavailable; never defaulted to 50D",
        contradiction_rule="build target and live-active schema are DIFFERENT fields with different meanings; comparing them raw is a false contradiction (documented EXPECTED_DIVERGENCE) (R5)",
        resolver=_resolve_build_target_schema,
    ),
    TruthField(
        name="model_input_dimension",
        source="runtime snapshot model.registry_champion.feature_dimension",
        field_type="int (50|60|70)",
        valid_states=("50", "60", "70"),
        derivation="registry champion dimension, cross-checked against scaler and schema (MODEL_LOAD_GATE)",
        consumers=("inference_validator", "shadow70", "replay70"),
        unknown_semantics="no champion = NOT_CONFIGURED, not 0",
        contradiction_rule="dimension mismatch vs active schema is a BLOCK (FEATURE_CONTRACT_MISMATCH), never a silent coercion (R2 family)",
        resolver=_resolve_model_dimension,
    ),
    TruthField(
        name="model_identity",
        source="runtime snapshot model.registry_champion.feature_schema_id",
        field_type="string (model / schema id)",
        valid_states=("*", "NOT_CONFIGURED"),
        derivation="registry champion; serving alignment reported by snapshot model.alignment",
        consumers=("live_engine", "web", "telegram", "diagnostics"),
        unknown_semantics="NOT_CONFIGURED before first promotion; never a silent stale-champion fallback",
        contradiction_rule="50D metadata while runtime model is 70D is a SEVERE contradiction (kind confusion, not just mismatch) (R2 family)",
        resolver=_resolve_model_identity,
        tags=("severe",),
    ),
    TruthField(
        name="db_state",
        source="runtime snapshot database.capability (per-domain availability)",
        field_type="enum(NOT_INITIALIZED, READY, DEGRADED, FAILED)",
        valid_states=("NOT_INITIALIZED", "READY", "DEGRADED", "FAILED"),
        derivation="migration/manifest + capability classification (audit/news) at snapshot time",
        consumers=("health", "cli doctor", "web"),
        unknown_semantics="unprobed = UNKNOWN; shadow tables NOT_INITIALIZED by design (lazy ensure_schema, not a defect)",
        contradiction_rule="process alive with DB FAILED must show degraded health; engine reporting READY over a FAILED DB is SEVERE (R3 family)",
        resolver=_resolve_db_state,
        tags=("severe",),
    ),
    TruthField(
        name="shadow_state",
        source="shadow/shadow70/runtime.py + SHADOW70_ATTACH registry (CHG-0046 closure)",
        field_type="enum(NOT_CONFIGURED, ATTACHED, DETACHED, DEGRADED)",
        valid_states=("NOT_CONFIGURED", "ATTACHED", "DETACHED", "DEGRADED"),
        derivation="shadow runtime attach state; independent of primary model load",
        consumers=("web", "drift monitor", "research"),
        unknown_semantics="no shadow config = NOT_CONFIGURED (valid, non-failing state)",
        contradiction_rule="shadow ATTACHED while primary NOT_CONFIGURED is EXPECTED (shadow-first strategy), not a contradiction",
    ),
    TruthField(
        name="provider_state",
        source="market_data provider adapter + provider_gate (bounded streak semantics)",
        field_type="enum(NOT_CONFIGURED, CONNECTED, DEGRADED, UNAVAILABLE)",
        valid_states=("NOT_CONFIGURED", "CONNECTED", "DEGRADED", "UNAVAILABLE"),
        derivation="last provider health probe (NXConn-style 2-failure streak arms, success resets)",
        consumers=("health", "dashboard", "telegram"),
        unknown_semantics="probe never run = UNKNOWN; failure streak = UNAVAILABLE with reason",
        contradiction_rule="provider UNAVAILABLE while health HEALTHY is allowed ONLY while readiness != READY (data-less readiness is forbidden) (R2)",
    ),
    TruthField(
        name="web_bundle_feature_schema",
        source="release/versioning RuntimeVersionBlock.build() feature_schema (registry active info)",
        field_type="string (schema id) + is_active flag",
        valid_states=("scalp_v1", "scalp_v3", "scalp_v4"),
        derivation="LIVE ACTIVE contract advertised to doctor/update/diagnostic surfaces",
        consumers=("cli doctor", "updater", "web_bundle diagnostics", "release gate"),
        unknown_semantics="missing bundle = NOT_RECORDED; empty id with problems[] = DEGRADED payload",
        contradiction_rule="pairs with feature_schema_build_target under R5: identity reports the build TARGET schema, bundle reports the LIVE ACTIVE schema; equality is required only when no candidate schema is registered (R5)",
        resolver=_resolve_live_active_schema,
    ),
)

MATRIX_BY_NAME: Final[dict[str, TruthField]] = {f.name: f for f in MATRIX}

# Machine-checkable contradiction rules (id -> rule text). Tests assert every
# rule id is cited by at least one field and vice versa.
CONTRADICTION_RULES: Final[dict[str, str]] = {
    "R1": "configured_mode is a command, actual_engine_mode is an observation; they are distinct fields and conflating them is the BUG-211 class defect.",
    "R2": "readiness READY requires model identity + provider/warmup inputs present; data-less or model-less READY is forbidden, and dimension/kind mismatches block instead of coerce.",
    "R3": "health is aggregated from real inputs; a synthesized HEALTHY over unavailable inputs is a fake-fallback defect.",
    "R4": "version identity must be consistent across surfaces for the same build (web_bundle.application_version == identity.version; stamped builds must carry a commit).",
    "R5": "feature schema fields are semantics-aware: identity carries the build target, the web bundle carries the live active contract; raw equality checks between them are false contradictions, but both must name registered schema ids.",
}

EXPECTED_DIVERGENCES: Final[tuple[str, ...]] = (
    "identity.feature_schema (build target, e.g. scalp_v3) vs web_bundle feature_schema (live active, e.g. scalp_v1): different semantics, documented under R5.",
    "shadow ATTACHED while primary NOT_CONFIGURED: shadow-first strategy, expected.",
    "effective_mode != configured_mode when a named guard fired: expected with guard attribution.",
)


def field_names() -> list[str]:
    """Stable, ordered list of matrix field names (test-facing contract)."""
    return [f.name for f in MATRIX]


def validate_matrix_integrity() -> list[str]:
    """Return a list of internal inconsistencies (empty list = matrix sound)."""
    problems: list[str] = []
    seen: set[str] = set()
    cited_rules: set[str] = set()
    for f in MATRIX:
        if f.name in seen:
            problems.append(f"duplicate field name: {f.name}")
        seen.add(f.name)
        for rid in CONTRADICTION_RULES:
            if rid in f.contradiction_rule:
                cited_rules.add(rid)
    for rid in CONTRADICTION_RULES:
        if rid not in cited_rules:
            problems.append(f"rule {rid} defined but never cited by any field")
    for f in MATRIX:
        if "severe" in f.tags and f.resolver is None:
            problems.append(f"severe field {f.name} lacks a runtime resolver")
        if not f.valid_states:
            problems.append(f"field {f.name} has empty valid_states")
        if not f.consumers:
            problems.append(f"field {f.name} has no declared consumers")
    return problems


def resolve_field(name: str) -> tuple[str, object]:
    """Resolve a field's live value via its declared resolver.

    Returns (status, value); status is one of RESOLVED, UNKNOWN,
    NO_RESOLVER, NO_PROBE_REGISTERED, UNKNOWN_FIELD. Never raises.
    A resolver reporting the NO_PROBE sentinel is surfaced as the
    NO_PROBE_REGISTERED status, not as a resolved value.
    """
    f = MATRIX_BY_NAME.get(name)
    if f is None:
        return "UNKNOWN_FIELD", None
    if f.resolver is None:
        return "NO_RESOLVER", None
    try:
        value = f.resolver()
    except Exception as exc:
        return "UNKNOWN", f"resolver_error:{type(exc).__name__}"
    if value == _NO_PROBE:
        return "NO_PROBE_REGISTERED", _STATE_UNAVAILABLE
    if value in (None, _STATE_UNAVAILABLE):
        return "UNKNOWN", value
    return "RESOLVED", value


__all__ = [
    "CONTRADICTION_RULES",
    "EXPECTED_DIVERGENCES",
    "MATRIX",
    "MATRIX_BY_NAME",
    "TruthField",
    "clear_live_probe",
    "field_names",
    "register_live_probe",
    "resolve_field",
    "validate_matrix_integrity",
]
