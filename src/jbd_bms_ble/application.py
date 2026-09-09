import logging
import time
from datetime import UTC, datetime

from bleak.exc import BleakError
from pydoover import ui
from pydoover.docker import Application

from . import jbd
from .app_config import JbdBmsBleConfig
from .app_tags import JbdBmsBleTags
from .app_ui import JbdBmsBleUI
from .ble import JbdBle

log = logging.getLogger(__name__)

# Below this magnitude the pack is called idle rather than charging/discharging.
IDLE_CURRENT_A = 0.05
# Consecutive failed polls before the dashboard shows the comms warning.
FAILURES_BEFORE_WARNING = 3

CommsErrors = (jbd.JbdError, BleakError, TimeoutError, OSError)


def _now_text() -> str:
    """Device-local wall clock, for the 'last read' style fields."""
    return datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")


class JbdBmsBleApplication(Application):
    config_cls = JbdBmsBleConfig
    tags_cls = JbdBmsBleTags
    ui_cls = JbdBmsBleUI

    config: JbdBmsBleConfig
    tags: JbdBmsBleTags
    ui: JbdBmsBleUI

    async def setup(self):
        self.loop_target_period = max(2, int(self.config.poll_interval.value))

        address = (self.config.device_address.value or "").strip().upper()
        self.client = JbdBle(address)
        await self.tags.device_address.set(address)

        self._settings_read_at = 0.0
        self._identity_read = False
        self._consecutive_failures = 0
        self._last_protection_bits = 0
        self._low_soc_notified = False

    # ------------------------------------------------------------ main loop

    async def main_loop(self):
        if not self.client.address:
            log.warning(
                "No battery address configured; use 'Scan for batteries' to find one"
            )
            await self.tags.comms_ok.set(False)
            return

        try:
            await self._poll()
        except CommsErrors as exc:
            self._consecutive_failures += 1
            log.warning(
                "Battery poll failed (%d in a row): %s", self._consecutive_failures, exc
            )
            if self._consecutive_failures >= FAILURES_BEFORE_WARNING:
                await self.tags.comms_ok.set(False)
            await self.client.disconnect()
            return

        self._consecutive_failures = 0
        await self.tags.comms_ok.set(True)
        if self.config.disconnect_between_polls.value:
            await self.client.disconnect()

    async def _poll(self):
        basic = await self.client.read_basic_info()
        cells = await self.client.read_cell_voltages()

        if not self._identity_read:
            await self.tags.hardware_version.set(
                await self.client.read_hardware_version()
            )
            await self.tags.software_version.set(basic.software_version)
            await self.tags.manufacture_date.set(basic.manufacture_date)
            self._identity_read = True

        await self._publish(basic, cells)
        await self._maybe_notify(basic)

        refresh_after = self.config.settings_refresh_minutes.value * 60
        if time.time() - self._settings_read_at >= refresh_after:
            await self._refresh_settings()

    async def _publish(self, basic: jbd.BasicInfo, cells: list[int]):
        if basic.current > IDLE_CURRENT_A:
            state = "charging"
        elif basic.current < -IDLE_CURRENT_A:
            state = "discharging"
        else:
            state = "idle"

        await self.tags.pack_voltage.set(round(basic.voltage, 2))
        await self.tags.current.set(round(basic.current, 2))
        await self.tags.power.set(round(basic.power, 1))
        await self.tags.soc.set(basic.soc)
        await self.tags.remaining_ah.set(round(basic.remaining_ah, 2))
        await self.tags.nominal_ah.set(round(basic.nominal_ah, 2))
        await self.tags.cycles.set(basic.cycles)
        await self.tags.temperatures.set(basic.temperatures)
        await self.tags.temperature.set(
            max(basic.temperatures) if basic.temperatures else None
        )
        await self.tags.charge_state.set(state)

        await self.tags.cell_count.set(basic.cell_count)
        await self.tags.cell_voltages.set(cells)
        if cells:
            await self.tags.cell_min.set(min(cells))
            await self.tags.cell_max.set(max(cells))
            await self.tags.cell_delta.set(max(cells) - min(cells))
            await self.tags.cells_text.set(" / ".join(str(c) for c in cells) + " mV")
        balancing = basic.balancing_cells
        await self.tags.balancing.set(
            "cells " + ", ".join(str(c) for c in balancing) if balancing else "none"
        )

        await self.tags.protection_bits.set(basic.protection_bits)
        await self.tags.protection_text.set(", ".join(basic.protections) or "none")
        await self.tags.protection_clear.set(basic.protection_bits == 0)
        await self.tags.charge_fet.set(basic.charge_fet_on)
        await self.tags.discharge_fet.set(basic.discharge_fet_on)
        await self.tags.soc_ok.set(basic.soc >= self.config.low_soc_warning.value)
        await self.tags.last_read_text.set(_now_text())

        await self._sync_select("charge_switch", basic.charge_fet_on)
        await self._sync_select("discharge_switch", basic.discharge_fet_on)

    async def _maybe_notify(self, basic: jbd.BasicInfo):
        new_bits = basic.protection_bits & ~self._last_protection_bits
        if new_bits:
            names = ", ".join(jbd.protection_names(new_bits))
            await self.send_notification(
                f"Battery BMS protection tripped: {names}",
                title="Battery protection",
            )
        self._last_protection_bits = basic.protection_bits

        low = basic.soc < self.config.low_soc_warning.value
        if low and not self._low_soc_notified:
            await self.send_notification(
                f"Battery state of charge is {basic.soc}% ({basic.voltage:.2f} V)",
                title="Battery low",
            )
        self._low_soc_notified = low

    # ------------------------------------------------------------- settings

    async def _refresh_settings(self):
        settings = await self.client.read_settings()
        self._settings_read_at = time.time()
        await self.tags.settings.set(settings)
        await self.tags.settings_read_text.set(_now_text())
        for key in jbd.WRITABLE_KEYS:
            value = settings.get(key)
            if value is not None:
                await self._sync_input(key, value)

    def _interaction(self, name: str):
        return self.ui.get_interactions().get(name)

    async def _sync_input(self, key: str, value: float):
        """Show the BMS value in its input without logging it as a user action."""
        element = self._interaction(key)
        if element is None:
            return
        current = element.value
        try:
            unchanged = (
                current is not None and abs(float(current) - float(value)) < 1e-6
            )
        except (TypeError, ValueError):
            unchanged = False
        if not unchanged:
            await element.set(value, log_update=False)

    async def _sync_select(self, name: str, is_on: bool):
        element = self._interaction(name)
        if element is None:
            return
        wanted = "on" if is_on else "off"
        if str(element.value or "").lower() != wanted:
            await element.set(wanted, log_update=False)

    async def _set_status(self, text: str):
        log.info(text)
        await self.tags.settings_status.set(text)

    async def _on_setting_change(self, ctx, key: str, value):
        spec = jbd.SETTING_BY_KEY[key]
        settings = dict(self.tags.settings.get() or {})
        previous = settings.get(key)

        if not self.config.allow_settings_write.value:
            await self._set_status(
                f"{spec.label} not written: enable 'Allow Settings Changes' in the app config first"
            )
            if previous is not None:
                await ctx.set_value(previous, log_update=False)
            return

        try:
            number = float(value)
            jbd.validate(spec, number)
            jbd.check_consistency(key, number, settings)
            readback = await self.client.write_setting(key, number)
        except (ValueError, TypeError) as exc:
            await self._set_status(
                f"{spec.label} not written: {value!r} is not a number ({exc})"
            )
            if previous is not None:
                await ctx.set_value(previous, log_update=False)
            return
        except CommsErrors as exc:
            await self._set_status(f"{spec.label} not written: {exc}")
            if previous is not None:
                await ctx.set_value(previous, log_update=False)
            return
        finally:
            if self.config.disconnect_between_polls.value:
                await self.client.disconnect()

        settings[key] = readback
        await self.tags.settings.set(settings)
        await ctx.set_value(readback, log_update=False)
        await self._set_status(
            f"{spec.label} set to {readback:g} {spec.unit} (BMS read-back)"
        )

    # ------------------------------------------------------------- handlers

    @ui.handler("scan_batteries")
    async def on_scan(self, ctx, value):
        await self.tags.scan_results.set("scanning...")
        try:
            await self.client.disconnect()
            found = await JbdBle.scan(10.0)
        except CommsErrors as exc:
            await self.tags.scan_results.set(f"scan failed: {exc}")
        else:
            if found:
                lines = [
                    f"{d['name'] or '?'} {d['address']} ({d['rssi']} dBm)"
                    for d in found
                ]
                await self.tags.scan_results.set("; ".join(lines))
            else:
                await self.tags.scan_results.set("no JBD batteries found within range")
        await ctx.set_value(None)

    @ui.handler("reload_settings")
    async def on_reload_settings(self, ctx, value):
        try:
            await self._refresh_settings()
        except CommsErrors as exc:
            await self._set_status(f"Settings read failed: {exc}")
        else:
            await self._set_status("Settings reloaded from BMS")
        finally:
            if self.config.disconnect_between_polls.value:
                await self.client.disconnect()
        await ctx.set_value(None)

    @ui.handler("charge_switch")
    async def on_charge_switch(self, ctx, value):
        await self._on_mosfet(ctx, value, charge=True)

    @ui.handler("discharge_switch")
    async def on_discharge_switch(self, ctx, value):
        await self._on_mosfet(ctx, value, charge=False)

    async def _on_mosfet(self, ctx, value, *, charge: bool):
        label = "Charging" if charge else "Discharging"
        current_state = (
            self.tags.charge_fet.get() if charge else self.tags.discharge_fet.get()
        )
        want_on = str(value).strip().lower() in ("on", "true", "1")

        if not self.config.allow_mosfet_control.value:
            await self._set_status(
                f"{label} not switched: enable 'Allow Charge/Discharge Switching' in the app config first"
            )
            await ctx.set_value("on" if current_state else "off", log_update=False)
            return

        charge_on = want_on if charge else bool(self.tags.charge_fet.get())
        discharge_on = want_on if not charge else bool(self.tags.discharge_fet.get())
        try:
            await self.client.set_mosfets(charge_on, discharge_on)
            basic = await self.client.read_basic_info()
        except CommsErrors as exc:
            await self._set_status(f"{label} not switched: {exc}")
            await ctx.set_value("on" if current_state else "off", log_update=False)
            return
        finally:
            if self.config.disconnect_between_polls.value:
                await self.client.disconnect()

        await self.tags.charge_fet.set(basic.charge_fet_on)
        await self.tags.discharge_fet.set(basic.discharge_fet_on)
        actual = basic.charge_fet_on if charge else basic.discharge_fet_on
        await self._set_status(
            f"{label} switched {'on' if actual else 'off'} at the BMS"
        )
        await ctx.set_value("on" if actual else "off", log_update=False)


def _make_setting_handler(key: str):
    @ui.handler(key)
    async def handler(self, ctx, value):
        await self._on_setting_change(ctx, key, value)

    handler.__name__ = f"on_setting_{key}"
    return handler


# One handler per writable register, matching the FloatInput names in app_ui.
for _key in jbd.WRITABLE_KEYS:
    setattr(JbdBmsBleApplication, f"on_setting_{_key}", _make_setting_handler(_key))
