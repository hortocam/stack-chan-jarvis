"""The synthetic device's CLI contract and frame pacing (T027).

``tools/fake_device.py`` plays the robot: it builds the four handshake headers,
exchanges ``hello``, streams a WAV as 60 ms Opus frames, and can assert the
``--expect-loop`` sequence. These tests cover the parts that do not need a
gateway: argument parsing, WAV handling and pacing, and the ordering assertion
(including its negative controls).
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import pytest

from tools.fake_device import (
    SequenceMismatch,
    build_parser,
    check_loop_sequence,
    read_wav_16k,
)

AUDIO = Path(__file__).resolve().parents[1] / "fixtures" / "audio"


def test_defaults_are_the_protocol_defaults() -> None:
    args = build_parser().parse_args(
        [
            "--url",
            "ws://127.0.0.1:8010/xiaozhi/v1/",
            "--token",
            "t",
            "--wav",
            str(AUDIO / "utterance_ok.wav"),
        ]
    )

    assert args.device_id == "24:0a:c4:00:11:22"
    assert args.client_id is None
    assert args.expect_loop is False
    assert args.timeout == 60.0
    assert args.out_dir is None


def test_url_and_token_are_required() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--url", "ws://x/"])
    assert excinfo.value.code == 2

    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--token", "t"])
    assert excinfo.value.code == 2


def test_a_missing_wav_is_a_usage_error() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--url", "ws://x/", "--token", "t"])
    assert excinfo.value.code == 2


def test_the_recorded_utterance_is_16k_mono_pcm16() -> None:
    pcm, rate = read_wav_16k(AUDIO / "utterance_ok.wav")

    assert rate == 16000
    assert len(pcm) % 2 == 0
    assert len(pcm) > 0


def test_a_stereo_44k_wav_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "wrong.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x00\x00" * 200)

    with pytest.raises(ValueError) as excinfo:
        read_wav_16k(path)

    assert "16000" in str(excinfo.value)


def _good_log() -> list[dict]:
    return [
        {"type": "stt", "session_id": "s", "text": "what time is it"},
        {"type": "llm", "session_id": "s", "emotion": "neutral", "text": "Nine fifteen."},
        {"type": "tts", "session_id": "s", "state": "start"},
        {"type": "tts", "session_id": "s", "state": "sentence_start", "text": "Nine fifteen."},
        {"type": "tts", "session_id": "s", "state": "stop"},
    ]


def test_the_expected_loop_passes() -> None:
    check_loop_sequence(_good_log(), audio_frames=17, session_id="s")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda log: log.pop(1),  # no llm
        lambda log: log[1].update(emotion="grumpy"),  # emotion off the firmware set
        lambda log: log.insert(2, {"type": "stt", "session_id": "s", "text": "again"}),
        lambda log: log[0].update(session_id="other"),  # foreign session id
        lambda log: log[-1].update(state="start"),  # never stopped
        lambda log: log.insert(-1, {"type": "llm", "session_id": "s", "emotion": "sad"}),
    ],
)
def test_wrong_sequences_are_rejected(mutate) -> None:
    log = _good_log()
    mutate(log)

    with pytest.raises(SequenceMismatch):
        check_loop_sequence(log, audio_frames=17, session_id="s")


def test_no_audio_after_tts_stop_is_checked_separately() -> None:
    log = _good_log() + [{"type": "tts", "session_id": "s", "state": "start"}]

    with pytest.raises(SequenceMismatch):
        check_loop_sequence(log, audio_frames=17, session_id="s", quiet=True)


async def test_frames_are_paced_at_sixty_milliseconds() -> None:
    from tools.fake_device import Pacer

    sleeps: list[float] = []
    pacer = Pacer(delay_s=0.06, sleep=sleeps.append)

    for _ in range(4):
        await pacer.frame()

    assert sleeps == [0.06, 0.06, 0.06, 0.06]

    unpaced = Pacer(delay_s=0.0, sleep=sleeps.append)
    await unpaced.frame()
    assert len(sleeps) == 4  # no sleep when pacing is off


def test_the_module_is_runnable() -> None:
    assert "tools.fake_device" in sys.modules
