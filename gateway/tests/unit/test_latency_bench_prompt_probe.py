"""The bench's prompt-size probe (P3.3).

The lean-vs-default comparison is only honest if both runs report how many
prompt tokens the brain actually received. The gateway's turn record does not
carry ``usage`` (``BrainReply`` is text + saw-done), so the bench asks the same
base URL itself, on a throwaway session scope, for one ``max_tokens: 1``
completion and reads ``usage.prompt_tokens``.

These tests pin the three things that matter:

* the number comes from the server's ``usage`` block, not from a local guess;
* a reply without ``usage`` yields ``None`` — never ``0`` and never an estimate;
* a rejected call raises with the status and never echoes the API key.
"""

from __future__ import annotations

import pytest
from aiohttp import web

from tools.latency_bench import _prompt_tokens

KEY = "sk-test-not-a-real-key"


class _Brain:
    """A one-endpoint stand-in for the Hermes API server."""

    def __init__(self, status: int = 200, body: dict | None = None) -> None:
        self.status = status
        self.body = body if body is not None else {}
        self.requests_: list[dict] = []
        self.paths: list[str] = []

    async def handle(self, request: web.Request) -> web.Response:
        self.requests_.append(
            {
                "headers": dict(request.headers),
                "json": await request.json(),
            }
        )
        self.paths.append(request.path)
        return web.json_response(self.body, status=self.status)


@pytest.fixture
async def brain_factory():
    started: list[tuple[web.AppRunner, _Brain]] = []

    async def start(status: int = 200, body: dict | None = None) -> tuple[str, _Brain]:
        brain = _Brain(status=status, body=body)
        app = web.Application()
        app.router.add_post("/{tail:.*}", brain.handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        started.append((runner, brain))
        return f"http://127.0.0.1:{port}/p/jarvis-device/v1", brain

    yield start

    for runner, _ in started:
        await runner.cleanup()


async def test_prompt_tokens_come_from_the_servers_usage_block(brain_factory) -> None:
    base_url, brain = await brain_factory(
        body={
            "choices": [{"message": {"role": "assistant", "content": "p"}}],
            "usage": {"prompt_tokens": 16_700, "completion_tokens": 1, "total_tokens": 16_701},
        }
    )

    tokens = await _prompt_tokens(
        base_url=base_url,
        api_key=KEY,
        model="hermes-agent",
        session_key="stackchan-bench-probe-deadbeef",
    )

    assert tokens == 16_700
    # The probe must be a real completion request on the channel under test:
    # the profile path, the model, one token, and the throwaway scope.
    sent = brain.requests_[0]
    assert brain.paths[0] == "/p/jarvis-device/v1/chat/completions"
    assert sent["json"]["model"] == "hermes-agent"
    assert sent["json"]["max_tokens"] == 1
    assert sent["json"]["stream"] is False
    assert sent["headers"]["Authorization"] == f"Bearer {KEY}"
    assert sent["headers"]["X-Hermes-Session-Key"] == "stackchan-bench-probe-deadbeef"


async def test_a_reply_without_usage_is_none_not_zero(brain_factory) -> None:
    base_url, _ = await brain_factory(body={"choices": [{"message": {"content": "p"}}]})

    tokens = await _prompt_tokens(
        base_url=base_url,
        api_key=KEY,
        model="hermes-agent",
        session_key="stackchan-bench-probe-cafe",
    )

    assert tokens is None


async def test_a_usage_block_without_a_prompt_count_is_none(brain_factory) -> None:
    base_url, _ = await brain_factory(body={"usage": {"completion_tokens": 1}})

    tokens = await _prompt_tokens(
        base_url=base_url,
        api_key=KEY,
        model="hermes-agent",
        session_key="stackchan-bench-probe-feed",
    )

    assert tokens is None


async def test_a_rejected_call_raises_with_the_status_and_never_the_key(brain_factory) -> None:
    base_url, _ = await brain_factory(status=401, body={"error": "Invalid gateway API key"})

    with pytest.raises(RuntimeError) as excinfo:
        await _prompt_tokens(
            base_url=base_url,
            api_key=KEY,
            model="hermes-agent",
            session_key="stackchan-bench-probe-b00m",
        )

    message = str(excinfo.value)
    assert "401" in message
    assert KEY not in message


async def test_a_base_url_with_a_trailing_slash_still_hits_one_slash(brain_factory) -> None:
    base_url, brain = await brain_factory(body={"usage": {"prompt_tokens": 42}})

    tokens = await _prompt_tokens(
        base_url=base_url + "/",
        api_key=KEY,
        model="hermes-agent",
        session_key="stackchan-bench-probe-5eed",
    )

    assert tokens == 42
    assert brain.paths[0] == "/p/jarvis-device/v1/chat/completions"


def test_the_probe_is_in_the_bench_parser_as_a_flag() -> None:
    """A run must be able to skip the probe without editing code."""

    from tools.latency_bench import build_parser

    args = build_parser().parse_args([])
    assert args.prompt_probe is True
    assert build_parser().parse_args(["--no-prompt-probe"]).prompt_probe is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
