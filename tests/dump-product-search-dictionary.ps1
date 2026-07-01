param(
    [string]$DatabaseUrl = "",
    [int]$Limit = 500,
    [string]$Like = "",
    [switch]$Csv,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $PSScriptRoot "dump_product_search_dictionary.py"

if (-not $PythonExe) {
    $PythonExe = Join-Path $repoRoot "venv\Scripts\python.exe"
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

$arguments = @($scriptPath, "--limit", $Limit)

if ($DatabaseUrl) {
    $arguments += @("--database-url", $DatabaseUrl)
}

if ($Like) {
    $arguments += @("--like", $Like)
}

if ($Csv) {
    $arguments += "--csv"
}

& $PythonExe @arguments
