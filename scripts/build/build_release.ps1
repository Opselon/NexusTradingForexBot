# =============================================================================
# Nexus Scalp Engine — Release Build Orchestrator (Windows PowerShell)
# =============================================================================
# Usage:
#   .\scripts\build\build_release.ps1 [-Version 9.0.0] [-Channel stable]
#       [-Arch x64] [-SkipGates] [-SkipInstaller] [-SkipSmoke]
#
# Pipeline (spec section 57):
#   validate version -> repo audit / clean-tree check -> quality gates
#   (ruff/mypy/pytest) -> frontend production build + dist validation
#   -> detect target -> PyInstaller onedir + onefile
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
Write-Step "1/11 Validate version (canonical source: pyproject.toml)"
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
# 2. Git state + secret guard
# ---------------------------------------------------------------------------
Write-Step "2/11 Repository audit (git state + secret guard)"
# Release-wave Finding 2: the build identity binds to the FULL 40-hex commit
# SHA. Short SHAs are presentation-only (the Pass line below may display it).
$GitCommitFull = (& git rev-parse HEAD).Trim()
if ($GitCommitFull -notmatch '^[0-9a-fA-F]{40}$') {
    Fail "RELEASE_IDENTITY_FULL_SHA_REQUIRED: got '$GitCommitFull'"
}
$GitCommit = $GitCommitFull
$Dirty = (& git status --porcelain) -ne $null -and (& git status --porcelain | Measure-Object).Count -gt 0
$Tag = try { (& git describe --tags --exact-match 2>$null) } catch { $null }
if ($Dirty) {
    Write-Host "[RELEASE] WARNING: working tree is dirty (build still proceeds with dirty marker)." -ForegroundColor Yellow
}
Pass "commit=$GitCommit dirty=$Dirty tag=$Tag"

# HARD SECRET GUARD: refuse to build from a config carrying a real token.
$TokenGuard = & $Py (Join-Path $PSScriptRoot "update_helpers.py") token-guard (Join-Path $Root ".")
if ($LASTEXITCODE -ne 0) {
    Fail "configs/live.yaml contains a real bot token — mask it before building a release."
}
Pass "secret guard: no real telegram token in configs/live.yaml"

