"""Config-flow tests for pixoo_spp."""

import asyncio
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.pixoo_spp.const import DOMAIN


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow_success(hass: HomeAssistant, fake_connection) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.168.1.50", "port": 8888}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Pixoo @ 192.168.1.50"
    assert result["data"] == {"host": "192.168.1.50", "port": 8888}
    assert result["result"].unique_id == "192.168.1.50:8888"


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with patch(
        "custom_components.pixoo_spp.config_flow.asyncio.open_connection",
        side_effect=OSError("refused"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "10.0.0.9", "port": 8888}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_timeout_is_cannot_connect(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with patch(
        "custom_components.pixoo_spp.config_flow.asyncio.open_connection",
        side_effect=asyncio.TimeoutError,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "10.0.0.9", "port": 8888}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_already_configured(hass: HomeAssistant, fake_connection) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.50:8888",
        data={"host": "192.168.1.50", "port": 8888},
    ).add_to_hass(hass)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.168.1.50", "port": 8888}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
