"""Offline build integrity checks; these fixtures do not test model inference."""

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from scripts import fetch_portrait_assets as fetcher


class Response(io.BytesIO):
    def geturl(self):
        return "https://assets.example.test/model.onnx"


class PortraitAssetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "assets"
        self.content = b"verified-model-fixture"
        self.asset = {
            "path": "models/model.onnx",
            "url": "https://assets.example.test/model.onnx",
            "size": len(self.content),
            "sha256": hashlib.sha256(self.content).hexdigest(),
        }

    def test_valid_download_is_readable_and_then_skips_network(self):
        with patch.object(fetcher, "urlopen", return_value=Response(self.content)) as request:
            self.assertEqual(fetcher.download_asset(self.asset, self.output), "downloaded")
            self.assertEqual(fetcher.download_asset(self.asset, self.output), "verified")
        request.assert_called_once()
        destination = self.output / self.asset["path"]
        self.assertEqual(destination.read_bytes(), self.content)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o644)
        self.assertEqual(list(self.output.rglob("*.download")), [])

    def test_hash_and_short_or_long_size_fail_without_publishing(self):
        for body in (b"x" * len(self.content), self.content[:-1], self.content + b"extra"):
            with self.subTest(body=body):
                with patch.object(fetcher, "urlopen", return_value=Response(body)):
                    with self.assertRaises(fetcher.IntegrityError):
                        fetcher.download_asset(self.asset, self.output)
                self.assertFalse((self.output / self.asset["path"]).exists())
                self.assertEqual(list(self.output.rglob("*.download")), [])

    def test_bad_download_preserves_existing_file(self):
        destination = self.output / self.asset["path"]
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"previous-version")
        with patch.object(fetcher, "urlopen", return_value=Response(b"bad")):
            with self.assertRaises(fetcher.IntegrityError):
                fetcher.download_asset(self.asset, self.output)
        self.assertEqual(destination.read_bytes(), b"previous-version")

    def test_retries_transient_network_failure_but_is_bounded(self):
        with patch.object(fetcher, "time") as clock:
            clock.monotonic.return_value = 0
            with patch.object(fetcher, "urlopen", side_effect=[URLError("temporary"), Response(self.content)]) as request:
                self.assertEqual(fetcher.download_asset(self.asset, self.output), "downloaded")
            self.assertEqual(request.call_count, 2)
        (self.output / self.asset["path"]).unlink()
        with patch.object(fetcher, "time") as clock:
            clock.monotonic.return_value = 0
            with patch.object(fetcher, "urlopen", side_effect=URLError("offline")) as request:
                with self.assertRaises(RuntimeError):
                    fetcher.download_asset(self.asset, self.output)
            self.assertEqual(request.call_count, fetcher.MAX_ATTEMPTS)

    def test_traversal_and_symlink_escape_are_rejected_before_network(self):
        with patch.object(fetcher, "urlopen") as request:
            for relative in ("../outside", "/outside", "models/../../outside", "models/../outside",
                             "models\\outside", "C:/outside", ".", "models//file", "./file"):
                with self.subTest(path=relative):
                    with self.assertRaises(ValueError):
                        fetcher.download_asset({**self.asset, "path": relative}, self.output)
            self.output.mkdir()
            (self.output / "models").symlink_to(Path(self.directory.name), target_is_directory=True)
            with self.assertRaises(ValueError):
                fetcher.download_asset(self.asset, self.output)
        request.assert_not_called()

    def test_manifest_is_pinned_and_cli_returns_failure_for_bad_bytes(self):
        assets = fetcher.load_manifest(fetcher.DEFAULT_MANIFEST)
        self.assertEqual(len(assets), 5)
        for asset in assets:
            self.assertNotIn("/resolve/main/", asset["url"])
            if asset["path"].startswith("ort/"):
                self.assertIn("@1.20.1/", asset["url"])
        manifest = Path(self.directory.name) / "manifest.json"
        manifest.write_text(json.dumps({"version": 1, "assets": [self.asset]}))
        with patch.object(fetcher, "urlopen", return_value=Response(b"bad")):
            with patch("sys.stderr", new_callable=io.StringIO) as error:
                self.assertEqual(fetcher.main(["--manifest", str(manifest), "--output", str(self.output)]), 1)
        self.assertIn("Size mismatch", error.getvalue())


if __name__ == "__main__":
    unittest.main()
