import os
from urllib.parse import urlsplit


def secret(name):
    value = os.environ.get(name, "")
    if len(value) < 32:
        raise RuntimeError(f"{name} must contain at least 32 random characters")
    return value


def temporal_address():
    return os.getenv("TEMPORAL_ADDRESS") or os.getenv("TEMPORAL_HOST", "temporal") + ":7233"


def computer_url():
    return os.getenv("COMPUTER_URL") or "http://" + os.getenv("COMPUTER_HOST", "computer") + ":10000"


def checked_url(url):
    allowed = set(os.getenv("ALLOWED_HOSTS", "alambda-forge.onrender.com").split(","))
    try:
        u = urlsplit(url)
        valid = (u.scheme == "https" and u.hostname in allowed and
                 u.port in (None, 443) and not u.username and not u.password)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Only HTTPS URLs on the configured exact host allowlist are allowed")
    return url


QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "reign-computer")
NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
