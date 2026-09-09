# JBD Bluetooth BMS

Reads and configures a JBD (Jiabaida, "Xiaoxiang") Bluetooth BMS from a Doover
device. JBD boards sit inside a large share of the LiFePO4 batteries sold with a
Bluetooth app, including iTechworld's iTECH range, so this app also covers those.

Verified on an **iTechworld iTECH120X PRO** (JBD `DP04S007L4S150A`, 4S 150 A)
from a Doovit (Compute Module 4) on 2026-09-09. The battery advertises the JBD
service (`0xFF00`) and answers stock JBD frames with no pairing or password.

## What it shows

- **Pack**: state of charge, voltage, current (positive while charging), power,
  charging / discharging / idle state, remaining capacity, temperature.
- **Cells**: highest, lowest and spread in mV, the full per-cell list, and which
  cells the BMS is balancing.
- **Details**: cycle count, rated capacity, cell count, charge and discharge
  MOSFET state, active protections, BMS model and firmware, manufacture date.
- **Warnings**: no communication, BMS protection active, low state of charge,
  charging or discharging switched off at the BMS.
- **Notifications** through the Doover `notifications` channel when a
  protection trips or the state of charge falls below the configured level.

Every value is also published as a tag, so other apps on the device can use
them (`pack_voltage`, `soc`, `current`, `cell_delta`, `protection_bits`, ...).

## What it can change

All of these are off by default and gated by app config.

- **BMS settings** (`Allow Settings Changes`): pack and cell over/under-voltage
  thresholds and their release points, charge and discharge over-current,
  charge and discharge temperature limits, balance start voltage and window,
  design and cycle capacity, and the cell voltages the BMS maps to
  100 / 80 / 60 / 40 / 20 / 0 % state of charge. Each is a numeric input in the
  **BMS Settings** section; changing one writes that register, reads it back
  and reports the result in **Settings Status**. Writes are range-checked and
  refused if they would put a threshold on the wrong side of its release
  point.
- **Charge / discharge MOSFETs** (`Allow Charge/Discharge Switching`): the
  **Charging** and **Discharging** selectors in **Controls**. Switching
  discharge off cuts power to everything on the battery, which usually
  includes the device running this app.

Read-only registers (delays, shunt resistance, function bits, serial number,
manufacturer, model, barcode) are published in the `settings` tag but not
editable.

## Configuration

| Setting | Default | Notes |
|---|---|---|
| Battery Bluetooth Address | blank | MAC of the BMS, e.g. `A5:C2:37:17:A5:34`. Leave blank, deploy, then press **Scan for batteries** on the dashboard to list JBD devices in range. |
| Poll Interval (seconds) | 10 | |
| Settings Refresh (minutes) | 60 | How often the configuration registers are re-read. **Reload settings from BMS** forces one. |
| Disconnect Between Polls | on | A JBD BMS accepts one Bluetooth connection at a time. Disconnecting between polls leaves room for the phone app; turn it off for faster polling. |
| Allow Settings Changes | off | Gate for writing BMS registers. |
| Allow Charge/Discharge Switching | off | Gate for the MOSFET selectors. |
| Low State of Charge Warning (%) | 20 | Warning indicator and notification threshold. |

## Requirements

The container needs the device's Bluetooth stack, declared in
`deployment/docker-compose.yml` and shipped with the app: host networking, the
host's system D-Bus socket mounted in, and `DBUS_SYSTEM_BUS_ADDRESS` pointing at
it. bleak talks to BlueZ over that socket. Nothing else is needed; the Doovit's
CM4 radio and `bluetoothd` are already there.

Only one central can hold the link. While the app is connected the phone app
cannot connect, and vice versa. If polls start failing, check whether someone
has the iTechworld / Xiaoxiang app open.

## Protocol notes

Frames are `DD <A5 read | 5A write> <reg> <len> <data> <checksum:2> 77` on
service `0xFF00`, notify `0xFF01`, write `0xFF02`. Telemetry comes from
registers `0x03` (basic info), `0x04` (cell voltages) and `0x05` (hardware
version). Configuration registers `0x10`-`0x3F` and `0xA0`-`0xA2` need factory
mode (`0x00` with `56 78`) and are left with `0x01` `00 00`. The register map
in `src/jbd_bms_ble/jbd.py` was checked against the pack's own values; a
couple of widely copied maps online have the block from `0x12` shifted by
four registers.

Settings live in the BMS EEPROM, so the app writes only when a user changes a
value, never on a schedule.

## Development

```bash
uv sync
uv run pytest tests -v
uv run export-config && uv run export-ui   # regenerate doover_config.json schemas
uv run ruff check src tests
doover app run                              # on a Linux box with BlueZ and a battery in range
```

Publish with `doover app publish --profile dv2`. The GitHub workflow builds and
signs the image for amd64 and arm64 on push to `main`.
