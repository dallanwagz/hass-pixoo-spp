"""Divoom Pixoo 16 over a Bluetooth-Classic-SPP <-> TCP bridge (untether_spp)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .coordinator import PixooConfigEntry, PixooCoordinator
from .services import async_setup_services


async def async_setup_entry(hass: HomeAssistant, entry: PixooConfigEntry) -> bool:
    """Set up a Pixoo bridge from a config entry."""
    coordinator = PixooCoordinator(hass, entry)
    await coordinator.async_start()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_setup_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PixooConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
