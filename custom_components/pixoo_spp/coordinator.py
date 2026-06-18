"""TCP transport + state coordinator for a Pixoo behind an untether_spp bridge.

The ESP32 ``untether_spp`` component is a transparent Classic-SPP <-> TCP pipe, so this
coordinator simply opens an asyncio TCP stream to ``tcp://<bridge>:<port>`` and speaks the
Pixoo wire protocol over it — the exact same ``protocol.py`` you'd use for a BLE device.

It owns one persistent connection: sends the 0xAF handshake on connect, reads framed bytes
in a background task (pushing decoded state to entities), serialises writes behind a lock,
reconnects with capped backoff, and tears down on staleness.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._run_task: asyncio.Task | None = None
        self._anim_acks: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def _async_update_data(self) -> dict:
        # State is pushed by the reader; the first refresh just returns what we have.
        return self.data or {}

    async def async_start(self) -> None:
        """Launch the persistent connection loop."""
        self._run_task = self.entry.async_create_background_task(
            self.hass, self._run(), f"{DOMAIN}-{self.host}-conn"
        )

    async def async_stop(self) -> None:
        if self._run_task:
            self._run_task.cancel()
            self._run_task = None
        await self._close()

    async def _close(self) -> None:
        self._connected = False
        writer, self._writer, self._reader = self._writer, None, None
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, asyncio.CancelledError):
                pass

    async def _run(self) -> None:
        backoff = RECONNECT_MIN
        while True:
            try:
                await self._connect_once()
                backoff = RECONNECT_MIN
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.debug("Pixoo bridge %s: %s", self.host, err)
            finally:
                await self._close()
                self.async_update_listeners()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _connect_once(self) -> None:
        _LOGGER.debug("Connecting to Pixoo bridge %s:%s", self.host, self.port)
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=CONNECT_TIMEOUT
        )
        await self._raw_send(proto.handshake_frame())
        self._connected = True
        _LOGGER.info("Pixoo bridge %s:%s connected (handshake sent)", self.host, self.port)
        self.async_update_listeners()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        leftover = b""
        while True:
            try:
                chunk = await asyncio.wait_for(self._reader.read(512), timeout=STALE_AFTER)
            except asyncio.TimeoutError as err:
                raise OSError("stale: no data") from err
            if not chunk:
                raise OSError("connection closed by peer")
            frames, leftover = proto.iter_frames(leftover + chunk)
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

    async def _raw_send(self, frame: bytes) -> None:
        if self._writer is None:
            raise OSError("not connected")
        self._writer.write(frame)
        await self._writer.drain()

    async def async_send(self, frame: bytes) -> None:
        """Serialise a single framed write to the device."""
        async with self._write_lock:
            await self._raw_send(frame)

    async def async_send_animation(self, payload: bytes) -> None:
        """Port of the 0x8b chunked upload: START -> wait ready-ACK -> stream -> resends."""
        chunks = proto.anim_chunks(payload)
        gap = 0.022 if len(chunks) <= 500 else 0.035
        async with self._write_lock:
            # 1) START + 2) wait the device's ready-ACK (drain stale acks first)
            while not self._anim_acks.empty():
                self._anim_acks.get_nowait()
            await self._raw_send(proto.anim_start_frame(len(payload)))
            try:
                await asyncio.wait_for(self._anim_acks.get(), timeout=1.0)
            except asyncio.TimeoutError:
                await asyncio.sleep(0.12)
            # 3) stream chunks (pace the first ~400 slower — flash write warm-up)
            for j, c in enumerate(chunks):
                await self._raw_send(c)
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
                    await self._raw_send(chunks[k])
                    resent += 1
                    await asyncio.sleep(0.006)
            if resent:
                _LOGGER.debug("Pixoo anim: re-sent %d dropped chunk(s)", resent)
