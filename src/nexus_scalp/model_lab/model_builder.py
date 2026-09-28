"""Model Builder — explicit 50D/70D contract configuration and reproducibility.

The Neural Studio must let an operator build the EXACT model contract they want:

    50D  scalp_v1
    70D  scalp_v3

rather than a vague "N-dimensional" path that bypasses schema validation. This
module is the single source of truth for what the trainer can actually do: it
enumerates real ScalpNet capabilities, validates a full configuration against
the canonical feature schemas, and persists the exact reproducibility bundle
(dataset identity + schema + ordering + hyperparameters + seed + code version).

Nothing here is invented: every option maps to a field the trainer really writes
and every dimension check is delegated to ``features.schema_contract``, which is
the module the dataset builder, replay, inference validator and live engine all
derive from.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.features.schema import FEATURE_SCHEMAS, active_schema
from nexus_scalp.features.schema_contract import (
    canonical_feature_names,
    feature_schema_hash,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lab.model_builder")

REPO_ROOT = Path(__file__).resolve().parents[3]

# The two explicit model contracts. A generic "N-D" path would let a 70D model
# silently pair with a 50D scaler; these are the only legal dimensions and each
# binds its schema id, feature ordering and class contract.
SUPPORTED_DIMENSIONS: tuple[int, ...] = (50, 70)

DIMENSION_TO_SCHEMA_ID: dict[int, str] = {50: "scalp_v1", 70: "scalp_v3"}

# Real ``ScalpNet.__init__`` parameters (models/scalp_net.py). The builder never
# exposes a control that does not land on one of these.
SUPPORTED_HIDDEN_DIMS: tuple[int, ...] = (64, 96, 128, 192, 256)
SUPPORTED_HEADS: tuple[int, ...] = (2, 4, 8)
SUPPORTED_DROPOUT: tuple[float, ...] = (0.0, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5)
SUPPORTED_OPTIMIZERS: tuple[str, ...] = ("adamw", "adam", "sgd")
SUPPORTED_SCHEDULERS: tuple[str, ...] = ("none", "cosine", "step")
SUPPORTED_ACTIVATIONS: tuple[str, ...] = ("gelu",)

# Trainer capabilities actually implemented in model_lab.trainer. "Full train"
# and "fine-tune" are the only modes; a fine-tune run must name its base model.
SUPPORTED_TRAIN_MODES: tuple[str, ...] = ("full", "fine_tune")
SUPPORTED_DEVICES: tuple[str, ...] = ("cpu", "cuda")


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class FeatureSlotContract:
    """One slot of the canonical feature contract (Phase 5)."""

    index: int
    name: str
    family: str
    dtype: str
    required: bool
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureContract:
    """The backend-authoritative schema for one model contract (50D or 70D).

    The frontend must not keep a second hardcoded copy of the feature list:
    it reads this object through GET /api/model-studio/feature-contract.
    """

    schema_id: str
    dimension: int
    schema_hash: str
    sequence_length: int
    dtype: str
    normalization: str
    output_classes: int
    slots: tuple[FeatureSlotContract, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "dimension": self.dimension,
            "schema_hash": self.schema_hash,
            "sequence_length": self.sequence_length,
            "dtype": self.dtype,
            "normalization": self.normalization,
            "output_classes": self.output_classes,
            "feature_count": len(self.slots),
            "families": self._family_ranges(),
            "slots": [s.to_dict() for s in self.slots],
        }

    def _family_ranges(self) -> dict[str, dict[str, int]]:
        ranges: dict[str, dict[str, int]] = {}
        for slot in self.slots:
            if slot.family not in ranges:
                ranges[slot.family] = {"start": slot.index, "end": slot.index, "count": 1}
            else:
                r = ranges[slot.family]
                r["end"] = max(r["end"], slot.index)
                r["count"] += 1
        return ranges


def _family_for_index(index: int) -> str:
    if index < 50:
        return "BASE"
    if index < 60:
        return "NEWS"
    return "LIQUIDITY"


def _source_for_index(index: int) -> str:
    if index < 50:
        return "market_bars"
    if index < 60:
        return "news_engine"
    return "liquidity_governor"


def get_feature_contract(dimension: int) -> FeatureContract:
    """Resolve the canonical contract for 50D or 70D.

    Dimension is validated against the FEATURE SCHEMA REGISTRY — the same
    registry the live engine and the inference validator consult — so the
    contract shown in the UI can never drift from the contract the tensor
    pipeline enforces.
    """
    if dimension not in SUPPORTED_DIMENSIONS:
        raise ValueError(
            f"unsupported model contract dimension {dimension!r}; "
            f"Neural Studio supports only {SUPPORTED_DIMENSIONS} "
            "(50D scalp_v1 / 70D scalp_v3)"
        )
    schema_id = DIMENSION_TO_SCHEMA_ID[dimension]
    if not FEATURE_SCHEMAS.is_registered(schema_id):
        raise ValueError(
            f"schema {schema_id!r} is not registered in the feature schema registry"
        )
    schema = FEATURE_SCHEMAS.resolve(schema_id)
    if schema.dimension != dimension:
        raise ValueError(
            f"schema {schema_id!r} declares dimension {schema.dimension}, "
            f"expected {dimension}"
        )

    names = canonical_feature_names()
    if len(names) < dimension:
        raise ValueError(
            f"canonical feature names exhausted: registry holds {len(names)} "
            f"but contract {schema_id} requires {dimension}"
        )

    slots = tuple(
        FeatureSlotContract(
            index=i,
            name=str(names[i]),
            family=_family_for_index(i),
            dtype="float32",
            required=True,
            source=_source_for_index(i),
        )
        for i in range(dimension)
    )

    return FeatureContract(
        schema_id=schema_id,
        dimension=dimension,
        schema_hash=feature_schema_hash(schema_id),
        sequence_length=1,
        dtype="float32",
        normalization="zscore_clip5",
        output_classes=3,
        slots=slots,
    )


@dataclass
class ModelBuilderConfig:
    """A complete, persistable model configuration (Phase 4/45).

    Every field lands on a real trainer/ScalpNet parameter; the optional ones
    default to ``None`` so a saved-but-unset value is distinguishable from an
    explicit one when the config is replayed.
    """

    # --- identity / contract -------------------------------------------------
    config_name: str
    dimension: int
    schema_id: str = ""
    # --- dataset -------------------------------------------------------------
    dataset_path: str = ""
    dataset_sha256: str = ""
    dataset_rows: int = 0
    # --- architecture (real ScalpNet fields) ---------------------------------
    architecture: str = "ScalpNet"
    hidden_dim: int = 128
    num_heads: int = 4
    dropout_rate: float = 0.25
    output_classes: int = 3
    # --- training ------------------------------------------------------------
    train_mode: str = "full"
    epochs: int = 3
    batch_size: int = 256
    learning_rate: float = 5e-4
    weight_decay: float = 0.01
    optimizer: str = "adamw"
    scheduler: str = "none"
    seed: int = 42
    device: str = "cpu"
    early_stopping: bool = False
    early_stopping_patience: int = 3
    class_weights: list[float] | None = None
    # --- fine-tune -----------------------------------------------------------
    base_model_id: str = ""
    freeze_backbone: bool = False
    # --- sequence ------------------------------------------------------------
    sequence_length: int = 1
    # --- training-time OOS holdout -------------------------------------------
    oos_ratio: float = 0.2
    # --- reproducibility -----------------------------------------------------
    training_version: str = ""
    code_version: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def builder_options() -> dict[str, Any]:
    """Phase 4 / N2: only the options the implementation actually supports.

    Nothing fake: each entry is a real ``ScalpNet``/trainer capability, and the
    contracts come from the live feature schema registry.
    """
    contracts = {dim: get_feature_contract(dim).to_dict() for dim in SUPPORTED_DIMENSIONS}
    return {
        "status": "OK",
        "supported_dimensions": list(SUPPORTED_DIMENSIONS),
        "dimension_to_schema_id": DIMENSION_TO_SCHEMA_ID,
        "contracts": contracts,
        "architecture": {
            "name": "ScalpNet",
            # the real constructor signature
            "params": ["num_features", "num_classes", "hidden_dim", "num_heads", "dropout_rate"],
            "hidden_dim": list(SUPPORTED_HIDDEN_DIMS),
            "num_heads": list(SUPPORTED_HEADS),
            "dropout_rate": list(SUPPORTED_DROPOUT),
            "output_classes": [3],
        },
        "training": {
            "train_modes": list(SUPPORTED_TRAIN_MODES),
            "optimizers": list(SUPPORTED_OPTIMIZERS),
            "schedulers": list(SUPPORTED_SCHEDULERS),
            "activations": list(SUPPORTED_ACTIVATIONS),
            "devices": list(SUPPORTED_DEVICES),
            "epochs": {"min": 1, "max": 50},
            "batch_size": {"min": 16, "max": 2048},
            "learning_rate": {"min": 1e-6, "max": 1e-1},
            "weight_decay": {"min": 0.0, "max": 1.0},
            "seed": {"min": 0, "max": 2**31 - 1},
            "oos_ratio": {"min": 0.0, "max": 0.5},
            "early_stopping": True,
            "class_weights": True,
        },
        "defaults": {
            "hidden_dim": 128,
            "num_heads": 4,
            "dropout_rate": 0.25,
            "epochs": 3,
            "batch_size": 256,
            "learning_rate": 5e-4,
            "weight_decay": 0.01,
            "optimizer": "adamw",
            "scheduler": "none",
            "seed": 42,
            "device": "cpu",
            "train_mode": "full",
            "sequence_length": 1,
            "oos_ratio": 0.2,
        },
    }


# =============================================================================
# Validation (Phase 41: construction-time internal consistency)
# =============================================================================


@dataclass
class PreflightFinding:
    severity: str  # "error" | "warning"
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreflightReport:
    """Phase 7: dataset + schema + scaler compatibility verdict with a REASON."""

    compatible: bool
    schema_id: str
    dimension: int
    findings: list[PreflightFinding] = field(default_factory=list)
    dataset: dict[str, Any] | None = None
    contract: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "OK" if self.compatible else "INCOMPATIBLE",
            "compatible": self.compatible,
            "schema_id": self.schema_id,
            "dimension": self.dimension,
            "findings": [f.to_dict() for f in self.findings],
            "dataset": self.dataset,
            "contract": self.contract,
        }


def _dataset_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


def validate_builder_config(cfg: ModelBuilderConfig) -> list[PreflightFinding]:
    """Cross-field validation against the real schema and trainer bounds.

    This is the 50D/70D cross-contamination gate (Phase 40): the schema id must
    match the dimension, the feature count must be EXACT, and a fine-tune must
    name a base whose dimension equals the target dimension.
    """
    findings: list[PreflightFinding] = []

    # --- contract identity ---------------------------------------------------
    if cfg.dimension not in SUPPORTED_DIMENSIONS:
        findings.append(
            PreflightFinding(
                "error",
                "DIMENSION_UNSUPPORTED",
                f"dimension {cfg.dimension} is not a Neural Studio contract "
                f"(supported: {SUPPORTED_DIMENSIONS})",
            )
        )
        return findings

    expected_schema = DIMENSION_TO_SCHEMA_ID[cfg.dimension]
    if cfg.schema_id and cfg.schema_id != expected_schema:
        findings.append(
            PreflightFinding(
                "error",
                "SCHEMA_DIMENSION_MISMATCH",
                f"schema_id {cfg.schema_id!r} does not match dimension "
                f"{cfg.dimension} (contract requires {expected_schema!r})",
            )
        )

    schema = FEATURE_SCHEMAS.resolve(expected_schema)
    if schema.dimension != cfg.dimension:
        findings.append(
            PreflightFinding(
                "error",
                "SCHEMA_DIMENSION_DRIFT",
                f"schema {expected_schema!r} declares dimension {schema.dimension}, "
                f"config declares {cfg.dimension}",
            )
        )

    # --- architecture bounds (real ScalpNet constructor) ---------------------
    if cfg.hidden_dim not in SUPPORTED_HIDDEN_DIMS:
        findings.append(
            PreflightFinding(
                "error",
                "HIDDEN_DIM_UNSUPPORTED",
                f"hidden_dim {cfg.hidden_dim} not in {SUPPORTED_HIDDEN_DIMS}",
            )
        )
    if cfg.num_heads not in SUPPORTED_HEADS:
        findings.append(
            PreflightFinding(
                "error",
                "HEADS_UNSUPPORTED",
                f"num_heads {cfg.num_heads} not in {SUPPORTED_HEADS}",
            )
        )
    if not (SUPPORTED_DROPOUT[0] <= cfg.dropout_rate <= SUPPORTED_DROPOUT[-1]):
        findings.append(
            PreflightFinding(
                "error",
                "DROPOUT_OUT_OF_RANGE",
                f"dropout_rate {cfg.dropout_rate} outside "
                f"[{SUPPORTED_DROPOUT[0]}, {SUPPORTED_DROPOUT[-1]}]",
            )
        )
    if cfg.output_classes != 3:
        findings.append(
            PreflightFinding(
                "error",
                "OUTPUT_CLASSES_CONTRACT",
                f"output_classes {cfg.output_classes} violates the trained 3-class "
                "head contract (NO_TRADE/BUY/SELL)",
            )
        )

    # --- training bounds -----------------------------------------------------
    if not (1 <= cfg.epochs <= 50):
        findings.append(PreflightFinding("error", "EPOCHS_RANGE", "epochs must be 1..50"))
    if not (16 <= cfg.batch_size <= 2048):
        findings.append(
            PreflightFinding("error", "BATCH_RANGE", "batch_size must be 16..2048")
        )
    if not (1e-6 <= cfg.learning_rate <= 1e-1):
        findings.append(
            PreflightFinding("error", "LR_RANGE", "learning_rate must be 1e-6..1e-1")
        )
    if cfg.optimizer not in SUPPORTED_OPTIMIZERS:
        findings.append(
            PreflightFinding(
                "error",
                "OPTIMIZER_UNSUPPORTED",
                f"optimizer {cfg.optimizer!r} not in {SUPPORTED_OPTIMIZERS}",
            )
        )
    if cfg.scheduler not in SUPPORTED_SCHEDULERS:
        findings.append(
            PreflightFinding(
                "error",
                "SCHEDULER_UNSUPPORTED",
                f"scheduler {cfg.scheduler!r} not in {SUPPORTED_SCHEDULERS}",
            )
        )
    if cfg.device not in SUPPORTED_DEVICES:
        findings.append(
            PreflightFinding(
                "error",
                "DEVICE_UNSUPPORTED",
                f"device {cfg.device!r} not in {SUPPORTED_DEVICES}",
            )
        )
    if cfg.device == "cuda" and cfg.base_model_id == "":
        # Not an error — torch falls back to CPU — but the operator asked for
        # CUDA and must see that the box may not provide it. Warning, not a
        # hard failure (Phase 49: no unnecessary gates).
        findings.append(
            PreflightFinding(
                "warning",
                "CUDA_UNVERIFIED",
                "device=cuda requested; availability is checked at train time "
                "and falls back to cpu",
            )
        )

    # --- fine-tune contract (Phase 11) ---------------------------------------
    if cfg.train_mode == "fine_tune":
        if not cfg.base_model_id:
            findings.append(
                PreflightFinding(
                    "error",
                    "FINE_TUNE_NO_BASE",
                    "train_mode=fine_tune requires base_model_id",
                )
            )
        else:
            base_dim = _registry_dimension_for(cfg.base_model_id)
            if base_dim is not None and base_dim != cfg.dimension:
                findings.append(
                    PreflightFinding(
                        "error",
                        "FINE_TUNE_DIM_MISMATCH",
                        f"base model {cfg.base_model_id!r} is {base_dim}D; "
                        f"a {cfg.dimension}D fine-tune would contaminate the contract",
                    )
                )

    # --- OOS -----------------------------------------------------------------
    if not (0.0 <= cfg.oos_ratio < 0.5):
        findings.append(
            PreflightFinding("error", "OOS_RATIO_RANGE", "oos_ratio must be [0.0, 0.5)")
        )

    return findings


def _registry_dimension_for(model_id: str) -> int | None:
    """Read a registered model's dimension without importing the registry eagerly."""
    from nexus_scalp.model_generation.model_registry import get_model_registry

    rec = get_model_registry().get_model(model_id)
    return rec.dimension if rec else None


