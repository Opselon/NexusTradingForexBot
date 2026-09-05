# WAVE 2 QA VERDICT — Ecosystem Scale (Million-User Data Path + GitHub Master)

**Scope:** `tests/critical_suite.txt`, `tests/unit/test_agent14*`, `test_a2*`, `test_dataset*`, `.github/workflows/ci.yml`, `agents/change_control.md`
**HEAD:** 2e3a762d (branch `hermes-subagent/subagent-sa-1-7c54ddfb`, base 69033c54)
**Date:** 2026-09-05
**Mode:** read-only audit, no code edits
**Verdict:** **PARTIAL** — single critical-suite probe covers the clean→train chain; the **remote fetch→clean linkage has no CI-gated test**, so at million-user scale a bypass ships green.

---

## 1) critical_suite coverage (fetch → clean → train)

**In the gate (67 entries):**
- `tests/unit/test_research_task4_dataset.py` — 15 tests (eligibility, dedup, lineage). IN suite → gated on every push.
- `tests/unit/test_a2_data_lineage_bounded.py` — 1 test `test_a2_bounded_dataset_build_and_lineage` on a 5k M1 tail via `build_70d_dataset` → `store.read_dataset` → `verify_70d_artifact` → `SequenceBuilder` with SSoT `CANONICAL_MAX_GAP_US`. This is the **only** end-to-end RAW→CLEAN→FEATURES→LABELS→DATASET→MODEL INPUT probe in the default CI gate (commit 7c821a42, added to suite line 81).

**NOT in the gate (landed on HEAD, zero default CI signal):**
- `tests/unit/test_agent14_dataset_integrity.py` — 19 tests (R1 wall-clock identity, R2 provenance v2, R3 incomplete vs complete, R4 window containment, R5 path traversal, R6 orphan/corrupt meta immutability, R7 fingerprint/tamper/swap/append rejection, R8 4-thread convergence). **Tracked** (`git ls-files` yes), commits 7715e4a8→8ff61ffa→22091be7 reachable from HEAD, but **absent from `critical_suite.txt`** and from `heavy-ci` matrix.
- `tests/unit/test_dataset_split_purge_bug244.py` — BUG-244 split-boundary horizon purge (train/val/test no longer split-adjacent). Commit 69033c54, tracked, **not in `critical_suite.txt` and not in `heavy-ci`** (research-backtest arm runs only 3 files; this is not one).
- `tests/unit/test_70d_dataset_parity_task3.py` — canonical 70D frame/artifact contract (12 tests, hash `235b8fccc96b7e0e`). Tracked, **not in suite**.
- `tests/unit/test_news_keywords_dataset.py` — tracked, **not in suite**.

**Implication:** The hardest fetch-layer hardening (CHG-0061 R1–R8) can regress and CI stays green on every PR/push to `main`.

---

## 2) CI gates (ecosystem reliability)

**`ci.yml` quality job (runs on every push/PR to `main|develop`):**
- `ruff lint` + `ruff format --check` → `continue-on-error: true` but final gate (lines 509–523) fails on `failed|errored`; `blocked` is honest (CHG-0052 — format failure blocks mypy/pytest as `blocked`, not fake `failed`). Good: formatter divergence cannot hide.
- `mypy` and `pytest` are **skipped when the committed tree is dirty** (`if: RUFF_FORMAT_RC==0 || repaired`) — correct root-cause semantics, but means type and test signal is absent on a dirty commit (blocked, not red on those checks individually).
- `pytest` runs **only** `tests/critical_suite.txt` via `pytest -n auto --dist loadgroup` (~5.5 min on 4 workers). Coverage derived from the same run.
- `runtime_gate` (CHG-0051) runs on the same gate.

**What does NOT run on `main` PRs:**
- `heavy-ci` matrix (integration, e2e, `database-provider`, `research-backtest`, `model-validation`) — `if: ref==ci-tests || inputs.full==true` only. So BUG-244, integration, and model-validation suites never gate a normal PR.
- `tests-os.yml` (windows+macos) — `push: branches: [ci-tests]` only + PR; not on push to `main`. The project's primary runtime (Windows packaged EXE, `beforePush.ps1`) has no push-to-main OS gate.
- `qa-deep-assurance.yml` fast lane **does** run on every push to `main` (`deep_assurance.py --fast`) — good, but orthogonal to dataset path.

