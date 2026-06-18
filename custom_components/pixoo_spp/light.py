"""Brightness light for the Pixoo (0x74)."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import protocol as proto
from .coordinator import PixooConfigEntry
from .entity import PixooEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PixooConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PixooLight(entry.runtime_data)])


def _to_ha(level_0_100: int) -> int:
    return min(255, round(level_0_100 * 255 / 100))


def _to_device(brightness_0_255: int) -> int:
    return min(100, max(0, math.ceil(brightness_0_255 * 100 / 255)))


class PixooLight(PixooEntity, LightEntity):
    """The panel's global brightness, exposed as a dimmable light."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_translation_key = "brightness"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._base_id}-brightness"
        self._last_level = 100  # remember a level so "on" restores it

    @property
    def _level(self) -> int | None:
        data = self.coordinator.data or {}
        return data.get("brightness")

    @property
    def is_on(self) -> bool:
        level = self._level
        return bool(level) if level is not None else False

    @property
    def brightness(self) -> int | None:
        level = self._level
        return _to_ha(level) if level else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            level = _to_device(kwargs[ATTR_BRIGHTNESS])
        else:
            level = self._last_level or 100
        level = max(1, level)
        self._last_level = level
        await self.coordinator.async_send(proto.brightness_frame(level))

    async def async_turn_off(self, **kwargs: Any) -> None:
        current = self._level
        if current:
            self._last_level = current
        await self.coordinator.async_send(proto.brightness_frame(0))
