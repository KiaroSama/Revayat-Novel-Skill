"""Public web retrieval contracts with only the external socket boundary faked."""

from io import BytesIO
import socket

import pytest

import reviewstate
import webfetch


def wire(monkeypatch, response, addresses=("1.1.1.1",)):
    observed = {"connections": [], "requests": []}

    class Socket:
        def sendall(self, data):
            observed["requests"].append(data)

        def makefile(self, *args, **kwargs):
            return BytesIO(response)

        def close(self):
            pass

    def connect(address, timeout):
        observed["connections"].append(address)
        assert timeout == 15
        return Socket()

    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80)) for address in addresses])
    monkeypatch.setattr(socket, "create_connection", connect)
    return observed


def test_fetch_pins_the_public_address_and_preserves_http_host_and_unicode_path(monkeypatch):
    observed = wire(monkeypatch, b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: 7\r\n\r\nchapter")
    body, result = webfetch.fetch("http://books.example/é", limit=20,
        allowed_hosts={"books.example"}, media_types={"text/html"})
    assert body == b"chapter" and result["media_type"] == "text/html"
    assert observed["connections"] == [("1.1.1.1", 80)]
    assert b"Host: books.example\r\n" in observed["requests"][0]
    assert b"GET /%C3%A9 HTTP/1.1" in observed["requests"][0]


@pytest.mark.parametrize("response,reason", [
    (b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/private\r\nContent-Length: 0\r\n\r\n", "unsafe-source"),
    (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 9999\r\n\r\n", "source-limit"),
    (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + b"x" * 21, "source-limit"),
    (b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n\r\n", "source-type"),
    (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Encoding: gzip\r\n\r\n", "source-encoding"),
    (b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 0\r\n\r\n", "source-http"),
])
def test_redirects_response_limits_and_failures_cannot_become_chapters(monkeypatch, response, reason):
    observed = wire(monkeypatch, response)
    with pytest.raises(reviewstate.Refused) as error:
        webfetch.fetch("http://books.example/chapter", limit=20,
                       allowed_hosts={"books.example"}, media_types={"text/html"})
    assert error.value.reason == reason
    assert len(observed["connections"]) == 1


def test_mixed_public_private_dns_is_refused_before_any_connection(monkeypatch):
    observed = wire(monkeypatch, b"", addresses=("1.1.1.1", "127.0.0.1"))
    with pytest.raises(reviewstate.Refused) as error:
        webfetch.fetch("http://books.example/", limit=20,
                       allowed_hosts={"books.example"}, media_types={"text/html"})
    assert error.value.reason == "unsafe-source"
    assert observed["connections"] == []
