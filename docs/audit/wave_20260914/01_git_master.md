# Lane 01 — Git Master Forensics & Coordination Matrix (NSE wave 2026-09-14)

**Base:** `nse/master-active-scalper-wave` @ `9431edd2` (== origin/main at wave open; wave-open commit `c60f0f2f` on top is lane-0 docs only).
**Note:** origin/main advanced DURING this audit — PR #193 (role5 closeout, docs-only) squash-merged at 2026-09-14T00:07Z → remote main tip is now `fd95a7b0`. Wave lanes should rebase onto ≥ `fd95a7b0` before final reports.
**Method (all VERIFIED = command executed this session):** `git merge-base --is-ancestor` per hash, `git cat-file -e HEAD:<path>` per deliverable, `git grep` on HEAD trees, GitHub REST via `gh api` (PR/branch state), `git branch -r --no-merged`, `git rev-list --left-right --count`.

**Auth note:** the token embedded in `git remote get-url origin` (`gho_…`, 13 chars, push-scope, never printed) returns **401 Bad credentials against the REST API** — same as `gh auth token` piped to curl. All PR data below was pulled via the `gh` CLI's own keyring auth, which works. Taskboard row TASK-ALT-UI-PRO independently recorded this ("API token in remote URL is push-scope: PR API 401"). Lanes must not script REST calls with the remote token.

---

## 1. Open PRs (VERIFIED via gh api)

| PR | Head | State | Verdict |
|---|---|---|---|
| #193 | role5/closeout-192 | open at audit start → **MERGED fd95a7b0 mid-audit** | docs-only swarm_log closeout |

**Zero open PRs remain.** Every "PR open/pending" taskboard row below is stale.

## 2. Branch triage (VERIFIED)

