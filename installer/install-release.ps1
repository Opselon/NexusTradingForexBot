# =============================================================================
# Nexus Scalp Engine - One-Command Release Installer (packaged Windows build)
# =============================================================================
# Installs the PACKAGED Windows release published by
# .github/workflows/release.yml on GitHub Releases, with a fail-closed
# download -> hash -> install -> verify chain.
#
# This is the end-user counterpart of installer\install.ps1 (source bootstrap):
#   install.ps1          provisions a SOURCE checkout (git, python, venv, deps)
#   install-release.ps1  installs the BINARY release (no Python, no Git, no admin)
#
# Design goals (same contract as install.ps1; docs/INSTALL_WINDOWS.md):
#   * INTEGRITY IS MANDATORY: the payload is verified against its entry in the
#     release SHA256SUMS.txt (and cross-checked against release-manifest.json)
#     BEFORE anything is installed. Missing entry, malformed digest, SHA256
#     mismatch, contract disagreement or an undersized download all BLOCK the
#     install with a precise reason and a distinct non-zero exit code. An
#     unverified artifact is never installed.
#   * Per-user install, no Administrator rights: the Inno script itself is
#     PrivilegesRequired=lowest and defaults to
#     %LOCALAPPDATA%\Programs\NexusScalpEngine (installer\NexusScalpEngine.iss).
#   * USER DATA (%LOCALAPPDATA%\NexusScalpEngine: config/logs/data/models) is
#     never deleted or overwritten. This script only adds its own state file
#     (state\install-release.json) and bounded log (logs\install-release.log).
#   * Idempotent: a second run against an already-current install exits 0
#     without reinstalling. -Repair re-runs the install for the same version.
#   * Post-install proof: the installed product verifies itself
#     (NexusScalpEngine.exe version + verify-release), exactly like the CI
#     clean-install gate (scripts\build\clean_install_test.ps1).
#   * Machine-readable JSON on stdout ONLY in -Json mode (RC-1 discipline).
#   * PS 5.1 + PowerShell 7 compatible: pure ASCII source, no PS7-only syntax,
#     no interactive prompt on any code path.
#
# Usage:
#   iex (irm https://raw.githubusercontent.com/Opselon/NexusTradingForexbot/main/installer/install-release.ps1)
#   .\install-release.ps1                 # latest stable release
#   .\install-release.ps1 -Version 9.1.0  # pin (builds asset URLs directly,
#                                         # never calls the GitHub API)
#   .\install-release.ps1 -Portable       # install the portable ZIP instead
#   .\install-release.ps1 -DryRun         # resolve + plan, mutate nothing
#   .\install-release.ps1 -Repair         # re-install the recorded version
#   .\install-release.ps1 -Json           # exactly one JSON frame on stdout
#   .\install-release.ps1 -ShowResolvedPaths   # resolved-path JSON, no mutation
#
# Exit codes (documented contract; -Json frames carry the same number as
# "exit_code"):
#   0  success: installed / repaired / already current / dry-run plan
#   1  unexpected internal failure (unhandled exception)
#   2  usage error (bad parameter combination, unsupported -ProtocolVersion)
#   3  environment error (not Windows x64, no %LOCALAPPDATA%, unwritable paths)
#   4  release resolution failed (release/asset not found, no assets, draft)
#   5  GitHub API rate limited (unauthenticated 60/hour) - pin with -Version
#   6  download failed (HTTP error, timeout, empty or undersized payload)
#   7  INTEGRITY BLOCKED (missing SHA256SUMS entry, malformed digest, SHA256
#      mismatch, SHA256SUMS vs release-manifest disagreement) - nothing installed
#   8  install failed (Inno setup.exe non-zero exit, ZIP expansion failure)
#   9  post-install verification failed (installed EXE version/verify-release)
# =============================================================================
param(
    # Release to install. Empty = latest stable release (GitHub API lookup).
    # A pin ("9.1.0" or "v9.1.0") builds the download URLs directly and skips
    # the API call entirely - the recommended path for CI and rate-limited
    # networks.
    [string]$Version = "",

    # Install directory. Default matches the Inno DefaultDirName:
    #   setup.exe -> %LOCALAPPDATA%\Programs\NexusScalpEngine
    #   -Portable -> %LOCALAPPDATA%\Programs\NexusScalpEngine
    [string]$InstallDir = "",

    # Install the portable ZIP (expanded, no uninstaller) instead of the
    # Inno Setup installer.
    [switch]$Portable,

    # Resolve the release and print the plan; download nothing, install
    # nothing, mutate nothing.
    [switch]$DryRun,

    # Re-run the install for the recorded/current version even when the
    # already-current short-circuit would apply. Never touches user data.
    [switch]$Repair,

    # Do not execute the installed EXE at all after install (kiosk/locked-down
    # hosts). The post-install proof is then reported as SKIPPED and the
    # install is recorded DEGRADED instead of AVAILABLE - it is never claimed
    # verified without running the verifier.
    [switch]$NoLaunch,

    # Emit exactly one machine-readable JSON frame on stdout; all human
    # diagnostics go to stderr.
    [switch]$Json,

    # JSON protocol version this installer speaks. Drivers pass the version
    # they expect; anything this script does not implement is refused loudly
    # (exit 2) instead of being silently misinterpreted.
    [int]$ProtocolVersion = 1,

    # Print the resolved-path report as JSON and exit without mutating
    # anything and without any network access.
    [switch]$ShowResolvedPaths
)

$ErrorActionPreference = "Stop"
# Invoke-WebRequest's PS 5.1 progress bar repaints synchronously per chunk and
# can slow downloads 10-100x.
$ProgressPreference = "SilentlyContinue"

# Display-only console UTF-8 so native tool output renders correctly; the
# script file itself stays pure ASCII for PS 5.1 parser compatibility.
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
} catch {
    # Constrained hosts disallow encoding mutation; output mojibake is
    # cosmetic-only and the install still works.
}

# =============================================================================
# Configuration (single source of truth)
# =============================================================================
$Script:RepoSlug = "Opselon/NexusTradingForexBot"
$Script:ApiBase = "https://api.github.com/repos/Opselon/NexusTradingForexBot/releases"
$Script:DownloadBase = "https://github.com/Opselon/NexusTradingForexBot/releases/download"
$Script:InstallerVersion = "1.0.0"
$Script:ProtocolVersionValue = 1

# Size floors (bytes). A payload below its floor is a truncated download and is
# BLOCKED before any hash comparison. The 50-byte contract-file floor mirrors
# the BUG-166 pre-stage guard in .github/workflows/release.yml. 10 MB is orders
# of magnitude below any real PyInstaller payload and far above any error page.
$Script:MinBytesPayload = 10MB
$Script:MinBytesContract = 50

$Script:_DriverMode = [bool]$Json
$Script:DownloadTelemetry = @()
$Script:LogFile = $null
$Script:StatePath = $null
$Script:InstallMutex = $null