def preflight_dataset(
    cfg: ModelBuilderConfig, dataset_loader: Any = None
) -> PreflightReport:
    """Phase 7: is this dataset compatible with this contract, and WHY.

    ``dataset_loader`` is injected so tests can supply an in-memory frame; the
    production path resolves the path through the Model Studio safe-path
    inventory (see ``resolve_dataset_for_training``).
    """
    contract = get_feature_contract(cfg.dimension)
    findings = list(validate_builder_config(cfg))

    dataset_info: dict[str, Any] | None = None

    if not cfg.dataset_path:
        findings.append(
            PreflightFinding(
                "warning",
                "DATASET_NOT_SELECTED",
                "no dataset selected; training would fall back to a synthetic "
                "generator, which cannot reproduce a real-data model",
            )
        )
    else:
        try:
            if dataset_loader is not None:
                frame = dataset_loader(cfg.dataset_path)
            else:
                frame = _load_frame(Path(cfg.dataset_path))
            dataset_info = _describe_frame(frame, cfg.dataset_path)
        except Exception as exc:
            findings.append(
                PreflightFinding(
                    "error",
                    "DATASET_UNREADABLE",
                    f"dataset {cfg.dataset_path!r} could not be read: {exc}",
                )
            )
            return PreflightReport(
                compatible=False,
                schema_id=contract.schema_id,
                dimension=cfg.dimension,
                findings=findings,
            )

        if dataset_info is not None:
            if dataset_info["row_count"] < 20:
                findings.append(
                    PreflightFinding(
                        "error",
                        "DATASET_TOO_SMALL",
                        f"dataset has {dataset_info['row_count']} rows; the trainer "
                        "needs at least 20 to split train/val/OOS",
                    )
                )
            if not dataset_info["has_ohlc"]:
                findings.append(
                    PreflightFinding(
                        "error",
                        "DATASET_NO_OHLC",
                        "dataset has no OHLC columns; the feature builder cannot "
                        "construct the base feature block",
                    )
                )

    errors = [f for f in findings if f.severity == "error"]
    return PreflightReport(
        compatible=len(errors) == 0,
        schema_id=contract.schema_id,
        dimension=cfg.dimension,
        findings=findings,
        dataset=dataset_info,
        contract=contract.to_dict(),
    )


