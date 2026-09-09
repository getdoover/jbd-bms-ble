from pathlib import Path

from pydoover import config


class JbdBmsBleConfig(config.Schema):
    # Explicit name= keeps the JSON keys tidy; the display names would otherwise
    # sanitise to things like "allow_chargedischarge_switching".
    device_address = config.String(
        "Battery Bluetooth Address",
        name="device_address",
        description=(
            "MAC address of the battery's BMS, e.g. A5:C2:37:17:A5:34. "
            "Leave blank, deploy, and press 'Scan for batteries' on the dashboard to find it."
        ),
        default="",
    )
    poll_interval = config.Integer(
        "Poll Interval (seconds)",
        name="poll_interval",
        description="How often to read the battery.",
        default=10,
        minimum=2,
        maximum=600,
    )
    settings_refresh_minutes = config.Integer(
        "Settings Refresh (minutes)",
        name="settings_refresh_minutes",
        description="How often the BMS configuration registers are re-read. They rarely change.",
        default=60,
        minimum=1,
        maximum=1440,
    )
    disconnect_between_polls = config.Boolean(
        "Disconnect Between Polls",
        name="disconnect_between_polls",
        description=(
            "Release the Bluetooth link after each poll so a phone app can still connect to the battery. "
            "Turn off for faster, lower-latency polling."
        ),
        default=True,
    )
    allow_settings_write = config.Boolean(
        "Allow Settings Changes",
        name="allow_settings_write",
        description=(
            "Let the BMS protection thresholds and capacity settings be changed from the dashboard. "
            "Off by default: a wrong value can stop the battery charging or discharging."
        ),
        default=False,
    )
    allow_mosfet_control = config.Boolean(
        "Allow Charge/Discharge Switching",
        name="allow_mosfet_control",
        description=(
            "Let the charge and discharge MOSFETs be switched from the dashboard. "
            "Switching discharge off cuts power to everything on the battery, including the device running this app."
        ),
        default=False,
    )
    low_soc_warning = config.Integer(
        "Low State of Charge Warning (%)",
        name="low_soc_warning",
        description="Show a warning and send a notification when the state of charge drops below this.",
        default=20,
        minimum=0,
        maximum=100,
    )


def export():
    JbdBmsBleConfig.export(
        Path(__file__).parents[2] / "doover_config.json", "jbd_bms_ble"
    )


if __name__ == "__main__":
    export()
