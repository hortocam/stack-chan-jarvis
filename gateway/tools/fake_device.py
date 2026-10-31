"""The synthetic StackChan — the device half of the xiaozhi protocol (T027).

Ported from ``projects/jarvis-hardware/sim/synthetic-device`` (its 13 tests are
the reference for the device side) onto the gateway's own dependencies, and
extended with the ``--expect-loop`` assertion the plan's independent test names:

    python -m tools.fake_device --url ws://127.0.0.1:8010/xiaozhi/v1/ \\
        --token "$JG_DEVICE_TOKEN" --wav tests/fixtures/audio/utterance_speech.wav \\
        --expect-loop --out-dir /tmp/turn

It speaks exactly what ``78/xiaozhi-esp32`` v2.2.4 ``docs/websocket.md`` says the
robot says: four handshake headers, a ``hello`` with 16 kHz mono Opus and 60 ms
frames, ``listen`` start/stop, and one Opus packet per binary frame at 60 ms
intervals. It knows nothing about whisper, Hermes or edge-tts; it is the CI
harness, not the gateway.

Exit codes: 0 the loop completed as asserted, 1 the gateway violated the
contract, 2 usage/configuration error.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from jarvis_gateway.audio import UPLINK_RATE, OpusEncoder, frame_60ms
from jarvis_gateway.emotion import ACCEPTED_EMOTIONS

__all__ = [
    "DEFAULT_DEVICE_ID",
    "DeviceClient",
    "DeviceError",
    "Pacer",
    "SequenceMismatch",
    "check_loop_sequence",
    "main",
    "read_wav_16k",
]

DEFAULT_DEVICE_ID = "24:0a:c4:00:11:22"  # looks like a real ESP32 MAC
FRAME_DURATION_S = 0.06


class DeviceError(RuntimeError):
    """The gateway did something the firmware would not tolerate."""


class SequenceMismatch(DeviceError):
    """The received message sequence is not the one the plan's loop asserts."""


def read_wav_16k(path: str | Path) -> tuple[bytes, int]:
    """Read a mono 16 kHz PCM16 WAV; anything else is a configuration error."""

    path = Path(path)
    if not path.exists():
        raise ValueError(f"{path} does not exist")
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            pcm = handle.readframes(handle.getnframes())
    except wave.Error as exc:
        raise ValueError(f"{path} is not a readable WAV: {exc}") from exc
    if channels != 1 or width != 2 or rate != UPLINK_RATE:
        raise ValueError(
            f"{path} must be mono 16-bit PCM16 at {UPLINK_RATE} Hz, "
            f"got {channels}ch/{width * 8}bit/{rate}Hz"
        )
    return pcm, rate


@dataclass
class Pacer:
    """60 ms per frame, exactly like the firmware's microphone timer."""

    delay_s: float = FRAME_DURATION_S
    sleep: Any = None

    def __post_init__(self) -> None:
        if self.sleep is None:
            self.sleep = asyncio.sleep

    async def frame(self) -> None:
        if self.delay_s <= 0:
            return
        result = self.sleep(self.delay_s)
        if inspect.isawaitable(result):
            await result


def build_device_hello(*, features_mcp: bool = True) -> dict[str, Any]:
    return {
        "type": "hello",
        "version": 1,
        "features": {"mcp": features_mcp},
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": UPLINK_RATE,
            "channels": 1,
            "frame_duration": 60,
        },
    }


@dataclass
class SessionLog:
    sent_json: list[dict[str, Any]] = field(default_factory=list)
    received_json: list[dict[str, Any]] = field(default_factory=list)
    received_audio: list[bytes] = field(default_factory=list)
    sent_frames: int = 0
    frame_send_times: list[float] = field(default_factory=list)
    server_hello: dict[str, Any] | None = None
    session_id: str | None = None

    def after_hello(self) -> list[dict[str, Any]]:
        return [m for m in self.received_json if m.get("type") != "hello"]

    def types(self) -> list[str]:
        return [m.get("type") for m in self.received_json]


