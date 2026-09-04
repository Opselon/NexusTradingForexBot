# MODEL CONTINUATION REPORT — 2026-09-04T15:50+03:30

> **HEAD:** `f1ef7cb9` (origin/main in sync)
> **Dataset:** `ds_70d_clean_m1_20260904` — 99,946 rows — `3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe` — `235b8fccc96b7e0e` — `scalp_v3` — `CLEAN_HISTORICAL` — PASS
> **Retrain PIDs:** `26528` (wrapper, idle) + `25032` (worker, 30k CPU, 34 threads, 349 MB) — both `HasExited=False` — still running at collection — DO NOT KILL

## 1. What the postmortem proved

* Reported "NOT ACTIVE" is **contradicted by live evidence**: `Get-CimInstance` + `Get-Process` at 15:33-15:50 IRDT shows `25032` `Running`, `UserMode 29.5 ks` (~8.2 h), `CPU 30833 s`, `WS 349 MB`, 34 threads, elapsed `09:13`, thread 25100 continuously `Running`, CPU still growing (+276 s in minutes). Parent chain `bash 18832 → venv 26528 → uv 25032` since `2026-09-04 06:36:47.569`.
* Verdict **`PARTIALLY_TRAINED` (secondary `OUTPUT_OVERWRITTEN` partial)**: champion `model.pt` mtime `2026-09-03 22:29:43` (1,334,268 B, `c8c0b5b0…`) **predates launch** — untouched. No `*.tmp` residue — atomic `tmp→replace` not yet reached. `model.scaler.npz` + `model.meta.json` + `benchmark_70d_liquidity.json` + `retrain_*.json` were **clobbered at 08:53:25** with `elapsed_sec 62.33` `EVIDENCE_WRITTEN` — a short validation overwrote sidecars while long job holds champion path. `wf_candidate` (08:11:42, `scalp_v4`, `ec84…`) is not the champion. Full report: `MODEL_RETRAIN_POSTMORTEM.md`.
* **Producer bug §d**: `scripts/dev/train_70d_liquidity_production.py → three_model.train_variant → WalkForwardTrainer(artifact_save_path=variant_artifact_path("70d_liquidity"))` writes **directly to champion** `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` via atomic `model.pt.tmp→replace` — no isolated candidate staging. When `25032` lands it will overwrite champion in place.
* **No resumable checkpoint** for `ds_70d_clean_m1_20260904` seed 42 folds 34×10 `scalp_v3` 70D `CANONICAL_CLASS_COUNT 3`: inventory of 30+ `*.pt` via `torch.load(weights_only=True)` — only `wf_candidate [3,32]` is 70D 3-class but `scalp_v4` `production_eligible false` `UNKNOWN` lineage; all `70D_liquidity` / `t70d_full_retrain` are `[4,32]` `c8c0…` P0; `t70d_seq_v1/v2` TCN `[3,64]` is different arch.
* **Stash state drift**: `git stash list` now **0** (commit `f1ef7cb9` doc: "final disposition — drop 6 audited stashes") — prior baseline `345932e3` had 6 preserved. Dropping was pre-existing before this recovery window; this report does not re-drop. Evidence archived at `forensic_recovery_20260904/stash-{0..5}.patch`.

## 2. Producer fix required before any new isolated retrain

Root cause (producer audit appendix, 4 delegated forensics):

* **SSOT is correct**: `architectures.CANONICAL_CLASS_COUNT=3`, `model_class_contract.TRAINED_CLASS_COUNT=3`, `WalkForwardTrainer.CANONICAL_NUM_CLASSES=3`, `_create_model` builds `ScalpNet(num_classes=3)`, `_save_metadata` writes `num_classes=3` `model_head_classes=3`. `TCNAttentionV1` is always 3-logit. `three_model.train_variant` correctly wires `scalp_v3` + `label_origin CLEAN_HISTORICAL`.
* **Divergence is at serving defaults**: `ScalpNet(num_classes=4)` default, `ModelFactory LEGACY_SCALPNET_V1` forces `4 even when caller asks 3`, `ChampionModel num_classes=4`, `live_engine ScalpNet(4)` — bundle becomes `4-class tensor + 3-class metadata` when tensor comes from legacy-default path and metadata is freshly written by 3-class SSOT, or when files are replaced independently (per-file atomics, no bundle atomic). `model.meta.json dataset_id null` because `WalkForwardTrainer._save_metadata` never stamps `dataset_id` — `three_model` bypasses `DatasetFactory/ArtifactStore`.
* **No emission gate**: `_save_checkpoint` does `torch.save→tmp→replace` **without** reading back `classifier.weight.shape[0]` vs `CANONICAL_NUM_CLASSES`; `_save_metadata` never cross-checks `actual_head == payload["num_classes"]`; `three_model` has no post-write `inspect_artifact` probe before publish.

