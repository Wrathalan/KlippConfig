# KlippConfig

## IMPORTANT NOTICE

This project is a work in progress. Expect broken features, incomplete workflows, and frequent changes while development is ongoing.

KlippConfig is a Windows-first desktop Klipper configurator built with Python and Qt.
It generates full Klipper config packs from curated Voron presets.

## Features

- Voron-only preset catalog
- Main tab launcher with quick workflow entry points (`New Firmware`, `Modify Existing`, `Connect/Manage Printer`, `About`)
- Guided wizard for board, toolhead, dimensions, and core hardware choices
- Optional CAN toolhead board selection and `toolhead.cfg` generation
- Includes LDO Nitehawk toolhead board options
- Expanded mainboard catalog with board pin aliases and board layout output
- LED control settings (pin, chain count, color order, initial color) with generated `leds.cfg`
- Add-on packs including AMS-style and other multi-material systems
- Files tab with raw/form `.cfg` editing and section override editor
- Firmware tools for existing `.cfg` files: one-click refactor and validation
- Live conflict validation banner that updates immediately on config changes
- Live validation with blocking errors and warnings shown in `Files`
- `Section Overrides` and `Validation Findings` are collapsed by default with unresolved-issues notice
- Export as ZIP or folder from the bottom of `Files`
- Open existing `.cfg` files directly in-app
- Footer device-health icon (red/green) reflects current SSH connection health
- Live SSH deploy to Klipper hosts (test connection, upload, optional restart)
- Saved SSH connection profiles (named reconnect presets)
- SSH and Manage Printer console logs are collapsible and collapsed by default
- Custom app icon is bundled for runtime window + built EXE/shortcut
- Detailed `About` tab with mission quote and creator icon
- Network scanner to discover likely Klipper printers on your LAN
- Dedicated `Modify Existing` remote workflow (connect -> open cfg -> refactor/validate -> upload with backup -> restart test)
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

Alternative launcher:

```powershell
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
5. Use `Connect`.
6. Set `Connection name` and keep `Save on successful connect` enabled to auto-save reconnect profiles.
7. Use `Saved` `Load/Save/Delete` controls for quick reconnect.
8. Use `Deploy Generated Pack` to upload the current validated pack.
9. Optional: enable restart and provide the restart command.

## Main Tab

1. Open the `Main` tab (first tab).
2. Click `New Firmware` to jump to `Configuration` (does not reset form state).
3. Click `Modify Existing` to open the dedicated remote edit/upload workflow.
4. Click `Connect/Manage Printer` to jump to `SSH`.
5. Click `About` to jump to the about page.

## Modify Existing Tab

1. Open `Modify Existing`.
2. Click `Connect` (reuses SSH credentials from `SSH` tab).
3. Set/confirm `Remote .cfg path` and click `Open Remote .cfg`.
4. Edit the file, then use `Refactor` and `Validate`.
5. Click `Upload` to write changes (always creates backup first using `Backup root`).
6. Click `Test Restart` to run the restart command and review command output in the tab log.

## Manage Printer Tab

1. Open the `Manage Printer` tab.
2. The connected printer name appears above the host field after a successful SSH connect.
3. Set target host (or click `Use SSH Host` / choose from discovery first).
4. Click `Refresh Files`, then browse/edit from the remote directory tree (double-click folders/files or use `Open Selected / Enter Folder`).
5. Edit the file and click `Save Current File`.
6. Use `Create Backup` / `Refresh Backups` / `Restore Selected Backup` for backup workflows.
7. Use `Download Selected Backup to Desktop` to copy a remote backup locally.
8. Use `Open Control Window` to open the printer UI (Mainsail/Fluidd) in an embedded web view.
9. Use `Refactor Current .cfg` and `Validate Current .cfg` for existing remote firmware files.

## Files Tab (Existing Firmware)

1. Open a local `.cfg` file from `Files -> Open Local .cfg` (or load a generated file).
2. Click `Refactor Current .cfg` to normalize section formatting and key/value style.
3. Click `Validate Current .cfg` to run firmware-specific checks (syntax/common sections/duplicate keys/numeric sanity).
4. Review the firmware validation status banner in `Files`.

## About Tab

1. Open the `About` tab for mission/context and platform overview.
2. Includes the quote about easier "accasability" for controlling 3D printers and firmware.
3. Shows the creator icon from `assets\creator.ico`.

## Windows Build

```powershell
.\scripts\build_windows.ps1
```

Build output:

- `dist\KlippConfig.exe` (self-contained, no console window)
- Desktop shortcut `KlippConfig.lnk` (created by the build script)
- Optional installer in `dist\installer` when Inno Setup (`iscc`) is on `PATH`
