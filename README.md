# KlippConfig

<p>
  <img src="assets/icon.ico" alt="KlippConfig app icon" width="96" height="96" />
</p>

## IMPORTANT NOTICE

This project is a work in progress. Expect broken features, incomplete workflows, and frequent changes while development is ongoing.

KlippConfig is a Windows-first desktop Klipper configurator built with Python and Qt.
It generates full Klipper config packs from curated Voron presets.

## Features

- Voron-only preset catalog, more to come
- Main tab launcher with quick workflow entry points (`New Firmware`, `Modify Existing`, `Connect/Manage Printer`, `About`)
- `Help -> About KlippConfig` menu entry in the command bar
- Existing Machine Import from local ZIP/folder with auto-detected mappings and review/apply suggestions
- Guided wizard for board, toolhead, dimensions, and core hardware choices
- Optional CAN toolhead board selection and `toolhead.cfg` generation
- Includes LDO Nitehawk toolhead board options
- Expanded mainboard catalog with board pin aliases and board layout output
- Config bundle system for drop-in board/toolhead/add-on support (`%APPDATA%\KlippConfig\bundles`)
- LED control settings (pin, chain count, color order, initial color) with generated `leds.cfg`
- Add-on packs including AMS-style systems plus AFC/KAMP/StealthBurner LEDs/Timelapse mappings
- Files tab with raw/form `.cfg` editing and section override editor
- Firmware tools for existing `.cfg` files: one-click refactor and role-aware validation
- Include-graph validation from imported root configs (wildcards, unresolved includes, cycle/conflict detection)
- Live conflict validation banner that updates immediately on config changes
- Live validation with blocking errors and warnings shown in `Files`
- `Section Overrides` and `Validation Findings` are collapsed by default with unresolved-issues notice
- Export as ZIP or folder from `File` menu
- Open existing `.cfg` files directly in-app
- Footer device-health icon (red/green) reflects current SSH connection health
- Live SSH deploy to Klipper hosts (connect, upload, optional restart)
- `Tools -> Printer Connection` menu for connect/open remote/deploy/discovery actions
- Saved SSH connection profiles (named reconnect presets)
- Saved machine import profiles for quick reopen and re-apply
- SSH and Manage Printer console logs are collapsible and collapsed by default
- Network scanner to discover likely Klipper printers on your LAN
- Dedicated `Modify Existing` remote workflow (connect -> open cfg -> refactor/validate -> upload with backup -> restart test)
- `Manage Printer` tab for direct remote file editing, backups, and restore operations
- Embedded printer control window (web view) from `Manage Printer` for live manual controls
- LAN Only

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

`run_klippconfig.ps1` always rebuilds `dist\KlippConfig.exe` first, then launches the EXE.


## Tests

```powershell
pytest
```


## SSH Tab

1. Open the `SSH` tab.
2. Enter or confirm SSH host, port, username, and auth credentials.
3. Set remote Klipper config directory (default `~/printer_data/config`).
4. Use `Tools -> Printer Connection -> Connect`.
5. Optionally run `Tools -> Printer Connection -> Scan for Printers` and then `Use Selected Host`.
6. Set `Connection name` and keep `Save on successful connect` enabled to auto-save reconnect profiles.
7. Use `Saved` `Load/Save/Delete` controls for quick reconnect.
8. Use `Tools -> Printer Connection -> Open Remote File` to pull a remote cfg into `Files`.
9. Use `Tools -> Printer Connection -> Deploy Generated Pack` to upload the current validated pack.
10. Optional: enable restart and provide the restart command.

## Main Tab

1. Open the `Main` tab (first tab).
2. Click `New Firmware` to jump to `Configuration` (does not reset form state).
3. Click `Modify Existing` to open the dedicated remote edit/upload workflow.
4. Click `Connect/Manage Printer` to jump to `SSH`.
5. Click `About` to open the About window (same as `Help -> About KlippConfig`).

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

## Config Bundles (Boards/Add-ons)

Use bundles to add support without editing Python code:

1. Create files under `%APPDATA%\KlippConfig\bundles`:
1. `boards\*.json`
2. `toolhead_boards\*.json`
3. `addons\*.json`
4. `templates\...` for add-on templates
2. Launch/restart KlippConfig.
3. New boards/toolhead boards appear in `Configuration`.
4. New add-ons appear in `Configuration -> Add-ons` when compatible with the preset family.

Reference examples and format docs:

- `app/bundles/README.md`
- `app/bundles/examples/boards/my_custom_mainboard.json`
- `app/bundles/examples/toolhead_boards/my_custom_toolhead.json`
- `app/bundles/examples/addons/chamber_heater.json`

## Files Tab (Existing Firmware)

1. Open a local `.cfg` file from `File -> Open .cfg File...` (or load a generated file in `Files`).
2. Click `Refactor Current .cfg` to normalize section formatting and key/value style.
3. Click `Validate Current .cfg` to run role-aware firmware checks (syntax/duplicate keys/numeric sanity). Imported machine mode validates the full include graph from root.
4. Review the firmware validation status banner in `Files`.

## Existing Machine Import

1. Click `File -> Import Existing Machine...`.
2. Choose a local config ZIP or folder.
3. Review detected suggestions in `Files -> Import Review` (`field/value/confidence/reason/source`).
4. Apply selected suggestions (high-confidence suggestions are preselected by default).
5. Optionally save the imported profile for quick reopen using `Save Machine Profile`.

## About (Help Menu)

1. Open `Help -> About KlippConfig` from the command bar.
2. Includes the quote about easier "accessibility" for controlling 3D printers and firmware.
3. Shows the creator icon from `assets\creator.ico`.
4. Includes the Discord community link: https://discord.gg/4CthQzS7Qy


## Windows Build

```powershell
.\scripts\build_windows.ps1
```

Build output:

- `dist\KlippConfig.exe` (self-contained, no console window)
- Desktop shortcut `KlippConfig.lnk` (created by the build script)
- Optional installer in `dist\installer` when Inno Setup (`iscc`) is on `PATH`

