"""Bounded, address-pinned public HTTP retrieval for chapter snapshots."""

import argparse
import http.client
import ipaddress
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
from urllib.parse import quote, urljoin, urlsplit

import bookir as ir
import reviewstate

SOCKET_TIMEOUT = 15
WALL_TIMEOUT = 60
REDIRECT_LIMIT = 5


def destination(url, allowed_hosts=None):
    if not isinstance(url, str) or any(ord(char) < 33 for char in url):
        raise reviewstate.Refused("unsafe-source", "source URL contains invalid characters")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise reviewstate.Refused("unsafe-source", "only credential-free HTTP(S) sources are supported")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in (80, 443):
        raise reviewstate.Refused("unsafe-source", "source URL must use a standard HTTP(S) port")
    if allowed_hosts is not None and host not in allowed_hosts:
        raise reviewstate.Refused("unsafe-source", "source or redirect host is outside the declared hosts")
    addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    if not addresses:
        raise reviewstate.Refused("unsafe-source", "source hostname resolved to no public address")
    for value in addresses:
        address = ipaddress.ip_address(value)
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise reviewstate.Refused("unsafe-source", "source hostname must resolve only to public unicast addresses")
    return parsed, host, port, sorted(addresses, key=lambda value: ipaddress.ip_address(value).version)


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, addresses, secure):
        super().__init__(host, port, timeout=SOCKET_TIMEOUT)
        self.addresses, self.secure = addresses, secure

    def connect(self):
        failure = None
        for address in self.addresses:
            try:
                self.sock = socket.create_connection((address, self.port), self.timeout)
                if self.secure:
                    self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)
                return
            except OSError as error:
                failure = error
                if self.sock is not None:
                    self.sock.close()
                    self.sock = None
        raise failure or OSError("no source address could be connected")


def fetch(url, *, limit, allowed_hosts, media_types):
    """Read at most limit bytes; callers use the worker for a total DNS/I/O bound."""
    visited = set()
    for _ in range(REDIRECT_LIMIT + 1):
        if url in visited:
            raise reviewstate.Refused("source-redirect", "source redirect loop")
        visited.add(url)
        parsed, host, port, addresses = destination(url, allowed_hosts)
        connection = _PinnedConnection(host, port, addresses, parsed.scheme == "https")
        try:
            target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parsed.query:
                target += "?" + quote(parsed.query, safe="=&%+/:;?@!$'()*,-._~")
            connection.request("GET", target, headers={"User-Agent": "Revayat-Novel/1.0", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise reviewstate.Refused("source-redirect", "redirect has no location")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise reviewstate.Refused("source-http", f"source returned HTTP {response.status}")
            if response.getheader("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise reviewstate.Refused("source-encoding", "compressed HTTP responses are not accepted")
            mime = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if mime not in media_types:
                raise reviewstate.Refused("source-type", "source response has an unsupported media type")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > limit):
                raise reviewstate.Refused("source-limit", "source response exceeds its declared byte limit")
            body = bytearray()
            while True:
                part = response.read(min(65536, limit + 1 - len(body)))
                if not part:
                    break
                body.extend(part)
                if len(body) > limit:
                    raise reviewstate.Refused("source-limit", "source response exceeds its byte limit")
            if length is not None and len(body) != int(length):
                raise reviewstate.Refused("source-incomplete", "source response ended before its declared length")
            return bytes(body), {"url": url, "media_type": mime}
        finally:
            connection.close()
    raise reviewstate.Refused("source-redirect", "source exceeded the redirect limit")


def download(url, target, *, limit, allowed_hosts, media_types):
    command = [sys.executable, str(Path(__file__).resolve()), "--url", url,
               "--out", str(target), "--limit", str(limit), "--hosts",
               json.dumps(sorted(allowed_hosts)), "--types", json.dumps(sorted(media_types))]
    try:
        result = ir.run_bounded(command, WALL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise reviewstate.Refused("source-timeout", "source retrieval exceeded its total wall limit") from None
    try:
        report = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise reviewstate.Refused("source-worker", "source worker returned no valid result") from None
    if not isinstance(report, dict):
        raise reviewstate.Refused("source-worker", "source worker returned an invalid result object")
    if result.returncode or not report.get("ok"):
        raise reviewstate.Refused(report.get("refused", "source-worker"), report.get("detail", "source retrieval failed"))
    raw = Path(target).read_bytes()
    if len(raw) > limit or ir.sha256_bytes(raw) != report["sha256"]:
        raise reviewstate.Refused("source-changed", "retrieved source bytes no longer match the worker result")
    return raw, report


@reviewstate.cli
def main(argv=None):
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--hosts", required=True)
    parser.add_argument("--types", required=True)
    args = parser.parse_args(argv)
    try:
        if not 0 < args.limit <= 32 * 1024 * 1024:
            raise ValueError("invalid response limit")
        hosts, types = json.loads(args.hosts), json.loads(args.types)
        if not all(isinstance(values, list) and values and all(isinstance(value, str) for value in values) for values in (hosts, types)):
            raise ValueError("invalid allowed hosts or media types")
        raw, metadata = fetch(args.url, limit=args.limit, allowed_hosts=set(hosts), media_types=set(types))
        with args.out.open("xb") as handle:
            handle.write(raw)
        print(json.dumps({"ok": True, "sha256": ir.sha256_bytes(raw), **metadata}))
        return 0
    except reviewstate.Refused as error:
        print(json.dumps({"ok": False, "refused": error.reason, "detail": error.detail}))
    except (OSError, ValueError, http.client.HTTPException):
        print(json.dumps({"ok": False, "refused": "source-network", "detail": "source retrieval failed; inspect URL, network and response format"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
