/**
 * Handbook — experiments & execution stack (topic entry).
 * Compiled from src/nexus_scalp/research/{forward_test,streaming_replay,
 * event_source,mt5_tick_dataset,replay_benchmark}.py module docstrings
 * (CHG-0035 execution stack; read from source during playbook authoring).
 */
import type { HandbookEntry } from "./types";

export const experimentsEntry: HandbookEntry = {
  id: "topic/experiments",
  kind: "topic",
  badge: "CHG-0035",
  title: "Experiments — forward tests, replays, and the frozen-cutoff rule",
  subtitle: "How TRUE FORWARD_TEST differs from walk-forward OOS, and why frozen artifacts make it trustworthy.",
  source: "src/nexus_scalp/research/{forward_test,streaming_replay,event_source,mt5_tick_dataset,replay_benchmark}.py",
  seeAlso: ["gate/WALK_FORWARD", "gate/OOS", "topic/evidence"],
  keywords: ["forward test", "experiment", "freeze", "cutoff", "streaming replay", "counterfactual", "benchmark", "event source", "mt5", "copy_ticks", "future data"],
  sections: [
    {
      heading: "ForwardTestExperiment — frozen cutoff",
      body: [
        "forward_test.py implements experiment_type=FORWARD_TEST: capture FREEZES at the cutoff — model, scaler, strategy fingerprints + schema hash + git commit — and the frozen artifact bytes are copied into artifacts/forward_test/<run_id>/ (taskboard TASK-RESEARCH-EXEC-STACK, CHG-0035).",
        "Future-data isolation is strict: streaming rows must satisfy timestamp > cutoff as they flow through the SHARED engine — the experiment sees only history that existed at the freeze, which is what 'forward' means here.",
        "The freeze is RE-VERIFIED post-run: bytes on disk must still hash-match what was frozen. A mismatch invalidates the run rather than producing a suspicious-but-plausible result.",
        "Distinction to keep straight: walk-forward OOS (gate 4's feeder) is purged/embargoed validation over recorded folds; FORWARD_TEST is a point-in-time frozen-artifact experiment. Both fight leakage; they answer different questions (fold generalization vs 'what would the frozen model have done next').",
      ],
    },
    {
      heading: "StreamingReplayEngine — one engine, logical clock",
      body: [
        "One StreamingReplayEngine for counterfactual evaluation: logical clock (zero sleeps), incremental BarAggregator, causal 50D + news + liquidity features computed AT T (never peeking ahead), local 70D bundle inference once per session, frozen SignalPolicy, RiskEngine, direction-aware simulated fills on historical bid/ask, tick SL/TP first-touch, logical latency, ledger MFE/MAE.",
        "NO order_send — enforced by test, not by convention: the replay stack physically cannot place orders (test_research_execution_stack.py asserts the surface).",
        "Per-candidate freshness: each candidate evaluates over the IDENTICAL historical event stream with a fresh engine; event-hash agreement between candidates on the same stream is ENFORCED (violation aborts the run) — divergence means the stream was not actually identical, which would invalidate the A/B.",
        "evaluation_mode=COUNTERFACTUAL_REPLAY is stamped on every block: replay results can never be mistaken for executed behavior — the experience ledger (executed trades) stays the sole executed-behavior record.",
      ],
    },
    {
      heading: "HistoricalEventSource + MT5 tick cache",
      body: [
        "event_source.py provides Bar/Tick/Chunked historical sources with validation and an explicit DATA_ERROR path — bad data fails loud instead of silently shortening a replay.",
        "mt5_tick_dataset.py caches MT5 ticks via the already-probed adapter surface (copy_ticks_range, COPY_TICKS_ALL) so that after acquisition the stack runs OFFLINE — replays are reproducible without live terminal dependence.",
        "Provenance travels: research_run_snapshots carry feature_schema_id, feature_dimension, model_id, git_commit, with honest NOT_RECORDED — no backfill invention (CHG-0035 v2 snapshot rules, topic/evidence).",
      ],
    },
    {
      heading: "ReplayCandidateBenchmark (champion/challenger)",
      body: [
        "replay_benchmark.py evaluates CHAMPION / CHALLENGER / CONTROL (A/B/C) over the identical stream — deterministic benchmark_id + ledger_content_hash per candidate (same-stream, same-hash discipline).",
        "The artifact is explicitly NOT training data (separation_note): evaluation streams can never leak back into training, or the benchmark would grade its own inputs.",
        "This is the sanctioned way to compare a candidate against the incumbent without risk: counterfactual replay first, shadow observation second, ACTIVE only after both plus an operator.",
      ],
    },
  ],
  params: [
    {
      name: "experiment_type",
      value: "FORWARD_TEST",
      meaning: "Frozen-cutoff forward experiment (freeze + strict timestamp>cutoff).",
      ref: "forward_test.py",
    },
    {
      name: "freeze payload",
      value: "model/scaler/strategy fingerprints + schema hash + git commit",
      meaning: "What gets frozen and copied to artifacts/forward_test/<run_id>/.",
      ref: "forward_test.py (CHG-0035)",
    },
    {
      name: "clock",
      value: "logical (zero sleeps)",
      meaning: "Replay timing is simulated — speed and determinism, no wall-clock drift.",
      ref: "streaming_replay.py",
    },
    {
      name: "order surface",
      value: "NO order_send (test-enforced)",
      meaning: "The replay stack cannot place orders — enforcement lives in tests.",
      ref: "tests/integration/test_research_execution_stack.py",
    },
    {
      name: "evaluation_mode",
      value: "COUNTERFACTUAL_REPLAY",
      meaning: "Stamped on every block — replay never masquerades as executed behavior.",
      ref: "replay_benchmark.py",
    },
    {
      name: "tick acquisition",
      value: "copy_ticks_range COPY_TICKS_ALL (offline after acquisition)",
      meaning: "MT5 tick cache — replay reproducible without a live terminal.",
      ref: "mt5_tick_dataset.py",
    },
  ],
  faq: [
    {
      q: "Forward test vs OOS gate — which is authoritative for VALIDATED?",
      a: "The gate chain: OOS (gate 4) with its evidence artifact is what scoring and VALIDATED consume. FORWARD_TEST experiments are sanctioned counterfactual studies with frozen cutoffs — they inform decisions but are not chain gates.",
    },
    {
      q: "Why re-verify the freeze AFTER the run?",
      a: "Because a freeze that silently changed during the run would make every result attributable to an unknown model. Hash mismatch invalidates the run — trustworthiness over throughput.",
    },
    {
      q: "Can replay results be used as experience-ledger data?",
      a: "No — evaluation_mode=COUNTERFACTUAL_REPLAY is stamped everywhere and the benchmark artifact explicitly excludes itself as training data. The ledger records executed behavior only; replay records what WOULD have happened.",
    },
  ],
};
