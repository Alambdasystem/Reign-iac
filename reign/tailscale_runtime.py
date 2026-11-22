"""Optional outbound-only Tailscale client for the dedicated Core container.

The existing mounted vault directory is the only permitted persistent store.
No shell, proxy listener, subnet router, exit node or public endpoint is added.
This module runs after core_entrypoint has dropped to the reign account.
"""

from contextlib import contextmanager
import fcntl
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time


VAULT_MOUNT = "/var/data/reign-vault"
RUNTIME_DIR = "/tmp/reign-tailscale"
TAILSCALED = "/usr/local/bin/tailscaled"
TAILSCALE = "/usr/local/bin/tailscale"
AUTH_ENV = "REIGN_TAILSCALE_AUTHKEY"
STARTUP_TIMEOUT = 45.0
COMMAND_TIMEOUT = 5.0
SHUTDOWN_TIMEOUT = 5.0
POLL_INTERVAL = 0.1


class TailscaleError(RuntimeError):
    """A fixed, safe diagnostic; never include child output or credentials."""


class _Interrupted(Exception):
    pass


def enabled():
    value = os.environ.get("REIGN_TAILSCALE_ENABLED", "0").lower()
    if value not in {"0", "1", "false", "true"}:
        raise TailscaleError("REIGN_TAILSCALE_ENABLED must be 0 or 1")
    return value in {"1", "true"}


def _private_object(info, directory=False):
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (not correct_type or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid() or stat.S_IMODE(info.st_mode) & 0o077
            or (not directory and info.st_nlink != 1)):
        raise TailscaleError(
            "Tailscale storage has unsafe ownership, permissions or type "
            f"(uid={info.st_uid} gid={info.st_gid} mode={stat.S_IMODE(info.st_mode):04o} "
            f"type={stat.S_IFMT(info.st_mode):06o} links={info.st_nlink})")


def _check_tree(directory_fd, depth=0):
    if depth > 8:
        raise TailscaleError("Tailscale storage has an unexpected directory structure")
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            is_directory = stat.S_ISDIR(info.st_mode)
            _private_object(info, directory=is_directory)
            if is_directory:
                child_fd = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=directory_fd)
                try:
                    _private_object(os.fstat(child_fd), directory=True)
                    _check_tree(child_fd, depth + 1)
                finally:
                    os.close(child_fd)


