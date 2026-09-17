# Official model publication — operator runbook (NSE)

Owner-only pipeline publishing a TRAINED Nexus model bundle as a signed
`nexus_model_bundle_v1` revision-2 release. **Model releases are separate
from application releases**: tags are `model-<version>` (never `v*`), so
`releases/latest` (application) is never affected. Model releases are
created with `--latest=false`.

## 1. Where do I add files (candidate draft release)?

Create a **draft GitHub release** on `Opselon/NexusTradingForexBot` with the
exact tag `model-candidate-<version>` (e.g. `model-candidate-1.2.3`):

> https://github.com/Opselon/NexusTradingForexBot/releases/new
>  - "Choose a tag": type `model-candidate-1.2.3` + "Create new tag"
>  - Target branch: `main`
>  - Title: anything (e.g. `Official model candidate 1.2.3`)
>  - ☑ **Set as a pre-release** and ☑ **Set as a draft**
>  - **Attach binaries** — upload ALL SIX files, exact names:

| File | Max size | Content |
|---|---|---|
| `model.pt` | 512 MiB | ScalpNet state_dict from the REAL training run |
| `model.scaler.npz` | 1 MiB | matching scaler sidecar |
| `model.meta.json` | 256 KiB | trainer metadata (schema id/hash/dim/classes) |
| `trainer-manifest.json` | 256 KiB | the trainer's own bundle manifest (renamed only; bytes untouched) |
| `provenance.json` | 256 KiB | truthful `producer` (python/pytorch/git_commit) + `training` (command/seed/dataset id+sha256) |
| `compatibility.json` | 256 KiB | `consumer` (bounded python/pytorch/platforms), `verification_runtime` {python,pytorch}, optional `metadata_bindings` |

- `model.pt`, `model.scaler.npz`, `model.meta.json` come from the real
  serving bundle (e.g. `artifacts/models/scalp/XAUUSD/<run>/`).
- **Never publish starter/dev-random weights.** The publisher rejects
  synthetic/empty evidence; `provenance.json` must describe the actual
  training run. Producer versions are evidence only — end-user support is
  governed by `consumer`.
- After the last upload, leave the draft UNPUBLISHED (draft state is
  verified by the workflow).

## 2. Run the publication workflow

1. GitHub → **Actions** → **Official model publication**
   (`.github/workflows/official-model.yml`).
2. **Run workflow** on branch `main`.
3. Inputs: `version` = `1.2.3` (no `v`), `candidate` = `model-candidate-1.2.3`
   (must match). The workflow verifies version/candidate/ref/repo identity
   before anything is fetched.
4. The workflow then, in order:
   - fetches ONLY the six allowlisted assets from the authorized draft
     (fixed asset IDs, declared size caps, no interpolation into shell);
   - validates actual model runtime/schema/scaler/health via the real
     consumer verification chain (signed manifest + integrity probe);
   - signs the manifest with the existing repo secret
     `NSE_UPDATE_SIGNING_KEY` (name only; the key is never printed);
   - creates the **`model-<version>` release** only if it does not already
     exist (immutable, `--latest=false`, not a `v*` tag), uploads assets,
     re-verifies remote bytes against the signed digests, and fails closed
     on any mismatch;
   - LAST, uploads `manifest.json` to the `official-model-stable` release —
     the single channel URL the clients default to
     (`.../releases/download/official-model-stable/manifest.json`).

## 3. End-user install

Users run `nexus model-official` (or First Setup → official model). The
engine downloads the channel manifest, verifies the Ed25519 signature
(embedded trust root) and every file digest, then installs atomically.
No clickable release asset is a supported install path.

## 4. Runtime / CPU torch note

Verification runs on Python 3.11 with a bounded stable CPU-only PyTorch
(2.6–2.19, pinned explicitly per compatibility.json; the workflow installs
`torch==<pinned>+cpu` from https://download.pytorch.org/whl/cpu — no CUDA
download). The pinned version is validated by `runtime_spec` before use.

## 5. Guarantees

- Publication is idempotent-safe: an existing `model-<version>` release
  aborts the run BEFORE any upload; the stable channel is written only
  after the versioned release is fully verified.
- Candidate assets are data-only: the pipeline never executes code from
  candidate files, never loads pickles unsafely, never mutates trust.
- No artifact is published by this documentation alone — a real release
  happens only when the operator runs the workflow with real evidence.
