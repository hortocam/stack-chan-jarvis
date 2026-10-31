"""Fake upstreams for hardware-free proofs (T006, constitution V).

`fake_whisper` and `fake_hermes` bind ``127.0.0.1:0`` — an ephemeral port, never
the real services' ports — and are torn down by the fixture. `fake_edge_tts` is
an object, not a server, because edge-tts is a library call.

The real services are never contacted by a test unless it carries the ``live``
marker, and ``pytest`` deselects that marker by default.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.fakes.fake_edge_tts import FakeEdgeTTS, FakeTTSError
from tests.fakes.fake_hermes import FakeHermes
from tests.fakes.fake_whisper import FakeWhisper

__all__ = [
    "FakeEdgeTTS",
    "FakeHermes",
    "FakeTTSError",
    "FakeWhisper",
    "fake_edge_tts",
    "fake_hermes",
    "fake_whisper",
]


@pytest.fixture
async def fake_whisper() -> AsyncIterator[FakeWhisper]:
    """A whisper stand-in listening on an ephemeral loopback port."""
    server = FakeWhisper()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def fake_hermes() -> AsyncIterator[FakeHermes]:
    """A Hermes brain stand-in listening on an ephemeral loopback port."""
    server = FakeHermes()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
def fake_edge_tts() -> FakeEdgeTTS:
    """A monkeypatchable edge-tts stand-in."""
    return FakeEdgeTTS()