**Minimal fix (proposed, not yet implemented — gated by live PID):**

1. **Isolated candidate path**: change `train_70d_liquidity_production.py` / `three_model.train_variant` to accept `output_dir` (e.g., `artifacts/model_generation/models/t70d_production_candidate_<run_id>/model.pt`) and never write to `artifacts/models/scalp/XAUUSD/...` until gate PASS. `WalkForwardTrainer` default is already `wf_candidate` isolated — `three_model` overrides it; remove the override for production runs or make it explicit `isolated=True`.
2. **Emission gate** (single synchronous gate after `model.eval()` before any `tmp→replace`):
   ```python
   actual_head = int(model.classifier.out_features)
   canonical = int(CANONICAL_CLASS_COUNT)  # 3
   if actual_head != canonical or int(self.CANONICAL_NUM_CLASSES) != canonical:
       raise RuntimeError(f"EMISSION_GATE_ABORT actual={actual_head} canonical={canonical}")
   # pre-replace: validate tmp shapes; post-replace: inspect_artifact(tmp, num_classes=canonical) must be integrity_ok else unlink tmps and abort
   ```
3. **Provenance stamp**: `meta["dataset_id"] = "ds_70d_clean_m1_20260904"` + `dataset_sha256` + `feature_schema_hash` + `git_commit` + `training_command` + `seed/folds/epochs` — when `ArtifactStore` handle absent, stamp deterministically or record `dataset_provenance: "walk_forward_bars_no_handle"` closed-world; never silent `null`.
4. **Bundle atomicity**: stage `model.pt.tmp + scaler.tmp + meta.tmp` into `.staging_<uuid>/` and require all three `inspect_artifact` PASS before any `replace`; on failure unlink staging and surface `FAILED` — never partially publish.

## 3. What was NOT done this window (why)

* **No new 34×10 launch** — live worker `25032` is still on CPU; duplicate would contend on champion path (both write `model.pt.tmp→replace`), waste 8 h, and violate §3 "verify before retrain". Pipe `bash | tail -40` buffers stdout — no disk log to tail; re-launching with same champion path would be destructive.
* **No manual `model.pt` repair / metadata hand-edit / `wf_candidate` rename / promotion** — per §1 rules, preserved `c8c0…` as evidence.
* **No `pinc-stash-rescue@0c90725b` touch**, no `git stash drop`, no champion overwrite.
* **No behavioral / OOS / calibration / offline↔live probes** — withheld on P0 4-class tensor (would be meaningless through 3-class decoder).

## 4. Dataset contract — frozen

All `MODEL_READINESS_REPORT.md` gate tables remain valid. `CONTRACT_AUDIT_REPORT.md` shows no competing `50/70/32/4` literal drives training geometry outside SSOT. No alternate / smoke / legacy dataset created for production candidate.

## 5. Next gated sequence (after snapshot + either let-live-finish or isolated rerun)

```
SNAPSHOT champion:  cp artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt{,.pre_25032} + meta + scaler — hash c8c0… preserved

FIX PRODUCER → commit (isolated output + emission gate + provenance stamp) → ruff + py_compile on training path

WAIT DECISION:
  A) If 25032 completes before fix lands — verify its emission (post-write gate) — if [4,32] or dataset_id null → REJECT, do not install, trigger isolated rerun
  B) Otherwise launch ONE clean isolated retrain:
     .venv/Scripts/python.exe scripts/dev/train_70d_liquidity_production.py --dataset-id ds_70d_clean_m1_20260904 --folds 34 --epochs 10 --batch 256 --seed 42 --output-dir artifacts/model_generation/models/cand_isolated_70d_34x10_<timestamp> 2>&1 | tee artifacts/model_generation/three_model/train_70d_isolated_<ts>.log

HARD CONTRACT CHECK (before any behavioral):
  torch.load(weights_only=True) → input_projection [128,70] && classifier [3,32] || P0 BLOCK
  meta: num_features 70, num_classes 3, model_head_classes 3, seq_len 32, scalp_v3, hash 235b8fccc96b7e0e, dataset_id ds_70d_clean_m1_20260904, dataset_sha256 3ae68…, class_count 3, git_commit set, seed 42, folds 34, epochs 10
  cross-checks: meta head == tensor head == 3, sha == recorded sha, scaler hash == recorded hash

GENUINE TRAINING PROOF: init vs final delta, parameter_movement_frac (fresh), loss trajectories per fold/epoch, fold metrics, scaler provenance, git_commit+command

12 PROBES → OOS 34-fold (accuracy/balanced_accuracy/macro F1/per-class PR/confusion/fold dispersion/entropy) → CALIBRATION ECE/reliability → OFFLINE↔LIVE (B,32,70) replay (same scaler, same logits±1e-5, same decision) → GOVERNANCE candidate→challenger proof (no rename/copy) → SECURITY weights_only=True + path allow-list

PROMOTION: none — MODEL READY FOR NEXT GOVERNED GATE only after all above PASS
```

