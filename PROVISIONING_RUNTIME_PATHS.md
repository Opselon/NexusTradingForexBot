# PROVISIONING_RUNTIME_PATHS.md

Effective runtime paths for the `/provisioning` control plane, measured on this
host. **No mixed paths** — every entry below is the resolved truth for the
engine serving `:8089`.

## The two directories are NOT equivalent

```
C:\Users\Capsizer\source\repos\NexusTradingForexBot      ← git repo root + shared .venv
C:\Users\Capsizer\source\repos\nse-review-main           ← a git WORKTREE of the same repo
```

Proof: `nse-review-main/.git` is a file pointing at
`NexusTradingForexBot/.git/worktrees/nse-review-main`; both share the same
remote (`Opselon/NexusTradingForexBot`). They are the SAME repository, and
`nse-review-main` is a second working tree, not a second project.

## Effective paths (measured)

```
repository root:   C:\Users\Capsizer\source\repos\nse-review-main        (worktree, branch review/main)
Python:            C:\Users\Capsizer\source\repos\NexusTradingForexBot\.venv\Scripts\python.exe
venv:              C:\Users\Capsizer\source\repos\NexusTradingForexBot\.venv   (3.11, torch 2.13.0+cu126)
API cwd:           C:\Users\Capsizer\source\repos\nse-review-main
model root:        C:\Users\Capsizer\source\repos\nse-review-main\artifacts\models\scalp\XAUUSD\70d_liquidity
dataset root:      C:\Users\Capsizer\source\repos\nse-review-main\data\raw            (7 files, EXISTS)
dataset root:      C:\Users\Capsizer\source\repos\nse-review-main\data\imports        (MISSING — shown as exists:false)
config root:       C:\Users\Capsizer\source\repos\nse-review-main\configs
artifact root:     C:\Users\Capsizer\source\repos\nse-review-main\artifacts
training-env:      C:\Users\Capsizer\source\repos\nse-review-main\training-env        (managed venv, does NOT exist)
```

## Why the split is CORRECT (not a defect)

1. **Launcher bootstrap.** `NexusTradingForexBot.py` inserts
   `<script-dir>/src` at the front of `sys.path` — the script lives in the
   worktree, so the WORKTREE code wins. `nexus_scalp.__file__` confirms it:
   `C:\...\nse-review-main\src\nexus_scalp\__init__.py`.
2. **Editable install pin.** The `.venv`'s `__editable__` `.pth` pins
   `nexus_scalp` to the MAIN checkout's `src/`, but the launcher's explicit
   `sys.path.insert` shadows it — exactly the documented behavior.
3. **Dependencies are NOT in the worktree.** The venv (and therefore torch,
   structlog, polars, uvicorn, fastapi) belongs to the main repo; the worktree
   only owns source. This is by construction, and it is why the
   `NexusTradingForexBot\.venv` python is the only interpreter that can run the
   engine. `NexusTradingForexBot.py` resolves its interpreter through PATH,
   which resolves to that venv.

So the "inconsistency" in the task description is a misread of a deliberate
worktree topology: **one repo, two trees, one venv**. Artifacts, datasets and
config all resolve under the WORKTREE root (correct — the engine's cwd), and
only the interpreter/deps come from the main repo.

## Verified env facts (this host)

```
Python:            3.11.16 (NexusTradingForexBot\.venv)
Torch:             2.13.0+cu126
Torch CUDA:        12.6 — torch.cuda.is_available() = True
GPU:               NVIDIA GeForce RTX 3060 Ti
driver:            616.92 (compute cap 8.6; win32 floor 528.33)
resolved backend:  cuda (GPU present) → CORRECT
training ready:    TRUE (all 10 checks green, incl. CUDA allocation smoke)
install needed:    NO
```

## Environment resolution order (why `+cpu` appeared — and is now resolved)

`TrainingEnvironmentManager.resolve_python()` tries, in order:
`NEXUS_TRAINING_PYTHON` → **`sys.executable`** (when not frozen) → PATH
`python`/`python3` → `python3.11..3.13` → `py` launcher.
`resolve_env()` then tries: managed venv (`<workspace>/training-env`) →
`NEXUS_TRAINING_ENV` → **the current process's active venv** (only when
`python_exe == sys.executable` and `prefix != base_prefix`) → not-found.

The server that reported `torch 2.13.0+cpu` was booted before the cu126 wheel
was installed into the shared venv (torch dist-info mtime `Sep 28 02:58`).
`probe_python_interpreter()` imports torch in the TARGET interpreter and reads
`torch.__version__` — there is no cache, no metadata trust, and no version-string
guessing anywhere in the chain. A fresh probe now returns `2.13.0+cu126` with
`training_ready=true`, because the venv genuinely holds the CUDA build and the
reporting process was superseded by one started after the upgrade.

**Conclusion: the CUDA/CPU report was truthful at the moment it was taken — it
described a venv that has since been upgraded. No code change was needed for
Phase 6/7; the environment is CUDA-ready.**

## Worktree rules respected

`agents/REVIEW_LOCK.md` forbids any agent from committing, pushing, rebasing or
cleaning inside `nse-review-main` (it is the human review console, refreshed
only by the owner's `update_review.sh`). All implementation happens in the
isolated worktree `C:/c/tmp/model-prov` on branch
`agent/hermes/model-provisioning-control-plane`, cut from `origin/main` with a
read-only junction to the shared `frontend/node_modules`. The review worktree is
treated as a read-only observation plane only.
