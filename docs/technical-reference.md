# KlippConfig Technical Reference

This document keeps the full technical breakdown that used to live in `README.md`.
If you are new to Klipper, start with the main `README.md` first.

## Feature Breakdown

- Voron-only preset catalog, more to come
- Main tab launcher with quick workflow entry points (`New Firmware`, `Modify Existing`, `Connect/Manage Printer`, `About`)
- `Help -> About KlippConfig` menu entry in the command bar
- Existing Machine Import from local ZIP/folder with auto-detected mappings and review/apply suggestions
- Project schema v2 with automatic migration from legacy saved projects
- Typed machine-attribute capture (printer limits, MCU map, steppers/drivers, probe/leveling, thermal/fan/sensor, resonance)
- Source-tree reconstruction support via imported section maps (`output_layout=source_tree`)
- Compile-time parity gating against imported configs (serial/canbus UUID differences are allowlisted)
- Guided wizard for board, toolhead, dimensions, and core hardware choices
- Separate USB/CAN toolhead board selection and `toolhead.cfg` generation
- Includes LDO Nitehawk toolhead board options
- Expanded mainboard catalog with board pin aliases and board layout output
- Config bundle system for drop-in board/toolhead/add-on support (`%APPDATA%\KlippConfig\bundles`)
- `Tools -> Learn Add-ons from Imported Config` to ingest KAMP/StealthBurner LEDs/Timelapse into user bundles
- LED control settings (pin, chain count, color order, initial color) with generated `leds.cfg`
- Add-on packs including AMS-style systems plus KAMP/StealthBurner LEDs/Timelapse mappings
- Files tab with raw/form `.cfg` editing and section override editor
- Firmware tools for existing `.cfg` files: one-click refactor and role-aware validation
- Include-graph validation from imported root configs (wildcards, unresolved includes, cycle/conflict detection)
- Live conflict and warning toast notifications in the lower-right corner
- Live validation with blocking errors and warnings shown in `Files`
- `Section Overrides` and `Validation Findings` are collapsed by default with unresolved-issues notice
- Opt-in experimental Material-style `Files` UI (`View -> Experiments -> Files UI v1`)
- Tokenized Qt theme foundation for dark/light modes (`app/ui/design_tokens.py`)
- Export as ZIP or folder from `File` menu
- Open existing `.cfg` files directly in-app
- Footer device-health icon (red/green) reflects current SSH connection health
- Live SSH deploy to Klipper hosts (connect, upload, optional restart)
- `Tools -> Printer Connection` menu for connect/open remote/explore config/deploy/discovery actions
- Saved SSH connection profiles (named reconnect presets)
- Saved machine import profiles for quick reopen and re-apply
- SSH and Manage Printer console logs are collapsible and collapsed by default
- Network scanner to discover likely Klipper printers on your LAN
- Dedicated `Modify Existing` remote workflow (connect -> open cfg -> refactor/validate -> upload with backup -> restart test)
- `Manage Printer` tab for direct remote file editing, backups, and restore operations
- Embedded printer control window (web view) from `Manage Printer` for live manual controls
- LAN only

## Main Tab

1. Open the `Main` tab (first tab).
2. Click `New Firmware` to jump to `Configuration` (does not reset form state).
3. Click `Modify Existing` to open the dedicated remote edit/upload workflow.
4. Click `Connect/Manage Printer` to jump to `SSH`.
5. Click `About` to open the About window (same as `Help -> About KlippConfig`).

## SSH Tab