**Reliability risks:**
1. Single-gate dependency: one file (`critical_suite.txt`) is the entire quality gate. Its omission of `test_agent14*` and `test_dataset_split_purge*` is a silent coverage loss (no CI job warns "suite entry missing").
2. Heavy lanes are branch-gated — a contributor opening a PR against `main` never exercises the research/model lanes that would catch dataset regressions.
3. `data/raw/XAUUSD_M1.csv` is gitignored (`data/raw/**/*`); the A2 probe reads it at runtime. On a fresh runner without the 100k-row CSV, the sole fetch→train probe fails or is skipped — CI cannot distinguish "missing fixture" from "clean failure" without an explicit skip contract.

---

## 3) Bypass gap — where remote fetch can skip cleaning without test failure

**Architecture has two disjoint ingest paths:**

- **Offline bar path (cleaned):** `scripts/dev/regen_70d_clean_dataset.py:load_bars` (sort, height≥60k) → `model_generation/schema_v2.py:compute_70d_frame` / `compute_70d_frame_fast` (sort, feature clipping `|3.0|`, schema `scalp_v3`, finish checks) → `DatasetFactory.build` (`SampleFactory.build_samples` + `_apply_split` with purge/embargo) → `verify_70d_artifact`. The A2 probe exercises this chain.
- **Remote tick path (lightly validated):** `research/mt5_tick_dataset.py:MT5TickDataset.acquire_ticks/acquire_bars` → `dataset_fingerprint` → parquet+`meta.json` → `load()` / `event_source()` → `research/streaming_replay.py` / replay. `acquire_*` only drops `out_of_window` rows and marks `complete`/`out_of_window`; **no OHLC, finiteness, duplicate, out-of-order, or spread sanity gate** at the fetch boundary. Those gates live downstream in `compute_70d_frame`/`TripleBarrierLabeler` — but nothing enforces that a consumer of `MT5TickDataset.load()` or `event_source()` goes through them.

**Concrete bypass (no test would fail today):**
- Poison the adapter: `get_tick_history` returns ticks with `bid=NaN`, `bid>ask`, duplicate `time_msc`, or inverted OHLC bars via `get_rate_history`. `acquire_ticks` fingerprints and caches them. A downstream caller that does `TickEventSource(ds.load(id))` → `StreamingReplayEngine` (or a future million-user feature: "train on my broker's ticks") can reach the replay/training path without `compute_70d_frame` ever running. The only runtime check is `fingerprint` integrity (tamper detection), not semantic validity. `SampleFactory.build_samples` drops rows only on `label_schema.encode` failure or `len(feature_vector)!=dimension` — a dirty OHLC bar with plausible dimensions still becomes a sample.
- `test_agent14_dataset_integrity.py` fuzzes tamper/swap/append/corrupt-meta, but **not** dirty semantics (NaN OHLC, non-finite bid/ask, massive spread, stale gap in (600s,900s], duplicate timestamps with distinct identities). No existing critical-suite test injects dirty remote bars and asserts `verify_70d_artifact` or `SampleFactory` rejects/cleans them.
- Missing contract test: "any path that trains or replays from `MT5TickDataset` must transit `compute_70d_frame`/`verify_70d_artifact` (or equivalent OHLC/finite/dedup/order gates); direct `load()→train` without cleaning is blocked."

---

## 4) GitHub master compliance

- **`agents/change_control.md` CHG-0061:** Full entry present (agent, role, scope `mt5_tick_dataset.py` + `dataset.py` + `dataset_factory.py`, 16 objectives, constraints, boundaries) but **Status: IMPLEMENTING** while code is already on `main` (commits 7715e4a8, 8ff61ffa, 89bbcd57, 22091be7 are ancestors of HEAD). Per master contract §4 lifecycle `PROPOSED→IMPLEMENTING→VERIFIED→READY_FOR_REVIEW→MERGED`, a change that has landed on `main` should be at least `VERIFIED` with evidence. Mismatch: committed without status advance.
- **`agents/taskboard.md` TASK-AGENT14-DATASET:** Row present, matches CHG-0061 (`Agent 14`, `CHG-0061`, `IN_PROGRESS`). Consistent with change_control, same lifecycle lag.
- **Evidence / handoff:** No `docs/agent_handoffs/*agent14*` for CHG-0061 (only `TASK-14-70D-CANDIDATE-LATENCY.md` from an older series). No verification JSON for the 16 objectives. The fix correctness must be inferred from tests, not from a handoff.
- **BUG-244 (69033c54):** No `change_control.md` entry (only a taskboard-adjacent commit). BUG-246 does have a `taskboard.md` row (A2 gap SSoT). Inconsistent disclosure.
- **`beforePush.sh` / CI parity:** `beforePush.sh` line 88 iterates `critical_suite.txt` (same as CI quality job line 222) — parity holds. Tool parity checked by `scripts/ci/gate_parity.py` in `ci-integrity` job.

