# Files UI Experiment v1 (Material 3, Desktop Qt)

## Goal
Redesign only the `Files` route with Material 3-inspired desktop patterns, while preserving existing KlippConfig behavior and workflows.

## Experiment Toggle
- Setting key: `ui/experiments/files_material_v1_enabled`
- Default: `false`
- Menu path: `View -> Experiments -> Files UI v1`
- Notes:
  - Toggle persists immediately.
  - Rebuild is applied on next app launch.

## Figma File Structure
Create Figma file: `KlippConfig - Files Experiment v1`

Pages (exact order):
1. `00_Foundations`
2. `01_Components`
3. `02_Files_Layouts`
4. `03_Prototype_Flows`
5. `04_Handoff`

## Foundations (00_Foundations)
- Color roles (dark + light): app surface, control surface, borders, text, selection, semantic states.
- Typography scale: titles, section headers, body, micro labels.
- Spacing scale: 4/8 grid.
- Shape/elevation: radius and emphasis hierarchy.
- Motion guidance: expand/collapse and status feedback timing.

## Components (01_Components)
- Files top command strip (primary + tonal actions).
- Button variants (filled/tonal/outlined/text).
- Input and selection fields.
- Tabs (`Raw`, `Form`) for file editing mode.
- Tables (Validation and Import Review).
- List states for generated/imported files.
- Status chips (ready, warning, blocking, unsaved).
- Collapsible section headers.
- Toast and inline status patterns.

## Files Layout Variants (02_Files_Layouts)
- Empty state (`No file loaded`).
- Loaded `.cfg` state.
- Unsaved changes state.
- Warnings-only state.
- Blocking/error-heavy state.

## Prototype Tasks (03_Prototype_Flows)
1. Open and select a file.
2. Switch Raw/Form.
3. Edit and apply form changes.
4. Run validation.
5. Resolve warning and blocking issues.
6. Use Import Review and Section Overrides.

## Handoff Requirements (04_Handoff)
- Redlines and spacing specs.
- Component usage matrix.
- Token-to-Qt style mapping.
- Interaction/state matrix.
- Plain-English copy deck for warning/error/help text.

## Qt Token Mapping (Current Implementation)
Token source: `app/ui/design_tokens.py`

Core mappings:
- App/base styles: `build_base_stylesheet(mode)`
- Files experiment overlay: `build_files_material_stylesheet(mode)`
- Mode switch: `MainWindow._apply_theme_mode`

Files experiment selectors:
- `QWidget#files_tab_material_v1`
- `QWidget#files_top_command_bar`
- `QPushButton#files_primary_action`
- `QPushButton#files_tonal_action`
- `QLabel#files_chip[chipSeverity=...]`
- `QListWidget#files_generated_list`
- `QTableWidget#files_validation_table`
- `QTableWidget#files_import_review_table`
- `QTabWidget#files_view_tabs`

## Runtime Behaviors in v1
- Validation and guidance uses plain-English status copy in the Files route.
- Status chips summarize:
  - Primary state
  - Blocking count
  - Warning count
  - Dirty/saved state
- Existing Files operations and signatures remain unchanged.

## QA Focus
- Functional parity between classic and experimental Files routes.
- Theme parity in dark/light modes.
- Context menu and command-bar action continuity.
- No regression in validation/refactor/apply flows.
