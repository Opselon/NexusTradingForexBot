"""Platform-level runtime integration (Windows shell identity, OS ports).

Sub-packages for behavior that is OS-specific but belongs to the application
runtime rather than to a domain or adapter:

    windows_identity  Windows taskbar / AppUserModelID identity for the
                      running NSE process (``apply_windows_identity``)
"""

from __future__ import annotations
