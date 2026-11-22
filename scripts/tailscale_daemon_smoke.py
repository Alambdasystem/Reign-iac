"""Exercise real Tailscale binaries offline; never authenticate or enroll a node.

Run only in the Core image with --user 10001:10001 --group-add 1000,
PYTHONPATH=/app and --network none.
All daemon state lives in a temporary directory removed on completion.
"""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

from reign import tailscale_runtime as runtime


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


def unenrolled(status, states):
    if (status.get("BackendState") not in states or status.get("HaveNodeKey", False)
            or status.get("TailscaleIPs") or status.get("AuthURL")):
        raise SmokeError("daemon must remain unauthenticated throughout the smoke")


def check_daemon(instance, first_start):
    environment = instance.environment
    cli = [TAILSCALE, "--socket=" + instance.socket]
    states = {"NeedsLogin"} if first_start else {"NeedsLogin", "Stopped"}
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if instance.daemon.poll() is not None:
            raise SmokeError("real userspace daemon exited before LocalAPI readiness")
        result = command([*cli, "status", "--json", "--peers=false"], environment)
        if result.returncode == 0:
            status = json.loads(result.stdout)
            if status.get("BackendState") in states:
                unenrolled(status, states)
                break
        time.sleep(0.1)
    else:
        raise SmokeError("real userspace daemon did not become ready offline")
    # Production reapplies this policy on every start. An unauthenticated profile
    # does not establish enrolled-identity or preference persistence.
    flags = ["--" + name + "=" + (str(value).lower() if isinstance(value, bool) else value)
             for name, value in PREFERENCES.items()]
    if command([*cli, "set", *flags], environment).returncode:
        raise SmokeError("real CLI rejected outbound-only client preferences")
    result = command([*cli, "get", "--json"], environment)
    if result.returncode:
        raise SmokeError("real CLI could not read client preferences")
    preferences = json.loads(result.stdout)
    mismatches = [name for name, value in PREFERENCES.items() if preferences.get(name) != value]
    if mismatches:
        raise SmokeError("client policy differs at startup " + ("1" if first_start else "2")
                         + ": " + ",".join(mismatches))
    result = command([*cli, "status", "--json", "--peers=false"], environment)
    if result.returncode:
        raise SmokeError("real LocalAPI stopped responding after preference check")
    unenrolled(json.loads(result.stdout), states)


def main():
    if os.geteuid() != 10001 or os.getegid() != 10001 or 1000 not in os.getgroups():
        raise SmokeError("run with --user 10001:10001 --group-add 1000")
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
        parent = root / "setgid-parent"
        parent.mkdir(mode=0o700)
        os.chown(parent, -1, 1000)
        parent.chmod(0o2700)
        runtime_path = parent / "runtime"
        runtime.RUNTIME_DIR = str(runtime_path)
        # Reproduce the observed inherited group/mode without changing an existing object.
        runtime_path.mkdir(mode=0o700)
        before = runtime_path.stat()
        if before.st_gid != 1000 or stat.S_IMODE(before.st_mode) != 0o2700:
            raise SmokeError("the real filesystem did not reproduce setgid inheritance")
        try:
            runtime._runtime_directory()
        except runtime.TailscaleError:
            pass
        else:
            raise SmokeError("an existing runtime directory with the wrong group was accepted")
        after = runtime_path.stat()
        if (after.st_uid, after.st_gid, after.st_mode) != (before.st_uid, before.st_gid, before.st_mode):
            raise SmokeError("an existing unsafe runtime directory was modified")
        runtime_path.rmdir()  # Remove only the empty directory created by this fixture.

        for attempt in range(2):
            runtime._runtime_directory()
            info = runtime_path.stat()
            if ((info.st_uid, info.st_gid) != (10001, 10001)
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise SmokeError("new runtime directory did not clear inherited group and setgid")
            instance = runtime._Runtime(str(state), "render-offline-smoke", "")
            environment = instance.environment
            if environment.get("TS_LOGS_DIR") != str(state):
                raise SmokeError("daemon logs must use the validated private state directory")
            if attempt == 0:
                for binary, option in ((TAILSCALE, "version"), (TAILSCALED, "--version")):
                    result = command([binary, option], environment)
                    if result.returncode or result.stdout.splitlines()[:1] != [EXPECTED_VERSION]:
                        raise SmokeError("Core must contain the pinned Tailscale client and daemon")
                if command(["ssh", "-V"], environment).returncode:
                    raise SmokeError("Core must contain the OpenSSH client")
            # Use production logging behavior; network isolation prevents any log upload.
            instance.daemon = subprocess.Popen(
                [TAILSCALED, "--tun=userspace-networking", "--state=" + str(state / "tailscaled.state"),
                 "--statedir=" + str(state), "--socket=" + instance.socket],
                env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            try:
                check_daemon(instance, first_start=attempt == 0)
            finally:
                instance.close()
            if runtime_path.exists():
                raise SmokeError("daemon left unexpected files in the ephemeral runtime directory")
            descriptor = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                runtime._check_tree(descriptor)
            finally:
                os.close(descriptor)
            if not (state / "tailscaled.log.conf").is_file() or not list(state.glob("tailscaled.log*.txt")):
                raise SmokeError("real daemon did not write logs into private persistent storage")
        print("Offline Tailscale smoke passed: v" + EXPECTED_VERSION
              + ", nonroot SGID directory, private logs, two daemon starts, no enrollment")


if __name__ == "__main__":
    try:
        main()
    except (SmokeError, runtime.TailscaleError, OSError, ValueError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, SmokeError) else type(error).__name__
        raise SystemExit("Offline Tailscale smoke failed: " + message) from None
