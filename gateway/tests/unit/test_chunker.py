"""Sentence chunker for streaming TTS (T014).

The chunker trades latency for prosody: a short first sentence is spoken
sooner, but sentences below ``min_chars`` are merged so the voice does not
stammer one-word bursts. A hard cap keeps a single synthesis request bounded.
"""

from __future__ import annotations

import pytest

from jarvis_gateway.chunker import SentenceChunker, split_sentences


def test_single_long_sentence_is_emitted_at_the_boundary() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("It is half past nine.") == ["It is half past nine."]
    assert chunker.flush() == []


def test_a_sentence_arrives_across_deltas() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("It is half ") == []
    assert chunker.feed("past nine.") == ["It is half past nine."]


def test_two_long_sentences_split() -> None:
    chunker = SentenceChunker()

    sentences = chunker.feed("The weather is lovely today. Shall we go outside?")

    assert sentences == ["The weather is lovely today.", "Shall we go outside?"]


def test_exclamation_and_question_marks_are_boundaries() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("That is absolutely wonderful!") == ["That is absolutely wonderful!"]
    assert chunker.feed("Would you like another cup of tea?") == [
        "Would you like another cup of tea?"
    ]


def test_ellipsis_is_a_boundary() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("I was thinking about the garden… maybe we can go later.") == [
        "I was thinking about the garden…",
        "maybe we can go later.",
    ]


def test_newline_flushes_the_line() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("First line of the answer\nSecond line of the answer") == [
        "First line of the answer"
    ]
    assert chunker.flush() == ["Second line of the answer"]


def test_short_sentences_merge_until_the_threshold() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("Yes. No. Maybe.") == []
    assert chunker.flush() == ["Yes. No. Maybe."]


def test_short_sentence_merges_with_the_next_long_one() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("Yes. The weather is lovely today.") == [
        "Yes. The weather is lovely today."
    ]


def test_decimal_numbers_are_not_split() -> None:
    chunker = SentenceChunker()

    text = "The whole routine takes about 3.5 seconds to warm up."
    assert chunker.feed(text) == [text]


@pytest.mark.parametrize(
    "text",
    [
        "The patient is waiting for Dr. Smith in the corridor.",
        "She said Mr. Jones arrived at the office on time.",
        "We spoke to Mrs. Patel in the hall earlier today.",
        "The lecture by Prof. Green was long but useful.",
    ],
)
def test_common_abbreviations_are_not_split(text: str) -> None:
    chunker = SentenceChunker()

    assert chunker.feed(text) == [text]


def test_closing_quote_stays_with_its_sentence() -> None:
    chunker = SentenceChunker()

    assert chunker.feed('She asked, "Is it ready?" He nodded slowly and left.') == [
        'She asked, "Is it ready?"',
        "He nodded slowly and left.",
    ]


def test_long_text_without_punctuation_is_hard_split() -> None:
    chunker = SentenceChunker(max_chars=220)
    text = "word " * 60

    chunks = chunker.feed(text)

    assert chunks == [] or all(len(chunk) <= 220 for chunk in chunks)
    chunks += chunker.flush()
    assert sum(len(chunk) for chunk in chunks) >= 200
    assert all(len(chunk) <= 220 for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_hard_split_prefers_a_word_boundary() -> None:
    chunker = SentenceChunker(max_chars=60)
    text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima"

    chunks = chunker.feed(text) + chunker.flush()

    assert all(len(chunk) <= 60 for chunk in chunks)
    for chunk in chunks[:-1]:
        assert not chunk.endswith(" ")
        assert chunk.split()[-1] in text.split()  # no half words


def test_a_single_long_word_is_split_at_the_cap() -> None:
    chunker = SentenceChunker(min_chars=5, max_chars=20)

    chunks = chunker.feed("x" * 45) + chunker.flush()

    assert chunks[0] == "x" * 20
    assert all(len(chunk) <= 20 for chunk in chunks)
    assert "".join(chunks) == "x" * 45


def test_flush_emits_the_tail_once() -> None:
    chunker = SentenceChunker()
    chunker.feed("The answer is almost ready")

    assert chunker.flush() == ["The answer is almost ready"]
    assert chunker.flush() == []


def test_empty_and_whitespace_input_is_ignored() -> None:
    chunker = SentenceChunker()

    assert chunker.feed("") == []
    assert chunker.feed("   \n\t ") == []
    assert chunker.flush() == []


def test_a_sentence_exactly_at_the_boundary_is_not_kept() -> None:
    chunker = SentenceChunker()
    text = "This sentence has twenty"

    assert chunker.feed(text + ".") == [text + "."]
    assert chunker.flush() == []


def test_split_sentences_helper_matches_the_streaming_chunker() -> None:
    text = "The weather is lovely today. Shall we go outside?"

    assert split_sentences(text) == ["The weather is lovely today.", "Shall we go outside?"]


def test_min_chars_is_configurable() -> None:
    chunker = SentenceChunker(min_chars=3)

    assert chunker.feed("Yes. No. Maybe.") == ["Yes.", "No.", "Maybe."]