# ---------------------------------------------------------------------------
# 3. Quality gates
# ---------------------------------------------------------------------------
Write-Step "3/11 Quality gates (ruff / mypy / pytest)"
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
# 4. Frontend production build + dist validation (CONTRACT frozen decision #11)
# ---------------------------------------------------------------------------
# The end user never runs Node: the release pipeline runs `npm ci` +
# `npm run build` in frontend/ HERE (build-time only), FAIL-LOUD validates
# dist (CONTRACT #11 gate), then bakes dist into the onedir tree. `npm run
# build` is the single source of truth for how the bundle is produced
# (frontend/package.json), so a build-script change can never drift from the
# bytes this release ships.
Write-Step "4/11 Frontend production build + dist validation"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Fail "npm not found on PATH - Node.js is required to BUILD this release (the end user never needs it)"
}
$FrontendDir = Join-Path $Root "frontend"
Push-Location $FrontendDir
try {
    # Wave worktrees junction node_modules into the SHARED parent checkout's
    # install. `npm ci` deletes node_modules first, so running it here would
    # destroy that link — and, if the removal follows reparse points, the
    # shared install itself. Never risk a shared tree: deps are already
    # served through the link, so only a real directory is re-provisioned.
    $NodeModules = Join-Path $FrontendDir "node_modules"
    $IsSharedInstall = ((Test-Path $NodeModules) -and (((Get-Item $NodeModules -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0))
    if ($IsSharedInstall) {
        Write-Host "[RELEASE] node_modules is a junction into a shared install - skipping npm ci (deps already provisioned)" -ForegroundColor Yellow
    } else {
        & npm ci
        if ($LASTEXITCODE -ne 0) { Fail "frontend npm ci failed (exit $LASTEXITCODE)" }
    }
    & npm run build
    if ($LASTEXITCODE -ne 0) { Fail "frontend production build failed (npm run build exit $LASTEXITCODE)" }
} finally { Pop-Location }
$FrontendDist = Join-Path $Root "frontend\dist"
if (-not (Test-Path (Join-Path $FrontendDist "index.html"))) {
    Fail "frontend build produced no dist\index.html at $FrontendDist"
}
Pass "frontend production build: $FrontendDist"
# CONTRACT #11: missing index.html/JS/CSS/manifest/favicon, broken /assets refs
# or an external http(s) boot URL = STOP RELEASE.
& $Py (Join-Path $Root "scripts\build\validate_frontend_dist.py") $FrontendDist
if ($LASTEXITCODE -ne 0) { Fail "frontend dist validation failed (validate_frontend_dist.py exit $LASTEXITCODE)" }
Pass "frontend dist validated (validate_frontend_dist.py)"

# ---------------------------------------------------------------------------
# 5. Build windows-x64 with PyInstaller (onedir + onefile)
# ---------------------------------------------------------------------------
Write-Step "5/11 PyInstaller build (windows-$Arch) — onedir + onefile"

# EU-RELEASE-001: canonical branded application icon (generated from
# frontend/public/icon-512.png). Without --icon PyInstaller stamps its
# generic placeholder onto the EXE, the Start Menu entry and the shortcut.
& $Py (Join-Path $Root "scripts\build\generate_app_icon.py")
if ($LASTEXITCODE -ne 0) { Fail "application icon generation failed" }
if ($Arch -ne "x64") {
    Fail "Only windows-x64 is supported by the dependency stack (torch/polars/MetaTrader5). '$Arch' requested = BLOCKED."
}
$BuildDir = Join-Path $Root "release\build\windows-x64"
if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

# Stale-EXE lock guard (WinError 32 hardening): if a previous build EXE is
# still running it locks the onedir and --clean fails. Terminate ONLY the
# process whose image path is inside this build's output tree — never a
# user-launched engine elsewhere.
$StaleLock = Get-Process -Name "NexusScalpEngine" -ErrorAction SilentlyContinue | Where-Object {
    try { $_.Path -like "$BuildDir*" -or $_.Path -like "$Root\release\*" } catch { $false }
}
foreach ($p in $StaleLock) {
    Write-Host "[RELEASE] Terminating stale packaged EXE from previous build (pid $($p.Id): $($p.Path))" -ForegroundColor Yellow
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
}
if ($StaleLock) { Start-Sleep -Seconds 1 }

$IconPath = Join-Path $Root "installer\NexusScalpEngine.ico"
if (-not (Test-Path $IconPath)) {
    Fail "Application icon missing: $IconPath — run: python scripts\build\generate_app_icon.py (it converts the canonical frontend/public/icon-512.png)"
}

# WINDOWS-UX-001: PE version-info resource (ProductName / FileDescription /
# InternalName / OriginalFilename / CompanyName). Without it PyInstaller
# embeds the default Python resource, and Explorer + Task Manager + the
# taskbar brand the process image as Python instead of the product.
& $Py (Join-Path $Root "scripts\build\generate_version_info.py") $Version
if ($LASTEXITCODE -ne 0) { Fail "version-info generation failed" }
$VersionInfoPath = Join-Path $Root "installer\version_info.txt"
if (-not (Test-Path $VersionInfoPath)) {
    Fail "Version-info resource missing: $VersionInfoPath — run: python scripts\build\generate_version_info.py"
}
$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $PyInstaller)) { Fail "pyinstaller not found — install with: .venv\Scripts\python -m pip install pyinstaller" }

$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ"
$webAssetHash = (Get-FileHash -Algorithm SHA256 (Join-Path $Root "Web\app.js")).Hash.ToLower()
$webIndexHash = (Get-FileHash -Algorithm SHA256 (Join-Path $Root "Web\index.html")).Hash.ToLower()
$webApiClientHash = (Get-FileHash -Algorithm SHA256 (Join-Path $Root "Web\api_client.js")).Hash.ToLower()
$webStylesHash = (Get-FileHash -Algorithm SHA256 (Join-Path $Root "Web\styles.css")).Hash.ToLower()
# CONTRACT frozen decision #11: release identity also carries the SHA256 of
# the built React entrypoint baked into the onedir tree.
$FrontendIndexPath = Join-Path $Root "frontend\dist\index.html"
if (-not (Test-Path $FrontendIndexPath)) {
    Fail "frontend/dist/index.html missing - frontend build must precede PyInstaller (CONTRACT #11)"
}
$frontendIndexHash = (Get-FileHash -Algorithm SHA256 $FrontendIndexPath).Hash.ToLower()
# Guard the hash against staleness: Get-FileHash reads whatever bytes are on
# disk, so the build cannot stamp a dist that the validator just rejected.
$env:NSE_FRONTEND_INDEX_HASH = $frontendIndexHash
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
    web_asset_hash  = $webAssetHash
    web_index_hash  = $webIndexHash
    web_api_client_hash = $webApiClientHash
    web_styles_hash = $webStylesHash
    frontend_index_hash = $frontendIndexHash
    frontend_dist = "frontend/dist"
} | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $Root "build-info.json"), $buildInfo, (New-Object System.Text.UTF8Encoding($false)))

