"""Integration services: push a still / solid colour / animation to the Pixoo."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from . import protocol as proto
from .const import (
    DOMAIN,
    SERVICE_PUSH_IMAGE,
    SERVICE_PUSH_SOLID,
    SERVICE_UPLOAD_ANIMATION,
)
from .coordinator import PixooCoordinator

ATTR_DEVICE_ID = "device_id"
ATTR_RGB = "rgb"
ATTR_PALETTE = "palette"
ATTR_PIXELS = "pixels"
ATTR_FRAMES = "frames"
ATTR_TIMECODE = "timecode"

_RGB = vol.All([vol.All(vol.Coerce(int), vol.Range(0, 255))], vol.Length(min=3, max=3))
_PALETTE = vol.All(
    [vol.All(vol.Coerce(int), vol.Range(0, 255))],
    vol.Length(min=3, max=48),  # up to 16 colours (flattened RGB)
)
_PIXELS = vol.All(
    [vol.All(vol.Coerce(int), vol.Range(0, 15))], vol.Length(min=256, max=256)
)

_SOLID_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_DEVICE_ID): cv.string, vol.Required(ATTR_RGB): _RGB}
)
_IMAGE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_PALETTE): _PALETTE,
        vol.Required(ATTR_PIXELS): _PIXELS,
    }
)
_ANIM_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_PALETTE): _PALETTE,
        vol.Required(ATTR_FRAMES): vol.All([_PIXELS], vol.Length(min=1)),
        vol.Optional(ATTR_TIMECODE, default=100): vol.All(
            vol.Coerce(int), vol.Range(1, 65535)
        ),
    }
)


def _flat_to_palette(flat: list[int]) -> list[tuple[int, int, int]]:
    return [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat), 3)]


def _resolve_coordinator(hass: HomeAssistant, call: ServiceCall) -> PixooCoordinator:
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state.recoverable and getattr(e, "runtime_data", None)
    ]
    device_id = call.data.get(ATTR_DEVICE_ID)
    if device_id:
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get(device_id)
        if device is None:
            raise ServiceValidationError(f"Unknown device_id {device_id}")
        entry_ids = device.config_entries
        entries = [e for e in entries if e.entry_id in entry_ids]
    if not entries:
        raise ServiceValidationError("No loaded Pixoo device matched this call")
    if len(entries) > 1:
        raise ServiceValidationError(
            "Multiple Pixoo devices configured — pass device_id to pick one"
        )
    return entries[0].runtime_data


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services once."""
    if hass.services.has_service(DOMAIN, SERVICE_PUSH_SOLID):
        return

    async def _push_solid(call: ServiceCall) -> None:
        coord = _resolve_coordinator(hass, call)
        r, g, b = call.data[ATTR_RGB]
        await coord.async_send(proto.solid_still_frame((r, g, b)))

    async def _push_image(call: ServiceCall) -> None:
        coord = _resolve_coordinator(hass, call)
        palette = _flat_to_palette(call.data[ATTR_PALETTE])
        frame = proto.still_frame(palette, call.data[ATTR_PIXELS])
        await coord.async_send(frame)

    async def _upload_animation(call: ServiceCall) -> None:
        coord = _resolve_coordinator(hass, call)
        palette = _flat_to_palette(call.data[ATTR_PALETTE])
        payload = proto.encode_anim_payload(
            palette, call.data[ATTR_FRAMES], timecode=call.data[ATTR_TIMECODE]
        )
        try:
            await coord.async_send_animation(payload)
        except OSError as err:
            raise HomeAssistantError(f"Animation upload failed: {err}") from err

    hass.services.async_register(DOMAIN, SERVICE_PUSH_SOLID, _push_solid, _SOLID_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_PUSH_IMAGE, _push_image, _IMAGE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_UPLOAD_ANIMATION, _upload_animation, _ANIM_SCHEMA
    )
