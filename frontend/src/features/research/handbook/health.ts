/**
 * Handbook — health, preflight, diagnostics: the three "why" lenses
 * (topic entry). Compiled from API.md endpoints + the page's own wiring
 * (ResearchPage ResearchDiagMini, StrategyDrawer preflight card).
 */
import type { HandbookEntry } from "./types";

export const healthEntry: HandbookEntry = {
  id: "topic/health",
  kind: "topic",
  badge: "WHY LENSES",
  title: "Health, preflight, diagnostics — three lenses on 'why?'",
  subtitle: "Why is the registry empty · can this strategy run · what is blocking — three endpoints, three questions, no overlap.",
  source: "src/nexus_scalp/web/debug_research_routes.py, API.md (/health, preflight, /diagnostics)",
  seeAlso: ["topic/operations", "topic/discovery", "topic/uiguide"],
  keywords: ["health", "preflight", "diagnostics", "blocked", "blockers", "why", "empty", "source trades", "attempts", "readiness"],
  sections: [
    {
      heading: "Lens 1 — /api/research/health: WHY is the registry (not) full?",
      body: [
        "The Registry tab's empty-state hint says it plainly: '/api/research/health explains WHY (source trades, rejections, attempts).' — health is the funnel question: how many source trades existed, how many attempts were made, how many candidates each floor/reason rejected.",
        "Funnel reading (source → registry): source trades closed and ingested → experiences recorded → families formed → floors applied (8/20 +0.10R) → candidates minted → gates run → registry rows. Health exposes the stage counts so 'empty registry' decomposes into WHERE the funnel emptied.",
        "Stage-by-stage diagnoses — read the funnel top-down; each bullet names the stage, the signal you will see, and the subsystem to check first (Analytics' rejection heatmap mirrors the same rejection counters for cross-checking):",
      ],
      bullets: [
        "source trades ≈ 0 — no executed+closed economic observations reached the ledger: ingestion/experience problem, upstream of research entirely.",
        "trades exist, attempts 0 — discovery has not run (press Run discovery) or ran against a different dataset_id than you expect.",
        "attempts > 0, rejections high — floors doing their job: read rejection_reasons (also mirrored in Analytics heatmap) to see which floor rejected how many families.",
        "families minted, no gate rows — candidates exist but validation never started (queue/worker lens, see Lens 3 / topic/operations).",
        "gate rows exist, registry rows missing — registry cache issue: self-heal rebuilds derived intelligence (topic/registry).",
      ],
    },
    {
      heading: "Lens 2 — preflight: CAN this strategy run right now?",
      body: [
        "The drawer's Trace tab shows a Preflight card: preflight.status (StatusPill) plus preflight.blockers joined verbatim — or 'no blockers reported' when the backend returned none.",
        "Preflight is readiness, not history: it answers 'if I pressed validate/promote NOW, what would stop it?' — dataset present, subsystem available, upstream gates satisfied, schema coherent. It is the check you want BEFORE burning a queue slot.",
        "Reading the pill: a non-green status WITH named blockers = fix the blockers (each blocker string names its prerequisite); green + 'no blockers reported' = the backend knows of nothing stopping the next command — if the command still fails, the failure message quotes the rule it hit (legal refusals explain themselves).",
        "Honest absence discipline applies: if preflight is not reported, the card says so — the UI does not simulate a preflight locally (a UI-computed 'ready' that the backend then refuses would teach operators to distrust every status).",
      ],
    },
    {
      heading: "Lens 3 — /diagnostics: WHAT is blocking the chain?",
      body: [
        "The Worker tab embeds diagnostics.blocked_gates as the same stepper the drawer uses: each row carries gate_type, strategy_id, status and failure_reason — what is stuck and on whom.",
        "blocked_gates are BLOCKED-status rows specifically: the gate cannot START because upstream evidence is missing — distinct from FAILED (ran, said no) and QUEUED (waiting for worker capacity). The three drive three different fixes (topic/glossary has the one-liners).",
        "blocked_gates routing: find the blocking step by chain order (topic/gates) — an OOS BLOCKED row means the WALK_FORWARD artifact it needs is absent; produce upstream evidence (re-run the upstream gate) rather than retrying the blocked one.",
        "Diagnostics complements health: health explains EMPTY FUNNELS (no candidates), diagnostics explains JAMMED CHAINS (candidates that cannot advance). If both are clean and something still looks wrong, the drawer's per-strategy Events/Evidence tabs are the last mile (append-only truth, topic/evidence).",
        "These three lenses are also the reporting protocol: an operator bug report that includes health + preflight + diagnostics answers (data? readiness? jam?) usually needs no follow-up round-trip.",
      ],
    },
    {
      heading: "Choosing the right lens (cheat sheet)",
      body: [
        "Empty or suspicious registry counts → health (funnel).",
        "Before pressing a command → preflight (readiness + blockers).",
        "Queue not moving / chain stuck → diagnostics blocked_gates + Worker health + Queue tab (jam).",
        "One specific strategy misbehaving → drawer Trace/Gates/Events/Evidence (per-strategy append-only record).",
        "Every panel shows 'unavailable' → availability reasons (topic/operations) — fix the prerequisite subsystem first; the other lenses cannot answer while detached.",
      ],
    },
  ],
  params: [
    {
      name: "health funnel",
      value: "source trades → attempts → rejections → families → rows",
      meaning: "The stage counts /health exposes for 'why empty?'.",
      ref: "API.md /api/research/health",
    },
    {
      name: "preflight shape",
      value: "{ status, blockers: string[] }",
      meaning: "Readiness verdict + named prerequisites (drawer card).",
      ref: "API.md preflight / drawer wiring",
    },
    {
      name: "diagnostics shape",
      value: "blocked_gates: [{ gate_type, strategy_id, status, failure_reason }]",
      meaning: "Jammed-chain census rendered by the worker-tab stepper.",
      ref: "API.md /api/research/diagnostics",
    },
    {
      name: "blocked vs failed",
      value: "BLOCKED = upstream missing · FAILED = evidence negative",
      meaning: "Different fixes: produce upstream evidence vs act on statistics.",
      ref: "evidence.py / pipeline diagnostics",
    },
  ],
  faq: [
    {
      q: "Health says attempts > 0 but I see no candidates — contradiction?",
      a: "No: attempts counts discovery attempts, not minted candidates. Attempts with zero mints = every family missed a floor — health's rejection counters plus Analytics' rejection_reasons name the floor that rejected them.",
    },
    {
      q: "Preflight is green but validate failed — which do I believe?",
      a: "The command's verdict line: it quotes the rule the backend actually enforced (preflight reports what it knows at its snapshot time; the command enforces atomically at execution). If this reproduces, capture both strings — that mismatch is itself a finding worth reporting.",
    },
    {
      q: "Should I retry a BLOCKED gate?",
      a: "Retrying the blocked gate accomplishes nothing — it is waiting for upstream evidence. Re-run the UPSTREAM gate (find it by chain order), then the blocked gate proceeds. Retry buttons exist for TECHNICAL/DATA failures, which are a different state.",
    },
    {
      q: "Why not merge health/preflight/diagnostics into one panel?",
      a: "They answer different questions at different times (history, readiness, jam) and are separate endpoints with separate availability. Split lenses keep each answer honest — one mega-panel would blur which question a given number is answering.",
    },
  ],
};
