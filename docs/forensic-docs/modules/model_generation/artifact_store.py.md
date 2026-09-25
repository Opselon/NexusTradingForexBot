# src/nexus_scalp/model_generation/artifact_store.py

- **PURPOSE:** The filesystem artifact store for PHASE 13 — datasets,
  experiments and models are versioned directory artifacts with manifests. The
  store NEVER requires the trading DB; model inference reads ONLY the filesystem
  artifact (spec 6/40).
- **ARCHITECTURE LAYER:** Research/ML infrastructure — artifact persistence, no
  order authority.
- **RESPONSIBILITY:** Content-addressed-style layout + integrity hashing,
  atomic JSON/weights/scaler writes, verify-on-read, path-safety validation.
- **DEPENDENCIES:** numpy (scaler IO), polars (lazy import for parquet), torch
  (lazy import for weights save), observability logger. NO database.
- **CONNECTS TO:** dataset_factory, experiment_factory, training,
  sequence_training, runtime, replay, benchmark.

- **KEY CONCEPTS:**
  - Layout (lines 4-16): `artifacts/model_generation/{datasets,experiments,
    models}/<id>/` with `dataset.parquet` + `dataset_manifest.json`;
    `experiment.json`; `model.pt`/`model.json` (manifest)/`scaler.npz`/
    `validation.json`.
  - `validate_artifact_id` (line 40): regex `^[A-Za-z0-9_.-]+$` + no ".." —
    blocks path traversal / separators that could escape the artifact root
    (forensic audit T03/T58). ALL id→path methods route through it.
  - `sha256_file` (line 52): chunked full-file SHA256 (64 KiB chunks) or '' when
    absent; `sha256_bytes`; `sha256_text` (sorted-key JSON digest) — the
    deterministic dict hash.
  - `write_json` (line 89): atomic via tmp + `tmp.replace(path)`; `read_json`
    returns None on parse failure (logged).
  - Dataset paths/handles: `save_dataset` (line 119) writes parquet + stamps
    `dataset_hash` into the manifest; `read_dataset` returns None for a MISSING
    parquet (deliberate — raising turned absent artifacts into hard test
    failures; callers guard with `if frame is None`).
  - `save_model_artifact` (line 179): weights via torch.save to `model.pt.tmp`
    then atomic replace; scaler via np.savez to a temp WITHOUT the .npz suffix
    then rename (`np.savez` auto-appends ".npz" — handled, lines 209-218);
    stamps artifact_hash + scaler_hash + created_at into the manifest and writes
    model.json. Returns {model_id, weights_path, artifact_hash, manifest_path}.
  - `verify_artifact` (line 257): never raises; verdict dict
    {ok, reason: MANIFEST_MISSING|WEIGHTS_MISSING|hash mismatch}. The raw
    end-to-end integrity check used by governance/CLI.
  - `default_artifact_root` (line 272): `Path("artifacts") / "model_generation"`
    — a RELATIVE path, so cwd-dependent (repo-relative convention).

- **HOT PATH / PERFORMANCE:** Inference-time reads are manifest JSON + scaler
  npz (once per load); sha256_file on save is the main cost (full state dict).
  `read_scaler` np.loads the npz per runtime load — reusable across predicts.

- **EDGE CASES & PITFALLS:**
  - `save_dataset` writes parquet BEFORE the manifest (crash between leaves an
    orphan parquet without manifest — harmless, read_dataset still works).
  - `save_model_artifact` writes weights BEFORE manifest — a crash between
    leaves weights without manifest (verify_artifact reports MANIFEST_MISSING —
    correct failure mode).
  - Manifest `created_at` is overwritten to `datetime.now(UTC).isoformat()` at
    save time (line 224), mutating the caller's manifest dict — replicated
    stamps and stale writes.
  - `read_dataset(None)` so a missing dataset silently yields None — callers
    MUST handle None; a typo'd id is indistinguishable from "not built yet".
  - npz scaler content is assumed to hold keys "mean"/"std" with equal lengths
    — no validation at read time (runtime relies on training writing the right
    shape; integrity failures surface later as predict-time dim errors).