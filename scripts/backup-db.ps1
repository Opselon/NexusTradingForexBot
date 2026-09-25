# ============================================================
# Nexus Scalp Engine — database backup [Windows]
# ============================================================
# Safely exports the SQLite databases from the running nexus-artifacts
# volume to a timestamped host directory (default: backups/).
# Backups are never part of startup.
# ============================================================
param(
    [string]$OutputDir = "backups"
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$target = Join-Path $OutputDir $stamp
New-Item -ItemType Directory -Force -Path $target | Out-Null

# Check the core container exists
$running = docker ps --filter "name=nexus-scalp-core" --format "{{.Names}}" *> $null
if ($LASTEXITCODE -ne 0 -or -not $running) {
    Write-Host "core container not running - start the stack first: .\scripts\start.ps1 up" -ForegroundColor Red
    exit 1
}

# SQLite online backup: use sqlite3's .backup via the container python
$backed = 0
foreach ($db in @("audit.db", "news.db", "candle_intel.db")) {
    $copy = Join-Path $target $db
    docker compose exec -T core python -c "import sqlite3,sys; src=sqlite3.connect('/app/artifacts/$db'); dst=sqlite3.connect(sys.argv[1]); src.backup(dst); dst.close(); src.close()" $copy 2>$null
    if ($LASTEXITCODE -eq 0 -and (Test-Path $copy)) {
        Write-Host "[OK]   $db -> $copy" -ForegroundColor Green
        $backed++
    }
    else {
        Write-Host "[SKIP] $db missing in container (expected on first run)" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Backup complete: $target ($backed databases)" -ForegroundColor Green
Write-Host "Restore: copy a file over /app/artifacts/<name> then: docker compose restart core"