$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$Service = "kb-manager"
$RunningServices = docker compose ps --status running --services

if ($LASTEXITCODE -ne 0 -or $RunningServices -notcontains $Service) {
    Write-Error "Container '$Service' is not running. Start it first with: docker compose up -d $Service"
    exit 1
}

docker compose exec -T $Service python -m app.scripts.load_tables

if ($LASTEXITCODE -ne 0) {
    Write-Error "Tables loader failed."
    exit $LASTEXITCODE
}
