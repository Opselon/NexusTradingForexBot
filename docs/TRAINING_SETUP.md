# XAUUSD M1 Training Data Setup — First Setup & CLI

Status: CLI help and focused tests are verified on Linux. Source payload packaging
is implemented; a built Windows EXE and native CUDA training remain unverified.

## How the model gets its data (no synthetic fallback, ever)

`nexus_scalp.model_provisioning.dataset_source.prepare_training_dataset(...)` is
the single acquisition entry point for both CLI and web. It accepts:

- `source='file'` — your own CSV / TXT / Parquet (MT5 tab export accepted), or
- `source='broker'` — explicit read-only history from the logged-in MT5
  terminal (existing `DirectMT5Adapter.get_rate_history`, BROKER_NATIVE provenance
  only), or the existing `RemoteMT5GatewayAdapter.get_historical_bars` RPC.

It returns a unique validated CSV (never writes your input file), validates
OHLC/volume/timestamps and explicit instrument metadata, excludes unclosed
candles, and never invents data. An unlabelled user export cannot prove its
broker provenance: you must supply genuine XAUUSD M1 history. Known simulation
provenance is rejected. In particular:
missing ticks are `missing_bar_gaps`, not fabrications. The file path is the
only thing shared with the training worker — no adapter or order path ever
crosses that boundary. The environment manager and worker path are unchanged.

Validated inputs: `candles >= 3000` (`None` = all file rows), broker requires
explicit 3000..100000; the provider's existing 100k bar limit is enforced.
Every failure raises `DatasetSourceError` with the reason; `cancel_event`
cancels cooperatively (the blocking broker call itself cannot be interrupted).

## CLI (same manager/install/worker paths as the app)

```powershell
# First setup, unattended: file source, prepare the pinned training stack
nexus model-train-local --input C:\data\XAUUSD_M1.csv --prepare-environment

# Explicit broker source (logged-in terminal; no engine needed)
nexus model-train-local --source broker --candles 50000 --prepare-environment

# CPU-only, keep candidate without installing over a governed champion
nexus model-train-local --input bars.parquet --candles 10000 --backend cpu --no-install --prepare-environment
```

- `--prepare-environment` is the explicit consent that authorizes
  `TrainingEnvironmentManager.install` (same manager and worker paths as the
  web app). Interactive runs prompt instead; `--json` never prompts.
- `nexus model-train-env` without `--install` is read-only discovery — it never
  downloads or installs anything. A READY managed environment is usable by the
  training worker; you do not need to activate its venv manually.
- No Python found: install a supported 64-bit Python from python.org, then
  re-run `nexus model-train-env` (discovery first, `--install` only with
  consent). The packaged EXE itself and the Official Download path need no
  separate training Python.
- `nexus train-once` is a deprecated alias of
  `model-train-local --source broker` and forwards `--prepare-environment`.
- Progress is real measurement (candles, features, epochs, loss, val_loss,
  measured ETA when available). Ctrl+C requests cooperative cancellation;
  wait for the provider/worker boundary.
- Governance: training completion is NOT promotion. Install happens only over
  an empty or DEV-STARTER slot; a governed champion is never displaced.

## Official downloadable model (operator blocker)

`NEXUS_OFFICIAL_MODEL_BASE_URL` is intentionally empty and no official signed
model bundle is published as a release asset (checked: only EXE/zip/CLI/
release-manifest/SBOM/checksums per tag). Nothing is downloaded or invented
until the operator publishes a signed bundle and sets the base URL.

See `docs/CLI.md` for the rest of the command surface.
