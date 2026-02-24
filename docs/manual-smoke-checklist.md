# KlippConfig Manual Smoke Checklist

Use this checklist before release candidates and hotfix EXE drops.

## Setup

1. Launch app from source and packaged EXE.
2. Confirm no startup exceptions.
3. Confirm expected app version in window title.

## Core Workflow

1. Connect:
   - Open `Printer -> Connect`.
   - Connect using current SSH fields.
   - Confirm status updates to connected.
2. Open remote config:
   - Run `Configuration -> Open Remote Config`.
   - Confirm file loads in `Files`.
3. Edit:
   - Modify one scalar field in raw view.
   - Modify one field in form view.
   - Apply changes.
4. Validate:
   - Run `Configuration -> Validate Current`.
   - Confirm validation result is visible and non-silent.
5. Generate:
   - Run `Configuration -> Compile / Generate`.
   - Confirm pack appears and `printer.cfg` preview is readable.
6. Upload:
   - Run `Tools -> Advanced Settings -> Deploy Generated Pack`.
   - Confirm upload logs and status.
7. Restart:
   - Run `Printer -> Restart Klipper`.
   - Confirm restart log output and health status refresh.

## Backup + Recovery

1. Open `Tools -> Backup Manager`.
2. Create backup.
3. Refresh backups list.
4. Download selected backup.
5. Restore selected backup to verify flow.

## Discovery + Files

1. Run `Printer -> Scan Network`.
2. Use selected host.
3. Open local config via `Configuration -> Open Local Config`.
4. Explore config directory via `Tools -> Explore Config Directory`.

## Exit Criteria

1. No blocking regressions in connect/validate/generate/upload/restart.
2. No unhandled exception dialogs.
3. Logs include connect, validate, generate, upload, and restart events.
