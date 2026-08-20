# ============================================================
# Nexus Scalp Engine — developer lifecycle helpers (Windows)
# ============================================================
# Thin convenience wrappers around docker compose; the core
# configuration lives in docker-compose.yml / .env (single
# source of truth). POSIX equivalent: scripts/start.sh
# ============================================================
param(
    [Parameter(Position = 0)]
    [ValidateSet("up", "down", "logs", "ps", "restart", "reset", "doctor")]
    [string]$Command = "up",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$compose = "docker", "compose"
$envFile = ".env"

switch ($Command) {
    "up" {
        if (-not (Test-Path $envFile)) {
            Write-Host "[nexus] .env not found - copying .env.example (safe dev defaults)"
            Copy-Item ".env.example" $envFile
        }
        & $compose "up" "-d" "--build" @Args
        if ($LASTEXITCODE -ne 0) { Write-Host "[nexus] FAILED" -ForegroundColor Red; exit $LASTEXITCODE }
        $port = if ($env:NSE_WEB_PORT) { $env:NSE_WEB_PORT } else { "9090" }
        Write-Host "[nexus] stack started. UI: http://localhost:$port"
    }
    "down" {
        & $compose "down" @Args
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "logs" {
        & $compose "logs" "-f" "--tail=100" @Args
    }
    "ps" {
        & $compose "ps"
    }
    "restart" {
        & $compose "restart" "core" "redis"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "reset" {
        Write-Host "[nexus] WARNING: removing containers AND named volumes (databases, models, research artifacts will be DELETED)." -ForegroundColor Yellow
        $confirm = Read-Host "Type YES to proceed"
        if ($confirm -eq "YES") {
            & $compose "down" "-v"
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
            Write-Host "[nexus] volumes removed."
        }
        else {
            Write-Host "[nexus] aborted."
        }
    }
    "doctor" {
        docker info *> $null
        if ($LASTEXITCODE -ne 0) { Write-Host "[nexus] FAIL: Docker daemon not running." -ForegroundColor Red; exit 1 }
        & $compose "config" "--quiet"
        if ($LASTEXITCODE -ne 0) { Write-Host "[nexus] FAIL: compose config invalid" -ForegroundColor Red; exit $LASTEXITCODE }
        Write-Host "[nexus] compose config OK"
        if (-not (Test-Path $envFile)) {
            Write-Host "[nexus] WARNING: .env missing (defaults will be used)" -ForegroundColor Yellow
        }
    }
}