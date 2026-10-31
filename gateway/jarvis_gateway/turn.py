"""One question-to-answer cycle: utter → stt → brain → chunk → speak (T024).

The wire order the firmware expects (plan C2, acceptance scenario 1)::

    stt{text}
    llm{emotion, text}          # before the first audio frame (FR-009)
    tts{state: start}
    tts{state: sentence_start, text}   # once per spoken sentence
    <binary 24 kHz Opus frames, 60 ms each>
    tts{state: stop}            # exactly once, iff start was sent

The reply streams: sentences are chunked as deltas arrive and each one is
synthesised while the brain is still talking, so the first sentence plays long
before the last has been received (FR-008, acceptance scenario 3).

Failure policy (FR-018): if the user has heard nothing yet, say
``JG_APOLOGY_TEXT``; if speech already started, stop quietly. Either way the
session stays usable and the turn ends with exactly one ``tts stop`` if speech
began. Nothing here logs what was said unless ``JG_LOG_TRANSCRIPTS`` is on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .brain import BrainCancelled, BrainError
from .chunker import SentenceChunker
from .config import Config
from .emotion import choose_emotion
from .logging import TurnTimer
from .protocol import llm_message, stt_message, tts_sentence_start, tts_start, tts_stop
from .stt import SttError
from .tts import TtsError

__all__ = ["TurnResult", "TurnRunner"]

logger = logging.getLogger(__name__)

SendJson = Callable[[dict[str, Any]], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]


@dataclass(slots=True)
class _Speech:
    started: bool = False
    ack_only: bool = False
    answered: bool = False
    frames: int = 0
    sentences: int = 0
    failures: int = 0
    emotion: str = "neutral"


@dataclass(slots=True)
class TurnResult:
    transcript: str
    reply: str
    emotion: str
    sentences: int
    frames: int
    outcome: str
    timings: dict[str, float] = field(default_factory=dict)
    deltas: dict[str, float] = field(default_factory=dict)


class TurnRunner:
    """Runs one turn. One instance per turn; the session owns the lifecycle."""

    def __init__(
        self,
        *,
        config: Config,
        session_id: str,
        device_id: str,
        stt,
        brain,
        tts,
        send_json: SendJson,
        send_bytes: SendBytes,
        clock: Callable[[], float] | None = None,
        cancel: asyncio.Event | None = None,
        ack=None,
    ) -> None:
        self._config = config
        self._session_id = session_id
        self._device_id = device_id
        self._stt = stt
        self._brain = brain
        self._tts = tts
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._cancel = cancel
        self._clock = clock
        self._ack = ack
        self._chunker = SentenceChunker(first_chunk_words=config.first_chunk_words)

    # -- public ----------------------------------------------------------

    async def run(self, utterance, *, vad_end_ms: float = 0.0) -> TurnResult:
        timer = TurnTimer(self._clock, offset_ms=vad_end_ms) if self._clock else TurnTimer(
            offset_ms=vad_end_ms
        )
        timer.mark("vad_end_ms")

        try:
            transcript = await self._stt.transcribe(utterance.pcm)
        except SttError as exc:
            logger.warning("stt failed", extra={"kind": exc.kind, "session_id": self._session_id})
            return await self._fail(timer, "stt_failed", reply="")

        if not transcript:
            logger.info("turn had nothing to transcribe", extra={"session_id": self._session_id})
            return TurnResult(
                transcript="",
                reply="",
                emotion="neutral",
                sentences=0,
                frames=0,
                outcome="empty",
            )

        timer.mark("stt_ms")
        await self._send_json(stt_message(self._session_id, transcript))

        speech = _Speech()
        reply, brain_error, cancelled = await self._stream_and_speak(
            transcript, speech, timer
        )

        if cancelled:
            # T037 owns the abort path; the turn still closes its speech cleanly.
            if speech.started:
                await self._close_speech(speech, timer)
            return self._result(transcript, reply, speech, timer, "cancelled")

        if not speech.answered:
            # Nothing was heard that answered the question: apologise (FR-018),
            # whatever the cause. An acknowledgement alone is not an answer.
            reason = brain_error.kind if brain_error is not None else "no_speech"
            if speech.failures and brain_error is None:
                reason = "tts_failed"
            spoke = await self._speak_apology(speech, timer)
            await self._close_speech(speech, timer)
            result = self._result(
                transcript, reply, speech, timer, "apology" if spoke else "silent_failure"
            )
            self._log_turn(result, transcript, reply, reason)
            return result

        await self._close_speech(speech, timer)
        outcome = "spoken"
        if brain_error is not None:
            outcome = "partial"
            logger.warning(
                "brain failed after speech started",
                extra={"kind": brain_error.kind, "session_id": self._session_id},
            )
        result = self._result(transcript, reply, speech, timer, outcome)
        self._log_turn(result, transcript, reply, outcome)
        return result

    # -- streaming -------------------------------------------------------

    async def _stream_and_speak(
        self, transcript: str, speech: _Speech, timer: TurnTimer
    ) -> tuple[str, BrainError | None, bool]:
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        first_token = False

        def on_delta(text: str) -> None:
            nonlocal first_token
            if not first_token:
                first_token = True
                timer.mark("ttft_ms")
            for sentence in self._chunker.feed(text):
                sentences.put_nowait(sentence)

        async def produce() -> str:
            try:
                reply = await self._brain.stream(
                    device_id=self._device_id,
                    text=transcript,
                    on_delta=on_delta,
                    cancel=self._cancel,
                )
            except (BrainError, BrainCancelled):
                # A cut stream's tail is not a sentence: the pending buffer is
                # dropped, and the apology (or silence) speaks for the turn.
                raise
            else:
                for sentence in self._chunker.flush():
                    sentences.put_nowait(sentence)
                return reply.text
            finally:
                sentences.put_nowait(None)

        producer = asyncio.create_task(produce())
        reply = ""
        error: BrainError | None = None
        cancelled = False
        ack_played = False
        try:
            while True:
                if not ack_played and self._ack is not None and not speech.started:
                    try:
                        sentence = await asyncio.wait_for(
                            sentences.get(), timeout=self._config.ack_delay_ms / 1000.0
                        )
                    except TimeoutError:
                        # The brain is still thinking: say something short now.
                        ack_played = True
                        await self._speak_ack(speech, timer)
                        continue
                else:
                    sentence = await sentences.get()
                if sentence is None:
                    break
                await self._speak_sentence(sentence, speech, timer)
            try:
                reply = await producer
            except BrainCancelled:
                cancelled = True
            except BrainError as exc:
                error = exc
                reply = exc.partial
        finally:
            if not producer.done():
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
        return reply, error, cancelled

    async def _speak_ack(self, speech: _Speech, timer: TurnTimer) -> bool:
        """Speak the cached acknowledgement as this turn's first sentence."""

        if self._ack is None:
            return False
        frames = await self._ack.frames(self._tts)
        if not frames:
            return False
        text = self._ack.text
        speech.emotion = choose_emotion(text)
        await self._send_json(llm_message(self._session_id, speech.emotion, text))
        await self._send_json(tts_start(self._session_id))
        speech.started = True
        speech.ack_only = True
        await self._send_json(tts_sentence_start(self._session_id, text))
        speech.sentences += 1
        for frame in frames:
            if self._cancel is not None and self._cancel.is_set():
                break
            if speech.frames == 0:
                timer.mark("first_audio_ms")
            await self._send_bytes(frame)
            speech.frames += 1
        return True

    async def _speak_sentence(self, sentence: str, speech: _Speech, timer: TurnTimer) -> bool:
        """Synthesize first, announce only if it worked, then stream the frames."""

        stream = self._tts.frames(sentence, cancel=self._cancel)
        try:
            first = await anext(stream)
        except StopAsyncIteration:  # cancelled before the first frame
            return False
        except TtsError as exc:
            speech.failures += 1
            logger.warning(
                "tts failed for one sentence",
                extra={"kind": exc.kind, "session_id": self._session_id},
            )
            return False

        if not speech.started:
            speech.emotion = choose_emotion(sentence)
            await self._send_json(llm_message(self._session_id, speech.emotion, sentence))
            await self._send_json(tts_start(self._session_id))
            speech.started = True
        elif speech.ack_only:
            # The acknowledgement already reached the device; the answer's own
            # text and emotion must follow it, before the first answer frame.
            speech.emotion = choose_emotion(sentence)
            await self._send_json(llm_message(self._session_id, speech.emotion, sentence))
            speech.ack_only = False
        await self._send_json(tts_sentence_start(self._session_id, sentence))

        try:
            if speech.frames == 0:
                timer.mark("first_audio_ms")
            await self._send_bytes(first)
            speech.frames += 1
            async for frame in stream:
                await self._send_bytes(frame)
                speech.frames += 1
        finally:
            await stream.aclose()
        speech.sentences += 1
        speech.answered = True
        return True

    async def _speak_apology(self, speech: _Speech, timer: TurnTimer) -> bool:
        logger.warning("speaking the apology", extra={"session_id": self._session_id})
        return await self._speak_sentence(self._config.apology_text, speech, timer)

    async def _close_speech(self, speech: _Speech, timer: TurnTimer) -> None:
        if speech.started:
            await self._send_json(tts_stop(self._session_id))
            timer.mark("tts_end_ms")

    async def _fail(self, timer: TurnTimer, reason: str, *, reply: str) -> TurnResult:
        speech = _Speech()
        spoke = await self._speak_apology(speech, timer)
        await self._close_speech(speech, timer)
        result = self._result("", reply, speech, timer, "apology" if spoke else "silent_failure")
        self._log_turn(result, "", reply, reason)
        return result

    # -- reporting -------------------------------------------------------

    def _result(
        self,
        transcript: str,
        reply: str,
        speech: _Speech,
        timer: TurnTimer,
        outcome: str,
    ) -> TurnResult:
        spoken = speech.frames > 0
        return TurnResult(
            transcript=transcript,
            reply=reply,
            emotion=speech.emotion,
            sentences=speech.sentences,
            frames=speech.frames,
            outcome=outcome,
            timings=timer.stages if spoken else {},
            deltas=timer.deltas if spoken else {},
        )

    def _log_turn(self, result: TurnResult, transcript: str, reply: str, reason: str) -> None:
        if result.frames == 0:
            logger.info(
                "turn finished without speech",
                extra={
                    "session_id": self._session_id,
                    "outcome": result.outcome,
                    "reason": reason,
                    "frames": 0,
                    "sentences": result.sentences,
                },
            )
            return
        logger.info(
            "turn",
            extra={
                "session_id": self._session_id,
                "outcome": result.outcome,
                "reason": reason,
                "timings": result.timings,
                "deltas": result.deltas,
                "frames": result.frames,
                "sentences": result.sentences,
                "emotion": result.emotion,
                "transcript": transcript,
                "reply": reply,
            },
        )
