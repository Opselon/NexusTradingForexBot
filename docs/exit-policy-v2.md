# Exit policy v2, calibration, and the web response path

Three changes that share one theme: **stop deciding with numbers that do not
mean what the gate thinks they mean.**

| Area | Before | After |
|---|---|---|
| Risk-free trigger | flat `$15` (≈0.15R on a 0.10-lot XAUUSD plan) | R-anchored, ATR-floored, dwell-gated ladder |
| Stop target | entry price | cost-aware lock (spread + commission + buffer) |
| Probability gate | raw softmax | temperature-calibrated softmax (`artifacts/calibration.json`) |
| Exit geometry | hand-tuned constants | learned per regime from the ledger (`artifacts/exit_policy.json`) |
| Web responses | uncompressed, uncacheable | gzip + strong ETag/304 |
| Performance proof | none | `tests/bench/run_benchmarks.py` + CI job |

## 1. Why the position looked "risk-free immediately"

`apply_breakeven_lock` fired on **either** `BREAKEVEN_PROFIT_USD = $15` **or**
`0.15R` **or** `1.5 × ATR`. On a 0.10-lot XAUUSD plan with a $150 risk budget
one R is $15 — so the flat dollar trigger *was* the R trigger, and both were a
fraction of the distance a scalp needs to prove itself. The stop jumped to the
entry on noise, and the first normal pullback turned every developing winner
into a scratch. The reported symptom ("it opens, then goes risk-free, then
does nothing") is that single constant, not the model.

Two secondary defects surfaced while fixing it:

* a position restored from a restart had no `_initial_risks` entry, so
  `risk_usd` was 0 and **no** protection path could ever fire;
* `_sl_is_beyond` returned `False` for a zero stop (`current = 0`), which read
  as "already protected" and skipped the ticket forever.

Both are fixed and regression-tested (`test_exit_policy_geometry.py`).

## 2. What the policy computes now

`ExitProtectionPolicy.plan()` (`execution/lifecycle/exit_policy.py`) answers one
question per tick, with geometry instead of dollars:

1. **Where is 1R?** `risk_usd / (volume × contract_size)`, or — after a restart
   — the position's own stop distance.
2. **Is the move real?** The trigger must clear both an R floor for the regime
   (`TREND` earlier than `RANGE`) and a fraction of the symbol's ATR. A move
   inside one ATR of noise can never trigger protection.
3. **Has it persisted?** `min_hold_seconds` (12s) plus MFE persistence: a spike
   that immediately reverses does not count.
4. **What is the first rung?** `PARTIAL_DE_RISK` lifts the stop to −0.45R, not
   to the entry: the trade stops being a full loss without being scratched.
5. **Where does the lock go?** `entry ± (spread + commission per unit + buffer)`
   — the stop is only "risk-free" once it actually covers the round trip.

The ladder means a winner that gives back 30% of its move keeps a profit
instead of losing the trade, and a winner that never develops never gets a
premature stop.

`AlgoConfig.exit_policy_v2_enabled` (default `True`) keeps the legacy path
reachable for one release so a replay can be diffed against it.

## 3. Self-learning: the ledger decides the floor

`experience/exit_optimizer.py` learns **one number per regime** — the R level at
which protection starts. The statistic is the ledger's own round-trip curve,
not a heuristic:

> Among closed trades that reached `T` R of favourable excursion, what share
> still closed negative?

If that share is high at `T`, a stop at `T` buys nothing (the move reverses
anyway) and the floor belongs higher. The learner picks the **first** grid level
whose bucket is at or below `target_negative_rate` (25%), requires ≥10 trades
in the bucket and ≥`min_samples` (40) per regime, and clamps into a safe
envelope (`0.8R … 2.5R`). Anything else keeps the baseline — an unproven
regime never gets a new geometry.

Validation and publication:

```bash
# inspect without touching anything
python scripts/learn_exit_policy.py --dry-run

# publish (atomic replace; the runtime reads it on the next construction)
python scripts/learn_exit_policy.py --out artifacts/exit_policy.json

# offline / CI reproduction
python scripts/learn_exit_policy.py --from-csv trades.csv --dry-run
```

The artifact is versioned, carries its sample sizes, seed and lift, and is
byte-identical for the same ledger. A malformed artifact means "no learned
data" — the defaults stay live; the learner can never disable protection.

`counterfactual_r` values a candidate **as if** a protected trade never runs to
target, so a change has to earn its lift against the trades it would have cut.

## 4. Calibration: making "60%" mean 60%

`models/calibration.py` fits a single temperature on held-out logits
(golden-section search on NLL, no RNG) and exposes the ECE before/after. The
policy applies it once, right after probabilities are parsed
(`SignalPolicy._calibrated`), so the NO_TRADE read, the dedup fingerprint and
every confidence gate share one scale. Fail-open: no artifact, an absurd
temperature, or a malformed file returns the raw vector.

## 5. Web response path

`web/performance.py` is one pass over buffered GET/HEAD responses:

* **304** on `If-None-Match` with a blake2b ETag over the body — the dashboard
  polling loop stops re-downloading what it already has;
* **gzip** (level 6) above 1 KiB for JSON/text, with `Vary: Accept-Encoding`;
* **never** for `text/event-stream`, non-200s, POST bodies or anything over
  8 MiB, which are replayed verbatim;
* `Server-Timing: app;dur=…` so the effect is visible in devtools.

Measured on this repo's own benchmark (117 KB JSON dashboard payload):

| path | before | after |
|---|---|---|
| first load | 117,817 B | 13,040 B (9.04× smaller) |
| repeat poll | 117,817 B | 0 B (304) |
| 3k-row optimizer run | 346 ms | 20.7 ms |

## 6. Running the benchmarks

```bash
python tests/bench/run_benchmarks.py --write-baseline --baseline-json .bench-baseline.json
python tests/bench/run_benchmarks.py --fail-on-regression --max-regression-pct 15
```

Each benchmark has an absolute budget (catches algorithmic regressions) and an
optional baseline comparison (catches drift). The harness measures its own noise
floor by repeating the cheapest call, so a delta smaller than machine noise is
never reported as a regression. CI runs it on every PR touching `web/`,
`models/`, `experience/` or the bench itself, uploads the JSON report and
comments the table on the PR (`.github/workflows/perf-benchmark.yml`).

## Invariants this work must not break

* No intelligence module gains order authority; the learner writes a JSON
  artifact and nothing else.
* Protection can only tighten a stop within the policy's envelope; a bad or
  missing artifact leaves the defaults live.
* Every gate consumes calibrated probabilities, and calibration failure is a
  no-op, never a veto.
* SSE is never buffered.
