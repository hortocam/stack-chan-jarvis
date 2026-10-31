"""Regenerate every audio fixture (T003).

Run from ``gateway/`` with the project venv:

    .venv/bin/python tests/fixtures/make_audio.py

What it writes into ``tests/fixtures/audio/``:

- ``utterance_ok.wav``         1.5 s, 16 kHz mono PCM16, speech-like and clear of the VAD threshold
- ``silence_2s.wav``           2 s of digital silence
- ``noise_burst_100ms.wav``    2 s of silence with one 100 ms high-energy burst
- ``long_35s.wav``             35 s of alternating speech and pauses (git-ignored: 1.1 MB)
- ``opus_16k_60ms/NNN.bin``    ten real Opus packets, 16 kHz mono, 60 ms each

The WAVs are written with the standard-library ``wave`` module so the fixtures do not depend on an
encoder. The Opus packets come from libopus — the pinned codec from plan D2, which the gateway
decodes through PyAV in T009 — driven here by the host's ffmpeg binary so the fixture script needs
no extra dependency:

    ffmpeg -f s16le -ar 16000 -ac 1 -i source.raw \
           -c:a libopus -b:a 24k -frame_duration 60 -application voip -vbr off \
           -f ogg out.opus

Constant bitrate is deliberate: every packet is then exactly 180 bytes (24 kbit/s x 60 ms) and a
single SILK-WB frame, which is the shape the firmware's encoder produces for the uplink.

The script then splits the Ogg container into packets (the ``OpusHead``/``OpusTags`` headers are
dropped) and writes the first ten, one per file: protocol version 1 carries no per-packet header, so
one Opus packet *is* one WebSocket binary frame. Each packet spans exactly 60 ms (960 samples at
16 kHz). The Ogg pre-skip belongs to the container, not the packets: decoding these ten packets with
a zero pre-skip yields 2880 samples at Opus' native 48 kHz each, i.e. exactly 600 ms. T009 owns the
decoder and the sample-count accounting.

Every value is derived from a fixed seed, so re-running the script reproduces byte-identical files.
"""

from __future__ import annotations

import math
import random
import shutil
import subprocess
import tempfile
import wave
from array import array
from pathlib import Path

AUDIO_DIR = Path(__file__).resolve().parent / "audio"
OPUS_DIR = AUDIO_DIR / "opus_16k_60ms"

RATE = 16_000
SEED = 20_261_031
OPUS_FRAME_MS = 60
OPUS_BITRATE = "24k"


def write_wav(path: Path, samples: array) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(samples.tobytes())


def silence(seconds: float) -> array:
    return array("h", bytes(int(seconds * RATE) * 2))


def speech_like(seconds: float, amplitude: float = 9_000.0) -> array:
    """A voiced-sounding tone with a slow envelope: energy well above any sane VAD threshold."""
    count = int(seconds * RATE)
    out = array("h")
    for index in range(count):
        t = index / RATE
        envelope = 0.55 + 0.45 * math.sin(2 * math.pi * 3.0 * t)
        carrier = (
            math.sin(2 * math.pi * 180.0 * t)
            + 0.5 * math.sin(2 * math.pi * 360.0 * t)
            + 0.25 * math.sin(2 * math.pi * 540.0 * t)
        ) / 1.75
        out.append(int(max(-32_767.0, min(32_767.0, amplitude * envelope * carrier))))
    return out


def utterance_ok() -> array:
    return silence(0.1) + speech_like(1.3) + silence(0.1)


def noise_burst() -> array:
    rng = random.Random(SEED)
    burst = array("h", (rng.randint(-12_000, 12_000) for _ in range(int(0.1 * RATE))))
    return silence(0.95) + burst + silence(0.95)


def long_utterance() -> array:
    out = array("h")
    for _ in range(11):
        out += speech_like(2.4)
        out += silence(0.78)
    out += speech_like(0.42)
    return out


def _ogg_packets(data: bytes) -> list[bytes]:
    """Split an Ogg bitstream into its packets (segments joined, lacing 255 continues)."""
    packets: list[bytes] = []
    current = bytearray()
    pos = 0
    while pos < len(data):
        if data[pos : pos + 4] != b"OggS":
            raise ValueError(f"not an Ogg page at byte {pos}")
        page_segments = data[pos + 26]
        table = data[pos + 27 : pos + 27 + page_segments]
        body = pos + 27 + page_segments
        for segment in table:
            current += data[body : body + segment]
            body += segment
            if segment < 255:
                packets.append(bytes(current))
                current = bytearray()
        pos = body
    return packets


def opus_packets(count: int = 10) -> list[bytes]:
    """Encode ``count`` x 60 ms of 16 kHz audio and return one Opus packet per frame."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("make_audio.py needs the ffmpeg binary (libopus encoder) on PATH")

    source = speech_like(count * OPUS_FRAME_MS / 1000.0)
    with tempfile.TemporaryDirectory(prefix="jarvis-audio-") as tmp:
        raw = Path(tmp) / "source.raw"
        ogg = Path(tmp) / "packets.opus"
        raw.write_bytes(source.tobytes())
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", str(raw),
                "-c:a", "libopus", "-b:a", OPUS_BITRATE,
                "-frame_duration", str(OPUS_FRAME_MS),
                "-application", "voip", "-vbr", "off", "-f", "ogg", str(ogg),
            ],
            check=True,
        )  # fmt: skip
        packets = _ogg_packets(ogg.read_bytes())

    audio = [p for p in packets if not p.startswith((b"OpusHead", b"OpusTags"))]
    return audio[:count]


def speech_fixture(text: str, path: Path) -> None:
    """Render real English speech with ffmpeg's flite filter (live tests only).

    The other WAVs are synthetic tones: the VAD and the framing tests want a
    deterministic envelope, and whisper correctly transcribes a tone as nothing.
    A live transcription test needs actual words, so these two fixtures are
    rendered from flite — no vendor, no account, byte-stable for a fixed text.
    """

    if shutil.which("ffmpeg") is None:
        raise SystemExit("make_audio.py needs ffmpeg (with the flite filter) to render speech")
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"flite=text='{text}':voice=slt",
            "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(path),
        ],
        check=True,
    )  # fmt: skip


def main() -> int:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    OPUS_DIR.mkdir(parents=True, exist_ok=True)

    wavs = {
        "utterance_ok.wav": utterance_ok(),
        "silence_2s.wav": silence(2.0),
        "noise_burst_100ms.wav": noise_burst(),
        "long_35s.wav": long_utterance(),
    }
    for name, samples in wavs.items():
        write_wav(AUDIO_DIR / name, samples)
        print(f"{name}: {len(samples)} samples, {len(samples) / RATE:.3f} s")

    for name, text in (
        ("utterance_speech.wav", "Jarvis, what is the weather like today?"),
        ("remember_speech.wav", "Remember the word lantern. Reply with just the word stored."),
        ("followup_speech.wav", "What word did I just ask you to remember?"),
    ):
        speech_fixture(text, AUDIO_DIR / name)
        print(f"{name}: {text!r}")

    packets = opus_packets()
    if len(packets) != 10:
        raise SystemExit(f"expected 10 Opus packets, encoder produced {len(packets)}")
    for index, packet in enumerate(packets, start=1):
        path = OPUS_DIR / f"{index:03d}.bin"
        path.write_bytes(packet)
    sizes = [len(packet) for packet in packets]
    print(f"opus_16k_60ms: {len(packets)} packets, sizes {min(sizes)}..{max(sizes)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
