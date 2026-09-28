# Runtime dependency closure (developer + end-user)

## The failure this prevents

```
File "NexusTradingForexBot.py", line 53, in <module>
    import uvicorn
File ".../uvicorn/main.py", line 14, in <module>
    import click
ModuleNotFoundError: No module named 'click'
```

`click` is not a project dependency: it is what the **installed uvicorn
declares it needs**. An environment can therefore hold `uvicorn` while its
transitive `click` is absent, and `import uvicorn` — the first thing the
launcher does — dies before any NSE diagnostic can run.

Two independent gaps produced that outcome:

1. **Nothing verified the declared closure before importing it.** The failure
   surfaced as a raw traceback with no repair path and no statement of which
   dependency was missing or who required it.
2. **The standard integrity tools are blind to it.** `pip check` and
   `uv pip check` compare dist-info METADATA only. A distribution whose
   `dist-info` exists while its module files are gone — this repository's
   environment really contained `python_dotenv-1.2.3.dist-info` with no
   `dotenv/` package — is reported as fully satisfied by both. Only an
   importability probe finds it.

## The three-layer answer

| Layer | Owner | What it guarantees |
|-------|-------|--------------------|
| Developer checkout | `NexusTradingForexBot.py` startup gate | verifies, then repairs via `uv pip install -e .`, then starts |
| Developer / CI | `python -m nexus_scalp.release.runtime_deps` | offline closure report; exit 1 when incomplete |
| Release artifact | `installer/install.ps1` + `scripts/build/build_release.ps1` | fail-closed install; refuses to package an incomplete runtime |
| End-user runtime | frozen bundle | ships its closure; the build gate owns it (no auto-install) |

## How the closure is derived

`src/nexus_scalp/release/runtime_deps.py` walks, transitively:

* source checkout -> `pyproject.toml` `[project].dependencies`
* packaged runtime -> installed metadata of `nexus-scalp-engine`

then follows each **installed distribution's own** `requires` (skipping
`extra` markers and evaluating environment markers). The uvicorn -> click edge
is discovered from uvicorn's metadata, never from a list in this repository, so
a new dependency in `pyproject.toml` is verified without touching any verifier.

A distribution is reported as unusable only when **none** of its import roots
resolve. Metadata legitimately names nested paths and console scripts
(`torch` -> `functorch`, `sympy` -> `isympy`); treating those healthy installs
as broken would make the gate noise.

## Behaviour

Developer checkout (`NexusTradingForexBot.py`):

```
NSE runtime dependency validation failed.

Missing or unusable runtime dependencies (1):
  - click: not installed (required by uvicorn)

Checked 47 declared runtime dependencies (pyproject.toml [project].dependencies).

Developer checkout detected: repairing the runtime dependency closure with the
repository's own dependency workflow.
$ uv pip install --python <venv-python> -e <checkout>
Runtime dependency closure restored — starting NSE.
```

Still broken after repair -> exit 1 with the diagnostic and the documented
bootstrap command. The application never continues into a raw traceback, and
nothing is suppressed: an incomplete installation still fails, loudly.

Repair has two stages because there are two defect classes: an editable install
(fixes "never installed", e.g. `click`), then a forced reinstall of anything
still unusable (fixes "dist-info present, files gone", e.g. `python-dotenv`).

## Escape hatches and boundaries

* Auto-repair **never** runs in a frozen bundle: the packaged runtime ships its
  closure inside the PyInstaller archive and dist metadata is absent there.
* `NSE_NO_AUTO_INSTALL=1` disables auto-repair everywhere (CI, probes, operators
  who must not have a startup path touch the environment) — the verification and
  the failure still happen.
* `--repair` is required on the CLI; no path installs implicitly.
* No dependency is pinned here and uvicorn is not upgraded: the fix restores the
  declared closure rather than changing the dependency tree.
* `release/health.py` RUNTIME check reports an incomplete closure as FAIL, so
  `nexus doctor` (and the launcher's pre-flight table) surface it before the
  engine boots — the RUNTIME category is the only place it can be reported
  before an import kills the process.

## Commands

```bash
python -m nexus_scalp.release.runtime_deps            # report (exit 1 if incomplete)
python -m nexus_scalp.release.runtime_deps --json     # machine-readable (CI / release)
python -m nexus_scalp.release.runtime_deps --repair   # repair, then re-verify
python -m nexus_scalp.release.runtime_deps --no-import-check
```

## Tests

`tests/unit/test_runtime_dependency_closure.py` (offline, no installs): closure
derivation, uvicorn -> click discovery, missing vs unusable detection, the
no-false-positive contract, fail-closed gate behaviour, frozen-bundle
exemption, `NSE_NO_AUTO_INSTALL`, repair workflow + escalation, and a source
guard that no runtime module outside the gate executes a `pip install`.
