# ============================================================
# Nexus Scalp Engine — docker doctor (pre-flight)  [Windows]
# ============================================================
# Verifies the host BEFORE an expensive startup attempt:
#   Docker installed, daemon running, compose available,
#   .env presence, compose config validity, required ports free.
# POSIX equivalent: scripts/start.sh doctor
# ============================================================
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$fail = 0

# 1. docker CLI
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[FAIL] docker CLI not found. Install Docker Desktop (https://www.docker.com/products/docker-desktop/)." -ForegroundColor Red
    exit 1
}
Write-Host "[PASS] docker CLI present"

# 2. daemon running
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] Docker daemon is not running. Start Docker Desktop and wait for the whale icon." -ForegroundColor Red
    exit 1
}
Write-Host "[PASS] Docker daemon running"

# 3. compose available
docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] docker compose plugin missing. Update Docker Desktop (Compose v2+ required)." -ForegroundColor Red
    exit 1
}
Write-Host "[PASS] docker compose available"

# 4. .env presence
if (Test-Path ".env") {
    Write-Host "[PASS] .env present"
}
else {
    Write-Host "[WARN] .env missing - safe defaults from .env.example will be used. Copy it for overrides:  Copy-Item .env.example .env" -ForegroundColor Yellow
}

# 5. compose config validity
docker compose config --quiet *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] docker-compose.yml is invalid. Run: docker compose config" -ForegroundColor Red
    $fail = 1
}
else {
    Write-Host "[PASS] compose config valid"
}

# 6. required host ports free
$port = if ($env:NSE_WEB_PORT) { [int]$env:NSE_WEB_PORT } else { 9090 }
$busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "[FAIL] Port $port already in use. Stop the other process or set NSE_WEB_PORT in .env." -ForegroundColor Red
    $fail = 1
}
else {
    Write-Host "[PASS] port $port free"
}

if ($fail -ne 0) {
    Write-Host "doctor: FAILED - fix the items above, then run: .\scripts\start.ps1 up" -ForegroundColor Red
    exit 1
}
Write-Host "doctor: OK - ready for: .\scripts\start.ps1 up" -ForegroundColor Green