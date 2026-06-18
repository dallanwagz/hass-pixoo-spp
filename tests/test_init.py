"""Setup / entity / service tests for pixoo_spp."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from custom_components.pixoo_spp import protocol as proto
from custom_components.pixoo_spp.const import (
    DOMAIN,
    SERVICE_PUSH_IMAGE,
    SERVICE_PUSH_SOLID,
    SERVICE_UPLOAD_ANIMATION,
)


async def _setup(hass: HomeAssistant):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.1.50:8888",
        data={"host": "192.168.1.50", "port": 8888},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_entities_and_services(
    hass: HomeAssistant, fake_connection
) -> None:
    writer, _mock = fake_connection
    entry = await _setup(hass)

    # the handshake was sent on connect
    assert writer.written.hex().startswith(proto.handshake_frame().hex())

    assert hass.states.get("light.divoom_pixoo_16_brightness") is not None
    assert hass.states.get("sensor.divoom_pixoo_16_brightness_reported") is not None
    assert hass.states.get("binary_sensor.divoom_pixoo_16_bridge_link") is not None

    for svc in (SERVICE_PUSH_SOLID, SERVICE_PUSH_IMAGE, SERVICE_UPLOAD_ANIMATION):
        assert hass.services.has_service(DOMAIN, svc)

    # unload cleanly
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_push_solid_writes_golden_frame(
    hass: HomeAssistant, fake_connection
) -> None:
    writer, _mock = fake_connection
    await _setup(hass)
    writer.written.clear()

    await hass.services.async_call(
        DOMAIN, SERVICE_PUSH_SOLID, {"rgb": [255, 0, 0]}, blocking=True
    )
    assert writer.written.hex() == "01110044000a0a04aa0a0064000001ff0000850202"


async def test_light_turn_on_sets_brightness(
    hass: HomeAssistant, fake_connection
) -> None:
    writer, _mock = fake_connection
    await _setup(hass)
    writer.written.clear()

    await hass.services.async_call(
        Platform.LIGHT,
        "turn_on",
        {"entity_id": "light.divoom_pixoo_16_brightness", "brightness": 255},
        blocking=True,
    )
    # brightness 255 -> device level 100 -> golden brightness frame
    assert writer.written.hex() == proto.brightness_frame(100).hex()


async def test_state_echo_updates_brightness_sensor(
    hass: HomeAssistant, fake_connection
) -> None:
    await _setup(hass)
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data

    echo = bytes.fromhex("011b00044655000000ff5000640001036400ffffff00010000000000d30502")
    frames, _ = proto.iter_frames(echo)
    coordinator._handle_frames(frames)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.divoom_pixoo_16_brightness_reported").state == "100"