1. Open the `SSH` tab.
2. Enter or confirm SSH host, port, username, and auth credentials.
3. Set remote Klipper config directory (default `~/printer_data/config`).
4. Use `Tools -> Printer Connection -> Connect`.
5. Optionally run `Tools -> Printer Connection -> Scan for Printers` and then `Use Selected Host`.
6. Set `Connection name` and keep `Save on successful connect` enabled to auto-save reconnect profiles.
7. Use `Saved` `Load/Save/Delete` controls for quick reconnect.
8. Use `Tools -> Printer Connection -> Open Remote File` to pull a remote cfg into `Files`.
9. Use `Tools -> Printer Connection -> Explore Config Directory` (or the SSH-tab button) to open the connected printer's config directory in `Manage Printer`.
10. Use `Tools -> Printer Connection -> Deploy Generated Pack` to upload the current validated pack.
11. Optional: enable restart and provide the restart command.

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
3. Set target host (or click `Use SSH Host` or choose from discovery first).
4. Click `Refresh Files`, then browse/edit from the remote directory tree (double-click folders/files or use `Open Selected / Enter Folder`).
5. Edit the file and click `Save Current File`.
6. Use `Create Backup`, `Refresh Backups`, and `Restore Selected Backup` for backup workflows.
7. Use `Download Selected Backup to Desktop` to copy a remote backup locally.
8. Use `Open Control Window` to open the printer UI (Mainsail/Fluidd) in an embedded web view.
9. Use `Refactor Current .cfg` and `Validate Current .cfg` for existing remote firmware files.

## Files Tab (Existing Firmware)

1. Open a local `.cfg` file from `File -> Open .cfg File...` (or load a generated file in `Files`).
2. Click `Refactor Current .cfg` to normalize section formatting and key/value style.
3. Click `Validate Current .cfg` to run role-aware firmware checks (syntax/duplicate keys/numeric sanity). Imported machine mode validates the full include graph from root.
4. Review validation status and findings in `Files`.

## Existing Machine Import

1. Click `File -> Import Existing Machine...`.
2. Choose a local config ZIP or folder.
3. Review detected suggestions in `Files -> Import Review` (`field/value/confidence/reason/source`).
4. Apply selected suggestions (high-confidence suggestions are preselected by default). This applies:
- schema v2 settings (`schema_version`, `output_layout`)
- machine attributes
- add-on package metadata
- source `section_map` for source-tree reconstruction
5. Optionally run `Tools -> Learn Add-ons from Imported Config` to generate user bundle templates from imported add-on files.
6. Optionally save the imported profile for quick reopen using `Save Machine Profile`.

## Config Bundles (Boards/Add-ons)

Use bundles to add support without editing Python code:

1. Create files under `%APPDATA%\KlippConfig\bundles`:
- `boards\*.json`
- `toolhead_boards\*.json`
- `addons\*.json`
- `templates\...` for add-on templates
2. Launch/restart KlippConfig.
3. New boards/toolhead boards appear in `Configuration`.
4. New add-ons appear in `Configuration -> Add-ons` when compatible with the preset family.

Reference examples and format docs:

- `app/bundles/README.md`
- `app/bundles/examples/boards/my_custom_mainboard.json`
- `app/bundles/examples/toolhead_boards/my_custom_toolhead.json`
- `app/bundles/examples/addons/chamber_heater.json`

## Project Schema v2 (Auto Migration)

- Saved legacy project files are auto-migrated to schema v2 when loaded.
- v2 introduces:
- `schema_version: 2`
- `output_layout` (`source_tree` or `modular`)
- `machine_attributes`
- `addon_configs`
- `section_map`
- When re-saved, projects are written as schema v2 automatically.

## About (Help Menu)

1. Open `Help -> About KlippConfig` from the command bar.
2. Includes the quote about easier accessibility for controlling 3D printers and firmware.
3. Shows the creator icon from `assets\creator.ico`.
4. Includes the Discord community link: https://discord.gg/4CthQzS7Qy

## Testing and Manual Smoke

- Run tests:

```powershell
pytest
```

- Manual release smoke checklist: `docs/manual-smoke-checklist.md`
- Repeatable demo pack fixture: `tests/fixtures/demo_config_pack`

## Windows Build

```powershell
& "$env:APPDATA\KlippConfig\private-sidecar\scripts\build_windows.ps1"
```

Build output:

- `dist\KlippConfig.exe` (self-contained, no console window)
- Desktop shortcut `KlippConfig.lnk` (created by the build script)
- Optional installer in `dist\installer` when Inno Setup (`iscc`) is on `PATH`
