"""Private, bounded Core-to-Windows Ollama connectivity check.

No arbitrary SSH command, public listener, or model-provider switch. The
Tailscale socket belongs to the existing Core userspace daemon.
"""
import asyncio
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

HOST = "100.125.47.54"
USER = "owner"
MODEL = "qwen3:4b"
SOCKET = "/tmp/reign-tailscale/tailscaled.sock"
HOST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKgYrB+77QPXhFJVvsFdZNc8QT2s4rk15GMoXh2uHmpQ"
KEY_NAMES = ("REIGN_BACKEND_SSH_PRIVATE_KEY", "REIGN_SSH_PRIVATE_KEY", "REIGN_OLLAMA_SSH_PRIVATE_KEY", "REIGN_BACKEND_SSH_KEY", "REIGN_OLLAMA_SSH_KEY")
MAX_RESPONSE = 32768


def _private_key():
    for name in KEY_NAMES:
        value = os.environ.get(name, "")
        if value:
            value = value.replace("\\n", "\n").strip() + "\n"
            if len(value) > 16384 or not value.startswith("-----BEGIN OPENSSH PRIVATE KEY-----"):
                raise ValueError("invalid_private_key")
            return value
    raise ValueError("ssh_private_key_not_configured")


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def probe():
    """One read-only model query. Returns only safe stage and performance fields."""
    result = {"verification": "reign_ollama_tailnet", "ssh": "not_tested",
              "inference": "not_tested", "model": MODEL}
    if os.environ.get("REIGN_TAILSCALE_ENABLED") != "1" or not Path(SOCKET).is_socket():
        return {**result, "error_code": "tailscale_socket_unavailable"}
    try:
        key = _private_key()
    except ValueError as exc:
        return {**result, "error_code": str(exc)}
    with tempfile.TemporaryDirectory(prefix="reign-ollama-") as directory:
        root = Path(directory)
        os.chmod(directory, 0o700)
        keyfile, knownhosts = root / "id_ed25519", root / "known_hosts"
        keyfile.write_text(key)
        os.chmod(keyfile, 0o600)
        knownhosts.write_text(HOST + " " + HOST_KEY + "\n")
        os.chmod(knownhosts, 0o600)
        port = _free_port()
        proxy = f"/usr/local/bin/tailscale --socket={SOCKET} nc %h %p"
        args = [
            "/usr/bin/ssh", "-N", "-T",
            "-i", str(keyfile), "-p", "22",
            "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={knownhosts}",
            "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", f"ProxyCommand={proxy}", "-o", "ConnectTimeout=10",
            "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=2",
            "-L", f"127.0.0.1:{port}:127.0.0.1:11434", f"{USER}@{HOST}",
        ]
        started = time.monotonic()
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                   env={"HOME": str(root), "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C"})
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=3.0),
                                         trust_env=False, follow_redirects=False) as client:
                # Retry local listener only; one model generation once ready.
                for _ in range(80):
                    if process.poll() is not None:
                        return {**result, "error_code": "ssh_auth_or_transport_failed"}
                    try:
                        version = await client.get(f"http://127.0.0.1:{port}/api/version",
                                                   timeout=2.0)
                        if version.status_code == 200:
                            result["ssh"] = "passed"
                            result["ollama_version_reachable"] = True
                            break
                    except httpx.RequestError:
                        pass
                    await asyncio.sleep(0.15)
                else:
                    return {**result, "error_code": "ssh_forward_timeout"}
                sent = time.monotonic()
                try:
                    response = await client.post(
                        f"http://127.0.0.1:{port}/api/generate",
                        json={"model": MODEL,
                              "prompt": "Reply with only the word READY.",
                              "stream": False, "options": {"num_predict": 24, "temperature": 0}},
                        timeout=90.0,
                    )
                except (httpx.RequestError, TimeoutError):
                    return {**result, "error_code": "ollama_generation_unavailable"}
                if response.status_code != 200 or len(response.content) > MAX_RESPONSE:
                    return {**result, "error_code": "ollama_bad_response"}
                try:
                    payload = response.json()
                    answer = payload["response"]
                    if not isinstance(answer, str) or not answer.strip():
                        raise ValueError()
                except (ValueError, KeyError, TypeError):
                    return {**result, "error_code": "ollama_invalid_answer"}
                result["inference"] = "passed"
                result["answer"] = answer[:120]
                result["elapsed_ms"] = round((time.monotonic() - sent) * 1000)
                for metric in ("eval_count", "prompt_eval_count", "total_duration", "eval_duration"):
                    if isinstance(payload.get(metric), int) and not isinstance(payload[metric], bool):
                        result[metric] = payload[metric]
                result["ssh_setup_ms"] = round((sent - started) * 1000)
                return result
        finally:
            process.terminate() if process.poll() is None else None
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


async def _startup():
    try:
        receipt = await asyncio.wait_for(probe(), timeout=135)
    except Exception:
        receipt = {"verification": "reign_ollama_tailnet", "ssh": "not_verified",
                   "inference": "not_verified", "error_code": "probe_internal_failure"}
    print("REIGN_OLLAMA_PROBE " + json.dumps(receipt, separators=(",", ":")), flush=True)


def schedule_startup_probe():
    if os.environ.get("REIGN_OLLAMA_PROBE_ENABLED") == "1":
        return asyncio.create_task(_startup())
    return None
