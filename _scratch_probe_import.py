"""Probe: can nexus_scalp.adapters.mt5.mt5_adapter be imported directly (engine-style)?"""

import sys
from pathlib import Path

src = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(src))

# Mirror the engine import order (NexusTradingForexBot.py imports the adapter
# module directly, which forces the package __init__ chain).
try:
    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter

    print("IMPORT-OK", DirectMT5Adapter.__name__)
except Exception as exc:
    print("IMPORT-FAIL", type(exc).__name__, str(exc)[:200])
    import traceback

    traceback.print_exc()