- **Merged (strict ancestry into HEAD):** only `origin/main` + `origin/nse/master-active-scalper-wave`. The repo is **squash-merge policy** — branch ancestry almost never proves landing; content+PR-state do (see §1/§3).
- **Merged by PR, branch left remote (squash):** `agent/agent4-mlrepro-next`(partial — see below), `feat/alt-ui-pro-ux`(#166 merged but branch carries 23 further unmerged W2 commits), `origin/ui/lane3|4|5` (14 unique commits each — **active next-wave UI lanes**, not abandoned), `origin/role5/closeout-192`(#193, just merged), `origin/swarm/docs-run12` (PR #119 ABANDONED, content superseded via #118/#120 per its own commit message — abandon confirmed).
- **Abandoned (closed PR, never merged) — 19 in last 100 PRs:** #185 (release-9.0.12 taskboard row; content absorbed via `04994b0e` squash per RELEASE-9.0.12 row), #169 (deplock regen), **#165 (recovery/c4-uv-input-fix — deliberately withdrawn, wrong fix, superseded by #168)**, #143/#105/#102/#101 (dependabot superseded), #140 (bug258-news — landed via #138), #135 (ci-trigger-contract — superseded by #134), #133, #130, #127/#126 (agent3 wave2 — content landed other carriers), #119, #116 (a17-reland — superseded by main-sync #129), #99/#98/#97/#96 (jules/forensic reports).
- **Local debris:** 95 local branches; **24 have upstream `[gone]`** (remote ref deleted after merge) — all recovered-landed lanes (bug270, bug266-auth, c4-*, wip-lanes, agent10, fi9, alt-ui…). ~65 further local branches are unmerged leftovers of the 09-03–09-11 swarm (many pre-squash). **Unmerged local P0-relevant carriers to watch:** `agent/hermes/bug254-audit-exec-migration` (ahead 3 — BUG-256 migration; content NOW on main via `07d83fc1` #129 + executions_idempotency.py PRESENT at HEAD → branch is STALE, do not re-merge), `agent/hermes/p0-parity-trust-integrity` (2 unique commits, Agent-4 seed net — main already carries `c92d3bd2` test_training_seed_reproducibility as ancestor → likely dup), `agent4-mlrepro-next` (2 unique: multi-seed **dispersion driver** `108efb97` — NOT on main; real open work), `agent2-eval-governance` (ahead 1: `test_eval_governance_p0_4.py` 754-line regression net + artifact_store scaler_sha256 stamping — **NOT on main**, see §5).

## 3. Status-vs-HEAD matrix for non-closed taskboard rows

Legend: ✅ content VERIFIED at HEAD (via cat-file/grep/hash, hash noted) · 🟡 partially verified · ❌ not at HEAD.

### DONE-at-HEAD but row says IN_PROGRESS / PR-open / pending (19 rows)

| Row (line) | Claimed | Evidence at HEAD |
|---|---|---|
| TASK-BUG270-HEADERSPLIT (12) | IN_PROGRESS (PR open) | PR **#184 MERGED** `9d531476` (ancestor) |
| TASK-A10-FINAL-C4 (13) | PR open recovery/c4-a10-final | PR **#172 MERGED** `328f4dbb`; ci_final_gate.py + scripts/release/release_auth_gate.py PRESENT |
| TASK-WORKFLOW-DOC (14) | PR #155 OPEN | **#155 MERGED** `1f42c543`; docs/engineering/workflow-test-build-release.md PRESENT |
| TASK-P1-TRAIN-SERVE-PARITY (20) | IN_PROGRESS | trained_mode SERVING gate PRESENT (live_sequence.py:74-98, inference.py:246); pins in test_live_sequence_characterization.py + test_runtime_failure_injection.py. Named file test_train_serve_parity_p0.py NOT at HEAD (see §5 rename-rot) |
| TASK-P2-ARTIFACT-TRUST-ANCHOR (21) | IN_PROGRESS | scaler_sha256 fail-closed (load_integrity.py:185-210) + CHAMPION cross-check TRUST_ANCHOR (model_bundle_store.py:282-390) + verify_champion_row_pairing (registry.py); later hardened by BUG-271/272 |
| TASK-P3-BENCH-SPLIT-INTEGRITY (22) | IN_PROGRESS | OOS_SPLIT_INTEGRITY refusals (validation.py:345-496), evaluate_test_block_once single-shot, three_model `gate_artifact_ok` artifact-evidence CHALLENGER (three_model.py:286-378) |
| TASK-072-RESIDUAL (23) | COMMITTED 620c3613 | hash **NOT_FOUND** (rebase loss); ticket-set state PRESENT (`_context_bound_tickets`/`_unbound_ticket_contexts`, pending_orders.py:19-20) but test_bug072_073_residuals.py ABSENT → content landed, test net lost |
| TASK-073-RESIDUAL (24) | IN_PROGRESS | ledger-OPENED entry-context recovery PRESENT (reconciliation.py:67-124); no named regression file at HEAD → treat DONE-by-absorption, close after test check |
| TASK-ARCH-DECOMP / -SCOPE (73/74) | IN_PROGRESS | decomposition demonstrably progressed (web/operator_routes.py, live/bar_handler.py, web/replay_routes.py all PRESENT) — row stale since 08-31 |
| TASK-INSTALLER (86) | IN_PROGRESS | installer/install.ps1 + tests/installer PRESENT (incl. test_rc_regressions.py from EVOLUTION wave) |
| TASK-RUNTIME-TRUTH (90) | IN_PROGRESS | src/nexus_scalp/release/state_truth.py, diagnostics_state_routes.py PRESENT; STP0-CODER/STP0-QA rows already DONE |
| TASK-QA-DEEP-ASSURANCE (92 AND 98 — duplicate rows) | IN_PROGRESS ×2 | scripts/qa/deep_assurance.py + QA_BLIND_SPOT_MATRIX.md + .github/workflows/qa-deep-assurance.yml all PRESENT → content landed; row exists twice (known dup class) |
| TASK-CONTROL-CENTER (93) | IN_PROGRESS | Web/cc_components.js, cc_state.js, tests/js/cc_state.test.js, operator_routes.py PRESENT |
| TASK-AGENT12-EXEC-FORENSIC (142) | IN_PROGRESS | superseded by its own COMPLETE update row (157); test_agent12_execution_forensic.py PRESENT → stale duplicate |
| TASK-AGENT14/15/17/3-forensic rows (144/143/151/148) | IN_PROGRESS | test_agent14_dataset_integrity.py, test_agent15_bug244_*.py, test_agent17_oos_hard_gate.py, test_agent3_champion_registry_sync.py all PRESENT → content landed 09-05..09-07 via squashes; statuses never flipped |
| TASK-AUDREV-G1 (191) | IN_PROGRESS | `a178c7c5` ANCESTOR + test_broker_offset_config.py PRESENT |
| TASK-AUDREV-G3-HOLD-CLOCK (193) | IN_PROGRESS | `15d39552`+`f462fe5c` ANCESTORS + test_hold_time_fallback_g3.py PRESENT |
| TASK-AUDREV-E1-ZERO-FRICTION (194) | IN_PROGRESS | `80f17ef2` ANCESTOR + test_zero_friction_guard_e1.py PRESENT |
| TASK-AUDREV-C4-SYMBOL-UNLOCK (196) | IN_PROGRESS | `8c2360ba` ANCESTOR + test_c4_symbol_magic_unlock.py PRESENT |
| TASK-AUDREV-A4-DL-SPLIT (197) | IN_PROGRESS | dead_letter_store.py PRESENT + test_dead_letter_store_split.py PRESENT |
| TASK-AUDREV-H5-CANDLE-INTEL (198) | IN_PROGRESS | `e637b9f0` ANCESTOR; gating live at live_engine.py:946-963 honoring AppConfig.candle_intel (base.yaml `enabled:false`), test_h5_candle_intel_gate.py PRESENT |
| TASK-AUDREV-HOUSEKEEPING (199) | IN_PROGRESS | `a77f8315` ANCESTOR (scratch untrack + docs) |
| TASK-CI-TRIGGER-ALIGNMENT (280) | PR open | **#134 MERGED** `72fdff1e` |
| TASK-REL-SEC-S1-SIGNED-FETCH (282) | PR open | **#163 MERGED** `4700e482` (SignedManifestResolver) |
| TASK-C3-ENVFIX-TEST-HYGIENE (283) | OPEN PR | **#158 MERGED** `9ded0d8b` |
| TASK-WIPBUNDLE-D-WSL2-DOCS (284) | READY_FOR_REVIEW | **#156 MERGED** `008ea325`; docs/linux_wsl2_mt5_platform.md + test_wsl2_platform_registry.py PRESENT |
| TASK-WIPBUNDLE-A-CHAMPION-GUARD (285) | READY_FOR_REVIEW | **#157 MERGED** `bc1bad33`; test_champion_writer_governance.py PRESENT |
| TASK-BUG263-ROLLBACK-INTEGRITY (287) | PR open | **#159 MERGED** `0e72f47a` |
| TASK-ALT-UI-PRO (288) | branch pushed, PR pending | **#166 MERGED** `567ad1ad` — but branch holds 23 FURTHER unmerged W2 commits (open work, re-scope the row) |
| TASK-WIPBUNDLE-B-SERVING-GATE (290) | READY_FOR_REVIEW | **#154 MERGED** `60b785d3` |
| TASK-C4-UV-PIN-RESTORE (293) | PR pending | **#168 MERGED** `df275c16` |
| TASK-BUG267-ALTUI-AUTH (294) | COMMITTED → PR | **#174 MERGED** `5ebf97ec` |

### Genuinely IN-PROGRESS (content NOT fully at HEAD — VERIFIED)

| Row | Residual (verified) |
|---|---|
| TASK-REPLAY-ON-CHART (89/100) | replay_session.py + replay_routes.py PRESENT, but the row's own remaining item is real: session-create route → local dataset loader NOT found (`git grep HistoricalEventSource loader` in replay_routes.py empty) |
| TASK-TDF (108) | orchestrator row; R4 confidence-semantics + guardian-staleness items still unclaimed — **this is exactly what wave lane 02 is now doing** |
| TASK-PROMSTAT / MLFIX-PINC-EXEC (126) | named pins PRESENT (test_bug234_htf_window_parity.py); engine-wiring residue per T3 row "open orchestrator item" |
| TASK-EXITREPLAY-HARNESS (211) | harness PRESENT (scripts/forensics/exit_policy_counterfactual.py + test_exit_replay_harness.py) — mechanics DONE; real-PF run still data-blocked (next row) |
| TASK-EXIT-LEDGER-EXPORT (215/221) | **BLOCKED-ON-OPERATOR, confirmed twice** — prod audit.db export never delivered; validator test PRESENT (test_audit_ledger_export_w3.py) |
| TASK-INSTALLER-EVOLUTION (112) | wave-1 content landed; wave-2 (RC-3/RC-4, G1-G5 gaps) genuinely unstarted |
| TASK-DEVOPS-HARDENING (225) | umbrella; dependabot child row DONE (#139 `1eff96ee` ancestor); remaining gap matrix waves unscoped — overlaps row 279 |
| TASK-AGENT9-70D-RESEARCH (140) / TASK-ANTIGOD (111) / TASK-RR-DIAGNOSTICS (109) | mission rows; ANTI_GOD_LEDGER.md ABSENT at HEAD (planned deliverable never landed) → PHASE1-dispatched claims went stale |
| agent4-mlrepro-next branch work | multi-seed dispersion driver (108efb97) NOT on main — real open deliverable, no taskboard row flipped for it |

### BLOCKED (confirmed still blocked)

- **TASK-EXIT-LEDGER-EXPORT** — operator prod audit.db export (see lane 02: the export may now exist locally as `artifacts/audit.db` copies per wave probes — coordinator should re-ask).
- **TASK-04-70D-MODEL-VALIDATION (48)** — BUG-106 phase-2 landed (`891f78d0` money-path row) so the stated blocker is dead; the row itself is stale → reclassify STALE-DONE-partial.
- **TASK-BUG257 drift-writer identification** — BUG-272 sentinel landed but is **alert-only by design** (`maintenance.py`: drift → CRITICAL log + Telegram, "Trading continues; NEXT boot will refuse"); the unidentified out-of-process writer is STILL OPEN.

### DUPLICATE rows (same ID, competing status)

- TASK-QA-DEEP-ASSURANCE ×2 (92, 98) — both IN_PROGRESS, content already landed.
- TASK-AGENT12-EXEC-FORENSIC (142 IN_PROGRESS vs 157 COMPLETE) — later row wins.
- TASK-REPLAY-ON-CHART (89 vs 100 status update).
- TASK-RUNTIME-RESILIENCE-FI wave 2 / 2b (25/26) — both landed via #131/#132.
- TASK-LINUX-MT5-PLATFORM (241) vs TASK-LINUX-MT5-WSL2-REVERIFY (270) — WSL2 reverify supersedes; #156 landed the doc.
- TASK-A10-CICD-GATE-INTEGRITY (18) vs TASK-A10-CI-GATE-INTEGRITY (229) vs TASK-A10-FINAL-C4 (13) — three A10 rows; all landed (#172/#188 etc.).
- TASK-BUG249 ID **collides three ways in bugs.md** (8335 research fail-open trio, 8347 BUG-249-A6 clamp, 8368 dead spread guard) — cite with qualifier.
- TASK-SEC-AUDIT-SUPPLYCHAIN (27) vs -2 (272) — wave-2 supersedes; F2 pidfile fix VERIFIED at HEAD (orchestrator.py:544-559 `_default_pidfile` → `get_data_root()`).
- BUG-266/267 ID collision (paper-replay = #173 `1c6588c5`; alt-ui-auth renumbered 267 = #174 `5ebf97ec`) — both MERGED; the a9d29e21 closeout row documents it.

### STALE (abandoned/ghost — owner absent, content absent at HEAD)

- **TASK-API-CONTRACTS (95)** — `src/nexus_scalp/api/**` ABSENT at HEAD (whole tree) — confirms the 20260902E GHOST→NEEDS-DECISION ruling; never resurrected.
- TASK-API-PLATFORM (34, TODO) — `src/nexus_scalp/api` absent BUT the successor surface exists (web/api_v1_wiring? api_v1 marketplace present at `src/nexus_scalp/web/api_v1/`) → the row's file plan is obsolete; needs owner rewrite.
- TASK-SWARM-GOV (34-ish, TODO) and 08-19-era fragments in prose lines (29-47) — historical, archive candidates.
- TASK-073 test file (test_bug072_073_residuals.py) ABSENT — regression net lost in a squash/rebase wave; content landed. Same class: the three `*_p0.py` test files of P1/P2/P3 rows never appeared on any ref (`git log --all -- <file>` empty) → those exact filenames were renamed at reland (roles covered by test_live_sequence_characterization / test_runtime_failure_injection / test_experience_provenance_contract). **Lesson for all lanes: verify by HEAD content, never by row-quoted hash.**

### REGRESSION ledger (fixes that broke or erased other fixes — VERIFIED from history)

1. **P0-wave strip** — `f3f53f69` (containment revert) severed ScalerBundle.corrupt + the P0-2/P0-4/P1 reland lineage; re-landed via `bd944fea` (#131) + TASK-P0-WAVE-RELAND-20260911. Root cause: squash-merge + shared index.
2. **A16 hotpath wipe** — rebase carrier `b5b5766e` silently reverted bar_handler/scalp_features fixes + deleted their test; recovered by `2f283966`. (Row 246 documents it.)
3. **Trust-anchor boot death (BUG-271)** — the P0-2 CHAMPION cross-check itself caused a **regression class**: any legitimate in-place retrain orphaned the governed fingerprint → next cold boot REFUSED, trading permanently dead. Fixed `5a9e36e3` (#190, supersession seam at `_register_active_model`). **Lane 05 (zero-state bootstrap) and lane 07 must build on the post-#190 semantics.**
4. **First-warning suppression (BUG-273)** — the BUG-268 fix's HOLD_AGE_FALLBACK throttle (`_last=0.0` vs monotonic) swallowed the first warning on hosts with uptime <300s (CI runners); fixed inside #192 `9431edd2`.
5. **candle-intel DB staleness** — H5 gating turned the subsystem off → `candle_intel.db` trade_decisions frozen 2026-09-07 (master's COORDINATION probe). Not a code regression; a data-staliness consequence lanes 02/03/09 must know about.
6. **EXP-PROV-1 historical mislabels** — 975 prod audit rows carry (scalp_v3, dim=50) impossible triples; NOT rewritten (test-pinned). Every consumer lane (02, 07, 08) must treat pre-`bd944fea` experience rows as **base-50 evidence with wrong schema id**; cross-schema feature_hash joins invalid.

### HIGH-RISK rows (collision surface for this wave)

- **TASK-TDF / lane 02**: CONFIDENCE_FAIL 29.7% + HIGH_SPREAD_CHOP 24.2% funnel findings already have landed gate work (C3 #118, BUG-249 spread guards) — re-diagnose against HEAD, not against 09-02 audit text.
- **agent2-eval-governance unmerged test net (754 LOC)** vs lane 14 (QA matrix) and lane 07 (parity) — someone will re-write these tests twice otherwise.
- **ui/lane3-5 + feat/alt-ui-pro-ux 23 unmerged commits** vs lane 09 (indicators UI) and lane 13 — frontend/ is mid-flight; treat merged `f5fe5738` React-parity-wave-1 tip as the read baseline and flag any frontend claim.
- **EXIT-SEPARATION constants (giveback_arm_r default 0.50, ai_flip_exit_enabled False, trail 1.15)** — landed (test_exit_separation_w3.py PRESENT; `f6cc0fb6` hash lost to squash). Lane tuning must not re-propose defaults as "missing config".
- **hold_duration.py + market_entry_gate.py + order_write.py are UNWIRED by design** (rows say so; VERIFIED: zero consumers at HEAD outside their own modules) — lanes 02/11 that hunt "dead code" will re-find these three; they are deliberate hand-offs, not defects.

## 4. Recently-landed passivity fixes (all VERIFIED at HEAD)

| Fix | Landing | HEAD evidence |
|---|---|---|
| BUG-249 dead spread guard | content via C3 chain (`fa126e46` #118 ancestor; `dd9cd7f8` branch itself NOT ancestor — squashed) | policy.py:61/729-735/1348 `SPREAD_ATR_RATIO_EXCEEDED`; :1333-1340 `SPREAD_TP_RATIO_EXCEEDED` + `SPREAD_SESSION_PCT_EXCEEDED` |
| BUG-251 reversal confidence | same Agent-5 chain | policy.py:2245-2269 flip gate now uses `_directional_confidence` semantics |
| BUG-252 peak_equity penalty | same | accounting/aggregation.py:295-340 peak-drawdown live |
| H5 candle-intel gating | `e637b9f0` ANCESTOR | live_engine.py:946-963 honors AppConfig.candle_intel; base.yaml:70-75 enabled=false; test_h5_candle_intel_gate.py |
| EXP-PROV-1 provenance | `bd944fea` #131 (`483723b1` not ancestor) | experience/intelligence.py:562-569 schema_for_dimension + SNAPSHOT_SCHEMA_UNRESOLVED; test_experience_provenance_contract.py (401L) |
| BUG-266 paper replay | `1c6588c5` #173 | paper_data.py:66 build_paper_adapter; runtime_mode.py:108-111, live_engine.py:4250-4253, engine_boot.py:452 |
| BUG-267 auth bootstrap | `5ebf97ec` #174 | web/auth_boot.py PRESENT |
| BUG-268/273 grace clock + throttle | `46ccf2f4` #178 / `9431edd2` #192 | scoring.py:424-540 HOLD_AGE_FALLBACK |
| BUG-271 fingerprint supersession | `5a9e36e3` #190 | `_register_active_model` choke point (commit body VERIFIED; trust-anchor byte-unchanged) |
| BUG-272 champion-drift sentinel | `9431edd2` #192 (HEAD) | champion_sentinel.py + maintenance.py:149-205 — **alert-only between boots; writer hunt still open** |

## 5. Top duplication risks for other lanes (ranked)

1. **Re-implementing the P0 serving/trust/bench-split trio (lanes 05/07/14)** — trained_mode SERVING gate, scaler_sha256 + CHAMPION cross-check, OOS_SPLIT_INTEGRITY + single-shot test-block + artifact-evidence CHALLENGER are ALL at HEAD. The correct open edges are: REPLAY-DEFECT-B (post-gap window starts one bar later live than dataset — pinned RED in test_agent5_replay_gap_parity), the scaler-route convention ruling (clip [-5,5] live vs f64 no-clip replay, 0.88% argmax flips bounded to |z|>5 rows), and the unmerged `agent2-eval-governance` test net.
2. **Spread/regime gate rediscovery (lane 02/03)** — C3 session-percentile + BUG-249 ATR-ratio + TP-ratio gates are wired and fail-closed; HIGH_SPREAD_CHOP veto fires BEFORE inference (policy.py:2190) — analyze the guardian's label quality, don't re-add spread gates.
3. **Alt-UI/auth work (lanes 09/11/13)** — ALT-UI, standalone host serve_alt_ui.py, BUG-267 bootstrap, BUG-270 header-splitting (#184), React parity wave 1 (#186) ALL merged; only feat/alt-ui-pro-ux W2 + ui/lane3-5 are open. Any "auth 401 dead-end" or "header injection" finding is already fixed — check HEAD first.
4. **EXIT-ledger PF numbers (lane 02/03)** — harness + validator exist; the blocker is the OPERATOR file, not code. Running synthetic grids again wastes a lane.
5. **Dead-code sweeps (lane 11/10)** — hold_duration/market_entry_gate/order_write are deliberate unwired hand-offs (documented); candle_intel is deliberately off (decision doc 2026-09-07). File findings against these = duplicates.
6. **Bug-ID collisions** — BUG-249 (×3 semantics), BUG-244/248 renumber, BUG-262/263 renumber, BUG-266/267 renumber, BUG-233 collision class precedent: cite IDs with lane+date qualifiers in every report file.

## 6. Housekeeping recommendations (read-only lane — NOT executed)

- ~24 `[gone]`-upstream local branches + ~65 stale unmerged locals: coordinator-deletion candidates after patch-id audit (DEC-0008: no destructive ref ops by lanes).
- Taskboard needs one append-only reconciliation row closing the 19 DONE-but-open statuses above (matches 20260902D/E precedent).
