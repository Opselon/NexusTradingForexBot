# ============================================================
# Nexus Scalp Engine — development reset [Windows]
# ============================================================
# Explicitly destroys the Docker stack INCLUDING persistent named
# volumes (SQLite databases, model artifacts, research results).
# This is DESTRUCTIVE — it is NEVER part of normal startup.
# Use plain `docker compose down` to stop without deleting data.
# ============================================================
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host @"

  WARNING: this will delete ALL Docker volumes for the Nexus stack:
    - nexus-artifacts (databases: audit.db, news.db, candle_intel.db, models, research)
    - nexus-data
    - redis-data
  Local files under artifacts/ and data/ on THIS machine are not touched.

"@ -ForegroundColor Yellow

$confirm = Read-Host "Type YES to destroy the stack and its volumes"
if ($confirm -ne "YES") {
    Write-Host "aborted - nothing was deleted."
    exit 0
}

docker compose down -v
if ($LASTEXITCODE -ne 0) {
    Write-Host "reset failed (see output above)." -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "reset done. Next: .\scripts\start.ps1 up" -ForegroundColor Green