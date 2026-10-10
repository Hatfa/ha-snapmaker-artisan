"""Config flow for Snapmaker Artisan."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResult

from . import sacp
from .const import CONF_MACHINE_NAME, CONF_MODEL, DOMAIN, SACP_PORT, SOCKET_TIMEOUT

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


async def _async_test_sacp_connection(hass, host: str) -> None:
    """Open a TCP connection and complete the SACP handshake, then close.
    Raises on any failure."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, SACP_PORT), timeout=SOCKET_TIMEOUT
    )
    try:
        await sacp.handshake(reader, writer, SOCKET_TIMEOUT)
    finally:
        writer.close()


class SnapmakerArtisanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Snapmaker Artisan."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]

            discovery = await sacp.async_discover(self.hass, host)
            machine_name = discovery.name if discovery else host
            model = discovery.model if discovery else None

            try:
                await _async_test_sacp_connection(self.hass, host)
            except (asyncio.TimeoutError, OSError, Exception):  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                unique_id = discovery.name if discovery else host
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=machine_name,
                    data={
                        CONF_HOST: host,
                        CONF_MACHINE_NAME: machine_name,
                        CONF_MODEL: model,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )
