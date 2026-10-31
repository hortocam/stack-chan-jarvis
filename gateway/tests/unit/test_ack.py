"""The cached acknowledgement phrase (P3.1 latency)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_gateway.ack import AckVoice
from jarvis_gateway.tts import TtsEngine
from tests.fakes.fake_edge_tts import FakeEdgeTTS


class _CountingTTS(FakeEdgeTTS):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.count = 0

    async def synthesize(self, text: str) -> bytes:
        self.count += 1
        return await super().synthesize(text)


async def test_the_phrase_is_synthesised_once_and_then_reused(tmp_path: Path) -> None:
    synthesizer = _CountingTTS()
    engine = TtsEngine(synthesizer)
    first = AckVoice("Right.", voice="en-GB-RyanNeural", cache_dir=tmp_path)

    frames = await first.frames(engine)
    assert frames and synthesizer.count == 1
    assert len(frames) == 18  # 1 s of 60 ms packets from the fake voice (tail padded)

    # A second process with the same cache does not call the voice at all.
    second = AckVoice("Right.", voice="en-GB-RyanNeural", cache_dir=tmp_path)
    assert await second.frames(engine) == frames
    assert synthesizer.count == 1


async def test_a_different_voice_does_not_replay_the_wrong_words(tmp_path: Path) -> None:
    synthesizer = _CountingTTS()
    engine = TtsEngine(synthesizer)
    await AckVoice("Right.", voice="en-GB-RyanNeural", cache_dir=tmp_path).frames(engine)

    other = AckVoice("Right.", voice="en-US-AriaNeural", cache_dir=tmp_path)
    await other.frames(engine)

    assert synthesizer.count == 2


async def test_a_corrupt_cache_is_re_synthesised(tmp_path: Path) -> None:
    """Negative control: a truncated file must not be replayed as audio."""

    synthesizer = _CountingTTS()
    engine = TtsEngine(synthesizer)
    voice = AckVoice("Right.", voice="en-GB-RyanNeural", cache_dir=tmp_path)
    await voice.frames(engine)
    cache = next(tmp_path.glob("ack-*.opus"))  # noqa: ASYNC240 - test-local file
    cache.write_bytes(b"\xff\xff\x00")

    fresh = AckVoice("Right.", voice="en-GB-RyanNeural", cache_dir=tmp_path)
    assert await fresh.frames(engine) is not None
    assert synthesizer.count == 2


async def test_a_failed_synthesis_disables_the_phrase_without_retrying(tmp_path: Path) -> None:
    synthesizer = _CountingTTS(fail_on={"Right."})
    engine = TtsEngine(synthesizer)
    voice = AckVoice("Right.", voice="en-GB-RyanNeural", cache_dir=tmp_path)

    assert await voice.frames(engine) is None
    assert await voice.frames(engine) is None
    assert synthesizer.count == 1
    assert voice.enabled is False


async def test_an_empty_phrase_is_never_spoken(tmp_path: Path) -> None:
    synthesizer = _CountingTTS()
    engine = TtsEngine(synthesizer)
    voice = AckVoice("   ", voice="en-GB-RyanNeural", cache_dir=tmp_path)

    assert await voice.frames(engine) is None
    assert synthesizer.count == 0


@pytest.mark.parametrize("text", ["", "Right."])
def test_enabled_matches_the_configured_phrase(tmp_path: Path, text: str) -> None:
    voice = AckVoice(text, voice="en-GB-RyanNeural", cache_dir=tmp_path)
    assert voice.enabled is bool(text)
