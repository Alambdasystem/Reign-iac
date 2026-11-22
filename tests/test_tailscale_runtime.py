"""Filesystem checks and real child-process lifecycle tests; no tailnet access."""

import json
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from reign import core_entrypoint
from reign import tailscale_runtime as runtime


FIXTURE_KEY = "fixture-auth-value-not-a-real-tailscale-key"


class ConfigurationTests(unittest.TestCase):
    def test_disabled_by_default_and_explicit_predeploy_override(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(runtime.enabled())
        with patch.dict(os.environ, {"REIGN_TAILSCALE_ENABLED": "0"}, clear=True):
            self.assertFalse(runtime.enabled())
        with patch.dict(os.environ, {"REIGN_TAILSCALE_ENABLED": "1"}, clear=True):
            self.assertTrue(runtime.enabled())
        with patch.dict(os.environ, {"REIGN_TAILSCALE_ENABLED": "enabled"}, clear=True):
            with self.assertRaises(runtime.TailscaleError):
                runtime.enabled()

    def test_disabled_entrypoint_keeps_exec_and_strips_auth_key(self):
        account = pwd.getpwuid(os.geteuid())
        with patch.dict(os.environ, {runtime.AUTH_ENV: FIXTURE_KEY,
                                     "REIGN_TAILSCALE_ENABLED": "0"}), \
                patch.object(sys, "argv", ["entrypoint", "app", "argument"]), \
                patch.object(core_entrypoint.pwd, "getpwnam", return_value=account), \
                patch.object(core_entrypoint.os, "execvp") as execute, \
                patch.object(core_entrypoint.os, "umask"), \
                patch.object(runtime, "run") as supervise:
            core_entrypoint.main()
            execute.assert_called_once_with("app", ["app", "argument"])
            supervise.assert_not_called()
            self.assertNotIn(runtime.AUTH_ENV, os.environ)

    def test_enabled_service_predeploy_does_not_need_disk_or_enroll(self):
        account = pwd.getpwuid(os.geteuid())
        command = ["env", "REIGN_TAILSCALE_ENABLED=0", "python", "-m", "reign.release_acceptance"]
        with patch.dict(os.environ, {runtime.AUTH_ENV: FIXTURE_KEY,
                                     "REIGN_TAILSCALE_ENABLED": "1"}), \
                patch.object(sys, "argv", ["entrypoint", *command]), \
                patch.object(core_entrypoint.pwd, "getpwnam", return_value=account), \
                patch.object(core_entrypoint.os.path, "ismount", return_value=False), \
                patch.object(core_entrypoint.os, "execvp") as execute, \
                patch.object(core_entrypoint.os, "umask"), \
                patch.object(runtime, "run") as supervise:
            core_entrypoint.main()
            execute.assert_called_once_with("env", command)
            supervise.assert_not_called()
            self.assertNotIn(runtime.AUTH_ENV, os.environ)

    def test_only_explicit_runtime_command_receives_enrollment_key(self):
        account = pwd.getpwuid(os.geteuid())
        command = ["python", "-m", "reign.tailscale_runtime", "app"]
        with patch.dict(os.environ, {runtime.AUTH_ENV: FIXTURE_KEY}), \
                patch.object(sys, "argv", ["entrypoint", *command]), \
                patch.object(core_entrypoint.pwd, "getpwnam", return_value=account), \
                patch.object(core_entrypoint.os, "execvp") as execute, \
                patch.object(core_entrypoint.os, "umask"):
            core_entrypoint.main()
            execute.assert_called_once_with("python", command)
            self.assertEqual(os.environ[runtime.AUTH_ENV], FIXTURE_KEY)

    def test_disabled_runtime_command_execs_app_without_enrollment_key(self):
        with patch.dict(os.environ, {runtime.AUTH_ENV: FIXTURE_KEY,
                                     "REIGN_TAILSCALE_ENABLED": "0"}), \
                patch.object(sys, "argv", ["runtime", "app", "argument"]), \
                patch.object(runtime.os, "execvp") as execute, \
                patch.object(runtime, "run") as supervise:
            runtime.main()
            execute.assert_called_once_with("app", ["app", "argument"])
            supervise.assert_not_called()
            self.assertNotIn(runtime.AUTH_ENV, os.environ)

    def test_root_cannot_start_runtime_directly(self):
        with patch.object(runtime.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(runtime.TailscaleError, "non-root"):
                runtime.run(["unused"])

    def test_invalid_hostname_cannot_become_cli_argument(self):
        with patch.dict(os.environ, {"REIGN_TAILSCALE_HOSTNAME": "--ssh=true"}):
            with self.assertRaisesRegex(runtime.TailscaleError, "hostname"):
                runtime.run(["unused"])

    def test_whitespace_and_oversized_keys_still_fail_before_storage(self):
        with patch.dict(os.environ, {"REIGN_TAILSCALE_HOSTNAME": "render-test"}), \
                patch.object(runtime, "_state_lock") as storage:
            for key in (" ", "\t", "value\n", "x" * 4097):
                with self.subTest(key_length=len(key)):
                    with self.assertRaisesRegex(runtime.TailscaleError, "auth key configuration"):
                        runtime.run(["unused"], key)
            storage.assert_not_called()


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mount = self.root / "vault"
        self.mount.mkdir(mode=0o700)
        self.state = self.mount / "tailscale"
        self.addCleanup(patch.stopall)
        patch.object(runtime, "VAULT_MOUNT", str(self.mount)).start()
        patch.dict(os.environ, {"REIGN_VAULT_DIR": str(self.mount)}).start()

    def test_rejects_unmounted_directory_without_creating_fallback(self):
        with self.assertRaisesRegex(runtime.TailscaleError, "mounted Core disk"):
            with runtime._state_lock():
                self.fail("an ordinary directory is not a dedicated mount")
        self.assertFalse(self.state.exists())

    def test_new_state_is_private_and_only_one_runtime_can_lock_it(self):
        with patch.object(runtime.os.path, "ismount", return_value=True):
            with runtime._state_lock() as state_path:
                self.assertEqual(state_path, str(self.state))
                self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((self.state / "runtime.lock").stat().st_mode), 0o600)
                with self.assertRaisesRegex(runtime.TailscaleError, "already owns"):
                    with runtime._state_lock():
                        self.fail("concurrent ownership must fail")
            with runtime._state_lock():
                pass

    def test_rejects_symlinked_state_directory(self):
        other = self.root / "other"
        other.mkdir(mode=0o700)
        self.state.symlink_to(other, target_is_directory=True)
        with patch.object(runtime.os.path, "ismount", return_value=True):
            with self.assertRaises(OSError):
                with runtime._state_lock():
                    pass
        self.assertEqual(list(other.iterdir()), [])

    def test_rejects_existing_state_with_unsafe_mode_without_repair(self):
        self.state.mkdir(mode=0o700)
        file = self.state / "tailscaled.state"
        file.write_text("fixture state")
        file.chmod(0o644)
        with patch.object(runtime.os.path, "ismount", return_value=True):
            with self.assertRaisesRegex(runtime.TailscaleError, "unsafe"):
                with runtime._state_lock():
                    pass
        self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o644)

    def test_rejects_state_symlinks_and_hardlinks(self):
        self.state.mkdir(mode=0o700)
        source = self.root / "source"
        source.write_text("fixture state")
        source.chmod(0o600)
        linked = self.state / "tailscaled.state"
        with patch.object(runtime.os.path, "ismount", return_value=True):
            linked.symlink_to(source)
            with self.assertRaises(runtime.TailscaleError):
                with runtime._state_lock():
                    pass
            linked.unlink()
            os.link(source, linked)
            with self.assertRaises(runtime.TailscaleError):
                with runtime._state_lock():
                    pass

    def test_rejects_unsafe_or_symlinked_runtime_directory(self):
        destination = self.root / "runtime"
        destination.mkdir(mode=0o755)
        destination.chmod(0o755)
        with patch.object(runtime, "RUNTIME_DIR", str(destination)):
            with self.assertRaises(runtime.TailscaleError):
                runtime._runtime_directory()
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)
        destination.rmdir()
        destination.symlink_to(self.mount, target_is_directory=True)
        with patch.object(runtime, "RUNTIME_DIR", str(destination)):
            with self.assertRaises(runtime.TailscaleError):
                runtime._runtime_directory()

    def test_new_runtime_directory_clears_inherited_setgid(self):
        parent = self.root / "setgid-parent"
        parent.mkdir(mode=0o700)
        parent.chmod(0o2700)
        destination = parent / "runtime"
        with patch.object(runtime, "RUNTIME_DIR", str(destination)):
            runtime._runtime_directory()
        info = destination.stat()
        self.assertEqual((info.st_uid, info.st_gid), (os.geteuid(), os.getegid()))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o2700)

    def test_new_runtime_directory_wrong_owner_is_not_normalized(self):
        destination = self.root / "new-runtime"
        real_fstat = os.fstat

        def wrong_owner(descriptor):
            info = list(real_fstat(descriptor))
            info[4] = os.geteuid() + 1
            return os.stat_result(info)

        with patch.object(runtime, "RUNTIME_DIR", str(destination)), \
                patch.object(runtime.os, "fstat", side_effect=wrong_owner), \
                patch.object(runtime.os, "fchown") as chown, \
                patch.object(runtime.os, "fchmod") as chmod:
            with self.assertRaisesRegex(runtime.TailscaleError, "unsafe ownership or type"):
                runtime._runtime_directory()
            chown.assert_not_called()
            chmod.assert_not_called()

    def test_existing_runtime_directory_wrong_group_is_not_normalized(self):
        destination = self.root / "existing-runtime"
        destination.mkdir(mode=0o700)
        before = destination.stat()
        with patch.object(runtime, "RUNTIME_DIR", str(destination)), \
                patch.object(runtime.os, "getegid", return_value=before.st_gid + 1), \
                patch.object(runtime.os, "fchown") as chown, \
                patch.object(runtime.os, "fchmod") as chmod:
            with self.assertRaisesRegex(runtime.TailscaleError, "unsafe"):
                runtime._runtime_directory()
            chown.assert_not_called()
            chmod.assert_not_called()
        after = destination.stat()
        self.assertEqual((after.st_uid, after.st_gid, after.st_mode),
                         (before.st_uid, before.st_gid, before.st_mode))


