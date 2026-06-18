"""Connectivity binary sensor for the Pixoo bridge link."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PixooConfigEntry
from .entity import PixooEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PixooConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PixooConnectivity(entry.runtime_data)])


class PixooConnectivity(PixooEntity, BinarySensorEntity):
    """Whether the bridge holds an open SPP link to the panel."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "link"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._base_id}-link"

    @property
    def available(self) -> bool:
        return True  # the link sensor itself is always meaningful

    @property
    def is_on(self) -> bool:
        return self.coordinator.connected