@contextmanager
def _state_lock():
    """Reject fallback paths and existing unsafe files; never repair their mode."""
    if (os.environ.get("REIGN_VAULT_DIR") != VAULT_MOUNT
            or os.path.realpath(VAULT_MOUNT) != VAULT_MOUNT
            or not os.path.ismount(VAULT_MOUNT)):
        raise TailscaleError("Tailscale requires the dedicated mounted Core disk")
    mount_fd = os.open(VAULT_MOUNT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    state_fd = lock_fd = None
    try:
        _private_object(os.fstat(mount_fd), directory=True)
        try:
            os.mkdir("tailscale", 0o700, dir_fd=mount_fd)
        except FileExistsError:
            pass
        state_fd = os.open("tailscale", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                           dir_fd=mount_fd)
        _private_object(os.fstat(state_fd), directory=True)
        lock_fd = os.open("runtime.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                          0o600, dir_fd=state_fd)
        _private_object(os.fstat(lock_fd))
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TailscaleError("A Tailscale runtime already owns this Core state") from None
        _check_tree(state_fd)
        yield os.path.join(VAULT_MOUNT, "tailscale")
    finally:
        for descriptor in (lock_fd, state_fd, mount_fd):
            if descriptor is not None:
                os.close(descriptor)


def _runtime_directory():
    created = False
    try:
        os.mkdir(RUNTIME_DIR, 0o700)
        created = True
    except FileExistsError:
        pass
    if os.path.realpath(RUNTIME_DIR) != RUNTIME_DIR:
        raise TailscaleError("Tailscale runtime directory must not be a symlink")
    descriptor = os.open(RUNTIME_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if created:
            # A setgid /tmp can assign its group to a newly created directory.
            # Normalize only our own new directory, never an existing object.
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                raise TailscaleError("New Tailscale runtime directory has unsafe ownership or type")
            os.fchown(descriptor, -1, os.getegid())
            os.fchmod(descriptor, 0o700)
        _private_object(os.fstat(descriptor), directory=True)
        # Only a socket from a previous terminated container process is reusable.
        # The persistent state lock has already excluded a concurrent supervisor.
        with os.scandir(descriptor) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if (entry.name != "tailscaled.sock" or not stat.S_ISSOCK(info.st_mode)
                        or info.st_uid != os.geteuid() or info.st_gid != os.getegid()):
                    raise TailscaleError("Tailscale runtime directory contains unexpected objects")
                os.unlink(entry.name, dir_fd=descriptor)
    finally:
        os.close(descriptor)


def _return_code(code):
    return code if code >= 0 else 128 - code


class _Runtime:
    def __init__(self, state_dir, hostname, tags):
        self.state_dir = state_dir
        self.socket = os.path.join(RUNTIME_DIR, "tailscaled.sock")
        self.hostname = hostname
        self.tags = tags
        self.daemon = None
        self.app = None
        self.cli = None
        self.stop_signal = None
        # Tailscale does not inherit Core's provider, vault or enrollment secrets.
        self.environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": RUNTIME_DIR,
            "TMPDIR": RUNTIME_DIR, "TS_LOGS_DIR": self.state_dir,
        }
        for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                     "http_proxy", "https_proxy", "no_proxy"):
            if name in os.environ:
                self.environment[name] = os.environ[name]

    def _children(self):
        return [child for child in (self.cli, self.app, self.daemon) if child is not None]

    @staticmethod
    def _signal(child, signum):
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    def interrupt(self, signum, _frame):
        self.stop_signal = signum
        for child in self._children():
            self._signal(child, signum)

    def _check_running(self):
        if self.stop_signal is not None:
            raise _Interrupted()
        if self.daemon is not None and self.daemon.poll() is not None:
            raise TailscaleError("Tailscale daemon stopped; Core cannot continue")

    def _command(self, arguments, deadline, capture=False):
        self._check_running()
        # An anonymous private file bounds memory even if a failed CLI is noisy.
        with tempfile.TemporaryFile(dir=RUNTIME_DIR) as output:
            child = subprocess.Popen(
                [TAILSCALE, "--socket=" + self.socket, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=output if capture else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=self.environment, start_new_session=True,
            )
            self.cli = child
            try:
                while child.poll() is None:
                    self._check_running()
                    if time.monotonic() >= deadline:
                        raise TailscaleError("Tailscale command timed out")
                    time.sleep(POLL_INTERVAL)
                self._check_running()
                if capture:
                    output.seek(0)
                    content = output.read(65537)
                    if len(content) > 65536:
                        raise TailscaleError("Tailscale status exceeded its size limit")
                else:
                    content = b""
                return child.returncode, content
            finally:
                self._stop_children([child])
                self.cli = None

    def _status(self, deadline):
        _code, output = self._command(
            ["status", "--json", "--peers=false"],
            min(deadline, time.monotonic() + COMMAND_TIMEOUT), capture=True,
        )
        try:
            status = json.loads(output)
        except (ValueError, UnicodeError):
            return None
        return status.get("BackendState") if isinstance(status, dict) else None

    def start(self, auth_key):
        self.daemon = subprocess.Popen(
            [TAILSCALED, "--tun=userspace-networking",
             "--state=" + os.path.join(self.state_dir, "tailscaled.state"),
             "--statedir=" + self.state_dir, "--socket=" + self.socket],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=self.environment, start_new_session=True,
        )
        deadline = time.monotonic() + STARTUP_TIMEOUT
        state = None
        while time.monotonic() < deadline:
            self._check_running()
            state = self._status(deadline)
            # NoState is also reported while saved identity is still loading.
            if state in {"Running", "NeedsLogin", "Stopped", "NeedsMachineAuth"}:
                break
            time.sleep(POLL_INTERVAL)
        else:
            raise TailscaleError("Tailscale daemon did not become ready in time")
        if state == "NeedsMachineAuth":
            raise TailscaleError("Tailscale device requires tailnet administrator approval")
        preferences = [
            "--hostname=" + self.hostname, "--accept-dns=false", "--accept-routes=false",
            "--advertise-routes=", "--advertise-exit-node=false", "--exit-node=",
            "--shields-up=true", "--ssh=false",
        ]
        if state == "NeedsLogin":
            if not auth_key:
                raise TailscaleError("Tailscale enrollment requires a configured auth key")
            # A file reference keeps the key out of process arguments and the
            # environment. Delete it on success, failure, timeout or SIGTERM.
            with tempfile.NamedTemporaryFile(mode="wb", prefix="auth-", dir=RUNTIME_DIR) as key_file:
                key_file.write(auth_key.encode("utf-8"))
                key_file.flush()
                enrollment = ["up", "--auth-key=file:" + key_file.name, *preferences]
                if self.tags:
                    enrollment.append("--advertise-tags=" + self.tags)
                code, _ = self._command(
                    enrollment, deadline,
                )
            if code != 0:
                raise TailscaleError("Tailscale enrollment failed; inspect tailnet authorization")
        # Apply the same outbound-only policy to reused state without re-enrolling.
        code, _ = self._command(["set", *preferences, "--webclient=false"], deadline)
        if code != 0:
            raise TailscaleError("Tailscale client preferences could not be applied")
        if state == "Stopped":
            # An authenticated, explicitly stopped node resumes with its saved
            # identity; a consumed one-off enrollment key is not needed again.
            code, _ = self._command(["up"], deadline)
            if code != 0:
                raise TailscaleError("Tailscale saved identity could not be resumed")
        while time.monotonic() < deadline:
            state = self._status(deadline)
            if state == "Running":
                return
            if state == "NeedsMachineAuth":
                raise TailscaleError("Tailscale device requires tailnet administrator approval")
            time.sleep(POLL_INTERVAL)
        raise TailscaleError("Tailscale did not authenticate before the startup deadline")

    def supervise(self, command):
        self._check_running()
        environment = os.environ.copy()
        environment.pop(AUTH_ENV, None)
        environment["REIGN_TAILSCALE_SOCKET"] = self.socket
        self.app = subprocess.Popen(command, env=environment, start_new_session=True)
        while True:
            self._check_running()
            code = self.app.poll()
            if code is not None:
                return _return_code(code)
            time.sleep(POLL_INTERVAL)

    def _stop_children(self, children):
        for child in children:
            self._signal(child, signal.SIGTERM)
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT
        for child in children:
            try:
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        # Also stop descendants whose process-group leader already exited.
        for child in children:
            self._signal(child, signal.SIGKILL)
            child.wait()

    def close(self):
        self._stop_children(self._children())
        # Never remove persistent identity on process termination.
        try:
            os.unlink(self.socket)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(RUNTIME_DIR)
        except OSError:
            pass


def run(command, auth_key=None):
    """Return the app's exit code, or fail closed with a sanitized diagnostic."""
    os.environ.pop(AUTH_ENV, None)
    if auth_key == "":
        auth_key = None
    if os.geteuid() == 0:
        raise TailscaleError("Tailscale must run as the dedicated non-root Core account")
    hostname = os.environ.get("REIGN_TAILSCALE_HOSTNAME", "")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", hostname):
        raise TailscaleError("Tailscale requires a valid configured hostname")
    tags = os.environ.get("REIGN_TAILSCALE_TAGS", "")
    if tags and (len(tags) > 512 or not all(
            re.fullmatch(r"tag:[A-Za-z][A-Za-z0-9-]*", tag) for tag in tags.split(","))):
        raise TailscaleError("Tailscale tags must be a comma-separated list of tag names")
    if auth_key is not None and (len(auth_key) > 4096 or not auth_key
                                 or any(character.isspace() for character in auth_key)):
        raise TailscaleError("Tailscale auth key configuration is invalid")
    runtime = None
    handlers = {}
    try:
        with _state_lock() as state_dir:
            _runtime_directory()
            runtime = _Runtime(state_dir, hostname, tags)
            for signum in (signal.SIGTERM, signal.SIGINT):
                handlers[signum] = signal.signal(signum, runtime.interrupt)
            try:
                runtime.start(auth_key)
                auth_key = None
                return runtime.supervise(command)
            except _Interrupted:
                return 128 + runtime.stop_signal
            finally:
                try:
                    runtime.close()
                finally:
                    for signum, handler in handlers.items():
                        signal.signal(signum, handler)
    except TailscaleError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        raise TailscaleError("Tailscale runtime could not start safely") from None


def main():
    """The image CMD opts into supervision; pre-deploy never invokes this CMD."""
    auth_key = os.environ.pop(AUTH_ENV, None)
    if len(sys.argv) < 2:
        raise SystemExit("Core startup command is required")
    try:
        if enabled():
            raise SystemExit(run(sys.argv[1:], auth_key))
    except TailscaleError as error:
        raise SystemExit(str(error)) from None
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
