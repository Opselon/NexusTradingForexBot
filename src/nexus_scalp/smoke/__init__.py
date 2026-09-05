"""Smoke package — production-grade layered runtime verification.

Public surface is intentionally small; most logic lives in runner.py and the
layer modules. Importing this package must not trigger heavy deps (torch is
lazy inside layer bodies).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
