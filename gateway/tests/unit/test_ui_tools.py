"""Unit tests for the ``self.ui.*`` tool surface (card t_b4d67050).

The UI tools are the gateway's second, non-body MCP surface. They are
best-effort: the wire shape and the argument limits are the contract the
firmware card builds to, so every field is asserted here before the channel
that sends it exists.

The negative controls matter as much as the happy shapes: an unknown tool
name, an extra argument and an out-of-range value must all be refused in the
gateway, not discovered on the device.
"""

from __future__ import annotations

import pytest

from jarvis_gateway.ui import (
    DEFAULT_HOLD_MS,
    HEADLINE_MAX,
    LABEL_MAX,
    LOG_LINE_MAX,
    UI_CLOCKS,
    UI_GLOBE_FOCUS,
    UI_LOG_LINE,
    UI_STATE,
    UI_STATES,
    UI_TOOLS,
    UiError,
    build_ui_call,
    clocks_arguments,
)


def test_ui_tools_are_exactly_the_four_the_card_names() -> None:
    assert UI_TOOLS == frozenset({UI_GLOBE_FOCUS, UI_LOG_LINE, UI_STATE, UI_CLOCKS})
    assert UI_STATES == frozenset(
        {"idle", "listening", "thinking", "speaking", "attention", "sleeping"}
    )


def test_globe_focus_shape_and_default_hold() -> None:
    call = build_ui_call(
        UI_GLOBE_FOCUS,
        {
            "lat": 42.3601,
            "lon": -71.0589,
            "label": "Boston",
            "headline": "Lab: 359 tests green",
        },
    )

    assert call == {
        "name": "self.ui.globe_focus",
        "arguments": {
            "lat": 42.3601,
            "lon": -71.0589,
            "label": "Boston",
            "headline": "Lab: 359 tests green",
            "hold_ms": DEFAULT_HOLD_MS,
        },
    }


def test_log_line_keeps_the_text_verbatim() -> None:
    text = "git: PR #11 merged: docs/ui-concept"
    call = build_ui_call(UI_LOG_LINE, {"text": text})

    assert call == {"name": "self.ui.log_line", "arguments": {"text": text}}


def test_state_accepts_every_documented_state() -> None:
    for state in sorted(UI_STATES):
        call = build_ui_call(UI_STATE, {"name": state})
        assert call["arguments"] == {"name": state}


def test_clocks_shape() -> None:
    call = build_ui_call(UI_CLOCKS, {"utc": "17:56", "local": "12:56", "tz": "EST"})

    assert call == {
        "name": "self.ui.clocks",
        "arguments": {"utc": "17:56", "local": "12:56", "tz": "EST"},
    }


def test_clocks_arguments_from_a_timestamp() -> None:
    args = clocks_arguments(0.0)

    assert args["utc"] == "00:00"
    assert len(args["local"]) == 5 and args["local"][2] == ":"
    assert args["tz"] and len(args["tz"]) <= 8


def test_unknown_tool_is_refused_and_names_the_tool() -> None:
    """Negative control: only the four UI tools may ever reach the wire."""

    with pytest.raises(UiError, match="self.ui.reboot"):
        build_ui_call("self.ui.reboot", {})


@pytest.mark.parametrize(
    ("name", "arguments", "field"),
    [
        (UI_GLOBE_FOCUS, {"lat": 91, "lon": 0, "label": "x", "headline": "h"}, "lat"),
        (UI_GLOBE_FOCUS, {"lat": 0, "lon": -181, "label": "x", "headline": "h"}, "lon"),
        (UI_GLOBE_FOCUS, {"lat": True, "lon": 0, "label": "x", "headline": "h"}, "lat"),
        (UI_GLOBE_FOCUS, {"lat": 0, "lon": 0, "label": "", "headline": "h"}, "label"),
        (
            UI_GLOBE_FOCUS,
            {"lat": 0, "lon": 0, "label": "x" * (LABEL_MAX + 1), "headline": "h"},
            "label",
        ),
        (
            UI_GLOBE_FOCUS,
            {"lat": 0, "lon": 0, "label": "x", "headline": "h" * (HEADLINE_MAX + 1)},
            "headline",
        ),
        (
            UI_GLOBE_FOCUS,
            {"lat": 0, "lon": 0, "label": "x", "headline": "h", "hold_ms": 0},
            "hold_ms",
        ),
        (
            UI_GLOBE_FOCUS,
            {"lat": 0, "lon": 0, "label": "x", "headline": "h", "hold_ms": 60001},
            "hold_ms",
        ),
        (UI_GLOBE_FOCUS, {"lat": 0, "lon": 0, "label": "x"}, "headline"),
        (UI_LOG_LINE, {"text": ""}, "text"),
        (UI_LOG_LINE, {"text": "x" * (LOG_LINE_MAX + 1)}, "text"),
        (UI_STATE, {"name": "dancing"}, "name"),
        (UI_CLOCKS, {"utc": "7:00", "local": "12:56", "tz": "EST"}, "utc"),
        (UI_CLOCKS, {"utc": "24:00", "local": "12:56", "tz": "EST"}, "utc"),
        (UI_CLOCKS, {"utc": "17:56", "local": "12:56", "tz": ""}, "tz"),
    ],
)
def test_invalid_arguments_are_refused_with_the_field_named(
    name: str, arguments: dict[str, object], field: str
) -> None:
    """Negative control: the validation message names the offending field."""

    with pytest.raises(UiError, match=field):
        build_ui_call(name, arguments)


def test_extra_arguments_are_refused() -> None:
    with pytest.raises(UiError, match="unexpected argument"):
        build_ui_call(UI_LOG_LINE, {"text": "ok", "colour": "green"})
