# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.1.5] - 2026-02-26

### Summary
Major UX consolidation and workflow reliability release across navigation, printer control, and Files behavior.  
Version `0.1.4` was intentionally skipped to release these grouped improvements as a single cut.

### Highlights
- Replaced the left sidebar workflow with a second top navigation bar and updated command/menu interaction styling.
- Added dedicated `Printers` route behavior with embedded in-app webview flow and SSH setup fallback when no printer is configured.
- Moved operational utilities into dedicated windows/menus (printer connection, discovery, active console) to reduce workflow clutter.
- Removed unstable add-on workflow paths for now and cleaned related user-facing messaging.
- Continued Files route modernization work, including experimental UI support and clearer status messaging patterns.

## [0.1.3] - 2026-02-22

### Summary
Usability and catalog refresh release with expanded Voron 2.4 coverage and streamlined project distribution boundaries.

### Highlights
- Added upstream-ingested Voron 2.4 preset coverage including the new 250 profile and refreshed board mappings.
- Improved configuration workflow UX with vendor/preset defaults, probe/toolhead behavior updates, and preview refinements.
- Added root dependency bootstrap script (`install_dependencies.ps1`) and moved non-runtime release automation/assets to a private sidecar path.

## [0.1.2.1] - 2026-02-21

### Summary
Hotfix release for launch stability and About navigation updates.

### Highlights
- Fixed launch-time preset loading failure by removing non-preset placeholders from the preset index.
- Moved About from a dedicated tab to `Help -> About KlippConfig` while keeping the Main-page About shortcut.
- Updated README and UI wiring tests to match the new About access path.

## [0.1.2] - 2026-02-20

### Summary
Feature update focused on existing machine import and bundle extensibility.

### Highlights
- Added Existing Machine Import with suggestion review plus saved machine profiles for quick reopen/re-apply.
- Expanded config bundle support for boards, toolhead boards, and add-ons loaded from JSON bundles.
- Improved add-on catalog modeling/loading to support richer dynamic add-on profiles.
- Extended release tooling with Discord webhook publishing support.

## [0.1.1] - 2026-02-19

### Summary
Maintenance release focused on workflow polish and release tooling.

### Highlights
- Improved main and modify-existing workflows with stronger SSH management paths.
- Added release/version management tooling with changelog + announcement generation.
- Updated About/README project metadata and community links.

## [0.1.0] - 2026-02-19

### Summary
Initial public baseline with KlippConfig desktop workflows and SSH-based printer management.

### Highlights
- Voron-focused preset generation and validation.
- SSH and Manage Printer workflows for remote file management and backups.
- Windows packaging with self-contained EXE output.

