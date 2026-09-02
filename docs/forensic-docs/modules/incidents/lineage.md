# src/nexus_scalp/incidents/lineage.py

- PURPOSE: Value lineage & source-of-truth tracing (TASK-12 spec 7/8/38-43).
  For important values (Balance, Equity, PnL, Open positions, Model ID,
  Feature vector, Liquidity state, News state, Strategy ID, Realized R):
  SOURCE OF TRUTH → TRANSFORMATIONS → CACHES → PERSISTENCE → API → UI.
  When a UI value is wrong, the tracer walks backward to the FIRST incorrect
  value (first-failure identification, spec 6). Purely diagnostic.
- ARCHITECTURE LAYER: Domain (trace construction), read-only.
- RESPONSIBILITY: PRODUCERS registry (canonical source per value field),
  TRANSFORMATIONS chains (per-value hop lists), LineageEngine (trace
  building + first-divergence walk), build_simple_trace utility.
- DEPENDENCIES: incidents.models (LineageStep/ValueTrace), stdlib.
- CONNECTS TO: incidents worker, reports, accounting forensics, web
  diagnostics (lineage UI), WHY-trace queries in trace.py.
- KEY CONCEPTS:
  - PRODUCERS maps each field to its documented source of truth e.g.
    balance/equity → MT5 get_account_info; open_positions → broker
    positions (authoritative, INV-011); feature_vector → features from
    completed bars + decision tick (INV-008); mt5_timebase → broker-local
    epoch (BUG-070).
  - TRANSFORMATIONS encodes the canonical hop chains for pnl (adapter
    normalization → deal snapshot → reconciliation → accounting core → API
    → UI), realized_r (outcome recovery → R-multiple conversion →
    reconstruction_source check → …), open_positions (broker snapshot →
    exposure cache → policy check → UI render), feature_vector (bar
    aggregator reseed REPLACE+ALIGN → feature calc → normalization/clip →
    vector assembly → model inference).
  - `trace()` (line 87): builds a ValueTrace with source + transformation
    steps stamped with source_timestamp; `pnl_trace`/`realized_r_trace`
    shortcuts.
  - `exposure_trace()` (line 107): hard-coded historical MAX_EXPOSURE
    false-block path (broker → snapshot normalization → in-memory session
    cache → policy check → API → UI).
  - `model_output_trace()` (line 131): feature vector (validated, finite,
    clipped) → Champion inference (deterministic) → probability→action →
    signal policy/rule matrix → API → UI — the earliest incorrect layer
    walk (spec 18).
  - `ui_value_trace()` (line 148): appends JS-loader fetch + renderer +
    widget hops to the canonical trace (WHY UI EMPTY, spec 21/43).
  - `find_first_divergence` (line 169): walks hops in order; a hop is
    suspect if its name is in known_bad_steps (evidence) or its query-hook
    probe value looks suspicious; returns the FIRST suspect as
    first_divergence + full inspected-hops audit list. Diagnostics only.
  - `_value_suspect` (line 225): None/empty/NaN/non-finite/zero-where-
    non-zero expected ("0", "0.0", "waiting", "empty", "") → suspect;
    numeric |v| < 1e-12 → suspect; empty containers → suspect.
- HOT PATH / PERFORMANCE: on-demand forensic use only; per-hop probes may
  hit DB/logs via hooks (caller-injected, bounded by hop count).
- EDGE CASES & PITFALLS: hook key matching is name.lower() — hop names must
  match hook keys case-insensitively; a probe raising returns
  "PROBE_ERROR: …" string which _value_suspect treats as NOT suspect (long
  strings are not in the suspect list) — a broken probe silently looks
  healthy; find_first_divergence reports the first suspect, not the
  strongest; build_simple_trace treats hop[0] as source and derives steps
  from hops[1:] (single-hop list yields an empty transformation chain).