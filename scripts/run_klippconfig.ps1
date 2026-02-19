$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPythonw = Join-Path $repoRoot ".venv\Scripts\pythonw.exe"

if (Test-Path $venvPythonw) {
    $pythonw = $venvPythonw
} else {
    $pythonw = "pythonw"
}

Start-Process -FilePath $pythonw -ArgumentList "-m", "app.main" -WorkingDirectory $repoRoot -WindowStyle Hidden
