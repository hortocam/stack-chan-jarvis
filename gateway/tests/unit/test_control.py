"""Control socket contract (T016).

The bridge and the gateway talk newline-delimited JSON over an ``AF_UNIX``
stream socket. The filesystem is the authentication boundary: the socket is
0660 and never a TCP listener (plan D9, C5).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

from jarvis_gateway.control import (
    ControlClient,
    ControlError,
    ControlServer,
    NotConnected,
    StaticBackend,
)


@pytest.fixture
def sockdir():
    """A short directory: AF_UNIX paths are capped near 107 bytes."""

    directory = tempfile.mkdtemp(prefix="jg-ctl-")
    try:
        yield Path(directory)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def tmp_socket(sockdir: Path) -> Path:
    return sockdir / "control.sock"


async def start_server(path: Path, backend=None) -> ControlServer:
    server = ControlServer(path, backend or StaticBackend())
    await server.start()
    return server


async def test_round_trip_tools_list(sockdir: Path) -> None:
    server = await start_server(tmp_socket(sockdir))
    try:
        client = ControlClient(server.path, timeout=1.0)

        result = await client.request("tools/list")

        assert result == {"tools": [{"name": "robot_get_head_angles"}]}
    finally:
        await server.stop()


async def test_round_trip_tools_call(sockdir: Path) -> None:
    server = await start_server(tmp_socket(sockdir))
    try:
        client = ControlClient(server.path, timeout=1.0)

        result = await client.request(
            "tools/call", {"name": "self.robot.set_head_angles", "arguments": {"yaw": 10}}
        )

        assert result == {"content": [{"type": "text", "text": "ok"}], "isError": False}
    finally:
        await server.stop()


async def test_socket_permissions_are_0660(sockdir: Path) -> None:
    server = await start_server(tmp_socket(sockdir))
    try:
        mode = stat.S_IMODE(os.stat(server.path).st_mode)
        assert mode == 0o660
    finally:
        await server.stop()


async def test_socket_file_is_removed_on_stop(sockdir: Path) -> None:
    path = tmp_socket(sockdir)
    server = await start_server(path)

    await server.stop()

    assert not path.exists()


async def test_malformed_line_gets_parse_error_and_the_connection_survives(
    sockdir: Path,
) -> None:
    server = await start_server(tmp_socket(sockdir))
    try:
        reader, writer = await asyncio.open_unix_connection(server.path)
        writer.write(b"this is not json\n")
        await writer.drain()

        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=2.0))

        assert response["error"]["code"] == -32700
        assert response["id"] is None

        writer.write(b'{"id": 2, "method": "tools/list"}\n')
        await writer.drain()
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=2.0))
        assert response["id"] == 2
        assert "result" in response
        writer.close()
    finally:
        await server.stop()


async def test_oversized_line_is_rejected_without_buffering(sockdir: Path) -> None:
    server = ControlServer(tmp_socket(sockdir), StaticBackend(), max_line_bytes=256)
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.path)
        writer.write(b'{"id": 1, "method": "' + b"x" * 1024 + b'"}\n')
        await writer.drain()

        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=2.0))
        assert response["error"]["code"] == -32600
        assert await asyncio.wait_for(reader.readline(), timeout=2.0) == b""
        writer.close()
    finally:
        await server.stop()


async def test_unknown_method_is_refused(sockdir: Path) -> None:
    server = await start_server(tmp_socket(sockdir))
    try:
        client = ControlClient(server.path, timeout=1.0)

        with pytest.raises(ControlError) as excinfo:
            await client.request("tools/delete_everything")

        assert excinfo.value.code == -32601
    finally:
        await server.stop()


async def test_tools_call_without_a_session_is_not_connected(sockdir: Path) -> None:
    server = await start_server(tmp_socket(sockdir), StaticBackend(connected=False))
    try:
        client = ControlClient(server.path, timeout=1.0)

        with pytest.raises(NotConnected) as excinfo:
            await client.request("tools/call", {"name": "self.robot.get_head_angles"})

        assert excinfo.value.code == -32000
        assert excinfo.value.message == "robot not connected"
    finally:
        await server.stop()


async def test_missing_socket_is_not_connected(sockdir: Path) -> None:
    client = ControlClient(tmp_socket(sockdir), timeout=0.2)

    with pytest.raises(NotConnected) as excinfo:
        await client.request("tools/list")

    assert excinfo.value.code == -32000
    assert excinfo.value.message == "robot not connected"


async def test_client_timeout_is_not_connected(sockdir: Path) -> None:
    path = tmp_socket(sockdir)

    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(5)

    server = await asyncio.start_unix_server(silent, path=str(path))
    try:
        client = ControlClient(path, timeout=0.05)

        with pytest.raises(ControlError) as excinfo:
            await client.request("tools/list")

        assert excinfo.value.code == -32000
    finally:
        server.close()
        await server.wait_closed()


async def test_two_clients_are_served_independently(sockdir: Path) -> None:
    server = await start_server(tmp_socket(sockdir))
    try:
        one = ControlClient(server.path, timeout=1.0)
        two = ControlClient(server.path, timeout=1.0)

        results = await asyncio.gather(one.request("tools/list"), two.request("tools/list"))

        assert results == [{"tools": [{"name": "robot_get_head_angles"}]}] * 2
    finally:
        await server.stop()
