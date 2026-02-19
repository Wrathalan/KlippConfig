# KlippConfig

KlippConfig is a Windows-first desktop Klipper configurator built with Python and Qt.
It generates full Klipper config packs from curated Voron presets.

## Features

- Voron-only preset catalog
- Guided wizard for board, toolhead, dimensions, and core hardware choices
- Optional CAN toolhead board selection and `toolhead.cfg` generation
- Includes LDO Nitehawk toolhead board options
- Expanded mainboard catalog with board pin aliases and board layout output
- LED control settings (pin, chain count, color order, initial color) with generated `leds.cfg`
- Add-on packs including AMS-style and other multi-material systems
- Files tab with raw/form `.cfg` editing and section override editor
- Live conflict validation banner that updates immediately on config changes
- Live validation with blocking errors and warnings shown in `Files`
- `Section Overrides` and `Validation Findings` are collapsed by default with unresolved-issues notice
- Export as ZIP or folder from the bottom of `Files`
- Open existing `.cfg` files directly in-app
- Footer device-health icon (red/green) reflects current SSH connection health
- Live SSH deploy to Klipper hosts (test connection, upload, optional restart)
- Network scanner to discover likely Klipper printers on your LAN
- `Manage Printer` tab for direct remote file editing, backups, and restore operations
- Embedded printer control window (web view) from `Manage Printer` for live manual controls
- Fully offline operation

## Quick Start

```powershell
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -e .[dev]
pythonw -m app.main
```

`pythonw` launches without a visible terminal window.

Alternative launcher:

```powershell
.\scripts\run_klippconfig.ps1
```

## UI Scaling

- In-app: `View -> UI Scale` (`Auto`, `85%`, `90%`, `100%`, `110%`, `125%`, `150%`)
- The selected UI scale is saved per user and reused on next launch.
- Startup override (useful for recovery/testing):

```powershell
$env:KLIPPCONFIG_UI_SCALE="90"
.\scripts\run_klippconfig.ps1
```

## Tests

```powershell
pytest
```

## SSH Tab

1. Open the `SSH` tab.
2. Optionally use `Printer Discovery` to scan your LAN and pick a host.
3. Enter or confirm SSH host, port, username, and auth credentials.
4. Set remote Klipper config directory (default `~/printer_data/config`).
5. Use `Test Connection`.
6. Use `Deploy Generated Pack` to upload the current validated pack.
7. Optional: enable restart and provide the restart command.

## Manage Printer Tab

1. Open the `Manage Printer` tab.
2. Set target host (or click `Use SSH Host` / choose from discovery first).
3. Click `Refresh Files`, browse folders with `Open Selected / Enter Folder` and `Up Directory`, then open a file.
4. Edit the file and click `Save Current File`.
5. Use `Create Backup` / `Refresh Backups` / `Restore Selected Backup` for backup workflows.
6. Use `Download Selected Backup to Desktop` to copy a remote backup locally.
7. Use `Open Control Window` to open the printer UI (Mainsail/Fluidd) in an embedded web view.

## Windows Build

```powershell
.\scripts\build_windows.ps1
```

Build output:

- `dist\KlippConfig.exe` (self-contained, no console window)
- Desktop shortcut `KlippConfig.lnk` (created by the build script)
- Optional installer in `dist\installer` when Inno Setup (`iscc`) is on `PATH`

