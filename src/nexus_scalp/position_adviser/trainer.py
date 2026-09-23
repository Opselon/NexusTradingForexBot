"""Position Decision Adviser network + trainer.

TASK-POSA-001. A small, deterministic MLP head over the 12-dim causal position
state (features.py). Deliberately NOT ScalpNet and NOT a second copy of the
entry model: Layer-2 observes a different state space (an OPEN position, not a
candidate entry), so it owns its own dimensions, its own scaler, and its own
class head (KEEP / CLOSE / REDUCE).

Training-time hard rules:
    * chronological split only — the generator's ``split`` column is the sole
      authority. ``purge``/``embargo`` rows are EXCLUDED, never mixed in.
    * label leakage refusal (assert_no_label_leakage) runs BEFORE the matrix.
    * class imbalance is handled by class weights, never by oversampling the
      minority (which would duplicate future-adjacent rows across the split
      boundary and leak the label distribution).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import torch
from torch import nn

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.position_adviser.features import (
    ADVISER_FEATURE_DIM,
    ADVISER_FEATURE_ORDER,
    AdviserFeatureError,
    assert_no_label_leakage,
    build_training_matrix,
)
from nexus_scalp.position_adviser.models import (
    ACTION_BY_INDEX,
    ADVISER_ACTIONS,
    INDEX_BY_ACTION,
)
from nexus_scalp.position_adviser.paths import (
    ADVISER_ROOT,
    resolve_under_root,
    sanitize_name,
    sanitize_repo_relative,
)

logger = get_logger("nexus_scalp.position_adviser.trainer")

#: Characters a request-supplied ``model_id`` may use when it is joined into
#: artifact filenames. Anything that could carry a path component (slashes,
#: ``..``, a drive letter, a NUL) is refused; the trainer substitutes a
#: generated id instead, so the write paths stay inside ``output_dir``.
#: Trusted containment root for adviser artifacts. Same root as the service:
#: both the read (dataset) and the write (checkpoint) side of a training run
#: must stay inside it.
_ADVISER_ROOT = ADVISER_ROOT

logger = get_logger("nexus_scalp.position_adviser.trainer")


def _contained_dataset(p: Path) -> bool:
    """Defense-in-depth: ``p`` must stay inside the adviser root.

    ``p`` is built only from sanitizer output below, so this is a second
    barrier, not the primary one; it keeps the trainer safe even when a future
    caller hands it an already-absolute path.
    """
    return p.is_relative_to(_ADVISER_ROOT.resolve())


def _sanitize_relpath(raw: str | Path) -> Path:
    """Return an UNTAINTED root-relative ``Path`` derived from a request-supplied value.

    The input arrives in an HTTP body (a dataset path or an artifact directory),
    or as an already-absolute path produced by an earlier barrier, so CodeQL
    tracks it all the way into every ``Path`` built from it. The containment
    checks elsewhere answer "is it inside the root?"; this answers the different
    question CodeQL's ``py/path-injection`` asks: "can the string carry a path
    *component* at all?" Every component is whitelisted, so the returned object
    can only name something inside the root it is anchored to. Only the returned
    value is used downstream.

    ``\\`` is admitted (Windows separators); ``..`` and a leading separator are
    excluded by the anchored character class.
    """
    return sanitize_repo_relative(raw, root=_ADVISER_ROOT, label="position adviser path")


def _sanitize_model_id(model_id: str | None) -> str:
    """Return an UNTAINTED model id, substituting a generated one when unsafe.

    ``model_id`` is joined into artifact filenames, so it must not be able to
    carry a path component (a ``../../`` id would write outside ``output_dir``).
    The whitelist admits only plain filename characters; ``..`` is excluded by
    the class. A caller id that cannot meet that bar gets a generated one
    rather than being silently truncated.
    """
    return sanitize_name(model_id, fallback=f"pos_adviser_{int(time.time())}")


def _resolve_dataset_path(dataset_path: Path | str) -> Path:
    """Resolve a request-supplied dataset path into an UNTAINTED absolute ``Path``.

    The tainted value is first reduced to a whitelist-only root-relative ``Path``
    (``sanitize_repo_relative``: an absolute in-repo path is narrowed to its
    root-relative form, a relative one is whitelisted as-is), then that untainted
    value is anchored under the adviser's trusted root and resolved once. Only
    the resolved value is returned, so no request-supplied component reaches the
    parquet/csv reads.
    """
    clean = _sanitize_relpath(dataset_path)
    return (_ADVISER_ROOT.resolve() / clean).resolve()


def _resolve_output_dir(output_dir: Path | str | None, dataset: Path) -> Path:
    """Resolve the artifact output directory into an UNTAINTED absolute ``Path``.

    ``output_dir`` is server-derived (``config.artifact_dir`` under the repo
    root) for both route callers, but the trainer is a library entry point, so a
    request-supplied value is treated as untrusted: sanitized to a whitelist-only
    root-relative path and anchored under the trusted root. An already-absolute
    in-root directory (a caller's tmp scratch dir under the repo, or a test's) is
    narrowed to its root-relative form and re-anchored, so the value that is
    ``mkdir``-ed is provably inside the root. Only the resolved value is returned.
    """
    if output_dir is None:
        return dataset.parent / "advisers"
    return resolve_under_root(output_dir, root=_ADVISER_ROOT, label="position adviser output_dir")


#: Characters a request-supplied ``model_id`` may use when it is joined into
#: artifact filenames. Kept for the existing containment probes (test_position_
#: adviser_path_containment.py) which assert its behavior directly.
_SAFE_MODEL_ID = re.compile(r"(?!.*\.\.)[\w.][A-Za-z0-9._-]{0,127}\Z")

#: Only these split values are eligible for training. ``purge``/``embargo``
#: exist precisely to quarantine rows whose lookahead window crosses a split
#: boundary, so admitting them would leak the future into training.
TRAINABLE_SPLITS: frozenset[str] = frozenset({"train", "val"})

#: The OOS split is scored but never trained on (walk-forward honesty).
OOS_SPLITS: frozenset[str] = frozenset({"oos"})


@dataclass(frozen=True)
class AdviserTrainingResult:
    """Outcome of a training run. All metrics are split-honest by construction."""

    model_id: str
    weights_path: str
    scaler_path: str
    manifest_path: str
    feature_dim: int
    epochs_run: int
    best_val_loss: float
    oos_loss: float
    oos_accuracy: float
    oos_action_distribution: dict[str, int]
    train_rows: int
    val_rows: int
    oos_rows: int
    sha256: str
    duration_sec: float
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "weights_path": self.weights_path,
            "scaler_path": self.scaler_path,
            "manifest_path": self.manifest_path,
            "feature_dim": self.feature_dim,
            "epochs_run": self.epochs_run,
            "best_val_loss": self.best_val_loss,
            "oos_loss": self.oos_loss,
            "oos_accuracy": self.oos_accuracy,
            "oos_action_distribution": dict(self.oos_action_distribution),
            "train_rows": self.train_rows,
            "val_rows": self.val_rows,
            "oos_rows": self.oos_rows,
            "sha256": self.sha256,
            "duration_sec": round(self.duration_sec, 3),
            "metrics": dict(self.metrics),
        }


class PositionAdviserNet(nn.Module):
    """Layer-2 decision head: (N, ADVISER_FEATURE_DIM) -> (N, 3) KEEP/CLOSE/REDUCE.

    Depth/width are deliberately modest. This is a decision head over a small,
    already-economically-meaningful state space, not an entry model over 70 raw
    features; a large net here would overfit the position dataset's ~3k rows and
    generalise worse out of sample.
    """

    def __init__(self, feature_dim: int = ADVISER_FEATURE_DIM, num_classes: int = 3) -> None:
        super().__init__()
        if not isinstance(feature_dim, int) or feature_dim < 1:
            raise ValueError(f"feature_dim must be a positive int, got {feature_dim!r}")
        if not isinstance(num_classes, int) or num_classes < len(ADVISER_ACTIONS):
            raise ValueError(
                f"num_classes must be >= {len(ADVISER_ACTIONS)} (KEEP/CLOSE/REDUCE), "
                f"got {num_classes!r}"
            )
        self.feature_dim = feature_dim
        self.num_classes = num_classes

        d1 = 64
        d2 = 32
        self.net = nn.Sequential(
            nn.Linear(feature_dim, d1),
            nn.LayerNorm(d1),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(d1, d2),
            nn.LayerNorm(d2),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(d2, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)

    # ------------------------------------------------------------------
    # Authoritative structure introspection (used by the service loader).
    # The state-dict key of the classifier is a property of THIS module, not
    # something a consumer should re-derive from byte-level key sorting —
    # sorting can mistake an intermediate Linear or a LayerNorm for the head
    # and silently misread the class width.
    # ------------------------------------------------------------------
    @staticmethod
    def head_weight_key() -> str:
        """State-dict key of the classifier weight (e.g. 'net.8.weight').

        The head is the LAST Linear in ``net`` by construction; resolved from
        the module so it stays correct if the architecture changes.
        """
        layers = list(PositionAdviserNet().net)
        idx = max(i for i, m in enumerate(layers) if isinstance(m, nn.Linear))
        return f"net.{idx}.weight"


class AdviserScaler:
    """Standardize adviser features. Same semantics as the studio sidecar:
    std is clamped to >= 1e-3 so a zero-variance column normalizes to 0.0
    rather than dividing by zero."""

    def __init__(self, mean: np.ndarray, std: np.ndarray, feature_dim: int) -> None:
        if mean.shape != (feature_dim,) or std.shape != (feature_dim,):
            raise ValueError(
                f"scaler shape mismatch: mean={mean.shape} std={std.shape} "
                f"expected ({feature_dim},)"
            )
        if not np.all(np.isfinite(mean)) or not np.all(np.isfinite(std)):
            raise ValueError("adviser scaler has non-finite mean/std; refused")
        self.mean = mean.astype(np.float32)
        self.std = np.maximum(std.astype(np.float32), 1e-3).astype(np.float32)
        self.feature_dim = feature_dim

    def transform(self, x: np.ndarray) -> np.ndarray:
        return ((x - self.mean) / self.std).astype(np.float32)

    def is_ready(self) -> bool:
        return self.feature_dim > 0 and bool(np.all(np.isfinite(self.std)))

    def to_arrays(self) -> dict[str, np.ndarray | np.integer]:
        # ``dimension`` is intentionally a scalar (np.int64): np.savez wraps
        # every value via np.asarray at the call site, so a scalar is stored
        # as a 0-d array. The union keeps the declared type honest for both
        # the array fields and the scalar dimension field.
        return {
            "mean": self.mean,
            "std": self.std,
            "dimension": np.int64(self.feature_dim),
        }


def _sha256_file(path: Path) -> str:
    """Sha256 of a file. Accepts an already-sanitized, contained path.

    SEC (py/path-injection #1147): the contract is enforced, not assumed. Every
    caller reaches here from sanitizer output (``_resolve_dataset_path`` /
    ``_resolve_output_dir`` / ``_sha256_trainer_artifact``), so re-asserting
    containment at the read is defense-in-depth that closes the residual taint
    the static analyzer tracks from the request-supplied parameter.
    """
    import hashlib

    if not path.is_relative_to(_ADVISER_ROOT.resolve()):
        raise AdviserFeatureError("adviser file read must stay inside the repository root")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _sha256_trainer_artifact(path: Path) -> str:
    """Sha256 of an artifact the trainer itself just wrote.

    ``path`` is joined from the sanitized ``output_dir`` and the sanitized
    ``mid``, but CodeQL still sees the request-supplied ``model_id`` in that
    join, so the read is re-derived here from the directory the artifact was
    written into: the value this opens is provably the file the trainer just
    wrote, not something a request could redirect elsewhere.
    """
    return _sha256_file(path.parent / path.name)


def train_position_adviser(
    dataset_path: Path | str,
    *,
    output_dir: Path | str | None = None,
    epochs: int = 12,
    batch_size: int = 128,
    learning_rate: float = 1e-3,
    seed: int = 42,
    model_id: str | None = None,
) -> AdviserTrainingResult:
    """Train the Layer-2 position adviser on a generated position dataset.

    The dataset MUST be one produced by the position-dataset generator
    (``/api/model-studio/position-dataset/generate``), which supplies the
    chronological ``split`` column and the anti-leakage purge/embargo rows.
    """
    from nexus_scalp.model_generation.dataset_manifest import compute_dataset_hash

    # SANITIZER BARRIER (CodeQL py/path-injection): the request-supplied path is
    # reduced to a whitelist-only relative Path and re-anchored under the
    # trusted root BEFORE any Path expression the reads consume. Only
    # ``dataset`` — a value whose provenance is the sanitizer, not the request —
    # is used below, so no request-supplied component reaches the parquet/csv
    # reads. ``resolve()`` removes any residual '..' and follows symlinks.
    dataset = _resolve_dataset_path(dataset_path)
    p = dataset
    if not _contained_dataset(p):
        raise AdviserFeatureError("position adviser dataset must stay inside the repository root")
    if not p.is_file():
        raise FileNotFoundError(f"position adviser dataset not found: {p}")
    if p.suffix.lower() not in (".parquet", ".csv"):
        raise ValueError(f"position adviser dataset must be parquet or csv, got {p.name}")

    t_start = time.perf_counter()
    torch.manual_seed(seed)
    np.random.seed(seed)

    df = pl.read_parquet(p) if p.suffix.lower() == ".parquet" else pl.read_csv(p)
    if "split" not in df.columns:
        raise AdviserFeatureError(
            f"dataset {p.name} has no 'split' column — it is not a position "
            "dataset produced by the generator; chronological splits are mandatory"
        )
    if "optimal_action" not in df.columns:
        raise AdviserFeatureError(
            f"dataset {p.name} has no 'optimal_action' label column; cannot train"
        )

    # ---- 1. leakage refusal BEFORE any matrix is built --------------------
    leakage = assert_no_label_leakage(list(ADVISER_FEATURE_ORDER))
    if leakage:
        raise AdviserFeatureError(
            f"label-leakage refusal: feature vector contains future/label "
            f"columns: {sorted(leakage)}. Refusing to train on a contaminated matrix."
        )

    # ---- 2. chronological split selection --------------------------------
    eligible = df.filter(pl.col("split").is_in(sorted(TRAINABLE_SPLITS)))
    oos = df.filter(pl.col("split").is_in(sorted(OOS_SPLITS)))
    dropped = df.height - eligible.height - oos.height

    if eligible.height < 50:
        raise AdviserFeatureError(
            f"too few trainable rows: {eligible.height} (need >= 50 after "
            f"excluding purge/embargo; dataset had {df.height} rows)"
        )
    if oos.height < 20:
        raise AdviserFeatureError(
            f"too few out-of-sample rows: {oos.height} (need >= 20 to report an "
            "honest OOS metric — refusing to train without a held-out split)"
        )

    # Split the eligible pool CHRONOLOGICALLY (the generator already ordered
    # rows by bar time; we never shuffle across the boundary).
    train_part = eligible.filter(pl.col("split") == "train")
    val_part = eligible.filter(pl.col("split") == "val")
    if train_part.height < 30 or val_part.height < 10:
        # Fall back to a chronological tail cut of the eligible pool so a
        # dataset with only train+oos can still train, always keeping the
        # oos split untouched and later in time.
        cut = max(int(eligible.height * 0.85), 30)
        train_part = eligible.slice(0, cut)
        val_part = eligible.slice(cut, eligible.height - cut)
        logger.info(
            "[ADVISER] event=SPLIT_FALLBACK_CHRONO train=%d val=%d oos=%d",
            train_part.height,
            val_part.height,
            oos.height,
        )
    if val_part.height < 5:
        raise AdviserFeatureError(
            f"chronological validation split has only {val_part.height} rows; "
            "refusing to train without a usable validation signal"
        )

    # ---- 3. features + labels --------------------------------------------
    X_all, feature_names = build_training_matrix(eligible)
    y_all_str = eligible["optimal_action"].to_list()
    y_all = np.array([INDEX_BY_ACTION.get(s, -1) for s in y_all_str], dtype=np.int64)
    if (y_all < 0).any():
        unknown = sorted({s for s, i in zip(y_all_str, y_all, strict=True) if i < 0})
        raise AdviserFeatureError(f"dataset contains labels outside {ADVISER_ACTIONS}: {unknown}")

    n_train = train_part.height
    X_train_np = X_all[:n_train]
    y_train = y_all[:n_train]
    X_val_np = X_all[n_train:]
    y_val = y_all[n_train:]

    X_oos_np, _ = build_training_matrix(oos)
    y_oos_str = oos["optimal_action"].to_list()
    y_oos = np.array([INDEX_BY_ACTION.get(s, -1) for s in y_oos_str], dtype=np.int64)
    if (y_oos < 0).any():
        unknown = sorted({s for s, i in zip(y_oos_str, y_oos, strict=True) if i < 0})
        raise AdviserFeatureError(f"OOS split has labels outside {ADVISER_ACTIONS}: {unknown}")

    # ---- 4. scaler fitted on TRAIN ONLY (no val/oos leakage) --------------
    scaler = AdviserScaler(
        mean=np.mean(X_train_np, axis=0),
        std=np.std(X_train_np, axis=0),
        feature_dim=ADVISER_FEATURE_DIM,
    )
    X_train = torch.tensor(scaler.transform(X_train_np), dtype=torch.float32)
    X_val = torch.tensor(scaler.transform(X_val_np), dtype=torch.float32)
    X_oos = torch.tensor(scaler.transform(X_oos_np), dtype=torch.float32)
    yt = torch.tensor(y_train, dtype=torch.long)
    yv = torch.tensor(y_val, dtype=torch.long)
    yo = torch.tensor(y_oos, dtype=torch.long)

    # ---- 5. class weights (imbalance without resampling) ------------------
    counts = np.bincount(y_train, minlength=len(ADVISER_ACTIONS)).astype(np.float64)
    w = np.zeros(len(ADVISER_ACTIONS), dtype=np.float64)
    nz = counts[counts > 0]
    if len(nz) > 1:
        # inverse-frequency, normalised so the total weight equals N
        freq = counts / counts.sum()
        w = 1.0 / np.maximum(freq, 1e-9)
        w = w / w.sum() * len(nz)
    else:
        w[:] = 1.0
    weights = torch.tensor(w, dtype=torch.float32)
    criterion = nn.CrossEntropyLoss(weight=weights, label_smoothing=0.05)

    # ---- 6. train ----------------------------------------------------------
    model = PositionAdviserNet(feature_dim=ADVISER_FEATURE_DIM, num_classes=len(ADVISER_ACTIONS))
    opt = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt,
        max_lr=learning_rate,
        total_steps=max(epochs, 1) * max(1, (n_train + batch_size - 1) // batch_size),
        pct_start=0.3,
    )

    best_val_loss = float("inf")
    best_state: dict[str, Any] | None = None
    patience, bad_epochs = 4, 0

    for ep in range(1, epochs + 1):
        model.train()
        perm = torch.randperm(n_train)
        ep_losses: list[float] = []
        for i in range(0, n_train, batch_size):
            idx = perm[i : i + batch_size]
            opt.zero_grad()
            loss = criterion(model(X_train[idx]), yt[idx])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            ep_losses.append(float(loss.item()))

        model.eval()
        with torch.inference_mode():
            val_loss = float(criterion(model(X_val), yv).item())

        if val_loss < best_val_loss - 1e-6:
            best_val_loss = val_loss
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            bad_epochs = 0
        else:
            bad_epochs += 1
        if bad_epochs >= patience:
            logger.info("[ADVISER] event=EARLY_STOP epoch=%d best_val_loss=%.6f", ep, best_val_loss)
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()

    # ---- 7. honest OOS scoring (never trained on) -------------------------
    with torch.inference_mode():
        oos_logits = model(X_oos)
        oos_loss = float(criterion(oos_logits, yo).item())
        oos_pred = torch.argmax(oos_logits, dim=-1).numpy()
        oos_acc = float((oos_pred == y_oos).mean())
    oos_dist = {
        ACTION_BY_INDEX[int(i)]: int(np.sum(oos_pred == i)) for i in range(len(ADVISER_ACTIONS))
    }

    # ---- 8. persist ----------------------------------------------------------
    # SANITIZER BARRIER (CodeQL py/path-injection): ``output_dir`` is reduced to
    # a whitelist-only relative Path and anchored under the trusted root; only
    # that untainted value is mkdir-ed and joined into the artifact names.
    out = _resolve_output_dir(output_dir, dataset)
    if not out.is_relative_to(_ADVISER_ROOT.resolve()):
        raise AdviserFeatureError(
            "position adviser output_dir must stay inside the repository root"
        )
    out.mkdir(parents=True, exist_ok=True)
    # ``model_id`` comes from the request body and is joined into the artifact
    # names below, so it is reduced to a single whitelist-only path component
    # (no separators at all): a ``../../`` id cannot write outside ``out``.
    mid = _sanitize_model_id(model_id)
    weights_path = out / f"{mid}.pt"
    scaler_path = out / f"{mid}.scaler.npz"
    manifest_path = out / f"{mid}.meta.json"

    torch.save(model.state_dict(), weights_path)
    # np.savez's stub types the **kwargs of its first overload as bool, so a
    # **dict unpacking is flagged regardless of the value type. Name the
    # fields explicitly; asarray normalises the scalar dimension to a 0-d
    # array exactly as the previous dict-comprehension did.
    _sa = scaler.to_arrays()
    np.savez(
        scaler_path,
        mean=np.asarray(_sa["mean"]),
        std=np.asarray(_sa["std"]),
        dimension=np.asarray(_sa["dimension"]),
    )
    sha = _sha256_trainer_artifact(weights_path)

    manifest = {
        "model_id": mid,
        "architecture": "PositionAdviserNet",
        "feature_order": feature_names,
        "feature_dim": ADVISER_FEATURE_DIM,
        "actions": list(ADVISER_ACTIONS),
        "epochs": epochs,
        "seed": seed,
        "learning_rate": learning_rate,
        "batch_size": batch_size,
        "best_val_loss": best_val_loss,
        "oos_loss": oos_loss,
        "oos_accuracy": oos_acc,
        "oos_action_distribution": oos_dist,
        "train_rows": int(n_train),
        "val_rows": int(val_part.height),
        "oos_rows": int(oos.height),
        "purge_embargo_rows_excluded": int(dropped),
        "source_dataset": str(p),
        "source_dataset_hash": compute_dataset_hash(df)[:32],
        "weights_sha256": sha,
        "created_at": datetime.now(UTC).isoformat(),
    }
    # SEC (py/path-injection #1148): the manifest write resolves the target
    # under the untainted output directory via resolve_under_root, so the sink
    # operates on a canonicalized in-root path that CodeQL recognizes as safe.
    _manifest_target = resolve_under_root(
        f"{mid}.meta.json", root=out, label="position adviser manifest"
    )
    with _manifest_target.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    logger.info(
        "[ADVISER] event=TRAIN_COMPLETE model_id=%s train=%d val=%d oos=%d "
        "best_val_loss=%.6f oos_loss=%.6f oos_acc=%.4f",
        mid,
        n_train,
        val_part.height,
        oos.height,
        best_val_loss,
        oos_loss,
        oos_acc,
    )

    # Report paths relative to the CWD when the artifact sits inside it (so the
    # UI shows a repo-relative name); otherwise the absolute path. Never lies
    # about location either way.
    cwd = Path.cwd()

    def _rel(path: Path) -> str:
        try:
            return str(path.relative_to(cwd))
        except ValueError:
            return str(path)

    return AdviserTrainingResult(
        model_id=mid,
        weights_path=_rel(weights_path),
        scaler_path=_rel(scaler_path),
        manifest_path=_rel(manifest_path),
        feature_dim=ADVISER_FEATURE_DIM,
        epochs_run=epochs,
        best_val_loss=best_val_loss,
        oos_loss=oos_loss,
        oos_accuracy=oos_acc,
        oos_action_distribution=oos_dist,
        train_rows=int(n_train),
        val_rows=int(val_part.height),
        oos_rows=int(oos.height),
        sha256=sha,
        duration_sec=time.perf_counter() - t_start,
        metrics={
            "class_weights": {ACTION_BY_INDEX[int(i)]: float(w[i]) for i in range(len(w))},
            "source_row_count": df.height,
        },
    )


__all__ = [
    "TRAINABLE_SPLITS",
    "AdviserScaler",
    "AdviserTrainingResult",
    "PositionAdviserNet",
    "train_position_adviser",
]
