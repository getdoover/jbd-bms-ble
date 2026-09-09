"""Smoke tests: modules import, schemas export, every writable register has a
UI input and a handler."""

import json

from pydoover.config import Schema
from pydoover.tags import Tags
from pydoover.ui import UI

from jbd_bms_ble import jbd


def test_import_app():
    from jbd_bms_ble.application import JbdBmsBleApplication

    assert JbdBmsBleApplication.config_cls is not None
    assert JbdBmsBleApplication.tags_cls is not None
    assert JbdBmsBleApplication.ui_cls is not None


def test_config_schema():
    from jbd_bms_ble.app_config import JbdBmsBleConfig

    assert issubclass(JbdBmsBleConfig, Schema)
    schema = JbdBmsBleConfig.to_schema()
    assert schema["type"] == "object"
    assert "device_address" in schema["properties"]
    assert schema["properties"]["allow_settings_write"]["default"] is False
    assert schema["properties"]["allow_mosfet_control"]["default"] is False


def test_tags():
    from jbd_bms_ble.app_tags import JbdBmsBleTags

    assert issubclass(JbdBmsBleTags, Tags)


def test_ui_has_an_input_per_writable_register():
    from jbd_bms_ble.app_ui import JbdBmsBleUI

    assert issubclass(JbdBmsBleUI, UI)
    interactions = JbdBmsBleUI(None, None, None).get_interactions()
    for key in jbd.WRITABLE_KEYS:
        assert key in interactions, key
    for name in (
        "charge_switch",
        "discharge_switch",
        "scan_batteries",
        "reload_settings",
    ):
        assert name in interactions


def test_application_has_a_handler_per_writable_register():
    from jbd_bms_ble.application import JbdBmsBleApplication

    for key in jbd.WRITABLE_KEYS:
        handler = getattr(JbdBmsBleApplication, f"on_setting_{key}")
        assert handler._rpc_method == key


def test_config_export(tmp_path):
    from jbd_bms_ble.app_config import JbdBmsBleConfig

    fp = tmp_path / "doover_config.json"
    JbdBmsBleConfig.export(fp, "jbd_bms_ble")
    data = json.loads(fp.read_text())
    assert "properties" in data["jbd_bms_ble"]["config_schema"]


def test_ui_export(tmp_path):
    from jbd_bms_ble.app_ui import JbdBmsBleUI

    fp = tmp_path / "doover_config.json"
    JbdBmsBleUI(None, None, None).export(fp, "jbd_bms_ble")
    data = json.loads(fp.read_text())
    assert data["jbd_bms_ble"]["ui_schema"]["type"] == "uiApplication"
    assert "soc" in data["jbd_bms_ble"]["ui_schema"]["children"]
