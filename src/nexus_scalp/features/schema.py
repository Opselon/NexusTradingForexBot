"""
Feature Schema Registry — Future-Dimension Infrastructure
=========================================================
Single source of truth for "what does a feature vector look like?".

WHY THIS EXISTS
---------------
The engine's canonical live contract is `scalp_v1` / 50 dimensions and that
contract is UNCHANGED by this module. What changes is that the dimension is no
longer a magic number scattered across the trainer, the live engine, the model
factory and the persistence layer: every one of those reads it from here.

That makes the documented roadmap (50D -> 60D -> 128D -> 350D) an ADDITIVE
registry entry plus a retrained artifact, instead of a repo-wide hunt for
hard-coded `50`s.

INVARIANTS
----------
1. `ACTIVE_SCHEMA_ID` is the ONLY place the live dimension is declared.
2. Column naming stays `feat_0 .. feat_{n-1}` for every schema, so the existing
   training frames, Polars selections and scaler files keep working verbatim.
3. Registering a new schema NEVER mutates an existing one. Historical
   experiences and old model artifacts keep resolving against the schema they
   were produced under (see `nexus_scalp.experience.models.FeatureSnapshot`).
4. `resolve()` is strict: an unknown schema id raises rather than silently
   defaulting to 50D, because a silent default would mean training a model on a
   dimension the runtime does not actually produce.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.features.schema")


@dataclass(frozen=True)
class FeatureSchema:
    """
    Immutable description of one feature-vector contract.

    Attributes:
        schema_id: Stable identity, e.g. "scalp_v1".
        dimension: Number of float features in the vector.
        description: Human summary of what the schema adds.
        is_active: True for the single schema the live engine currently emits.
        supersedes: Schema id this one extends, for lineage/audit.
    """

    schema_id: str
    dimension: int
    description: str = ""
    is_active: bool = False
    supersedes: str = ""

    def __post_init__(self) -> None:
        if self.dimension <= 0:
            raise ValueError(f"Feature schema {self.schema_id} has invalid dimension")

    @property
    def columns(self) -> tuple[str, ...]:
        """Canonical ordered training column names (`feat_0 .. feat_{n-1}`)."""
        return tuple(f"feat_{i}" for i in range(self.dimension))

    def validate_vector(self, values: list[float], context: str = "") -> list[float]:
        """
        Validates a feature vector against THIS schema.

        Raises:
            ValueError: when the length does not match the declared dimension.
                Fail-loud is deliberate: a silently truncated or padded vector
                would corrupt both inference and every stored experience.
        """
        if len(values) != self.dimension:
            raise ValueError(
                f"Feature contract violation{f' in {context}' if context else ''}: "
                f"schema={self.schema_id} expected {self.dimension} features, got {len(values)}"
            )
        return values

    def validate_columns(self, feature_cols: list[str], context: str = "") -> None:
        """Validates a training column list against this schema's arity."""
        if len(feature_cols) != self.dimension:
            raise ValueError(
                f"Feature contract violation{f' in {context}' if context else ''}: "
                f"schema={self.schema_id} expected {self.dimension} feature columns, "
                f"got {len(feature_cols)}"
            )


#: Currently emitted by `ScalpFeatureEngine.to_tensor_input()`. Changing this is
#: the single switch that migrates the live contract to a wider schema.
ACTIVE_SCHEMA_ID: str = "scalp_v1"


@dataclass
class FeatureSchemaRegistry:
    """
    Append-only registry of feature contracts.

    Deliberately NOT a second model registry: it describes feature geometry
    only. Model identity/provenance lives in
    `nexus_scalp.experience.provenance.ModelRegistry`.
    """

    _schemas: dict[str, FeatureSchema] = field(default_factory=dict)

    def register(self, schema: FeatureSchema, replace: bool = False) -> FeatureSchema:
        """
        Registers a schema. Re-registering an existing id requires `replace=True`
        so a typo cannot silently redefine the live contract.
        """
        existing = self._schemas.get(schema.schema_id)
        if existing is not None and not replace:
            if existing.dimension != schema.dimension:
                raise ValueError(
                    f"Refusing to redefine schema {schema.schema_id}: "
                    f"dimension {existing.dimension} -> {schema.dimension}"
                )
            return existing
        self._schemas[schema.schema_id] = schema
        return schema

    def resolve(self, schema_id: str | None = None) -> FeatureSchema:
        """
        Resolves a schema id, defaulting to the ACTIVE schema.

        Raises:
            KeyError: for an unknown id (never falls back to a guess).
        """
        target = schema_id or ACTIVE_SCHEMA_ID
        schema = self._schemas.get(target)
        if schema is None:
            raise KeyError(
                f"Unknown feature schema '{target}'. Registered: {sorted(self._schemas)}"
            )
        return schema

    @property
    def active(self) -> FeatureSchema:
        """The schema the live engine currently emits."""
        return self.resolve(ACTIVE_SCHEMA_ID)

    def list_schemas(self) -> list[FeatureSchema]:
        """All registered schemas ordered by dimension."""
        return sorted(self._schemas.values(), key=lambda s: s.dimension)

    def is_registered(self, schema_id: str) -> bool:
        return schema_id in self._schemas


