param(
    [switch]$CreateDesktopShortcut = $true,
    [switch]$BuildInstaller = $true
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$python = "python"

Write-Host "[1/5] Installing build dependencies..."
& $python -m pip install --upgrade pip pyinstaller

Write-Host "[2/5] Cleaning previous build outputs..."
Remove-Item -Recurse -Force "$repoRoot\build" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$repoRoot\dist\KlippConfig" -ErrorAction SilentlyContinue
Remove-Item -Force "$repoRoot\dist\KlippConfig.exe" -ErrorAction SilentlyContinue

Write-Host "[3/5] Building self-contained no-console binary..."
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name KlippConfig `
    --add-data "app\presets;app\presets" `
    --add-data "app\templates;app\templates" `
    app\main.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

$exePath = Join-Path $repoRoot "dist\KlippConfig.exe"
if (-not (Test-Path $exePath)) {
    throw "Expected binary not found: $exePath"
}

if ($CreateDesktopShortcut) {
    Write-Host "[4/5] Creating desktop shortcut..."
    & (Join-Path $PSScriptRoot "create_desktop_shortcut.ps1") `
        -TargetPath $exePath `
        -ShortcutName "KlippConfig"
} else {
    Write-Host "[4/5] Skipping desktop shortcut."
}

if ($BuildInstaller) {
    $iscc = Get-Command iscc -ErrorAction SilentlyContinue
    if ($null -ne $iscc) {
        Write-Host "[5/5] Building Inno Setup installer..."
        & $iscc.Source (Join-Path $PSScriptRoot "klippconfig-installer.iss")
    } else {
        Write-Host "[5/5] Inno Setup (iscc) not found. Skipping installer build."
    }
} else {
    Write-Host "[5/5] Installer build skipped by flag."
}

Write-Host "Build complete: $exePath"

