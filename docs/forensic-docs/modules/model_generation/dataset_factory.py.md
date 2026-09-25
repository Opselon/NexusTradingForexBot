# src/nexus_scalp/model_generation/dataset_factory.py

- **PURPOSE:** Dataset Factory (PHASE 13, spec 7/8/17): reproducible dataset
  artifacts — same inputs ⇒ same dataset identity. The generator does NOT depend
  on the current live model (spec 17).
- **ARCHITECTURE LAYER:** Research/ML — dataset artifact builder, no order
  authority.
- **RESPONSIBILITY:** samples → temporal split (train/val/test) → purge/embargo
  preserved via labels → DatasetManifest (hashes, provenance, splits, purge,
  news lineage) → parquet artifact + manifest, all persisted through the
  ArtifactStore.
- **DEPENDENCIES:** polars, artifact_store, models.DatasetManifest,
  sample_factory (SampleFactory, samples_to_frame), news_bridge (lazy,
  normalize_news_frame), hashlib/json.
- **CONNECTS TO:** schema_v2 (60D), schema_v2_incremental (70D),
  benchmark (one dataset per schema), training (dataset_id → manifest reads).

- **KEY CONCEPTS:**
  - `deterministic_dataset_id` (line 32): `ds_<sha256[:16]>` over
    symbol|timeframe|feature_schema_id|label_schema_id|strategy_id|config_hash
    + optional news digest — news content is PART of dataset identity, so a
    real-news dataset is distinguishable from no-news (spec 17/18).
  - `_news_digest` (line 51): normalized news frame → {version, rows, range,
    content_hash (sha256 over the full normalized 12-field matrix + publication
    times)}; any news change re-identifies the dataset. None when no news frame
    or normalization fails.
  - `build` (line 104): builds samples (raises ValueError when ZERO samples —
    explicit failure, not a silent empty artifact), applies the chronological
    split, computes cfg/config_blob, then the REAL dataset_id; manifest records
    row_counts per split, temporal_range, label_config_hash (repr of labeler
    dict, truncated 64 chars — weak but deterministic),
    split_config_hash, purge/embargo parameters read from the labeler's
    `embargo_bars` attr (BOTH purge_gap_bars and embargo_bars get the SAME
    value — the labeler doesn't carry purge separately), news lineage, and
    strategy_context_version. `save_dataset` stamps dataset_hash.
    CRITICAL IDENTITY POINT: `dataset_id` is computed BEFORE the frame is
    saved, from (cfg, news_digest) only — it does NOT include features/labels
    (content hash of the frame is the manifest's dataset_hash instead).
  - `_apply_split` (line 229): CHRONOLOGICAL temporal split (train=earliest
    ratio 0.7, val=middle 0.15, test=latest); deterministic given seed (the
    seed is recorded but NOT consumed by this function — the split is pure
    chronological, which is the correct no-lookahead design for time series).
- **HOT PATH / PERFORMANCE:** Offline. `_news_digest` normalizes the news frame
  ONCE and `_apply_split` is vectorized; `label_config_hash` via repr of the
  labeler's __dict__ is computed per build (trivial).
- **EDGE CASES & PITFALLS:**
  - The `purge_parameters` value reads `self.sample_factory.labeler.embargo_bars`
    for BOTH keys — purge_gap_bars is a lie-by-proxy for labeler defaults; only
    `embargo_bars` is real. The manifest documents this as "preserved via
    labels" (the labeler already purged/embargoed at label time).
  - `dataset_id` excludes the actual row content: two builds with identical
    cfg+news but different bar data share the id ONLY if the frame differs —
    actually the id can COLLIDE across different bar data (same config, same
    news digest). The dataset_hash covers content, but callers keying on
    dataset_id alone can alias distinct content. (observed pattern; deliberate
    trade-off so id stays a config fingerprint)
  - Worker count: values are read by `frame.filter(pl.col("_split")==...)` with
    the `_split` column DROPPED at save (frame.drop("_split"), line 212) so the
    artifact frame has no split column — replay/validation must re-derive splits
    temporally (sequence trainer uses tail 20%; benchmark uses the manifest
    counts).