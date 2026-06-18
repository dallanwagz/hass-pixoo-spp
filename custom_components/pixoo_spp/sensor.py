"""Brightness read-back sensor for the Pixoo (decoded from the 0x46 state echo)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PixooConfigEntry
from .entity import PixooEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PixooConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PixooBrightnessSensor(entry.runtime_data)])


class PixooBrightnessSensor(PixooEntity, SensorEntity):
    """Brightness as reported by the device (0..100)."""

    _attr_translation_key = "device_brightness"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._base_id}-device-brightness"

    @property
    def native_value(self) -> int | None:
        return (self.coordinator.data or {}).get("brightness")
