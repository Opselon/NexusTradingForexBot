# ============================================================================
# Nexus Scalp Engine - Windows Installer / Bootstrap Platform
# ============================================================================
# Nexus-native installation system for NexusTradingForexBot.
#
# Design goals (see docs/INSTALLER_ARCHITECTURE.md):
#   * IDEMPOTENT, SAFE, REPEATABLE, NO-ADMIN-FIRST
#   * USER-SCOPED install under %LOCALAPPDATA%\Nexus (overridable)
#   * uv-managed Python provisioning (no admin, no system pollution)
#   * Managed portable Git fallback (never touches an existing system Git)
#   * GitHub source acquisition: git SSH -> git HTTPS -> ZIP archive
#   * Commit > Tag > Branch pinning with explicit -ForceCommit downgrades
#   * Stage protocol: -Manifest / -Stage <name> / -Json / -ProtocolVersion
#   * Machine-readable JSON on stdout ONLY in protocol modes
#   * 8.3 short-path normalization for Windows profile aliases
#   * venv placement, config preservation, PATH hygiene, state file
#   * PS 5.1 + PowerShell 7 compatible (pure ASCII source, no PS7-only syntax)
#
# Usage:
#   iex (irm https://<your-nexus-domain>/installer/install.ps1)
#   .\install.ps1                        # full install
#   .\install.ps1 -Manifest              # print stage manifest JSON, exit
#   .\install.ps1 -ProtocolVersion       # print protocol version, exit
#   .\install.ps1 -Stage venv -Json      # run one stage, one JSON frame
#   .\install.ps1 -ShowResolvedPaths     # print resolved paths JSON, no mutation
#   .\install.ps1 -Ensure mt5            # lazily ensure a named dependency
#   .\install.ps1 -PostInstall           # post-install (model/MT5 checks)
#
# This is an independent implementation. It borrows architectural patterns
# (stage protocol, idempotency, safe updates) but contains no third-party code.
#
# NOTE: this file intentionally avoids:
#   * PS7-only syntax (null-conditional ?., ternary, utf8NoBOM switch values)
#   * non-ASCII characters (PS 5.1 parser + legacy codepage safety)
#   * Read-Host in any code path reached under -NonInteractive
# ============================================================================

param(
    # Reproducible install pins. Precedence: Commit > Tag > Branch.
    [string]$Branch = "main",
    [string]$Commit = "",
    [string]$Tag = "",

    # Apply -Commit even when it would roll an existing install BACKWARDS.
    # Without this, a pin that is already an ancestor of HEAD is skipped so a
    # stale caller cannot silently downgrade a current checkout.
    [switch]$ForceCommit,

    # Installation roots. Precedence: explicit parameter > env var > default.
    [string]$NexusHome = "",
    [string]$InstallDir = "",

    # Python pinning (single source of truth lives below in $Script:NexusPythonVersion;
    # the parameters only override it when explicitly passed).
    [string]$PythonVersion = "",

    # venv control
    [switch]$NoVenv,

    # --- Stage protocol -------------------------------------------------------
    [switch]$Manifest,
    [string]$Stage = "",
    [switch]$ProtocolVersion,
    [switch]$NonInteractive,
    [switch]$Json,

    # Print resolved paths as JSON and exit without touching anything.
    [switch]$ShowResolvedPaths,

    # --- Dependency ensure mode (lazy tooling) --------------------------------
    [string]$Ensure = "",

    # --- Post-install mode ----------------------------------------------------
    [switch]$PostInstall,

    # Skip optional heavyweight optional stages (none mandatory in Nexus today)
    [switch]$SkipOptional
)

$ErrorActionPreference = "Stop"

# Suppress Invoke-WebRequest's per-chunk progress bar: Windows PowerShell 5.1
# repaints it synchronously on every byte and can slow downloads 10-100x.
$ProgressPreference = "SilentlyContinue"

# Display-only console UTF-8 so native tool output renders correctly; the
# script file itself stays pure ASCII for PS 5.1 parser compatibility.
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
} catch {
    # Constrained hosts disallow encoding mutation; output mojibake is
    # cosmetic-only and the install still works.
}

# ============================================================================
# Configuration (single source of truth)
# ============================================================================

# Nexus repository. Prefer HTTPS (tokenless, works everywhere); SSH is tried
# first only when the caller already has SSH keys configured.
$Script:RepoUrlHttps = "https://github.com/Opselon/NexusTradingForexBot.git"
$Script:RepoUrlSsh   = "git@github.com:Opselon/NexusTradingForexBot.git"

# Canonical Python minor version for Nexus (pyproject requires-python >= 3.11).
$Script:NexusPythonVersion = "3.11"
# Safe fallback minors the engine actually supports, in preference order.
$Script:NexusPythonFallbackVersions = @("3.12", "3.13")

# Stage-protocol version. Bumped ONLY for breaking changes to the manifest
# schema, stage-name semantics, or stdout JSON shape. Adding stages does not
# bump this - drivers iterate the manifest dynamically.
$Script:ProtocolVersionValue = 1

$Script:InstallerVersion = "1.0.0"

# ============================================================================
# 8.3 short-path normalization
# ============================================================================
# Windows generates 8.3 aliases for profile folders containing spaces, dots,
# or non-ASCII characters ("First Last" -> FIRST~1.LAS). %TEMP%, %LOCALAPPDATA%
# and everything derived from them can then be exposed in short form, which
# PowerShell's FileSystem provider mishandles in provider cmdlets. Expand every
# profile-rooted path to long form once, up front.
#
# Three resolvers, tried in order; each degrades to returning the input:
#   1. kernel32!GetLongPathNameW
#   2. Scripting.FileSystemObject (COM)
#   3. Profile-root reconstruction (alias maps to nothing on disk anymore)

$Script:LongProfileRoot = $null
$Script:LastResolver = 'none'

function Write-Diag {
    # Diagnostics go to stderr, never stdout: protocol modes hand drivers a
    # single JSON stream on stdout and stray notes would corrupt parsing.
    param([string]$Message)
    if ($ShowResolvedPaths) { return }
    try {
        [Console]::Error.WriteLine("[nexus-installer] $Message")
    } catch {
        # Some hosts cannot write stderr; diagnostics are best-effort.
    }
}

function Get-LongProfileRoot {
    if ($null -ne $Script:LongProfileRoot) { return $Script:LongProfileRoot }
    $Script:LongProfileRoot = ''

    $envProfile = [Environment]::GetEnvironmentVariable('USERPROFILE')
    $shellProfile = [Environment]::GetFolderPath('UserProfile')
    $candidates = @($envProfile, $shellProfile, "$env:HOMEDRIVE$env:HOMEPATH")
    foreach ($anchor in @($envProfile, $shellProfile)) {
        if ($anchor -and $env:USERNAME) {
            $parent = Split-Path -Parent $anchor.TrimEnd('\', '/')
            if ($parent) { $candidates += (Join-Path $parent $env:USERNAME) }
        }
    }

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        $candidate = $candidate.TrimEnd('\', '/')
        if (-not $candidate) { continue }
        if ($candidate -match '~\d') { continue }
        try {
            if (Test-Path -LiteralPath $candidate -PathType Container) {
                $Script:LongProfileRoot = $candidate
                break
            }
        } catch {
            # Unreadable candidate (denied, malformed): try the next one.
        }
    }
    return $Script:LongProfileRoot
}

function ConvertTo-LongPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $Path }
    if ($Path -notmatch '~\d') {
        $Script:LastResolver = 'skipped-long-path'
        return $Path
    }

    # 1. kernel32 (compiled on first use only)
    try {
        if (-not ([System.Management.Automation.PSTypeName]'NexusInstall.LongPath').Type) {
            Add-Type -Namespace 'NexusInstall' -Name 'LongPath' -MemberDefinition @'
[DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern int GetLongPathNameW(string lpszShortPath, System.Text.StringBuilder lpszLongPath, int cchBuffer);
'@
        }
        $buffer = New-Object System.Text.StringBuilder 4096
        $length = [NexusInstall.LongPath]::GetLongPathNameW($Path, $buffer, $buffer.Capacity)
        if ($length -gt $buffer.Capacity) {
            $buffer = New-Object System.Text.StringBuilder $length
            $length = [NexusInstall.LongPath]::GetLongPathNameW($Path, $buffer, $buffer.Capacity)
        }
        if ($length -gt 0) {
            $expanded = $buffer.ToString()
            if ($expanded -and ($expanded -notmatch '~\d')) {
                $Script:LastResolver = 'kernel32'
                return $expanded
            }
        }
    } catch {
        # Not Windows, or P/Invoke denied by policy: try the next resolver.
    }

    # 2. COM fallback. Validate the result: it can report success and still
    #    hand back the alias unchanged.
    try {
        $fso = New-Object -ComObject Scripting.FileSystemObject
        $resolved = $null
        if ($fso.FolderExists($Path))   { $resolved = $fso.GetFolder($Path).Path }
        elseif ($fso.FileExists($Path)) { $resolved = $fso.GetFile($Path).Path }
        if ($resolved -and ($resolved -notmatch '~\d')) {
            $Script:LastResolver = 'com'
            return $resolved
        }
    } catch {
        # COM unavailable / locked-down host: try profile-root rebuild.
    }

    # 3. Rebuild from a known-long profile root.
    $Script:LastResolver = 'none'
    $longRoot = Get-LongProfileRoot
    if ($longRoot) {
        $longRootParent = Split-Path -Parent $longRoot
        if ($longRootParent) {
            $node = $Path
            $tail = ''
            while ($node -and ($node -match '~\d')) {
                $leaf = Split-Path -Leaf $node
                $parent = Split-Path -Parent $node
                if (-not $parent) { break }
                if ($leaf -match '~\d') {
                    if ($parent -eq $longRootParent) {
                        $rebuilt = if ($tail) { Join-Path $longRoot $tail } else { $longRoot }
                        if ($rebuilt -and ($rebuilt -notmatch '~\d')) {
                            $Script:LastResolver = 'profile-root'
                            return $rebuilt
                        }
                    }
                    break
                }
                $tail = if ($tail) { Join-Path $leaf $tail } else { $leaf }
                $node = $parent
            }
        }
    }
    return $Path
}

function Set-LongProfileEnvVars {
    $rewrote = @{}
    foreach ($name in @('TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE')) {
        $current = [Environment]::GetEnvironmentVariable($name)
        if (-not $current) { continue }
        $expanded = ConvertTo-LongPath $current
        if ($expanded -and ($expanded -ne $current)) {
            Set-Item -Path "Env:$name" -Value $expanded
            $rewrote[$name] = $expanded
            Write-Diag "expanded 8.3 short path in %$name%: $current -> $expanded"
        }
    }
    return $rewrote
}

$Script:NormalizedProfilePaths = Set-LongProfileEnvVars

# Re-derive install roots now that the env vars behind their defaults are long.
# An explicitly passed -NexusHome / -InstallDir is normalized in place, never
# replaced by a default.
if (-not $NexusHome) {
    if ($env:NEXUS_HOME) { $NexusHome = $env:NEXUS_HOME }
    else { $NexusHome = Join-Path $env:LOCALAPPDATA "Nexus" }
}
if (-not $InstallDir) {
    if ($env:NEXUS_HOME) { $InstallDir = Join-Path $env:NEXUS_HOME "engine" }
    else { $InstallDir = Join-Path $env:LOCALAPPDATA "Nexus\engine" }
}
$NexusHome = ConvertTo-LongPath $NexusHome
$InstallDir = ConvertTo-LongPath $InstallDir

# Canonical Python version resolution: explicit -PythonVersion wins, then the
# script constant. Single source of truth shared by all stages.
if (-not $PythonVersion) { $PythonVersion = $Script:NexusPythonVersion }

