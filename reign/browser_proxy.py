"""Per-context HTTPS egress boundary, including native browser redirect hops.

Playwright routing alone doesn't intercept every HTTP redirect. This loopback
CONNECT proxy checks the destination before opening each TCP tunnel and can
narrow an existing context to its credential origin. TLS stays end to end: no
request bodies, cookies, passwords, or decrypted traffic are read or recorded.
"""
import asyncio
import base64
import contextlib
import ipaddress
import os
import re
import secrets
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from reign.config import checked_url


class BrowserProxyError(RuntimeError):
    def __init__(self, code="proxy_unavailable"):
        self.code = code if code in {"proxy_unavailable", "origin_blocked", "invalid_request"} else "proxy_unavailable"
        super().__init__({"proxy_unavailable": "The managed browser network boundary is unavailable.",
                          "origin_blocked": "The destination is outside the permitted website policy.",
                          "invalid_request": "The proxy request is invalid."}[self.code])


def _hostname(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 253
            or not re.fullmatch(r"[A-Za-z0-9.-]+", value) or value.endswith(".")):
        raise BrowserProxyError("invalid_request")
    labels = value.split(".")
    if any(not 1 <= len(label) <= 63 or label.startswith("-") or label.endswith("-") for label in labels):
        raise BrowserProxyError("invalid_request")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise BrowserProxyError("origin_blocked")
    # Also reject legacy numeric IPv4 spellings understood by browser resolvers.
    if all(re.fullmatch(r"(?:[0-9]+|0[xX][0-9a-fA-F]+)", label) for label in labels):
        raise BrowserProxyError("origin_blocked")
    return value.lower()


def _approved_origin(origin):
    try:
        if not isinstance(origin, str) or any(ord(char) < 33 or ord(char) > 126 for char in origin):
            raise ValueError()
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise ValueError()
        host = _hostname(parsed.hostname)
        result = "https://" + host
        checked_url(result)
        return result
    except (ValueError, TypeError, AttributeError):
        raise BrowserProxyError("origin_blocked") from None


@dataclass(repr=False)
class _Tunnel:
    origin: str
    client: asyncio.StreamWriter
    upstream: asyncio.StreamWriter | None = None


