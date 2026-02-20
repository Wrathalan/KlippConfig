$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$buildScript = Join-Path $PSScriptRoot "build_windows.ps1"
$exePath = Join-Path $repoRoot "dist\KlippConfig.exe"

if (-not (Test-Path $buildScript)) {
    throw "Build script not found: $buildScript"
}

& $buildScript -CreateDesktopShortcut:$false -BuildInstaller:$false
if ($LASTEXITCODE -ne 0) {
    throw "Build failed."
}

if (-not (Test-Path $exePath)) {
    throw "Built executable not found: $exePath"
}

Start-Process -FilePath $exePath -WorkingDirectory $repoRoot -WindowStyle Hidden
