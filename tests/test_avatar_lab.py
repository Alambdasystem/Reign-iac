"""Portrait Lab routing/security contracts; these fixtures do not test inference."""
import os
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from reign.ax import AXStaticFiles, app

AX_SOURCE = Path(__file__).resolve().parents[1] / "services" / "ax"


class FrontendReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.stylesheets = []
        self.inline_scripts = 0

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "script":
            if attrs.get("src"):
                self.scripts.append(attrs["src"])
            else:
                self.inline_scripts += 1
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.stylesheets.append(attrs.get("href", ""))


class AvatarLabTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "AX_ADMIN_PASSWORD": "p" * 40,
            "AX_SESSION_KEY": "s" * 40,
            "CORE_TOKEN": "c" * 40,
            "AX_PUBLIC_ORIGIN": "https://ax.example.com",
        }

    def test_lab_requires_ax_session_and_keeps_destination(self):
        with patch.dict(os.environ, self.settings):
            with TestClient(app, base_url="https://ax.example.com",
                            follow_redirects=False) as client:
                for cookie in (None, "invalid-session"):
                    if cookie:
                        client.cookies.set("ax_session", cookie)
                    unsigned = client.get("/avatar-lab")
                    self.assertEqual(unsigned.status_code, 303)
                    self.assertEqual(unsigned.headers["location"],
                                     "/?next=%2Favatar-lab")
                    self.assertEqual(unsigned.headers["cache-control"], "no-store")
                    self.assertNotIn("<script", unsigned.text)
                client.cookies.clear()
                login = client.post("/login", json={
                    "password": self.settings["AX_ADMIN_PASSWORD"]})
                self.assertEqual(login.status_code, 200)
                session_cookie = next(value for value in login.headers.get_list("set-cookie")
                                      if value.startswith("ax_session="))
                self.assertIn("HttpOnly", session_cookie)
                self.assertIn("Secure", session_cookie)
                self.assertIn("SameSite=strict", session_cookie)
                page = client.get("/avatar-lab")
                self.assertEqual(page.status_code, 200)
                self.assertEqual(page.headers["cache-control"], "no-store")
                self.assertEqual(page.headers["x-content-type-options"], "nosniff")
                self.assertEqual(page.headers["referrer-policy"], "no-referrer")
                csp = page.headers["content-security-policy"]
                for directive in ("script-src 'self' 'wasm-unsafe-eval'",
                                  "style-src 'self'", "connect-src 'self'",
                                  "worker-src 'self'", "frame-ancestors 'none'",
                                  "base-uri 'none'", "form-action 'none'"):
                    self.assertIn(directive, csp)
                self.assertNotIn("'unsafe-eval'", csp)
                self.assertNotIn("'unsafe-inline'", csp)
                self.assertNotIn("https://", csp)
                self.assertNotIn("AX_ADMIN_PASSWORD", page.text)
                self.assertNotIn("CORE_TOKEN", page.text)
                self.assertNotIn("/api/chat", page.text)

    def test_document_is_never_available_through_public_static_paths(self):
        with TestClient(app, base_url="https://ax.example.com") as client:
            for path in ("/assets/avatar-lab.html",
                         "/assets/%61vatar-lab.html",
                         "/assets/%2e/avatar-lab.html",
                         "/assets/avatar-assets/models/distilled.onnx"):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 404)
            self.assertEqual(client.get("/assets/live.js").status_code, 200)

    def test_static_aliases_cannot_bypass_document_protection_and_worker_has_csp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "avatar-lab.html").write_text("private-lab-fixture")
            (root / "alias.html").symlink_to(root / "avatar-lab.html")
            (root / "portrait-engine.js").write_text("export const fixture = true;")
            static_app = FastAPI()
            static_app.mount("/assets", AXStaticFiles(directory=root))
            with TestClient(static_app) as client:
                self.assertEqual(client.get("/assets/alias.html").status_code, 404)
                worker = client.get("/assets/portrait-engine.js")
                self.assertEqual(worker.status_code, 200)
                self.assertIn("connect-src 'self'",
                              worker.headers["content-security-policy"])
                self.assertIn("'wasm-unsafe-eval'",
                              worker.headers["content-security-policy"])

    def test_public_asset_route_only_serves_bundled_names_and_correct_mime_types(self):
        files = {
            "models/distilled.onnx": "application/octet-stream",
            "models/appearance.onnx": "application/octet-stream",
            "ort/ort.webgpu.min.mjs": "text/javascript",
            "ort/ort-wasm-simd-threaded.jsep.mjs": "text/javascript",
            "ort/ort-wasm-simd-threaded.jsep.wasm": "application/wasm",
            "sample-portrait.png": "image/png",
            "THIRD_PARTY_NOTICES.txt": "text/plain",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"bundled-public-asset-fixture")
            with patch("reign.ax.AVATAR_ASSETS", root):
                with TestClient(app) as client:
                    for name, mime in files.items():
                        with self.subTest(name=name):
                            response = client.get("/avatar-assets/" + name)
                            self.assertEqual(response.status_code, 200)
                            self.assertTrue(response.headers["content-type"].startswith(mime))
                            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
                            self.assertEqual(response.headers["cross-origin-resource-policy"],
                                             "same-origin")
                    part = client.get("/avatar-assets/models/distilled.onnx",
                                      headers={"Range": "bytes=0-6"})
                    self.assertEqual(part.status_code, 206)
                    self.assertEqual(part.content, b"bundled")
                    self.assertEqual(client.get("/avatar-assets/sample-portrait.jpg").status_code,
                                     404)

    def test_asset_route_rejects_traversal_unknown_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "public"
            (root / "models").mkdir(parents=True)
            (root / "ort").mkdir()
            (root / "private.json").write_text("private-fixture")
            (root / "ort" / "not-runtime.mjs").write_text("private-fixture")
            outside = Path(directory) / "private.onnx"
            outside.write_text("outside-private-fixture")
            (root / "models" / "distilled.onnx").symlink_to(outside)
            with patch("reign.ax.AVATAR_ASSETS", root):
                with TestClient(app) as client:
                    for path in ("private.json", "ort/not-runtime.mjs",
                                 "models/other.onnx", "models/distilled.onnx",
                                 "%2e%2e/private.onnx", "models/%2e%2e/private.json",
                                 "ort/%2e%2e/%2e%2e/private.onnx",
                                 "ort/ort.private.env", "ort/subdir/ort.mjs",
                                 "models%5c..%5cprivate.onnx"):
                        with self.subTest(path=path):
                            response = client.get("/avatar-assets/" + path)
                            self.assertEqual(response.status_code, 404)
                            self.assertNotIn("private-fixture", response.text)

    def test_frontend_references_local_packaged_scripts_and_styles(self):
        references = FrontendReferences()
        references.feed((AX_SOURCE / "avatar-lab.html").read_text(encoding="utf-8"))
        self.assertTrue(references.scripts)
        self.assertTrue(references.stylesheets)
        self.assertEqual(references.inline_scripts, 0)
        for source in references.scripts + references.stylesheets:
            with self.subTest(source=source):
                url = urlsplit(source)
                self.assertFalse(url.netloc or url.scheme)
                self.assertTrue(url.path.startswith("/assets/"))
                path = (AX_SOURCE / url.path.removeprefix("/assets/")).resolve()
                self.assertTrue(path.is_relative_to(AX_SOURCE))
                self.assertTrue(path.is_file(), str(path))
        index = (AX_SOURCE / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/avatar-lab"', index)

    def test_avatar_modules_and_login_controller_parse_in_node(self):
        for filename in ("avatar-lab.js", "portrait-engine.js", "live.js"):
            source = (AX_SOURCE / filename).read_text(encoding="utf-8")
            result = subprocess.run(["node", "--check", "--input-type=module"],
                                    input=source, text=True, capture_output=True,
                                    timeout=15)
            self.assertEqual(result.returncode, 0,
                             filename + ": " + result.stderr[:500])

    def test_portrait_ui_lifecycle_regressions(self):
        test_file = Path(__file__).with_name("avatar_lab_lifecycle.test.js")
        result = subprocess.run(["node", "--test", str(test_file)], text=True,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-6000:])


if __name__ == "__main__":
    unittest.main()
