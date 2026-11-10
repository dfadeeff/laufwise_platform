"""Web import connects to the addresses it checked, even if the next DNS answer is private."""

import socket

import httpcore
import pytest

from app.agents import web_import


def test_rebinding_cannot_change_the_address_used_for_the_connection(monkeypatch):
    answers = iter(["93.184.216.34", "127.0.0.1"])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), 443))
    ])
    connected = []
    stream = object()
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda self, host, port, **k:
                        connected.append(host) or stream)
    backend = web_import.PublicNetworkBackend()
    assert backend.connect_tcp("rebind.example", 443) is stream
    assert connected == ["93.184.216.34"]


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"])
def test_no_socket_is_opened_for_a_private_dns_answer(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
    ])
    def forbidden(*a, **k):
        pytest.fail("private network connection attempted")
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", forbidden)
    with pytest.raises(web_import.WebImportError):
        web_import.PublicNetworkBackend().connect_tcp("rebind.example", 443)


def test_a_mixed_public_private_answer_is_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ("93.184.216.34", "10.0.0.1")
    ])
    with pytest.raises(web_import.WebImportError):
        web_import.PublicNetworkBackend().connect_tcp("mixed.example", 443)
