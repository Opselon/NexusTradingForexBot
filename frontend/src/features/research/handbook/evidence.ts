/**
 * Handbook — evidence vault, snapshots, events, outcome lineage (topic).
 * Compiled from src/nexus_scalp/research/{evidence,run_snapshot,events,outcome_lineage}.py.
 * GATE_CHAIN/kind/status vocab is asserted against evidence.py by the test.
 */
import type { HandbookEntry } from "./types";
import type { ScoringTranslate } from "./scoring";

/** Identity translator (en): English fallback with {var} interpolation only. */
const identity: ScoringTranslate = (_key, fallback, vars) =>
  vars
    ? Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), fallback)
    : fallback;

/**
 * Single source of the entry: every user-visible string goes through t() with
 * the English only as fallback; keys live in features/research/i18n.ts.
 */
function buildEvidenceEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
  id: "topic/evidence",
  kind: "topic",
  badge: t("research.hb.evidence.badge", "APPEND-ONLY · SHA-256"),
  title: t("research.hb.evidence.title", "Evidence — the vault that gates are judged from"),
  subtitle: t("research.hb.evidence.subtitle", "Immutable gate artifacts, v2 run snapshots, append-only events, and outcome lineage with repair states."),
  source: "src/nexus_scalp/research/{evidence,run_snapshot,events,outcome_lineage}.py",
  seeAlso: ["topic/lifecycle", "gate/SCORING", "topic/operations"],
  keywords: ["evidence", "artifact", "content hash", "snapshot", "fingerprint", "lineage", "events", "immutability", "not recorded", "repair"],
  sections: [
    {
      heading: t("research.hb.evidence.sec1_head", "Immutability is the product"),
      body: [
        t("research.hb.evidence.sec1_p1", "research/evidence.py\'s header rule: the gate chain is judged from evidence artifacts, and the stores are APPEND-ONLY: runs, gate results, checks and artifacts — never mutated in place — so any status or verdict traces to an immutable record."),
        t("research.hb.evidence.sec1_p2", "Append-only means corrections are NEW rows: a retry appends a new gate result referencing the prior one; a repair appends lineage events. History is never edited to look cleaner."),
        t("research.hb.evidence.sec1_p3", "Why gates read from vault instead of memory: a verdict re-derived from a mutable cache can change tomorrow. A verdict bound to a content-hashed artifact can be re-verified by anyone, forever."),
      ],
    },
    {
      heading: t("research.hb.evidence.sec2_head", "The seven evidence kinds"),
      body: [
        t("research.hb.evidence.sec2_p1", "EVIDENCE_KINDS (evidence.py): discovery — family statistics and sample_ids; backtest — deterministic baseline stats; walk_forward — fold-level battery; out_of_sample — holdout evaluation; robustness — stress battery results; scoring — the StrategyScore payload; lineage — provenance/repair records."),
        t("research.hb.evidence.sec2_p2", "One artifact per (run_id, gate, kind) — a second write for the same key raises EvidenceConflictError (errors.py): artifacts are content-addressed claims, not upsert rows."),
        t("research.hb.evidence.sec2_p3", "EvidenceNotFound (errors.py) covers the absence case: scoring looking for an OOS artifact that was never persisted fails loudly instead of scoring silence as zero."),
        t("research.hb.evidence.sec2_p4", "read_gate_evidence is the single reader gate code uses — one contract for all six gates, so a half-written artifact cannot sneak past with bespoke parsing."),
      ],
    },
    {
      heading: t("research.hb.evidence.sec3_head", "content_hash — how \'immutable\' is checked"),
      body: [
        t("research.hb.evidence.sec3_p1", "content_hash = sha256 over stable_digest(payload): a deterministic serialization (sorted keys, fixed float formatting) so the SAME logical payload always hashes identically across processes and machines (evidence.py docstring)."),
        t("research.hb.evidence.sec3_p2", "The guard is build-time: verify_content_hash recomputes and compares on read-path verification — \'guards against a corrupted artifact pretending to be valid\' (module docstring)."),
        t("research.hb.evidence.sec3_p3", "stable_digest matters as much as sha256: without canonical serialization, JSON key order or 17-vs-16 decimal digits would change the hash and break reproducibility for honest data."),
        t("research.hb.evidence.sec3_p4", "When a verdict is disputed, recomputing the hash from the payload IS the arbitration — no trust in the UI, the API, or the operator required."),
      ],
    },
    {
      heading: t("research.hb.evidence.sec4_head", "Run snapshots — CHG-0035 v2"),
      body: [
        t("research.hb.evidence.sec4_p1", "run_snapshot.py: v2 snapshots bound to (run_id, attempt) — engine identity (engine_version + semantic git describe), research_mode, dataset identity (dataset_id + fingerprint when present), evidence kinds and counts, and a signals_b64 blob that reproduces the model\'s decision inputs (fields + context fingerprint)."),
        t("research.hb.evidence.sec4_p2", "Fingerprinting extends to data: dataset identity rides the snapshot so a verdict can be re-checked against the EXACT data it was computed from — different data, different claim."),
        t("research.hb.evidence.sec4_p3", "Non-recorded semantics are explicit: where a datum is unavailable the snapshot records \'NOT_RECORDED\' (e.g. \'lineage recorded in v2 as NOT_RECORDED when absent\'). Absence is data; it is never filled with a plausible default (404s → NOT_RECORDED, task.md 34)."),
        t("research.hb.evidence.sec4_p4", "append_summary counts arrive as \'artifacts per kind and counts\' — the Retention & audits events panel quotes these counts; a count mismatch between panel and snapshot is itself a finding."),
      ],
    },
    {
      heading: t("research.hb.evidence.sec5_head", "Events — the append-only narration"),
      body: [
        t("research.hb.evidence.sec5_p1", "research/events.py builds lifecycle events including RETRY events recording failure→retry pairing: \'a retry event with error_class carries both the failure and the retry in one history entry\'."),
        t("research.hb.evidence.sec5_p2", "Every event carries its own write_timestamp and lineage — the history panel\'s ordering derives from the event stream, never from client clocks (rendered literally in the Retention & audits tab)."),
        t("research.hb.evidence.sec5_p3", "EvidenceRetentionError (errors.py) guards the retention/archive boundary: archived evidence stays readable for audit but immutable rows are never truncated in place — retention moves, it does not rewrite."),
        t("research.hb.evidence.sec5_p4", "Because events are the narrative layer over immutable artifacts, the two cross-check: an event claims something happened; the artifact + content_hash proves what exactly happened."),
      ],
    },
    {
      heading: t("research.hb.evidence.sec6_head", "Outcome lineage — did this trade really happen?"),
      body: [
        t("research.hb.evidence.sec6_p1", "outcome_lineage.py classifies every economic outcome\'s provenance: NONE, BROKER_DEALS, BROKER_DEALS_AGGREGATED, RECONSTRUCTED — from broker statements (strongest) to aggregation (deals merged to one observation) to reconstruction (derived when deals are absent)."),
        t("research.hb.evidence.sec6_p2", "Repair states carry the same honesty: not_attempted, succeeded, failed — a repair attempt without an outcome is recorded as attempted-and-failed, never silently dropped."),
        t("research.hb.evidence.sec6_p3", "Broker statements must not be fabricated (task.md: \'broker statements must not be fabricated; repairs record outcome_lineage plus repair_state\') — the data model makes fabrication a type error, not a policy."),
        t("research.hb.evidence.sec6_p4", "Why it gates: expectancy computed on reconstructed outcomes is weaker evidence than on broker-confirmed deals, and the lineage artifact is how later readers (scoring, auditors, the outcome-quality panel) know which they have."),
      ],
    },
    {
      heading: t("research.hb.evidence.sec7_head", "Reproducibility checklist per verdict"),
      body: [
        t("research.hb.evidence.sec7_p1", "A gate verdict that cannot answer ALL of these is not audit-grade — the vault exists so every row can:"),
      ],
      bullets: [
        t("research.hb.evidence.sec7_b1", "WHICH run + attempt? — run_id, attempt bound into snapshots and artifacts."),
        t("research.hb.evidence.sec7_b2", "WHICH data? — dataset_id + dataset fingerprint."),
        t("research.hb.evidence.sec7_b3", "WHICH model/decision inputs? — signals_b64 (fields + context fingerprint), engine_version + git describe."),
        t("research.hb.evidence.sec7_b4", "WHICH payload exactly? — stable_digest + content_hash (recompute to verify)."),
        t("research.hb.evidence.sec7_b5", "WHEN and in what order? — event stream with its own timestamps + lineage."),
        t("research.hb.evidence.sec7_b6", "WHERE did the trades come from? — outcome lineage + repair state."),
      ],
    },
  ],
  params: [
    {
      name: "EVIDENCE_KINDS",
      value: "discovery · backtest · walk_forward · out_of_sample · robustness · scoring · lineage",
      meaning: t("research.hb.evidence.param_kinds", "The seven artifact families a run can persist."),
      ref: "evidence.py",
    },
    {
      name: "uniqueness",
      value: "one artifact per (run_id, gate, kind)",
      meaning: t("research.hb.evidence.param_uniqueness", "Duplicate write raises EvidenceConflictError — append-only, no upserts."),
      ref: "evidence.py / errors.py",
    },
    {
      name: "content_hash",
      value: "sha256(stable_digest(payload))",
      meaning: t("research.hb.evidence.param_content_hash", "Canonical serialization + hash; recompute-on-read guards corruption."),
      ref: "evidence.py::content_hash",
    },
    {
      name: "snapshot version",
      value: "v2 (CHG-0035)",
      meaning: t("research.hb.evidence.param_snapshot", "engine identity + dataset fingerprint + signals blob; absent data = NOT_RECORDED."),
      ref: "run_snapshot.py",
    },
    {
      name: "outcome lineage kinds",
      value: "NONE | BROKER_DEALS | BROKER_DEALS_AGGREGATED | RECONSTRUCTED",
      meaning: t("research.hb.evidence.param_lineage_kinds", "Provenance strength of each economic outcome."),
      ref: "outcome_lineage.py",
    },
    {
      name: "repair states",
      value: "not_attempted | succeeded | failed",
      meaning: t("research.hb.evidence.param_repair_states", "Repair attempts are recorded, never silently dropped."),
      ref: "outcome_lineage.py",
    },
  ],
  faq: [
    {
      q: t("research.hb.evidence.faq1_q", "What does NOT_RECORDED mean in the events panel?"),
      a: t("research.hb.evidence.faq1_a", "The backend explicitly recorded that the datum was absent — the honest state — instead of the UI guessing. It appears e.g. when v2 lineage fields were absent at snapshot time (task.md 34)."),
    },
    {
      q: t("research.hb.evidence.faq2_q", "An artifact\'s hash fails verification — now what?"),
      a: t("research.hb.evidence.faq2_a", "The artifact is not to be trusted: verify_content_hash recomputes stable_digest + sha256 and a mismatch means corruption or tampering. Treat the gate\'s evidence as missing (EvidenceNotFound semantics), investigate storage, and re-run the gate — never patch the hash."),
    },
    {
      q: t("research.hb.evidence.faq3_q", "Why can\'t I just edit a wrong artifact?"),
      a: t("research.hb.evidence.faq3_a", "Because every verdict downstream is bound to it. Corrections append: a new artifact/event with its own hash and lineage, so the original claim AND its correction both survive for audit — that is what makes a status traceable to an immutable record."),
    },
    {
      q: t("research.hb.evidence.faq4_q", "Is RECONSTRUCTED outcome data useless?"),
      a: t("research.hb.evidence.faq4_a", "No — it is weaker than broker-confirmed data and labeled as such. Lineage lets downstream readers weigh it: BROKER_DEALS is strongest, RECONSTRUCTED carries reconstruction assumptions. Unlabeled fabricated data would be useless; labeled reconstruction is evidence with a stated pedigree."),
    },
  ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const evidenceEntry: HandbookEntry = buildEvidenceEntry();

/**
 * Translator-aware copy: call during render with the store's current t();
 * the result changes with the language — never cache it outside render.
 * Returned as an array so the handbook overlay can register whole lanes.
 */
export function evidenceTranslated(t?: ScoringTranslate): HandbookEntry[] {
  return [buildEvidenceEntry(t)];
}
