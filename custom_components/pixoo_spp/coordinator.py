"""State coordinator for a Pixoo behind an untether_spp bridge.

The ESP32 ``untether_spp`` component is a transparent Classic-SPP <-> TCP pipe. The connection
lifecycle — persistent connect, the 0xAF handshake on connect, a background reader, serialised
writes, capped-backoff reconnect, and a staleness watchdog — is the reusable transport provided by
``untether_bt.SppConnection``; this coordinator owns only the Pixoo-specific bits: deframing inbound
bytes, decoding state, and the chunked animation upload.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from untether_bt import SppConnection

from . import protocol as proto
from .const import (
    CONNECT_TIMEOUT,
    DOMAIN,
    RECONNECT_MAX,
    RECONNECT_MIN,
    STALE_AFTER,
)

_LOGGER = logging.getLogger(__name__)

type PixooConfigEntry = ConfigEntry["PixooCoordinator"]


class PixooCoordinator(DataUpdateCoordinator[dict]):
    """Owns the bridge connection and the decoded device state."""

    def __init__(self, hass: HomeAssistant, entry: PixooConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.data['host']}",
            update_interval=None,  # push-only; the reader task drives updates
        )
        self.entry = entry
        self.host: str = entry.data["host"]
        self.port: int = entry.data["port"]

        self._anim_acks: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
        self._leftover = b""
        self._conn = SppConnection(
            self.host,
            self.port,
            on_chunk=self._on_chunk,
            on_connect=self._on_connect,
            on_state=lambda _up: self.async_update_listeners(),
            connect_timeout=CONNECT_TIMEOUT,
            reconnect_min=RECONNECT_MIN,
            reconnect_max=RECONNECT_MAX,
            stale_after=STALE_AFTER,
            logger=_LOGGER,
        )

    @property
    def connected(self) -> bool:
        return self._conn.connected

    async def _async_update_data(self) -> dict:
        # State is pushed by the reader; the first refresh just returns what we have.
        return self.data or {}

    async def async_start(self) -> None:
        """Launch the persistent connection loop."""
        await self._conn.start()

    async def async_stop(self) -> None:
        await self._conn.stop()

    async def _on_connect(self) -> None:
        """Reset the deframer and send the handshake (runs on every (re)connect)."""
        self._leftover = b""
        await self._conn.send(proto.handshake_frame())

    def _on_chunk(self, chunk: bytes) -> None:
        frames, self._leftover = proto.iter_frames(self._leftover + chunk)
        self._handle_frames(frames)

    def _handle_frames(self, frames: list[tuple[int, bytes]]) -> None:
        new_state = dict(self.data or {})
        changed = False
        for type_byte, args in frames:
            reply = proto.parse_reply(type_byte, args)
            if reply is None:
                continue
            opcode, data = reply
            if opcode == proto.OP_ANIM:
                self._anim_acks.put_nowait((opcode, data))
            elif opcode == proto.OP_STATE_ECHO:
                decoded = proto.decode_state_echo(data)
                if decoded:
                    new_state.update(decoded)
                    changed = True
        if changed:
            self.async_set_updated_data(new_state)

    async def async_send(self, frame: bytes) -> None:
        """Serialise a single framed write to the device."""
        await self._conn.send(frame)

    async def async_send_animation(self, payload: bytes) -> None:
        """Port of the 0x8b chunked upload: START -> wait ready-ACK -> stream -> resends."""
        chunks = proto.anim_chunks(payload)
        gap = 0.022 if len(chunks) <= 500 else 0.035
        async with self._conn.write_lock:
            # 1) START + 2) wait the device's ready-ACK (drain stale acks first)
            while not self._anim_acks.empty():
                self._anim_acks.get_nowait()
            await self._conn.send_raw(proto.anim_start_frame(len(payload)))
            try:
                await asyncio.wait_for(self._anim_acks.get(), timeout=1.0)
            except asyncio.TimeoutError:
                await asyncio.sleep(0.12)
            # 3) stream chunks (pace the first ~400 slower — flash write warm-up)
            for j, c in enumerate(chunks):
                await self._conn.send_raw(c)
                await asyncio.sleep(gap * 1.8 if j < 400 else gap)
            # 4) serve resend requests for ~1s
            deadline = self.hass.loop.time() + 1.0
            resent = 0
            while self.hass.loop.time() < deadline:
                try:
                    timeout = max(0.0, deadline - self.hass.loop.time())
                    _op, data = await asyncio.wait_for(self._anim_acks.get(), timeout=timeout or 0.01)
                except asyncio.TimeoutError:
                    break
                k = proto.parse_resend_index(data)
                if k is not None and 0 <= k < len(chunks):
                    await self._conn.send_raw(chunks[k])
                    resent += 1
                    await asyncio.sleep(0.006)
            if resent:
                _LOGGER.debug("Pixoo anim: re-sent %d dropped chunk(s)", resent)
