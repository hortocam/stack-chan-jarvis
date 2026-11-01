"""Adapters from the lab's webhook payloads to UI events.

The gateway does not add a listener for these sources (constitution I): the
observers that already exist — Gitea/GitHub webhooks, the kanban board, Hermes
cron, Uptime-Kuma — hand a decoded payload to :meth:`UiEventBus.publish_source`.
This module is the whole translation layer, kept pure so it can be tested
without any of them.

Order is the card's: git/PR, kanban, cron, Uptime-Kuma. Headlines are the
separate ``feed`` module because their source is external and starts canned.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .feed import Headline
from .ui import LOG_LINE_MAX
from .ui_events import UiEvent

__all__ = [
    "SOURCE_PARSERS",
    "event_from_headline",
    "events_from_cron_digest",
    "events_from_git",
    "events_from_kanban",
    "events_from_uptime_kuma",
]


def _truncate(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= LOG_LINE_MAX:
        return text
    return text[: LOG_LINE_MAX - 3] + "..."


def _log(text: str) -> UiEvent:
    return UiEvent("log_line", {"text": _truncate(text)})


def _attention() -> UiEvent:
    return UiEvent("state", {"name": "attention"})


def _git_pull_request(payload: dict[str, Any], action: Any) -> list[UiEvent]:
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        return []
    number = pull_request.get("number")
    title = pull_request.get("title")
    if not isinstance(number, int) or isinstance(number, bool):
        return []
    title = str(title or "").strip()
    if action == "closed" and pull_request.get("merged") is True:
        return [_log(f"git: PR #{number} merged: {title}")]
    if action == "opened":
        return [_log(f"git: PR #{number} opened: {title}")]
    return []


def events_from_git(payload: dict[str, Any]) -> list[UiEvent]:
    """GitHub/Gitea pull-request and push payloads → log lines."""

    if not isinstance(payload, dict):
        return []
    events = _git_pull_request(payload, payload.get("action"))
    if events:
        return events

    commits = payload.get("commits")
    ref = payload.get("ref")
    if isinstance(commits, list) and isinstance(ref, str) and ref:
        branch = ref.rsplit("/", 1)[-1]
        return [_log(f"git: {len(commits)} commits to {branch}")]
    return []


def events_from_kanban(payload: dict[str, Any]) -> list[UiEvent]:
    """A card transition → a log line; blocked/failed also raises attention."""

    if not isinstance(payload, dict):
        return []
    card = payload.get("card")
    to = payload.get("to")
    if not isinstance(card, dict) or not isinstance(to, str) or not to:
        return []
    title = str(card.get("title") or card.get("id") or "").strip()
    if not title:
        return []
    events: list[UiEvent] = []
    if to in {"blocked", "failed"}:
        events.append(_attention())
    events.append(_log(f"kanban: {to}: {title}"))
    return events


def events_from_cron_digest(payload: dict[str, Any]) -> list[UiEvent]:
    """A Hermes cron digest → one log line."""

    if not isinstance(payload, dict):
        return []
    job = str(payload.get("job") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    if not job or not summary:
        return []
    return [_log(f"cron: {job}: {summary}")]


def events_from_uptime_kuma(payload: dict[str, Any]) -> list[UiEvent]:
    """An Uptime-Kuma alert → attention on DOWN, a log line either way."""

    if not isinstance(payload, dict):
        return []
    monitor = payload.get("monitor")
    heartbeat = payload.get("heartbeat")
    if not isinstance(monitor, dict) or not isinstance(heartbeat, dict):
        return []
    name = str(monitor.get("name") or "").strip()
    if not name:
        return []
    status = heartbeat.get("status")
    down = status in {0, "0", "down", "DOWN"}
    message = str(heartbeat.get("msg") or "").strip()
    events: list[UiEvent] = []
    if down:
        events.append(_attention())
        detail = f": {message}" if message else ""
        events.append(_log(f"uptime: {name} DOWN{detail}"))
    else:
        events.append(_log(f"uptime: {name} up"))
    return events


def event_from_headline(headline: Headline) -> UiEvent:
    """One headline ping → one globe focus (the feed module's output)."""

    return UiEvent(
        "globe_focus",
        {
            "lat": headline.lat,
            "lon": headline.lon,
            "label": headline.label,
            "headline": headline.headline,
        },
    )


#: Registration order matters only for documentation; dicts keep insertion order.
SOURCE_PARSERS: dict[str, Callable[[dict[str, Any]], list[UiEvent]]] = {
    "git": events_from_git,
    "kanban": events_from_kanban,
    "cron": events_from_cron_digest,
    "uptime_kuma": events_from_uptime_kuma,
}
