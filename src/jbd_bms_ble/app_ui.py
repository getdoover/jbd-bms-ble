from pathlib import Path

from pydoover import ui

from . import jbd
from .app_tags import JbdBmsBleTags as Tags


def _setting_inputs() -> list[ui.FloatInput]:
    """One numeric input per writable BMS register, named by its key so the
    application can attach a handler per register."""
    inputs = []
    for spec in jbd.SETTINGS:
        if not spec.writable:
            continue
        inputs.append(
            ui.FloatInput(
                spec.label,
                name=spec.key,
                min_val=spec.minimum,
                max_val=spec.maximum,
                units=spec.unit,
                requires_confirm=True,
            )
        )
    return inputs


class JbdBmsBleUI(ui.UI):
    soc = ui.NumericVariable(
        "State of Charge",
        name="soc",
        value=Tags.soc,
        units="%",
        precision=0,
        ranges=[
            ui.Range("Low", 0, 20, colour=ui.Colour.red),
            ui.Range("Medium", 20, 60, colour=ui.Colour.yellow),
            ui.Range("Good", 60, 100, colour=ui.Colour.green),
        ],
    )
    pack_voltage = ui.NumericVariable(
        "Battery Voltage",
        name="pack_voltage",
        value=Tags.pack_voltage,
        units="V",
        precision=2,
    )
    current = ui.NumericVariable(
        "Current",
        name="current",
        value=Tags.current,
        units="A",
        precision=2,
        help_str="Positive while charging, negative while discharging.",
    )
    power = ui.NumericVariable(
        "Power",
        name="power",
        value=Tags.power,
        units="W",
        precision=0,
    )
    charge_state = ui.TextVariable(
        "State", name="charge_state", value=Tags.charge_state
    )
    remaining_ah = ui.NumericVariable(
        "Remaining Capacity",
        name="remaining_ah",
        value=Tags.remaining_ah,
        units="Ah",
        precision=1,
    )
    temperature = ui.NumericVariable(
        "Battery Temperature",
        name="temperature",
        value=Tags.temperature,
        units="°C",
        precision=1,
        ranges=[
            ui.Range("Cold", -20, 0, colour=ui.Colour.blue),
            ui.Range("Normal", 0, 45, colour=ui.Colour.green),
            ui.Range("Hot", 45, 80, colour=ui.Colour.red),
        ],
    )

    comms_warning = ui.WarningIndicator(
        "No communication with battery",
        name="comms_warning",
        hidden=Tags.comms_ok,
    )
    protection_warning = ui.WarningIndicator(
        "BMS protection active",
        name="protection_warning",
        hidden=Tags.protection_clear,
    )
    low_soc_warning = ui.WarningIndicator(
        "Battery state of charge is low",
        name="low_soc_warning",
        hidden=Tags.soc_ok,
    )
    charge_off_warning = ui.WarningIndicator(
        "Charging is switched off at the BMS",
        name="charge_off_warning",
        hidden=Tags.charge_fet,
    )
    discharge_off_warning = ui.WarningIndicator(
        "Discharging is switched off at the BMS",
        name="discharge_off_warning",
        hidden=Tags.discharge_fet,
    )

    cells = ui.Submodule(
        "Cells",
        name="cells",
        children=[
            ui.NumericVariable(
                "Highest Cell",
                name="cell_max",
                value=Tags.cell_max,
                units="mV",
                precision=0,
            ),
            ui.NumericVariable(
                "Lowest Cell",
                name="cell_min",
                value=Tags.cell_min,
                units="mV",
                precision=0,
            ),
            ui.NumericVariable(
                "Cell Spread",
                name="cell_delta",
                value=Tags.cell_delta,
                units="mV",
                precision=0,
                ranges=[
                    ui.Range("Balanced", 0, 30, colour=ui.Colour.green),
                    ui.Range("Drifting", 30, 80, colour=ui.Colour.yellow),
                    ui.Range("Unbalanced", 80, 500, colour=ui.Colour.red),
                ],
            ),
            ui.TextVariable("Cell Voltages", name="cells_text", value=Tags.cells_text),
            ui.TextVariable("Balancing", name="balancing", value=Tags.balancing),
        ],
    )

    details = ui.Submodule(
        "Details",
        name="details",
        is_collapsed=True,
        children=[
            ui.NumericVariable("Cycles", name="cycles", value=Tags.cycles, precision=0),
            ui.NumericVariable(
                "Rated Capacity",
                name="nominal_ah",
                value=Tags.nominal_ah,
                units="Ah",
                precision=1,
            ),
            ui.NumericVariable(
                "Cell Count", name="cell_count", value=Tags.cell_count, precision=0
            ),
            ui.BooleanVariable(
                "Charge MOSFET On", name="charge_fet", value=Tags.charge_fet
            ),
            ui.BooleanVariable(
                "Discharge MOSFET On", name="discharge_fet", value=Tags.discharge_fet
            ),
            ui.TextVariable(
                "Protection", name="protection_text", value=Tags.protection_text
            ),
            ui.TextVariable(
                "BMS Model", name="hardware_version", value=Tags.hardware_version
            ),
            ui.TextVariable(
                "BMS Firmware", name="software_version", value=Tags.software_version
            ),
            ui.TextVariable(
                "Manufactured", name="manufacture_date", value=Tags.manufacture_date
            ),
            ui.TextVariable(
                "Bluetooth Address", name="device_address", value=Tags.device_address
            ),
            ui.TextVariable(
                "Last Read", name="last_read_text", value=Tags.last_read_text
            ),
        ],
    )

    controls = ui.Submodule(
        "Controls",
        name="controls",
        is_collapsed=True,
        children=[
            ui.Select(
                "Charging",
                name="charge_switch",
                options=[ui.Option("On"), ui.Option("Off")],
                requires_confirm=True,
                help_str="Switches the BMS charge MOSFET. Needs 'Allow Charge/Discharge Switching' in the app config.",
            ),
            ui.Select(
                "Discharging",
                name="discharge_switch",
                options=[ui.Option("On"), ui.Option("Off")],
                requires_confirm=True,
                help_str="Switches the BMS discharge MOSFET. Off cuts power to every load on the battery.",
            ),
            ui.Button("Scan for batteries", name="scan_batteries"),
            ui.TextVariable(
                "Scan Results", name="scan_results", value=Tags.scan_results
            ),
            ui.Button("Reload settings from BMS", name="reload_settings"),
        ],
    )

    settings = ui.Submodule(
        "BMS Settings",
        name="settings",
        is_collapsed=True,
        children=[
            ui.TextVariable(
                "Settings Status", name="settings_status", value=Tags.settings_status
            ),
            ui.TextVariable(
                "Settings Read",
                name="settings_read_text",
                value=Tags.settings_read_text,
            ),
            *_setting_inputs(),
        ],
    )


def export():
    JbdBmsBleUI(None, None, None).export(
        Path(__file__).parents[2] / "doover_config.json",
        "jbd_bms_ble",
    )


if __name__ == "__main__":
    export()