def _load_frame(path: Path) -> Any:
    import polars as pl

    if path.suffix.lower() == ".parquet":
        return pl.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pl.read_csv(path)
    raise ValueError(f"unsupported dataset type: {path.suffix}")


def _describe_frame(frame: Any, path: str) -> dict[str, Any]:
    columns = [str(c) for c in frame.columns]
    height = int(frame.height)
    return {
        "path": path,
        "row_count": height,
        "column_count": len(columns),
        "columns": columns,
        "has_ohlc": any(
            col in columns for col in ("open", "high", "low", "close", "current_price")
        ),
    }


def resolve_dataset_for_training(dataset_path: str) -> Path | None:
    """Resolve an operator-named dataset through the Model Studio safe inventory.

    The trainer must never construct a path from request input; it reuses the
    studio's server-derived inventory so the read is confined to an allowlisted
    root (same containment contract as every other studio read).
    """
    if not str(dataset_path or "").strip():
        return None
    # Local import: web module is a heavier dependency than model_lab needs.
    from nexus_scalp.web.model_studio_routes import _resolve_requested_dataset

    return _resolve_requested_dataset(dataset_path)


# =============================================================================
# Persistence (Phase 45: SAVE CONFIGURATION before training)
# =============================================================================


@dataclass
class SavedBuilderConfig:
    config_id: str
    config_name: str
    dimension: int
    schema_id: str
    config_json: str
    config_sha256: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["config"] = json.loads(self.config_json)
        return d