#: Process-wide registry. Future schemas are added here as one line each.
FEATURE_SCHEMAS = FeatureSchemaRegistry()

FEATURE_SCHEMAS.register(
    FeatureSchema(
        schema_id="scalp_v1",
        dimension=50,
        description=(
            "Canonical production contract: price action, wick anatomy, swing "
            "structure, sessions, lags, ICT/SMC signals, Ichimoku, dynamic S/R, "
            "multi-timeframe context and institutional OB validation."
        ),
        is_active=True,
    )
)

# ---------------------------------------------------------------------------
# FORWARD-DECLARED SCHEMAS (not active, no artifact yet)
# ---------------------------------------------------------------------------
# These exist so the memory layer, the trainer and the model factory can already
# be exercised against wider geometries in tests, and so a future migration is a
# config switch plus a retrain rather than a refactor. Declaring them does NOT
# change what the live engine emits.
FEATURE_SCHEMAS.register(
    FeatureSchema(
        schema_id="scalp_v2",
        dimension=60,
        description=(
            "scalp_v1 + 10 TASK-5 causal augmentation features "
            "(regime_compression, momentum_5_atr, wick_imbalance_5, volume_z_5, "
            "range_z_5, clv_avg_5, session_phase_enc, price_acceleration, "
            "atr_trend_ratio, direction_bias_8) — produced by "
            "features/schema_augment.compute_60d_extras. Candidate-only; the "
            "ACTIVE live contract remains scalp_v1."
        ),
        supersedes="scalp_v1",
    )
)
FEATURE_SCHEMAS.register(
    FeatureSchema(
        schema_id="scalp_v3",
        dimension=70,
        description=(
            "CANONICAL 70D contract (TASK-03-70D-PARITY): Base 50D (scalp_v1, "
            "indices 0..49) + News 10D (canonical news_context_v1 first-10, "
            "indices 50..59) + Liquidity 10D (features/liquidity_engine "
            "as_vector order, indices 60..69). The single source of truth for "
            "the 70D vector is features/schema_contract.py (schema hash, family "
            "layout, validation). Candidate-only; the ACTIVE live contract "
            "remains scalp_v1. Supersedes the forward-declared 350D research "
            "contract which never materialized (no 350D artifact ever existed)."
        ),
        supersedes="scalp_v1",
    )
)
FEATURE_SCHEMAS.register(
    FeatureSchema(
        schema_id="scalp_v4",
        dimension=70,
        description=(
            "scalp_v1 50D Base + 10 slot-50..59 family features (TASK-5 "
            "scalp_v2 momentum extras or TASK-1 liquidity at 50..59 under "
            "their own schema ids) + 10 TASK-01-60D-LIQUIDITY Liquidity "
            "Intelligence features (bsl_distance_atr, ssl_distance_atr, "
            "eqh_strength, eql_strength, htf_liquidity_score, "
            "internal_liquidity_distance, external_liquidity_distance, "
            "liquidity_confluence, liquidity_sweep_state, "
            "post_sweep_displacement) at indices 60..69. The 70D integration "
            "contract: BASE 0..49 | FAMILY 50..59 | LIQUIDITY 60..69 "
            "(TASK-02-70D-INTEGRATION). Candidate-only; the ACTIVE live "
            "contract remains scalp_v1."
        ),
        supersedes="scalp_v1",
    )
)
FEATURE_SCHEMAS.register(
    FeatureSchema(
        schema_id="scalp_liquidity_v1",
        dimension=60,
        description=(
            "scalp_v1 + 10 TASK-01-60D-LIQUIDITY causal Liquidity Intelligence "
            "features (bsl_distance_atr, ssl_distance_atr, eqh_strength, "
            "eql_strength, htf_liquidity_score, internal_liquidity_distance, "
            "external_liquidity_distance, liquidity_confluence, "
            "liquidity_sweep_state, post_sweep_displacement) — produced by "
            "features/liquidity_engine.compute_liquidity_features. Candidate-only; "
            "the ACTIVE live contract remains scalp_v1. Distinct from scalp_v2 "
            "(TASK-5 momentum augmentation) which keeps indices 50..59; "
            "scalp_liquidity_v1 defines its OWN 10D semantics at indices 50..59 "
            "under a separate schema id."
        ),
        supersedes="scalp_v1",
    )
)


def active_schema() -> FeatureSchema:
    """Convenience accessor for the live feature contract."""
    return FEATURE_SCHEMAS.active


def active_dimension() -> int:
    """Dimensionality of the live feature contract."""
    return FEATURE_SCHEMAS.active.dimension


def active_columns() -> tuple[str, ...]:
    """Ordered training column names for the live feature contract."""
    return FEATURE_SCHEMAS.active.columns


def schema_for_dimension(dimension: int) -> FeatureSchema | None:
    """
    Reverse lookup used when reading legacy artifacts that recorded only a width.

    Returns None when no registered schema has that dimension, so the caller can
    decide whether to refuse the artifact rather than misattribute it.
    """
    for schema in FEATURE_SCHEMAS.list_schemas():
        if schema.dimension == dimension:
            return schema
    return None
