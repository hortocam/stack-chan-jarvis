"""First-chunk cap for streaming TTS (P3.1 latency).

The brain's opening sentence can be long; waiting for its full stop costs
seconds before the first audio frame. The first chunk of a turn is therefore
emitted at the earlier of a clause boundary (~4 words in) and a hard word cap
(default 12), while every later chunk keeps the sentence rules. ``first_chunk_words=0``
restores the old behaviour exactly.
"""

from __future__ import annotations

from jarvis_gateway.chunker import SentenceChunker


def test_a_long_first_sentence_is_cut_at_the_word_cap() -> None:
    chunker = SentenceChunker(first_chunk_words=12)

    first = chunker.feed(
        "Clear and dry over Winchester Massachusetts fifty two degrees with a north wind"
    )

    assert first == ["Clear and dry over Winchester Massachusetts fifty two degrees with a north"]


def test_the_rest_of_the_sentence_is_not_lost() -> None:
    chunker = SentenceChunker(first_chunk_words=12)

    first = chunker.feed(
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    )
    rest = chunker.flush()

    assert first == ["one two three four five six seven eight nine ten eleven twelve"]
    assert rest == ["thirteen fourteen"]


def test_the_first_chunk_splits_at_a_clause_boundary() -> None:
    chunker = SentenceChunker(first_chunk_words=12)

    first = chunker.feed(
        "Clear and dry over Winchester, Massachusetts, though the wind makes it feel colder."
    )

    assert first[0] == "Clear and dry over Winchester,"
    assert first == [
        "Clear and dry over Winchester,",
        "Massachusetts, though the wind makes it feel colder.",
    ]


def test_a_very_short_clause_does_not_make_a_stammering_chunk() -> None:
    """'Well,' alone is not worth a synthesis request; wait for more words."""

    chunker = SentenceChunker(first_chunk_words=12)

    assert chunker.feed("Well, I was going to say something useful about the weather") == []

    after_cap = chunker.feed(" today and tomorrow")
    assert after_cap == ["Well, I was going to say something useful about the weather today"]


def test_a_sentence_end_still_wins_when_it_comes_first() -> None:
    chunker = SentenceChunker(first_chunk_words=12)

    first = chunker.feed("It is nine fifteen in the evening. And the evening is young.")

    assert first[0] == "It is nine fifteen in the evening."
    assert first == ["It is nine fifteen in the evening.", "And the evening is young."]


def test_later_chunks_keep_the_sentence_rules() -> None:
    chunker = SentenceChunker(first_chunk_words=12)

    assert chunker.feed("one two three four five six seven eight nine ten eleven twelve and") == [
        "one two three four five six seven eight nine ten eleven twelve"
    ]
    assert (
        chunker.feed(" thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty")
        == []
    )

    assert chunker.flush() == [
        "and thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
    ]


def test_zero_disables_the_first_chunk_cap() -> None:
    """Negative control: without the cap the same text waits for the full stop."""

    chunker = SentenceChunker(first_chunk_words=0)
    text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"

    assert chunker.feed(text) == []
    assert chunker.flush() == [text]


def test_a_numeric_comma_does_not_look_like_a_clause_boundary() -> None:
    chunker = SentenceChunker(first_chunk_words=12)

    assert chunker.feed(
        "The temperature is 1,000 degrees and rising quickly outside right now today again"
    ) == ["The temperature is 1,000 degrees and rising quickly outside right now today"]


def test_the_cap_can_be_raised() -> None:
    chunker = SentenceChunker(first_chunk_words=20)

    assert chunker.feed(
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    ) == []
    assert chunker.flush() == [
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    ]
