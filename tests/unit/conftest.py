"""Unit tests fail if code attempts any network connection."""

import socket

import pytest


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    connect = socket.socket.connect
    socketpair = socket.socketpair

    def reject(*args, **kwargs):
        pytest.fail("Unit tests must not access the network")

    def local_socketpair(*args, **kwargs):
        # Windows implements this stdlib primitive with a private loopback pair.
        # Allow only its synchronous construction, not arbitrary loopback access.
        with monkeypatch.context() as temporary:
            temporary.setattr(socket.socket, "connect", connect)
            return socketpair(*args, **kwargs)

    monkeypatch.setattr(socket, "socketpair", local_socketpair)
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect_ex", reject)
    monkeypatch.setattr(socket, "create_connection", reject)
