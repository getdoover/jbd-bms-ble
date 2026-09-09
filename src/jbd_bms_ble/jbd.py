"""JBD (Jiabaida / "Xiaoxiang") BMS protocol, as carried over Bluetooth LE.

Pure functions and data only: no I/O lives here, so everything is unit
testable against captured frames.

Frame layout (both directions)::

    DD  <kind>  <reg>  <len>  <data ...>  <checksum:2>  77

A request has ``kind`` 0xA5 (read) or 0x5A (write). The reply puts the
register number where ``kind`` was and a status byte where ``reg`` was
(0x00 OK, 0x80 error). The checksum is the two's complement of the sum of
every byte between ``kind``/``reg`` and the checksum itself.

Register numbers and scaling were verified on 2026-09-09 against an
iTechworld iTECH120X PRO (JBD ``DP04S007L4S150A``), which reads and writes
exactly like a stock JBD board.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

START = 0xDD
END = 0x77
CMD_READ = 0xA5
CMD_WRITE = 0x5A

STATUS_OK = 0x00

REG_BASIC_INFO = 0x03
REG_CELL_VOLTAGES = 0x04
REG_HARDWARE_VERSION = 0x05
REG_FACTORY_ENTER = 0x00
REG_FACTORY_EXIT = 0x01
REG_MOSFET_CONTROL = 0xE1

FACTORY_ENTER_PAYLOAD = b"\x56\x78"
# 0x00 0x00 leaves settings as written; 0x28 0x28 would also reset error counters.
FACTORY_EXIT_PAYLOAD = b"\x00\x00"

PROTECTION_FLAGS = (
    "cell over-voltage",
    "cell under-voltage",
    "pack over-voltage",
    "pack under-voltage",
    "charge over-temperature",
    "charge under-temperature",
    "discharge over-temperature",
    "discharge under-temperature",
    "charge over-current",
    "discharge over-current",
    "short circuit",
    "IC error",
    "MOSFET software lock",
)


class JbdError(Exception):
    """A malformed frame or a BMS-side rejection."""


def checksum(body: bytes) -> int:
    return (0x10000 - sum(body)) & 0xFFFF


def build_frame(kind: int, reg: int, data: bytes = b"") -> bytes:
    body = bytes([reg, len(data)]) + data
    return (
        bytes([START, kind]) + body + struct.pack(">H", checksum(body)) + bytes([END])
    )


def frame_complete(buf: bytes | bytearray) -> bool:
    """True once ``buf`` holds a whole frame. Replies arrive in 20-byte BLE chunks."""
    if len(buf) < 7:
        return False
    return len(buf) >= buf[3] + 7 and buf[-1] == END


@dataclass(frozen=True)
class Frame:
    reg: int
    status: int
    payload: bytes


def parse_frame(raw: bytes) -> Frame:
    if len(raw) < 7 or raw[0] != START or raw[-1] != END:
        raise JbdError(f"malformed frame: {raw.hex()}")
    length = raw[3]
    if len(raw) != length + 7:
        raise JbdError(
            f"frame length mismatch ({len(raw)} bytes, header says {length + 7}): {raw.hex()}"
        )
    body = raw[2 : 4 + length]
    expected = struct.unpack(">H", raw[4 + length : 6 + length])[0]
    if checksum(body) != expected:
        raise JbdError(f"bad checksum on frame: {raw.hex()}")
    return Frame(reg=raw[1], status=raw[2], payload=bytes(raw[4 : 4 + length]))


@dataclass(frozen=True)
class BasicInfo:
    voltage: float  # V
    current: float  # A, positive while charging
    remaining_ah: float
    nominal_ah: float
    cycles: int
    manufacture_date: str
    balance_bits: int
    protection_bits: int
    software_version: str
    soc: int
    fet_bits: int
    cell_count: int
    temperatures: list[float] = field(default_factory=list)

    @property
    def charge_fet_on(self) -> bool:
        return bool(self.fet_bits & 0x01)

    @property
    def discharge_fet_on(self) -> bool:
        return bool(self.fet_bits & 0x02)

    @property
    def balancing_cells(self) -> list[int]:
        return [i + 1 for i in range(32) if (self.balance_bits >> i) & 1]

    @property
    def protections(self) -> list[str]:
        return protection_names(self.protection_bits)

    @property
    def power(self) -> float:
        return self.voltage * self.current


def protection_names(bits: int) -> list[str]:
    return [name for i, name in enumerate(PROTECTION_FLAGS) if (bits >> i) & 1]


def decode_date(raw: int) -> str:
    year = 2000 + (raw >> 9)
    month = (raw >> 5) & 0x0F
    day = raw & 0x1F
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_basic_info(payload: bytes) -> BasicInfo:
    if len(payload) < 23:
        raise JbdError(f"basic info payload too short ({len(payload)} bytes)")
    (
        voltage,
        current,
        remaining,
        nominal,
        cycles,
        date,
        balance_lo,
        balance_hi,
        protection,
        version,
        soc,
        fet,
        cell_count,
        ntc_count,
    ) = struct.unpack(">HhHHHHHHHBBBBB", payload[:23])

    temps: list[float] = []
    for i in range(ntc_count):
        start = 23 + 2 * i
        if start + 2 > len(payload):
            break
        kelvin_tenths = struct.unpack(">H", payload[start : start + 2])[0]
        temps.append(round((kelvin_tenths - 2731) / 10, 1))

    return BasicInfo(
        voltage=voltage / 100,
        current=current / 100,
        remaining_ah=remaining / 100,
        nominal_ah=nominal / 100,
        cycles=cycles,
        manufacture_date=decode_date(date),
        balance_bits=balance_lo | (balance_hi << 16),
        protection_bits=protection,
        software_version=f"{version >> 4}.{version & 0x0F}",
        soc=soc,
        fet_bits=fet,
        cell_count=cell_count,
        temperatures=temps,
    )


def parse_cell_voltages(payload: bytes) -> list[int]:
    """Per-cell voltages in mV."""
    count = len(payload) // 2
    return list(struct.unpack(f">{count}H", payload[: 2 * count]))


def parse_hardware_version(payload: bytes) -> str:
    return payload.decode("ascii", errors="replace").strip("\x00 ")


def parse_string(payload: bytes) -> str:
    """Length-prefixed ASCII, as used by the name and barcode registers."""
    if payload and payload[0] == len(payload) - 1:
        payload = payload[1:]
    return payload.decode("ascii", errors="replace").strip("\x00 ")


def mosfet_payload(charge_on: bool, discharge_on: bool) -> bytes:
    """Data for the MOSFET control register: a set bit *disables* that FET."""
    mask = (0 if charge_on else 0x01) | (0 if discharge_on else 0x02)
    return bytes([0x00, mask])


# --------------------------------------------------------------------------- #
# Configuration registers (require factory mode to read or write)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RegisterSpec:
    key: str
    address: int
    label: str
    unit: str  # engineering unit shown to the user
    codec: str  # how the raw 16-bit word maps to the engineering value
    writable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    help: str = ""

    @property
    def is_numeric(self) -> bool:
        return self.codec not in ("str", "packed", "date", "bits")


# codec: raw -> engineering
#   V10   : raw * 10 mV       -> V
#   mV    : raw               -> mV
#   A10   : raw * 10 mA       -> A
#   A10s  : signed raw * 10mA -> A (magnitude; the BMS stores discharge limits negative)
#   K10   : raw / 10 K        -> degrees C
#   Ah10  : raw * 10 mAh      -> Ah
#   pct10 : raw / 10          -> %
#   int   : raw               -> integer
#   bits / packed / date / str: shown raw or decoded, never written by this app

SETTINGS: tuple[RegisterSpec, ...] = (
    RegisterSpec(
        "design_capacity", 0x10, "Design Capacity", "Ah", "Ah10", True, 1, 2000
    ),
    RegisterSpec("cycle_capacity", 0x11, "Cycle Capacity", "Ah", "Ah10", True, 1, 2000),
    RegisterSpec(
        "cell_full_voltage", 0x12, "Cell Voltage at 100%", "mV", "mV", True, 2500, 4500
    ),
    RegisterSpec(
        "cell_empty_voltage", 0x13, "Cell Voltage at 0%", "mV", "mV", True, 1500, 3500
    ),
    RegisterSpec("self_discharge_rate", 0x14, "Self Discharge Rate", "%", "pct10"),
    RegisterSpec("manufacture_date", 0x15, "Manufacture Date", "", "date"),
    RegisterSpec("serial_number", 0x16, "Serial Number", "", "int"),
    RegisterSpec("cycle_count", 0x17, "Cycle Count", "", "int"),
    RegisterSpec(
        "charge_over_temp", 0x18, "Charge Over-Temperature", "°C", "K10", True, 0, 120
    ),
    RegisterSpec(
        "charge_over_temp_release",
        0x19,
        "Charge Over-Temp Release",
        "°C",
        "K10",
        True,
        0,
        120,
    ),
    RegisterSpec(
        "charge_under_temp",
        0x1A,
        "Charge Under-Temperature",
        "°C",
        "K10",
        True,
        -40,
        30,
    ),
    RegisterSpec(
        "charge_under_temp_release",
        0x1B,
        "Charge Under-Temp Release",
        "°C",
        "K10",
        True,
        -40,
        30,
    ),
    RegisterSpec(
        "discharge_over_temp",
        0x1C,
        "Discharge Over-Temperature",
        "°C",
        "K10",
        True,
        0,
        120,
    ),
    RegisterSpec(
        "discharge_over_temp_release",
        0x1D,
        "Discharge Over-Temp Release",
        "°C",
        "K10",
        True,
        0,
        120,
    ),
    RegisterSpec(
        "discharge_under_temp",
        0x1E,
        "Discharge Under-Temperature",
        "°C",
        "K10",
        True,
        -40,
        30,
    ),
    RegisterSpec(
        "discharge_under_temp_release",
        0x1F,
        "Discharge Under-Temp Release",
        "°C",
        "K10",
        True,
        -40,
        30,
    ),
    RegisterSpec(
        "pack_over_voltage", 0x20, "Pack Over-Voltage", "V", "V10", True, 4, 80
    ),
    RegisterSpec(
        "pack_over_voltage_release",
        0x21,
        "Pack Over-Voltage Release",
        "V",
        "V10",
        True,
        4,
        80,
    ),
    RegisterSpec(
        "pack_under_voltage", 0x22, "Pack Under-Voltage", "V", "V10", True, 4, 80
    ),
    RegisterSpec(
        "pack_under_voltage_release",
        0x23,
        "Pack Under-Voltage Release",
        "V",
        "V10",
        True,
        4,
        80,
    ),
    RegisterSpec(
        "cell_over_voltage", 0x24, "Cell Over-Voltage", "mV", "mV", True, 2500, 4500
    ),
    RegisterSpec(
        "cell_over_voltage_release",
        0x25,
        "Cell Over-Voltage Release",
        "mV",
        "mV",
        True,
        2500,
        4500,
    ),
    RegisterSpec(
        "cell_under_voltage", 0x26, "Cell Under-Voltage", "mV", "mV", True, 1500, 3500
    ),
    RegisterSpec(
        "cell_under_voltage_release",
        0x27,
        "Cell Under-Voltage Release",
        "mV",
        "mV",
        True,
        1500,
        3500,
    ),
    RegisterSpec(
        "charge_over_current", 0x28, "Charge Over-Current", "A", "A10", True, 1, 500
    ),
    RegisterSpec(
        "discharge_over_current",
        0x29,
        "Discharge Over-Current",
        "A",
        "A10s",
        True,
        1,
        500,
    ),
    RegisterSpec(
        "balance_start_voltage",
        0x2A,
        "Balance Start Voltage",
        "mV",
        "mV",
        True,
        2500,
        4500,
    ),
    RegisterSpec("balance_window", 0x2B, "Balance Window", "mV", "mV", True, 2, 500),
    RegisterSpec("shunt_resistance", 0x2C, "Shunt Resistance", "x0.1 mOhm", "int"),
    RegisterSpec("function_config", 0x2D, "Function Config", "", "bits"),
    RegisterSpec("ntc_config", 0x2E, "NTC Config", "", "bits"),
    RegisterSpec("cell_count", 0x2F, "Cell Count", "", "int"),
    RegisterSpec("fet_control_time", 0x30, "FET Control Time", "s", "int"),
    RegisterSpec("led_timer", 0x31, "LED Timer", "s", "int"),
    RegisterSpec(
        "cell_soc_80_voltage", 0x32, "Cell Voltage at 80%", "mV", "mV", True, 2500, 4500
    ),
    RegisterSpec(
        "cell_soc_60_voltage", 0x33, "Cell Voltage at 60%", "mV", "mV", True, 2500, 4500
    ),
    RegisterSpec(
        "cell_soc_40_voltage", 0x34, "Cell Voltage at 40%", "mV", "mV", True, 2500, 4500
    ),
    RegisterSpec(
        "cell_soc_20_voltage", 0x35, "Cell Voltage at 20%", "mV", "mV", True, 2500, 4500
    ),
    RegisterSpec(
        "cell_over_voltage_hard",
        0x36,
        "Cell Over-Voltage (hard)",
        "mV",
        "mV",
        True,
        2500,
        4800,
    ),
    RegisterSpec(
        "cell_under_voltage_hard",
        0x37,
        "Cell Under-Voltage (hard)",
        "mV",
        "mV",
        True,
        1000,
        3500,
    ),
    RegisterSpec(
        "short_circuit_config", 0x38, "Short Circuit / OC2 Config", "", "packed"
    ),
    RegisterSpec("hard_protect_delays", 0x39, "Hard Protect Delays", "", "packed"),
    RegisterSpec("charge_temp_delays", 0x3A, "Charge Temp Delays", "", "packed"),
    RegisterSpec("discharge_temp_delays", 0x3B, "Discharge Temp Delays", "", "packed"),
    RegisterSpec("pack_voltage_delays", 0x3C, "Pack Voltage Delays", "", "packed"),
    RegisterSpec("cell_voltage_delays", 0x3D, "Cell Voltage Delays", "", "packed"),
    RegisterSpec("charge_current_delays", 0x3E, "Charge Current Delays", "", "packed"),
    RegisterSpec(
        "discharge_current_delays", 0x3F, "Discharge Current Delays", "", "packed"
    ),
    RegisterSpec("manufacturer", 0xA0, "Manufacturer", "", "str"),
    RegisterSpec("model", 0xA1, "Model", "", "str"),
    RegisterSpec("barcode", 0xA2, "Barcode", "", "str"),
)

SETTING_BY_KEY: dict[str, RegisterSpec] = {spec.key: spec for spec in SETTINGS}
WRITABLE_KEYS: tuple[str, ...] = tuple(spec.key for spec in SETTINGS if spec.writable)

# A threshold and the value it must sit on the correct side of. Under-voltage
# limits must be below their release point, over-voltage limits above it.
_LOWER_THAN: dict[str, str] = {
    "pack_under_voltage": "pack_under_voltage_release",
    "cell_under_voltage": "cell_under_voltage_release",
    "cell_empty_voltage": "cell_full_voltage",
    "charge_under_temp": "charge_under_temp_release",
    "discharge_under_temp": "discharge_under_temp_release",
    "charge_over_temp_release": "charge_over_temp",
    "discharge_over_temp_release": "discharge_over_temp",
    "pack_over_voltage_release": "pack_over_voltage",
    "cell_over_voltage_release": "cell_over_voltage",
}


def decode(spec: RegisterSpec, payload: bytes):
    """Engineering value for a register read."""
    if spec.codec == "str":
        return parse_string(payload)
    if len(payload) < 2:
        raise JbdError(f"register {spec.key} returned {len(payload)} bytes")
    raw = struct.unpack(">H", payload[:2])[0]
    if spec.codec == "V10":
        return raw / 100
    if spec.codec == "mV":
        return raw
    if spec.codec == "A10":
        return raw / 100
    if spec.codec == "A10s":
        signed = struct.unpack(">h", payload[:2])[0]
        return abs(signed) / 100
    if spec.codec == "K10":
        return round((raw - 2731) / 10, 1)
    if spec.codec == "Ah10":
        return raw / 100
    if spec.codec == "pct10":
        return raw / 10
    if spec.codec == "date":
        return decode_date(raw)
    if spec.codec == "bits":
        return f"0x{raw:04x}"
    if spec.codec == "packed":
        return f"0x{raw:04x}"
    return raw


def encode(spec: RegisterSpec, value: float) -> bytes:
    """Raw register data for an engineering value. Range-checks first."""
    if not spec.writable:
        raise JbdError(f"{spec.label} is read-only")
    validate(spec, value)
    if spec.codec == "V10":
        raw = round(value * 100)
    elif spec.codec == "mV":
        raw = round(value)
    elif spec.codec == "A10":
        raw = round(value * 100)
    elif spec.codec == "A10s":
        return struct.pack(">h", -round(abs(value) * 100))
    elif spec.codec == "K10":
        raw = round(value * 10 + 2731)
    elif spec.codec == "Ah10":
        raw = round(value * 100)
    elif spec.codec == "pct10":
        raw = round(value * 10)
    elif spec.codec == "int":
        raw = round(value)
    else:
        raise JbdError(f"{spec.label} cannot be written by this app")
    if not 0 <= raw <= 0xFFFF:
        raise JbdError(f"{spec.label}: {value} is out of range for the register")
    return struct.pack(">H", raw)


def validate(spec: RegisterSpec, value: float) -> None:
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise JbdError(f"{spec.label}: {value!r} is not a number") from exc
    if spec.minimum is not None and value < spec.minimum:
        raise JbdError(
            f"{spec.label}: {value:g} {spec.unit} is below the minimum of {spec.minimum:g}"
        )
    if spec.maximum is not None and value > spec.maximum:
        raise JbdError(
            f"{spec.label}: {value:g} {spec.unit} is above the maximum of {spec.maximum:g}"
        )


def check_consistency(key: str, value: float, current: dict[str, float | None]) -> None:
    """Refuse a write that would invert a threshold against its release point."""
    spec = SETTING_BY_KEY[key]
    partner_key = _LOWER_THAN.get(key)
    if partner_key is not None and current.get(partner_key) is not None:
        partner = SETTING_BY_KEY[partner_key]
        if value >= current[partner_key]:
            raise JbdError(
                f"{spec.label} ({value:g} {spec.unit}) must stay below "
                f"{partner.label} ({current[partner_key]:g} {partner.unit})"
            )
    for lower_key, upper_key in _LOWER_THAN.items():
        if (
            upper_key == key
            and current.get(lower_key) is not None
            and value <= current[lower_key]
        ):
            lower = SETTING_BY_KEY[lower_key]
            raise JbdError(
                f"{spec.label} ({value:g} {spec.unit}) must stay above "
                f"{lower.label} ({current[lower_key]:g} {lower.unit})"
            )
