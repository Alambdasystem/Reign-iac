"""Connections menu is present across REIGN UI surfaces without inventing routes."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "services" / "ax"


class ConnectionsMenuTests(unittest.TestCase):
    def test_consistent_navigation_and_external_link_safety(self):
        for name in ("index.html", "genesis.html", "nodes.html"):
            with self.subTest(name=name):
                markup = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn('aria-label="Alambda application connections"', markup)
                self.assertIn("/assets/connections.css", markup)
                self.assertIn("https://reign-ax-pilot-d934.onrender.com/", markup)
                self.assertIn("https://anthesis-v1-vtqh.onrender.com/", markup)
                self.assertIn("https://alambda.systems/", markup)
                self.assertIn('rel="noopener noreferrer"', markup)
                self.assertIn('aria-disabled="true">LPR', markup)
                self.assertNotIn("href=\"#lpr\"", markup)

    def test_menu_has_no_secrets_or_session_bridge(self):
        css = (ROOT / "connections.css").read_text(encoding="utf-8")
        self.assertIn("focus-visible", css)
        for filename in ("index.html", "genesis.html", "nodes.html"):
            markup = (ROOT / filename).read_text(encoding="utf-8")
            self.assertNotIn("workmates-api.onrender.com", markup)
            self.assertNotIn("workmates-core.onrender.com", markup)
