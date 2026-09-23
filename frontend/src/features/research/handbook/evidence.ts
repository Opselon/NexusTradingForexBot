/**
 * Handbook — evidence vault, snapshots, events, outcome lineage (topic).
 * Compiled from src/nexus_scalp/research/{evidence,run_snapshot,events,outcome_lineage}.py.
 * GATE_CHAIN/kind/status vocab is asserted against evidence.py by the test.
 */
import type { HandbookEntry } from "./types";

export const evidenceEntry: HandbookEntry = {
  id: "topic/evidence",
  kind: "topic",
  badge: "APPEND-ONLY · SHA-256",
  title: "Evidence — the vault that gates are judged from",
  subtitle: "Immutable gate artifacts, v2 run snapshots, append-only events, and outcome lineage with repair states.",
  source: "src/nexus_scalp/research/{evidence,run_snapshot,events,outcome_lineage}.py",
  seeAlso: ["topic/lifecycle", "gate/SCORING", "topic/operations"],
  keywords: ["evidence", "artifact", "content hash", "snapshot", "fingerprint", "lineage", "events", "immutability", "not recorded", "repair"],
  sections: [
    {
      heading: "Immutability is the product",
      body: [
        "research/evidence.py's header rule: the gate chain is judged from evidence artifacts, and the stores are APPEND-ONLY: runs, gate results, checks and artifacts — never mutated in place — so any status or verdict traces to an immutable record.",
        "Append-only means corrections are NEW rows: a retry appends a new gate result referencing the prior one; a repair appends lineage events. History is never edited to look cleaner.",
        "Why gates read from vault instead of memory: a verdict re-derived from a mutable cache can change tomorrow. A verdict bound to a content-hashed artifact can be re-verified by anyone, forever.",
      ],
    },
    {
      heading: "The seven evidence kinds",
      body: [
        "EVIDENCE_KINDS (evidence.py): discovery — family statistics and sample_ids; backtest — deterministic baseline stats; walk_forward — fold-level battery; out_of_sample — holdout evaluation; robustness — stress battery results; scoring — the StrategyScore payload; lineage — provenance/repair records.",
        "One artifact per (run_id, gate, kind) — a second write for the same key raises EvidenceConflictError (errors.py): artifacts are content-addressed claims, not upsert rows.",
        "EvidenceNotFound (errors.py) covers the absence case: scoring looking for an OOS artifact that was never persisted fails loudly instead of scoring silence as zero.",
        "read_gate_evidence is the single reader gate code uses — one contract for all six gates, so a half-written artifact cannot sneak past with bespoke parsing.",
      ],
    },
    {
      heading: "content_hash — how 'immutable' is checked",
      body: [
        "content_hash = sha256 over stable_digest(payload): a deterministic serialization (sorted keys, fixed float formatting) so the SAME logical payload always hashes identically across processes and machines (evidence.py docstring).",
        "The guard is build-time: verify_content_hash recomputes and compares on read-path verification — 'guards against a corrupted artifact pretending to be valid' (module docstring).",
        "stable_digest matters as much as sha256: without canonical serialization, JSON key order or 17-vs-16 decimal digits would change the hash and break reproducibility for honest data.",
        "When a verdict is disputed, recomputing the hash from the payload IS the arbitration — no trust in the UI, the API, or the operator required.",
      ],
    },
    {
      heading: "Run snapshots — CHG-0035 v2",
      body: [
        "run_snapshot.py: v2 snapshots bound to (run_id, attempt) — engine identity (engine_version + semantic git describe), research_mode, dataset identity (dataset_id + fingerprint when present), evidence kinds and counts, and a signals_b64 blob that reproduces the model's decision inputs (fields + context fingerprint).",
        "Fingerprinting extends to data: dataset identity rides the snapshot so a verdict can be re-checked against the EXACT data it was computed from — different data, different claim.",
        "Non-recorded semantics are explicit: where a datum is unavailable the snapshot records 'NOT_RECORDED' (e.g. 'lineage recorded in v2 as NOT_RECORDED when absent'). Absence is data; it is never filled with a plausible default (404s → NOT_RECORDED, task.md 34).",
        "append_summary counts arrive as 'artifacts per kind and counts' — the Retention & audits events panel quotes these counts; a count mismatch between panel and snapshot is itself a finding.",
      ],
    },
    {
      heading: "Events — the append-only narration",
      body: [
        "research/events.py builds lifecycle events including RETRY events recording failure→retry pairing: 'a retry event with error_class carries both the failure and the retry in one history entry'.",
        "Every event carries its own write_timestamp and lineage — the history panel's ordering derives from the event stream, never from client clocks (rendered literally in the Retention & audits tab).",
        "EvidenceRetentionError (errors.py) guards the retention/archive boundary: archived evidence stays readable for audit but immutable rows are never truncated in place — retention moves, it does not rewrite.",
        "Because events are the narrative layer over immutable artifacts, the two cross-check: an event claims something happened; the artifact + content_hash proves what exactly happened.",
      ],
    },
    {
      heading: "Outcome lineage — did this trade really happen?",
      body: [
        "outcome_lineage.py classifies every economic outcome's provenance: NONE, BROKER_DEALS, BROKER_DEALS_AGGREGATED, RECONSTRUCTED — from broker statements (strongest) to aggregation (deals merged to one observation) to reconstruction (derived when deals are absent).",
        "Repair states carry the same honesty: not_attempted, succeeded, failed — a repair attempt without an outcome is recorded as attempted-and-failed, never silently dropped.",
        "Broker statements must not be fabricated (task.md: 'broker statements must not be fabricated; repairs record outcome_lineage plus repair_state') — the data model makes fabrication a type error, not a policy.",
        "Why it gates: expectancy computed on reconstructed outcomes is weaker evidence than on broker-confirmed deals, and the lineage artifact is how later readers (scoring, auditors, the outcome-quality panel) know which they have.",
      ],
    },
    {
      heading: "Reproducibility checklist per verdict",
      body: [
        "A gate verdict that cannot answer ALL of these is not audit-grade — the vault exists so every row can:",
      ],
      bullets: [
        "WHICH run + attempt? — run_id, attempt bound into snapshots and artifacts.",
        "WHICH data? — dataset_id + dataset fingerprint.",
        "WHICH model/decision inputs? — signals_b64 (fields + context fingerprint), engine_version + git describe.",
        "WHICH payload exactly? — stable_digest + content_hash (recompute to verify).",
        "WHEN and in what order? — event stream with its own timestamps + lineage.",
        "WHERE did the trades come from? — outcome lineage + repair state.",
      ],
    },
  ],
  params: [
    {
      name: "EVIDENCE_KINDS",
      value: "discovery · backtest · walk_forward · out_of_sample · robustness · scoring · lineage",
      meaning: "The seven artifact families a run can persist.",
      ref: "evidence.py",
    },
    {
      name: "uniqueness",
      value: "one artifact per (run_id, gate, kind)",
      meaning: "Duplicate write raises EvidenceConflictError — append-only, no upserts.",
      ref: "evidence.py / errors.py",
    },
    {
      name: "content_hash",
      value: "sha256(stable_digest(payload))",
      meaning: "Canonical serialization + hash; recompute-on-read guards corruption.",
      ref: "evidence.py::content_hash",
    },
    {
      name: "snapshot version",
      value: "v2 (CHG-0035)",
      meaning: "engine identity + dataset fingerprint + signals blob; absent data = NOT_RECORDED.",
      ref: "run_snapshot.py",
    },
    {
      name: "outcome lineage kinds",
      value: "NONE | BROKER_DEALS | BROKER_DEALS_AGGREGATED | RECONSTRUCTED",
      meaning: "Provenance strength of each economic outcome.",
      ref: "outcome_lineage.py",
    },
    {
      name: "repair states",
      value: "not_attempted | succeeded | failed",
      meaning: "Repair attempts are recorded, never silently dropped.",
      ref: "outcome_lineage.py",
    },
  ],
  faq: [
    {
      q: "What does NOT_RECORDED mean in the events panel?",
      a: "The backend explicitly recorded that the datum was absent — the honest state — instead of the UI guessing. It appears e.g. when v2 lineage fields were absent at snapshot time (task.md 34).",
    },
    {
      q: "An artifact's hash fails verification — now what?",
      a: "The artifact is not to be trusted: verify_content_hash recomputes stable_digest + sha256 and a mismatch means corruption or tampering. Treat the gate's evidence as missing (EvidenceNotFound semantics), investigate storage, and re-run the gate — never patch the hash.",
    },
    {
      q: "Why can't I just edit a wrong artifact?",
      a: "Because every verdict downstream is bound to it. Corrections append: a new artifact/event with its own hash and lineage, so the original claim AND its correction both survive for audit — that is what makes a status traceable to an immutable record.",
    },
    {
      q: "Is RECONSTRUCTED outcome data useless?",
      a: "No — it is weaker than broker-confirmed data and labeled as such. Lineage lets downstream readers weigh it: BROKER_DEALS is strongest, RECONSTRUCTED carries reconstruction assumptions. Unlabeled fabricated data would be useless; labeled reconstruction is evidence with a stated pedigree.",
    },
  ],
};
