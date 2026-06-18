"""Config flow for Divoom Pixoo (SPP bridge)."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import config_validation as cv

from . import protocol as proto
from .const import CONF_HOST, CONF_PORT, CONNECT_TIMEOUT, DEFAULT_PORT, DOMAIN


async def _try_connect(host: str, port: int) -> None:
    """Open the bridge socket and send the handshake; raise OSError on failure."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=CONNECT_TIMEOUT
    )
    try:
        writer.write(proto.handshake_frame())
        await writer.drain()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


class PixooConfigFlow(ConfigFlow, domain=DOMAIN):
    """Manual host/port flow — the device sits behind a TCP bridge, not on BT directly."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            try:
                await _try_connect(host, port)
            except (OSError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"Pixoo @ {host}", data={CONF_HOST: host, CONF_PORT: port}
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
