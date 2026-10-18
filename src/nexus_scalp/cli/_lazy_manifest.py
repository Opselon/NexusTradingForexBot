"""EU-03 manifest: the command surface of the lazily-imported CLI modules.

WHY (wave-6 perf): ``nexus --help`` / ``nexus help`` list EVERY command, but
Click's stock help renderer materializes each listed command to read its
one-line summary — which for the lazy modules means importing
``model_studio_routes`` (torch, ~4s), ``ai_commands`` (orchestrator),
``dependency_commands`` (networkx) and ``gateway_commands`` (fastapi). That
made every help pay the whole neural/provider/graph stack, the exact EU-03
invariant ``engine_boot._heavy_*`` shims exist to prevent.

This table is the static registration contract of those modules: the command
names they register plus their first docstring line. ``lazy_subapps`` renders
help straight from it, so the listing is complete and correct WITHOUT the
import; the modules still load on first real dispatch (``get_command``), where
the cost is expected.

KEEP IN SYNC: every command a lazy module registers must appear here, and the
text must stay the command's first docstring line. ``tests/unit/
test_cli_lazy_imports.py`` enforces both — it imports the real modules in a
subprocess and asserts this manifest matches the actual Typer registry and
each command's own short help, so drift fails the suite rather than silently
making help wrong.
"""

from __future__ import annotations

# {command name: first docstring line} for the four lazy command modules.
# Ordering is the module-registration order (model-studio first), which is
# what `nexus help` showed before the deferral.
LAZY_COMMAND_SHORT_HELP: dict[str, str] = {
    # nexus_scalp.cli.model_studio_commands  (-> nexus_scalp.web.model_studio_routes -> torch)
    "model-quality": "Inspect model quality, parameter count, weights fingerprint, and calibration.",
    "model-predict": "Run an interactive neural network prediction test with uncertainty metrics.",
    "model-stress-test": "Run automated 6-step adversarial stress testing on the model.",
    "model-train-dataset": "Dispatch model training on a chosen dataset into the engine lifecycle.",
    "dataset-download": "Download/ingest historical market candles with strict schema validation.",
    "dataset-inspect": "Inspect dataset features, normalize values, and report statistical distribution.",
    "position-dataset-generate": (
        "Generate Layer-2 Position Management dataset with mathematical labeling & anti-leakage."
    ),
    "position-dataset-validate": (
        "Validate structural, causal, and economic integrity of a Position Manager dataset."
    ),
    "model-list": "List all registered neural model checkpoints in the SQLite catalog.",
    "model-hot-load": "Hot-load a model checkpoint and scaler into live memory without restarting.",
    "model-active": "Show details of the currently hot-loaded active champion model in memory.",
    "model-rollback": "Roll back active model to the previously active champion model from history.",
    "model-verify": "Run pre-load verification battery (tensors, NaN check, weights variance, smoke inference).",
    "position-adviser-packages": (
        "List Position-Adviser model packages and verify artifact integrity OFFLINE."
    ),
    # nexus_scalp.cli.ai_commands  (-> ai_providers.orchestrator)
    "ai": "AI provider ecosystem: providers, models, decisions.",
    # nexus_scalp.cli.dependency_commands  (-> networkx)
    "dependency": "Dependency Intelligence toolkit.",
    # nexus_scalp.cli.gateway_commands  (-> fastapi -> torch + polars)
    "gateway": "Windows MT5 bridge server for the existing Linux gateway client (Demo by default).",
}

#: All command names the lazy modules register (derived from the table above).
LAZY_COMMAND_NAMES: tuple[str, ...] = tuple(LAZY_COMMAND_SHORT_HELP)