# ---------------------------------------------------------------------------
# Exit codes (documented in the file header; mirrored into JSON frames)
# ---------------------------------------------------------------------------
$Script:EXIT_OK = 0
$Script:EXIT_INTERNAL = 1
$Script:EXIT_USAGE = 2
$Script:EXIT_ENVIRONMENT = 3
$Script:EXIT_RELEASE = 4
$Script:EXIT_RATE_LIMIT = 5
$Script:EXIT_DOWNLOAD = 6
$Script:EXIT_INTEGRITY = 7
$Script:EXIT_INSTALL = 8
$Script:EXIT_VERIFY = 9

# =============================================================================
# Console / protocol output helpers
# =============================================================================
# In -Json (driver) mode stdout is reserved for a single JSON frame, so every
# human-readable line is routed to stderr - the RC-1 banner-purity contract.

function Write-Diag {
    param([string]$Message)
    try {
        [Console]::Error.WriteLine("[nexus-release-installer] $Message")
    } catch {
        # Some hosts cannot write stderr; diagnostics are best-effort.
    }
}
function Write-Info {
    param([string]$m)
    if ($Script:_DriverMode) { Write-Diag $m; return }
    Write-Host "  -> $m" -ForegroundColor Cyan
}
function Write-Success {
    param([string]$m)
    if ($Script:_DriverMode) { Write-Diag $m; return }
    Write-Host "  [OK] $m" -ForegroundColor Green
}
function Write-WarnMsg {
    param([string]$m)
    if ($Script:_DriverMode) { Write-Diag "WARN: $m"; return }
    Write-Host "  [WARN] $m" -ForegroundColor Yellow
}
function Write-ErrMsg {
    param([string]$m)
    if ($Script:_DriverMode) { Write-Diag "ERROR: $m"; return }
    Write-Host "  [ERROR] $m" -ForegroundColor Red
}
function Write-Step {
    param([string]$Title)
    if ($Script:_DriverMode) { Write-Diag $Title; return }
    Write-Host ""
    Write-Host "  $Title" -ForegroundColor Magenta
}

function Emit-NexusFrame {
    # The ONLY stdout writer in driver mode. One frame per invocation.
    param([hashtable]$Frame)
    Write-Output (($Frame | ConvertTo-Json -Depth 6 -Compress))
}

function Exit-NexusRelease {
    # Documented failure exit: emits the machine-readable frame (driver mode)
    # or the human error + hint (interactive mode), then exits.
    param(
        [Parameter(Mandatory = $true)][int]$Code,
        [Parameter(Mandatory = $true)][string]$Reason,
        [string]$Hint = ""
    )
    if ($Script:_DriverMode) {
        Emit-NexusFrame @{
            ok              = $false
            protocol_version = $Script:ProtocolVersionValue
            installer_version = $Script:InstallerVersion
            exit_code       = $Code
            reason          = $Reason
            hint            = $(if ($Hint) { $Hint } else { $null })
        }
    } else {
        Write-ErrMsg $Reason
        if ($Hint) { Write-Host "         $Hint" -ForegroundColor Yellow }
    }
    exit $Code
}

# =============================================================================
# Logging (bounded, no secrets)
# =============================================================================
function Initialize-NexusReleaseLog {
    param([string]$DataDir)
    try {
        $logDir = Join-Path $DataDir "logs"
        if (-not (Test-Path -LiteralPath $logDir)) {
            New-Item -ItemType Directory -Force -Path $logDir -ErrorAction Stop | Out-Null
        }
        $Script:LogFile = Join-Path $logDir "install-release.log"
        if ((Test-Path -LiteralPath $Script:LogFile) -and ((Get-Item -LiteralPath $Script:LogFile).Length -gt 1MB)) {
            $tail = Get-Content -LiteralPath $Script:LogFile -Tail 500 -ErrorAction SilentlyContinue
            $utf8NoBom = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllLines($Script:LogFile, $tail, $utf8NoBom)
        }
    } catch {
        $Script:LogFile = $null
    }
}

function Write-NexusLog {
    param([string]$Message)
    if (-not $Script:LogFile) { return }
    try {
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        $utf8NoBom = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::AppendAllText($Script:LogFile, "[$stamp] $Message`r`n", $utf8NoBom)
    } catch {
        # Logging must never break the install.
    }
}

# =============================================================================
# Download integrity helpers (INSTALL-VERIFY semantics, install.ps1 parity)
# =============================================================================
# Partial-file staging -> size floor -> SHA256 comparison -> atomic move.
# A digest mismatch, a missing/malformed expected digest or an undersized file
# is a hard BLOCKED failure, never a warning: a silently corrupted artifact
# becomes a corrupt install that only surfaces at first run.

function Add-NexusDownloadTelemetry {
    param([hashtable]$Entry)
    try { $Script:DownloadTelemetry += $Entry } catch { }
}

function Get-FileSha256 {
    # Streaming SHA-256 (large archives never load into memory).
    # Returns lowercase hex, or $null on any failure.
    param([Parameter(Mandatory = $true)][string]$Path)
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            $stream = [System.IO.File]::OpenRead($Path)
            try {
                $hash = $sha.ComputeHash($stream)
                return (([System.BitConverter]::ToString($hash)).Replace("-", "").ToLowerInvariant())
            } finally {
                $stream.Dispose()
            }
        } finally {
            $sha.Dispose()
        }
    } catch {
        return $null
    }
}

function Test-FileIntegrity {
    # Verify a downloaded artifact BEFORE it is committed to its destination.
    # Throws with a precise reason on missing/empty/undersized/mismatch.
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$ExpectedSha256 = "",
        [long]$MinBytes = 0
    )
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "integrity: downloaded file is missing at $Path"
    }
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -le 0) {
        throw "integrity: downloaded file is empty (0 bytes)"
    }
    if (($MinBytes -gt 0) -and ($item.Length -lt $MinBytes)) {
        throw ("integrity: downloaded file is {0} bytes, below the expected minimum {1} bytes (truncated download suspected)" -f $item.Length, $MinBytes)
    }
    if ($ExpectedSha256) {
        $expected = ($ExpectedSha256 -replace "[^0-9a-fA-F]", "").ToLowerInvariant()
        if ($expected.Length -ne 64) {
            throw ("integrity: expected SHA256 '{0}' is not a 64-hex-digit digest" -f $ExpectedSha256)
        }
        $actual = Get-FileSha256 -Path $Path
        if (-not $actual) {
            throw "integrity: could not hash the downloaded file (read/ACL failure)"
        }
        if ($actual -ne $expected) {
            throw ("integrity: SHA256 MISMATCH (artifact tampered or corrupt): expected {0}, actual {1}" -f $expected, $actual)
        }
        Write-Diag ("integrity: SHA256 verified OK ({0}...)" -f $actual.Substring(0, 16))
    }
    return $true
}

