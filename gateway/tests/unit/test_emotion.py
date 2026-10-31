"""Reply text and tool use → one of the firmware's eight emotions (T023).

The StackChan firmware's ``SetEmotion`` accepts exactly these strings
(``firmware/main/hal/board/stackchan_display.cc``): ``neutral``, ``happy``,
``laughing``, ``angry``, ``sad``, ``crying``, ``sleepy``, ``doubtful``. FR-009.
Anything else is logged as unknown and falls back to the neutral face, so a bug
here would be invisible on hardware — hence the never-outside-the-set test.
"""

from __future__ import annotations

import random
import string

import pytest

from jarvis_gateway.emotion import ACCEPTED_EMOTIONS, choose_emotion

SAMPLES = {
    "neutral": "The kitchen light is off.",
    "happy": "What a wonderful surprise, I am delighted!",
    "laughing": "Ha ha, that is a funny one.",
    "angry": "I am furious that you did that.",
    "sad": "I am sorry, I could not find it.",
    "crying": "That story makes me want to cry.",
    "sleepy": "It is late; time for bed. Good night.",
    "doubtful": "I am not sure that is right.",
}


@pytest.mark.parametrize("expected,text", sorted(SAMPLES.items()))
def test_each_emotion_has_a_documented_trigger(expected: str, text: str) -> None:
    assert choose_emotion(text) == expected


def test_default_is_neutral() -> None:
    assert choose_emotion("") == "neutral"
    assert choose_emotion("   ") == "neutral"
    assert choose_emotion("The time is nine fifteen.") == "neutral"


@pytest.mark.parametrize("text", ["🙂🙂🙂", "こんにちは世界", "¿Qué hora es?", "🎉🎉"])
def test_unicode_and_emoji_input_never_leave_the_set(text: str) -> None:
    assert choose_emotion(text) in ACCEPTED_EMOTIONS


def test_the_result_is_never_outside_the_firmware_set() -> None:
    rng = random.Random(20_261_031)
    alphabet = string.ascii_letters + string.digits + " .,!?'\n🙂…"
    for _ in range(500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        assert choose_emotion(text) in ACCEPTED_EMOTIONS


def test_a_body_tool_maps_to_an_emotion_from_the_same_set() -> None:
    assert choose_emotion("Done.", tools_used=["self.robot.set_led_color"]) == "happy"
    assert choose_emotion("Done.", tools_used=["self.robot.create_reminder"]) == "happy"
    assert choose_emotion("Done.", tools_used=["self.robot.get_reminders"]) == "neutral"


def test_text_wins_over_a_neutral_tool() -> None:
    assert (
        choose_emotion("I am sorry, that failed.", tools_used=["self.robot.get_head_angles"])
        == "sad"
    )
