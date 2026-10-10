"""Coordinator that owns a persistent SACP connection to the Artisan.

Push-based: a background task holds one TCP connection open and pushes
new values to entities as they arrive. It auto-reconnects on a dropped
connection -- unless it was stopped deliberately (via the Connect/
Disconnect button), in which case it stays disconnected until turned
back on.

Only one client can hold an SACP session with the printer at a time --
if another tool (a script on your server, Luban, etc.) is connected,
this integration's handshake will just hang/fail. Use the Connect/
Disconnect button to free the connection for another tool, then
reconnect when you're done.

Position is handled specially: it's a direct one-shot query, not a
subscription (the subscribe wrapper is confirmed rejected), so the read
loop re-sends it on its own timer rather than waiting for the printer
to push it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import sacp
from .const import (
    DOMAIN,
    POSITION_POLL_INTERVAL_S,
    RECONNECT_DELAY,
    SACP_PORT,
    SOCKET_TIMEOUT,
    DEFAULT_SUBSCRIPTION_INTERVAL_MS,
)

_LOGGER = logging.getLogger(__name__)


class SnapmakerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Owns the live connection and the latest decoded values."""

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        self.host = host
        self.data: dict[str, Any] = {}
        self.available_keys: set[str] = set()
        self.connected: bool = False
        self.last_error: str | None = None
        self.update_interval_s: int = DEFAULT_SUBSCRIPTION_INTERVAL_MS // 1000
        self.device_modules: list[dict[str, Any]] = []
        self.print_head_firmware: str | None = None
        self._task: asyncio.Task | None = None
        self._should_run = False

    async def async_start(self) -> None:
        """Start (or resume) trying to maintain a connection."""
        if self._task is not None and not self._task.done():
            return  # already running
        self._should_run = True
        self._task = self.hass.loop.create_task(self._runner())

    async def async_stop(self) -> None:
        """Stop and close the connection. Idempotent."""
        self._should_run = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self.connected = False
        self.available_keys = set()
        self.async_update_listeners()

    async def async_set_update_interval(self, seconds: int) -> None:
        """Change how often the printer pushes updates. Applies live by
        reconnecting (if currently connected); otherwise just remembered
        for the next connection."""
        self.update_interval_s = max(1, int(seconds))
        self.async_update_listeners()
        if self._should_run:
            await self.async_stop()
            await self.async_start()

    async def _runner(self) -> None:
        while self._should_run:
            writer = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, SACP_PORT), timeout=SOCKET_TIMEOUT
                )
                await sacp.handshake(reader, writer, SOCKET_TIMEOUT)
                _LOGGER.debug("SACP handshake OK for %s", self.host)

                sequence = 2
                sequence += 1
                try:
                    writer.write(sacp.make_module_info_query_packet(sequence))
                    await writer.drain()
                    response = await asyncio.wait_for(
                        self._wait_for(reader, sequence, 0x01, 0x20), timeout=SOCKET_TIMEOUT
                    )
                    self.device_modules = sacp.parse_module_info_response(response["payload"])
                    self.print_head_firmware = sacp.find_print_head_firmware(self.device_modules)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Device info query failed for %s: %s", self.host, err)

                active = await self._subscribe_all(reader, writer, sequence)
                # Position is always attempted via direct query, regardless
                # of what the subscribe wrapper accepted.
                self.available_keys = {key for key, *_ in active} | {"position"}
                if not self.available_keys:
                    raise RuntimeError("No subscriptions were accepted")

                self.connected = True
                self.last_error = None
                self.async_update_listeners()

                last_position_query = 0.0
                while self._should_run:
                    if time.time() - last_position_query >= POSITION_POLL_INTERVAL_S:
                        sequence += 1
                        writer.write(sacp.make_position_query_packet(
                            sequence, self.update_interval_s * 1000
                        ))
                        await writer.drain()
                        last_position_query = time.time()

                    try:
                        packet = await asyncio.wait_for(sacp.read_packet(reader), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    if packet["command_set"] == 0x01 and packet["command_id"] == 0x30:
                        self.data = {**self.data, "position": sacp.decode_position(packet["payload"])}
                        self.async_update_listeners()
                        continue

                    for key, _receiver, cset, cid, decoder in active:
                        if packet["command_set"] == cset and packet["command_id"] == cid:
                            self.data = {**self.data, key: decoder(packet["payload"])}
                            self.async_update_listeners()
                            break

            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                self.last_error = f"{type(err).__name__}: {err}"
                _LOGGER.warning(
                    "Snapmaker Artisan (%s) connection error: %s", self.host, self.last_error
                )
            finally:
                self.connected = False
                self.available_keys = set()
                self.async_update_listeners()
                if writer is not None:
                    try:
                        writer.write(sacp.make_disconnect_packet())
                        await writer.drain()
                    except Exception:  # noqa: BLE001
                        pass
                    writer.close()

            if self._should_run:
                await asyncio.sleep(RECONNECT_DELAY)

    @staticmethod
    async def _wait_for(reader, sequence, cset, cid):
        while True:
            response = await sacp.read_packet(reader)
            if (response["sequence"] == sequence and response["command_set"] == cset
                    and response["command_id"] == cid):
                return response

    async def _subscribe_all(self, reader, writer, sequence):
        """Subscribe to everything in sacp.SUBSCRIPTIONS. Returns the
        list actually accepted."""
        interval_ms = self.update_interval_s * 1000
        active = []
        for key, receiver, cset, cid, decoder in sacp.SUBSCRIPTIONS:
            sequence += 1
            status = await sacp.subscribe(
                reader, writer, sequence, receiver, cset, cid,
                interval_ms, SOCKET_TIMEOUT,
            )
            if status == 0:
                active.append((key, receiver, cset, cid, decoder))
            else:
                _LOGGER.debug(
                    "Snapmaker Artisan (%s): subscription %s (%#04x/%#04x) rejected (status=%s)",
                    self.host, key, cset, cid, status,
                )
        return active
