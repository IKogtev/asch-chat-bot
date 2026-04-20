$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot "venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Python interpreter not found: $pythonExe"
}

Set-Location $projectRoot

& $pythonExe -m pytest `
    -p no:cacheprovider `
    tests\unit\agent `
    tests\unit\utils `
    tests\unit\bot `
    tests\unit\mcps `
    -m unit `
    -vv `
    -s
