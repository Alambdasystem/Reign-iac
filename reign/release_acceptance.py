"""Require both existing private-workspace and shared-room provider gates."""
import subprocess
import sys


def main():
    for module in ("reign.voice_acceptance", "reign.team_acceptance"):
        try:
            result = subprocess.run([sys.executable, "-m", module], timeout=210, check=False)
        except subprocess.TimeoutExpired:
            print('{"status":"failed","error_code":"provider_release_gate_timeout"}')
            return 1
        if result.returncode:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
