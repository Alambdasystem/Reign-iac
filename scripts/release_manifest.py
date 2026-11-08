"""Record a disabled legacy manifest and tested image provenance for Blueprint promotion."""
import json
import re
import subprocess
import sys
from pathlib import Path

SERVICES = ("core", "ax", "computer", "temporal")


def manifest(owner, sha, smoke, inspect):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", owner):
        raise ValueError("Invalid registry owner")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid source commit")
    if smoke.get("status") != "passed" or smoke.get("source_sha") != sha:
        raise ValueError("Complete acceptance evidence must match the source commit")
    images = {}
    for service in SERVICES:
        repo = f"ghcr.io/{owner}/reign-{service}"
        digests = inspect(f"{repo}:{sha}")
        matching = [value.split("@", 1)[1] for value in digests
                    if value.startswith(repo + "@")]
        if len(matching) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", matching[0]):
            raise ValueError("Missing unique registry digest for " + service)
        images[service] = {"image_url": repo, "digest": matching[0]}
    return {"provision": False, "images": images}


def inspect_image(image):
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image],
        check=True, text=True, capture_output=True)
    return json.loads(result.stdout) or []


if __name__ == "__main__":
    owner, sha = sys.argv[1:]
    smoke = json.loads(Path("artifacts/smoke/smoke.json").read_text())
    release = manifest(owner, sha, smoke, inspect_image)
    output = Path("artifacts/release")
    output.mkdir(parents=True, exist_ok=True)
    (output / "release.json").write_text(json.dumps(release, indent=2) + "\n")
    (output / "provenance.json").write_text(json.dumps({
        "source_sha": sha, "acceptance": smoke,
        "promotion": "Promote reviewed digests through the existing Render Blueprint with autoSync=false; never apply the legacy OpenTofu manifest."
    }, indent=2) + "\n")
    print(json.dumps(release))
