"""US1 integration: the loop against real services, and 20 consecutive turns.

Two tests live here, with different evidence value:

* ``test_a_real_question_is_answered_and_kept_in_context`` — marked ``live``.
  Real whisper on CT 912, the real Hermes API server on loopback, fake edge-tts.
  It asserts the transcript of ``utterance_speech.wav`` and that a follow-up
  question is answered in the same conversation (T018, acceptance scenario 4).
  It needs ``JG_HERMES_API_KEY`` in the environment and is skipped otherwise.
* ``test_twenty_consecutive_turns_complete`` — marked ``slow``, fake upstreams.
  Twenty hello → stt → llm → tts → stop loops on one session with no manual
  intervention and no error (SC-004). Run it with ``-m slow``.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from tools.fake_device import check_loop_sequence

AUDIO = Path(__file__).resolve().parents[1] / "fixtures" / "audio"

REAL_STT_URL = os.environ.get("JG_STT_URL", "http://172.16.90.12:8001")
REAL_HERMES_URL = os.environ.get("JG_HERMES_BASE_URL", "http://127.0.0.1:8642/v1")


def _real_env() -> dict[str, str]:
    api_key = os.environ.get("JG_HERMES_API_KEY") or os.environ.get("API_SERVER_KEY")
    if not api_key:
        pytest.skip("live turn needs JG_HERMES_API_KEY (or API_SERVER_KEY) in the environment")
    return {
        "JG_STT_URL": REAL_STT_URL,
        "JG_HERMES_BASE_URL": REAL_HERMES_URL,
        "JG_HERMES_API_KEY": api_key,
        "JG_HERMES_TIMEOUT_S": "60",
        "JG_STT_TIMEOUT_S": "30",
    }


@pytest.mark.live
async def test_a_real_question_is_answered_and_kept_in_context(harness_factory, caplog) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="jarvis_gateway.turn")
    harness = await harness_factory(**_real_env())
    device = await harness.connect()

    async def turn(wav: str) -> list[dict]:
        boundary = len(device.log.received_json)
        await device.listen("start")
        await device.stream_wav(AUDIO / wav)
        await device.listen("stop")
        stop = await device.wait_for_tts_stop(90)
        assert await device.wait_quiet(0.5)
        messages = device.log.received_json[boundary:]
        check_loop_sequence(
            messages,
            audio_frames=len(device.log.received_audio),
            session_id=stop["session_id"],
            quiet=True,
        )
        return messages

    def sentences(messages: list[dict]) -> str:
        return " ".join(
            m["text"]
            for m in messages
            if m.get("type") == "tts" and m.get("state") == "sentence_start"
        )

    try:
        # -- turn 1: a real spoken question, transcribed by the real whisper -----
        first = await turn("utterance_speech.wav")
        stt = next(m for m in first if m.get("type") == "stt")
        assert "weather" in stt["text"].casefold(), stt["text"]

        # -- turn 2: give the conversation something only it can know ------------
        await asyncio.sleep(0.5)  # T011: the session row is written when a turn ends
        await turn("remember_speech.wav")

        # -- turn 3: the follow-up is answered from the same conversation ---------
        await asyncio.sleep(0.5)
        third = await turn("followup_speech.wav")
        reply = sentences(third).casefold()
        assert "lantern" in reply, f"the follow-up lost context: {reply!r}"

        for record in caplog.records:
            if record.getMessage() == "turn":
                print(
                    "LIVE TURN",
                    f"session={record.session_id}",
                    f"timings={record.timings}",
                    f"deltas={record.deltas}",
                    f"frames={record.frames}",
                )
    finally:
        await device.close()


@pytest.mark.slow
async def test_twenty_consecutive_turns_complete(harness) -> None:
    harness.whisper.text = "what time is it"
    harness.hermes.tokens = ["It is nine fifteen.", " The evening is young."]
    device = await harness.connect()
    frames: list[int] = []
    try:
        for index in range(20):
            boundary = len(device.log.received_json)
            await device.listen("start")
            await device.stream_wav(AUDIO / "utterance_ok.wav")
            await device.listen("stop")
            stop = await device.wait_for_tts_stop(10)
            assert await device.wait_quiet(0.1), f"turn {index} kept talking after tts stop"

            turn = device.log.received_json[boundary:]
            check_loop_sequence(
                turn,
                audio_frames=len([m for m in device.log.received_audio]),
                session_id=stop["session_id"],
                quiet=True,
            )
            frames.append(len(turn))
            await asyncio.sleep(0.01)  # let the turn task observe IDLE before the next start

        assert len(frames) == 20
        assert len(harness.whisper.requests) == 20
        assert len(harness.hermes.requests) == 20
    finally:
        await device.close()
