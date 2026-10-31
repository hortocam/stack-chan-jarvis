"""Session registry contract (T013).

One live session per device id; the newest connection replaces the older one
exactly once (FR-011, plan D11). The registry is the only place that owns
mutable per-device state.
"""

from __future__ import annotations

import pytest

from jarvis_gateway.session import DeviceSession, SessionRegistry, SessionState


class FakeTurn:
    cancel = None


def make_session(device_id: str = "aa:bb:cc:dd:ee:ff", **overrides) -> DeviceSession:
    values = {
        "device_id": device_id,
        "client_id": "client-1",
        "protocol_version": 1,
        "session_id": f"session-{device_id}",
    }
    values.update(overrides)
    return DeviceSession(**values)


def test_new_session_starts_connected_and_is_touched() -> None:
    session = make_session()

    assert session.state is SessionState.CONNECTED
    assert session.utterance is None
    assert session.turn is None
    assert session.created_at > 0
    assert session.last_activity >= session.created_at


def test_two_connections_for_one_device_replace_the_first_exactly_once() -> None:
    replaced: list[DeviceSession] = []
    registry = SessionRegistry(on_replace=replaced.append)
    first = make_session()
    second = make_session(session_id="session-2")

    registry.register(first)
    returned = registry.register(second)

    assert returned is first
    assert replaced == [first]
    assert first.state is SessionState.CLOSED
    assert registry.get(first.device_id) is second
    assert registry.register(second) is None  # registering the same object again is idempotent


def test_replacement_closes_the_old_session_and_clears_its_audio() -> None:
    registry = SessionRegistry()
    first = make_session()
    first.utterance = object()
    first.turn = FakeTurn()

    registry.register(first)
    registry.register(make_session())

    assert first.utterance is None
    assert first.turn is None


def test_distinct_devices_coexist() -> None:
    registry = SessionRegistry()
    one = make_session("aa:aa:aa:aa:aa:aa")
    two = make_session("bb:bb:bb:bb:bb:bb")

    registry.register(one)
    registry.register(two)

    assert len(registry) == 2
    assert registry.get(one.device_id) is one
    assert registry.get(two.device_id) is two


def test_one_device_id_replaces_only_that_device() -> None:
    registry = SessionRegistry()
    one = make_session("aa:aa:aa:aa:aa:aa")
    two = make_session("bb:bb:bb:bb:bb:bb")
    registry.register(one)
    registry.register(two)

    registry.register(make_session("aa:aa:aa:aa:aa:aa"))

    assert registry.get(two.device_id) is two
    assert len(registry) == 2


def test_idle_scan_closes_only_expired_sessions() -> None:
    registry = SessionRegistry(idle_s=120.0)
    old = make_session("aa:aa:aa:aa:aa:aa")
    fresh = make_session("bb:bb:bb:bb:bb:bb")
    registry.register(old, now=1000.0)
    registry.register(fresh, now=1150.0)

    closed = registry.scan_idle(now=1200.0)

    assert closed == [old]
    assert old.state is SessionState.CLOSED
    assert fresh.state is not SessionState.CLOSED
    assert registry.get(old.device_id) is None
    assert len(registry) == 1


def test_activity_defers_the_idle_scan() -> None:
    registry = SessionRegistry(idle_s=120.0)
    session = make_session()
    registry.register(session, now=1000.0)

    session.touch(now=1100.0)

    assert registry.scan_idle(now=1200.0) == []
    assert registry.scan_idle(now=1221.0) == [session]


def test_close_clears_references_and_removes_the_session() -> None:
    registry = SessionRegistry()
    session = make_session()
    session.utterance = object()
    session.turn = FakeTurn()
    registry.register(session)

    registry.close(session, reason="test")

    assert session.state is SessionState.CLOSED
    assert session.utterance is None
    assert session.turn is None
    assert session.close_reason == "test"
    assert registry.get(session.device_id) is None
    assert len(registry) == 0


def test_close_is_idempotent() -> None:
    registry = SessionRegistry()
    session = make_session()
    registry.register(session)

    registry.close(session)
    registry.close(session)

    assert len(registry) == 0


def test_counts_report_sessions_by_state() -> None:
    registry = SessionRegistry()
    idle = make_session("aa:aa:aa:aa:aa:aa")
    idle.state = SessionState.LISTENING
    registry.register(idle)
    registry.register(make_session("bb:bb:bb:bb:bb:bb"))

    counts = registry.counts()

    assert counts["total"] == 2
    assert counts["by_state"]["listening"] == 1
    assert counts["by_state"]["connected"] == 1
    assert counts["by_state"]["speaking"] == 0


def test_state_enum_covers_the_plan_state_machine() -> None:
    names = {state.name for state in SessionState}

    assert names == {
        "CONNECTED",
        "IDLE",
        "LISTENING",
        "THINKING",
        "SPEAKING",
        "CANCELLING",
        "CLOSED",
    }


@pytest.mark.parametrize("device_id", ["", None])
def test_device_id_is_required(device_id) -> None:
    with pytest.raises(ValueError):
        make_session(device_id)