$Script:ResolvedPathReport = [ordered]@{
    nexus_home        = $NexusHome
    install_dir       = $InstallDir
    temp_dir          = $env:TEMP
    profile_root      = (Get-LongProfileRoot)
    path_normalized   = ($Script:NormalizedProfilePaths.Count -gt 0)
    normalizer        = $Script:LastResolver
    installer_version = $Script:InstallerVersion
    protocol_version  = $Script:ProtocolVersionValue
}

# ============================================================================
# Helper functions
# ============================================================================

# Human-facing output helpers. In protocol/driver mode (-Json / -Stage) every
# one of these reroutes to STDERR so stdout carries ONLY the documented JSON
# frames - the "JSON stdout discipline" contract. Under `irm | iex` and plain
# interactive runs they print to the console as usual.
function Write-Info {
    param([string]$m)
    if ($Script:_DriverMode) { Write-Diag $m; return }
    Write-Host "-> $m" -ForegroundColor Cyan
}
function Write-Success {
    param([string]$m)
    if ($Script:_DriverMode) { Write-Diag $m; return }
    Write-Host "[OK] $m" -ForegroundColor Green
}
function Write-WarnMsg {
    param([string]$m)
    if ($Script:_DriverMode) { Write-Diag "WARN: $m"; return }
    Write-Host "[!] $m" -ForegroundColor Yellow
}
function Write-ErrMsg  {
    param([string]$m)
    if ($Script:_DriverMode) { Write-Diag "ERROR: $m"; return }
    Write-Host "[X] $m" -ForegroundColor Red
}

function Invoke-NativeWithRelaxedErrorAction {
    # Run a scriptblock with ErrorActionPreference=Continue so native-command
    # stderr lines are not wrapped as terminating ErrorRecords under the
    # script-global EAP=Stop. Callers must check $LASTEXITCODE / artifacts.
    param([scriptblock]$Script)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Script
    } finally {
        $ErrorActionPreference = $prevEAP
    }
}

function Test-IsWindows {
    return ($env:OS -eq "Windows_NT")
}

function Get-WindowsArch {
    # Real OS architecture, invariant to WoW64/Prism emulation:
    # Win32_Processor.Architecture 0=x86, 5=ARM, 9=x64, 12=ARM64.
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
    $envArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($envArch) {
        "ARM64" { return "arm64" }
        "AMD64" { return "x64" }
        "x86"   { return "x86" }
        default {
            if ([Environment]::Is64BitOperatingSystem) { return "x64" } else { return "x86" }
        }
    }
}

function Sync-EnvPath {
    # Re-read PATH from User + Machine registry hives into this process.
    # Stage drivers invoke stages in fresh PowerShell processes that inherit a
    # STALE parent env, so binaries installed by an earlier stage look missing.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
}

function Merge-ProcessPath {
    # Merge the current process PATH with the registry hives (and winget Links)
    # WITHOUT dropping process-only entries added earlier in this run.
    $candidates = @()
    $candidates += $env:Path
    $candidates += [Environment]::GetEnvironmentVariable("Path", "User")
    $candidates += [Environment]::GetEnvironmentVariable("Path", "Machine")
    $wingetLinks = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"
    if (Test-Path $wingetLinks) { $candidates += $wingetLinks }
    $seen = New-Object System.Collections.Generic.HashSet[string] ([StringComparer]::OrdinalIgnoreCase)
    $ordered = New-Object System.Collections.Generic.List[string]
    foreach ($chunk in $candidates) {
        if ([string]::IsNullOrEmpty($chunk)) { continue }
        foreach ($entry in $chunk.Split(';')) {
            $trimmed = $entry.Trim()
            if ($trimmed -and $seen.Add($trimmed)) { $ordered.Add($trimmed) }
        }
    }
    $env:Path = [string]::Join(';', $ordered)
}

function Add-UserPathEntry {
    # Idempotent, order-preserving, deduplicated User PATH entry insert.
    # Existing unrelated entries (including empty segments) are preserved.
    param(
        [Parameter(Mandatory = $true)][string]$Entry,
        [switch]$ToFront
    )
    if (-not $Entry) { return }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $items = @()
    if ($userPath) { $items = @($userPath -split ';') }
    $exists = $false
    foreach ($item in $items) {
        if ($item -and ($item.Trim() -eq $Entry)) { $exists = $true; break }
    }
    if ($exists) {
        if ($ToFront) { Set-UserPathEntryFirst -Entry $Entry }
        return
    }
    $updated = if ($ToFront) { (@($Entry) + $items) -join ";" } else { ($items + @($Entry)) -join ";" }
    [Environment]::SetEnvironmentVariable("Path", $updated, "User")
    $env:Path = "$Entry;$env:Path"
}

function Set-UserPathEntryFirst {
    # Move an existing entry to the front of the persisted User PATH without
    # touching the relative order of anything else. No-op when already first.
    param([Parameter(Mandatory = $true)][string]$Entry)
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) { return }
    $items = @($userPath -split ';')
    $rest = @($items | Where-Object { $_ -ne $Entry })
    $updated = (@($Entry) + $rest) -join ";"
    if ($updated -ne $userPath) {
        [Environment]::SetEnvironmentVariable("Path", $updated, "User")
    }
}

function Test-DirectoryWritable {
    param([Parameter(Mandatory = $true)][string]$Path)
    try {
        if (-not (Test-Path -LiteralPath $Path)) {
            New-Item -ItemType Directory -Force -Path $Path -ErrorAction Stop | Out-Null
        }
        $probe = Join-Path $Path (".nexus-write-test-" + [Guid]::NewGuid().ToString("N"))
        [System.IO.File]::WriteAllText($probe, "probe")
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        return $true
    } catch {
        return $false
    }
}

function Get-FreeDiskSpaceGB {
    param([Parameter(Mandatory = $true)][string]$DriveRoot)
    try {
        $drive = Get-PSDrive -Name ($DriveRoot.Substring(0, 1)) -ErrorAction Stop
        return [math]::Round($drive.Free / 1GB, 1)
    } catch {
        return $null
    }
}

function ConvertTo-PlainText {
    # Flatten any object stream (including ErrorRecords from native stderr)
    # into diagnostic text safe to embed in logs/reasons.
    param($InputObject)
    if ($null -eq $InputObject) { return "" }
    $text = ($InputObject | ForEach-Object { "$_" }) -join "`n"
    return ([string]$text).Trim()
}

# ============================================================================
# Log file (bounded, no secrets)
# ============================================================================

$Script:LogFile = $null

function Initialize-InstallerLog {
    try {
        $logDir = Join-Path $NexusHome "logs"
        if (-not (Test-DirectoryWritable $logDir)) { return }
        $Script:LogFile = Join-Path $logDir "installer.log"
        # Bound the log at ~1MB by keeping the newest 500 lines.
        if ((Test-Path $Script:LogFile) -and ((Get-Item $Script:LogFile).Length -gt 1MB)) {
            $tail = Get-Content $Script:LogFile -Tail 500 -ErrorAction SilentlyContinue
            $utf8NoBom = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllLines($Script:LogFile, $tail, $utf8NoBom)
        }
    } catch {
        $Script:LogFile = $null
    }
}

function Write-Log {
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

# ============================================================================
# Download helpers (timeout, retry, integrity, zip-slip-safe extraction)
# ============================================================================

function Invoke-NexusDownload {
    # Bounded, retrying download with exponential backoff + jitter.
    # Returns the local file path on success; throws on failure.
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination,
        [int]$MaxAttempts = 3,
        [int]$TimeoutSec = 300
    )
    $attempt = 0
    $lastError = $null
    while ($attempt -lt $MaxAttempts) {
        $attempt++
        try {
            $partial = "$Destination.partial-$PID"
            if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue }
            # Download to a .partial file, then atomically move on success so an
            # interrupted download can never be mistaken for a complete artifact.
            $job = $null
            if ($PSVersionTable.PSVersion.Major -ge 7) {
                Invoke-WebRequest -Uri $Url -OutFile $partial -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
            } else {
                # PS 5.1 Invoke-WebRequest has no -TimeoutSec; run under a job
                # with a hard wall-clock ceiling instead of hanging forever.
                $job = Start-Job -ScriptBlock {
                    param($u, $o)
                    $ProgressPreference = "SilentlyContinue"
                    Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing
                } -ArgumentList $Url, $partial
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
            $size = (Get-Item -LiteralPath $partial).Length
            if ($size -le 0) { throw "downloaded file is empty" }
            Move-Item -LiteralPath $partial -Destination $Destination -Force
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
            if ($attempt -lt $MaxAttempts) {
                # Exponential backoff + jitter: 1s, 2s, 4s... +/- 0.5s
                $sleepSec = [math]::Pow(2, $attempt - 1) + (Get-Random -Minimum 0 -Maximum 500) / 1000.0
                Start-Sleep -Seconds ([math]::Min($sleepSec, 15))
            }
        }
    }
    throw "Download failed after $MaxAttempts attempts ($Url): $lastError"
}

function Expand-NexusZipSafe {
    # Zip-slip-safe extraction: every entry's resolved destination path must
    # remain inside $Destination. Blocks absolute paths, drive-qualified paths,
    # UNC paths, and ../ traversal. Rejects entry names that could escape.
    param(
        [Parameter(Mandatory = $true)][string]$ZipPath,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if (-not (Test-Path -LiteralPath $ZipPath)) { throw "ZIP not found: $ZipPath" }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $destRoot = [System.IO.Path]::GetFullPath(($Destination + "\"))
    $destRootUpper = $destRoot.ToUpperInvariant()

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $ZipPath).ProviderPath)
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName
            if ([string]::IsNullOrEmpty($name)) { continue }
            # Absolute, drive-qualified, UNC, or traversal in the raw name.
            if ($name -match '^([A-Za-z]:|\\\\|/)') {
                throw "ZIP entry has absolute/UNC/drive path (zip-slip blocked): $name"
            }
            if ($name -match '(\.\.|//)') {
                throw "ZIP entry contains traversal (zip-slip blocked): $name"
            }
            # Normalize and re-verify the final target stays inside the root.
            $target = [System.IO.Path]::GetFullPath((Join-Path $destRoot $name))
            if (-not $target.ToUpperInvariant().StartsWith($destRootUpper)) {
                throw "ZIP entry escapes destination (zip-slip blocked): $name"
            }
        }
        # All entries validated: extract with the framework extractor.
        Expand-Archive -LiteralPath $ZipPath -DestinationPath $Destination -Force
    } finally {
        $zip.Dispose()
    }
}

# ============================================================================
# uv provisioning
# ============================================================================

$Script:UvCmd = $null

function Get-PowerShellHostExe {
    # Resolve the PowerShell host executable for spawning child PS processes
    # without assuming `powershell` is on PATH (pwsh-only setups).
    try {
        $hostExe = (Get-Process -Id $PID).Path
        if ($hostExe -and (Test-Path $hostExe)) {
            $leaf = Split-Path $hostExe -Leaf
            if ($leaf -match '^(?i:powershell|pwsh)\.exe$') { return $hostExe }
        }
    } catch { }
    foreach ($candidate in @("powershell", "pwsh")) {
        $cmd = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd -and $cmd.Source) { return $cmd.Source }
    }
    return "powershell"
}

