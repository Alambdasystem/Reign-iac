#!/usr/bin/env python3
"""Write a salted AX named-account verifier without echoing a password."""

import argparse
import getpass
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reign.ax_auth import InvalidCredentials, MAX_PASSWORD_BYTES, hash_password  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path,
                        help="New private file outside the repository for the password hash")
    parser.add_argument("--stdin", action="store_true",
                        help="Read a password from redirected standard input instead of a prompt")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.expanduser().resolve()
    if output.is_relative_to(root):
        parser.error("The verifier output must be outside the repository")
    try:
        if args.stdin:
            if sys.stdin.isatty():
                parser.error("--stdin requires redirected input; use the hidden prompt at a terminal")
            raw = sys.stdin.buffer.read(MAX_PASSWORD_BYTES + 3)
            if len(raw) > MAX_PASSWORD_BYTES + 2:
                raise ValueError("Password input is too long")
            password = raw.decode("utf-8").removesuffix("\n").removesuffix("\r")
        else:
            password = getpass.getpass("Named-account password: ")
            if password != getpass.getpass("Confirm password: "):
                raise ValueError("Passwords do not match")
        verifier = hash_password(password)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as target:
            target.write(verifier + "\n")
        print(f"Password verifier written to {output}")
    except InvalidCredentials:
        print("Could not create password verifier: Invalid password input", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Could not create password verifier: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