class BuilderConfigStore:
    """SQLite store for saved model-builder configurations.

    Lives in the same artifacts root as the model registry (``artifacts/models.db``
    by default) so the registry stays the single authoritative inventory and no
    competing store appears. The tables are NEW and additive — the registry's own
    schema is untouched.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = REPO_ROOT / "artifacts" / "models.db"
        self._db_path = Path(db_path) if str(db_path) != ":memory:" else db_path
        self._lock = threading.RLock()
        self._mem_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        assert isinstance(self._db_path, Path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _close(self, conn: sqlite3.Connection) -> None:
        if conn is not self._mem_conn:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS model_builder_configs (
                            config_id TEXT PRIMARY KEY,
                            config_name TEXT NOT NULL,
                            dimension INTEGER NOT NULL,
                            schema_id TEXT NOT NULL,
                            config_json TEXT NOT NULL,
                            config_sha256 TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        );
                        """
                    )
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_builder_cfg_name "
                        "ON model_builder_configs(config_name);"
                    )
            finally:
                self._close(conn)

    @staticmethod
    def _hash_config(payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def save(self, cfg: ModelBuilderConfig) -> SavedBuilderConfig:
        payload = cfg.to_dict()
        payload_sha = self._hash_config(payload)
        config_id = f"cfg_{int(datetime.now(UTC).timestamp())}_{payload_sha[:8]}"
        row = SavedBuilderConfig(
            config_id=config_id,
            config_name=cfg.config_name,
            dimension=cfg.dimension,
            schema_id=cfg.schema_id or DIMENSION_TO_SCHEMA_ID.get(cfg.dimension, ""),
            config_json=json.dumps(payload, sort_keys=True, default=str),
            config_sha256=payload_sha,
            created_at=_now(),
        )
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO model_builder_configs (
                            config_id, config_name, dimension, schema_id,
                            config_json, config_sha256, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(config_id) DO UPDATE SET
                            config_name=excluded.config_name,
                            dimension=excluded.dimension,
                            schema_id=excluded.schema_id,
                            config_json=excluded.config_json,
                            config_sha256=excluded.config_sha256;
                        """,
                        (
                            row.config_id,
                            row.config_name,
                            row.dimension,
                            row.schema_id,
                            row.config_json,
                            row.config_sha256,
                            row.created_at,
                        ),
                    )
                return row
            finally:
                self._close(conn)

    def list_configs(self, limit: int = 100) -> list[SavedBuilderConfig]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM model_builder_configs
                    ORDER BY created_at DESC LIMIT ?;
                    """,
                    (limit,),
                ).fetchall()
                return [self._row_to_config(r) for r in rows]
            finally:
                self._close(conn)

    def get_config(self, config_id: str) -> SavedBuilderConfig | None:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM model_builder_configs WHERE config_id = ?;",
                    (config_id,),
                ).fetchone()
                return self._row_to_config(row) if row else None
            finally:
                self._close(conn)

    @staticmethod
    def _row_to_config(row: sqlite3.Row) -> SavedBuilderConfig:
        return SavedBuilderConfig(
            config_id=str(row["config_id"]),
            config_name=str(row["config_name"]),
            dimension=int(row["dimension"]),
            schema_id=str(row["schema_id"]),
            config_json=str(row["config_json"]),
            config_sha256=str(row["config_sha256"]),
            created_at=str(row["created_at"]),
        )


_STORE: BuilderConfigStore | None = None
_STORE_LOCK = threading.Lock()


def get_builder_config_store() -> BuilderConfigStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = BuilderConfigStore()
    return _STORE
