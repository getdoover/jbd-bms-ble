"""Bluetooth transport for a JBD BMS, built on bleak.

The BMS exposes one service (0xFF00) with a notify characteristic (0xFF01)
for replies and a write characteristic (0xFF02) for requests. Replies come
back as 20-byte notification chunks that are reassembled here.

Only one central can be connected at a time, so while this client holds the
link the phone app cannot connect. ``disconnect()`` between polls if that
matters.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from . import jbd

log = logging.getLogger(__name__)

SERVICE_UUID = "0000ff00-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"

# The BMS drops the first request that arrives straight after notifications
# are enabled on a fresh connection. A short settle plus one retry covers it.
SETTLE_AFTER_CONNECT = 0.3
REQUEST_ATTEMPTS = 2
SCAN_TIMEOUT = 15.0


def bluez_device_path(address: str, adapter: str = "hci0") -> str:
    """The D-Bus object path BlueZ gives a device it has seen since boot."""
    return f"/org/bluez/{adapter}/dev_{address.upper().replace(':', '_')}"


class JbdBle:
    def __init__(
        self,
        address: str,
        connect_timeout: float = 20.0,
        response_timeout: float = 6.0,
        adapter: str = "hci0",
    ):
        self.address = address
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout
        self.adapter = adapter

        self._client: BleakClient | None = None
        self._buf = bytearray()
        self._done = asyncio.Event()
        # Held for a whole logical operation (a poll, a settings read, one
        # write) so a UI-triggered write cannot interleave with a poll and,
        # in particular, cannot land inside someone else's factory-mode session.
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ link

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self) -> None:
        if self.is_connected:
            return
        await self.disconnect()

        client = await self._connect_known_device()
        if client is None:
            client = await self._connect_after_scan()

        await client.start_notify(NOTIFY_UUID, self._on_notify)
        await asyncio.sleep(SETTLE_AFTER_CONNECT)
        self._client = client
        log.info("Connected to BMS at %s", self.address)

    async def _connect_known_device(self) -> BleakClient | None:
        """Connect through the device object BlueZ already holds, if any.

        This needs no scan, and it is the only route that works when BlueZ is
        still holding a link from a previous process (an app container that was
        killed mid-poll): a connected BMS stops advertising, so a scan cannot
        see it. bleak notices the existing link and reuses it.
        """
        device = BLEDevice(
            self.address,
            None,
            {"path": bluez_device_path(self.address, self.adapter), "props": {}},
        )
        client = BleakClient(device, timeout=self.connect_timeout)
        try:
            await client.connect()
        except (BleakError, TimeoutError, OSError) as exc:
            log.info("BlueZ has no usable device object for %s (%s)", self.address, exc)
            return None
        return client

    async def _connect_after_scan(self) -> BleakClient:
        device = await BleakScanner.find_device_by_address(
            self.address, timeout=SCAN_TIMEOUT
        )
        if device is None:
            raise jbd.JbdError(f"battery {self.address} not found in a Bluetooth scan")
        client = BleakClient(device, timeout=self.connect_timeout)
        await client.connect()
        return client

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        with contextlib.suppress(BleakError, TimeoutError, OSError):
            if client.is_connected:
                await client.disconnect()

    def _on_notify(self, _characteristic, data: bytearray) -> None:
        self._buf.extend(data)
        if jbd.frame_complete(self._buf):
            self._done.set()

    # --------------------------------------------------------------- frames

    async def _request(self, kind: int, reg: int, data: bytes = b"") -> bytes:
        frame = jbd.build_frame(kind, reg, data)
        last_error: Exception | None = None
        for attempt in range(1, REQUEST_ATTEMPTS + 1):
            await self.connect()
            assert self._client is not None
            self._buf.clear()
            self._done.clear()
            await self._client.write_gatt_char(WRITE_UUID, frame, response=False)
            try:
                await asyncio.wait_for(self._done.wait(), self.response_timeout)
            except TimeoutError as exc:
                last_error = exc
                log.debug("No reply for register 0x%02x (attempt %d)", reg, attempt)
                if not self.is_connected:
                    await self.disconnect()
                continue
            reply = jbd.parse_frame(bytes(self._buf))
            if reply.status != jbd.STATUS_OK:
                raise jbd.JbdError(
                    f"BMS rejected register 0x{reg:02x} (status 0x{reply.status:02x})"
                )
            return reply.payload
        raise jbd.JbdError(f"no reply for register 0x{reg:02x}") from last_error

    async def _read(self, reg: int) -> bytes:
        return await self._request(jbd.CMD_READ, reg)

    async def _write(self, reg: int, data: bytes) -> bytes:
        return await self._request(jbd.CMD_WRITE, reg, data)

    @contextlib.asynccontextmanager
    async def _factory_mode(self):
        await self._write(jbd.REG_FACTORY_ENTER, jbd.FACTORY_ENTER_PAYLOAD)
        try:
            yield
        finally:
            with contextlib.suppress(jbd.JbdError, BleakError):
                await self._write(jbd.REG_FACTORY_EXIT, jbd.FACTORY_EXIT_PAYLOAD)

    # ------------------------------------------------------------ telemetry

    async def read_basic_info(self) -> jbd.BasicInfo:
        async with self._lock:
            return jbd.parse_basic_info(await self._read(jbd.REG_BASIC_INFO))

    async def read_cell_voltages(self) -> list[int]:
        async with self._lock:
            return jbd.parse_cell_voltages(await self._read(jbd.REG_CELL_VOLTAGES))

    async def read_hardware_version(self) -> str:
        async with self._lock:
            return jbd.parse_hardware_version(
                await self._read(jbd.REG_HARDWARE_VERSION)
            )

    # ------------------------------------------------------------- settings

    async def read_settings(self) -> dict[str, Any]:
        """Every configuration register, keyed by ``RegisterSpec.key``.

        A register the firmware refuses is reported as ``None`` rather than
        failing the whole read, since older boards lack some of them.
        """
        result: dict[str, Any] = {}
        async with self._lock, self._factory_mode():
            for spec in jbd.SETTINGS:
                try:
                    result[spec.key] = jbd.decode(spec, await self._read(spec.address))
                except jbd.JbdError as exc:
                    log.warning("Could not read %s: %s", spec.key, exc)
                    result[spec.key] = None
        return result

    async def write_setting(self, key: str, value: float) -> Any:
        """Write one register and return the value read back from the BMS."""
        spec = jbd.SETTING_BY_KEY[key]
        data = jbd.encode(spec, value)
        async with self._lock, self._factory_mode():
            await self._write(spec.address, data)
            return jbd.decode(spec, await self._read(spec.address))

    async def set_mosfets(self, charge_on: bool, discharge_on: bool) -> None:
        payload = jbd.mosfet_payload(charge_on, discharge_on)
        async with self._lock:
            try:
                await self._write(jbd.REG_MOSFET_CONTROL, payload)
            except jbd.JbdError:
                # Some firmware only honours the switch inside factory mode.
                async with self._factory_mode():
                    await self._write(jbd.REG_MOSFET_CONTROL, payload)

    # ----------------------------------------------------------------- scan

    @staticmethod
    async def scan(timeout: float = 10.0) -> list[dict[str, Any]]:
        """Nearby devices advertising the JBD service, strongest signal first."""
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        results = []
        for device, adv in found.values():
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            if SERVICE_UUID not in uuids:
                continue
            results.append(
                {"name": device.name or "", "address": device.address, "rssi": adv.rssi}
            )
        results.sort(key=lambda r: r["rssi"], reverse=True)
        return results