class DeviceClient:
    """One synthetic StackChan over one WebSocket session."""

    def __init__(
        self,
        ws_url: str,
        token: str,
        *,
        device_id: str = DEFAULT_DEVICE_ID,
        client_id: str | None = None,
        features_mcp: bool = True,
        pacer: Pacer | None = None,
        hello_timeout_s: float = 10.0,
    ) -> None:
        if not ws_url.startswith(("ws://", "wss://")):
            raise ValueError("ws_url must start with ws:// or wss://")
        if not token:
            raise ValueError("token is required")
        self.ws_url = ws_url
        self.token = token
        self.device_id = device_id
        self.client_id = client_id or str(uuid.uuid4())
        self.features_mcp = features_mcp
        self.pacer = pacer or Pacer()
        self.hello_timeout_s = hello_timeout_s
        self.log = SessionLog()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._reader: asyncio.Task | None = None
        self._condition = asyncio.Condition()
        self._revision = 0

    # -- handshake -------------------------------------------------------

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Protocol-Version": "1",
            "Device-Id": self.device_id,
            "Client-Id": self.client_id,
        }

    def hello_payload(self) -> dict[str, Any]:
        return build_device_hello(features_mcp=self.features_mcp)

    async def connect(self, session: aiohttp.ClientSession | None = None) -> dict[str, Any]:
        if session is None:
            self._session = aiohttp.ClientSession()
            session = self._session
        try:
            self._ws = await session.ws_connect(
                self.ws_url,
                headers=self.headers(),
                max_msg_size=0,
                heartbeat=None,
                timeout=aiohttp.ClientWSTimeout(ws_close=self.hello_timeout_s),
            )
        except aiohttp.WSServerHandshakeError as exc:
            raise DeviceError(f"handshake rejected with HTTP {exc.status}") from exc
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise DeviceError(f"cannot connect to {self.ws_url}: {exc}") from exc

        self._reader = asyncio.create_task(self._read_loop())
        await self.send_json(self.hello_payload())
        hello = await self.wait_for(
            lambda message: message.get("type") == "hello", self.hello_timeout_s
        )
        session_id = hello.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise DeviceError(f"server hello has no session_id: {hello!r}")
        params = hello.get("audio_params")
        if not isinstance(params, dict) or params.get("sample_rate") != 24_000:
            raise DeviceError(f"server hello has no 24 kHz audio_params: {hello!r}")
        self.log.server_hello = hello
        self.log.session_id = session_id
        return hello

    # -- sending ---------------------------------------------------------

    async def send_json(self, message: dict[str, Any]) -> None:
        if self._ws is None:
            raise DeviceError("not connected")
        self.log.sent_json.append(message)
        await self._ws.send_str(json.dumps(message))

    async def listen(self, state: str, *, mode: str = "auto") -> None:
        if state not in {"start", "stop", "detect"}:
            raise ValueError("state must be start, stop or detect")
        await self.send_json(
            {"type": "listen", "state": state, "mode": mode, "session_id": self.log.session_id}
        )

    async def abort(self, *, reason: str = "wake_word_detected") -> None:
        await self.send_json({"type": "abort", "reason": reason, "session_id": self.log.session_id})

    async def send_packets(self, packets: list[bytes]) -> int:
        """Send pre-encoded 60 ms Opus packets, one binary frame each, paced."""

        for packet in packets:
            if self._ws is None:
                raise DeviceError("not connected")
            await self._ws.send_bytes(packet)
            self.log.sent_frames += 1
            self.log.frame_send_times.append(time.monotonic())
            await self.pacer.frame()
        return len(packets)

    async def stream_wav(self, path: str | Path, *, count: int | None = None) -> int:
        """Encode a WAV to 16 kHz Opus and send one packet per 60 ms frame."""

        pcm, _ = read_wav_16k(path)
        frames = frame_60ms(pcm, UPLINK_RATE)
        if count is not None:
            frames = frames[:count]
        encoder = OpusEncoder(UPLINK_RATE)
        try:
            packets = [encoder.encode(frame) for frame in frames]
        finally:
            encoder.close()
        return await self.send_packets(packets)

    # -- receiving -------------------------------------------------------

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                async with self._condition:
                    if message.type is aiohttp.WSMsgType.TEXT:
                        self.log.received_json.append(json.loads(message.data))
                    elif message.type is aiohttp.WSMsgType.BINARY:
                        self.log.received_audio.append(bytes(message.data))
                    self._revision += 1
                    self._condition.notify_all()
        except (asyncio.CancelledError, aiohttp.ClientError, json.JSONDecodeError):
            pass

    def tts_stops(self) -> list[dict[str, Any]]:
        return [
            m
            for m in self.log.received_json
            if m.get("type") == "tts" and m.get("state") == "stop"
        ]

    async def wait_for_tts_stop(self, timeout: float) -> dict[str, Any]:  # noqa: ASYNC109
        """Wait for a ``tts stop`` that arrives after this call started."""

        since = len(self.log.received_json)
        return await self.wait_for(
            lambda m: m.get("type") == "tts" and m.get("state") == "stop",
            timeout,
            since=since,
        )

    async def wait_for(
        self,
        predicate,
        timeout: float,  # noqa: ASYNC109 - asyncio.wait_for does the timing
        *,
        since: int = 0,
    ) -> dict[str, Any]:
        async with self._condition:

            async def _wait() -> dict[str, Any]:
                while True:
                    for message in self.log.received_json[since:]:
                        if predicate(message):
                            return message
                    await self._condition.wait()

            try:
                return await asyncio.wait_for(_wait(), timeout)
            except TimeoutError as exc:
                raise DeviceError(
                    f"no matching message within {timeout:g}s; saw {self.log.types()}"
                ) from exc

    async def wait_quiet(self, seconds: float) -> bool:
        """True when nothing arrived for ``seconds`` (the turn really ended)."""

        async with self._condition:
            revision = self._revision

            async def _wait() -> None:
                while self._revision == revision:
                    await self._condition.wait()

            try:
                await asyncio.wait_for(_wait(), seconds)
            except TimeoutError:
                return True
            return False

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self._session is not None:
            await self._session.close()
            self._session = None


