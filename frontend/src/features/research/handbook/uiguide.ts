/**
 * Handbook — operator UI guide: how to READ /alt/research (topic entry).
 * Static documentation of the page itself; every claim about data sources
 * mirrors useCases.ts / researchApi.ts wiring.
 */
import type { HandbookEntry } from "./types";

export const uiGuideEntry: HandbookEntry = {
  id: "topic/uiguide",
  kind: "topic",
  badge: "PAGE MAP",
  title: "Reading this page — tab-by-tab UI guide",
  subtitle: "What every panel, column, chip and button means, where its data comes from, and the workflows that use them.",
  source: "frontend/src/features/research/{ui/*.tsx,useCases.ts,model.ts}, researchApi.ts",
  seeAlso: ["topic/operations", "topic/lifecycle", "topic/gates"],
  keywords: ["ui", "guide", "tab", "column", "registry", "queue", "worker", "analytics", "retention", "datasets", "drawer", "promote", "cancel", "workflow"],
  sections: [
    {
      heading: "The hero — pipeline map and freshness",
      body: [
        "The hero shows the canonical gate chain (six nodes, chain order from the handbook which the test pins to evidence.py) with a one-line role per gate. It is orientation, not live status: per-strategy gate statuses live in the drawer and the queue tab.",
        "The worker chip carries a live dot keyed to the backend's reported worker status: green pulse HEALTHY, amber DEGRADED, red STUCK/FAILED, grey idle/unknown. The dot never invents health — it colors what summary.worker.status reported.",
        "The FreshnessCaption shows when the v1 status payload was generated and whether a refetch is in flight or errored — if the caption is stale, every number below it is stale too, and that is the first thing to fix.",
        "The 'open strategy handbook →' chip jumps to the Playbook tab — documentation compiled from backend source, clearly separated from live data.",
      ],
    },
    {
      heading: "KPI row (4 cards)",
      body: [
        "Registry total — summary.total over strategy_intelligence_registry. When the subsystem answers available:false the card shows 'n/a' with the backend's reason, never a fake zero.",
        "Validated — by_lifecycle.VALIDATED: gate-certified candidates awaiting/making promotion.",
        "Active strategies — by_lifecycle.ACTIVE: candidates with real dispatch authority (in LIVE mode this is money-moving inventory).",
        "Research worker — the worker status badge plus closed-outcome count when outcome quality is reported. A red badge here explains frozen queues and stale everything else.",
        "The integers count up on change: a motion affordance marking a value as freshly fetched — the value itself is always the backend's.",
      ],
    },
    {
      heading: "Lifecycle census rail",
      body: [
        "Each chip shows a state's row count with a fill bar = share of the registry total (pct printed in the chip's hover title with the raw denominator).",
        "Click a chip to filter the Registry tab to that state (aria-pressed reflects the filter; clicking the active chip clears it). The filter is a query param to GET /api/research/registry — the backend does the filtering.",
        "Chip vocabulary comes from summary.by_lifecycle — the backend's registry progress labels (see topic/lifecycle for state-machine vs progress-label layers).",
      ],
    },
    {
      heading: "Command bar",
      body: [
        "Every button POSTs /api/research/<command> through researchUseCases and renders the backend's verdict line verbatim (CommandResultLine). Destructive commands (cancel, retry, promote) are confirm-guarded — the modal repeats the backend's rule, not an invented warning.",
        "Buttons disabled with a reason quote availability — the reason is a prerequisite diagnosis, not an error. See topic/operations for each command's exact semantics.",
        "strategyId context: commands that need a target take the currently selected registry row (selected via 'trace'); the drawer shows which strategy it is bound to in its title.",
      ],
    },
    {
      heading: "Registry tab",
      body: [
        "strategy — strategy_id + version from the registry cache. Truncated with full id on hover; version comes from canonical_version (content-derived, so a changed definition is a new version).",
        "lifecycle — StatusPill of the backend's lifecycle_state. Filter via the census rail.",
        "conf / samples / score — from score_payload: confidence (3 decimals), sample_count, final score (2 decimals). '—' means the payload did not carry the field; the UI never backfills a default.",
        "updated — registry row freshness. A stale updated with fresh caption = registry not being rewritten (worker/queue problem); stale with stale caption = the whole subsystem is stale (availability problem).",
        "trace — opens the drawer (gates, runs, events, evidence, raw invariant) for that strategy.",
      ],
    },
    {
      heading: "Gate queue tab",
      body: [
        "Left: queued/running counts grouped by gate type × status as distribution bars — the backlog census (data: queue.queued map flattened to label/count rows).",
        "Right: the queue.running list — exactly what the worker is executing right now (gate, strategy, status).",
        "Reading a backlog: many QUEUED + healthy worker = large family or many candidates (normal); many QUEUED + STUCK worker = worker problem first, gate problem later; per-gate last_errors surface in the payload when the backend reports them.",
      ],
    },
    {
      heading: "Worker tab",
      body: [
        "health — the classified health (HEALTHY/DEGRADED/STUCK/FAILED/IDLE/UNKNOWN, thresholds in topic/operations).",
        "last beat / cycle / status / last error — heartbeat and runtime counters straight from the worker row. last_error 'none reported' means exactly that — not 'unknown'.",
        "blocked/failed gates (diagnostics) — diagnostics.blocked_gates rendered as the same stepper the drawer uses: blocked (missing upstream) vs failed (ran and said no) drive different fixes.",
      ],
    },
    {
      heading: "Analytics tab",
      body: [
        "failed gates — heatmap.by_gate distribution + total_failures. Which gate dominates failures tells you WHERE the chain is strict: STATIC_VALIDATION-heavy = schema/identity problems; OOS-heavy = the holdout is doing its job; SCORING-heavy = verdict chain blocks (often DSR/CI/evidence floors).",
        "rejection reasons — heatmap.rejection_reasons top 15. These are backend reason strings verbatim; a reason you don't recognize belongs in the handbook's glossary or is new backend vocabulary (docs lag → update them).",
      ],
    },
    {
      heading: "Retention tab",
      body: [
        "The retention.kv list = events/evidence live vs archived counts from GET /api/research/history. Live+archived should agree with the snapshot's artifact counts (topic/evidence); a disagreement is a storage finding, not a UI bug.",
        "Archive-only contract: archived rows are moved, never mutated — counts can shift live→archived over time, totals should not silently drop.",
      ],
    },
    {
      heading: "Datasets (v1) tab",
      body: [
        "dataset_id + run_count — the v1 dataset inventory derived from real runs (provenance, not configuration). A dataset with runs but no registry rows means discovery found no qualifying family — check floors (topic/discovery).",
        "Request errors render the message in tx-bad styling — the tab distinguishes 'no datasets' (EmptyState) from 'could not fetch' (error) deliberately; never confuse the two when reporting.",
      ],
    },
    {
      heading: "Playbook tab",
      body: [
        "Static documentation compiled from backend source: gates, lifecycle states, discovery, scoring, economics, evidence, operations — with verbatim constants tables and operator FAQs.",
        "Search is AND-term full-text over titles, prose, keywords, params and FAQs (research_handbook.test.js pins the semantics). Cross-link chips ('see also →') jump + expand the target entry.",
        "It renders NO live data on purpose: mixing docs numbers with query numbers in one visual language is how dashboards start lying. Live tabs = backend responses; playbook = prose about the code.",
      ],
    },
    {
      heading: "The strategy drawer (trace)",
      body: [
        "Chain rail — the six chain steps colored strictly from this strategy's own gate rows: green PASSED, red FAILED/ERROR/CANCELLED, pulsing RUNNING/QUEUED, neutral when no row exists. No row = no claim.",
        "Tabs: Trace (lifecycle/preflight cards + gate pipeline stepper + validation runs), Gates (full ledger with class/ms/retry affordance), Events (persisted timeline), Evidence (immutable vault, expandable payloads), Raw invariant (backend's own invariant JSON), Playbook (handbook, deep-linked to the gate-chain overview).",
        "Retry buttons appear only for retryable classes (TECHNICAL/DATA) because the API refuses the others — the UI hiding them is honesty, not filtering.",
        "Cancel run modal: 'becomes CANCELLED (never FAILED); completed gate results are preserved' — that is RunStatus semantics from evidence.py, quoted not paraphrased.",
      ],
    },
    {
      heading: "Accessibility and motion notes",
      body: [
        "Every interactive control on this page is a native button or input: keyboard reachable in DOM order, with visible focus rings from the theme; the census rail chips expose aria-pressed for their filter state and the playbook TOC/search carry explicit aria-labels.",
        "Entrance animations are one-shot (they end at the resting state, no looping) except three deliberate loops that encode LIVE SIGNAL: the worker dot (health is changing), the pipeline flow pulses (chain direction), and the running-step pulse in the drawer rail (work in flight).",
        "prefers-reduced-motion: reduce freezes every .rs-* animation via a local guard on top of the theme's global rule — count-up KPIs jump straight to the fetched value, entrances render at rest, pulses stop. No information is carried by motion alone: every animated state also exists as text (status words, counts, pill labels).",
        "Color is never the only channel: status pills and the drawer rail pair color with the status word itself; the rail chips print raw counts (percentages in hover titles); '—' vs 0 is textually distinct. The theme's contrast tokens apply unchanged — the playbook introduces no new palette.",
        "Search-as-you-type filters are announced structurally (result count beside the input, empty state with the query echoed), and expandable entries set aria-expanded on their header button so screen readers track the accordion state.",
      ],
    },
    {
      heading: "Standard workflows",
      body: [
        "Certify a candidate: Discover (mint families) → watch queue → drawer Trace until all six rail steps turn green → Registry row reaches VALIDATED.",
        "Promote: select row → command promote — backend enforces approve_for_live (only SHADOW/VALIDATED → ACTIVE, actor recorded). A LifecycleError message in the verdict line IS the decision.",
        "Triage a stuck pipeline: Worker health first (STUCK/FAILED?) → Queue backlog second → blocked_gates (blocked=upstream, failed=science) → per-gate failure class (TECHNICAL/DATA = retry button; RESEARCH = needs new evidence, never retry).",
        "Audit a disputed number: drawer Evidence tab → open the artifact → recompute content_hash from payload (topic/evidence) → compare snapshot dataset_id/fingerprint. The UI is never the arbiter; the hash is.",
      ],
    },
  ],
  faq: [
    {
      q: "Why does the playbook show '0.25' but my gate failed at 0.18R degradation?",
      a: "The playbook documents the shipped DEFAULT (MAX_ACCEPTABLE_DEGRADATION_R=0.25 in robustness.py). A deployment config may set a different number, and the gate row's failure_reason quotes what the backend actually used. Config wins; docs describe defaults; the gate row describes this run.",
    },
    {
      q: "A column shows '—'. Is data missing?",
      a: "— means the backend payload did not carry that field (NOT recorded, not zero). It renders as missing deliberately: a default of 0 would be indistinguishable from a real 0. The source panel (Raw invariant / evidence JSON) shows exactly what was sent.",
    },
    {
      q: "Can I trust the count-up animation?",
      a: "The animation interpolates the DISPLAY from the previous fetched value to the new fetched value; both endpoints are backend numbers. Reduced-motion users get instant values. If endpoints and display disagree, that is a bug worth reporting — the endpoint values are query data.",
    },
  ],
};