function Install-Uv {
    # Nexus owns its uv at %NexusHome%\bin\uv.exe. Always install there.
    $managedUv = Join-Path $NexusHome "bin\uv.exe"

    if (Test-Path $managedUv) {
        $Script:UvCmd = $managedUv
        try {
            $version = & $managedUv --version 2>$null
            Write-Success "Managed uv found ($version)"
            return $true
        } catch {
            Write-WarnMsg "uv at $managedUv is present but not runnable; reinstalling"
        }
    }

    Write-Info "Installing managed uv into $NexusHome\bin ..."
    New-Item -ItemType Directory -Path (Join-Path $NexusHome "bin") -Force | Out-Null

    $prevEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $env:UV_INSTALL_DIR = Join-Path $NexusHome "bin"
        $psHostExe = Get-PowerShellHostExe
        $installerOutput = @()

        # Rung 1: astral.sh. Rung 2: GitHub releases mirror (proxies/AV often
        # block astral.sh but pass github.com).
        $astralOut = @()
        & $psHostExe -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex" 2>&1 | Tee-Object -Variable astralOut | Out-Null
        $installerOutput += "--- uv installer source: astral.sh ---"
        $installerOutput += @($astralOut | ForEach-Object { "$_" })
        if (Test-Path $managedUv) {
            Write-Info "uv installer succeeded via astral.sh"
        } else {
            Write-Info "astral.sh did not produce uv; trying GitHub releases mirror ..."
            $ghOut = @()
            & $psHostExe -ExecutionPolicy ByPass -Command "irm https://github.com/astral-sh/uv/releases/latest/download/uv-installer.ps1 | iex" 2>&1 | Tee-Object -Variable ghOut | Out-Null
            $installerOutput += "--- uv installer source: GitHub releases ---"
            $installerOutput += @($ghOut | ForEach-Object { "$_" })
        }

        $ErrorActionPreference = $prevEAP

        if (Test-Path $managedUv) {
            try {
                $null = & $managedUv --version
                $Script:UvCmd = $managedUv
                $version = & $managedUv --version 2>$null
                Write-Success "Managed uv installed ($version)"
                return $true
            } catch {
                Write-WarnMsg "uv binary present but probe failed: $($_.Exception.Message)"
            }
        }

        Write-ErrMsg "uv installed but not found at $managedUv"
        if ($installerOutput.Count -gt 0) {
            Write-Info "uv installer output (last 10 lines):"
            $installerOutput | Select-Object -Last 10 | ForEach-Object { Write-Info "  $_" }
        }
        Write-Info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        return $false
    } catch {
        if ($prevEAP) { $ErrorActionPreference = $prevEAP }
        Write-ErrMsg "Failed to install uv: $_"
        return $false
    }
}

function Resolve-UvCmd {
    # Re-discover uv without reinstalling (cross-process stage drivers run
    # each stage in a fresh PowerShell process; $Script:UvCmd does not carry
    # over). Throws a clean error when uv is genuinely unavailable.
    if ($Script:UvCmd) {
        if ($Script:UvCmd -eq "uv") {
            if (Get-Command uv -ErrorAction SilentlyContinue) { return }
        } elseif (Test-Path $Script:UvCmd) {
            return
        }
    }

    $managedUv = Join-Path $NexusHome "bin\uv.exe"
    if (Test-Path $managedUv) {
        $Script:UvCmd = $managedUv
        return
    }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $Script:UvCmd = "uv"
        return
    }
    Sync-EnvPath
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $Script:UvCmd = "uv"
        return
    }
    throw "uv is not installed. Run: install.ps1 -Stage runtime  (or install uv manually: https://docs.astral.sh/uv/getting-started/installation/)"
}

# ============================================================================
# Python provisioning
# ============================================================================

function Test-PythonStoreStub {
    # Windows Store python.exe stubs live in WindowsApps and are 0-byte
    # reparse points. Get-Command finds them; invoking them produces the
    # "Python was not found" store banner. Never trust them as interpreters.
    param([string]$Path)
    if (-not $Path) { return $true }
    if ($Path -like "*\WindowsApps\*") { return $true }
    try {
        $item = Get-Item -LiteralPath $Path -ErrorAction Stop
        if ($item.Length -eq 0) { return $true }
    } catch {
        return $true
    }
    return $false
}

function Resolve-AvailablePythonVersion {
    # First minor version uv can actually find, preferring the requested
    # version then the fallback list. Cross-process-safe counterpart to
    # Test-Python's in-process fallback.
    Resolve-UvCmd
    $candidates = @($PythonVersion) + $Script:NexusPythonFallbackVersions
    $seen = @{}
    foreach ($ver in $candidates) {
        if (-not $ver -or $seen.ContainsKey($ver)) { continue }
        $seen[$ver] = $true
        try {
            $found = & $Script:UvCmd python find $ver 2>$null
            if ($found) { return $ver }
        } catch { }
    }
    return $null
}

function Test-Python {
    Write-Info "Checking Python $PythonVersion (managed via uv)..."
    Resolve-UvCmd

    try {
        $pythonPath = & $Script:UvCmd python find $PythonVersion 2>$null
        if ($pythonPath) {
            $ver = & $pythonPath --version 2>$null
            Write-Success "Python found: $ver"
            return $true
        }
    } catch { }

    Write-Info "Python $PythonVersion not found, installing via uv (user-scoped, no admin)..."
    $prevEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $uvOutput = & $Script:UvCmd python install $PythonVersion 2>&1
        $uvExitCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP

        $pythonPath = & $Script:UvCmd python find $PythonVersion 2>$null
        if ($pythonPath) {
            $ver = & $pythonPath --version 2>$null
            Write-Success "Python installed: $ver"
            return $true
        }
        if ($uvExitCode -ne 0) {
            Write-WarnMsg "uv python install output:"
            $uvText = ConvertTo-PlainText $uvOutput
            if ($uvText) { Write-Diag $uvText }
        }
    } catch {
        if ($prevEAP) { $ErrorActionPreference = $prevEAP }
        Write-WarnMsg "uv python install error: $_"
    }

    # Fallback: any supported minor version already available to uv.
    foreach ($fallbackVer in $Script:NexusPythonFallbackVersions) {
        try {
            $pythonPath = & $Script:UvCmd python find $fallbackVer 2>$null
            if ($pythonPath) {
                $ver = & $pythonPath --version 2>$null
                Write-Success "Using fallback Python: $ver (requested $PythonVersion unavailable; explicitly reported)"
                Write-WarnMsg "Python fallback in effect: $fallbackVer (requested $PythonVersion was unavailable)"
                $Script:EffectivePythonVersion = $fallbackVer
                return $true
            }
        } catch { }
    }

    # Fallback: system python, rejecting the Microsoft Store stub and
    # unsupported versions by actually invoking the interpreter.
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd -and -not (Test-PythonStoreStub -Path $pythonCmd.Source)) {
        try {
            $prevEAP2 = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            $sysVer = & python --version 2>&1
            $ErrorActionPreference = $prevEAP2
            if ("$sysVer" -match "Python 3\.(11|12|13)") {
                Write-Success "Using system Python: $sysVer"
                return $true
            }
            Write-WarnMsg "System Python ($sysVer) does not meet the >= 3.11 requirement"
        } catch {
            if ($prevEAP2) { $ErrorActionPreference = $prevEAP2 }
        }
    }

    Write-ErrMsg "Failed to provision Python $PythonVersion"
    Write-Info "Install Python 3.11+ manually, then re-run: https://www.python.org/downloads/"
    Write-Info "  Or: winget install Python.Python.3.11"
    return $false
}

# ============================================================================
# Git provisioning
# ============================================================================

$Script:GitBashPath = $null

function Test-GitBashCompatibility {
    # Verify Git Bash can launch external MSYS programs, not just builtins.
    # Mandatory ASLR can allow bash.exe to start while every msys-2.0.dll
    # child fails during fork/spawn. Probe: run a harmless real child.
    param([Parameter(Mandatory = $true)][string]$BashPath)
    if (-not (Test-Path -LiteralPath $BashPath)) { return $false }
    try {
        $proc = Start-Process -FilePath $BashPath `
            -ArgumentList '--noprofile --norc -c "/usr/bin/true; /usr/bin/cat --version >/dev/null"' `
            -NoNewWindow -Wait -PassThru -ErrorAction Stop
        return ($proc.ExitCode -eq 0)
    } catch {
        return $false
    }
}

function Set-GitBashEnvVar {
    # Locate bash.exe and persist NEXUS_GIT_BASH_PATH (User scope).
    $Script:GitBashPath = $null
    $candidates = @()
    $candidates += "$NexusHome\git\bin\bash.exe"
    $candidates += "$NexusHome\git\usr\bin\bash.exe"
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) {
        $gitRoot = Split-Path (Split-Path $gitCmd.Source -Parent) -Parent
        $candidates += "$gitRoot\bin\bash.exe"
        $candidates += "$gitRoot\usr\bin\bash.exe"
    }
    $candidates += "${env:ProgramFiles}\Git\bin\bash.exe"
    $pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($pf86) { $candidates += "$pf86\Git\bin\bash.exe" }
    $candidates += "${env:LocalAppData}\Programs\Git\bin\bash.exe"

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            [Environment]::SetEnvironmentVariable("NEXUS_GIT_BASH_PATH", $candidate, "User")
            $env:NEXUS_GIT_BASH_PATH = $candidate
            $Script:GitBashPath = $candidate
            return
        }
    }
    Write-WarnMsg "Could not locate bash.exe - Nexus shell integration may need NEXUS_GIT_BASH_PATH set manually"
}

function Install-Git {
    # Priority: existing git on PATH -> managed PortableGit (user-scoped).
    # No admin, no winget, never touches a system Git installation.
    Write-Info "Checking Git..."
    if (Get-Command git -ErrorAction SilentlyContinue) {
        $version = & git --version 2>$null
        Write-Success "Git found ($version)"
        Set-GitBashEnvVar
        if ($Script:GitBashPath -and (Test-GitBashCompatibility -BashPath $Script:GitBashPath)) {
            Write-Success "Git Bash can launch MSYS programs"
            return $true
        }
        Write-WarnMsg "System Git Bash probe failed; trying a Nexus-managed PortableGit install instead..."
    }

    Write-Info "Downloading PortableGit to $NexusHome\git\ (no admin required)..."
    try {
        $arch = Get-WindowsArch
        $gitTag = "v2.51.0.windows.1"
        $gitVer = "2.51.0"
        if ($arch -eq "arm64") {
            $assetName = "PortableGit-$gitVer-arm64.7z.exe"
        } elseif ($arch -eq "x64") {
            $assetName = "PortableGit-$gitVer-64-bit.7z.exe"
        } else {
            # PortableGit is 64-bit only; MinGit 32-bit as a last resort.
            $assetName = "MinGit-$gitVer-32-bit.zip"
        }
        $downloadUrl = "https://github.com/git-for-windows/git/releases/download/$gitTag/$assetName"
        $tmpFile = Join-Path $env:TEMP ("nexus-git-" + [Guid]::NewGuid().ToString("N") + "-" + $assetName)
        $gitDir = Join-Path $NexusHome "git"

        Invoke-NexusDownload -Url $downloadUrl -Destination $tmpFile -TimeoutSec 600

        New-Item -ItemType Directory -Path $gitDir -Force | Out-Null
        if ($assetName -like "*.zip") {
            Expand-NexusZipSafe -ZipPath $tmpFile -Destination $gitDir
        } else {
            # PortableGit is a self-extracting 7z archive: extract silently.
            Write-Info "Extracting PortableGit to $gitDir ..."
            $extractProc = Start-Process -FilePath $tmpFile `
                -ArgumentList "-o`"$gitDir`"", "-y" `
                -NoNewWindow -Wait -PassThru
            if ($extractProc.ExitCode -ne 0) {
                throw "PortableGit extraction failed (exit code $($extractProc.ExitCode))"
            }
        }
        Remove-Item -LiteralPath $tmpFile -Force -ErrorAction SilentlyContinue

        $gitExe = Join-Path $gitDir "cmd\git.exe"
        if (-not (Test-Path $gitExe)) { throw "Git extraction did not produce git.exe at $gitExe" }

        $env:Path = "$gitDir\cmd;$env:Path"
        $newPathEntries = @("$gitDir\cmd", "$gitDir\bin", "$gitDir\usr\bin")
        foreach ($entry in $newPathEntries) { Add-UserPathEntry -Entry $entry }

        $version = & $gitExe --version 2>$null
        Write-Success "Git $version installed to $gitDir (portable, user-scoped)"
        Set-GitBashEnvVar
        if (-not $Script:GitBashPath) { throw "PortableGit extraction did not produce a usable bash.exe" }
        if (-not (Test-GitBashCompatibility -BashPath $Script:GitBashPath)) {
            throw "Git Bash at $Script:GitBashPath cannot launch MSYS programs (possible Mandatory ASLR policy)"
        }
        return $true
    } catch {
        Write-ErrMsg "Could not install portable Git: $_"
        Write-Info "Fallback: install Git manually from https://git-scm.com/download/win then re-run."
        return $false
    }
}