function Invoke-NexusReleaseDownload {
    # Bounded, retrying download with exponential backoff + jitter, optional
    # digest pin and size floor. Verification happens on the .partial file
    # BEFORE the atomic move, so an interrupted or tampered download can never
    # be mistaken for a complete artifact and never lands at $Destination.
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination,
        [int]$MaxAttempts = 3,
        [int]$TimeoutSec = 600,
        [string]$ExpectedSha256 = "",
        [long]$MinBytes = 0,
        [hashtable]$RequestHeaders = $null
    )
    $attempt = 0
    $lastError = $null
    $urlHost = ""
    try { $urlHost = ([Uri]$Url).Host } catch { $urlHost = "unknown" }
    $verification = "skipped"
    while ($attempt -lt $MaxAttempts) {
        $attempt++
        $job = $null
        try {
            $partial = "$Destination.partial-$PID"
            if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue }
            if ($PSVersionTable.PSVersion.Major -ge 7) {
                if ($RequestHeaders) {
                    Invoke-WebRequest -Uri $Url -OutFile $partial -UseBasicParsing -Headers $RequestHeaders -TimeoutSec $TimeoutSec -ErrorAction Stop
                } else {
                    Invoke-WebRequest -Uri $Url -OutFile $partial -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
                }
            } else {
                # PS 5.1 Invoke-WebRequest has no -TimeoutSec: run under a job
                # with a hard wall-clock ceiling instead of hanging forever.
                $job = Start-Job -ScriptBlock {
                    param($u, $o, $h)
                    $ProgressPreference = "SilentlyContinue"
                    if ($h) { Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing -Headers $h }
                    else { Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing }
                } -ArgumentList $Url, $partial, $RequestHeaders
                if (Wait-Job $job -Timeout $TimeoutSec) {
                    Receive-Job $job -ErrorAction SilentlyContinue
                    $state = $job.State
                    Remove-Job $job -Force -ErrorAction SilentlyContinue
                    if ($state -ne "Completed") { throw "download job state: $state" }
                } else {
                    Stop-Job $job -ErrorAction SilentlyContinue
                    Remove-Job $job -Force -ErrorAction SilentlyContinue
                    if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue }
                    throw "download timed out after ${TimeoutSec}s"
                }
            }
            # Integrity gate on the PARTIAL file, before the atomic move.
            if ($ExpectedSha256 -or ($MinBytes -gt 0)) {
                Test-FileIntegrity -Path $partial -ExpectedSha256 $ExpectedSha256 -MinBytes $MinBytes | Out-Null
                if ($ExpectedSha256) { $verification = "sha256" } else { $verification = "size" }
            } else {
                $size = (Get-Item -LiteralPath $partial).Length
                if ($size -le 0) { throw "downloaded file is empty" }
                $verification = "empty-check"
            }
            Move-Item -LiteralPath $partial -Destination $Destination -Force
            $actualSha = Get-FileSha256 -Path $Destination
            Add-NexusDownloadTelemetry @{
                url_host     = $urlHost
                bytes        = (Get-Item -LiteralPath $Destination).Length
                attempts     = $attempt
                expected_sha = $(if ($ExpectedSha256) { $ExpectedSha256.ToLowerInvariant() } else { $null })
                actual_sha   = $actualSha
                verification = $verification
                outcome      = "ok"
            }
            Write-NexusLog ("DOWNLOAD OK host={0} bytes={1} attempts={2} sha256={3} verification={4}" -f $urlHost, (Get-Item -LiteralPath $Destination).Length, $attempt, $actualSha, $verification)
            return $Destination
        } catch {
            $lastError = $_.Exception.Message
            if ($job) {
                Stop-Job $job -ErrorAction SilentlyContinue
                Remove-Job $job -Force -ErrorAction SilentlyContinue
            }
            $partial = "$Destination.partial-$PID"
            if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue }
            Write-Diag "download attempt $attempt/$MaxAttempts failed: $lastError"
            Write-NexusLog "DOWNLOAD FAILED attempt=$attempt host=$urlHost error=$lastError"
            if ($attempt -lt $MaxAttempts) {
                # Exponential backoff + jitter: 1s, 2s, 4s... +/- 0.5s, cap 15s.
                $sleepSec = [math]::Pow(2, $attempt - 1) + (Get-Random -Minimum 0 -Maximum 500) / 1000.0
                Start-Sleep -Seconds ([math]::Min($sleepSec, 15))
            }
        }
    }
    Add-NexusDownloadTelemetry @{
        url_host     = $urlHost
        bytes        = 0
        attempts     = $MaxAttempts
        expected_sha = $(if ($ExpectedSha256) { $ExpectedSha256.ToLowerInvariant() } else { $null })
        actual_sha   = $null
        verification = $verification
        outcome      = "blocked"
    }
    throw "Download failed after $MaxAttempts attempts ($Url): $lastError"
}

function Expand-NexusReleaseZip {
    # Zip-slip-safe extraction: every entry's resolved destination must stay
    # inside $Destination (absolute, drive-qualified, UNC and ../ paths are
    # rejected). Mirrors install.ps1's Expand-NexusZipSafe.
    param(
        [Parameter(Mandatory = $true)][string]$ZipPath,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if (-not (Test-Path -LiteralPath $ZipPath)) { throw "ZIP not found: $ZipPath" }
    $destExisted = Test-Path -LiteralPath $Destination
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $destRoot = [System.IO.Path]::GetFullPath(($Destination + "\"))
    $destRootUpper = $destRoot.ToUpperInvariant()

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    try {
        $zip = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $ZipPath).ProviderPath)
    } catch {
        if (-not $destExisted) {
            Remove-Item -LiteralPath $Destination -Recurse -Force -ErrorAction SilentlyContinue
        }
        throw "ZIP is corrupt or not an archive: $($_.Exception.Message)"
    }
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName
            if ([string]::IsNullOrEmpty($name)) { continue }
            if ($name -match '^([A-Za-z]:|\\\\|/)') {
                throw "ZIP entry has absolute/UNC/drive path (zip-slip blocked): $name"
            }
            if ($name -match '(\.\.|//)') {
                throw "ZIP entry contains traversal (zip-slip blocked): $name"
            }
            $target = [System.IO.Path]::GetFullPath((Join-Path $destRoot $name))
            if (-not $target.ToUpperInvariant().StartsWith($destRootUpper)) {
                throw "ZIP entry escapes destination (zip-slip blocked): $name"
            }
        }
        Expand-Archive -LiteralPath $ZipPath -DestinationPath $Destination -Force
    } catch {
        if (-not $destExisted) {
            Remove-Item -LiteralPath $Destination -Recurse -Force -ErrorAction SilentlyContinue
        }
        throw
    } finally {
        $zip.Dispose()
    }
}

# =============================================================================
# Release resolution (GitHub REST API or direct pinned URLs)
# =============================================================================
function Get-NexusHeaderValue {
    # Best-effort response-header read across the two HTTP error shapes:
    # PS 5.1 WebException.Response (WebHeaderCollection) and PS 7
    # HttpResponseException.Response (HttpResponseMessage).
    param($Response, [string]$Name)
    if (-not $Response) { return $null }
    try {
        if ($Response -is [System.Net.WebHeaderCollection]) {
            $v = $Response[$Name]
            if ($v) { return "$v" }
            return $null
        }
        if ($Response.PSObject.Properties['Headers']) {
            try {
                $v = $Response.Headers[$Name]
                if ($v) { return "$v" }
            } catch { }
            try {
                $values = $null
                if ($Response.Headers.TryGetValues($Name, [ref]$values)) {
                    return (@($values) -join ",")
                }
            } catch { }
        }
    } catch { }
    return $null
}

