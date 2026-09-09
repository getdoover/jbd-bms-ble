# JBD Bluetooth BMS app

Doover device app (pydoover 1.x declarative API) that talks to a JBD / Xiaoxiang
Bluetooth BMS with bleak. Read `README.md` first.

## Commands

```bash
uv run pytest tests -v          # protocol tests use a frame captured from a real pack
uv run export-config             # writes config_schema into doover_config.json
uv run export-ui                 # writes ui_schema into doover_config.json (required to publish)
uv run ruff check src tests
doover app run                   # needs BlueZ + a battery in range; no simulator exists
```

## Layout

```
src/jbd_bms_ble/
  jbd.py          # protocol: frames, checksum, parsers, register map, encode/decode/validate. No I/O.
  ble.py          # JbdBle: bleak transport, notification reassembly, factory-mode sessions, scan.
  app_config.py   # config schema (address, poll interval, write gates, low-SOC threshold)
  app_tags.py     # every published value
  app_ui.py       # dashboard; one FloatInput per writable register, named by RegisterSpec.key
  application.py  # poll loop, publish, notifications, UI handlers (one per writable register, generated)
deployment/docker-compose.yml   # host network + D-Bus socket mount; shipped with the app on publish
```

## Rules that matter here

- `jbd.py` stays pure. Anything that touches bleak goes in `ble.py`.
- `JbdBle._lock` is held for a whole logical operation so a UI write can never
  land inside another factory-mode session. Do not add a public method that
  calls `_read`/`_write` without taking the lock.
- Register addresses in `jbd.SETTINGS` were verified against a real pack.
  Change one only with a read-back from hardware.
- Writes to the BMS are EEPROM writes. Never write on a schedule.
- `discharge_over_current` is stored negative by the BMS; `encode`/`decode`
  handle the sign so users always see a positive amp figure.
- Every writable `RegisterSpec` must have `minimum`/`maximum`; a test enforces it.
- Handlers for setting inputs are generated at import time from
  `jbd.WRITABLE_KEYS`; the FloatInput `name` must equal the register key.