def check_loop_sequence(
    messages: list[dict[str, Any]],
    *,
    audio_frames: int,
    session_id: str,
    quiet: bool = True,
) -> None:
    """Assert hello → stt → llm → tts start → sentence → frames → one stop."""

    messages = [m for m in messages if m.get("type") != "hello"]
    if not messages:
        raise SequenceMismatch("no messages after hello")

    index = 0
    first = messages[index]
    if first.get("type") != "stt" or not first.get("text"):
        raise SequenceMismatch(f"first message must be a non-empty stt, got {first!r}")
    index += 1

    if index >= len(messages) or messages[index].get("type") != "llm":
        raise SequenceMismatch(f"expected llm after stt, got {messages[index:index + 2]!r}")
    emotion = messages[index].get("emotion")
    if emotion not in ACCEPTED_EMOTIONS:
        raise SequenceMismatch(f"llm emotion {emotion!r} is not in the firmware set")
    index += 1

    if index >= len(messages):
        raise SequenceMismatch("missing tts start")
    if messages[index].get("type") != "tts" or messages[index].get("state") != "start":
        raise SequenceMismatch(f"expected tts start, got {messages[index]!r}")
    index += 1

    sentences = 0
    while index < len(messages):
        message = messages[index]
        if message.get("type") == "tts" and message.get("state") == "sentence_start":
            if not message.get("text"):
                raise SequenceMismatch("tts sentence_start has no text")
            sentences += 1
            index += 1
            continue
        break
    if sentences == 0:
        raise SequenceMismatch("no tts sentence_start before the audio")
    if index >= len(messages):
        raise SequenceMismatch("missing tts stop")
    stop = messages[index]
    if stop.get("type") != "tts" or stop.get("state") != "stop":
        raise SequenceMismatch(f"expected tts stop, got {stop!r}")
    index += 1

    if index != len(messages):
        raise SequenceMismatch(f"unexpected messages after tts stop: {messages[index:]!r}")
    if quiet and audio_frames < 1:
        raise SequenceMismatch("tts start/stop arrived but no binary audio frame did")

    for message in messages:
        if message.get("session_id") != session_id:
            raise SequenceMismatch(
                f"message carries session_id {message.get('session_id')!r}, "
                f"expected {session_id!r}: {message!r}"
            )


