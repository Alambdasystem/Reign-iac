"""Fail closed before accessing a remote state backend or provider."""
import os
import sys
from urllib.parse import urlsplit


def check(env, applying=False):
    for key in ("RENDER_API_KEY", "TF_HTTP_USERNAME", "TF_HTTP_PASSWORD", "PLAN_ENCRYPTION_KEY", "TF_VAR_runtime_secrets"):
        if not env.get(key):
            raise ValueError(f"Missing protected configuration: {key}")
    if len(env["PLAN_ENCRYPTION_KEY"]) < 32:
        raise ValueError("PLAN_ENCRYPTION_KEY must contain at least 32 random characters")
    for key in ("TF_HTTP_ADDRESS", "TF_HTTP_LOCK_ADDRESS", "TF_HTTP_UNLOCK_ADDRESS"):
        url = urlsplit(env.get(key, ""))
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError(f"{key} must be an HTTPS endpoint with credentials supplied separately")
    if applying and env.get("PAID_CHANGE_APPROVAL") != "true":
        raise ValueError("Explicit approval for paid changes is required to apply")


if __name__ == "__main__":
    try:
        check(os.environ, applying="--apply" in sys.argv)
        print("Protected configuration is present; values withheld.")
    except ValueError as error:
        sys.exit(str(error))
