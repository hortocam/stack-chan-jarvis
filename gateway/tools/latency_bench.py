"""Live latency bench for the first-audible-word budget (P3.1, SC-001).

Runs the gateway in-process against the real whisper (CT 912), the real Hermes
API server on loopback and the real ``en-GB-RyanNeural`` voice, drives it with
the synthetic device from ``tools/fake_device.py``, and records, per turn, the
time from ``listen stop`` to the first binary audio frame (the first audible
word) and to the first frame of the answer. Stage timings from the gateway's own
turn record are captured alongside for cross-checking.

Nothing secret is printed: the API key stays in the environment. The script is
deliberately outside pytest's default run because it needs every live service;
run it with the gateway's venv:

    cd gateway
    set -a; . /etc/jarvis-gateway/env; set +a
    .venv/bin/python -m tools.latency_bench --turns 20 --out /tmp/p31.jsonl

To measure one change at a time, override the configuration in the environment:

    JG_ACK_TEXT=                 # acknowledgement off
    JG_FIRST_CHUNK_WORDS=0       # first-chunk cap off

``--stt-only`` measures whisper alone on the same utterance (item 3 of the
card) and prints its own percentile table.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import web

from jarvis_gateway.config import load_config
from jarvis_gateway.stt import SttClient
from jarvis_gateway.ws_app import create_app
from tools.fake_device import DeviceClient, read_wav_16k

__all__ = ["main"]

AUDIO = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "audio"
DEFAULT_WAV = AUDIO / "utterance_speech.wav"
SAMPLE_S = 0.005


@dataclass
class _TurnRecord:
    number: int
    first_audio_ms: float | None = None
    answer_audio_ms: float | None = None
    ack_played: bool = False
    sentences: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    deltas: dict[str, float] = field(default_factory=dict)
    frames: int = 0
    outcome: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn": self.number,
            "first_audio_ms": _round(self.first_audio_ms),
            "answer_audio_ms": _round(self.answer_audio_ms),
            "ack_played": self.ack_played,
            "sentences": len(self.sentences),
            "frames": self.frames,
            "outcome": self.outcome,
            **{f"gw_{key}": _round(value) for key, value in self.timings.items()},
            **{f"gw_delta_{key}": _round(value) for key, value in self.deltas.items()},
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


class _TurnLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() == "turn":
            self.records.append(record)


def _append_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.writelines(lines)


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values")
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _summary(name: str, values: list[float]) -> str:
    if not values:
        return f"{name:24} n=0"
    return (
        f"{name:24} n={len(values):2d}  p50={_percentile(values, 0.5):7.0f} ms"
        f"  p90={_percentile(values, 0.9):7.0f} ms"
        f"  min={min(values):6.0f}  max={max(values):6.0f}"
    )


async def _monitor(device: DeviceClient, stop: asyncio.Event) -> list[tuple[str, float, Any]]:
    """Sample the device log; timestamps every audio frame and JSON message."""

    loop = asyncio.get_running_loop()
    events: list[tuple[str, float, Any]] = []
    seen_json = len(device.log.received_json)
    seen_audio = len(device.log.received_audio)
    while not stop.is_set():
        now = loop.time()
        while seen_audio < len(device.log.received_audio):
            events.append(("audio", now, None))
            seen_audio += 1
        while seen_json < len(device.log.received_json):
            events.append(("json", now, device.log.received_json[seen_json]))
            seen_json += 1
        await asyncio.sleep(SAMPLE_S)
    return events


async def _one_turn(
    device: DeviceClient, wav: Path, number: int, ack_text: str, turn_log: _TurnLogHandler
) -> _TurnRecord:
    await device.listen("start")
    await device.stream_wav(wav)

    stop = asyncio.Event()
    monitor = asyncio.create_task(_monitor(device, stop))
    before = len(turn_log.records)
    sent_at = asyncio.get_running_loop().time()
    await device.listen("stop")

    await device.wait_for_tts_stop(180)
    await device.wait_quiet(0.3)
    stop.set()
    events = await monitor

    record = _TurnRecord(number=number)
    audio_times = [at for kind, at, _ in events if kind == "audio"]
    if audio_times:
        record.first_audio_ms = (audio_times[0] - sent_at) * 1000.0

    sentence_starts: list[tuple[float, str]] = []
    answer_llm_at: float | None = None
    for kind, at, payload in events:
        if kind != "json":
            continue
        if payload.get("type") == "tts" and payload.get("state") == "sentence_start":
            sentence_starts.append((at, payload.get("text", "")))
        elif payload.get("type") == "llm" and answer_llm_at is None:
            if ack_text and payload.get("text") == ack_text:
                record.ack_played = True
            else:
                answer_llm_at = at
    record.sentences = [text for _, text in sentence_starts]

    if answer_llm_at is not None:
        after_llm = [at for at in audio_times if at >= answer_llm_at]
        if after_llm:
            record.answer_audio_ms = (after_llm[0] - sent_at) * 1000.0
    if record.answer_audio_ms is None:
        record.answer_audio_ms = record.first_audio_ms

    if len(turn_log.records) > before:
        logged = turn_log.records[-1]
        record.timings = dict(getattr(logged, "timings", {}))
        record.deltas = dict(getattr(logged, "deltas", {}))
        record.frames = int(getattr(logged, "frames", 0))
        record.outcome = str(getattr(logged, "outcome", ""))
    return record


async def _run_turns(args: argparse.Namespace, config) -> int:
    handler = _TurnLogHandler()
    logger = logging.getLogger("jarvis_gateway.turn")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    app = create_app(config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    ws_url = f"ws://127.0.0.1:{port}{config.ws_path}"
    print(
        f"latency_bench: {args.turns} turns, voice={config.voice}, "
        f"ack={getattr(config, 'ack_text', '')!r} "
        f"delay={getattr(config, 'ack_delay_ms', 0)} ms, "
        f"first_chunk_words={getattr(config, 'first_chunk_words', 0)}",
        flush=True,
    )

    device = DeviceClient(
        ws_url,
        config.device_token,
        **({"device_id": args.device_id} if args.device_id else {}),
    )
    await device.connect()
    records: list[_TurnRecord] = []
    try:
        for number in range(1, args.turns + 1):
            record = await _one_turn(
                device, args.wav, number, getattr(config, "ack_text", ""), handler
            )
            records.append(record)
            print(f"  turn {number:2d}: {json.dumps(record.as_dict())}", flush=True)
            await asyncio.sleep(0.3)  # the session row is written when a turn ends
    finally:
        await device.close()
        await runner.cleanup()
        logger.removeHandler(handler)

    out = Path(args.out)
    await asyncio.to_thread(
        _append_lines, out, [json.dumps(record.as_dict()) + "\n" for record in records]
    )

    spoken = [r for r in records if r.outcome in {"spoken", "partial"}]
    first = [r.first_audio_ms for r in spoken if r.first_audio_ms is not None]
    answer = [r.answer_audio_ms for r in spoken if r.answer_audio_ms is not None]
    ttft = [r.timings["ttft_ms"] for r in spoken if "ttft_ms" in r.timings]
    stt = [r.timings["stt_ms"] for r in spoken if "stt_ms" in r.timings]

    print("\nlatency_bench summary", flush=True)
    print(_summary("first audible word", first), flush=True)
    print(_summary("answer first audio", answer), flush=True)
    print(_summary("whisper STT (gateway)", stt), flush=True)
    print(_summary("Hermes TTFT (gateway)", ttft), flush=True)
    print(f"acknowledgements spoken: {sum(r.ack_played for r in records)}/{len(records)}")
    failed = [r.outcome for r in records if r.outcome not in {"spoken", "partial"}]
    print(f"turns with no answer: {len(failed)} ({', '.join(failed) or 'none'})")
    return 0


async def _run_stt(args: argparse.Namespace, config) -> int:
    pcm, _ = read_wav_16k(args.wav)
    client = SttClient(config.stt_url, timeout_s=config.stt_timeout_s)
    samples: list[float] = []
    texts: list[str] = []
    try:
        for index in range(args.turns):
            started = time.monotonic()
            text = await client.transcribe(pcm)
            samples.append((time.monotonic() - started) * 1000.0)
            texts.append(text)
            print(f"  call {index + 1:2d}: {samples[-1]:7.0f} ms", flush=True)
    finally:
        await client.close()

    print("\nlatency_bench STT summary", flush=True)
    print(_summary("whisper STT", samples), flush=True)
    print(f"transcripts identical: {len(set(texts)) == 1}")
    if args.out:
        out = Path(args.out)
        await asyncio.to_thread(
            _append_lines,
            out,
            [
                json.dumps({"call": index, "stt_ms": _round(sample)}) + "\n"
                for index, sample in enumerate(samples, start=1)
            ],
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latency_bench",
        description="Measure first-audible-word latency against the live services (P3.1).",
    )
    parser.add_argument("--turns", type=int, default=20, help="turns (or STT calls) to measure")
    parser.add_argument(
        "--wav",
        type=Path,
        default=DEFAULT_WAV,
        help=f"utterance WAV (default: {DEFAULT_WAV.name})",
    )
    parser.add_argument("--out", default="/tmp/latency-bench.jsonl", help="JSON-lines output")
    parser.add_argument(
        "--device-id",
        default="",
        help="device id sent in the handshake (default: the synthetic device's); "
        "use a distinct value per configuration so each run starts a fresh brain scope",
    )
    parser.add_argument(
        "--stt-only",
        action="store_true",
        help="measure the whisper call alone; no gateway, no brain, no TTS",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.wav.is_file():
        print(f"latency_bench: no such WAV: {args.wav}", file=sys.stderr)
        return 2
    try:
        config = load_config(os.environ)
    except Exception as exc:  # ConfigError names the offending key
        print(f"latency_bench: configuration error: {exc}", file=sys.stderr)
        return 2
    try:
        if args.stt_only:
            return asyncio.run(_run_stt(args, config))
        return asyncio.run(_run_turns(args, config))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"latency_bench: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
