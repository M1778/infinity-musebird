<#
.SYNOPSIS
  infinity-musebird bootstrapper for Windows.

.DESCRIPTION
  Ensures uv exists, installs the pinned package as an isolated tool, then hands
  off to `musebird install`. All real logic lives in Python.

.EXAMPLE
  irm https://raw.githubusercontent.com/M1778/infinity-musebird/main/install.ps1 | iex
#>

$ErrorActionPreference = "Stop"
$MusebirdVersion = if ($env:MUSEBIRD_VERSION) { $env:MUSEBIRD_VERSION } else { "0.1.0" }
$Repo = "M1778/infinity-musebird"

function Say($msg) { Write-Host $msg }
function Fail($msg) { Write-Error $msg; exit 1 }

$uv = $null
if ($env:MUSEBIRD_UV -and (Test-Path $env:MUSEBIRD_UV)) {
    $uv = $env:MUSEBIRD_UV
} else {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { $uv = $cmd.Source }
}

if (-not $uv) {
    Say "Installing uv (provides Python, no admin needed)..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $candidate = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $candidate) { $uv = $candidate }
    else {
        $cmd = Get-Command uv -ErrorAction SilentlyContinue
        if ($cmd) { $uv = $cmd.Source } else { Fail "uv installed but not found; add %USERPROFILE%\.local\bin to PATH and re-run" }
    }
}

Say "Using $(& $uv --version)"

Say "Installing infinity-musebird $MusebirdVersion..."
& $uv tool install --force "infinity-musebird==$MusebirdVersion"
if ($LASTEXITCODE -ne 0) {
    & $uv tool install --force "infinity-musebird @ git+https://github.com/$Repo.git@v$MusebirdVersion"
    if ($LASTEXITCODE -ne 0) { Fail "package installation failed" }
}

$musebird = Get-Command musebird -ErrorAction SilentlyContinue
if (-not $musebird) {
    $toolDir = (& $uv tool dir) | Select-Object -First 1
    $candidate = Join-Path $toolDir "infinity-musebird\Scripts\musebird.exe"
    if (Test-Path $candidate) { $musebird = @{ Source = $candidate } }
    else { Fail "musebird installed but not on PATH; run: uv tool update-shell" }
}

Say "Configuring harnesses..."
& $musebird.Source install @args