function Get-NexusHttpStatusCode {
    param($ErrorRecord)
    try {
        $resp = $ErrorRecord.Exception.Response
        if ($resp -and $resp.PSObject.Properties['StatusCode']) {
            return [int]$resp.StatusCode
        }
    } catch { }
    if ("$($ErrorRecord.Exception.Message)" -match '\b(403|429)\b') {
        if ($Matches[1] -eq "429") { return 429 }
        return 403
    }
    return $null
}

function Get-NexusRateLimitHint {
    return "Unauthenticated GitHub API callers get 60 requests/hour. Pin an exact release with -Version <x.y.z>: a pinned install builds the download URLs directly and never calls the API."
}

function Resolve-NexusRelease {
    # Returns a release plan:
    #   @{ tag; version; prerelease; draft; asset_names; asset_urls }
    # - Pinned: no API call; asset URLs are constructed from the tag.
    # - Unpinned: GET /releases/latest (never returns drafts or prereleases).
    param([string]$RequestedVersion)

    if ($RequestedVersion) {
        $ver = $RequestedVersion.Trim()
        if ($ver.ToLowerInvariant().StartsWith("v")) { $ver = $ver.Substring(1) }
        if ($ver -notmatch '^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.\-+]+)?$') {
            throw (New-Object System.Exception("release: '-Version $RequestedVersion' is not a semantic version (expected e.g. 9.1.0 or v9.1.0)"))
        }
        $tag = "v$ver"
        $assetNames = @(
            "NexusScalpEngine-$ver-win-x64-setup.exe",
            "NexusScalpEngine-$ver-win-x64.zip",
            "SHA256SUMS.txt",
            "release-manifest.json"
        )
        $urls = @{}
        foreach ($n in $assetNames) { $urls[$n] = "$Script:DownloadBase/$tag/$n" }
        return @{
            tag        = $tag
            version    = $ver
            prerelease = $false
            draft      = $false
            pinned     = $true
            asset_urls = $urls
            note       = "pinned via -Version (no API lookup)"
        }
    }

    $url = "$Script:ApiBase/latest"
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 60 -ErrorAction Stop -Headers @{
            "Accept"               = "application/vnd.github+json"
            "User-Agent"           = "nexus-release-installer"
            "X-GitHub-Api-Version" = "2022-11-28"
        }
    } catch {
        $status = Get-NexusHttpStatusCode -ErrorRecord $_
        $headers = $null
        try { $headers = $_.Exception.Response.Headers } catch { $headers = $null }
        $remaining = Get-NexusHeaderValue -Response $headers -Name "x-ratelimit-remaining"
        if (($status -eq 429) -or ($status -eq 403 -and "$remaining" -eq "0") -or ("$remaining" -eq "0")) {
            $resetHint = ""
            $reset = Get-NexusHeaderValue -Response $headers -Name "x-ratelimit-reset"
            if ($reset -match '^[0-9]+$') {
                $resetHint = " Limit resets at $([DateTimeOffset]::FromUnixTimeSeconds([long]$reset).UtcDateTime.ToString('yyyy-MM-ddTHH:mm:ssZ')) UTC."
            }
            throw (New-Object System.Exception("release: GitHub API rate limit reached (HTTP $status, x-ratelimit-remaining=$remaining).$resetHint $(Get-NexusRateLimitHint)"))
        }
        if ($status -eq 404) {
            throw (New-Object System.Exception("release: no published release found at $url (404). Publish a release first, or pin one with -Version <x.y.z>."))
        }
        if ($status) {
            throw (New-Object System.Exception("release: GitHub API request failed (HTTP $status) for $url"))
        }
        throw (New-Object System.Exception("release: could not query the GitHub API ($url): $($_.Exception.Message)"))
    }

    try {
        $data = $resp.Content | ConvertFrom-Json
    } catch {
        throw (New-Object System.Exception("release: GitHub API returned a non-JSON body for $url"))
    }
    if ($data.draft) {
        throw (New-Object System.Exception("release: the latest release '$($data.tag_name)' is a DRAFT and cannot be installed."))
    }
    if ($data.prerelease) {
        throw (New-Object System.Exception("release: the latest release '$($data.tag_name)' is a PRERELEASE. Install it explicitly with -Version $($data.tag_name) once you intend to run it."))
    }
    $tag = [string]$data.tag_name
    if (-not $tag) {
        throw (New-Object System.Exception("release: the GitHub API response carries no tag_name"))
    }
    $ver = $tag
    if ($ver.ToLowerInvariant().StartsWith("v")) { $ver = $ver.Substring(1) }

    $urls = @{}
    $names = @()
    foreach ($a in @($data.assets)) {
        if (-not $a) { continue }
        $names += [string]$a.name
        if ($a.browser_download_url) { $urls[[string]$a.name] = [string]$a.browser_download_url }
    }
    if ($names.Count -eq 0) {
        throw (New-Object System.Exception("release: release '$tag' published no assets - nothing to install"))
    }
    return @{
        tag        = $tag
        version    = $ver
        prerelease = [bool]$data.prerelease
        draft      = [bool]$data.draft
        pinned     = $false
        asset_urls = $urls
        asset_names = $names
        note       = "latest stable release (GitHub API)"
    }
}

# =============================================================================
# Release contract files (SHA256SUMS.txt / release-manifest.json)
# =============================================================================
function Get-NexusSumsDigest {
    # Find the SHA256SUMS.txt entry for an exact asset file name. Entries are
    # '<64-hex>  <relative path>' (coreutils style, optionally ' *' for binary
    # mode); the comparison is on the BASE NAME so the release-root form
    # ('NexusScalpEngine-9.1.0-win-x64-setup.exe') and the CI-staged relative
    # forms ('portable/...', 'cli/...') are both handled. Returns lowercase
    # hex, or $null when no entry exists.
    param(
        [Parameter(Mandatory = $true)][string]$SumsPath,
        [Parameter(Mandatory = $true)][string]$FileName
    )
    foreach ($line in (Get-Content -LiteralPath $SumsPath -ErrorAction Stop)) {
        $text = "$line".Trim()
        if (-not $text) { continue }
        if ($text -notmatch '^(?<sha>[0-9a-fA-F]{64})\s+\*?(?<name>.+?)\s*$') { continue }
        $name = $Matches["name"].Trim()
        $base = $name
        $slash = $name.LastIndexOfAny(@([char]'\', [char]'/'))
        if ($slash -ge 0) { $base = $name.Substring($slash + 1) }
        if ($base -ieq $FileName) { return $Matches["sha"].ToLowerInvariant() }
    }
    return $null
}

function Get-NexusManifestDigest {
    # Find the release-manifest.json artifact record for an exact asset name.
    # Returns lowercase hex, or $null when the contract does not list it.
    param(
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)][string]$FileName
    )
    try {
        $data = Get-Content -LiteralPath $ManifestPath -Raw -ErrorAction Stop | ConvertFrom-Json
    } catch {
        return $null
    }
    if (-not $data) { return $null }
    foreach ($a in @($data.artifacts)) {
        if (-not $a) { continue }
        if ([string]$a.name -ieq $FileName) {
            $sha = ([string]$a.sha256).ToLowerInvariant()
            if ($sha -match '^[0-9a-f]{64}$') { return $sha }
            return "malformed"
        }
    }
    return $null
}

