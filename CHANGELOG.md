# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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

