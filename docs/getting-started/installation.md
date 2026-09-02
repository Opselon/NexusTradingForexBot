---
title: Installation
description: Installing Nexus Scalp Engine — packaged release, source bootstrap installer, or developer from-source setup.
lang: en
---

# Installation

Three supported paths. Pick one — they do not mix.

## Path 1 — End users: packaged Windows release (no Python)

Download `NexusScalpEngine-<version>-win-x64-setup.exe` (or the portable ZIP
`NexusScalpEngine-<version>-win-x64.zip`) from
[GitHub Releases](https://github.com/Opselon/NexusTradingForexBot/releases).
The installer bundles the full Python runtime (PyInstaller) — you never touch
Python, pip or PyTorch.

1. Run the installer (per-user, **no admin**) or unpack the portable ZIP.
2. First run opens the **setup wizard** (`nexus setup`): compatibility report →
   mode (**default: PAPER**, never silently LIVE) → symbol → health check.
3. User data (config/logs/databases/models) lives in
   `%LOCALAPPDATA%\NexusScalpEngine` and survives upgrades, repairs and
   uninstalls.

> [!NOTE]
> Release pipeline: `.github/workflows/release.yml` builds packaged Windows x64
> binaries with SHA-256 digests, release manifests and SBOMs.
> Details: [`docs/RELEASE.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/RELEASE.md).

## Path 2 — PowerShell bootstrap installer (no Python, no Git, no admin)

Downloads and provisions everything user-scoped (Python via uv, Git if missing,
engine source from GitHub, a managed venv, dependencies, the `nexus` command on
PATH) with safe-update, repair, recovery and a machine-readable stage protocol:

```powershell
iex (irm https://raw.githubusercontent.com/Opselon/NexusTradingForexBot/main/installer/install.ps1)
```

```powershell
.\install.ps1 -DryRun      # show the plan as JSON, mutate nothing
.\install.ps1 -Manifest    # list the installer's 12 stages (JSON)
.\install.ps1 -Repair      # repair runtime without touching user data
.\install.ps1 -Commit sha  # reproducible pin (Commit > Tag > Branch)
```

Installs under `%LOCALAPPDATA%\Nexus` (override with `-NexusHome`); user config
in `%LOCALAPPDATA%\Nexus\config` always survives reinstall/updates.
Full parameter reference:
[`docs/INSTALL_WINDOWS.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/INSTALL_WINDOWS.md)
· architecture:
[`docs/INSTALLER_ARCHITECTURE.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/INSTALLER_ARCHITECTURE.md).

## Path 3 — Developers: from source

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

pip install --upgrade pip
pip install -e .[dev]

# Smoke-test the whole toolchain — no broker needed
pytest tests/unit -q
```

- Python **3.11.x** is required for a source run (not needed for the packaged release).
- Windows 10/11 **x64** for the native MT5 adapter. **ARM64 is explicitly
  unsupported** (PyTorch/Polars/MetaTrader5 ship no ARM64 wheels); `nexus doctor`
  reports this.
- Linux x64 is developer/Docker (remote-gateway adapter) only.

## Next step

→ [Quickstart](quickstart.md)