class BrowserProxy:
    HEADER_LIMIT = 8192
    HEADER_TIMEOUT = 5.0
    CONNECT_TIMEOUT = 10.0
    RESOLVE_TIMEOUT = 4.0
    IDLE_TIMEOUT = 120.0
    TUNNEL_TIMEOUT = 1800.0
    MAX_CONNECTIONS = 32
    BUFFER_SIZE = 65536

    def __init__(self, *, connect_factory=None):
        # A constructor-only test seam maps approved fixture names to local TLS
        # servers. Production has no resolver/environment policy bypass.
        self._connect = connect_factory or self._public_connect
        self._server = None
        self._tasks = set()
        self._clients = set()
        self._tunnels = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._pinned_origin = None
        self._username = "reign"
        self._password = secrets.token_urlsafe(32)
        self._authorization = b"Basic " + base64.b64encode((self._username + ":" + self._password).encode("ascii"))

    @property
    def url(self):
        if self._server is None or self._closed:
            raise BrowserProxyError()
        return "http://127.0.0.1:" + str(self._server.sockets[0].getsockname()[1])

    @property
    def playwright_settings(self):
        # These ephemeral credentials must remain in worker memory, never logs.
        return {"server": self.url, "bypass": "<-loopback>",
                "username": self._username, "password": self._password}

    async def start(self):
        async with self._lock:
            if self._closed or os.getenv("BROWSER_PROXY", "").strip():
                raise BrowserProxyError()
            if self._server is not None:
                return self
            try:
                self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0,
                    limit=self.HEADER_LIMIT, backlog=self.MAX_CONNECTIONS)
            except Exception:
                raise BrowserProxyError() from None
        return self

    def _accept(self, reader, writer):
        if self._closed or len(self._tasks) >= self.MAX_CONNECTIONS:
            writer.close()
            return
        self._clients.add(writer)
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _allowed(self, origin):
        _approved_origin(origin)
        if self._pinned_origin is not None and origin != self._pinned_origin:
            raise BrowserProxyError("origin_blocked")

    async def _public_connect(self, host, port):
        try:
            answers = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP), self.RESOLVE_TIMEOUT)
        except Exception:
            raise BrowserProxyError() from None
        if not answers or len(answers) > 16:
            raise BrowserProxyError("origin_blocked")
        destinations = []
        for family, kind, protocol, canonical, address in answers:
            try:
                numeric = ipaddress.ip_address(address[0])
            except (ValueError, TypeError, IndexError):
                raise BrowserProxyError("origin_blocked") from None
            if (family not in {socket.AF_INET, socket.AF_INET6} or not numeric.is_global
                    or numeric.is_multicast or numeric.is_reserved or numeric.is_unspecified
                    or (family == socket.AF_INET6 and address[3] != 0)):
                raise BrowserProxyError("origin_blocked")
            destination = (family, str(numeric))
            if destination not in destinations:
                destinations.append(destination)
        # Use the vetted numeric address directly. A second DNS lookup could
        # otherwise turn an approved public name into a private destination.
        for family, numeric in destinations:
            try:
                return await asyncio.wait_for(asyncio.open_connection(numeric, port,
                    family=family, flags=socket.AI_NUMERICHOST), 2.0)
            except (OSError, TimeoutError):
                continue
        raise BrowserProxyError()

    async def pin_origin(self, origin):
        origin = _approved_origin(origin)
        async with self._lock:
            if self._closed:
                raise BrowserProxyError()
            if self._pinned_origin is not None and self._pinned_origin != origin:
                raise BrowserProxyError("origin_blocked")
            self._pinned_origin = origin
            rejected = [task for task, tunnel in self._tunnels.items() if tunnel.origin != origin]
            for task in rejected:
                tunnel = self._tunnels[task]
                tunnel.client.close()
                if tunnel.upstream:
                    tunnel.upstream.close()
                task.cancel()
        # No other-origin relay may survive the return to credential injection.
        if rejected:
            await asyncio.gather(*rejected, return_exceptions=True)

    @staticmethod
    async def _close_writer(writer):
        if writer is None:
            return
        writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer.wait_closed(), 1.0)

    async def close(self):
        async with self._lock:
            self._closed = True
            server, self._server = self._server, None
        if server:
            server.close()
        tasks = list(self._tasks)
        for writer in tuple(self._clients):
            writer.close()
        for tunnel in tuple(self._tunnels.values()):
            if tunnel.upstream:
                tunnel.upstream.close()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if server:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(server.wait_closed(), 2.0)
        self._clients.clear()
        self._tunnels.clear()

    def _parse(self, data):
        if len(data) > self.HEADER_LIMIT:
            raise BrowserProxyError("invalid_request")
        lines = data[:-4].split(b"\r\n")
        if not lines or len(lines) > 65 or len(lines[0]) > 512:
            raise BrowserProxyError("invalid_request")
        if any(any(byte < 32 or byte > 126 for byte in line) for line in lines):
            raise BrowserProxyError("invalid_request")
        parts = lines[0].split(b" ")
        if len(parts) != 3 or parts[0] != b"CONNECT" or parts[2] not in {b"HTTP/1.0", b"HTTP/1.1"}:
            raise BrowserProxyError("invalid_request")
        authority = parts[1].decode("ascii")
        if authority.count(":") != 1:
            raise BrowserProxyError("invalid_request")
        host, port = authority.rsplit(":", 1)
        if port != "443":
            raise BrowserProxyError("origin_blocked")
        host = _hostname(host)
        authorization = None
        for line in lines[1:]:
            if b":" not in line:
                raise BrowserProxyError("invalid_request")
            name, value = line.split(b":", 1)
            if not re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                raise BrowserProxyError("invalid_request")
            value = value.strip(b" ")
            if name.lower() == b"proxy-authorization":
                if authorization is not None:
                    raise BrowserProxyError("invalid_request")
                authorization = value
            if name.lower() == b"transfer-encoding" or (name.lower() == b"content-length" and value != b"0"):
                raise BrowserProxyError("invalid_request")
        return host, authorization

    @staticmethod
    async def _reply(writer, status):
        phrases = {200: "Connection Established", 400: "Bad Request", 403: "Forbidden",
                   407: "Proxy Authentication Required", 408: "Request Timeout", 502: "Bad Gateway"}
        headers = "HTTP/1.1 " + str(status) + " " + phrases[status] + "\r\n"
        if status != 200:
            headers += "Content-Length: 0\r\nConnection: close\r\n"
        if status == 407:
            headers += 'Proxy-Authenticate: Basic realm="REIGN"\r\n'
        writer.write((headers + "\r\n").encode("ascii"))
        await asyncio.wait_for(writer.drain(), 2.0)

    async def _relay(self, reader, writer):
        while True:
            data = await asyncio.wait_for(reader.read(self.BUFFER_SIZE), self.IDLE_TIMEOUT)
            if not data:
                return
            writer.write(data)
            await asyncio.wait_for(writer.drain(), self.IDLE_TIMEOUT)

    async def _serve(self, reader, writer):
        task = asyncio.current_task()
        upstream = None
        relays = []
        established = False
        try:
            data = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.HEADER_TIMEOUT)
            host, authorization = self._parse(data)
            if authorization is None or not secrets.compare_digest(authorization, self._authorization):
                await self._reply(writer, 407)
                return
            origin = "https://" + host
            async with self._lock:
                if self._closed:
                    raise BrowserProxyError()
                self._allowed(origin)
                tunnel = _Tunnel(origin, writer)
                self._tunnels[task] = tunnel
            upstream_reader, upstream = await asyncio.wait_for(self._connect(host, 443), self.CONNECT_TIMEOUT)
            tunnel.upstream = upstream
            async with self._lock:
                if self._closed:
                    raise BrowserProxyError()
                self._allowed(origin)
            await self._reply(writer, 200)
            established = True
            relays = [asyncio.create_task(self._relay(reader, upstream)),
                      asyncio.create_task(self._relay(upstream_reader, writer))]
            await asyncio.wait(relays, timeout=self.TUNNEL_TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not established:
                status = 403 if isinstance(error, BrowserProxyError) and error.code == "origin_blocked" else (
                    400 if isinstance(error, (BrowserProxyError, asyncio.LimitOverrunError, asyncio.IncompleteReadError)) else
                    408 if isinstance(error, TimeoutError) else 502)
                with contextlib.suppress(Exception):
                    await self._reply(writer, status)
        finally:
            for relay in relays:
                relay.cancel()
            if relays:
                await asyncio.gather(*relays, return_exceptions=True)
            self._tunnels.pop(task, None)
            self._clients.discard(writer)
            await asyncio.gather(self._close_writer(upstream), self._close_writer(writer))
