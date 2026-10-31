"""Map a reply to one of the StackChan firmware's eight emotions (T023, FR-009).

The accepted strings are fixed by the firmware's ``SetEmotion``
(``firmware/main/hal/board/stackchan_display.cc``): ``neutral``, ``happy``,
``laughing``, ``angry``, ``sad``, ``crying``, ``sleepy``, ``doubtful``. Unknown
strings are drawn as a neutral face and only logged, so this module's contract is
that it never returns anything outside the set.

Matching is deliberately a small keyword table, not a model: it is deterministic,
testable, and costs nothing on the latency path. The order is most-specific first
(``crying`` before ``sad``, ``laughing`` before ``happy``), and a body-tool hint
can pick the face only when the text itself is neutral.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["ACCEPTED_EMOTIONS", "TOOL_EMOTIONS", "choose_emotion"]

#: Exactly what the firmware's SetEmotion understands (FR-009).
ACCEPTED_EMOTIONS = (
    "neutral",
    "happy",
    "laughing",
    "angry",
    "sad",
    "crying",
    "sleepy",
    "doubtful",
)

#: Keywords, checked in this order; the first hit wins.
EMOTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("crying", ("cry", "crying", "tears", "sob", "weep")),
    ("laughing", ("ha ha", "haha", "laugh", "funny", "joke", "hilarious")),
    ("angry", ("angry", "furious", "annoyed", "irritated", "outraged", "how dare")),
    ("sad", ("sorry", "sad", "unfortunately", "afraid", "regret", "miss you")),
    ("sleepy", ("sleep", "sleepy", "tired", "bedtime", "good night", "exhausted")),
    ("doubtful", ("not sure", "unsure", "maybe", "perhaps", "i doubt", "hmm", "?")),
    (
        "happy",
        (
            "happy",
            "glad",
            "delighted",
            "wonderful",
            "great",
            "lovely",
            "perfect",
            "congrat",
            "excellent",
            "yes!",
            "pleasure",
        ),
    ),
)

#: A tool that moved the body gets a cheerful face when the words are neutral.
TOOL_EMOTIONS: dict[str, str] = {
    "self.robot.set_head_angles": "happy",
    "self.robot.set_led_color": "happy",
    "self.robot.create_reminder": "happy",
    "self.robot.stop_reminder": "happy",
    "self.robot.get_head_angles": "neutral",
    "self.robot.get_reminders": "neutral",
}


def choose_emotion(text: str, *, tools_used: Sequence[str] = ()) -> str:
    """Return one emotion from :data:`ACCEPTED_EMOTIONS` for a reply.

    ``tools_used`` carries the firmware tool names a turn called (US4); a text
    with no emotional keyword inherits the most expressive of their emotions.
    """

    haystack = (text or "").casefold()
    for emotion, keywords in EMOTION_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return emotion

    for name in tools_used:
        emotion = TOOL_EMOTIONS.get(name)
        if emotion and emotion != "neutral":
            return emotion
    return "neutral"
