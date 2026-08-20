# main.py + NexusTradingForexBot.py (repo root)

- **PURPOSE:** Root entrypoints. `main.py` — the canonical root launcher
  forwarding to the Typer CLI app; `NexusTradingForexBot.py` — legacy
  convenience redirect (kept for muscle memory; `python NexusTradingForexBot.py
  --doctor` works) forwarding to main.
- **ARCHITECTURE LAYER:** Entrypoint (process root).
- **RESPONSIBILITY:** import the CLI app and dispatch (`app()`); the E402
  per-file ignores in pyproject cover the sys.path/first-import ordering.
- **DEPENDENCIES:** nexus_scalp.cli.main.
- **CONNECTS TO:** shell / Docker / packaged launcher.
- **KEY CONCEPTS:** The documented `python -m nexus_scalp.cli.main run
  --mode LIVE` is the primary entrypoint; these wrappers exist for
  convenience/legacy. The packaged EXE uses release/packaged_main.py
  instead (frozen-root awareness).
- **EDGE CASES & PITFALLS:** never add logic here beyond forwarding (the
  files stay thin proxies); keep the import AFTER sys.path setup (E402
  exemption).