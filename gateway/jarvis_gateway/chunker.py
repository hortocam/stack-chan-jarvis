"""Sentence segmentation for streaming TTS (T014, plan D7).

The brain streams deltas; the voice speaks one sentence at a time. This module
turns the delta stream into sentences with three rules:

* a sentence ends at ``. ! ? …`` or a newline, absorbing a closing quote or
  bracket that belongs to it;
* a boundary only counts once the text before it is at least ``min_chars``
  long, so "Yes. No." becomes one utterance instead of two nervous fragments —
  the first sentence is what determines time-to-first-audio;
* if no boundary arrives, the buffer is cut at ``max_chars`` (at a word
  boundary when there is one) so one synthesis request cannot grow without
  bound, and the final tail is flushed at end of stream.

Two false boundaries are suppressed: a decimal point (``3.5``) and a full stop
after a common abbreviation (``Dr.``, ``Mr.``, …). No other English-specific
cleverness is attempted: this is a voice loop, not a parser.
"""

from __future__ import annotations

import re

__all__ = ["SentenceChunker", "split_sentences"]

# Words whose trailing full stop does not end a sentence.
ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "fig",
        "mt",
    }
)

TERMINATORS = ".!?…"
#: Punctuation that can end the first chunk early, before a full sentence.
CLAUSE_MARKS = ",;:—"
#: A clause boundary must have at least this many words before it.
FIRST_CHUNK_MIN_WORDS = 4
CLOSERS = '"”’\')]'


class SentenceChunker:
    """Accumulate deltas and yield speech-sized sentences."""

    def __init__(
        self,
        *,
        min_chars: int = 20,
        max_chars: int = 220,
        first_chunk_words: int = 0,
    ) -> None:
        if min_chars < 1:
            raise ValueError("min_chars must be at least one")
        if max_chars <= min_chars:
            raise ValueError("max_chars must be greater than min_chars")
        if first_chunk_words < 0:
            raise ValueError("first_chunk_words must not be negative")
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.first_chunk_words = first_chunk_words
        self._buffer = ""
        self._emitted = 0

    def feed(self, text: str) -> list[str]:
        """Add a delta and return every sentence that is now complete."""

        if text:
            self._buffer += text
        return self._drain(final=False)

    def flush(self) -> list[str]:
        """End of stream: emit the remaining text, hard-split if oversized."""

        return self._drain(final=True)

    @property
    def pending(self) -> str:
        return self._buffer

    # -- internals ---------------------------------------------------------

    def _drain(self, *, final: bool) -> list[str]:
        out: list[str] = []
        while True:
            text = self._buffer
            stripped = text.strip()
            if not stripped:
                self._buffer = ""
                break

            boundary = self._find_emit_boundary(text)
            if self._emitted == 0:
                first = self._first_chunk_boundary(text)
                if first is not None and (boundary is None or first < boundary):
                    boundary = first
            if boundary is not None:
                out.append(text[:boundary].strip())
                self._buffer = text[boundary:].lstrip()
                self._emitted += 1
                continue

            if len(stripped) > self.max_chars:
                chunk, rest = self._hard_split(text)
                out.append(chunk)
                self._buffer = rest
                continue
            break

        if final:
            tail = self._buffer.strip()
            self._buffer = ""
            while tail:
                chunk, tail = self._hard_split(tail)
                out.append(chunk)
        return [chunk for chunk in out if chunk]

    def _find_emit_boundary(self, text: str) -> int | None:
        """First boundary whose text is long enough to speak; later ones absorb short heads."""

        position = 0
        while True:
            boundary = self._next_boundary(text, start=position)
            if boundary is None:
                return None
            if len(text[:boundary].strip()) >= self.min_chars:
                return boundary
            position = boundary

    def _first_chunk_boundary(self, text: str) -> int | None:
        """Earliest of a clause boundary and the first-chunk word cap.

        Only the first chunk of a turn uses this: it is the chunk that decides
        when the robot starts speaking, so it may end mid-sentence.
        """

        if self.first_chunk_words <= 0:
            return None
        candidates = [boundary for boundary in (
            self._clause_boundary(text),
            self._word_cap_boundary(text),
        ) if boundary is not None]
        return min(candidates) if candidates else None

    def _clause_boundary(self, text: str) -> int | None:
        for index, char in enumerate(text):
            if char in CLAUSE_MARKS:
                if self._inside_number(text, index):
                    continue
                if self._word_count(text[:index]) >= FIRST_CHUNK_MIN_WORDS:
                    return index + 1
            elif char == " " and text.startswith(" - ", index):
                if self._word_count(text[:index]) >= FIRST_CHUNK_MIN_WORDS:
                    return index + 3
        return None

    def _word_cap_boundary(self, text: str) -> int | None:
        if self.first_chunk_words <= 0:
            return None
        matches = list(re.finditer(r"\S+", text))
        if len(matches) <= self.first_chunk_words:
            return None
        return matches[self.first_chunk_words].start()

    @staticmethod
    def _word_count(text: str) -> int:
        return len(text.split())

    @staticmethod
    def _inside_number(text: str, index: int) -> bool:
        """A comma or colon between digits is a number, not a clause mark."""

        if text[index] not in ",:":
            return False
        before = text[index - 1 : index]
        after = text[index + 1 : index + 2]
        return before.isdigit() and after.isdigit()

    def _next_boundary(self, text: str, *, start: int = 0) -> int | None:
        for index in range(start, len(text)):
            char = text[index]
            if char == "\n":
                return index + 1
            if char not in TERMINATORS:
                continue
            if char == "." and self._period_is_protected(text, index):
                continue
            end = index + 1
            while end < len(text) and text[end] in CLOSERS:
                end += 1
            return end
        return None

    @staticmethod
    def _period_is_protected(text: str, index: int) -> bool:
        following = text[index + 1 : index + 2]
        if following.isdigit():
            return True  # 3.5 seconds
        if following == ".":
            return True  # the first two dots of an ASCII ellipsis
        start = index
        while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "'’"):
            start -= 1
        word = text[start:index].lower().strip("'’")
        return word in ABBREVIATIONS

    def _hard_split(self, text: str) -> tuple[str, str]:
        """Cut at most ``max_chars`` off the front, preferring a word boundary."""

        working = text
        if len(working.strip()) <= self.max_chars:
            return working.strip(), ""

        cut = min(self.max_chars, len(working))
        space = working.rfind(" ", 0, cut + 1)
        if space > 0:
            cut = space
        chunk = working[:cut].strip()
        rest = working[cut:].lstrip()
        if not chunk:  # a single token with no space at all
            chunk = working[: self.max_chars]
            rest = working[self.max_chars :]
        return chunk, rest


def split_sentences(text: str, *, min_chars: int = 20, max_chars: int = 220) -> list[str]:
    """Segment a complete text (tests, offline tooling)."""

    chunker = SentenceChunker(min_chars=min_chars, max_chars=max_chars)
    return chunker.feed(text) + chunker.flush()
