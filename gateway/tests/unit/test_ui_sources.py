"""Unit tests for the lab event sources that drive the UI.

Each source is a pure adapter: a webhook-shaped payload in, `UiEvent`s out.
The transport that brings the payload (Gitea/GitHub, Hermes cron, Uptime-Kuma)
already exists; the gateway adds no listener for it (constitution I).
"""

from __future__ import annotations

import pytest

from jarvis_gateway.feed import CannedFeed, Headline
from jarvis_gateway.ui import UI_GLOBE_FOCUS, UI_LOG_LINE, UI_STATE
from jarvis_gateway.ui_sources import (
    SOURCE_PARSERS,
    event_from_headline,
    events_from_cron_digest,
    events_from_git,
    events_from_kanban,
    events_from_uptime_kuma,
)


def test_source_parsers_cover_the_five_documented_sources() -> None:
    assert list(SOURCE_PARSERS) == ["git", "kanban", "cron", "uptime_kuma"]


def test_git_pull_request_merged_becomes_one_log_line() -> None:
    events = events_from_git(
        {
            "action": "closed",
            "pull_request": {"number": 11, "title": "docs/ui-concept", "merged": True},
        }
    )

    assert len(events) == 1
    assert events[0].kind == "log_line"
    assert events[0].arguments == {"text": "git: PR #11 merged: docs/ui-concept"}


def test_git_pull_request_opened_is_logged() -> None:
    events = events_from_git(
        {"action": "opened", "pull_request": {"number": 12, "title": "ui backend"}}
    )

    assert [event.arguments["text"] for event in events] == ["git: PR #12 opened: ui backend"]


def test_git_push_counts_the_commits() -> None:
    events = events_from_git(
        {"ref": "refs/heads/main", "commits": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    )

    assert [event.arguments["text"] for event in events] == ["git: 3 commits to main"]


def test_git_payload_we_do_not_understand_is_ignored() -> None:
    assert events_from_git({"action": "synchronize"}) == []


def test_kanban_blocked_raises_attention_and_logs() -> None:
    events = events_from_kanban({"card": {"title": "UI backend"}, "to": "blocked"})

    assert [event.kind for event in events] == ["state", "log_line"]
    assert events[0].arguments == {"name": "attention"}
    assert events[1].arguments == {"text": "kanban: blocked: UI backend"}


def test_kanban_done_only_logs() -> None:
    events = events_from_kanban({"card": {"title": "UI backend"}, "to": "done"})

    assert [event.kind for event in events] == ["log_line"]
    assert events[0].arguments == {"text": "kanban: done: UI backend"}


def test_cron_digest_becomes_one_log_line() -> None:
    events = events_from_cron_digest({"job": "nightly", "summary": "backup ok"})

    assert [event.arguments["text"] for event in events] == ["cron: nightly: backup ok"]


def test_uptime_kuma_down_raises_attention() -> None:
    events = events_from_uptime_kuma(
        {"monitor": {"name": "jarvis-gateway"}, "heartbeat": {"status": 0, "msg": "refused"}}
    )

    assert [event.kind for event in events] == ["state", "log_line"]
    assert events[0].arguments == {"name": "attention"}
    assert events[1].arguments == {"text": "uptime: jarvis-gateway DOWN: refused"}


def test_uptime_kuma_up_only_logs() -> None:
    events = events_from_uptime_kuma(
        {"monitor": {"name": "jarvis-gateway"}, "heartbeat": {"status": 1, "msg": "ok"}}
    )

    assert [event.arguments["text"] for event in events] == ["uptime: jarvis-gateway up"]


def test_headline_becomes_a_globe_focus() -> None:
    headline = Headline(label="Boston", headline="Lab: green", lat=42.36, lon=-71.06)
    event = event_from_headline(headline)

    assert event.kind == "globe_focus"
    assert event.name == UI_GLOBE_FOCUS
    assert event.arguments == {
        "lat": 42.36,
        "lon": -71.06,
        "label": "Boston",
        "headline": "Lab: green",
        "hold_ms": 8000,
    }


def test_long_text_is_truncated_to_the_log_width() -> None:
    events = events_from_cron_digest({"job": "job", "summary": "x" * 500})

    assert len(events[0].arguments["text"]) <= 120


def test_canned_feed_is_off_by_default() -> None:
    feed = CannedFeed()

    assert feed.next_event() is None
    assert feed.events() == []


def test_canned_feed_cycles_the_canned_headlines_when_enabled() -> None:
    feed = CannedFeed(enabled=True)

    first = feed.next_event()
    second = feed.next_event()

    assert first is not None and second is not None
    assert first.name == UI_GLOBE_FOCUS
    assert (first.arguments["label"], first.arguments["headline"]) != (
        second.arguments["label"],
        second.arguments["headline"],
    )


@pytest.mark.parametrize(
    "state", ["idle", "listening", "thinking", "speaking", "attention", "sleeping"]
)
def test_state_event_accepts_the_documented_states(state: str) -> None:
    from jarvis_gateway.ui_events import UiEvent

    assert UiEvent("state", {"name": state}).name == UI_STATE


def test_unknown_source_is_rejected() -> None:
    from jarvis_gateway.ui_events import UiEventBus

    bus = UiEventBus()
    with pytest.raises(KeyError, match="weather"):
        bus.publish_source("weather", {})


def test_log_line_wire_name() -> None:
    assert UI_LOG_LINE == "self.ui.log_line"
