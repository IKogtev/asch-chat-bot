$root = (Resolve-Path .).Path
$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell session."
}

$items = Get-ChildItem -LiteralPath $root -Force | Where-Object {
    ($_.Name -match '^[A-Za-z0-9_]{8}$' -and -not $_.PSIsContainer -and $_.Length -eq 4) -or
    ($_.PSIsContainer -and $_.Name -like 'pytest-cache-files-*') -or
    ($_.PSIsContainer -and $_.Name -eq '.tmp')
}

foreach ($item in $items) {
    $resolved = $item.FullName
    if (-not $resolved.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete outside workspace: $resolved"
    }

    if ($item.PSIsContainer) {
        takeown.exe /F "$resolved" /R /D Y | Out-Null
        icacls.exe "$resolved" /grant "$($env:USERNAME):F" /T /C | Out-Null
        Remove-Item -LiteralPath $resolved -Recurse -Force
    } else {
        takeown.exe /F "$resolved" | Out-Null
        icacls.exe "$resolved" /grant "$($env:USERNAME):F" /C | Out-Null
        Remove-Item -LiteralPath $resolved -Force
    }
}

git status --short
