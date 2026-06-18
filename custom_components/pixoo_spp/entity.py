"""Shared entity base for the Pixoo integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PixooCoordinator


class PixooEntity(CoordinatorEntity[PixooCoordinator]):
    """Base entity tied to one Pixoo bridge."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PixooCoordinator) -> None:
        super().__init__(coordinator)
        self._base_id = f"{coordinator.host}:{coordinator.port}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._base_id)},
            name="Divoom Pixoo 16",
            manufacturer="Divoom",
            model="Pixoo 16",
            configuration_url=f"http://{coordinator.host}",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.connected
