# =============================================================================
# Nexus Scalp Engine — Release Build Orchestrator (Windows PowerShell)
# =============================================================================
# Usage:
#   .\scripts\build\build_release.ps1 [-Version 9.0.0] [-Channel stable]
#       [-Arch x64] [-SkipGates] [-SkipInstaller] [-SkipSmoke]
#
# Pipeline (spec section 57):
#   validate version -> repo audit / clean-tree check -> quality gates
#   (ruff/mypy/pytest) -> detect target -> PyInstaller onedir + onefile
#   -> EXE smoke tests -> stage release tree -> Inno Setup installer
#   -> clean-install test (optional) -> SHA256 + manifest + SBOM + secrets scan
#   -> verify-release -> release metadata
#
# Any failure = STOP RELEASE (exit non-zero) and report the exact failure.
# =============================================================================
param(
    [string]$Version = "",
    [string]$Channel = "stable",
    [string]$Arch = "x64",
    [switch]$SkipGates,
    [switch]$SkipInstaller,
    [switch]$SkipSmoke,
    [switch]$SkipCleanInstallTest
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

function Write-Step($Msg) {
    Write-Host "`n================================================================" -ForegroundColor Cyan
    Write-Host "  $Msg" -ForegroundColor Cyan
    Write-Host "================================================================`n" -ForegroundColor Cyan
}
function Fail($Msg) {
    Write-Host "`n[RELEASE] BLOCKED: $Msg" -ForegroundColor Red
    exit 1
}
function Pass($Msg) {
    Write-Host "[RELEASE] OK: $Msg" -ForegroundColor Green
}

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Fail "venv python not found at $Py" }

# ---------------------------------------------------------------------------
# 1. Version — single canonical source (pyproject.toml)
# ---------------------------------------------------------------------------
Write-Step "1/10 Validate version (canonical source: pyproject.toml)"
if (-not $Version) {
    $m = Select-String -Path pyproject.toml -Pattern '^version = "([^"]+)"'
    if (-not $m) { Fail "cannot read version from pyproject.toml" }
    $Version = $m.Matches[0].Groups[1].Value
}
if ($Version -notmatch '^\d+\.\d+\.\d+') {
    Fail "invalid version '$Version' — must be x.y.z"
}
Pass "Canonical version: $Version (channel: $Channel)"

# ---------------------------------------------------------------------------
# 2. Git state
# ---------------------------------------------------------------------------
Write-Step "2/10 Repository audit (git state)"
$GitCommit = (& git rev-parse --short HEAD).Trim()
$Dirty = (& git status --porcelain) -ne $null -and (& git status --porcelain | Measure-Object).Count -gt 0
$Tag = (& git describe --tags --exact-match 2>$null)
if ($Dirty) {
    Write-Host "[RELEASE] WARNING: working tree is dirty (build still proceeds with dirty marker)." -ForegroundColor Yellow
}
Pass "commit=$GitCommit dirty=$Dirty tag=$Tag"

# ---------------------------------------------------------------------------
# 3. Quality gates
# ---------------------------------------------------------------------------
Write-Step "3/10 Quality gates (ruff / mypy / pytest)"
if (-not $SkipGates) {
    & $Py -m ruff check . --fix --unsafe-fixes | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "ruff lint failed" }
    & $Py -m ruff format . | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "ruff format failed" }
    & $Py -m mypy src
    if ($LASTEXITCODE -ne 0) { Fail "mypy failed" }
    & $Py -m pytest tests/unit/ -q --tb=short
    if ($LASTEXITCODE -ne 0) { Fail "unit tests failed" }
    Pass "quality gates green"
} else { Write-Host "[RELEASE] gates skipped (-SkipGates)" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# 4. Build windows-x64 with PyInstaller (onedir + onefile)
# ---------------------------------------------------------------------------
Write-Step "4/10 PyInstaller build (windows-$Arch) — onedir + onefile"
if ($Arch -ne "x64") {
    Fail "Only windows-x64 is supported by the dependency stack (torch/polars/MetaTrader5). '$Arch' requested = BLOCKED."
}
$BuildDir = Join-Path $Root "release\build\windows-x64"
if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $PyInstaller)) { Fail "pyinstaller not found — install with: .venv\Scripts\python -m pip install pyinstaller" }

