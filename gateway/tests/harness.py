"""A gateway on an ephemeral port, its fake upstreams, and a synthetic device.

Every user-story-1 contract test needs the same three pieces: the app over a
real socket (``aiohttp.test_utils.TestClient``), whisper/Hermes/edge-tts stand-ins
started on loopback ephemeral ports, and the device client from
``tools/fake_device.py``. This module wires them once so the tests read as the
scenario, not as setup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from jarvis_gateway.config import Config, load_config
from jarvis_gateway.ws_app import create_app
from tests.fakes.fake_edge_tts import FakeEdgeTTS
from tests.fakes.fake_hermes import FakeHermes
from tests.fakes.fake_whisper import FakeWhisper
from tools.fake_device import DeviceClient, Pacer

__all__ = ["BASE_ENV", "Harness", "start_harness"]

BASE_ENV = {
    "JG_DEVICE_TOKEN": "test-token",
    "JG_HERMES_API_KEY": "brain-key",
    "JG_PUBLIC_BASE_URL": "http://172.16.90.14:8010",
}


@dataclass
class Harness:
    client: TestClient
    app: Any
    config: Config
    whisper: FakeWhisper
    hermes: FakeHermes
    tts: FakeEdgeTTS

    @property
    def ws_url(self) -> str:
        return str(self.client.make_url(self.config.ws_path)).replace("http://", "ws://")

    async def connect(self, **kwargs: Any) -> DeviceClient:
        device = DeviceClient(
            self.ws_url,
            self.config.device_token,
            pacer=Pacer(0.0),
            **kwargs,
        )
        await device.connect()
        return device

    async def close(self) -> None:
        await self.client.close()
        await self.whisper.stop()
        await self.hermes.stop()


async def start_harness(**env: str) -> Harness:
    """Start fake upstreams, then the gateway pointed at them."""

    whisper = FakeWhisper()
    hermes = FakeHermes()
    await whisper.start()
    await hermes.start()
    tts = FakeEdgeTTS()
    config = load_config(
        {
            **BASE_ENV,
            "JG_STT_URL": whisper.base_url,
            "JG_HERMES_BASE_URL": f"{hermes.base_url}/v1",
            **env,
        }
    )
    app = create_app(config, hello_timeout=1.0, tts=tts)
    client = TestClient(TestServer(app))
    await client.start_server()
    return Harness(
        client=client,
        app=app,
        config=config,
        whisper=whisper,
        hermes=hermes,
        tts=tts,
    )
