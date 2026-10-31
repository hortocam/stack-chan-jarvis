"""US1 failure paths (T025, FR-018).

Every failure after the handshake ends in a defined state: a spoken one-line
apology when the user is waiting, silence when speech already started, exactly
one ``tts stop`` if speech began, no unhandled task exception, and a session that
is immediately usable for the next question.
"""

from __future__ import annotations

import logging
from pathlib import Path

AUDIO = Path(__file__).resolve().parents[1] / "fixtures" / "audio"
APOLOGY = "Sorry, I didn't catch that."


def _starts(device) -> list[str]:
    return [
        m["text"]
        for m in device.log.after_hello()
        if m.get("type") == "tts" and m.get("state") == "sentence_start"
    ]


def _stops(device) -> int:
    return len(
        [m for m in device.log.after_hello() if m.get("type") == "tts" and m.get("state") == "stop"]
    )


def _no_task_exception(caplog) -> None:
    for record in caplog.records:
        assert "Task exception was never retrieved" not in record.getMessage()
        assert record.exc_info is None or record.levelno < logging.ERROR


async def _turn(device) -> None:
    await device.listen("start")
    await device.stream_wav(AUDIO / "utterance_ok.wav")
    await device.listen("stop")
    await device.wait_for_tts_stop(15)


async def test_an_stt_timeout_speaks_the_apology(harness_factory, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    harness = await harness_factory(JG_STT_TIMEOUT_S="0.15")
    harness.whisper.delay_s = 1.0
    device = await harness.connect()
    try:
        await _turn(device)
        assert await device.wait_quiet(0.2) is True
        assert _starts(device) == [APOLOGY]
        assert len(device.log.received_audio) > 0
        assert _stops(device) == 1
    finally:
        await device.close()
    _no_task_exception(caplog)


async def test_a_brain_error_before_the_first_token_speaks_the_apology(harness, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    harness.hermes.error_before_first = True
    device = await harness.connect()
    try:
        await _turn(device)
        assert _starts(device) == [APOLOGY]
        assert len(device.log.received_audio) > 0
        assert _stops(device) == 1
    finally:
        await device.close()
    _no_task_exception(caplog)


async def test_a_brain_stall_speaks_the_apology(harness_factory, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    harness = await harness_factory(JG_HERMES_TIMEOUT_S="0.3")
    harness.hermes.tokens = ["I was just about to say someth"]  # no complete sentence
    harness.hermes.stall_after_last_s = 1.0
    device = await harness.connect()
    try:
        await _turn(device)
        assert APOLOGY in _starts(device)
        assert _stops(device) == 1
    finally:
        await device.close()
    _no_task_exception(caplog)


async def test_a_brain_cut_before_any_sentence_speaks_the_apology(harness, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    harness.hermes.error_after_first = True
    harness.hermes.tokens = ["The answer began but never", " finished"]
    device = await harness.connect()
    try:
        await _turn(device)
        assert _starts(device) == [APOLOGY]
        assert _stops(device) == 1
    finally:
        await device.close()
    _no_task_exception(caplog)


async def test_a_brain_cut_after_speech_started_stops_without_an_apology(harness, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    harness.hermes.error_after_first = True
    harness.hermes.tokens = ["The first sentence is right here.", " And then it dies"]
    device = await harness.connect()
    try:
        await _turn(device)
        assert _starts(device) == ["The first sentence is right here."]
        assert APOLOGY not in _starts(device)
        assert len(device.log.received_audio) > 0
        assert _stops(device) == 1
    finally:
        await device.close()
    _no_task_exception(caplog)


async def test_one_failed_sentence_does_not_stop_the_rest(harness, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    harness.hermes.tokens = [
        "The first sentence is right here.",
        " And this is the second sentence, spoken after it.",
    ]
    harness.tts.fail_on = {"The first sentence is right here."}
    device = await harness.connect()
    try:
        await _turn(device)
        assert _starts(device) == ["And this is the second sentence, spoken after it."]
        assert len(device.log.received_audio) > 0
        assert _stops(device) == 1
    finally:
        await device.close()
    _no_task_exception(caplog)


async def test_all_sentences_failing_still_says_the_apology(harness, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    first, second = "The first sentence is right here.", "And this is the second one."
    harness.hermes.tokens = [first, " " + second]
    harness.tts.fail_on = {first, second}
    device = await harness.connect()
    try:
        await _turn(device)
        assert _starts(device) == [APOLOGY]
        assert len(device.log.received_audio) > 0
        assert _stops(device) == 1
    finally:
        await device.close()
    _no_task_exception(caplog)


async def test_every_failure_leaves_the_session_usable(harness, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    harness.hermes.error_before_first = True
    device = await harness.connect()
    try:
        await _turn(device)
        harness.hermes.error_before_first = False
        harness.hermes.tokens = ["The second turn works fine."]
        harness.whisper.text = "are you there"

        await _turn(device)
        assert _starts(device)[-1] == "The second turn works fine."
        assert [m["type"] for m in device.log.after_hello()].count("stt") == 2
    finally:
        await device.close()
    _no_task_exception(caplog)