class ProcessTests(unittest.TestCase):
    """Use executable fixtures to exercise supervision, not mocked Popen calls."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mount = self.root / "vault"
        self.mount.mkdir(mode=0o700)
        self.runtime_dir = self.root / "runtime"
        self.repo = Path(__file__).resolve().parents[1]
        self.processes = []
        self.addCleanup(self._cleanup_processes)

    def _cleanup_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # A failed supervisor test must not orphan fixture groups.
                    for name in ("cli.pid", "app.pid", "daemon.pid"):
                        file = self.root / name
                        if file.exists():
                            try:
                                os.killpg(int(file.read_text()), signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    process.kill()
                    process.wait(timeout=3)

    def _script(self, name, code):
        file = self.root / name
        file.write_text("#!" + sys.executable + "\n" + code)
        file.chmod(0o700)
        return file

    def _start(self, mode="enroll", app_mode="success", auth=True):
        common = "import json,os,pathlib,signal,sys,time\n"
        common += "root=pathlib.Path(" + repr(str(self.root)) + ")\n"
        common += "mode=" + repr(mode) + "\n"
        daemon = self._script("tailscaled", common + '''
(root / "daemon.args").write_text(json.dumps(sys.argv[1:]))
(root / "daemon.env").write_text(json.dumps(dict(os.environ)))
(root / "daemon.pid").write_text(str(os.getpid()))
def stop(signum, frame):
    (root / "daemon.stopped").write_text(str(signum))
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
(root / "daemon.ready").write_text("yes")
while True:
    if mode == "daemon_dies" and (root / "app.started").exists():
        sys.exit(7)
    time.sleep(0.01)
''')
        cli = self._script("tailscale", common + '''
(root / "cli.pid").write_text(str(os.getpid()))
args=sys.argv[2:]
command=args[0]
with (root / "cli.calls").open("a") as output:
    output.write(json.dumps(args) + "\\n")
if command == "status":
    state="Running" if mode in {"running", "daemon_dies"} or (root / "enrolled").exists() else "NeedsLogin"
    if mode == "stopped" and not (root / "enrolled").exists():
        state="Stopped"
    if mode == "loading":
        state="Running" if (root / "loaded").exists() else "NoState"
        (root / "loaded").write_text("yes")
    if not (root / "daemon.ready").exists():
        state="Starting"
    print(json.dumps({"BackendState":state}))
elif command == "up":
    if args == ["up"]:
        if mode != "stopped":
            sys.exit(8)
        (root / "enrolled").write_text("yes")
        sys.exit(0)
    keypath = pathlib.Path(next(value.split("file:",1)[1] for value in args if value.startswith("--auth-key=file:")))
    text=keypath.read_text()
    (root / "auth.observed").write_text(json.dumps({
        "mode":keypath.stat().st_mode & 0o777,
        "key_present": bool(text),
        "in_arguments": any(text in argument for argument in sys.argv),
        "in_environment": text in os.environ.values(),
        "path": str(keypath)
    }))
    print(text, flush=True)
    print(text, file=sys.stderr, flush=True)
    if mode == "auth_timeout":
        time.sleep(30)
    if mode == "auth_failure":
        sys.exit(9)
    (root / "enrolled").write_text("yes")
elif command == "set":
    # Unlike up, the real set subcommand has no advertise-tags flag.
    if any(value.startswith("--advertise-tags=") for value in args):
        sys.exit(8)
else:
    sys.exit(8)
''')
        app = self._script("app", common + "app_mode=" + repr(app_mode) + "\n" + '''
(root / "app.env").write_text(json.dumps(dict(os.environ)))
(root / "app.pid").write_text(str(os.getpid()))
def stop(signum, frame):
    (root / "app.stopped").write_text(str(signum))
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
if app_mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
(root / "app.started").write_text("yes")
if app_mode == "success":
    sys.exit(0)
if app_mode == "exit7":
    sys.exit(7)
while True:
    time.sleep(0.01)
''')
        runner = self._script("runner", """