& $PyInstaller --noconfirm --clean `
    --onedir --name "NexusScalpEngine" `
    --icon $IconPath `
    --version-file $VersionInfoPath `
    --add-data "$Root\Web;Web" `
    --add-data "$Root\frontend\dist;frontend/dist" `
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
    --add-data "$Root\src\nexus_scalp;training_payload\src\nexus_scalp" `
    --add-data "$Root\configs;training_payload\configs" `
    src\nexus_scalp\release\packaged_main.py
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller onedir build failed (exit $LASTEXITCODE)" }
Pass "onedir build: $BuildDir\onedir\NexusScalpEngine\NexusScalpEngine.exe"

& $PyInstaller --noconfirm --clean `
    --onefile --name "NexusScalpEngine-CLI" `
    --icon $IconPath `
    --version-file $VersionInfoPath `
    --exclude-module "torch" `
    --exclude-module "polars" `
    --exclude-module "numpy" `
    --exclude-module "pyarrow" `
    --exclude-module "MetaTrader5" `
    --distpath (Join-Path $BuildDir "onefile") `
    --workpath (Join-Path $BuildDir "work-cli") `
    --specpath $BuildDir `
    --add-data "$Root\src\nexus_scalp;training_payload\src\nexus_scalp" `
    --add-data "$Root\configs;training_payload\configs" `
    src\nexus_scalp\release\cli_shim.py
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller onefile CLI build failed (exit $LASTEXITCODE)" }
Pass "onefile CLI: $BuildDir\onefile\NexusScalpEngine-CLI.exe"

# ---------------------------------------------------------------------------
# 6. EXE smoke tests (launch / version / health)
# ---------------------------------------------------------------------------
Write-Step "6/11 EXE smoke tests"
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
# 7. Stage the release tree
# ---------------------------------------------------------------------------
Write-Step "7/11 Stage release tree (portable layout)"
$OutDir = Join-Path $Root "release\v$Version\windows\x64"
if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Stage = Join-Path $OutDir "portable"

# EU-01 (payload hygiene): every EXE launch in step 5 anchored its runtime
# workspace to the bundle dir, so the smoke run wrote SQLite databases,
# paper state, logs and archive/ INTO this bundle. Shipping them would hand
# every user the build machine's trading history. Clean here, then let the
# script's own invariant (no *.db / *.log / paper_state.json anywhere in the
# payload) fail the release if anything survives.
& (Join-Path $Root "scripts\build\clean_payload.ps1") -BundleDir (Join-Path $BuildDir "onedir\NexusScalpEngine")
if ($LASTEXITCODE -ne 0) { Fail "payload hygiene failed: build-machine runtime state survived in the staged bundle" }

# Portable bundle = onedir + asset dirs (configs already embedded; docs)
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Copy-Item (Join-Path $BuildDir "onedir\NexusScalpEngine\*") $Stage -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "docs") | Out-Null
Copy-Item (Join-Path $Root "docs\*") (Join-Path $Stage "docs") -Recurse -Force
# Stage the canonical build identity at the portable root (next to the EXE)
# so verify-release can cross-check it against the manifest.
Copy-Item (Join-Path $Root "build-info.json") (Join-Path $Stage "build-info.json") -Force
# Portable Web must mirror _internal/Web for the web server fallback (empty portable/Web broke production panel)
$portableWeb = Join-Path $Stage "Web"
# Join-Path takes exactly two positional args on Windows PowerShell 5.1 (the
# release host shell here); only pwsh 7 accepts three. Nest the calls so the
# script is semantically identical on both 5.1 and CI's pwsh 7.
$internalWeb = Join-Path (Join-Path $Stage "_internal") "Web"
if (Test-Path $internalWeb) {
    if (Test-Path $portableWeb) { Remove-Item $portableWeb -Recurse -Force -ErrorAction SilentlyContinue }
    Copy-Item $internalWeb $portableWeb -Recurse -Force
}

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

# CONTRACT frozen decision #11: the staged onedir tree MUST carry the React
# dist (PyInstaller 6 onedir places --add-data under _internal\) -- assert it
# on the exact tree the installer/zip ship. This is the release-side twin of
# the CI assertion (release.yml EUR_FRONTEND_MISSING).
$StagedFrontendIndex = Join-Path $Stage "_internal\frontend\dist\index.html"
if (-not (Test-Path $StagedFrontendIndex)) {
    Fail "staged tree missing _internal\frontend\dist\index.html - PyInstaller --add-data frontend/dist did not land"
}
# And the dist must be the SAME bytes the build-info.json hash describes.
$StagedHash = (Get-FileHash -Algorithm SHA256 $StagedFrontendIndex).Hash.ToLower()
if ($StagedHash -ne $frontendIndexHash) {
    Fail "staged frontend/dist/index.html hash mismatch: staged=$StagedHash build-info=$frontendIndexHash"
}
Pass "packaged frontend dist present: $StagedFrontendIndex"

