param(
    [string]$Python = "python",
    [string]$VenvPath = ".venv",
    [switch]$Dev
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

Write-Host "[1/4] Using repository root: $repoRoot"

$pythonCmd = Get-Command $Python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    throw "Python command '$Python' was not found on PATH."
}

$fullVenvPath = Join-Path $repoRoot $VenvPath
if (-not (Test-Path $fullVenvPath)) {
    Write-Host "[2/4] Creating virtual environment at $fullVenvPath"
    & $Python -m venv $fullVenvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment."
    }
} else {
    Write-Host "[2/4] Reusing existing virtual environment at $fullVenvPath"
}

$venvPython = Join-Path $fullVenvPath "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Virtual environment Python not found: $venvPython"
}

Write-Host "[3/4] Upgrading pip in virtual environment"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed."
}

$target = if ($Dev) { ".[dev]" } else { "." }
Write-Host "[4/4] Installing package dependencies ($target)"
& $venvPython -m pip install -e $target
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}

Write-Host ""
Write-Host "Dependencies installed successfully."
Write-Host "Activate with: . .\$VenvPath\Scripts\Activate.ps1"
