# KlippConfig Config Bundles

Config bundles let you add board, toolhead board, and add-on support without editing Python code.

## Bundle roots

KlippConfig scans these roots in order:

1. `KLIPPCONFIG_BUNDLE_DIRS` (optional, multiple paths separated by `;` on Windows)
2. Built-in: `app/bundles`
3. User: `%APPDATA%\KlippConfig\bundles`

Each root can contain:

- `boards/*.json`
- `toolhead_boards/*.json`
- `addons/*.json`
- `templates/...` (Jinja templates used by add-ons)

Later roots override earlier ones by matching `id`.

## Board bundle JSON

```json
{
  "id": "my_custom_mainboard",
  "label": "My Custom Mainboard",
  "mcu": "stm32f446xx",
  "serial_hint": "/dev/serial/by-id/usb-My_Custom_Mainboard",
  "pins": {
    "stepper_x_step": "PB13",
    "stepper_x_dir": "PB12",
    "stepper_x_enable": "PB14",
    "heater_hotend": "PA2",
    "temp_hotend": "PF4"
  },
  "layout": {
    "Stepper Drivers": ["X", "Y", "Z", "E0"],
    "Heaters": ["HE0", "BED"]
  }
}
```

## Toolhead board bundle JSON

```json
{
  "id": "my_custom_toolhead",
  "label": "My Custom Toolhead",
  "mcu": "rp2040",
  "transport": "can",
  "serial_hint": "canbus_uuid: replace-with-uuid",
  "pins": {
    "extruder_step": "toolhead:EXT_STEP",
    "extruder_dir": "toolhead:EXT_DIR",
    "extruder_enable": "toolhead:EXT_EN",
    "heater_hotend": "toolhead:HE0",
    "temp_hotend": "toolhead:TH0"
  },
  "layout": {
    "Motor and Heater": ["EXT_STEP", "EXT_DIR", "EXT_EN", "HE0"]
  }
}
```

`transport` is optional and can be `can` or `usb`.

- `can`: UI shows it in the CAN toolhead board list and validates `canbus_uuid`.
- `usb`: UI shows it in the USB toolhead board list and renders `serial:` in `toolhead.cfg` using `serial_hint`.

## Add-on bundle JSON

```json
{
  "id": "chamber_heater",
  "label": "Chamber Heater",
  "template": "addons/chamber_heater.cfg.j2",
  "description": "Basic heater/fan scaffold for enclosed builds.",
  "multi_material": false,
  "recommends_toolhead": false,
  "supported_families": ["voron"],
  "include_files": ["chamber_heater.cfg"],
  "package_templates": {
    "chamber_heater.cfg": "addons/chamber_heater.cfg.j2"
  },
  "output_files": ["chamber_heater.cfg"],
  "learned": false
}
```

Add-on templates are loaded from bundle `templates` folders, for example:

- `%APPDATA%\KlippConfig\bundles\templates\addons\chamber_heater.cfg.j2`

For learned/source-tree add-on packages:

- `include_files` defines include targets that should be injected into root config output.
- `package_templates` maps output config path -> template path.
- `output_files` is optional metadata for UI/reporting.
- `learned: true` marks bundles generated from imported machine configs.

You can generate these automatically via:

- `Tools -> Learn Add-ons from Imported Config`

## Notes

- Boards/toolheads from bundles are available in Configuration even if not in preset curated lists.
- Non-curated board selections are allowed but flagged with warnings for extra validation.
- Add-ons can be enabled by preset list or bundle compatibility (`supported_families` / `supported_presets`).
