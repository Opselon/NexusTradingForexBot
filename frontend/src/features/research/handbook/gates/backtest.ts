/**
 * Handbook entry — BACKTEST gate (chain position 2 of 6).
 * Compiled from src/nexus_scalp/research/{evidence,backtest,metrics,models}.py.
 */
import type { HandbookEntry } from "../types";

export const backtestEntry: HandbookEntry = {
  id: "gate/BACKTEST",
  kind: "gate",
  badge: "GATE 2 / 6",
  title: "Backtest",
  subtitle: "Deterministic, friction-aware replay over the candidate's own recorded trades.",
  source: "src/nexus_scalp/research/backtest.py, metrics.py, models.py",
  seeAlso: ["gate/WALK_FORWARD", "topic/economics", "topic/pipeline", "topic/evidence"],
  keywords: ["backtest", "deterministic", "friction", "econ", "replay", "metrics", "expectancy"],
  sections: [
    {
      heading: "What it computes",
      body: [
        "The backtest engine runs compute_backtest over a decided sample partition of the research dataset. Because the experience ledger already records realized R, exit PnL, holding duration and MAE/MFE for every executed+closed trade, the backtest reconstructs the strategy's performance without inventing fills (backtest.py module docstring, spec 12/13).",
        "It is DETERMINISTIC by contract: same dataset + strategy version + configuration + execution assumptions produce the same result byte-for-byte. The run snapshot (evidence.py::ResearchRunSnapshot) freezes exactly those inputs so any result can be re-derived later.",
        "The output BacktestResult feeds everything downstream: walk-forward folds compare against its baseline, the OOS gate measures relative degradation against it, robustness stresses its assumptions, and scoring consumes its performance dimension.",
      ],
      bullets: [
        "Inputs: candidate (entry/exit/risk logic), family-restricted sample partition, execution assumptions bundle.",
        "Outputs: BacktestResult — expectancy in R, win/loss statistics, drawdown, per-trade detail backing the aggregate.",
        "Side effects: none on the ledger. The gate writes gate rows, events and one evidence artifact; immutable stores are append-only.",
      ],
    },
    {
      heading: "Friction is part of the answer, not a footnote",
      body: [
        "Every fill pays spread and slippage from the execution-assumptions bundle. Effective per-trade friction is min(spread_ticks + slippage_ticks, max_slippage_ticks) — the same semantics research.metrics uses when it prices fills (robustness.py::_effective_friction).",
        "ECON v1: with assumptions=None the engine runs PRODUCTION-LIKE economics — conservative evidence-derived friction, the required-swap validation contract, and a sized economic re-valuation (backtest.py BacktestEngine docstring).",
        "Passing a legacy ExecutionAssumptions bundle keeps legacy friction semantics but LABELS the run FRICTIONLESS_RESEARCH: explicit construction is an explicit analytical opt-in, and no sized view is fabricated without an EconomicAssumptions companion.",
        "The E1 zero-friction guard (models.py::ensure_not_zero_friction) refuses to RUN a bundle with spread_ticks=0 AND slippage_ticks=0 unless the caller passes allow=True. Zero cost silently overstates every strategy ever tested — the audit-E1 defect this guard memorializes.",
      ],
    },
    {
      heading: "Temporal hygiene",
      body: [
        "Splits are produced by splitting.py::split_temporal with purge and embargo windows so no validation sample leaks information across a cut boundary from a training window (defaults: purge 300 s, embargo 60 s — see topic/economics).",
        "The backtest itself never crosses a split it was not given: the partition boundary is an input, and the engine's determinism means the same partition always yields the same numbers.",
      ],
    },
    {
      heading: "Reading the verdict",
      body: [
        "A PASSED backtest says: on this candidate's own recorded trades, after paying modeled friction, expectancy and the recorded statistics are as reported. It does NOT say the edge survives unseen data — that is exactly what walk-forward, OOS and robustness gates exist to test.",
        "Expectancy is expressed in R (multiple of initial risk), which keeps strategies with different absolute position sizes comparable. A backtest expectancy is a sample statistic; scoring.py's small-sample logic decides how much of it counts as evidence.",
        "FAILED here is a RESEARCH-class failure: the numbers did not clear the threshold. It is terminal for the run (is_terminal_gate includes FAILED) and is never retryable — retrying identical inputs would produce identical numbers.",
      ],
    },
    {
      heading: "Common failure reasons",
      body: [
        "INSUFFICIENT_TRADES / sample floors — the family had too few executed+closed observations. This is the ONE failure class with a second chance: discovery routes candidates that fail only on sample size with positive expectancy into the EVIDENCE_BUILDING track (lifecycle.py adjacency, PHASE 25), where they wait for more data instead of being rejected outright.",
        "Zero-friction refusal (ZeroFrictionError) — canonical costs failed to load and the caller did not opt in. Check configs/execution_assumptions.json availability; provenance is reported as CANONICAL_COSTS or FALLBACK_ZERO (models.py::default_research_assumptions).",
        "Family partition empty — the candidate's discovery_evidence.sample_ids no longer resolve against the current dataset (retention/pruning moved the rows). The gate fails loudly rather than silently widening to the full heterogeneous dataset.",
      ],
    },
  ],
  params: [
    {
      name: "determinism contract",
      value: "same dataset+version+config+assumptions => same result",
      meaning: "Backtest numbers are reproducible from the run snapshot alone.",
      ref: "backtest.py module docstring",
    },
    {
      name: "friction model",
      value: "min(spread + slippage, max_slippage_ticks)",
      meaning: "Per-trade cost actually paid by the deterministic backtest.",
      ref: "robustness.py::_effective_friction (research.metrics semantics)",
    },
    {
      name: "E1 guard",
      value: "ZeroFrictionError when spread=0 AND slippage=0 (unless allow=True)",
      meaning: "Refuses to run a zero-cost backtest that would overstate performance.",
      ref: "models.py::ensure_not_zero_friction",
    },
    {
      name: "cost provenance",
      value: "CANONICAL_COSTS | FALLBACK_ZERO",
      meaning: "Whether costs loaded from configs/execution_assumptions.json or the frozen zero default.",
      ref: "models.py::default_research_assumptions",
    },
  ],
  faq: [
    {
      q: "Backtest passed but the strategy was rejected — why?",
      a: "Because passing in-sample replay is only gate 2 of 6. VALIDATED requires BACKTEST, WALK_FORWARD, OOS, ROBUSTNESS and SCORING all PASSED (evidence.py invariant), and the scoring verdict has its own hard gates (bootstrap CI above breakeven, DSR floor, evidence floor).",
    },
    {
      q: "Can I re-run a FAILED backtest?",
      a: "Not as a retry of the same evidence. FAILED is a terminal, RESEARCH-class gate status and retry-gate only accepts TECHNICAL/DATA failures. To re-evaluate, the candidate needs new data (EVIDENCE_BUILDING track) or a new discovery after the dataset changes.",
    },
    {
      q: "Does the backtest place real orders?",
      a: "Never. Research is offline/background (pipeline.py spec 31/32/42) and cannot reach the live tick path; nothing in this gate can place, modify or close an order.",
    },
  ],
};