**Overall contract verdict:** **PARTIAL** — change is registered and committed, but lifecycle/status and evidence discipline lag the code on `main`.

---

## 5) Missing tests & CI risks (what to add without editing code here)

**Must-add to `tests/critical_suite.txt` (or a gated lane):**
1. `tests/unit/test_agent14_dataset_integrity.py` — the entire R1–R8 contract (19 tests). Without it, wall-clock identity, provenance honesty, containment, immutability, and corruption detection are un-gated.
2. `tests/unit/test_dataset_split_purge_bug244.py` — temporal leakage at split boundaries (horizon purge). A single-purge/bar regression would reintroduce lookahead and still pass CI.
3. `tests/unit/test_70d_dataset_parity_task3.py` — at least the hash/dimension/finite/clip/dedup subset, to pin `235b8fccc96b7e0e` and `verify_70d_artifact` beyond the single A2 probe.

**Must-create (new coverage, million-user scale):**
4. `test_remote_fetch_cannot_bypass_cleaning` — property test: a malicious `get_tick_history`/`get_rate_history` that returns NaN/infinite/inverted-OHLC/duplicate/out-of-order rows → `acquire_*` → attempt `build_70d_dataset`/`SampleFactory`/`verify_70d_artifact` must either reject loudly or produce an artifact that passes `verify_70d_artifact` with zero non-finite/out-of-range/dup defects. Proves the cleaning gate is on the **training** path, not just the offline CSV path.
5. `test_mt5_tick_dataset_offline_enforcement` — proves any training entry point that consumes `MT5TickDataset` must go through `compute_70d_frame`/`verify_70d_artifact` (or explicitly fail if bypassed). Prevents a future "user fetch → direct train" shortcut.
6. CSV-absent runner contract: `test_a2_data_lineage_bounded.py` should `pytest.skip` with an explicit reason when `data/raw/XAUUSD_M1.csv` is absent, and CI should assert "skipped count == expected" so a missing fixture does not silently reduce coverage.
7. Concurrency + cache-isolation under load: extend R8 beyond 4 threads (e.g., 16-way + cross-symbol/cross-window isolation — same symbol different windows must not collide; interrupted/corrupt cache must not yield a usable dataset).

**CI wiring risks to address:**
- Move dataset-critical suites from branch-gated `heavy-ci` into the default PR gate, or add a `dataset` lane that runs on `pull_request: [main]` (lightweight: the two agent14 + bug244 suites, ~30s).
- Add a CI check that `tests/critical_suite.txt` contains every `test_agent14*` / `test_dataset*` file that is tracked — fail if a dataset hardening test lands without a suite entry (prevents the current silent omission from recurring).
- Make `tests-os.yml` also run on `push: [main]` (or at least on `pull_request: [main]` + `push: [main]`) so the Windows primary runtime is gated on the same manifest.

---

## Summary

- **PASS:** `ruff`/`mypy`/`pytest`/`coverage`/`runtime_gate` gates are well-instrumented (artifacted, manifest-hashed, blocked-vs-failed honest). The A2 bounded probe gives one deterministic fetch→clean→train pin that runs on every push.
- **PARTIAL/FAIL at ecosystem scale:** The fetch acquisition hardening (CHG-0061, 19 tests) and the split-boundary purge (BUG-244) are **landed but not executed in any PR/push gate** — they live on `main` without a CI wire. A remote-user fetch can therefore cache dirty data and reach downstream consumers without traversing the bar-cleaning gates, and no test would fail.
- **GitHub contract:** Registered (`change_control.md` + `taskboard.md`) but lifecycle status (`IMPLEMENTING` on `main`) and evidence handoff lag the code.

**Recommendation before scaling to million users:** Gate `test_agent14_dataset_integrity` + `test_dataset_split_purge_bug244` in the default CI (critical suite or a new `dataset` PR lane) and land the bypass-cleaning property test (item 4 above) as a blocking regression. Until then, treat the remote-fetch data path as **un-gated**.
