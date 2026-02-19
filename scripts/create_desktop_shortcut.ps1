param(
    [Parameter(Mandatory = $true)]
    [string]$TargetPath,
    [string]$ShortcutName = "KlippConfig"
)

$ErrorActionPreference = "Stop"

$resolvedTarget = (Resolve-Path $TargetPath).Path
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktopPath "$ShortcutName.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $resolvedTarget
$shortcut.WorkingDirectory = Split-Path -Path $resolvedTarget -Parent
$shortcut.IconLocation = "$resolvedTarget,0"
$shortcut.Description = "Launch KlippConfig"
$shortcut.Save()

Write-Host "Desktop shortcut created: $shortcutPath"