# -- CLI -------------------------------------------------------------------


def _write_audio(out_dir: Path, packets: list[bytes]) -> int:
    """Write the received downlink packets as ``NNN.opus`` (sync: run in a thread)."""

    out_dir.mkdir(parents=True, exist_ok=True)
    for index, packet in enumerate(packets, start=1):
        (out_dir / f"{index:03d}.opus").write_bytes(packet)
    return len(packets)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fake_device",
        description="Play one StackChan session against the Jarvis gateway.",
    )
    parser.add_argument("--url", required=True, help="ws:// URL of the voice socket")
    parser.add_argument("--token", required=True, help="shared device token")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--wav", help="16 kHz mono PCM16 utterance to speak")
    source.add_argument(
        "--ota",
        action="store_true",
        help="fetch the OTA manifest at --url and exit (placeholder until T034)",
    )
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID, help="fake MAC address")
    parser.add_argument("--client-id", default=None, help="client UUID (random if omitted)")
    parser.add_argument("--frames", type=int, default=None, help="stop after N frames")
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="seconds to wait for tts stop"
    )
    parser.add_argument("--out-dir", default=None, help="write received audio frames here")
    parser.add_argument("--expect-loop", action="store_true", help="assert the full turn sequence")
    parser.add_argument("--no-pace", action="store_true", help="send frames back to back")
    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.ota:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                args.url,
                headers={"Device-Id": args.device_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                document = await response.json()
        print(json.dumps(document, indent=2, ensure_ascii=False))
        return 0

    pacer = Pacer(delay_s=0.0 if args.no_pace else FRAME_DURATION_S)
    client = DeviceClient(
        args.url,
        args.token,
        device_id=args.device_id,
        client_id=args.client_id,
        pacer=pacer,
    )
    started = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            hello = await client.connect(session)
            print(f"session {hello['session_id']} open (device {args.device_id})")
            await client.listen("start")
            frames = await client.stream_wav(args.wav, count=args.frames)
            print(
                f"sent {frames} uplink frames ({frames * 60} ms) "
                f"in {time.monotonic() - started:.2f}s"
            )
            await client.listen("stop")
            stop = await client.wait_for(
                lambda m: m.get("type") == "tts" and m.get("state") == "stop", args.timeout
            )
            quiet = await client.wait_quiet(0.3)
            audio_ms = len(client.log.received_audio) * 60
            print(
                f"tts stop after {time.monotonic() - started:.2f}s; "
                f"{len(client.log.received_audio)} audio frames ({audio_ms} ms)"
            )

            if args.expect_loop:
                check_loop_sequence(
                    client.log.after_hello(),
                    audio_frames=len(client.log.received_audio),
                    session_id=stop.get("session_id", ""),
                    quiet=quiet,
                )
                print("--expect-loop: sequence OK")

            if args.out_dir:
                written = await asyncio.to_thread(
                    _write_audio, Path(args.out_dir), client.log.received_audio
                )
                print(f"wrote {written} packets to {args.out_dir}")
            return 0
    except (DeviceError, ValueError) as exc:
        print(f"fake_device: {exc}", file=sys.stderr)
        return 1
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