# =============================================================================
# State file (durable record of version + digests + outcome)
# =============================================================================
# Location: %LOCALAPPDATA%\NexusScalpEngine\state\install-release.json
# The product's user data (config/logs/data/models) lives in the SAME root and
# is never written, moved or deleted by this script - the state file is a new,
# additive, installer-owned record.

function Read-NexusReleaseState {
    param([string]$StatePath)
    if (-not (Test-Path -LiteralPath $StatePath)) { return $null }
    try {
        return (Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Write-NexusReleaseState {
    # Atomic replace (temp + move): a crash can never leave a truncated state
    # file. BOM-less UTF-8 so any JSON parser accepts it.
    param(
        [Parameter(Mandatory = $true)][string]$StatePath,
        [Parameter(Mandatory = $true)][hashtable]$State
    )
    try {
        $dir = Split-Path -Parent $StatePath
        if (-not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
        }
        $utf8NoBom = New-Object System.Text.UTF8Encoding $false
        $tmp = "$StatePath.tmp-$PID"
        [System.IO.File]::WriteAllText($tmp, ($State | ConvertTo-Json -Depth 6), $utf8NoBom)
        Move-Item -LiteralPath $tmp -Destination $StatePath -Force
    } catch {
        # A state-write failure must never mask the real install outcome.
        Write-Diag "state file could not be written: $($_.Exception.Message)"
    }
}

function Get-NexusReleaseStateObject {
    # Build the state frame for the current run (shared by success and
    # failure paths so the digest + outcome is always recorded).
    param(
        [hashtable]$Plan,
        [string]$Mode,
        [string]$TargetInstallDir,
        [string]$DataDir,
        [string]$AssetName,
        [string]$AssetSha,
        [long]$AssetBytes,
        [string]$SumsDigest,
        [string]$ManifestDigest,
        [string]$Outcome,
        [string]$InstallState,
        [string]$Reason
    )
    return [ordered]@{
        installer_version = $Script:InstallerVersion
        protocol_version  = $Script:ProtocolVersionValue
        installed_at      = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        version           = $(if ($Plan) { [string]$Plan.version } else { $null })
        tag               = $(if ($Plan) { [string]$Plan.tag } else { $null })
        channel           = "stable"
        mode              = $Mode
        install_dir       = $TargetInstallDir
        data_dir          = $DataDir
        asset_name        = $AssetName
        asset_sha256      = $AssetSha
        asset_bytes       = $AssetBytes
        sums_sha256       = $SumsDigest
        manifest_sha256   = $ManifestDigest
        verification      = $Outcome
        install_state     = $InstallState
        reason            = $Reason
        download_telemetry = @($Script:DownloadTelemetry)
    }
}

# =============================================================================
# Environment / path helpers
# =============================================================================
function Test-IsWindows {
    return ($env:OS -eq "Windows_NT")
}

function Get-NexusOsArch {
    # Real OS architecture, invariant to WoW64/Prism emulation
    # (Win32_Processor.Architecture: 0=x86, 5=ARM, 9=x64, 12=ARM64).
    try {
        $proc = Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop | Select-Object -First 1
        switch ([int]$proc.Architecture) {
            12 { return "arm64" }
            9  { return "x64" }
            0  { return "x86" }
            5  { return "arm" }
        }
    } catch {
        # CIM unavailable: fall through to env vars.
    }
    $envArch = $env:PROCESSOR_ARCHITEW6432
    if (-not $envArch) { $envArch = $env:PROCESSOR_ARCHITECTURE }
    switch ($envArch) {
        "ARM64" { return "arm64" }
        "AMD64" { return "x64" }
        "x86"   { return "x86" }
        default {
            if ([Environment]::Is64BitOperatingSystem) { return "x64" }
            return "x86"
        }
    }
}

function Wait-NexusReleaseInstallLock {
    # Single-writer guard (install.ps1 parity): a second installer against the
    # same machine reports a well-formed skip instead of racing the first one.
    try {
        $m = New-Object System.Threading.Mutex($false, "Local\NexusScalpEngineReleaseInstall")
        if ($m.WaitOne(0)) {
            $Script:InstallMutex = $m
            return $true
        }
        $m.Dispose()
        return $false
    } catch {
        # Locking unavailable: proceed rather than brick the installer.
        return $true
    }
}

function Release-NexusReleaseInstallLock {
    if ($Script:InstallMutex) {
        try { $Script:InstallMutex.ReleaseMutex() } catch { }
        try { $Script:InstallMutex.Dispose() } catch { }
        $Script:InstallMutex = $null
    }
}

# =============================================================================
# Install + post-install proof (mirrors scripts\build\clean_install_test.ps1)
# =============================================================================
function Install-NexusReleaseSetup {
    # Silent, per-user Inno install. Switches are exactly the ones CI uses in
    # scripts\build\clean_install_test.ps1 (/VERYSILENT /SUPPRESSMSGBOXES
    # /NORESTART plus /DIR and /LOG), executed with -Wait -PassThru so the
    # exit code is the installer's own.
    param(
        [Parameter(Mandatory = $true)][string]$SetupExe,
        [Parameter(Mandatory = $true)][string]$TargetInstallDir,
        [Parameter(Mandatory = $true)][string]$SetupLog
    )
    $installerArgs = @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        "/DIR=$TargetInstallDir",
        "/LOG=$SetupLog"
    )
    Write-NexusLog ("SETUP START exe={0} args={1}" -f $SetupExe, ($installerArgs -join " "))
    $p = Start-Process -FilePath $SetupExe -ArgumentList $installerArgs -Wait -PassThru
    if ($p.ExitCode -ne 0) {
        $tail = ""
        if (Test-Path -LiteralPath $SetupLog) {
            $tail = ((Get-Content -LiteralPath $SetupLog -Tail 20 -ErrorAction SilentlyContinue) -join " | ")
        }
        throw ("install: setup.exe exited with code {0}. Setup log: {1} {2}" -f $p.ExitCode, $SetupLog, $tail)
    }
    Write-NexusLog "SETUP OK exit=0"
}

function Invoke-NexusPostInstallVerify {
    # Post-install proof against the INSTALLED tree, exactly like the CI
    # clean-install gate:
    #   <install>\NexusScalpEngine.exe version --plain
    #   <install>\NexusScalpEngine.exe verify-release --root <install>
    # The second one is the embedded release verification contract shipped in
    # the installed tree (BUG-160/BUG-166, docs\INSTALL_INTEGRITY.md).
    param([Parameter(Mandatory = $true)][string]$TargetInstallDir)

    $exe = Join-Path $TargetInstallDir "NexusScalpEngine.exe"
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "verify: installed executable missing at $exe"
    }

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $v = (& $exe version --plain 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0 -or $v -notmatch "version") {
            throw "verify: 'version --plain' failed (exit $LASTEXITCODE): $($v.Trim())"
        }
        Write-NexusLog ("VERIFY version ok: {0}" -f $v.Trim())
        $r = (& $exe verify-release --root $TargetInstallDir 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0) {
            throw "verify: 'verify-release' failed (exit $LASTEXITCODE): $($r.Trim())"
        }
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    Write-NexusLog "VERIFY verify-release ok"
    return $exe
}

function Test-NexusAlreadyCurrent {
    # Idempotency gate: same version + same mode + installed EXE present.
    param($State, [string]$Version, [string]$Mode, [string]$TargetInstallDir)
    if (-not $State) { return $false }
    if ([string]$State.version -ne $Version) { return $false }
    if ([string]$State.mode -ne $Mode) { return $false }
    $exe = Join-Path $TargetInstallDir "NexusScalpEngine.exe"
    return (Test-Path -LiteralPath $exe)
}

# =============================================================================
# Entry point
# =============================================================================
if ($MyInvocation.InvocationName -eq ".") { return }

try {
    # -- Protocol / usage validation -----------------------------------------
    if ($ProtocolVersion -ne $Script:ProtocolVersionValue) {
        Exit-NexusRelease -Code $Script:EXIT_USAGE `
            -Reason ("unsupported -ProtocolVersion {0}: this installer speaks protocol version {1}" -f $ProtocolVersion, $Script:ProtocolVersionValue) `
            -Hint "Run with -ShowResolvedPaths to read protocol_version from the resolved-path frame."
    }
    if ($DryRun -and $Repair) {
        Exit-NexusRelease -Code $Script:EXIT_USAGE -Reason "Cannot combine -DryRun with -Repair" `
            -Hint "Use -DryRun to only print the plan, or -Repair to actually re-install."
    }

    # -- Environment ----------------------------------------------------------
    if (-not (Test-IsWindows)) {
        Exit-NexusRelease -Code $Script:EXIT_ENVIRONMENT `
            -Reason "This installer targets Windows 10/11 x64 (packaged release). Run it on the Windows machine that will host the engine."
    }
    $arch = Get-NexusOsArch
    if ($arch -ne "x64") {
        Exit-NexusRelease -Code $Script:EXIT_ENVIRONMENT `
            -Reason ("Unsupported architecture: {0}. The packaged release is x64 only (PyTorch/Polars/MetaTrader5 ship no ARM64 wheels)." -f $arch) `
            -Hint "Source installs are also x64-only; see docs/INSTALL_WINDOWS.md."
    }
    $localAppData = $env:LOCALAPPDATA
    if (-not $localAppData) { $localAppData = $env:APPDATA }
    if (-not $localAppData) {
        Exit-NexusRelease -Code $Script:EXIT_ENVIRONMENT -Reason "%LOCALAPPDATA% is not defined; cannot resolve the per-user install and data roots."
    }

    # -- Resolved roots -------------------------------------------------------
    $DataDir = Join-Path $localAppData "NexusScalpEngine"
    if ($InstallDir) {
        $TargetInstallDir = $InstallDir
    } else {
        # Matches DefaultDirName in installer\NexusScalpEngine.iss.
        $TargetInstallDir = Join-Path $localAppData "Programs\NexusScalpEngine"
    }
    $StagingDir = Join-Path ([System.IO.Path]::GetTempPath()) "nexus-release-install"
    $StatePath = Join-Path $DataDir "state\install-release.json"
    $Mode = "setup"
    if ($Portable) { $Mode = "portable" }
    $Script:StatePath = $StatePath

    $ResolvedReport = [ordered]@{
        protocol_version  = $Script:ProtocolVersionValue
        installer_version = $Script:InstallerVersion
        mode              = $Mode
        version_pin       = $(if ($Version) { $Version } else { $null })
        install_dir       = $TargetInstallDir
        data_dir          = $DataDir
        staging_dir       = $StagingDir
        state_file        = $StatePath
        log_file          = (Join-Path $DataDir "logs\install-release.log")
        release_api       = "$Script:ApiBase/latest"
        dry_run           = [bool]$DryRun
        repair            = [bool]$Repair
        no_launch         = [bool]$NoLaunch
    }
    if ($ShowResolvedPaths) {
        Write-Output (($ResolvedReport | ConvertTo-Json -Depth 5 -Compress))
        exit 0
    }

    Initialize-NexusReleaseLog -DataDir $DataDir
    Write-NexusLog ("RUN start version_pin='{0}' mode={1} dry_run={2} repair={3} no_launch={4} ps={5}" -f $Version, $Mode, $DryRun, $Repair, $NoLaunch, $PSVersionTable.PSVersion)

    # -- Resolve the release --------------------------------------------------
    Write-Step "Resolving release"
    try {
        $Plan = Resolve-NexusRelease -RequestedVersion $Version
    } catch {
        $msg = "$($_.Exception.Message)"
        $code = $Script:EXIT_RELEASE
        if ($msg -match "rate limit") { $code = $Script:EXIT_RATE_LIMIT }
        Exit-NexusRelease -Code $code -Reason $msg -Hint (Get-NexusRateLimitHint)
    }
    Write-Info ("Release: {0} ({1})" -f $Plan.tag, $Plan.note)
    if (-not $Plan.pinned) { Write-Info ("Pinned installs skip the API lookup: -Version {0}" -f $Plan.version) }

    if ($Portable) {
        $PayloadName = "NexusScalpEngine-$($Plan.version)-win-x64.zip"
    } else {
        $PayloadName = "NexusScalpEngine-$($Plan.version)-win-x64-setup.exe"
    }
    $PayloadUrl = $Plan.asset_urls[$PayloadName]
    if (-not $PayloadUrl) {
        $available = ""
        if ($Plan.asset_names) { $available = " Available assets: $(@($Plan.asset_names) -join ', ')." }
        Exit-NexusRelease -Code $Script:EXIT_RELEASE `
            -Reason ("Release {0} does not publish '{1}'.{2}" -f $Plan.tag, $PayloadName, $available)
    }

    # -- Idempotency ----------------------------------------------------------
    $State = Read-NexusReleaseState -StatePath $StatePath
    if ((-not $Repair) -and (Test-NexusAlreadyCurrent -State $State -Version $Plan.version -Mode $Mode -TargetInstallDir $TargetInstallDir)) {
        Write-Success ("Already current: {0} ({1}) is installed at {2} - nothing to do." -f $Plan.version, $Mode, $TargetInstallDir)
        if ($Script:_DriverMode) {
            Emit-NexusFrame @{
                ok               = $true
                action           = "already-current"
                protocol_version = $Script:ProtocolVersionValue
                installer_version = $Script:InstallerVersion
                exit_code        = $Script:EXIT_OK
                version          = $Plan.version
                tag              = $Plan.tag
                mode             = $Mode
                install_dir      = $TargetInstallDir
                data_dir         = $DataDir
                install_state    = "AVAILABLE"
                reason           = "installed version matches the resolved release; re-run with -Repair to reinstall"
            }
        } else {
            Write-Info "User data is untouched. Re-run with -Repair to reinstall the same version."
        }
        Write-NexusLog ("RUN already-current version={0} mode={1}" -f $Plan.version, $Mode)
        exit 0
    }

    # -- Dry run: plan only ---------------------------------------------------
    if ($DryRun) {
        Write-Step "Dry run (nothing downloaded, nothing installed)"
        Write-Info ("Would install {0} from {1}" -f $PayloadName, $PayloadUrl)
        Write-Info ("Install dir: {0}" -f $TargetInstallDir)
        Write-Info ("Data dir:    {0} (never modified)" -f $DataDir)
        Write-Info ("Post-install proof: NexusScalpEngine.exe version --plain + verify-release --root <install_dir>")
        if ($Script:_DriverMode) {
            Emit-NexusFrame @{
                ok               = $true
                action           = "dry-run"
                protocol_version = $Script:ProtocolVersionValue
                installer_version = $Script:InstallerVersion
                exit_code        = $Script:EXIT_OK
                version          = $Plan.version
                tag              = $Plan.tag
                mode             = $Mode
                payload          = $PayloadName
                payload_url      = $PayloadUrl
                install_dir      = $TargetInstallDir
                data_dir         = $DataDir
                staging_dir      = $StagingDir
                install_state    = "UNKNOWN"
                reason           = "dry run: plan only, nothing was downloaded or installed"
            }
        }
        Write-NexusLog ("RUN dry-run version={0} payload={1}" -f $Plan.version, $PayloadName)
        exit 0
    }

    # -- Single-writer lock ---------------------------------------------------
    if (-not (Wait-NexusReleaseInstallLock)) {
        $reason = "another release installer is running on this machine"
        if ($Script:_DriverMode) {
            Emit-NexusFrame @{
                ok               = $false
                skipped          = $true
                protocol_version = $Script:ProtocolVersionValue
                installer_version = $Script:InstallerVersion
                exit_code        = $Script:EXIT_OK
                reason           = $reason
            }
        } else {
            Write-WarnMsg "$reason - exiting without installing. Re-run once it finishes."
        }
        exit 0
    }

    try {
        # -- Staging ----------------------------------------------------------
        Write-Step "Downloading release artifacts"
        try {
            New-Item -ItemType Directory -Force -Path $StagingDir -ErrorAction Stop | Out-Null
        } catch {
            Exit-NexusRelease -Code $Script:EXIT_ENVIRONMENT -Reason ("staging dir is not writable: {0} ({1})" -f $StagingDir, $_.Exception.Message)
        }
        $payloadPath = Join-Path $StagingDir $PayloadName
        $sumsPath = Join-Path $StagingDir "SHA256SUMS.txt"
        $manifestPath = Join-Path $StagingDir "release-manifest.json"

        # Contract files first: the payload is never fetched without its
        # checksum file, and nothing is installed without a verified payload.
        foreach ($contract in @(
            @{ Name = "SHA256SUMS.txt"; Path = $sumsPath; Min = $Script:MinBytesContract },
            @{ Name = "release-manifest.json"; Path = $manifestPath; Min = $Script:MinBytesContract }
        )) {
            $contractUrl = $Plan.asset_urls[$contract.Name]
            if (-not $contractUrl) {
                Exit-NexusRelease -Code $Script:EXIT_RELEASE `
                    -Reason ("Release {0} does not publish {1} - the integrity contract is incomplete, refusing to install." -f $Plan.tag, $contract.Name) `
                    -Hint "Integrity is mandatory: without the release checksum manifest the payload cannot be verified."
            }
            try {
                Invoke-NexusReleaseDownload -Url $contractUrl -Destination $contract.Path -MinBytes $contract.Min | Out-Null
            } catch {
                Exit-NexusRelease -Code $Script:EXIT_DOWNLOAD -Reason ("failed to download {0}: {1}" -f $contract.Name, $_.Exception.Message)
            }
            Write-Info ("Downloaded {0}" -f $contract.Name)
        }

        # Expected digest for the payload: the release SHA256SUMS.txt entry is
        # authoritative; release-manifest.json is an independent second source
        # that must agree when it lists the asset.
        $sumsDigest = Get-NexusSumsDigest -SumsPath $sumsPath -FileName $PayloadName
        if (-not $sumsDigest) {
            Exit-NexusRelease -Code $Script:EXIT_INTEGRITY `
                -Reason ("INTEGRITY BLOCKED: {0} has no entry for '{1}' in SHA256SUMS.txt." -f $Plan.tag, $PayloadName) `
                -Hint "Nothing was installed. The published checksum contract does not cover this asset."
        }
        $manifestDigest = Get-NexusManifestDigest -ManifestPath $manifestPath -FileName $PayloadName
        if ($manifestDigest -eq "malformed") {
            Exit-NexusRelease -Code $Script:EXIT_INTEGRITY `
                -Reason ("INTEGRITY BLOCKED: release-manifest.json lists '{0}' with a malformed SHA256 digest." -f $PayloadName)
        }
        if ($manifestDigest -and ($manifestDigest -ne $sumsDigest)) {
            Exit-NexusRelease -Code $Script:EXIT_INTEGRITY `
                -Reason ("INTEGRITY BLOCKED: SHA256SUMS.txt and release-manifest.json disagree for '{0}' ({1} vs {2})." -f $PayloadName, $sumsDigest, $manifestDigest) `
                -Hint "Two published contract files disagree; treat the release as untrusted until it is republished."
        }
        if ($manifestDigest) {
            Write-Info "Checksum contract cross-checked: SHA256SUMS.txt agrees with release-manifest.json"
        } else {
            Write-WarnMsg "release-manifest.json does not list this asset; relying on the SHA256SUMS.txt entry alone"
        }

        $payloadMinBytes = $Script:MinBytesPayload
        if ($Portable) { $payloadMinBytes = $Script:MinBytesPayload }
        try {
            Invoke-NexusReleaseDownload -Url $PayloadUrl -Destination $payloadPath `
                -ExpectedSha256 $sumsDigest -MinBytes $payloadMinBytes -TimeoutSec 3600 | Out-Null
        } catch {
            $msg = "$($_.Exception.Message)"
            $code = $Script:EXIT_DOWNLOAD
            if ($msg -match "integrity:") { $code = $Script:EXIT_INTEGRITY }
            $stateObj = Get-NexusReleaseStateObject -Plan $Plan -Mode $Mode -TargetInstallDir $TargetInstallDir `
                -DataDir $DataDir -AssetName $PayloadName -AssetSha $sumsDigest -AssetBytes 0 `
                -SumsDigest $sumsDigest -ManifestDigest $manifestDigest `
                -Outcome "blocked" -InstallState "BLOCKED" -Reason $msg
            Write-NexusReleaseState -StatePath $StatePath -State $stateObj
            Exit-NexusRelease -Code $code `
                -Reason ("INTEGRITY BLOCKED: {0}" -f $msg) `
                -Hint "Nothing was installed. Re-run to retry; if it persists, the published release is corrupt or tampered with."
        }
        $assetItem = Get-Item -LiteralPath $payloadPath
        $assetSha = Get-FileSha256 -Path $payloadPath
        Write-Success ("Verified {0} ({1} bytes, sha256 {2}...)" -f $PayloadName, $assetItem.Length, $assetSha.Substring(0, 16))

        # -- Install ----------------------------------------------------------
        Write-Step "Installing"
        $action = "installed"
        if ($Repair) { $action = "repaired" }
        try {
            if ($Portable) {
                Expand-NexusReleaseZip -ZipPath $payloadPath -Destination $TargetInstallDir
                if (-not (Test-Path -LiteralPath (Join-Path $TargetInstallDir "NexusScalpEngine.exe"))) {
                    throw "portable archive did not produce NexusScalpEngine.exe at $TargetInstallDir"
                }
            } else {
                $setupLog = Join-Path $StagingDir "setup.log"
                Install-NexusReleaseSetup -SetupExe $payloadPath -TargetInstallDir $TargetInstallDir -SetupLog $setupLog
            }
        } catch {
            $stateObj = Get-NexusReleaseStateObject -Plan $Plan -Mode $Mode -TargetInstallDir $TargetInstallDir `
                -DataDir $DataDir -AssetName $PayloadName -AssetSha $assetSha -AssetBytes $assetItem.Length `
                -SumsDigest $sumsDigest -ManifestDigest $manifestDigest `
                -Outcome "failed" -InstallState "BLOCKED" -Reason "$($_.Exception.Message)"
            Write-NexusReleaseState -StatePath $StatePath -State $stateObj
            Exit-NexusRelease -Code $Script:EXIT_INSTALL -Reason ("install failed: {0}" -f $_.Exception.Message) `
                -Hint "User data was not touched. Re-run to retry, or use -DryRun to check the plan."
        }
        Write-Success ("Installed {0} ({1}) into {2}" -f $Plan.version, $Mode, $TargetInstallDir)

        # -- Post-install proof ------------------------------------------------
        Write-Step "Post-install verification"
        $verification = "verified"
        $installState = "AVAILABLE"
        $verifyReason = $null
        if ($NoLaunch) {
            $verification = "skipped-no-launch"
            $installState = "DEGRADED"
            $verifyReason = "-NoLaunch: the installed executable was not executed, so the post-install proof did not run"
            Write-WarnMsg "$verifyReason (run 'NexusScalpEngine.exe verify-release --root $TargetInstallDir' yourself to close the gap)"
        } else {
            try {
                $exePath = Invoke-NexusPostInstallVerify -TargetInstallDir $TargetInstallDir
                Write-Success "Installed build verified (version + verify-release)"
                Write-Info ("Executable: {0}" -f $exePath)
            } catch {
                $stateObj = Get-NexusReleaseStateObject -Plan $Plan -Mode $Mode -TargetInstallDir $TargetInstallDir `
                    -DataDir $DataDir -AssetName $PayloadName -AssetSha $assetSha -AssetBytes $assetItem.Length `
                    -SumsDigest $sumsDigest -ManifestDigest $manifestDigest `
                    -Outcome "failed" -InstallState "BLOCKED" -Reason "$($_.Exception.Message)"
                Write-NexusReleaseState -StatePath $StatePath -State $stateObj
                Exit-NexusRelease -Code $Script:EXIT_VERIFY `
                    -Reason ("post-install verification failed: {0}" -f $_.Exception.Message) `
                    -Hint "The files are installed but the installed build did not self-verify. Re-run with -Repair, or inspect $TargetInstallDir"
            }
        }

        # -- State + summary ---------------------------------------------------
        $stateObj = Get-NexusReleaseStateObject -Plan $Plan -Mode $Mode -TargetInstallDir $TargetInstallDir `
            -DataDir $DataDir -AssetName $PayloadName -AssetSha $assetSha -AssetBytes $assetItem.Length `
            -SumsDigest $sumsDigest -ManifestDigest $manifestDigest `
            -Outcome $verification -InstallState $installState -Reason $verifyReason
        Write-NexusReleaseState -StatePath $StatePath -State $stateObj

        if ($Script:_DriverMode) {
            Emit-NexusFrame @{
                ok               = $true
                action           = $action
                protocol_version = $Script:ProtocolVersionValue
                installer_version = $Script:InstallerVersion
                exit_code        = $Script:EXIT_OK
                version          = $Plan.version
                tag              = $Plan.tag
                mode             = $Mode
                payload          = $PayloadName
                payload_sha256   = $assetSha
                install_dir      = $TargetInstallDir
                data_dir         = $DataDir
                state_file       = $StatePath
                verification     = $verification
                install_state    = $installState
                reason           = $verifyReason
            }
        } else {
            Write-Host ""
            Write-Host "  Nexus Scalp Engine $action" -ForegroundColor Green
            Write-Host "    Version:    $Plan.version ($Mode)" -ForegroundColor Gray
            Write-Host "    Installed:  $TargetInstallDir" -ForegroundColor Gray
            Write-Host "    Data:       $DataDir (config/logs/data/models - preserved on upgrade, repair and uninstall)" -ForegroundColor Gray
            Write-Host "    State:      $StatePath" -ForegroundColor Gray
            if ($installState -eq "AVAILABLE") {
                Write-Host "    Verified:   version + verify-release passed" -ForegroundColor Gray
            } else {
                Write-Host "    Verified:   SKIPPED ($verifyReason)" -ForegroundColor Yellow
            }
            Write-Host ""
            Write-Host "  Next: start NexusTraderBot from the Start Menu, or run:" -ForegroundColor Cyan
            Write-Host "    `"$TargetInstallDir\NexusScalpEngine.exe`" doctor" -ForegroundColor White
            Write-Host ""
        }
        Write-NexusLog ("RUN end action={0} version={1} install_state={2}" -f $action, $Plan.version, $installState)
        exit 0
    } finally {
        Release-NexusReleaseInstallLock
        # Staging is ours; the payload is verified and already installed.
        try {
            if (Test-Path -LiteralPath $StagingDir) {
                Remove-Item -LiteralPath $StagingDir -Recurse -Force -ErrorAction SilentlyContinue
            }
        } catch { }
    }
} catch {
    $reason = "$($_.Exception.Message)"
    Write-NexusLog ("RUN internal-failure: {0}" -f $reason)
    Exit-NexusRelease -Code $Script:EXIT_INTERNAL -Reason ("unexpected failure: {0}" -f $reason) `
        -Hint "Re-run with -ShowResolvedPaths to inspect the resolved paths, or -Json for a machine-readable frame."
}
