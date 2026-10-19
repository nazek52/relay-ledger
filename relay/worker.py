import hashlib
import hmac
import os
import random
import time
from urllib.parse import urlsplit

import httpx

from relay.store import Store


def signature(secret, timestamp, body):
    return hmac.new(
        secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()


def deliver_one(store, target, secret, client):
    job = store.claim()
    if job is None:
        return False
    timestamp = int(time.time())
    body = job["payload"].encode()
    try:
        # Never follow redirects. Targets are operator configuration, not API input.
        with client.stream(
            "POST",
            target,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Relay-Id": job["id"],
                "X-Relay-Timestamp": str(timestamp),
                "X-Relay-Signature": signature(secret, timestamp, body),
            },
        ) as response:
            store.finish(job, response.status_code, jitter=random.random())
    except httpx.HTTPError as exc:
        # Store the exception class only; URLs/credentials/body must not enter logs.
        store.finish(job, None, type(exc).__name__, jitter=random.random())
    return True


def main():
    target = os.environ["RELAY_TARGET"]
    parsed = urlsplit(target)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise SystemExit(
            "RELAY_TARGET must be an operator-controlled HTTP(S) URL without credentials"
        )
    secret = os.environ["RELAY_SIGNING_SECRET"]
    if len(secret) < 32:
        raise SystemExit("RELAY_SIGNING_SECRET must contain at least 32 characters")
    store = Store(os.getenv("RELAY_DB", "relay.db"))
    with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
        try:
            while True:
                if not deliver_one(store, target, secret, client):
                    time.sleep(0.25)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
