#!/usr/bin/env python3
"""Fetch and verify public portrait assets during image build, never at startup."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "services/ax/portrait-assets.json"
DEFAULT_OUTPUT = ROOT / "services/ax/avatar-assets"
CHUNK_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 128 * 1024 * 1024
MAX_ATTEMPTS = 3
NETWORK_TIMEOUT_SECONDS = 30
DOWNLOAD_DEADLINE_SECONDS = 300


class IntegrityError(ValueError):
    """The downloaded bytes do not match the pinned manifest."""


def destination_for(output, relative):
    """Reject traversal, ambiguous paths, and symlinks escaping the output root."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("Asset path must be a relative POSIX path")
    path = PurePosixPath(relative)
    if (path.is_absolute() or path.as_posix() != relative
            or any(part in (".", "..") or ":" in part for part in path.parts)):
        raise ValueError(f"Unsafe asset path: {relative}")
    root = Path(output).resolve()
    destination = root.joinpath(*path.parts)
    if not destination.resolve().is_relative_to(root) or destination.resolve() == root:
        raise ValueError(f"Asset path escapes output directory: {relative}")
    return destination


def validate_asset(asset):
    if not isinstance(asset, dict):
        raise ValueError("Each asset must be an object")
    destination_for(Path.cwd(), asset.get("path"))
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= MAX_ASSET_BYTES:
        raise ValueError("Asset size must be a positive integer below the download limit")
    if not isinstance(asset.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]):
        raise ValueError("Asset SHA-256 must contain 64 lowercase hexadecimal characters")
    url = urlsplit(asset.get("url", ""))
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("Asset URL must be public HTTPS without credentials or a fragment")


def load_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("Unsupported portrait asset manifest version")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not 1 <= len(assets) <= 16:
        raise ValueError("Manifest must contain between 1 and 16 assets")
    seen = set()
    for asset in assets:
        validate_asset(asset)
        if asset["path"] in seen:
            raise ValueError(f"Duplicate asset path: {asset['path']}")
        seen.add(asset["path"])
    return assets


def matches_asset(path, asset):
    if not path.is_file() or path.stat().st_size != asset["size"]:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest() == asset["sha256"]


def download_asset(asset, output):
    """Stream bounded bytes to a temporary file, then atomically publish on success."""
    validate_asset(asset)
    destination = destination_for(output, asset["path"])
    if matches_asset(destination, asset):
        return "verified"
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(MAX_ATTEMPTS):
        temporary = None
        try:
            request = Request(asset["url"], headers={"User-Agent": "REIGN-portrait-assets/1"})
            deadline = time.monotonic() + DOWNLOAD_DEADLINE_SECONDS
            with urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
                if urlsplit(response.geturl()).scheme != "https":
                    raise ValueError("Asset redirect did not preserve HTTPS")
                with tempfile.NamedTemporaryFile(dir=destination.parent,
                                                 prefix=f".{destination.name}.",
                                                 suffix=".download", delete=False) as target:
                    temporary = Path(target.name)
                    digest = hashlib.sha256()
                    received = 0
                    while True:
                        if time.monotonic() > deadline:
                            raise TimeoutError("Asset download exceeded its time limit")
                        # Read one extra byte at the size boundary to reject oversized bodies.
                        chunk = response.read(min(CHUNK_BYTES, asset["size"] - received + 1))
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > asset["size"]:
                            raise IntegrityError(f"Size mismatch for {asset['path']}")
                        digest.update(chunk)
                        target.write(chunk)
                    if received != asset["size"]:
                        raise IntegrityError(f"Size mismatch for {asset['path']}")
                    if digest.hexdigest() != asset["sha256"]:
                        raise IntegrityError(f"SHA-256 mismatch for {asset['path']}")
                    target.flush()
                    os.fsync(target.fileno())
            temporary.chmod(0o644)
            os.replace(temporary, destination)
            return "downloaded"
        except (URLError, OSError) as error:
            if attempt + 1 == MAX_ATTEMPTS:
                raise RuntimeError(f"Could not download {asset['path']} after {MAX_ATTEMPTS} attempts") from error
            time.sleep(1 + attempt)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    raise RuntimeError("Asset download did not complete")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        assets = load_manifest(args.manifest)
        # Validate every destination before downloading or writing any asset.
        for asset in assets:
            destination_for(args.output, asset["path"])
        for asset in assets:
            print(f"{download_asset(asset, args.output)} {asset['path']}", flush=True)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Portrait asset build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
