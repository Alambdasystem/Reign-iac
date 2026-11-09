import asyncio
import base64
import contextlib
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import ssl
import socket
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from playwright.async_api import async_playwright

from reign.browser_proxy import BrowserProxy, BrowserProxyError


ALLOWED = "approved.test"
OTHER = "other-approved.test"
FORBIDDEN = "forbidden.test"


class BrowserProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(os.environ, {"ALLOWED_HOSTS": ALLOWED + "," + OTHER, "BROWSER_PROXY": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.echo_clients = set()
        async def echo(reader, writer):
            self.echo_clients.add(writer)
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                self.echo_clients.discard(writer)
                writer.close()
        self.echo = await asyncio.start_server(echo, "127.0.0.1", 0)
        self.connections = []
        async def connect(host, port):
            self.connections.append((host, port))
            return await asyncio.open_connection("127.0.0.1", self.echo.sockets[0].getsockname()[1])
        self.proxy = await BrowserProxy(connect_factory=connect).start()
        self.clients = []

    async def asyncTearDown(self):
        await asyncio.wait_for(self.proxy.close(), 3)
        for writer in self.clients:
            writer.close()
        for writer in tuple(self.echo_clients):
            writer.close()
        self.echo.close()
        await self.echo.wait_closed()

    async def request(self, authority=ALLOWED + ":443", *, authenticated=True, raw=None):
        address = urlsplit(self.proxy.url)
        reader, writer = await asyncio.open_connection(address.hostname, address.port)
        self.clients.append(writer)
        settings = self.proxy.playwright_settings
        authorization = base64.b64encode((settings["username"] + ":" + settings["password"]).encode()).decode()
        if raw is None:
            raw = ("CONNECT " + authority + " HTTP/1.1\r\nHost: " + authority + "\r\n" +
                   ("Proxy-Authorization: Basic " + authorization + "\r\n" if authenticated else "") + "\r\n").encode()
        writer.write(raw)
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
        return int(response.split(b" ")[1]), reader, writer, response

    async def test_approved_tunnel_relays_opaque_bytes_and_requires_auth(self):
        denied, _, _, response = await self.request(authenticated=False)
        self.assertEqual(denied, 407)
        self.assertIn(b"Proxy-Authenticate: Basic", response)
        self.assertEqual(self.connections, [])
        status, reader, writer, _ = await self.request()
        self.assertEqual(status, 200)
        opaque = b"\x16\x03\x01\x00\x16opaque-test-TLS-bytes\x00\xff"
        writer.write(opaque)
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(opaque)), 2), opaque)
        self.assertEqual(self.connections, [(ALLOWED, 443)])

    async def test_destinations_are_checked_before_any_connection(self):
        authorities = [FORBIDDEN + ":443", ALLOWED + ":80", ALLOWED + ":444", ALLOWED + ":0443",
                       "user@" + ALLOWED + ":443", "https://" + ALLOWED + ":443",
                       "127.0.0.1:443", "[::1]:443", "2130706433:443", "0x7f000001:443",
                       "127.1:443", ALLOWED + ".:443", ALLOWED + "/path:443",
                       ALLOWED + "%00:443", ALLOWED + "\t:443"]
        for authority in authorities:
            with self.subTest(case=authorities.index(authority)):
                status, _, _, response = await self.request(authority)
                self.assertIn(status, (400, 403))
                self.assertNotIn(authority.encode(), response)
        self.assertEqual(self.connections, [])

    async def test_non_connect_and_invalid_headers_fail_closed(self):
        for raw in (b"GET https://approved.test/ HTTP/1.1\r\nHost: approved.test\r\n\r\n",
                    b"CONNECT approved.test:443 HTTP/1.1\r\n invalid: folded\r\n\r\n",
                    b"CONNECT approved.test:443 HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n",
                    b"CONNECT approved.test:443 HTTP/1.1\r\nContent-Length: 1\r\n\r\n",
                    b"CONNECT approved.test:443 HTTP/1.1\r\nProxy-Authorization: one\r\nProxy-Authorization: two\r\n\r\n"):
            self.assertEqual((await self.request(raw=raw))[0], 400)
        self.assertEqual(self.connections, [])

    async def test_origin_pin_closes_other_tunnel_and_cannot_be_widened(self):
        _, approved_reader, approved_writer, _ = await self.request()
        _, other_reader, _, _ = await self.request(OTHER + ":443")
        await self.proxy.pin_origin("https://" + ALLOWED)
        self.assertEqual(await asyncio.wait_for(other_reader.read(), 2), b"")
        approved_writer.write(b"still-approved")
        await approved_writer.drain()
        self.assertEqual(await asyncio.wait_for(approved_reader.readexactly(14), 2), b"still-approved")
        self.assertEqual((await self.request(OTHER + ":443"))[0], 403)
        with self.assertRaises(BrowserProxyError):
            await self.proxy.pin_origin("https://" + OTHER)
        with self.assertRaises(BrowserProxyError):
            await self.proxy.pin_origin("https://" + ALLOWED + "/not-an-origin")

    async def test_pin_cancels_other_origin_connect_in_flight(self):
        await self.proxy.close()
        entered = asyncio.Event()
        async def connect(host, port):
            entered.set()
            await asyncio.Event().wait()
        self.proxy = await BrowserProxy(connect_factory=connect).start()
        pending = asyncio.create_task(self.request(OTHER + ":443"))
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(self.proxy.pin_origin("https://" + ALLOWED), 2)
        with self.assertRaises(asyncio.IncompleteReadError):
            await pending
        self.assertFalse(self.proxy._tunnels)
        self.assertEqual((await self.request(OTHER + ":443"))[0], 403)

    async def test_bounded_headers_timeout_and_admission(self):
        address = urlsplit(self.proxy.url)
        reader, writer = await asyncio.open_connection(address.hostname, address.port)
        self.clients.append(writer)
        writer.write(b"CONNECT " + b"x" * (BrowserProxy.HEADER_LIMIT + 1))
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 2)
        self.assertIn(b"400 Bad Request", response)
        with patch.object(self.proxy, "HEADER_TIMEOUT", 0.03), patch.object(self.proxy, "MAX_CONNECTIONS", 1):
            first_reader, first = await asyncio.open_connection(address.hostname, address.port)
            self.clients.append(first)
            await asyncio.sleep(0)
            second_reader, second = await asyncio.open_connection(address.hostname, address.port)
            self.clients.append(second)
            self.assertEqual(await asyncio.wait_for(second_reader.read(), 2), b"")
            self.assertIn(b"408 Request Timeout", await asyncio.wait_for(first_reader.read(), 2))
        self.assertEqual(self.connections, [])

    async def test_close_releases_tunnels_and_rejects_restart(self):
        _, reader, _, _ = await self.request()
        await asyncio.wait_for(self.proxy.close(), 3)
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")
        self.assertFalse(self.proxy._tunnels)
        with self.assertRaises(BrowserProxyError):
            await self.proxy.start()

    async def test_configured_upstream_proxy_is_rejected_without_echo(self):
        with patch.dict(os.environ, {"BROWSER_PROXY": "http://private-user:private-password@unused.invalid"}):
            candidate = BrowserProxy()
            with self.assertRaises(BrowserProxyError) as caught:
                await candidate.start()
            self.assertNotIn("private", str(caught.exception))
            await candidate.close()

    async def test_private_or_mixed_dns_answers_never_open_a_socket(self):
        loop = asyncio.get_running_loop()
        for addresses in (["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"], ["224.0.0.1"],
                          ["1.1.1.1", "192.168.0.1"]):
            answers = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443)) for address in addresses]
            with patch.object(loop, "getaddrinfo", AsyncMock(return_value=answers)), patch(
                    "reign.browser_proxy.asyncio.open_connection", AsyncMock()) as connect:
                with self.assertRaises(BrowserProxyError):
                    await self.proxy._public_connect(ALLOWED, 443)
                connect.assert_not_awaited()

    async def test_public_dns_is_pinned_to_numeric_address_without_second_lookup(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 443))]
        expected = (object(), object())
        with patch.object(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=answers)) as resolve, patch(
                "reign.browser_proxy.asyncio.open_connection", AsyncMock(return_value=expected)) as connect:
            self.assertIs(await self.proxy._public_connect(ALLOWED, 443), expected)
            resolve.assert_awaited_once_with(ALLOWED, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
            connect.assert_awaited_once_with("1.1.1.1", 443, family=socket.AF_INET, flags=socket.AI_NUMERICHOST)


class BrowserProxyHTTPSRedirectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(os.environ, {"ALLOWED_HOSTS": ALLOWED + "," + OTHER, "BROWSER_PROXY": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.launch(headless=True, args=[
                "--disable-dev-shm-usage", "--disable-quic", "--disable-http2",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
        except Exception:
            await self.playwright.stop()
            if os.getenv("REIGN_REQUIRE_BROWSER_TESTS") == "1":
                raise AssertionError("Required Chromium proxy tests could not launch") from None
            self.skipTest("Chromium is not installed; CI must run with REIGN_REQUIRE_BROWSER_TESTS=1")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ALLOWED)])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(ALLOWED), x509.DNSName(OTHER), x509.DNSName(FORBIDDEN)]), critical=False)
            .sign(key, hashes.SHA256()))
        cert_file, key_file = Path(self.directory.name) / "fixture-cert.pem", Path(self.directory.name) / "fixture-key.pem"
        cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert_file, key_file)
        self.requests, self.destinations, self.tls_clients = [], [], set()
        self.forbidden_target = FORBIDDEN
        async def website(reader, writer):
            self.tls_clients.add(writer)
            try:
                while True:
                    headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
                    lines = headers.split(b"\r\n")
                    method, path, _ = lines[0].decode("ascii").split(" ")
                    fields = dict(line.split(b":", 1) for line in lines[1:] if b":" in line)
                    length = int(next((value.strip() for key, value in fields.items() if key.lower() == b"content-length"), b"0"))
                    if length:
                        await reader.readexactly(length)
                    host = next((value.strip().decode("ascii") for key, value in fields.items() if key.lower() == b"host"), "")
                    self.requests.append((host, method, path))
                    if path == "/login" and method == "POST":
                        status, extra, body = "303 See Other", "Location: /workspace\r\nSet-Cookie: fixture=accepted; Secure; HttpOnly; Path=/\r\n", b""
                    elif path == "/send" and method == "POST":
                        status, extra, body = "307 Temporary Redirect", "Location: https://" + self.forbidden_target + "/collect\r\n", b""
                    elif path == "/workspace":
                        self.assertTrue(any(key.lower() == b"cookie" and b"fixture=accepted" in value for key, value in fields.items()))
                        status, extra, body = "200 OK", "", b"<title>Controlled workspace</title>Login accepted"
                    elif path == "/collect":
                        status, extra, body = "200 OK", "", b"unexpected destination"
                    else:
                        status, extra, body = "200 OK", "", b'<title>Controlled login</title><form method="post" action="/login"><input name="username"><input type="password" name="password"><button>Sign in</button></form><form method="post" action="/send"><input name="test" value="synthetic-only"><button>Send fixture</button></form>'
                    writer.write(("HTTP/1.1 " + status + "\r\nContent-Type: text/html\r\nContent-Length: " + str(len(body)) + "\r\n" + extra + "\r\n").encode("ascii") + body)
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError, TimeoutError, asyncio.CancelledError):
                pass
            finally:
                self.tls_clients.discard(writer)
                writer.close()
        self.server = await asyncio.start_server(website, "127.0.0.1", 0, ssl=tls)
        async def connect(host, port):
            # The forbidden mapping exists solely to prove policy stops it
            # before the target receives the redirected synthetic POST.
            self.destinations.append((host, port))
            return await asyncio.open_connection("127.0.0.1", self.server.sockets[0].getsockname()[1])
        self.proxy = await BrowserProxy(connect_factory=connect).start()
        self.context = await self.browser.new_context(proxy=self.proxy.playwright_settings,
            ignore_https_errors=True, service_workers="block")
        self.page = await self.context.new_page()

    async def asyncTearDown(self):
        if hasattr(self, "context"):
            await self.context.close()
        if hasattr(self, "proxy"):
            await self.proxy.close()
        if hasattr(self, "server"):
            for writer in tuple(self.tls_clients):
                writer.close()
            self.server.close()
            await self.server.wait_closed()
        if hasattr(self, "browser"):
            await self.browser.close()
        if hasattr(self, "playwright"):
            await self.playwright.stop()

    async def test_native_https_303_preserves_cookie_and_real_page_url(self):
        await self.page.goto("https://" + ALLOWED + "/")
        await self.page.locator('[name="username"]').fill("synthetic-user")
        await self.page.locator('[name="password"]').fill("synthetic-password")
        await self.page.get_by_role("button", name="Sign in", exact=True).click()
        await self.page.wait_for_url("https://" + ALLOWED + "/workspace")
        self.assertEqual(await self.page.title(), "Controlled workspace")
        self.assertIn((ALLOWED, "POST", "/login"), self.requests)
        self.assertIn((ALLOWED, "GET", "/workspace"), self.requests)
        self.assertTrue(all(host == ALLOWED and port == 443 for host, port in self.destinations))

    async def _blocked_redirect(self, target):
        self.forbidden_target = target
        await self.page.goto("https://" + ALLOWED + "/")
        await self.proxy.pin_origin("https://" + ALLOWED)
        failed = asyncio.Event()
        self.page.on("requestfailed", lambda request: failed.set() if urlsplit(request.url).hostname == target else None)
        await self.page.get_by_role("button", name="Send fixture", exact=True).click(no_wait_after=True)
        await asyncio.wait_for(failed.wait(), 10)
        self.assertIn((ALLOWED, "POST", "/send"), self.requests)
        self.assertFalse(any(path == "/collect" for host, method, path in self.requests), "Forbidden destination received a redirected request")
        self.assertFalse(any(host == target for host, port in self.destinations), "Forbidden destination was connected before rejection")

    async def test_forbidden_307_is_blocked_before_target_receives_post(self):
        await self._blocked_redirect(FORBIDDEN)

    async def test_credential_pin_blocks_307_to_another_globally_allowed_host(self):
        await self._blocked_redirect(OTHER)


if __name__ == "__main__":
    unittest.main()
