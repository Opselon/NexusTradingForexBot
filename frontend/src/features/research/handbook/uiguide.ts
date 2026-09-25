/**
 * Handbook — operator UI guide: how to READ /alt/research (topic entry).
 * Static documentation of the page itself; every claim about data sources
 * mirrors useCases.ts / researchApi.ts wiring.
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
function buildUiGuideEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
  id: "topic/uiguide",
  kind: "topic",
  badge: t("research.hb.uiguide.badge", "PAGE MAP"),
  title: t("research.hb.uiguide.title", "Reading this page — tab-by-tab UI guide"),
  subtitle: t("research.hb.uiguide.subtitle", "What every panel, column, chip and button means, where its data comes from, and the workflows that use them."),
  source: "frontend/src/features/research/{ui/*.tsx,useCases.ts,model.ts}, researchApi.ts",
  seeAlso: ["topic/operations", "topic/lifecycle", "topic/gates"],
  keywords: ["ui", "guide", "tab", "column", "registry", "queue", "worker", "analytics", "retention", "datasets", "drawer", "promote", "cancel", "workflow"],
  sections: [
    {
      heading: t("research.hb.uiguide.sec1_head", "The hero — pipeline map and freshness"),
      body: [
        t("research.hb.uiguide.sec1_p1", "The hero shows the canonical gate chain (six nodes, chain order from the handbook which the test pins to evidence.py) with a one-line role per gate. It is orientation, not live status: per-strategy gate statuses live in the drawer and the queue tab."),
        t("research.hb.uiguide.sec1_p2", "The worker chip carries a live dot keyed to the backend\'s reported worker status: green pulse HEALTHY, amber DEGRADED, red STUCK/FAILED, grey idle/unknown. The dot never invents health — it colors what summary.worker.status reported."),
        t("research.hb.uiguide.sec1_p3", "The FreshnessCaption shows when the v1 status payload was generated and whether a refetch is in flight or errored — if the caption is stale, every number below it is stale too, and that is the first thing to fix."),
        t("research.hb.uiguide.sec1_p4", "The \'open strategy handbook →\' chip jumps to the Playbook tab — documentation compiled from backend source, clearly separated from live data."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec2_head", "KPI row (4 cards)"),
      body: [
        t("research.hb.uiguide.sec2_p1", "Registry total — summary.total over strategy_intelligence_registry. When the subsystem answers available:false the card shows \'n/a\' with the backend\'s reason, never a fake zero."),
        t("research.hb.uiguide.sec2_p2", "Validated — by_lifecycle.VALIDATED: gate-certified candidates awaiting/making promotion."),
        t("research.hb.uiguide.sec2_p3", "Active strategies — by_lifecycle.ACTIVE: candidates with real dispatch authority (in LIVE mode this is money-moving inventory)."),
        t("research.hb.uiguide.sec2_p4", "Research worker — the worker status badge plus closed-outcome count when outcome quality is reported. A red badge here explains frozen queues and stale everything else."),
        t("research.hb.uiguide.sec2_p5", "The integers count up on change: a motion affordance marking a value as freshly fetched — the value itself is always the backend\'s."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec3_head", "Lifecycle census rail"),
      body: [
        t("research.hb.uiguide.sec3_p1", "Each chip shows a state\'s row count with a fill bar = share of the registry total (pct printed in the chip\'s hover title with the raw denominator)."),
        t("research.hb.uiguide.sec3_p2", "Click a chip to filter the Registry tab to that state (aria-pressed reflects the filter; clicking the active chip clears it). The filter is a query param to GET /api/research/registry — the backend does the filtering."),
        t("research.hb.uiguide.sec3_p3", "Chip vocabulary comes from summary.by_lifecycle — the backend\'s registry progress labels (see topic/lifecycle for state-machine vs progress-label layers)."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec4_head", "Command bar"),
      body: [
        t("research.hb.uiguide.sec4_p1", "Every button POSTs /api/research/<command> through researchUseCases and renders the backend\'s verdict line verbatim (CommandResultLine). Destructive commands (cancel, retry, promote) are confirm-guarded — the modal repeats the backend\'s rule, not an invented warning."),
        t("research.hb.uiguide.sec4_p2", "Buttons disabled with a reason quote availability — the reason is a prerequisite diagnosis, not an error. See topic/operations for each command\'s exact semantics."),
        t("research.hb.uiguide.sec4_p3", "strategyId context: commands that need a target take the currently selected registry row (selected via \'trace\'); the drawer shows which strategy it is bound to in its title."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec5_head", "Registry tab"),
      body: [
        t("research.hb.uiguide.sec5_p1", "strategy — strategy_id + version from the registry cache. Truncated with full id on hover; version comes from canonical_version (content-derived, so a changed definition is a new version)."),
        t("research.hb.uiguide.sec5_p2", "lifecycle — StatusPill of the backend\'s lifecycle_state. Filter via the census rail."),
        t("research.hb.uiguide.sec5_p3", "conf / samples / score — from score_payload: confidence (3 decimals), sample_count, final score (2 decimals). \'—\' means the payload did not carry the field; the UI never backfills a default."),
        t("research.hb.uiguide.sec5_p4", "updated — registry row freshness. A stale updated with fresh caption = registry not being rewritten (worker/queue problem); stale with stale caption = the whole subsystem is stale (availability problem)."),
        t("research.hb.uiguide.sec5_p5", "trace — opens the drawer (gates, runs, events, evidence, raw invariant) for that strategy."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec6_head", "Gate queue tab"),
      body: [
        t("research.hb.uiguide.sec6_p1", "Left: queued/running counts grouped by gate type × status as distribution bars — the backlog census (data: queue.queued map flattened to label/count rows)."),
        t("research.hb.uiguide.sec6_p2", "Right: the queue.running list — exactly what the worker is executing right now (gate, strategy, status)."),
        t("research.hb.uiguide.sec6_p3", "Reading a backlog: many QUEUED + healthy worker = large family or many candidates (normal); many QUEUED + STUCK worker = worker problem first, gate problem later; per-gate last_errors surface in the payload when the backend reports them."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec7_head", "Worker tab"),
      body: [
        t("research.hb.uiguide.sec7_p1", "health — the classified health (HEALTHY/DEGRADED/STUCK/FAILED/IDLE/UNKNOWN, thresholds in topic/operations)."),
        t("research.hb.uiguide.sec7_p2", "last beat / cycle / status / last error — heartbeat and runtime counters straight from the worker row. last_error \'none reported\' means exactly that — not \'unknown\'."),
        t("research.hb.uiguide.sec7_p3", "blocked/failed gates (diagnostics) — diagnostics.blocked_gates rendered as the same stepper the drawer uses: blocked (missing upstream) vs failed (ran and said no) drive different fixes."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec8_head", "Analytics tab"),
      body: [
        t("research.hb.uiguide.sec8_p1", "failed gates — heatmap.by_gate distribution + total_failures. Which gate dominates failures tells you WHERE the chain is strict: STATIC_VALIDATION-heavy = schema/identity problems; OOS-heavy = the holdout is doing its job; SCORING-heavy = verdict chain blocks (often DSR/CI/evidence floors)."),
        t("research.hb.uiguide.sec8_p2", "rejection reasons — heatmap.rejection_reasons top 15. These are backend reason strings verbatim; a reason you don\'t recognize belongs in the handbook\'s glossary or is new backend vocabulary (docs lag → update them)."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec9_head", "Retention tab"),
      body: [
        t("research.hb.uiguide.sec9_p1", "The retention.kv list = events/evidence live vs archived counts from GET /api/research/history. Live+archived should agree with the snapshot\'s artifact counts (topic/evidence); a disagreement is a storage finding, not a UI bug."),
        t("research.hb.uiguide.sec9_p2", "Archive-only contract: archived rows are moved, never mutated — counts can shift live→archived over time, totals should not silently drop."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec10_head", "Datasets (v1) tab"),
      body: [
        t("research.hb.uiguide.sec10_p1", "dataset_id + run_count — the v1 dataset inventory derived from real runs (provenance, not configuration). A dataset with runs but no registry rows means discovery found no qualifying family — check floors (topic/discovery)."),
        t("research.hb.uiguide.sec10_p2", "Request errors render the message in tx-bad styling — the tab distinguishes \'no datasets\' (EmptyState) from \'could not fetch\' (error) deliberately; never confuse the two when reporting."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec11_head", "Playbook tab"),
      body: [
        t("research.hb.uiguide.sec11_p1", "Static documentation compiled from backend source: gates, lifecycle states, discovery, scoring, economics, evidence, operations — with verbatim constants tables and operator FAQs."),
        t("research.hb.uiguide.sec11_p2", "Search is AND-term full-text over titles, prose, keywords, params and FAQs (research_handbook.test.js pins the semantics). Cross-link chips (\'see also →\') jump + expand the target entry."),
        t("research.hb.uiguide.sec11_p3", "It renders NO live data on purpose: mixing docs numbers with query numbers in one visual language is how dashboards start lying. Live tabs = backend responses; playbook = prose about the code."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec12_head", "The strategy drawer (trace)"),
      body: [
        t("research.hb.uiguide.sec12_p1", "Chain rail — the six chain steps colored strictly from this strategy\'s own gate rows: green PASSED, red FAILED/ERROR/CANCELLED, pulsing RUNNING/QUEUED, neutral when no row exists. No row = no claim."),
        t("research.hb.uiguide.sec12_p2", "Tabs: Trace (lifecycle/preflight cards + gate pipeline stepper + validation runs), Gates (full ledger with class/ms/retry affordance), Events (persisted timeline), Evidence (immutable vault, expandable payloads), Raw invariant (backend\'s own invariant JSON), Playbook (handbook, deep-linked to the gate-chain overview)."),
        t("research.hb.uiguide.sec12_p3", "Retry buttons appear only for retryable classes (TECHNICAL/DATA) because the API refuses the others — the UI hiding them is honesty, not filtering."),
        t("research.hb.uiguide.sec12_p4", "Cancel run modal: \'becomes CANCELLED (never FAILED); completed gate results are preserved\' — that is RunStatus semantics from evidence.py, quoted not paraphrased."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec13_head", "Accessibility and motion notes"),
      body: [
        t("research.hb.uiguide.sec13_p1", "Every interactive control on this page is a native button or input: keyboard reachable in DOM order, with visible focus rings from the theme; the census rail chips expose aria-pressed for their filter state and the playbook TOC/search carry explicit aria-labels."),
        t("research.hb.uiguide.sec13_p2", "Entrance animations are one-shot (they end at the resting state, no looping) except three deliberate loops that encode LIVE SIGNAL: the worker dot (health is changing), the pipeline flow pulses (chain direction), and the running-step pulse in the drawer rail (work in flight)."),
        t("research.hb.uiguide.sec13_p3", "prefers-reduced-motion: reduce freezes every .rs-* animation via a local guard on top of the theme\'s global rule — count-up KPIs jump straight to the fetched value, entrances render at rest, pulses stop. No information is carried by motion alone: every animated state also exists as text (status words, counts, pill labels)."),
        t("research.hb.uiguide.sec13_p4", "Color is never the only channel: status pills and the drawer rail pair color with the status word itself; the rail chips print raw counts (percentages in hover titles); \'—\' vs 0 is textually distinct. The theme\'s contrast tokens apply unchanged — the playbook introduces no new palette."),
        t("research.hb.uiguide.sec13_p5", "Search-as-you-type filters are announced structurally (result count beside the input, empty state with the query echoed), and expandable entries set aria-expanded on their header button so screen readers track the accordion state."),
      ],
    },
    {
      heading: t("research.hb.uiguide.sec14_head", "Standard workflows"),
      body: [
        t("research.hb.uiguide.sec14_p1", "Certify a candidate: Discover (mint families) → watch queue → drawer Trace until all six rail steps turn green → Registry row reaches VALIDATED."),
        t("research.hb.uiguide.sec14_p2", "Promote: select row → command promote — backend enforces approve_for_live (only SHADOW/VALIDATED → ACTIVE, actor recorded). A LifecycleError message in the verdict line IS the decision."),
        t("research.hb.uiguide.sec14_p3", "Triage a stuck pipeline: Worker health first (STUCK/FAILED?) → Queue backlog second → blocked_gates (blocked=upstream, failed=science) → per-gate failure class (TECHNICAL/DATA = retry button; RESEARCH = needs new evidence, never retry)."),
        t("research.hb.uiguide.sec14_p4", "Audit a disputed number: drawer Evidence tab → open the artifact → recompute content_hash from payload (topic/evidence) → compare snapshot dataset_id/fingerprint. The UI is never the arbiter; the hash is."),
      ],
    },
  ],
  faq: [
    {
      q: t("research.hb.uiguide.faq1_q", "Why does the playbook show \'0.25\' but my gate failed at 0.18R degradation?"),
      a: t("research.hb.uiguide.faq1_a", "The playbook documents the shipped DEFAULT (MAX_ACCEPTABLE_DEGRADATION_R=0.25 in robustness.py). A deployment config may set a different number, and the gate row\'s failure_reason quotes what the backend actually used. Config wins; docs describe defaults; the gate row describes this run."),
    },
    {
      q: t("research.hb.uiguide.faq2_q", "A column shows \'—\'. Is data missing?"),
      a: t("research.hb.uiguide.faq2_a", "— means the backend payload did not carry that field (NOT recorded, not zero). It renders as missing deliberately: a default of 0 would be indistinguishable from a real 0. The source panel (Raw invariant / evidence JSON) shows exactly what was sent."),
    },
    {
      q: t("research.hb.uiguide.faq3_q", "Can I trust the count-up animation?"),
      a: t("research.hb.uiguide.faq3_a", "The animation interpolates the DISPLAY from the previous fetched value to the new fetched value; both endpoints are backend numbers. Reduced-motion users get instant values. If endpoints and display disagree, that is a bug worth reporting — the endpoint values are query data."),
    },
  ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const uiGuideEntry: HandbookEntry = buildUiGuideEntry();

/**
 * Translator-aware copy: call during render with the store's current t();
 * the result changes with the language — never cache it outside render.
 * Returned as an array so the handbook overlay can register whole lanes.
 */
export function uiguideTranslated(t?: ScoringTranslate): HandbookEntry[] {
  return [buildUiGuideEntry(t)];
}