# Onefile CLI into cli/
$CliDir = Join-Path $OutDir "cli"
New-Item -ItemType Directory -Force -Path $CliDir | Out-Null
Copy-Item (Join-Path $BuildDir "onefile\NexusScalpEngine-CLI.exe") $CliDir -Force

# ---------------------------------------------------------------------------
# 8. Inno Setup installer
# ---------------------------------------------------------------------------
Write-Step "8/11 Installer (Inno Setup)"
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
# 9. Clean-install test (./scripts/build/clean_install_test.ps1)
# ---------------------------------------------------------------------------
Write-Step "9/11 Clean-install test"
if (-not $SkipCleanInstallTest) {
    $TestScript = Join-Path $PSScriptRoot "clean_install_test.ps1"
    if (Test-Path $TestScript) {
        # BUG-293: -StartSmoke runs the packaged bare-launch START smoke
        # against the INSTALLED layout (fresh double-click story).
        & $TestScript -SetupExe (Join-Path $OutDir "NexusScalpEngine-$Version-win-x64-setup.exe") -StartSmoke
        if ($LASTEXITCODE -ne 0) { Fail "clean-install test failed" }
    } else {
        Write-Host "[RELEASE] clean_install_test.ps1 not present — skipped" -ForegroundColor Yellow
    }
} else { Write-Host "[RELEASE] clean-install test skipped" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# 10. Checksums + manifest + SBOM + secrets scan
# ---------------------------------------------------------------------------
Write-Step "10/11 Checksums / manifest / SBOM / secrets scan"
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
Try { $OutRootRel = [System.IO.Path]::GetRelativePath($OutDir, $OutDir) } Catch { $OutRootRel = "" }
$shaLines = foreach ($a in $Artifacts) {
    $h = (Get-FileHash -Algorithm SHA256 -Path $a).Hash.ToLower()
Try { $rel = [System.IO.Path]::GetRelativePath($OutDir, $a) } Catch { $rel = $a.Substring($OutDir.Length).TrimStart("\\") }
    "$h  $rel"
}
Set-Content -Path $SHA256Sums -Value $shaLines -Encoding ascii
New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "manifests") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "sbom") | Out-Null
Pass "SHA256SUMS.txt written for $($Artifacts.Count) artifacts"

# Manifest via python helper (build stamped)
# Manifest via shared helper (build stamped + embedded in portable tree)
& $Py (Join-Path $PSScriptRoot "update_helpers.py") manifest $OutDir
if ($LASTEXITCODE -ne 0) { Fail "manifest generation failed" }

# SBOM
# SBOM
& $Py (Join-Path $PSScriptRoot "update_helpers.py") sbom $OutDir
if ($LASTEXITCODE -ne 0) { Fail "SBOM generation failed" }

# Secrets scan (python helper — scans the staged tree)
# Secrets scan (shared helper — scans the staged tree)
& $Py (Join-Path $PSScriptRoot "update_helpers.py") scan-tree $Stage
if ($LASTEXITCODE -ne 0) { Fail "secrets scan failed" }
# Embed the release manifest inside the portable tree so `nexus update`
# can verify release-manifest.json from inside the payload zip (TASK-9 §64).
$ManifestSrc = Join-Path $OutDir "manifests\release-manifest.json"
if (Test-Path $ManifestSrc) {
    Copy-Item $ManifestSrc (Join-Path $Stage "release-manifest.json") -Force
    Pass "release-manifest.json embedded in portable tree"
}
Pass "checksums + manifest + SBOM + secrets scan complete"

# ---------------------------------------------------------------------------
# 11. verify-release (full tree self-check)
# ---------------------------------------------------------------------------
Write-Step "11/11 Release verification"
if (-not $SkipSmoke) {
    & (Join-Path $Root ".venv\Scripts\python.exe") (Join-Path $PSScriptRoot "update_helpers.py") verify $Stage
    if ($LASTEXITCODE -ne 0) { Fail "release verification failed" }
} else { Write-Host "[RELEASE] verification skipped (-SkipSmoke)" -ForegroundColor Yellow }

Write-Host "`n================================================================" -ForegroundColor Green
Write-Host "  RELEASE READY: v$Version ($Channel) windows-$Arch" -ForegroundColor Green
Write-Host "  Output: $OutDir" -ForegroundColor Green
Write-Host "  Manifest: $OutDir\manifests\release-manifest.json" -ForegroundColor Green
Write-Host "================================================================`n" -ForegroundColor Green