"""Prove the UI feed end to end, with no robot: one event, then a flood.

This is the card's evidence run. It starts the real gateway against the fake
upstreams, connects the synthetic StackChan (which registers the four
``self.ui.*`` tools on its MCP server), publishes one simulated git event and
then 100 log lines, and prints what reached the device plus the bus counters.

    cd gateway && .venv/bin/python -m tools.ui_feed_demo
"""

from __future__ import annotations

import asyncio
import json
import tempfile

from jarvis_gateway.ui import UI_LOG_LINE
from jarvis_gateway.ui_events import UiEvent
from jarvis_gateway.ws_app import UI_BUS
from tests.harness import start_harness


def _is_log_call(message: dict) -> bool:
    payload = message.get("payload") or {}
    params = payload.get("params") or {}
    return payload.get("method") == "tools/call" and params.get("name") == UI_LOG_LINE


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ui-demo-ack-") as ack_dir:
        harness = await start_harness(JG_ACK_CACHE_DIR=ack_dir)
        device = None
        try:
            device = await harness.connect()
            bus = harness.app[UI_BUS]

            queued = bus.publish_source(
                "git",
                {
                    "action": "closed",
                    "pull_request": {
                        "number": 11,
                        "title": "docs/ui-concept",
                        "merged": True,
                    },
                },
            )
            message = await device.wait_for_mcp_call(UI_LOG_LINE, timeout=1.0)
            await asyncio.sleep(0.05)
            print(f"publish_source(git) queued {queued} event(s)")
            print("device received:")
            print(json.dumps(message, indent=2, ensure_ascii=False))
            print(f"channel stats after one call: {bus.channel.stats}")

            for index in range(100):
                bus.publish(UiEvent("log_line", {"text": f"flood {index}"}))
            await asyncio.sleep(0.4)
            delivered = [m for m in device.log.received_json if _is_log_call(m)]
            print(f"flood: {len(delivered)} of 100 log calls reached the device")
            print(f"bus stats: {bus.stats}")
            print(f"channel stats: {bus.channel.stats}")
            return 0 if 1 <= len(delivered) <= 4 else 1
        finally:
            if device is not None:
                await device.close()
            await harness.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