Snapshot + producer fix is the immediate next commit; full validation chain is deferred until a coherent `[3,32]` bundle exists.

## 6. Risks if gate is skipped

* `4-class` ScalpNet through `3-class` decoder → `WAIT` logit masked or silent 4th logit ignored → policy mis-decision → off-book fills never guarded — capital risk.
* `dataset_id null` → irreproducible candidate, lineage gate bypass.
* Direct champion write without staging → torn bundle observable to `champion_or_none()` file-watch (`~2 Hz`) → live loads mixed-version bundle.

## 7. Evidence pointers

* `MODEL_RETRAIN_POSTMORTEM.md` (290 lines) — PIDs, mtimes, timeline 06:35:58→15:33, no `*.tmp`, pipe-to-tail log gap, dataset hash.
* `MODEL_ARTIFACT_FORENSICS.md` + `artifacts/forensics/model_artifact_forensics_20260904.json` — 33 pts, `c8c0…` vs `ec84…` vs `a04b…`.
* `CONTRACT_AUDIT_REPORT.md` — SSOT tables, no competing literal.
* `MODEL_READINESS_REPORT.md` — 14-section gate rollup, WITHHELD probes.
* `SECURITY_AUDIT_SEC_CAPITAL_DATA_BROKER.md` — 12 FAIL `torch.load`, 0/8 guarded path, head-gate missing.
* Subagent summaries `deleg_e1e41a41` task 0-3 (read-only, real `powershell`/`torch.load` output).

---

# 2026-09-04 ~19:25 IRDT — P0 FIX EXECUTED + PILOT PASSED (update)
> **HEAD:** d19195ec → fc36b2fb pushed (origin/main) · beforePush FULL PASS before push
> Full pilot evidence: **PILOT_VALIDATION_RESULT.md** (created)

## Execution summary
* Old 34×10 worker (25032 chain) TERMINATED under explicit user authorization — identity verified exactly via Win32_Process CommandLine; all 4 PIDs gone; record in `artifacts/forensics/evidence_snapshots/old_worker_termination_record.json`. Output ABORTED/INVALID — never resumed or promoted.
* Producer chain fixed in 10 commits: champion guard (realpath/symlink-safe denial of any write to `artifacts/models/scalp/**`), isolated candidate default outputs (trainer + `train_variant(output_dir=…)`), hard emission gate on serialized tensors, typed dataset-provenance binding (declare/bind from ArtifactStore manifest), atomic bundle publication (stage→gate→manifest→verify→commit), safe `weights_only` loader + hot-swap governance (path allow-list / manifest-hash binding / production_eligible gate).
* 14 new regression/security tests: exact P0 failure modes (4-class tensor+3-class meta, inverted, null provenance, wrong scaler, incomplete bundle, stale sidecar hash binding, champion-path write, canonical head=3, traversal/external) + 5 hot-swap attack tests. beforePush: ruff/mypy/CRITICAL suite ALL PASS.
* **PILOT PASSED**: 4×3 on contiguous 24k tail of `ds_70d_clean_m1_20260904` (subset hash bound), isolated bundle `pilot_70d_3class_20260904_130906`, model_sha `4ce21183d749…`, head=3/input=70/seq=32 verified from serialized bytes, deterministic (byte-identical rerun), behavioral non-degenerate (maxp 0.369 vs ~0.28 degenerate ref), offline/live parity Δ=0.0, champion c8c0b5b0 unchanged start→end.
* **Decision: FULL 34×10 JUSTIFIED** — still isolated output, no promotion, no architecture change.