# ============================================================================
# Repository acquisition
# ============================================================================

function Test-NexusRepoValid {
    # A directory is a usable Nexus checkout only when git itself confirms:
    # work tree + resolvable HEAD. Broken stubs are re-acquired fresh.
    param([string]$Repo)
    if (-not $Repo) { return $false }
    if (-not (Test-Path (Join-Path $Repo ".git"))) { return $false }
    $prevEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        Push-Location $Repo
        try {
            $global:LASTEXITCODE = 0
            $null = & git rev-parse --is-inside-work-tree 2>&1
            $revParseOk = ($LASTEXITCODE -eq 0)
            $global:LASTEXITCODE = 0
            $null = & git rev-parse --verify HEAD 2>&1
            $hasCommit = ($LASTEXITCODE -eq 0)
            return ($revParseOk -and $hasCommit)
        } finally {
            Pop-Location
            $ErrorActionPreference = $prevEAP
        }
    } catch {
        return $false
    }
}

function Get-RepoHeadSha {
    param([string]$Repo)
    try {
        Push-Location $Repo
        try {
            $ErrorActionPreference = "Continue"
            $global:LASTEXITCODE = 0
            $sha = & git rev-parse HEAD 2>$null
            if ($LASTEXITCODE -eq 0 -and $sha) { return ("$sha").Trim() }
            return $null
        } finally {
            Pop-Location
        }
    } catch {
        return $null
    }
}