$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ"
$buildInfo = @{
    product         = "NexusScalpEngine"
    version         = $Version
    git_commit      = $GitCommit
    dirty_tree      = $Dirty
    build_timestamp = $stamp
    platform        = "windows"
    architecture    = $Arch
    python          = (& $Py -c "import platform;print(platform.python_version())").Trim()
    channel         = $Channel
    build_mode      = "Release"
    feature_schema  = "scalp_v1"
    installer_version = "1.0.0"
} | ConvertTo-Json
Set-Content -Path (Join-Path $Root "build-info.json") -Value $buildInfo -Encoding utf8

& $PyInstaller --noconfirm --clean `
    --onedir --name "NexusScalpEngine" `
    --add-data "$Root\Web;Web" `
    --add-data "$Root\configs;configs" `
    --add-data "$Root\docs;docs" `
    --add-data "$Root\build-info.json;." `
    --collect-submodules "uvicorn" `
    --collect-submodules "fastapi" `
    --collect-submodules "feedparser" `
    --hidden-import "MetaTrader5" `
    --hidden-import "torch" `
    --hidden-import "polars" `
    --hidden-import "uvicorn.logging" `
    --hidden-import "uvicorn.loops" `
    --hidden-import "uvicorn.loops.auto" `
    --hidden-import "uvicorn.protocols" `
    --hidden-import "uvicorn.protocols.http" `
    --hidden-import "uvicorn.protocols.http.auto" `
    --hidden-import "uvicorn.protocols.websockets" `
    --hidden-import "uvicorn.protocols.websockets.auto" `
    --hidden-import "uvicorn.lifespan" `
    --hidden-import "uvicorn.lifespan.on" `
    --hidden-import "uvicorn.lifespan.off" `
    --hidden-import "uvicorn.lifespan.auto" `
    --hidden-import "engineio.async_drivers.threading" `
    --collect-data "fastapi" `
    --collect-data "uvicorn" `
    --collect-data "starlette" `
    --distpath (Join-Path $BuildDir "onedir") `
    --workpath (Join-Path $BuildDir "work") `
    --specpath $BuildDir `
    src\nexus_scalp\release\packaged_main.py
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller onedir build failed (exit $LASTEXITCODE)" }
Pass "onedir build: $BuildDir\onedir\NexusScalpEngine\NexusScalpEngine.exe"

& $PyInstaller --noconfirm --clean `
    --onefile --name "NexusScalpEngine-CLI" `
    --exclude-module "torch" `
    --exclude-module "polars" `
    --exclude-module "numpy" `
    --exclude-module "pyarrow" `
    --exclude-module "MetaTrader5" `
    --distpath (Join-Path $BuildDir "onefile") `
    --workpath (Join-Path $BuildDir "work-cli") `
    --specpath $BuildDir `
    src\nexus_scalp\release\cli_shim.py
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller onefile CLI build failed (exit $LASTEXITCODE)" }
Pass "onefile CLI: $BuildDir\onefile\NexusScalpEngine-CLI.exe"

# ---------------------------------------------------------------------------
# 5. EXE smoke tests (launch / version / health)
# ---------------------------------------------------------------------------
Write-Step "5/10 EXE smoke tests"
if (-not $SkipSmoke) {
    & (Join-Path $BuildDir "onedir\NexusScalpEngine\NexusScalpEngine.exe") version --plain
    if ($LASTEXITCODE -ne 0) { Fail "packaged EXE version failed" }
    & (Join-Path $BuildDir "onefile\NexusScalpEngine-CLI.exe") version --plain
    if ($LASTEXITCODE -ne 0) { Fail "onefile CLI version failed" }
    $healthJson = & (Join-Path $BuildDir "onedir\NexusScalpEngine\NexusScalpEngine.exe") health --json | Out-String
    if ($healthJson -notmatch '"overall"') { Fail "packaged EXE health did not emit JSON" }
    Pass "packaged EXE launch + version + health OK"
} else { Write-Host "[RELEASE] smoke skipped (-SkipSmoke)" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# 6. Stage the release tree
# ---------------------------------------------------------------------------
Write-Step "6/10 Stage release tree (portable layout)"
$OutDir = Join-Path $Root "release\v$Version\windows\x64"
if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Stage = Join-Path $OutDir "portable"

# Portable bundle = onedir + asset dirs (configs already embedded; docs)
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Copy-Item (Join-Path $BuildDir "onedir\NexusScalpEngine\*") $Stage -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "docs") | Out-Null
Copy-Item (Join-Path $Root "docs\*") (Join-Path $Stage "docs") -Recurse -Force

# licenses/
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "licenses") | Out-Null
if (Test-Path (Join-Path $Root "LICENSE")) {
    Copy-Item (Join-Path $Root "LICENSE") (Join-Path $Stage "licenses") -Force
}
# System prompts welcome — README.txt quick start
$ReadmeTxt = @"
NEXUS SCRAP ENGINE - PORTABLE (windows-x64)
Version: $Version  |  Channel: $Channel  |  Commit: $GitCommit

QUICK START
  1. Run NexusScalpEngine.exe
  2. First start: run 'NexusScalpEngine.exe setup' (or just start — default PAPER mode)
  3. Web dashboard: http://localhost:8080

HEALTH / DIAGNOSTICS
  NexusScalpEngine.exe doctor
  NexusScalpEngine.exe health
  NexusScalpEngine.exe test --quick

User data (config, logs, databases) is stored under %%LOCALAPPDATA%%\NexusScalpEngine
and survives upgrades/repairs. The engine NEVER starts LIVE without explicit
confirmation.

Supported: Windows 10/11 x64. ARM64 is NOT supported by PyTorch/Polars/MetaTrader5.
"@
Set-Content -Path (Join-Path $Stage "README.txt") -Value $ReadmeTxt -Encoding utf8
Pass "portable tree staged at $Stage"

# Onefile CLI into cli/
$CliDir = Join-Path $OutDir "cli"
New-Item -ItemType Directory -Force -Path $CliDir | Out-Null
Copy-Item (Join-Path $BuildDir "onefile\NexusScalpEngine-CLI.exe") $CliDir -Force

# ---------------------------------------------------------------------------
# 7. Inno Setup installer
# ---------------------------------------------------------------------------
Write-Step "7/10 Installer (Inno Setup)"
if (-not $SkipInstaller) {
    $Iscc = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) { Fail "ISCC.exe not found — install Inno Setup 6 (winget install JRSoftware.InnoSetup)" }

    $Iss = Join-Path $Root "installer\NexusScalpEngine.iss"
    & $Iscc $Iss `
        "/DNSE_VERSION=$Version" `
        "/DNSE_CHANNEL=$Channel" `
        "/DNSE_SOURCE_DIR=$Stage" `
        "/DNSE_OUTPUT_DIR=$OutDir"
    if ($LASTEXITCODE -ne 0) { Fail "Inno Setup compile failed (exit $LASTEXITCODE)" }
    Pass "installer: $OutDir\NexusScalpEngine-$Version-win-x64-setup.exe"
} else { Write-Host "[RELEASE] installer skipped (-SkipInstaller)" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# 8. Clean-install test (./scripts/build/clean_install_test.ps1)
# ---------------------------------------------------------------------------
Write-Step "8/10 Clean-install test"
if (-not $SkipCleanInstallTest) {
    $TestScript = Join-Path $PSScriptRoot "clean_install_test.ps1"
    if (Test-Path $TestScript) {
        & $TestScript -SetupExe (Join-Path $OutDir "NexusScalpEngine-$Version-win-x64-setup.exe")
        if ($LASTEXITCODE -ne 0) { Fail "clean-install test failed" }
    } else {
        Write-Host "[RELEASE] clean_install_test.ps1 not present — skipped" -ForegroundColor Yellow
    }
} else { Write-Host "[RELEASE] clean-install test skipped" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# 9. Checksums + manifest + SBOM + secrets scan
# ---------------------------------------------------------------------------
Write-Step "9/10 Checksums / manifest / SBOM / secrets scan"
$ChecksumsDir = Join-Path $OutDir "checksums"
New-Item -ItemType Directory -Force -Path $ChecksumsDir | Out-Null
$Artifacts = @()
if (Test-Path (Join-Path $Stage "NexusScalpEngine.exe")) { $Artifacts += Join-Path $Stage "NexusScalpEngine.exe" }
$CliExe = Join-Path $CliDir "NexusScalpEngine-CLI.exe"
if (Test-Path $CliExe) { $Artifacts += $CliExe }
$SetupExe = Join-Path $OutDir "NexusScalpEngine-$Version-win-x64-setup.exe"
if (Test-Path $SetupExe) { $Artifacts += $SetupExe }
$PortableZip = Join-Path $OutDir "NexusScalpEngine-$Version-win-x64.zip"
if (-not (Test-Path $PortableZip)) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($Stage, $PortableZip)
    if (-not (Test-Path $PortableZip)) { Fail "portable zip creation failed" }
}
$Artifacts += $PortableZip
$SHA256Sums = Join-Path $ChecksumsDir "SHA256SUMS.txt"
# Paths in the sums file are relative to the RELEASE ROOT (OutDir), matching
# the release layout (portable/…, cli/…, *.zip, *-setup.exe).
$OutRootRel = [System.IO.Path]::GetRelativePath($OutDir, $OutDir)
$shaLines = foreach ($a in $Artifacts) {
    $h = (Get-FileHash -Algorithm SHA256 -Path $a).Hash.ToLower()
    $rel = [System.IO.Path]::GetRelativePath($OutDir, $a)
    "$h  $rel"
}
Set-Content -Path $SHA256Sums -Value $shaLines -Encoding ascii
Pass "SHA256SUMS.txt written for $($Artifacts.Count) artifacts"

# Manifest via python helper (build stamped)
& $Py -c "import sys; sys.path.insert(0, 'src'); from pathlib import Path; from nexus_scalp.release import packaging as p; root = Path(r'$OutDir'); artifacts = list(root.glob('portable/*.exe')) + list(root.glob('cli/*.exe')) + list(root.glob('*.zip')) + list(root.glob('*-setup.exe')); p.generate_manifest(artifacts, root / 'manifests' / 'release-manifest.json', channel='$Channel', base_dir=root); print('manifest artifacts:', len(artifacts))"
if ($LASTEXITCODE -ne 0) { Fail "manifest generation failed" }

# SBOM
& $Py -c "import sys; sys.path.insert(0, 'src'); from pathlib import Path; from nexus_scalp.release import packaging as p; p.generate_sbom(out=Path(r'$OutDir') / 'sbom' / 'sbom.spdx.json'); print('SBOM written')"
if ($LASTEXITCODE -ne 0) { Fail "SBOM generation failed" }

# Secrets scan (python helper — scans the staged tree)
& $Py -c "import sys; sys.path.insert(0, 'src'); from pathlib import Path; from nexus_scalp.release import verify as v; res = v.verify_release(Path(r'$Stage'), include_launch=False); sec = next((c for c in res['checks'] if c['check'] == 'Secrets scan'), None); print('secrets scan:', sec['status'] if sec else 'n/a'); sys.exit(1 if (sec and sec['status'] == 'FAIL') else 0)"
if ($LASTEXITCODE -ne 0) { Fail "secrets scan failed" }
Pass "checksums + manifest + SBOM + secrets scan complete"

# ---------------------------------------------------------------------------
# 10. verify-release (full tree self-check)
# ---------------------------------------------------------------------------
Write-Step "10/10 Release verification"
if (-not $SkipSmoke) {
    & (Join-Path $Root ".venv\Scripts\python.exe") -c @"
import sys
sys.path.insert(0, 'src')
from pathlib import Path
from nexus_scalp.release import verify as v
res = v.verify_release(Path(r'$Stage'))
print('OVERALL:', res['overall'])
for c in res['checks']:
    print(f\"{c['status']:5} {c['check']}\")
sys.exit(0 if res['valid'] else 1)
"@
    if ($LASTEXITCODE -ne 0) { Fail "release verification failed" }
} else { Write-Host "[RELEASE] verification skipped (-SkipSmoke)" -ForegroundColor Yellow }

Write-Host "`n================================================================" -ForegroundColor Green
Write-Host "  RELEASE READY: v$Version ($Channel) windows-$Arch" -ForegroundColor Green
Write-Host "  Output: $OutDir" -ForegroundColor Green
Write-Host "  Manifest: $OutDir\manifests\release-manifest.json" -ForegroundColor Green
Write-Host "================================================================`n" -ForegroundColor Green