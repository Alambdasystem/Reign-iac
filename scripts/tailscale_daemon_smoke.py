"""Exercise real Tailscale binaries offline; never authenticate or enroll a node.

Run only in the Core image with --user 10001:10001 and --network none.
All daemon state lives in a temporary directory removed on completion.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


TAILSCALE = "/usr/local/bin/tailscale"
TAILSCALED = "/usr/local/bin/tailscaled"
EXPECTED_VERSION = "1.104.7"
PREFERENCES = {
    "hostname": "render-offline-smoke",
    "accept-dns": False,
    "accept-routes": False,
    "advertise-routes": "",
    "advertise-exit-node": False,
    "exit-node": "",
    "shields-up": True,
    "ssh": False,
    "webclient": False,
}


class SmokeError(RuntimeError):
    pass


def command(arguments, environment):
    return subprocess.run(arguments, env=environment, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, timeout=3, check=False)


def unenrolled(status):
    if (status.get("BackendState") != "NeedsLogin" or status.get("HaveNodeKey", False)
            or status.get("TailscaleIPs") or status.get("AuthURL")):
        raise SmokeError("daemon must remain unauthenticated throughout the smoke")


def main():
    if os.geteuid() != 10001 or os.getegid() != 10001:
        raise SmokeError("run the Core image with --user 10001:10001")
    if sorted(os.listdir("/sys/class/net")) != ["lo"]:
        raise SmokeError("run the Core image with --network none")
    if any(os.environ.get(name) for name in (
            "REIGN_TAILSCALE_AUTHKEY", "TS_AUTHKEY", "TS_CLIENT_SECRET", "TS_ID_TOKEN")):
        raise SmokeError("enrollment credentials must not be provided to the smoke")
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="tailscale-smoke-") as directory:
        root = Path(directory)
        state = root / "state"
        state.mkdir(mode=0o700)
        socket = root / "tailscaled.sock"
        environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": directory,
                       "TMPDIR": directory}
        for binary, option in ((TAILSCALE, "version"), (TAILSCALED, "--version")):
            result = command([binary, option], environment)
            if result.returncode or result.stdout.splitlines()[:1] != [EXPECTED_VERSION]:
                raise SmokeError("Core must contain the pinned Tailscale client and daemon")
        if command(["ssh", "-V"], environment).returncode:
            raise SmokeError("Core must contain the OpenSSH client")
        cli = [TAILSCALE, "--socket=" + str(socket)]
        daemon = subprocess.Popen(
            [TAILSCALED, "--tun=userspace-networking", "--state=" + str(state / "tailscaled.state"),
             "--statedir=" + str(state), "--socket=" + str(socket), "--no-logs-no-support"],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if daemon.poll() is not None:
                    raise SmokeError("real userspace daemon exited before LocalAPI readiness")
                result = command([*cli, "status", "--json", "--peers=false"], environment)
                if result.returncode == 0:
                    status = json.loads(result.stdout)
                    if status.get("BackendState") == "NeedsLogin":
                        unenrolled(status)
                        break
                time.sleep(0.1)
            else:
                raise SmokeError("real userspace daemon did not reach NeedsLogin offline")
            flags = ["--" + name + "=" + (str(value).lower() if isinstance(value, bool) else value)
                     for name, value in PREFERENCES.items()]
            if command([*cli, "set", *flags], environment).returncode:
                raise SmokeError("real CLI rejected outbound-only client preferences")
            result = command([*cli, "get", "--json"], environment)
            if result.returncode:
                raise SmokeError("real CLI could not read applied client preferences")
            preferences = json.loads(result.stdout)
            if any(preferences.get(name) != value for name, value in PREFERENCES.items()):
                raise SmokeError("applied client preferences differ from the outbound-only policy")
            result = command([*cli, "status", "--json", "--peers=false"], environment)
            if result.returncode:
                raise SmokeError("real LocalAPI stopped responding after preference update")
            unenrolled(json.loads(result.stdout))
            print("Offline Tailscale smoke passed: v" + EXPECTED_VERSION + ", nonroot, no enrollment")
        finally:
            if daemon.poll() is None:
                daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)


if __name__ == "__main__":
    try:
        main()
    except (SmokeError, OSError, ValueError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, SmokeError) else type(error).__name__
        raise SystemExit("Offline Tailscale smoke failed: " + message) from None
