#!/usr/bin/env python3
"""
Nexus Scalp Engine (NSE) - Main Launcher Redirector
===================================================
Delegates execution to the primary system launcher: NexusTradingForexBot.py.
Ensures both 'python main.py' and 'python NexusTradingForexBot.py' execute
the exact same unified backend engine and web control dashboard.
"""

import sys
from pathlib import Path

# Register `src` directory in sys.path BEFORE importing core
CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import NexusTradingForexBot

if __name__ == "__main__":
    NexusTradingForexBot.main()