function Invoke-RepoUpdate {
    # Update an existing valid checkout in place, preserving local changes:
    #   - stash (including untracked) before mutating operations
    #   - Commit > Tag > Branch precedence with downgrade guard
    #   - fast-forward preferred; diverged managed checkout resets to origin
    #     ONLY after the local work is safely stashed (never silently lost)
    Write-Info "Existing installation found, updating..."
    $autostashRef = ""
    $stashCreated = $false
    # Protocol isolation: caller GIT_* env vars (GIT_DIR/GIT_INDEX_FILE/
    # GIT_WORK_TREE/...) redirect every child git command at the CALLER's
    # repository. Clear them for the duration and restore on exit.
    $savedGitEnv = @{}
    foreach ($gitVar in @('GIT_DIR', 'GIT_INDEX_FILE', 'GIT_WORK_TREE', 'GIT_OBJECT_DIRECTORY', 'GIT_COMMON_DIR')) {
        $savedGitEnv[$gitVar] = [Environment]::GetEnvironmentVariable($gitVar)
        if ($savedGitEnv[$gitVar]) { Remove-Item "Env:$gitVar" -ErrorAction SilentlyContinue }
    }
    try {
        Push-Location $InstallDir
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            # Managed checkout hygiene: never let autocrlf fabricate dirt.
            & git config core.autocrlf false 2>$null

            # Preserve any real local changes before the update.
            $statusOut = & git status --porcelain 2>$null
            if (-not [string]::IsNullOrWhiteSpace(($statusOut -join "`n"))) {
                $unmergedOut = & git ls-files --unmerged 2>$null
                if (-not [string]::IsNullOrWhiteSpace(($unmergedOut -join "`n"))) {
                    Write-Info "Clearing unmerged index entries from a previous conflict..."
                    & git reset -q 2>$null
                }
                $stashName = "nexus-install-autostash-" + (Get-Date -Format "yyyyMMdd-HHmmss")
                Write-Info "Local changes detected, stashing before update..."
                & git stash push --include-untracked -m $stashName 2>$null
                if ($LASTEXITCODE -eq 0) {
                    $autostashRef = "stash@{0}"
                    $stashCreated = $true
                }
            }

            & git fetch origin 2>$null
            if ($LASTEXITCODE -ne 0) { throw "git fetch failed (exit $LASTEXITCODE)" }

            if ($Commit) {
                $pinnedSha = & git rev-parse "$Commit^{commit}" 2>$null
                if ($LASTEXITCODE -ne 0) {
                    & git fetch origin $Commit 2>$null
                    $pinnedSha = & git rev-parse "$Commit^{commit}" 2>$null
                    if ($LASTEXITCODE -ne 0) { throw "commit $Commit not found on origin" }
                }
                $headSha = & git rev-parse HEAD 2>$null
                if (-not $ForceCommit) {
                    & git merge-base --is-ancestor $Commit HEAD 2>$null
                    $isAncestor = ($LASTEXITCODE -eq 0)
                    if ($isAncestor -and $headSha -and $pinnedSha -and ($pinnedSha -ne $headSha)) {
                        Write-WarnMsg "Ignoring -Commit ${Commit}: the checkout is already newer. Pass -ForceCommit to override."
                        return
                    }
                }
                Write-Info "Pinning to commit $Commit..."
                & git checkout --detach $Commit 2>$null
                if ($LASTEXITCODE -ne 0) { throw "git checkout $Commit failed (exit $LASTEXITCODE)" }
            } elseif ($Tag) {
                & git fetch origin "refs/tags/${Tag}:refs/tags/${Tag}" 2>$null
                if ($LASTEXITCODE -ne 0) { throw "git fetch tag $Tag failed (exit $LASTEXITCODE)" }
                Write-Info "Checking out tag $Tag..."
                & git checkout --detach "refs/tags/$Tag" 2>$null
                if ($LASTEXITCODE -ne 0) { throw "git checkout tag $Tag failed (exit $LASTEXITCODE)" }
            } else {
                & git checkout $Branch 2>$null
                if ($LASTEXITCODE -ne 0) { throw "git checkout $Branch failed (exit $LASTEXITCODE)" }
                & git pull --ff-only origin $Branch 2>$null
                if ($LASTEXITCODE -ne 0) {
                    # Managed checkout: divergence blocks ff-only. Local work is
                    # already stashed, so resetting to origin loses nothing.
                    Write-WarnMsg "Fast-forward not possible; resetting managed install to origin/$Branch (local changes were stashed)..."
                    & git reset --hard "origin/$Branch" 2>$null
                    if ($LASTEXITCODE -ne 0) { throw "git reset --hard origin/$Branch failed (exit $LASTEXITCODE)" }
                }
            }
        } finally {
            $ErrorActionPreference = $prevEAP
        }
    } finally {
        Pop-Location
        if ($stashCreated) {
            try {
                Push-Location $InstallDir
                $prevEAP3 = $ErrorActionPreference
                $ErrorActionPreference = "Continue"
                try {
                    $restoreOut = & git stash apply $autostashRef 2>&1
                    $restoreExit = $LASTEXITCODE
                    $conflicted = @(& git diff --name-only --diff-filter=U 2>$null | Where-Object { $_ -and "$_".Trim() })
                    if (($restoreExit -eq 0) -and ($conflicted.Count -eq 0)) {
                        & git stash drop $autostashRef 2>$null
                        Write-WarnMsg "Local changes were restored on top of the updated codebase (review git status if behavior surprises)."
                    } else {
                        Write-ErrMsg "Update succeeded but restoring local changes hit conflicts. Your stashed changes are PRESERVED."
                        foreach ($file in $conflicted) { Write-Diag "conflicted: $file" }
                        Write-Info "Restore later with: git -C `"$InstallDir`" stash apply $autostashRef"
                    }
                } finally {
                    $ErrorActionPreference = $prevEAP3
                    Pop-Location
                }
            } catch {
                Write-WarnMsg "Stash restore attempt failed; stash remains at $autostashRef (nothing lost)."
            }
        }
    }
}

function Install-RepoFromZip {
    # ZIP fallback: download the ref's archive, validate structure, extract
    # zip-slip-safely to a temp dir, then atomically move into place. Initialize
    # git metadata so future updates still work.
    param([string]$RefKind, [string]$RefValue)
    Write-WarnMsg "Git clone failed - downloading ZIP archive instead ($RefKind $RefValue)..."

    $zipUrl = if ($RefKind -eq "commit") {
        "$Script:RepoUrlHttps".Replace(".git", "") + "/archive/$RefValue.zip"
    } elseif ($RefKind -eq "tag") {
        "$Script:RepoUrlHttps".Replace(".git", "") + "/archive/refs/tags/$RefValue.zip"
    } else {
        "$Script:RepoUrlHttps".Replace(".git", "") + "/archive/refs/heads/$RefValue.zip"
    }

    $session = [Guid]::NewGuid().ToString("N")
    $zipPath = Join-Path $env:TEMP ("nexus-repo-$session.zip")
    $extractPath = Join-Path $env:TEMP ("nexus-repo-extract-$session")

    try {
        Invoke-NexusDownload -Url $zipUrl -Destination $zipPath -TimeoutSec 600
        if (Test-Path $extractPath) { Remove-Item -Recurse -Force $extractPath -ErrorAction SilentlyContinue }
        Expand-NexusZipSafe -ZipPath $zipPath -Destination $extractPath

        # GitHub ZIPs extract to <repo>-<ref>/ subdirectory.
        $extractedDir = Get-ChildItem $extractPath -Directory | Select-Object -First 1
        if (-not $extractedDir) { throw "ZIP archive did not contain the expected repository directory" }

        # Validate expected repository structure before presenting it as valid.
        $expectedMarkers = @("pyproject.toml")
        foreach ($marker in $expectedMarkers) {
            if (-not (Test-Path (Join-Path $extractedDir.FullName $marker))) {
                throw "ZIP archive missing expected repository file: $marker"
            }
        }

        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $InstallDir) -ErrorAction SilentlyContinue | Out-Null
        if (Test-Path $InstallDir) {
            # Park the broken/partial existing dir rather than deleting it.
            $broken = "$InstallDir.broken-" + (Get-Date -Format "yyyyMMdd-HHmmss")
            Move-Item -LiteralPath $InstallDir -Destination $broken -Force
            Write-WarnMsg "Existing directory moved aside to $broken"
        }
        Move-Item -LiteralPath $extractedDir.FullName -Destination $InstallDir -Force

        # Initialize git metadata so future update runs work.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            Push-Location $InstallDir
            try {
                & git init 2>$null
                & git config core.autocrlf false 2>$null
                & git remote add origin $Script:RepoUrlHttps 2>$null
                $fetchRef = if ($RefKind -eq "commit") { $RefValue } elseif ($RefKind -eq "tag") { "refs/tags/$RefValue" } else { $RefValue }
                & git fetch --depth 1 origin $fetchRef 2>$null
                if ($LASTEXITCODE -eq 0) {
                    if ($RefKind -in @("commit", "tag")) {
                        & git checkout -f --detach FETCH_HEAD 2>$null
                    } else {
                        & git checkout -f -B $RefValue FETCH_HEAD 2>$null
                    }
                    if ($LASTEXITCODE -eq 0) { Write-Success "ZIP checkout pinned to $fetchRef" }
                    else { Write-WarnMsg "ZIP extracted but git checkout failed - code is in place, update tracking degraded" }
                } else {
                    Write-WarnMsg "ZIP extracted but git fetch failed - code is in place, update tracking degraded"
                }
            } finally {
                Pop-Location
            }
        } finally {
            $ErrorActionPreference = $prevEAP
        }
        Write-Success "Repository acquired via ZIP archive"
        return $true
    } catch {
        Write-ErrMsg "ZIP acquisition failed: $_"
        if (Test-Path $extractPath) { Remove-Item -Recurse -Force $extractPath -ErrorAction SilentlyContinue }
        if (Test-Path $zipPath) { Remove-Item -Force $zipPath -ErrorAction SilentlyContinue }
        return $false
    }
}

function Install-Repository {
    Write-Info "Installing Nexus engine to $InstallDir..."

    $didUpdate = $false
    if (Test-Path $InstallDir) {
        if (Test-NexusRepoValid -Repo $InstallDir) {
            Invoke-RepoUpdate
            $didUpdate = $true
        } else {
            # Not a usable repo (interrupted clone, .git stub). Move aside,
            # never destroy - then fall through to a fresh acquisition.
            $backupDir = "$InstallDir.broken-" + (Get-Date -Format "yyyyMMdd-HHmmss")
            Write-WarnMsg "Existing directory at $InstallDir is not a valid git repo; moving aside to $backupDir"
            try {
                Move-Item -LiteralPath $InstallDir -Destination $backupDir -ErrorAction Stop
            } catch {
                throw "Could not move $InstallDir aside: $($_.Exception.Message). Close programs using the directory and retry."
            }
        }
    }

    if (-not $didUpdate) {
        $cloneSuccess = $false

        # CRITICAL (protocol isolation): capture the CALLER's repo environment.
        # Under irm|iex or -File inside a git checkout, $env:GIT_DIR /
        # GIT_INDEX_FILE / GIT_WORK_TREE leak from the caller's shell and
        # redirect EVERY child git command at the CALLER's repository - here
        # they turned `git status` inside our PowerShell test-runner session
        # into the caller repo's status. Clear them for the duration and
        # restore on exit so the installer's git operations always target the
        # Nexus install tree, never the caller's repo.
        $savedGitEnv = @{}
        foreach ($gitVar in @('GIT_DIR', 'GIT_INDEX_FILE', 'GIT_WORK_TREE', 'GIT_OBJECT_DIRECTORY', 'GIT_COMMON_DIR')) {
            $savedGitEnv[$gitVar] = [Environment]::GetEnvironmentVariable($gitVar)
            if ($savedGitEnv[$gitVar]) { Remove-Item "Env:$gitVar" -ErrorAction SilentlyContinue }
        }

        # Git for Windows atomic-write fix (AV / OneDrive / NTFS filter drivers).
        $env:GIT_CONFIG_COUNT = "1"
        $env:GIT_CONFIG_KEY_0 = "windows.appendAtomically"
        $env:GIT_CONFIG_VALUE_0 = "false"

        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            # 1. SSH (only useful when the caller has keys configured).
            Write-Info "Trying SSH clone..."
            $env:GIT_SSH_COMMAND = "ssh -o BatchMode=yes -o ConnectTimeout=5"
            & git clone --depth 1 $Script:RepoUrlSsh $InstallDir 2>$null
            if ($LASTEXITCODE -eq 0) { $cloneSuccess = $true }
            $env:GIT_SSH_COMMAND = $null

            # 2. HTTPS.
            if (-not $cloneSuccess) {
                if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
                Write-Info "SSH failed, trying HTTPS clone..."
                & git clone --depth 1 $Script:RepoUrlHttps $InstallDir 2>$null
                if ($LASTEXITCODE -eq 0) { $cloneSuccess = $true }
            }

            # 3. ZIP archive.
            if (-not $cloneSuccess) {
                if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
                $refKind = if ($Commit) { "commit" } elseif ($Tag) { "tag" } else { "branch" }
                $refValue = if ($Commit) { $Commit } elseif ($Tag) { $Tag } else { $Branch }
                $ok = Install-RepoFromZip -RefKind $refKind -RefValue $refValue
                if ($ok) { $cloneSuccess = $true }
            }
        } finally {
            $ErrorActionPreference = $prevEAP
            foreach ($gitVar in $savedGitEnv.Keys) {
                if ($savedGitEnv[$gitVar]) { [Environment]::SetEnvironmentVariable($gitVar, $savedGitEnv[$gitVar]) }
            }
        }

        if (-not $cloneSuccess) {
            throw "Failed to acquire the Nexus repository (tried git SSH, git HTTPS, and ZIP archive). Check network/proxy and retry."
        }
    }

    # Post-acquisition: honour Commit/Tag pins on fresh clones (update path
    # already routed through precedence) and verify HEAD matches a commit pin.
    if (-not $didUpdate) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            if ($Commit) {
                Write-Info "Pinning fresh clone to commit $Commit..."
                & git fetch origin $Commit 2>$null
                & git checkout --detach $Commit 2>$null
                if ($LASTEXITCODE -ne 0) { throw "git checkout $Commit failed (exit $LASTEXITCODE)" }
            } elseif ($Tag) {
                Write-Info "Pinning fresh clone to tag $Tag..."
                & git fetch origin "refs/tags/${Tag}:refs/tags/${Tag}" 2>$null
                & git checkout --detach "refs/tags/$Tag" 2>$null
                if ($LASTEXITCODE -ne 0) { throw "git checkout tag $Tag failed (exit $LASTEXITCODE)" }
            } else {
                & git checkout $Branch 2>$null
                if ($LASTEXITCODE -eq 0) {
                    # Ensure the local branch tracks origin for future updates.
                    & git branch --set-upstream-to=origin/$Branch $Branch 2>$null
                }
            }
            if ($Commit) {
                $head = Get-RepoHeadSha -Repo $InstallDir
                $expected = (& git rev-parse "$Commit^{commit}" 2>$null)
                if ($LASTEXITCODE -eq 0 -and $head -and $expected -and ("$head" -ne "$expected")) {
                    throw "Repository integrity: HEAD ($head) != requested commit ($expected)"
                }
            }
        } finally {
            $ErrorActionPreference = $prevEAP
        }
    }

    Write-Success "Repository ready"
}

# ============================================================================
# venv
# ============================================================================

function Get-VenvPython {
    # The venv lives OUTSIDE the repository working tree so repo-relative
    # cleanup operations can never destroy it (application code may delete
    # files inside its own tree).
    if ($NoVenv) { return $null }
    return (Join-Path $NexusHome "venv\Scripts\python.exe")
}

function Test-VenvHealthy {
    param([string]$VenvPython)
    if (-not $VenvPython -or -not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) { return $false }
    try {
        $ErrorActionPreference = "Continue"
        $ver = & $VenvPython --version 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        if ("$ver" -notmatch "Python 3\.(11|12|13)") { return $false }
        # site-packages accessibility: import the stdlib machinery itself.
        & $VenvPython -c "import site, sys; assert site.getsitepackages()" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Install-Venv {
    if ($NoVenv) {
        Write-Info "Skipping virtual environment (-NoVenv)"
        return
    }
    Resolve-UvCmd

    # Re-resolve the interpreter cross-process-safely; the python stage's
    # fallback choice does not survive into a fresh stage process.
    $resolved = Resolve-AvailablePythonVersion
    if ($resolved -and ($resolved -ne $PythonVersion)) {
        Write-Info "Python $PythonVersion not available; using detected Python $resolved"
        $Script:EffectivePythonVersion = $resolved
        $PythonVersion = $resolved
    }

    $venvDir = Join-Path $NexusHome "venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"

    if (Test-VenvHealthy -VenvPython $venvPython) {
        Write-Success "Virtual environment already healthy at $venvDir"
        $env:VIRTUAL_ENV = $venvDir
        $env:UV_PYTHON = $venvPython
        return
    }

    Write-Info "Creating virtual environment at $venvDir (Python $PythonVersion)..."

    # Transactional recreate: park the old venv, build the replacement, and
    # only delete the parked tree once the new one is verified. A locked old
    # venv (running engine) parks under a new name instead of failing.
    $venvParked = $false
    $venvBackupName = $null
    if (Test-Path -LiteralPath $venvDir) {
        $venvBackupName = "venv.stale.{0}-{1}" -f (Get-Date -Format "yyyyMMddHHmmss"), ([Guid]::NewGuid().ToString("N"))
        try {
            Rename-Item -LiteralPath $venvDir -NewName $venvBackupName -ErrorAction Stop
            $venvParked = $true
            Write-Info "Previous venv parked at $venvBackupName"
        } catch {
            throw "Could not move the existing venv aside ($($_.Exception.Message)). A running Nexus process is using it - stop the engine and retry."
        }
    }

    try {
        Invoke-NativeWithRelaxedErrorAction { & $Script:UvCmd venv $venvDir --python $PythonVersion --seed }
        $venvExit = $LASTEXITCODE
        if ($venvExit -ne 0) { throw "uv venv failed (exit $venvExit)" }

        if (-not (Test-VenvHealthy -VenvPython $venvPython)) {
            throw "venv was created but failed health verification (interpreter/site-packages)"
        }
        Write-Success "Virtual environment ready (Python $PythonVersion) at $venvDir"

        $env:VIRTUAL_ENV = $venvDir
        $env:UV_PYTHON = $venvPython
    } catch {
        # Rollback: park the failed replacement, restore the previous venv.
        if ($venvParked) {
            try {
                if (Test-Path -LiteralPath $venvDir) {
                    $failedName = "venv.failed.{0}-{1}" -f (Get-Date -Format "yyyyMMddHHmmss"), ([Guid]::NewGuid().ToString("N"))
                    Rename-Item -LiteralPath $venvDir -NewName $failedName -ErrorAction Stop
                    Write-WarnMsg "Failed replacement parked at $failedName"
                }
                Rename-Item -LiteralPath (Join-Path $NexusHome $venvBackupName) -NewName "venv" -ErrorAction Stop
                Write-WarnMsg "Restored previous virtual environment after failed recreate"
            } catch {
                Write-ErrMsg "Rollback failed: $($_.Exception.Message). Previous venv remains at $venvBackupName"
            }
        }
        throw
    }

    # Commit the transaction: clean parked trees whose handles were released.
    if ($venvParked) {
        Remove-Item -LiteralPath (Join-Path $NexusHome $venvBackupName) -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath (Join-Path $NexusHome $venvBackupName)) {
            Write-WarnMsg "Old venv still held by a process; parked at $venvBackupName (will be cleaned by a later install)"
        }
    }
    # Clean stale parked venvs from earlier runs (older than 10 minutes).
    Get-ChildItem $NexusHome -Directory -Filter "venv.stale.*" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddMinutes(-10) } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

# ============================================================================
# Python dependencies (pyproject.toml is the single source of truth)
# ============================================================================

function Install-PythonDependencies {
    if ($NoVenv) {
        Write-Info "Skipping Python dependencies (-NoVenv)"
        return
    }
    Resolve-UvCmd

    $venvPython = Get-VenvPython
    if (-not (Test-Path $venvPython)) { throw "venv python not found at $venvPython - run install.ps1 -Stage venv first" }

    Write-Info "Installing dependencies from pyproject.toml (single source of truth)..."

    Push-Location $InstallDir
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # uv pip install -e . reads pyproject dependencies + extras directly;
        # no PowerShell-side dependency list exists to drift.
        $env:VIRTUAL_ENV = Join-Path $NexusHome "venv"
        $env:UV_PYTHON = $venvPython

        & $Script:UvCmd pip install -e "." 2>&1 | ForEach-Object { "$_" } | Write-Diag
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            # Tiered fallback: [web] extra (UI server), then core-only.
            Write-WarnMsg "Core editable install failed (exit $code); trying [web] extra tier..."
            & $Script:UvCmd pip install -e ".[web]" 2>&1 | ForEach-Object { "$_" } | Write-Diag
            $code = $LASTEXITCODE
            if ($code -ne 0) {
                Write-WarnMsg "[web] tier failed (exit $code); trying core-only tier..."
                & $Script:UvCmd pip install -e . --no-deps 2>&1 | ForEach-Object { "$_" } | Write-Diag
                $code = $LASTEXITCODE
                if ($code -ne 0) { throw "Failed to install nexus-scalp-engine dependencies even at the core tier (exit $code)" }
                Write-WarnMsg "Core-only tier installed with --no-deps; run 'install.ps1 -Stage dependencies' after network issues are resolved"
            }
        }
    } finally {
        $ErrorActionPreference = $prevEAP
        Pop-Location
    }

    # Baseline import gate: probe the venv's own python for the engine's
    # critical import surface. Catches misdirected installs before first run.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $venvPython -c "import nexus_scalp; import typer, pydantic, structlog" 2>&1 | Out-Null
        $importExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    if ($importExit -ne 0) {
        throw "Baseline imports failed in the venv (nexus_scalp/typer/pydantic/structlog). The install is incomplete - re-run: install.ps1 -Stage dependencies"
    }
    Write-Success "Dependencies installed and baseline imports verified"

    # Entry-point verification: the venv must expose the nexus CLI shim.
    $nexusExe = Join-Path $NexusHome "venv\Scripts\nexus.exe"
    if (Test-Path $nexusExe) {
        Write-Success "nexus CLI entry point present"
    } else {
        $nseExe = Join-Path $NexusHome "venv\Scripts\nse.exe"
        if (Test-Path $nseExe) {
            Write-WarnMsg "nse.exe present but nexus.exe missing - shim repair needed"
        } else {
            Write-WarnMsg "nexus.exe entry point missing after install (check pyproject [project.scripts])"
        }
    }
}

# ============================================================================
# Node (optional - only when the checkout carries a package.json workspace)
# ============================================================================

$Script:HasNode = $false

function Test-NodeVersionOk {
    param([string]$Version)
    if (-not $Version) { return $false }
    try {
        $v = [version]($Version -replace '^v', '')
        return ($v.Major -ge 18)
    } catch {
        return $false
    }
}

function Test-Node {
    Write-Info "Checking Node.js (optional - only needed if the checkout ships a Node workspace)..."
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    if ($nodeCmd) {
        $version = & node --version 2>$null
        if (Test-NodeVersionOk $version) {
            Write-Success "Node.js $version found"
            $Script:HasNode = $true
        } else {
            Write-WarnMsg "Node.js $version is unsupported (require >= 18)"
        }
    } else {
        Write-Info "Node.js not present - fine for a Python-only Nexus install (repo has no package.json by default)"
    }
    # Node is OPTIONAL for Nexus: absence is a deliberate skip, not a failure.
    return $true
}

function Install-NodeDependencies {
    # The Nexus repo does not ship a package.json today; if a future checkout
    # adds one, install deps via npm ci (lockfile-authoritative) with a
    # fallback to npm install. Otherwise this stage is a documented skip.
    if (Test-Path (Join-Path $InstallDir "package.json")) {
        if (-not $Script:HasNode) { Test-Node | Out-Null }
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
            Write-WarnMsg "package.json present but npm unavailable - Node dependencies skipped"
            $Script:_StageSkippedReason = "package.json present but npm unavailable"
            return
        }
        $npmExe = (Get-Command npm -ErrorAction SilentlyContinue).Source
        if ($npmExe -like "*.ps1") {
            $sibling = Join-Path (Split-Path $npmExe -Parent) "npm.cmd"
            if (Test-Path $sibling) { $npmExe = $sibling }
        }
        if (Test-Path (Join-Path $InstallDir "package-lock.json")) {
            Write-Info "Installing Node dependencies via npm ci (lockfile authoritative)..."
            Invoke-NativeWithRelaxedErrorAction { Push-Location $InstallDir; try { & $npmExe ci 2>&1 | Out-Null } finally { Pop-Location } }
            $code = $LASTEXITCODE
            if ($code -ne 0) {
                Write-WarnMsg "npm ci failed (exit $code); falling back to npm install"
                Invoke-NativeWithRelaxedErrorAction { Push-Location $InstallDir; try { & $npmExe install 2>&1 | Out-Null } finally { Pop-Location } }
                $code = $LASTEXITCODE
            }
            if ($code -eq 0) { Write-Success "Node dependencies installed" }
            else { throw "npm ci and npm install both failed (exit $code)" }
        } else {
            Write-Info "Installing Node dependencies via npm install (no lockfile present)..."
            Invoke-NativeWithRelaxedErrorAction { Push-Location $InstallDir; try { & $npmExe install 2>&1 | Out-Null } finally { Pop-Location } }
            if ($LASTEXITCODE -eq 0) { Write-Success "Node dependencies installed" }
            else { throw "npm install failed (exit $LASTEXITCODE)" }
        }
    } else {
        Write-Info "No package.json in the checkout - Node dependencies stage intentionally skipped"
        $Script:_StageSkippedReason = "no package.json in checkout (Node is optional for Nexus)"
    }
}

# ============================================================================
# Configuration (create-if-missing; NEVER overwrite user config)
# ============================================================================

function Copy-ConfigTemplates {
    Write-Info "Setting up Nexus configuration (create-if-missing)..."
    $configDir = Join-Path $NexusHome "config"
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null

    $repoConfigDir = Join-Path $InstallDir "configs"
    $templates = @(
        @{ Source = "base.yaml"; Target = "base.yaml" },
        @{ Source = "live.yaml.example"; Target = "live.yaml" }
    )
    foreach ($t in $templates) {
        $src = Join-Path $repoConfigDir $t.Source
        $dst = Join-Path $configDir $t.Target
        if (Test-Path $dst) {
            Write-Info "Config already exists, keeping it: $dst"
        } elseif (Test-Path $src) {
            Copy-Item -LiteralPath $src -Destination $dst
            Write-Success "Created $dst from template"
        } else {
            Write-WarnMsg "Template not found in repo: $src (skipped)"
        }
    }

    Write-Success "Configuration directory ready: $configDir"
    Write-Info "NOTE: Nexus reads live configuration from this directory; API keys are NOT required for installation."
}

# ============================================================================
# PATH + command shim
# ============================================================================

function Install-NexusCommandLaunchers {
    # Expose ONLY the nexus launcher on PATH - never the whole venv\Scripts
    # (which contains python.exe and would hijack the user's `python`).
    # A .cmd delegator is used (venv-agnostic, survives venv recreation).
    $scriptsDir = Join-Path $NexusHome "venv\Scripts"
    $binDir = Join-Path $NexusHome "bin"
    $requiredSource = Join-Path $scriptsDir "nexus.exe"
    if (-not (Test-Path -LiteralPath $requiredSource -PathType Leaf)) {
        # Fall back to the nse entry point if that is what the install produced.
        $nseSource = Join-Path $scriptsDir "nse.exe"
        if (Test-Path -LiteralPath $nseSource) { $requiredSource = $nseSource }
        else { throw "Cannot set up the nexus command: no launcher found in $scriptsDir" }
    }
    $launcherName = (Split-Path $requiredSource -Leaf).Replace(".exe", "")
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null

    $cmdPath = Join-Path $binDir "$launcherName.cmd"
    $cmdContent = "@echo off`r`n`"$requiredSource`" %*"
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($cmdPath, $cmdContent, $utf8NoBom)

    # Also write nexus.cmd -> nexus.exe delegator when the launcher is nse
    # so the documented `nexus` command always exists.
    if ($launcherName -eq "nse") {
        $nexusCmdPath = Join-Path $binDir "nexus.cmd"
        $nexusContent = "@echo off`r`n`"$requiredSource`" %*"
        [System.IO.File]::WriteAllText($nexusCmdPath, $nexusContent, $utf8NoBom)
    }

    # Verify the shim actually launches before PATH mutation.
    $probe = & $cmdPath version 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-WarnMsg "nexus.cmd shim installed but 'version' probe returned exit $LASTEXITCODE"
    }
    return $binDir
}

function Set-NexusPathVariable {
    Write-Info "Setting up the nexus command..."
    if ($NoVenv) {
        $nexusBin = Join-Path $InstallDir ""
        Write-Info "NoVenv mode: expecting pre-installed launchers in $nexusBin"
    } else {
        $nexusBin = Install-NexusCommandLaunchers
    }

    # Migrate legacy layouts off the user PATH (venv\Scripts shadowing python).
    $legacyEntries = @("$InstallDir\venv\Scripts", (Join-Path $NexusHome "venv\Scripts"))
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentPath) {
        $items = @($currentPath -split ';')
        $cleaned = @($items | Where-Object { $legacyEntries -notcontains $_ })
        if ($cleaned.Count -ne $items.Count) {
            [Environment]::SetEnvironmentVariable("Path", ($cleaned -join ";"), "User")
            Write-Info "Removed legacy venv\Scripts launcher entries from user PATH"
        }
    }

    Add-UserPathEntry -Entry $nexusBin -ToFront

    # Persist NEXUS_HOME so the application and future updates agree on roots.
    $currentNexusHome = [Environment]::GetEnvironmentVariable("NEXUS_HOME", "User")
    if ($currentNexusHome -ne $NexusHome) {
        [Environment]::SetEnvironmentVariable("NEXUS_HOME", $NexusHome, "User")
        Write-Success "Set NEXUS_HOME=$NexusHome"
    }
    $env:NEXUS_HOME = $NexusHome
    $env:Path = "$nexusBin;$env:Path"

    Write-Success "nexus command ready (new terminals only; restart your shell for PATH)"
}

# ============================================================================
# Installation state (non-secret metadata)
# ============================================================================

function Write-InstallState {
    param([string]$LastStage = "verify")
    $stateDir = Join-Path $NexusHome "state"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    $statePath = Join-Path $stateDir "install.json"

    # Resume support (task C-11): merge with the PREVIOUS state so the fields
    # of stages that were never re-run in this session survive. A stage that
    # ran writes its own per-stage record; the state file becomes a durable
    # stage-progress ledger a driver can use to resume a partial install.
    $previous = $null
    if (Test-Path -LiteralPath $statePath) {
        try {
            $previous = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        } catch { $previous = $null }
    }

    $gitVersion = $null
    try { $gitVersion = (& git --version 2>$null) } catch { }
    $pyVersion = $null
    $venvPython = Get-VenvPython
    if ($venvPython -and (Test-Path $venvPython)) {
        try { $pyVersion = (& $venvPython --version 2>$null) } catch { }
    }
    $repoSha = Get-RepoHeadSha -Repo $InstallDir

    # Per-stage evidence: merge this session's results over the previous ones.
    $stageRecords = [ordered]@{}
    if ($previous -and $previous.stages) {
        foreach ($prop in $previous.stages.PSObject.Properties) {
            $stageRecords[$prop.Name] = $prop.Value
        }
    }
    foreach ($key in $Script:_StageResults.Keys) {
        $stageRecords[$key] = $Script:_StageResults[$key]
    }
    # Determine the furthest successful stage for resume semantics.
    $lastOk = $LastStage
    if (-not $lastOk) {
        foreach ($s in $Script:InstallStages) {
            $rec = $stageRecords[$s.Name]
            if ($rec -and $rec.ok) { $lastOk = $s.Name }
        }
    }

    $state = [ordered]@{
        installer_version     = $Script:InstallerVersion
        protocol_version      = $Script:ProtocolVersionValue
        installed_at          = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        nexus_home            = $NexusHome
        install_dir           = $InstallDir
        repo_head             = $repoSha
        python                = if ($pyVersion) { "$pyVersion" } else { $null }
        git                   = if ($gitVersion) { "$gitVersion" } else { $null }
        last_successful_stage = $lastOk
        stages                = $stageRecords
    }

    # BOM-less UTF-8 so JSON parsers on any runtime accept the file. Atomic
    # replace: write to a temp file, then rename (crash cannot truncate state).
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    $tmpState = "$statePath.tmp-$PID"
    [System.IO.File]::WriteAllText($tmpState, ($state | ConvertTo-Json -Depth 6), $utf8NoBom)
    Move-Item -LiteralPath $tmpState -Destination $statePath -Force
    Write-Success "Install state written: $statePath"
}

# ============================================================================
# Verification (verify stage + doctor hooks)
# ============================================================================

function Invoke-NexusVerify {
    Write-Info "Verifying the installation..."

    $problems = @()

    # 1. Python interpreter.
    if (-not $NoVenv) {
        $venvPython = Get-VenvPython
        if (-not (Test-VenvHealthy -VenvPython $venvPython)) {
            $problems += "venv python missing or unhealthy at $venvPython"
        } else {
            $ver = & $venvPython --version 2>$null
            Write-Success "Python: $ver ($venvPython)"
        }
    }

    # 2. nexus CLI resolves and answers.
    if (-not $NoVenv) {
        $nexusExe = Join-Path $NexusHome "venv\Scripts\nexus.exe"
        $nseExe = Join-Path $NexusHome "venv\Scripts\nse.exe"
        $probe = $nexusExe
        if (-not (Test-Path $probe)) { $probe = $nseExe }
        if (Test-Path $probe) {
            $ErrorActionPreference = "Continue"
            $out = & $probe version 2>$null
            $exit = $LASTEXITCODE
            $ErrorActionPreference = "Stop"
            if ($exit -eq 0) {
                Write-Success "nexus CLI verified: $probe"
            } else {
                $problems += "nexus CLI at $probe returned exit $exit on 'version'"
            }
        } else {
            $problems += "nexus CLI entry point not found in venv\Scripts"
        }
    }

    # 3. Repository present.
    if (-not (Test-NexusRepoValid -Repo $InstallDir)) {
        $problems += "repository at $InstallDir is not a valid git checkout"
    } else {
        $sha = Get-RepoHeadSha -Repo $InstallDir
        Write-Success "Repository HEAD: $sha"
    }

    # 4. Config presence.
    if (-not (Test-Path (Join-Path $NexusHome "config\live.yaml"))) {
        Write-WarnMsg "No live.yaml yet at $($NexusHome)\config\live.yaml - Nexus will boot with defaults until configured"
    }

    if ($problems.Count -gt 0) {
        foreach ($p in $problems) { Write-ErrMsg $p }
        throw "Verification failed: $($problems.Count) problem(s) found"
    }
    Write-Success "Verification passed"
}

# ============================================================================
# Optional-tooling ensure mode + post-install
# ============================================================================

function Test-Mt5Available {
    # MT5 is an EXTERNAL/ONLINE data layer: never installed or modified here.
    # Detection only: is the MetaTrader5 Python package importable and does a
    # terminal exist? Read-only, no terminal interaction beyond metadata.
    $venvPython = Get-VenvPython
    if (-not $venvPython -or -not (Test-Path $venvPython)) { return $false }
    $ErrorActionPreference = "Continue"
    try {
        & $venvPython -c "import MetaTrader5" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Invoke-EnsureMode {
    param([string]$Deps)
    foreach ($depRaw in ($Deps -split ",")) {
        $dep = $depRaw.Trim()
        switch ($dep) {
            "python" {
                Resolve-UvCmd
                if (-not (Test-Python)) { Write-ErrMsg "python could not be provisioned"; exit 1 }
            }
            "git" {
                if (-not (Install-Git)) { Write-ErrMsg "git could not be provisioned"; exit 1 }
            }
            "mt5" {
                if (Test-Mt5Available) {
                    Write-Success "MetaTrader5 Python package importable (integration is the application's responsibility)"
                } else {
                    Write-Info "MetaTrader5 package not importable - it is a platform-conditional pyproject dependency on Windows; run -Stage dependencies in a venv, or install manually: pip install MetaTrader5"
                    Write-Info "NOTE: the MT5 terminal itself is NEVER installed or modified by the Nexus installer."
                }
            }
            "node" {
                [void](Test-Node)
                if (-not $Script:HasNode) { Write-ErrMsg "node not available (optional for Nexus)"; exit 1 }
            }
            default {
                Write-ErrMsg "Unknown dependency: '$dep'. Supported: python, git, mt5, node"
                exit 1
            }
        }
    }
}

function Invoke-PostInstallMode {
    # Post-install: report model/MT5 posture WITHOUT touching anything.
    Write-Info "Post-install verification (read-only)..."
    Write-Info ""
    Write-Info "Model contract expectation (application-owned): scalp_v3 / 70D"
    Write-Info "  The installer NEVER downloads or replaces model artifacts."
    Write-Info "  Model health belongs to the application: nexus doctor / nexus version"
    Write-Info ""
    if (Test-Mt5Available) {
        Write-Success "MetaTrader5 Python package: importable"
    } else {
        Write-WarnMsg "MetaTrader5 Python package: not importable (MetaTrader5 is a Windows-only pyproject dependency)"
    }
    Write-Info "  MT5 terminal: never installed/modified/started by the installer."
    Write-Info ""
    Write-Success "Post-install complete"
}

# ============================================================================
# Stage protocol (single source of truth)
# ============================================================================
#
# install.ps1 exposes a small, stable stage protocol for programmatic drivers
# (a future Nexus GUI, CI, agents). CLI users running the canonical one-liner
# never touch it - default invocation behaves as a plain installer.
#
#   install.ps1 -ProtocolVersion   -> integer on stdout
#   install.ps1 -Manifest          -> manifest JSON on stdout
#   install.ps1 -Stage <name>      -> one JSON result frame on stdout
#   install.ps1 -Json              -> JSON summary for a full install
#
# Exit codes: 0 = success or deliberate skip; 1 = stage failure;
#             2 = unknown stage.
#
# Stage names are API. Do not rename stages after external drivers depend on
# them; adding stages is additive and does NOT bump the protocol version.

$Script:InstallStages = @(
    @{ Name = "environment";  Title = "Detecting environment and normalizing paths";    Category = "prereqs";    NeedsUserInput = $false; Worker = "Stage-Environment" }
    @{ Name = "runtime";      Title = "Provisioning Python runtime (uv-managed)";        Category = "prereqs";    NeedsUserInput = $false; Worker = "Stage-Runtime" }
    @{ Name = "git";          Title = "Ensuring Git (system or managed portable)";       Category = "prereqs";    NeedsUserInput = $false; Worker = "Stage-Git" }
    @{ Name = "node";         Title = "Detecting Node.js (optional)";                    Category = "prereqs";    NeedsUserInput = $false; Worker = "Stage-Node" }
    @{ Name = "repository";   Title = "Acquiring Nexus source (git SSH/HTTPS/ZIP)";      Category = "install";    NeedsUserInput = $false; Worker = "Stage-Repository" }
    @{ Name = "venv";         Title = "Creating Python virtual environment";             Category = "install";    NeedsUserInput = $false; Worker = "Stage-Venv" }
    @{ Name = "dependencies"; Title = "Installing Python dependencies (pyproject)";      Category = "install";    NeedsUserInput = $false; Worker = "Stage-Dependencies" }
    @{ Name = "node-deps";    Title = "Installing Node dependencies (when applicable)";  Category = "install";    NeedsUserInput = $false; Worker = "Stage-NodeDeps" }
    @{ Name = "config";       Title = "Writing configuration templates (create-if-missing)"; Category = "finalize"; NeedsUserInput = $false; Worker = "Stage-Config" }
    @{ Name = "path";         Title = "Registering the nexus command on PATH";           Category = "finalize";   NeedsUserInput = $false; Worker = "Stage-Path" }
    @{ Name = "verify";       Title = "Verifying the installation";                      Category = "finalize";   NeedsUserInput = $false; Worker = "Stage-Verify" }
    @{ Name = "state";        Title = "Writing installation state";                      Category = "finalize";   NeedsUserInput = $false; Worker = "Stage-State" }
)

# Stage workers - thin wrappers over the implementation functions.
function Stage-Environment  {
    # Detection + early failure diagnosis. Asserts the environment is viable:
    # Windows, writable roots, sane disk space. Mutates nothing beyond logs.
    if (-not (Test-IsWindows)) { throw "This installer supports Windows only (PowerShell on Windows)" }
    $freeGB = Get-FreeDiskSpaceGB -DriveRoot $NexusHome
    if ($freeGB -ne $null -and $freeGB -lt 5) {
        throw "Insufficient disk space: ${freeGB}GB free on the install drive (need >= 5GB)"
    }
    if (-not (Test-DirectoryWritable $NexusHome)) {
        throw "NexusHome is not writable: $NexusHome"
    }
    if (-not (Test-DirectoryWritable (Split-Path -Parent $InstallDir))) {
        throw "InstallDir parent is not writable: $(Split-Path -Parent $InstallDir)"
    }
    Write-Success "Environment OK (arch=$(Get-WindowsArch), free=${freeGB}GB, home=$NexusHome)"
}
function Stage-Runtime      {
    if (-not (Install-Uv)) { throw "uv installation failed" }
    Resolve-UvCmd
    if (-not (Test-Python)) { throw "Python $PythonVersion not available" }
}
function Stage-Git          {
    if (-not (Install-Git)) { throw "Git not available and auto-install failed - install from https://git-scm.com/download/win then re-run" }
}
function Stage-Node         {
    $null = Test-Node
    # Node is optional by design; Test-Node never throws. skipped reason only
    # when no usable Node exists at all.
    if (-not $Script:HasNode) {
        $Script:_StageSkippedReason = "Node.js not available (optional for Nexus; only needed for a Node workspace)"
    }
}
function Stage-Repository   { Install-Repository }
function Stage-Venv         { Install-Venv }
function Stage-Dependencies { Install-PythonDependencies }
function Stage-NodeDeps     { Install-NodeDependencies }
function Stage-Config       { Copy-ConfigTemplates }
function Stage-Path         { Set-NexusPathVariable }
function Stage-Verify       { Invoke-NexusVerify }
function Stage-State        { Write-InstallState -LastStage "verify" }

function Get-InstallStage {
    param([string]$Name)
    foreach ($s in $Script:InstallStages) {
        if ($s.Name -eq $Name) { return $s }
    }
    return $null
}

function Step-OutOfInstallDir {
    # Two hazards live in the process CWD:
    #   1. Windows refuses to delete a directory any shell is cd'd inside.
    #   2. Git discovers the working repo by walking UP from the CWD. When the
    #      installer is launched from inside a git checkout (any child of a
    #      repo shell, which is the normal pytest/dev-machine case), every
    #      bare `git <cmd>` the installer runs resolves against the CALLER's
    #      repository - leaking the caller's status into installer output and
    #      pointing update/clone git operations at the wrong repo. GIT_DIR etc.
    #      are cleared too, but the CWD walk-up happens whenever the CWD is
    #      inside a repo, so move the process CWD to a repo-free anchor first.
    try {
        $currentResolved = (Get-Location).ProviderPath
        $installResolved = $null
        if (Test-Path $InstallDir) {
            $installResolved = (Resolve-Path $InstallDir -ErrorAction SilentlyContinue).ProviderPath
        }
        if ($installResolved -and $currentResolved.ToLower().StartsWith($installResolved.ToLower())) {
            Set-Location $env:USERPROFILE
            return
        }
        # Also step out when the CWD is inside ANY git work tree (repo walk-up
        # hazard). %USERPROFILE% is outside a checkout for every supported layout.
        if (Test-Path (Join-Path $currentResolved ".git")) {
            Set-Location $env:USERPROFILE
        }
    } catch { }
}

function Invoke-Stage {
    param([Parameter(Mandatory = $true)][hashtable]$StageDef)

    Sync-EnvPath
    $Script:_StageSkippedReason = $null

    $start = [DateTime]::UtcNow
    $result = [ordered]@{
        stage       = $StageDef.Name
        ok          = $false
        skipped     = $false
        reason      = $null
        duration_ms = 0
    }

    $threw = $null
    try {
        & $StageDef.Worker
        $result.ok = $true
        if ($Script:_StageSkippedReason) {
            $result.skipped = $true
            $result.reason = $Script:_StageSkippedReason
        }
    } catch {
        $threw = $_
        $result.ok = $false
        $result.reason = "$($_.Exception.Message)"
        Write-Log "STAGE FAILED: $($StageDef.Name): $($_.Exception.Message)"
    } finally {
        $result.duration_ms = [int]([DateTime]::UtcNow - $start).TotalMilliseconds
        # Durable per-stage evidence (task C-11): every stage result is
        # recorded on the session ledger and flushed to state/install.json,
        # so a partial install's progress survives process death.
        if (-not $Script:_StageResults) { $Script:_StageResults = @{} }
        $Script:_StageResults[$StageDef.Name] = $result
        if ($result.ok) {
            try { Write-InstallStateFromSession } catch { }
        }
        if ($Json -or $PSBoundParameters.ContainsKey("Stage") -or $Script:_DriverMode) {
            # Protocol mode: every stage emits exactly one JSON frame on
            # stdout. Human diagnostics already went to stderr/human channels.
            $result | ConvertTo-Json -Compress | Write-Output
            if (-not $result.ok) {
                $Script:_StageEmittedErrorFrame = $true
            }
        }
    }
    if ($threw) { throw $threw }
}

function Write-InstallStateFromSession {
    # Flush per-stage progress to disk without recomputing repo/venv facts:
    # fast, best-effort, called after each successful stage (task C-11).
    $stateDir = Join-Path $NexusHome "state"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    $statePath = Join-Path $stateDir "install.json"
    $previous = $null
    if (Test-Path -LiteralPath $statePath) {
        try { $previous = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json } catch { }
    }
    $stageRecords = [ordered]@{}
    if ($previous -and $previous.stages) {
        foreach ($prop in $previous.stages.PSObject.Properties) { $stageRecords[$prop.Name] = $prop.Value }
    }
    foreach ($key in $Script:_StageResults.Keys) { $stageRecords[$key] = $Script:_StageResults[$key] }
    $state = [ordered]@{
        installer_version     = $Script:InstallerVersion
        protocol_version      = $Script:ProtocolVersionValue
        installed_at          = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        nexus_home            = $NexusHome
        install_dir           = $InstallDir
        last_successful_stage = $StageDef.Name
        stages                = $stageRecords
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    $tmpState = "$statePath.tmp-$PID"
    [System.IO.File]::WriteAllText($tmpState, ($state | ConvertTo-Json -Depth 6), $utf8NoBom)
    Move-Item -LiteralPath $tmpState -Destination $statePath -Force
}

function Invoke-AllStages {
    Step-OutOfInstallDir
    Initialize-InstallerLog
    foreach ($s in $Script:InstallStages) {
        Invoke-Stage -StageDef $s
    }
}

function Invoke-FullInstall {
    Write-Banner
    Invoke-AllStages
    if (-not $Json) {
        Write-Completion
    } else {
        $summary = [ordered]@{
            ok               = $true
            protocol_version = $Script:ProtocolVersionValue
            nexus_home       = $NexusHome
            install_dir      = $InstallDir
        }
        $summary | ConvertTo-Json -Compress | Write-Output
    }
}

# ============================================================================
# Human-facing output
# ============================================================================

function Write-Banner {
    Write-Host ""
    Write-Host "+----------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host "|            * Nexus Scalp Engine Installer                |" -ForegroundColor Magenta
    Write-Host "|            Windows bootstrap / update / recovery         |" -ForegroundColor Magenta
    Write-Host "+----------------------------------------------------------+" -ForegroundColor Magenta
    Write-Host ""
}

function Write-Completion {
    Write-Host ""
    Write-Host "+----------------------------------------------------------+" -ForegroundColor Green
    Write-Host "|               [OK] Nexus installation complete!          |" -ForegroundColor Green
    Write-Host "+----------------------------------------------------------+" -ForegroundColor Green
    Write-Host ""
    Write-Host "* Locations:" -ForegroundColor Cyan
    Write-Host "   Engine code:  $InstallDir"
    Write-Host "   venv:         $(Join-Path $NexusHome 'venv')"
    Write-Host "   Config:       $(Join-Path $NexusHome 'config')  (live.yaml - created from template, never overwritten)"
    Write-Host "   State:        $(Join-Path $NexusHome 'state\install.json')"
    Write-Host "   Logs:         $(Join-Path $NexusHome 'logs\installer.log')"
    Write-Host ""
    Write-Host "* Commands:" -ForegroundColor Cyan
    Write-Host "   nexus version      Show build identity"
    Write-Host "   nexus doctor       Full system diagnostics (read-only)"
    Write-Host "   nexus start        Start the engine (paper mode by default)"
    Write-Host ""
    Write-Host "[*] Restart your terminal for PATH changes to take effect." -ForegroundColor Yellow
    Write-Host ""
}

# ============================================================================
# Entry-point dispatch
# ============================================================================
# One try/catch so failures in protocol mode produce a single structured JSON
# error frame (never a PowerShell traceback mixed with stdout JSON), and an
# `irm | iex` failure cannot kill the caller's session.

# Dot-sourcing loads the installer's functions for isolated behavioral tests
# without running an install (tests/installer/*).
if ($MyInvocation.InvocationName -eq ".") { return }

# ============================================================================
# Install lock (single-writer guarantee)
# ============================================================================
# Prevents two concurrent installers from mutating the same installation.
# Concurrency test contract (task C-9): both -Json processes targeting one
# home must survive: exactly one runs stages, the other exits 0 with a
# well-formed JSON frame (ok=false, skipped=true, reason=lock held).

$Script:_LockOwned = $false
$Script:_LockFile = $null

function Wait-NexusInstallerLock {
    # Try to acquire <NexusHome>\state\installer.lock exclusively. Windows
    # file locks are mandatory: a second open with 'FileShare.None' fails, so
    # a crashed installer releases the lock automatically when its handle
    # dies (no stale-lock cleanup problem). Retry briefly before reporting.
    $lockPath = Join-Path $NexusHome "state\installer.lock"
    try {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $lockPath) | Out-Null
    } catch { }
    $maxWaitMs = 5000
    $waitedMs = 0
    while ($true) {
        try {
            $stream = [System.IO.File]::Open(
                $lockPath,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None)
            $bytes = [System.Text.Encoding]::UTF8.GetBytes(("pid=$PID;started=" + (Get-Date).ToUniversalTime().ToString("o")))
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush()
            $Script:_LockFile = $stream
            $Script:_LockOwned = $true
            return $true
        } catch {
            if ($waitedMs -ge $maxWaitMs) { return $false }
            Start-Sleep -Milliseconds 250
            $waitedMs += 250
        }
    }
}

function Release-NexusInstallerLock {
    if ($Script:_LockFile) {
        try { $Script:_LockFile.Dispose() } catch { }
        $Script:_LockFile = $null
        $Script:_LockOwned = $false
    }
}

function Test-LockHeldByOtherProcess {
    # Cheap probe for the concurrent-driver test: can a second exclusive
    # open succeed right now? Returns $true when SOMEONE holds the lock.
    $lockPath = Join-Path $NexusHome "state\installer.lock"
    if (-not (Test-Path $lockPath)) { return $false }
    try {
        $s = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::None)
        $s.Dispose()
        return $false
    } catch {
        return $true
    }
}

$Script:_DriverMode = [bool]($Json -or $PSBoundParameters.ContainsKey("Stage"))

try {
    if ($Ensure -ne "") {
        if ($PSBoundParameters.ContainsKey("Stage")) {
            Write-ErrMsg "Cannot use -Ensure and -Stage simultaneously"
            exit 1
        }
        Invoke-EnsureMode -Deps $Ensure
        exit 0
    }

    if ($PostInstall) {
        Invoke-PostInstallMode
        exit 0
    }

    if ($ProtocolVersion) {
        Write-Output $Script:ProtocolVersionValue
        exit 0
    }

    if ($ShowResolvedPaths) {
        $Script:ResolvedPathReport | ConvertTo-Json -Depth 5 -Compress | Write-Output
        exit 0
    }

    if ($Manifest) {
        $payload = [ordered]@{
            protocol_version = $Script:ProtocolVersionValue
            installer_version = $Script:InstallerVersion
            stages = @($Script:InstallStages | ForEach-Object {
                [ordered]@{
                    name             = $_.Name
                    title            = $_.Title
                    category         = $_.Category
                    needs_user_input = $_.NeedsUserInput
                }
            })
        }
        $payload | ConvertTo-Json -Depth 5 -Compress | Write-Output
        exit 0
    }

    # -Stage dispatch. Uses PSBoundParameters so an explicit `-Stage ""` from a
    # misbehaving driver surfaces as unknown-stage exit 2 instead of falling
    # through to a full install.
    if ($PSBoundParameters.ContainsKey("Stage")) {
        $def = Get-InstallStage -Name $Stage
        if (-not $def) {
            $err = [ordered]@{
                ok     = $false
                stage  = $Stage
                reason = "unknown stage: '$Stage'. Run install.ps1 -Manifest to list valid stages."
            }
            $err | ConvertTo-Json -Compress | Write-Output
            exit 2
        }
        Step-OutOfInstallDir
        Initialize-InstallerLog
        # Single-writer lock (task C-9): mutating stages must not run when
        # another installer holds the lock. Deliberate skip: ok=true frame
        # with skipped=true + reason, exit 0 (never an error-shaped exit).
        if ($Stage -ne "environment") {
            $lockAcquired = Wait-NexusInstallerLock
            if (-not $lockAcquired) {
                $Script:_StageEmittedErrorFrame = $true  # authoritative frame below; suppress double-emit
                $frame = [ordered]@{
                    stage       = $Stage
                    ok          = $true
                    skipped     = $true
                    reason      = "another installer holds the install lock ($NexusHome\state\installer.lock)"
                    duration_ms = 0
                }
                if ($Script:_DriverMode) { $frame | ConvertTo-Json -Compress | Write-Output }
                else { Write-WarnMsg "Skipped stage $Stage - another installer holds the install lock." }
                exit 0
            }
        }
        try {
            Invoke-Stage -StageDef $def
        } finally {
            Release-NexusInstallerLock
        }
        exit 0
    }

    # Default: full install.
    $lockAcquired = Wait-NexusInstallerLock
    if (-not $lockAcquired) {
        $Script:_StageEmittedErrorFrame = $true
        $frame = [ordered]@{
            ok          = $false
            skipped     = $true
            reason      = "another installer holds the install lock ($NexusHome\state\installer.lock)"
        }
        if ($Script:_DriverMode) { $frame | ConvertTo-Json -Compress | Write-Output }
        else { Write-ErrMsg "Another installer is running against $NexusHome - exiting." }
        exit 0
    }
    try {
        Invoke-FullInstall
    } finally {
        Release-NexusInstallerLock
    }
} catch {
    if ($Script:_DriverMode) {
        # Protocol mode: emit a structured error frame ONLY if the stage
        # wrapper didn't already emit the authoritative frame for this
        # failure (one record per invocation, never double output).
        if (-not $Script:_StageEmittedErrorFrame) {
            $err = [ordered]@{
                ok     = $false
                stage  = if ($PSBoundParameters.ContainsKey("Stage")) { $Stage } else { $null }
                reason = "$($_.Exception.Message)"
            }
            $err | ConvertTo-Json -Compress | Write-Output
        }
        exit 1
    }

    # Interactive mode: friendly recovery guidance, no traceback wall.
    Write-Host ""
    Write-ErrMsg "Installation failed: $($_.Exception.Message)"
    Write-Host ""
    Write-Info "If the error is unclear, run with diagnostics:"
    Write-Host "  .\install.ps1 -ShowResolvedPaths" -ForegroundColor Yellow
    Write-Host "  .\install.ps1 -Stage <stage> -Json   (see: .\install.ps1 -Manifest)" -ForegroundColor Yellow
    Write-Host "  Log: $(Join-Path $NexusHome 'logs\installer.log')" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
