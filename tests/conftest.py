"""Shared fixtures for the pixoo_spp test suite."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Make `custom_components.pixoo_spp` importable from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading the custom integration in every test."""
    yield


class FakeWriter:
    def __init__(self) -> None:
        self.written = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeReader:
    """A reader whose read() blocks until cancelled (a quiet, open link)."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    async def read(self, _n: int) -> bytes:
        await self._event.wait()
        return b""


@pytest.fixture
def fake_connection(monkeypatch):
    """Patch asyncio.open_connection in config_flow + coordinator with a fake stream."""
    writer = FakeWriter()

    async def _open(host, port):  # noqa: ANN001
        return FakeReader(), writer

    mock = AsyncMock(side_effect=_open)
    # The coordinator opens its connection through untether_bt.SppConnection now.
    monkeypatch.setattr("untether_bt.connection.asyncio.open_connection", mock)
    monkeypatch.setattr("custom_components.pixoo_spp.config_flow.asyncio.open_connection", mock)
    return writer, mock
