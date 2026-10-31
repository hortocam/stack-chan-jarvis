"""Registers the shared fixtures: fake upstreams and the gateway harness.

The fake classes live in ``tests/fakes`` (T006) so a test module can import them
directly; the harness in ``tests/harness.py`` bundles app + fakes + device.
"""

from __future__ import annotations

import pytest

from tests.fakes import fake_edge_tts, fake_hermes, fake_whisper
from tests.harness import Harness, start_harness

__all__ = [
    "fake_edge_tts",
    "fake_hermes",
    "fake_whisper",
    "harness",
    "harness_factory",
]


@pytest.fixture
async def harness_factory():
    """Start one or more gateways (each with its own fakes) and close them all."""

    started: list[Harness] = []

    async def start(**env: str) -> Harness:
        instance = await start_harness(**env)
        started.append(instance)
        return instance

    try:
        yield start
    finally:
        for instance in started:
            await instance.close()


@pytest.fixture
async def harness(harness_factory) -> Harness:
    return await harness_factory()