import os,sys
from reign import tailscale_runtime as t
os.umask(0o077)
t.VAULT_MOUNT = %r
t.RUNTIME_DIR = %r
t.TAILSCALED = %r
t.TAILSCALE = %r
t.STARTUP_TIMEOUT = 0.8
t.COMMAND_TIMEOUT = 0.3
t.SHUTDOWN_TIMEOUT = 0.2
t.POLL_INTERVAL = 0.01
t.os.path.ismount = lambda path: path == t.VAULT_MOUNT
key = os.environ.pop(t.AUTH_ENV, None)
try:
    code=t.run([%r], key)
except t.TailscaleError as error:
    print(str(error), file=sys.stderr)
    code=1
raise SystemExit(code)
""" % (str(self.mount), str(self.runtime_dir), str(daemon), str(cli), str(app)))
        environment = os.environ.copy()
        environment.update({
            "PYTHONPATH": str(self.repo), "REIGN_VAULT_DIR": str(self.mount),
            "REIGN_TAILSCALE_HOSTNAME": "render-test-client",
            "REIGN_TAILSCALE_TAGS": "tag:render-test",
            "OPENAI_API_KEY": "fixture-provider-key-must-not-enter-daemon",
        })
        environment.pop(runtime.AUTH_ENV, None)
        if auth is not False:
            environment[runtime.AUTH_ENV] = FIXTURE_KEY if auth is True else auth
        process = subprocess.Popen([sys.executable, str(runner)], env=environment,
                                   cwd=self.repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append(process)
        return process

    def _wait_marker(self, name, process):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if (self.root / name).exists():
                return
            if process.poll() is not None:
                self.fail("supervisor exited before marker: " + repr(process.communicate()))
            time.sleep(0.01)
        self.fail("marker did not appear: " + name)

    def _finished(self, process, expected):
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, expected, stderr.decode())
        self.assertNotIn(FIXTURE_KEY.encode(), stdout + stderr)
        self.assertFalse(self.runtime_dir.exists(), "ephemeral files must be removed")
        self.assertTrue((self.mount / "tailscale" / "runtime.lock").exists())
        return stdout, stderr

    def _calls(self):
        return [json.loads(line) for line in (self.root / "cli.calls").read_text().splitlines()]

    def test_enrollment_secret_is_file_only_and_never_inherited_or_logged(self):
        process = self._start()
        self._finished(process, 0)
        observed = json.loads((self.root / "auth.observed").read_text())
        self.assertEqual(observed["mode"], 0o600)
        self.assertTrue(observed["key_present"])
        self.assertFalse(observed["in_arguments"])
        self.assertFalse(observed["in_environment"])
        self.assertFalse(Path(observed["path"]).exists())
        for name in ("daemon.env", "app.env"):
            environment = json.loads((self.root / name).read_text())
            self.assertNotIn(runtime.AUTH_ENV, environment)
        self.assertNotIn("OPENAI_API_KEY", json.loads((self.root / "daemon.env").read_text()))
        self.assertEqual(json.loads((self.root / "daemon.env").read_text())["TS_LOGS_DIR"],
                         str(self.mount / "tailscale"))
        daemon_args = json.loads((self.root / "daemon.args").read_text())
        self.assertIn("--tun=userspace-networking", daemon_args)
        self.assertFalse(any("socks5" in argument or "http-proxy" in argument for argument in daemon_args))
        prefs = next(call for call in self._calls() if call[0] == "set")
        for expected in ("--ssh=false", "--advertise-routes=", "--advertise-exit-node=false",
                         "--exit-node=", "--accept-routes=false", "--shields-up=true", "--webclient=false"):
            self.assertIn(expected, prefs)

    def test_running_identity_does_not_reenroll_or_require_key(self):
        process = self._start(mode="running", auth=False)
        self._finished(process, 0)
        self.assertFalse(any(call[0] == "up" for call in self._calls()))
        self.assertFalse((self.root / "auth.observed").exists())

    def test_empty_key_reuses_running_identity_without_enrollment(self):
        process = self._start(mode="running", auth="")
        self._finished(process, 0)
        self.assertFalse(any(call[0] == "up" for call in self._calls()))
        self.assertFalse((self.root / "auth.observed").exists())
        self.assertNotIn(runtime.AUTH_ENV, json.loads((self.root / "app.env").read_text()))

    def test_empty_key_with_fresh_state_fails_before_app(self):
        process = self._start(auth="")
        _, stderr = self._finished(process, 1)
        self.assertIn(b"requires a configured auth key", stderr)
        self.assertFalse((self.root / "app.started").exists())
        self.assertFalse(any(call[0] == "up" for call in self._calls()))

    def test_loading_saved_identity_waits_instead_of_reenrolling(self):
        process = self._start(mode="loading", auth=False)
        self._finished(process, 0)
        self.assertFalse(any(call[0] == "up" for call in self._calls()))

    def test_stopped_identity_resumes_without_reusing_enrollment_key(self):
        process = self._start(mode="stopped", auth=False)
        self._finished(process, 0)
        self.assertEqual([call for call in self._calls() if call[0] == "up"], [["up"]])
        self.assertFalse((self.root / "auth.observed").exists())

    def test_missing_enrollment_key_fails_before_app(self):
        process = self._start(auth=False)
        _, stderr = self._finished(process, 1)
        self.assertIn(b"requires a configured auth key", stderr)
        self.assertFalse((self.root / "app.started").exists())
        self.assertTrue((self.root / "daemon.stopped").exists())

    def test_enrollment_failure_suppresses_provider_output_and_cleans_secret(self):
        process = self._start(mode="auth_failure")
        _, stderr = self._finished(process, 1)
        self.assertIn(b"enrollment failed", stderr)
        self.assertFalse((self.root / "app.started").exists())

    def test_enrollment_timeout_terminates_children_and_cleans_secret(self):
        process = self._start(mode="auth_timeout")
        _, stderr = self._finished(process, 1)
        self.assertIn(b"command timed out", stderr)
        self.assertFalse((self.root / "app.started").exists())
        self.assertTrue((self.root / "daemon.stopped").exists())

    def test_daemon_death_stops_core_and_returns_failure(self):
        process = self._start(mode="daemon_dies", app_mode="wait", auth=False)
        _, stderr = self._finished(process, 1)
        self.assertIn(b"daemon stopped", stderr)
        self.assertTrue((self.root / "app.stopped").exists())

    def test_application_exit_code_is_preserved_and_daemon_is_stopped(self):
        process = self._start(mode="running", app_mode="exit7", auth=False)
        self._finished(process, 7)
        self.assertTrue((self.root / "daemon.stopped").exists())

    def test_sigterm_is_forwarded_to_app_and_daemon(self):
        process = self._start(mode="running", app_mode="wait", auth=False)
        self._wait_marker("app.started", process)
        process.send_signal(signal.SIGTERM)
        self._finished(process, 128 + signal.SIGTERM)
        self.assertTrue((self.root / "app.stopped").exists())
        self.assertTrue((self.root / "daemon.stopped").exists())

    def test_sigterm_during_enrollment_cleans_secret_without_launching_app(self):
        process = self._start(mode="auth_timeout")
        self._wait_marker("auth.observed", process)
        process.send_signal(signal.SIGTERM)
        self._finished(process, 128 + signal.SIGTERM)
        self.assertFalse((self.root / "app.started").exists())

    def test_shutdown_is_bounded_when_application_ignores_sigterm(self):
        process = self._start(mode="running", app_mode="ignore_term", auth=False)
        self._wait_marker("app.started", process)
        process.send_signal(signal.SIGTERM)
        self._finished(process, 128 + signal.SIGTERM)
        self.assertFalse((self.root / "app.stopped").exists())


if __name__ == "__main__":
    unittest.main()
