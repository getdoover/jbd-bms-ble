"""Protocol tests against frames captured from a real pack.

The basic-info frame below was read from an iTechworld iTECH120X PRO
(JBD DP04S007L4S150A) on 2026-09-09 while it was charging at ~3.9 A.
"""

import pytest

from jbd_bms_ble import jbd

BASIC_INFO_FRAME = bytes.fromhex(
    "dd03002204c00182010534bc0004309d00000000000067020304010b2500000034bc01050000fb3977"
)


def test_read_request_frame_matches_reference():
    # The canonical "read basic info" request every JBD app sends.
    assert jbd.build_frame(jbd.CMD_READ, jbd.REG_BASIC_INFO) == bytes.fromhex(
        "dda50300fffd77"
    )
    assert jbd.build_frame(jbd.CMD_READ, jbd.REG_CELL_VOLTAGES) == bytes.fromhex(
        "dda50400fffc77"
    )


def test_factory_mode_frames():
    assert jbd.build_frame(
        jbd.CMD_WRITE, jbd.REG_FACTORY_ENTER, jbd.FACTORY_ENTER_PAYLOAD
    ) == bytes.fromhex("dd5a00025678ff3077")
    assert jbd.build_frame(
        jbd.CMD_WRITE, jbd.REG_FACTORY_EXIT, jbd.FACTORY_EXIT_PAYLOAD
    ) == bytes.fromhex("dd5a01020000fffd77")


def test_frame_complete_handles_chunked_notifications():
    buf = bytearray()
    for i in range(0, len(BASIC_INFO_FRAME), 20):
        assert not jbd.frame_complete(buf)
        buf.extend(BASIC_INFO_FRAME[i : i + 20])
    assert jbd.frame_complete(buf)


def test_parse_frame_validates_checksum():
    frame = jbd.parse_frame(BASIC_INFO_FRAME)
    assert frame.reg == jbd.REG_BASIC_INFO
    assert frame.status == jbd.STATUS_OK
    assert len(frame.payload) == 0x22

    corrupted = bytearray(BASIC_INFO_FRAME)
    corrupted[5] ^= 0x01
    with pytest.raises(jbd.JbdError):
        jbd.parse_frame(bytes(corrupted))


def test_parse_basic_info_from_captured_frame():
    info = jbd.parse_basic_info(jbd.parse_frame(BASIC_INFO_FRAME).payload)
    assert info.voltage == pytest.approx(12.16)
    assert info.current == pytest.approx(3.86)
    assert info.remaining_ah == pytest.approx(2.61)
    assert info.nominal_ah == pytest.approx(135.0)
    assert info.cycles == 4
    assert info.manufacture_date == "2024-04-29"
    assert info.protection_bits == 0
    assert info.protections == []
    assert info.software_version == "6.7"
    assert info.soc == 2
    assert info.charge_fet_on and info.discharge_fet_on
    assert info.cell_count == 4
    assert info.temperatures == [12.2]
    assert info.balancing_cells == []
    assert info.power == pytest.approx(12.16 * 3.86)


def test_parse_cell_voltages():
    payload = bytes.fromhex("0bf80beb0bdc0bd2")
    assert jbd.parse_cell_voltages(payload) == [3064, 3051, 3036, 3026]


def test_parse_strings():
    assert jbd.parse_hardware_version(b"DP04S007L4S150A") == "DP04S007L4S150A"
    assert jbd.parse_string(b"\x05DGJBD") == "DGJBD"
    assert jbd.parse_string(b"\x12iTECH120X-PRO08738") == "iTECH120X-PRO08738"


def test_protection_names():
    assert jbd.protection_names(0b1000000001) == [
        "cell over-voltage",
        "discharge over-current",
    ]


def test_mosfet_payload():
    assert jbd.mosfet_payload(True, True) == b"\x00\x00"
    assert jbd.mosfet_payload(False, True) == b"\x00\x01"
    assert jbd.mosfet_payload(True, False) == b"\x00\x02"
    assert jbd.mosfet_payload(False, False) == b"\x00\x03"


@pytest.mark.parametrize(
    "key,payload,expected",
    [
        ("design_capacity", bytes.fromhex("34bc"), 135.0),
        ("pack_under_voltage", bytes.fromhex("0370"), 8.8),
        ("pack_over_voltage", bytes.fromhex("05b4"), 14.6),
        ("cell_under_voltage", bytes.fromhex("0898"), 2200),
        ("charge_over_temp", bytes.fromhex("0d35"), 65.0),
        ("charge_under_temp", bytes.fromhex("0aab"), 0.0),
        ("discharge_under_temp", bytes.fromhex("09e3"), -20.0),
        ("charge_over_current", bytes.fromhex("3e80"), 160.0),
        ("discharge_over_current", bytes.fromhex("c180"), 160.0),
        ("manufacture_date", bytes.fromhex("309d"), "2024-04-29"),
        ("function_config", bytes.fromhex("0006"), "0x0006"),
    ],
)
def test_decode_matches_pack_values(key, payload, expected):
    assert jbd.decode(jbd.SETTING_BY_KEY[key], payload) == expected


@pytest.mark.parametrize(
    "key,value",
    [
        ("design_capacity", 135.0),
        ("pack_under_voltage", 11.5),
        ("cell_under_voltage", 2900),
        ("charge_over_temp", 65.0),
        ("charge_under_temp", -5.0),
        ("charge_over_current", 100.0),
        ("discharge_over_current", 120.0),
        ("balance_window", 20),
    ],
)
def test_encode_decode_round_trip(key, value):
    spec = jbd.SETTING_BY_KEY[key]
    assert jbd.decode(spec, jbd.encode(spec, value)) == pytest.approx(value)


def test_encode_discharge_current_is_stored_negative():
    assert jbd.encode(
        jbd.SETTING_BY_KEY["discharge_over_current"], 160
    ) == bytes.fromhex("c180")


def test_encode_rejects_out_of_range_and_read_only():
    with pytest.raises(jbd.JbdError):
        jbd.encode(jbd.SETTING_BY_KEY["cell_under_voltage"], 500)
    with pytest.raises(jbd.JbdError):
        jbd.encode(jbd.SETTING_BY_KEY["cycle_count"], 0)


def test_consistency_guard():
    current = {"pack_under_voltage": 8.8, "pack_under_voltage_release": 10.4}
    jbd.check_consistency("pack_under_voltage", 10.0, current)
    with pytest.raises(jbd.JbdError):
        jbd.check_consistency("pack_under_voltage", 10.4, current)
    with pytest.raises(jbd.JbdError):
        jbd.check_consistency("pack_under_voltage_release", 8.0, current)


def test_every_writable_setting_has_bounds():
    for spec in jbd.SETTINGS:
        if spec.writable:
            assert spec.minimum is not None and spec.maximum is not None, spec.key
            assert spec.is_numeric, spec.key
