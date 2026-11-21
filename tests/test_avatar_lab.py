"""Isolated REIGN Avatar Lab smoke and security checks (no production effects)."""
import os
import re
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from reign.ax import app

ASSET = Path(__file__).resolve().parents[1] / "services" / "ax" / "avatar-lab.html"


class AvatarLabTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "AX_ADMIN_PASSWORD": "p" * 40,
            "AX_SESSION_KEY": "s" * 40,
            "CORE_TOKEN": "c" * 40,
            "AX_PUBLIC_ORIGIN": "https://ax.example.com",
        }

    def test_route_requires_real_ax_session(self):
        with patch.dict(os.environ, self.settings):
            with TestClient(app, base_url="https://ax.example.com",
                            follow_redirects=False) as client:
                unsigned = client.get("/avatar-lab")
                self.assertEqual(unsigned.status_code, 303)
                self.assertEqual(unsigned.headers.get("location"), "/")
                self.assertEqual(unsigned.headers.get("cache-control"), "no-store")
                self.assertNotIn("Cat morph", unsigned.text)
                self.assertEqual(client.post("/login", json={
                    "password": self.settings["AX_ADMIN_PASSWORD"]}).status_code, 200)
                page = client.get("/avatar-lab")
                self.assertEqual(page.status_code, 200)
                self.assertIn("UltraView Avatar Lab", page.text)
                self.assertIn("Cat morph", page.text)
                self.assertIn('id="yaw"', page.text)
                self.assertEqual(page.headers["cache-control"], "no-store")
                self.assertEqual(page.headers["x-content-type-options"], "nosniff")
                self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
                self.assertIn("form-action 'none'", page.headers["content-security-policy"])
                self.assertNotIn("AX_ADMIN_PASSWORD", page.text)
                self.assertNotIn("CORE_TOKEN", page.text)
                self.assertNotIn("/api/chat", page.text)

    def test_avatar_lab_source_contains_expected_controls_and_fallback(self):
        content = ASSET.read_text(encoding="utf-8")
        self.assertIn("import * as THREE", content)
        self.assertIn("three@0.169.0", content)
        self.assertIn("max=\"45\"", content)
        self.assertIn("min=\"-45\"", content)
        self.assertIn("window.__avatar3DReady=true", content)
        self.assertIn("fallbackCanvas", content)
        self.assertIn("speechSynthesis", content)
        self.assertIn("Not photorealistic", content)

    def test_inline_scripts_parse_in_node(self):
        content = ASSET.read_text(encoding="utf-8")
        scripts = re.findall(r"<script(?:\s+type=\"module\")?>(.*?)</script>",
                             content, re.DOTALL)
        self.assertEqual(len(scripts), 2)
        for script in scripts:
            result = subprocess.run(["node", "--check", "--input-type=module"],
                                    input=script, text=True, capture_output=True,
                                    timeout=15)
            self.assertEqual(result.returncode, 0,
                             "Invalid Avatar Lab JavaScript: " + result.stderr[:500])


if __name__ == "__main__":
    unittest.main()
