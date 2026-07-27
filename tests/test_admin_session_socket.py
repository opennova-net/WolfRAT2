"""Loopback integration coverage for the retail TCP session.

The scripted peer in this module is a real TCP server.  Tests intentionally
control write boundaries because retail frames travel over a byte stream, where
one frame may span reads and several frames may arrive in one read.
"""

from __future__ import annotations

from contextlib import contextmanager
import socket
import struct
import threading
import time
from typing import Callable, Iterator

import pytest

from wolfrat.admin_session import RetailAdminSession, SocketTransport


MAGIC = b"\x00\x00\x0d\x0a"
ConnectionScript = Callable[[socket.socket], None]


def retail_frame(payload: bytes) -> bytes:
    return MAGIC + struct.pack("<I", 8 + len(payload)) + payload


def receive_exact(peer: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    while size:
        chunk = peer.recv(size)
        if not chunk:
            raise ConnectionError(f"client closed with {size} bytes outstanding")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def receive_frame(peer: socket.socket) -> bytes:
    header = receive_exact(peer, 8)
    assert header[:4] == MAGIC
    total_length = struct.unpack("<I", header[4:])[0]
    assert total_length >= 8
    return receive_exact(peer, total_length - 8)


def authenticate(
    peer: socket.socket,
    *,
    challenge_byte: bytes,
    fragmented: bool = False,
) -> None:
    challenge = retail_frame(challenge_byte * 32 + b"\x00")
    if fragmented:
        for boundary in (1, 3, 9, len(challenge)):
            peer.sendall(challenge[:boundary])
            challenge = challenge[boundary:]
        if challenge:
            peer.sendall(challenge)
    else:
        peer.sendall(challenge)

    assert len(receive_frame(peer)) == 65
    login = retail_frame(b"User logged in\x00")
    if fragmented:
        for byte in login:
            peer.sendall(bytes((byte,)))
    else:
        peer.sendall(login)


def close_abortively(peer: socket.socket) -> None:
    errors: list[OSError] = []
    for packing in ("HH", "ii"):
        try:
            peer.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_LINGER,
                struct.pack(packing, 1, 0),
            )
            peer.close()
            return
        except OSError as error:
            errors.append(error)
    raise errors[-1]


@contextmanager
def loopback_retail_server(
    *scripts: ConnectionScript,
) -> Iterator[tuple[str, int]]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(len(scripts))
    listener.settimeout(2)
    failures: list[BaseException] = []

    def serve() -> None:
        try:
            for script in scripts:
                peer, _address = listener.accept()
                peer.settimeout(2)
                with peer:
                    script(peer)
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=serve, daemon=True, name="LoopbackRetail")
    worker.start()
    try:
        yield listener.getsockname()
    finally:
        listener.close()
        worker.join(timeout=3)
        assert not worker.is_alive(), "loopback retail peer did not stop"
        if failures:
            raise failures[0]


def test_real_session_reassembles_fragmented_and_coalesced_retail_frames():
    def exchange(peer: socket.socket) -> None:
        authenticate(peer, challenge_byte=b"A", fragmented=True)
        assert receive_frame(peer) == b"SET Unknown value\x00"
        peer.sendall(
            retail_frame(b"ERROR In Game\x00")
            + retail_frame(b"OK\x00")
        )

    with loopback_retail_server(exchange) as (host, port):
        session = RetailAdminSession(
            host,
            port,
            username="admin",
            password="secret",
            timeout=0.5,
        )
        try:
            result = session.execute_raw("SET Unknown value").result(timeout=2)
        finally:
            session.close()

    assert result.replies == ("ERROR In Game", "OK")
    assert result.accepted is False


def test_real_session_reconnects_after_orderly_eof_mid_frame():
    def truncated_reply(peer: socket.socket) -> None:
        authenticate(peer, challenge_byte=b"B")
        assert receive_frame(peer) == b"GET GAMESTATE\x00"
        peer.sendall(retail_frame(b"GAME\x00")[:-2])
        peer.shutdown(socket.SHUT_WR)

    def recovered(peer: socket.socket) -> None:
        authenticate(peer, challenge_byte=b"C")
        assert receive_frame(peer) == b"GET GAMESTATE\x00"
        peer.sendall(retail_frame(b"GAME\x00"))

    with loopback_retail_server(truncated_reply, recovered) as (host, port):
        session = RetailAdminSession(
            host,
            port,
            username="admin",
            password="secret",
            timeout=0.5,
        )
        try:
            with pytest.raises(ConnectionError, match="bytes outstanding"):
                session.execute_raw("GET GAMESTATE").result(timeout=2)
            assert session.connected is False

            result = session.execute_raw("GET GAMESTATE").result(timeout=2)
        finally:
            session.close()

    assert result.replies == ("GAME",)
    assert result.accepted is True


def test_real_session_reconnects_after_peer_reset():
    def reset_reply(peer: socket.socket) -> None:
        authenticate(peer, challenge_byte=b"D")
        assert receive_frame(peer) == b"GET GAMESTATE\x00"
        close_abortively(peer)

    def recovered(peer: socket.socket) -> None:
        authenticate(peer, challenge_byte=b"E")
        assert receive_frame(peer) == b"GET GAMESTATE\x00"
        peer.sendall(retail_frame(b"GAME\x00"))

    with loopback_retail_server(reset_reply, recovered) as (host, port):
        session = RetailAdminSession(
            host,
            port,
            username="admin",
            password="secret",
            timeout=0.5,
        )
        try:
            with pytest.raises(ConnectionResetError):
                session.execute_raw("GET GAMESTATE").result(timeout=2)
            assert session.connected is False

            result = session.execute_raw("GET GAMESTATE").result(timeout=2)
        finally:
            session.close()

    assert result.replies == ("GAME",)


def test_real_session_reconnects_after_reply_timeout():
    timed_out = threading.Event()

    def missing_reply(peer: socket.socket) -> None:
        authenticate(peer, challenge_byte=b"F")
        assert receive_frame(peer) == b"GET GAMESTATE\x00"
        time.sleep(0.15)
        timed_out.set()

    def recovered(peer: socket.socket) -> None:
        authenticate(peer, challenge_byte=b"G")
        assert receive_frame(peer) == b"GET GAMESTATE\x00"
        peer.sendall(retail_frame(b"GAME\x00"))

    with loopback_retail_server(missing_reply, recovered) as (host, port):
        session = RetailAdminSession(
            host,
            port,
            username="admin",
            password="secret",
            timeout=0.05,
        )
        try:
            with pytest.raises(TimeoutError):
                session.execute_raw("GET GAMESTATE").result(timeout=2)
            assert session.connected is False
            assert timed_out.wait(1)

            result = session.execute_raw("GET GAMESTATE").result(timeout=2)
        finally:
            session.close()

    assert result.replies == ("GAME",)


def test_socket_transport_close_is_abortive_for_retail_cleanup():
    observed: list[bytes | BaseException] = []

    def observe_close(peer: socket.socket) -> None:
        try:
            observed.append(peer.recv(1))
        except BaseException as error:
            observed.append(error)

    with loopback_retail_server(observe_close) as (host, port):
        transport = SocketTransport()
        transport.connect(host, port, timeout=0.5)
        transport.close()

    assert len(observed) == 1
    assert isinstance(observed[0], ConnectionResetError)
